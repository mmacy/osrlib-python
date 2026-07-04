"""The FastAPI wrapper over `GameSession` — the sync-endpoint, per-session-lock pattern.

`GameSession` is not thread-safe by contract, and FastAPI runs sync `def` endpoints
in its threadpool — which is precisely why every session lives in the store beside
its own `threading.Lock`, held across every `execute` and every view read. The
endpoints are deliberately sync: the engine is synchronous and CPU-bound, so the
threadpool-plus-lock pattern serializes each session's commands while separate
sessions proceed in parallel. An async facade would add nothing.

What crosses the wire is the spec's player surface and nothing else:

- Full state, referee events, and the master seed are server-side secrets. The
  server draws the seed by default and never returns it; the optional client seed
  exists for reproducible demos and the tests.
- Command results return with events filtered to player visibility — referee
  events are stripped at the wire, never trusted to the client.
- The only game-state read is `session.view(Visibility.PLAYER)`. There is no
  referee-view endpoint at all.
- Saves snapshot into a server-side store and return an opaque id; the save
  document (seed, referee state, full logs) never crosses the wire. `POST
  /sessions` accepts a save id to restore.

The status mapping mirrors the spec's error split — this is `osrlib.errors` earning
its keep. An in-fiction rejection (`accepted: false`) is a **200**: rejections are
game feedback, not transport errors. Out-of-fiction failures map from the typed
hierarchy: `ContentValidationError` → 422, `SaveVersionError` → 409, an unknown
session or save id → 404, and anything else is a 500.
"""

import secrets
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator

from osrlib.core.character import party_from_document
from osrlib.core.events import Visibility
from osrlib.crawl.commands import parse_command
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.errors import ContentValidationError, SaveVersionError
from osrlib.persistence import save_game
from osrlib.versioning import SCHEMA_VERSION, engine_version

from .content import new_session, restore_session

__all__ = ["app"]

app = FastAPI(title="osrlib barrow crawler", description="The example barrow adventure behind an HTTP API.")

_store_lock = threading.Lock()
_sessions: dict[str, tuple[GameSession, threading.Lock]] = {}
_saves: dict[str, dict] = {}


class CreateSession(BaseModel):
    """The `POST /sessions` body: a stamped party document, or a save id to restore.

    `seed` exists for reproducible demos and the tests; production clients omit it
    and the server draws one — the master seed is a server-side secret either way.
    """

    party_document: dict | None = None
    save_id: str | None = None
    seed: int | None = None

    @model_validator(mode="after")
    def _party_or_save(self) -> CreateSession:
        if (self.party_document is None) == (self.save_id is None):
            raise ValueError("exactly one of party_document or save_id is required")
        return self


@app.exception_handler(ContentValidationError)
def _content_validation_error(request: Request, error: ContentValidationError) -> JSONResponse:
    """Malformed content — a bad party document or command payload — is a 422."""
    return JSONResponse(status_code=422, content={"detail": str(error)})


@app.exception_handler(SaveVersionError)
def _save_version_error(request: Request, error: SaveVersionError) -> JSONResponse:
    """A document from a newer engine is a conflict with this server's, a 409."""
    return JSONResponse(status_code=409, content={"detail": str(error)})


def _session_or_404(session_id: str) -> tuple[GameSession, threading.Lock]:
    with _store_lock:
        entry = _sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown session id {session_id!r}")
    return entry


@app.post("/sessions")
def create_session(request: CreateSession) -> dict:
    """Create a session from a party document, or restore one from a server-side save.

    The response carries the schema handshake and the new session id — never the
    seed.
    """
    if request.save_id is not None:
        with _store_lock:
            document = _saves.get(request.save_id)
        if document is None:
            raise HTTPException(status_code=404, detail=f"unknown save id {request.save_id!r}")
        session = restore_session(document)
    elif request.party_document is not None:
        members = party_from_document(request.party_document)
        seed = request.seed if request.seed is not None else secrets.randbits(63)
        session = new_session(Party(members=members), seed=seed)
    else:  # unreachable: the body model requires exactly one of the two
        raise HTTPException(status_code=422, detail="exactly one of party_document or save_id is required")
    session_id = secrets.token_hex(8)
    with _store_lock:
        _sessions[session_id] = (session, threading.Lock())
    return {"session_id": session_id, "schema_version": SCHEMA_VERSION, "engine_version": engine_version()}


@app.get("/sessions/{session_id}")
def session_metadata(session_id: str) -> dict:
    """The schema handshake the spec promises front ends, plus the public mode."""
    session, lock = _session_or_404(session_id)
    with lock:
        mode = session.mode.value
        clock_rounds = session.clock.rounds
    return {
        "session_id": session_id,
        "schema_version": SCHEMA_VERSION,
        "engine_version": engine_version(),
        "mode": mode,
        "clock_rounds": clock_rounds,
    }


@app.post("/sessions/{session_id}/commands")
def execute_command(session_id: str, body: dict) -> dict:
    """Parse and execute one command under the session lock.

    An unknown `command_type` is a 422 (the additive-schema contract: this server
    doesn't understand the command, so it never reaches the engine). A rejected
    command is a 200 — rejections are game feedback, and they cost the client
    nothing: no draws, no clock time, no log entry.
    """
    session, lock = _session_or_404(session_id)
    command = parse_command(body)
    if command is None:
        raise HTTPException(status_code=422, detail=f"unknown command type {body.get('command_type')!r}")
    with lock:
        result = session.execute(command)
    return {
        "accepted": result.accepted,
        "rejections": [rejection.model_dump(mode="json") for rejection in result.rejections],
        "events": [event.model_dump(mode="json") for event in result.events if event.visibility is Visibility.PLAYER],
    }


@app.get("/sessions/{session_id}/view")
def player_view(session_id: str) -> dict:
    """The player projection — the only game-state read the API offers."""
    session, lock = _session_or_404(session_id)
    with lock:
        view = session.view(Visibility.PLAYER)
    return view.model_dump(mode="json")


@app.post("/sessions/{session_id}/save")
def save_session(session_id: str) -> dict:
    """Snapshot into the server-side save store; only the opaque id crosses the wire."""
    session, lock = _session_or_404(session_id)
    with lock:
        document = save_game(session)
    save_id = secrets.token_hex(8)
    with _store_lock:
        _saves[save_id] = document
    return {"save_id": save_id}
