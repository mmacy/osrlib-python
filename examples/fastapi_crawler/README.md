# FastAPI barrow crawler

The example TUI crawler's barrow adventure behind an HTTP API — the second front-end proof that osrlib is presentation-agnostic. The content is imported from `examples.tui_crawler.content` unchanged; only the presentation differs.

## Run it

```sh
uv sync
uv run uvicorn examples.fastapi_crawler:app
```

Then create a session and play:

```sh
# A stamped party document comes from osrlib.core.character.party_to_document;
# the tests build one with the TUI's scripted party.
curl -s localhost:8000/sessions -X POST -H 'content-type: application/json' \
  -d '{"party_document": {...}}'
# → {"session_id": "…", "schema_version": 2, "engine_version": "1.0.0"}

curl -s localhost:8000/sessions/$SID/commands -X POST -H 'content-type: application/json' \
  -d '{"command_type": "enter_dungeon", "dungeon_id": "barrow"}'

curl -s localhost:8000/sessions/$SID/view
```

## The patterns it demonstrates

- **The per-session lock.** `GameSession` is not thread-safe by contract, and FastAPI runs sync `def` endpoints in its threadpool. The store holds `(GameSession, threading.Lock)` per session id, and the lock is held across every `execute` and every view read: one session's commands serialize, separate sessions proceed in parallel. The endpoints are deliberately sync — the engine is synchronous and CPU-bound, so an async facade would add nothing.
- **Player visibility at the wire.** Command results return events filtered to player visibility; the only game-state read is `GET /sessions/{id}/view`, which returns `session.view(Visibility.PLAYER)`. There is no referee-view endpoint at all — referee events, hidden geometry, monster HP, and unidentified items' true identities never cross the wire.
- **The seed is a server secret.** The server draws the master seed and never returns it. The optional `seed` field on session creation exists for reproducible demos and the tests.
- **Saves stay server-side.** `POST /sessions/{id}/save` snapshots into an in-memory store and returns an opaque save id; `POST /sessions` accepts that id to restore. The save document — seed, referee state, full logs — never leaves the server.
- **The status mapping.** In-fiction rejections (`accepted: false`) are 200s: rejections are game feedback, not transport errors, and they cost nothing (no draws, no clock time, no log entry). Out-of-fiction failures map from the typed `osrlib.errors` hierarchy: `ContentValidationError` → 422, `SaveVersionError` → 409, an unknown session or save id → 404, anything else → 500.

## Endpoints

| Method and path | What it does |
| --- | --- |
| `POST /sessions` | Create from a stamped party document (optional `seed`), or restore from a `save_id`. Returns the session id and the schema handshake. |
| `GET /sessions/{id}` | The schema handshake: `schema_version`, `engine_version`, plus the public mode and clock. |
| `POST /sessions/{id}/commands` | Parse one serialized command (unknown type → 422), execute under the lock, return `accepted`/`rejections`/player-visible `events`. |
| `GET /sessions/{id}/view` | The player projection. |
| `POST /sessions/{id}/save` | Snapshot server-side; returns the opaque save id. |

## What it deliberately is not

Authentication, multi-process session stores, and persistence beyond the in-memory save store are production backend concerns, out of scope by design — the example teaches the lock and visibility patterns, not deployment.
