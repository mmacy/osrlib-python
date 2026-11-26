# The FastAPI pattern

[`examples/fastapi_crawler`](https://github.com/mmacy/osrlib-python/tree/main/examples/fastapi_crawler) wraps [`GameSession`][osrlib.crawl.session.GameSession] in a single-process, synchronous [FastAPI](https://fastapi.tiangolo.com/) reference server, exposing the same barrow adventure as [the TUI crawler](tui-crawler.md) over HTTP. It establishes the server boundaries needed for web and mobile backends: locking each session independently, keeping the master seed and save documents on the server, registering [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on both new and restored sessions, exposing only player-visible events and [`PlayerView`][osrlib.crawl.views.PlayerView] snapshots, and mapping [`osrlib.errors`][osrlib.errors] exceptions separately from in-game rejections.

From a clone of the [osrlib repository](https://github.com/mmacy/osrlib-python), sync the workspace dependencies (which include the `examples` group) and start the server with `uv`:

```sh
uv sync
uv run uvicorn examples.fastapi_crawler:app
```

Every server snippet on this page is included from `examples/fastapi_crawler/app.py` and `examples/fastapi_crawler/content.py` (see the example's [README on GitHub](https://github.com/mmacy/osrlib-python/tree/main/examples/fastapi_crawler) for `curl` examples). To test the exception classes directly without starting the server, run the standalone Python snippet in [Map rejections and exceptions to HTTP status codes](#map-rejections-and-exceptions-to-http-status-codes).

`examples/fastapi_crawler/app.py` exposes these routes:

| Endpoint | Purpose |
| --- | --- |
| `POST /sessions` | Start a new session from a stamped party document, or restore one from a `save_id`. |
| `GET /sessions/{session_id}` | Return `schema_version`, `engine_version`, `mode`, and `clock_rounds`. |
| `POST /sessions/{session_id}/commands` | Parse and execute one command, returning `accepted`, `rejections`, and player-visible `events`. |
| `GET /sessions/{session_id}/view` | Return the current [`PlayerView`][osrlib.crawl.views.PlayerView] snapshot. |
| `POST /sessions/{session_id}/save` | Store a server-side save snapshot and return an opaque `save_id`. |

With the server running on `http://localhost:8000`, serialize a party with [`party_to_document`][osrlib.core.character.party_to_document], create a session, and execute `enter_dungeon`:

```{.python .no-run}
import httpx2

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character, party_to_document
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset

stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream)
party_doc = party_to_document([hero.character])

with httpx2.Client(base_url="http://localhost:8000") as client:
    created = client.post("/sessions", json={"party_document": party_doc}).json()
    session_id = created["session_id"]

    entered = client.post(
        f"/sessions/{session_id}/commands",
        json={"command_type": "enter_dungeon", "dungeon_id": "barrow"},
    ).json()
    assert entered["accepted"] is True
    assert [event["code"] for event in entered["events"]] == [
        "exploration.location.entered",
        "session.quest.activated",
    ]
```

## Lock each session independently

`GameSession` mutates state in place and is not thread-safe. FastAPI runs plain `def` route handlers in a worker threadpool, so concurrent HTTP requests can execute simultaneously on different threads. `examples/fastapi_crawler/app.py` pairs each `GameSession` with its own `threading.Lock`:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:session-store"
```

Hold `_store_lock` only while reading or updating the `_sessions` and `_saves` dicts, and acquire the session's lock around every `session.execute(...)`, `session.view(...)`, or `save_game(session)` call. Commands targeting the same session run sequentially, while separate sessions run concurrently across worker threads. Because `osrlib` is synchronous and CPU-bound, defining routes with `def` rather than `async def` avoids blocking an `asyncio` event loop.

## Create and restore sessions

`POST /sessions` accepts either a stamped party document (created with [`party_to_document`][osrlib.core.character.party_to_document] and validated by [`party_from_document`][osrlib.core.character.party_from_document]) or a `save_id` from an earlier server-side snapshot. A model validator on `CreateSession` requires exactly one of those two fields:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:create-session-model"
```

The handler branches on which field you sent, registers the session in `_sessions` with a fresh lock, and returns the new `session_id` alongside the version stamps:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:create-session"
```

Keep three contracts in mind when creating sessions over HTTP:

- **Keep the master seed on the server**: By default, `create_session` draws a 63-bit seed with `secrets.randbits(63)` and never returns it, because knowing the master seed lets a client predict every future roll. `CreateSession.seed` exists only for deterministic tests and demos; omit `seed` from public request models, since a client that supplies a seed already knows it.
- **Keep feature handles separate from the seed**: The server passes an independent `secrets.token_bytes(32)` key to each new `GameSession`. A caller-controlled seed therefore can't choose the opaque `feature:` handles in `PlayerView`. `CreateSession` has no player-handle-key field. Restoring a save uses the key in that save, so handles from prior views still resolve.
- **Return version stamps up front**: Both `POST /sessions` and `GET /sessions/{session_id}` return [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] and [`engine_version()`][osrlib.versioning.engine_version] from [`osrlib.versioning`][osrlib.versioning] so a client can detect a newer server schema before sending commands (see [Determinism, saves, and replay](../guides/determinism-saves-replay.md)).

### Register listeners on both session paths

Authored triggers and quests do not run until you register [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session (see [Gates, triggers, and quests](../guides/gates-triggers-quests.md)). `examples/fastapi_crawler/content.py` attaches `Interpreter` both when creating a new session and when restoring a saved session:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/content.py:new-session"
```

Save documents store JSON data (`quests`, `fired_triggers`, `journal`, and `flags`), whereas listeners are Python objects. [`load_game`][osrlib.persistence.load_game] therefore returns a session with no registered listeners, and `restore_session` must attach `Interpreter` again:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/content.py:restore-session"
```

If you omit `register_listener` on the restore path, the saved session loads without error, but its triggers and quests stop reacting to subsequent commands.

## Parse commands and filter events

`POST /sessions/{session_id}/commands` deserializes JSON command payloads using [`parse_command`][osrlib.crawl.commands.parse_command] (see [the command schema reference](../reference/commands/index.md)):

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:execute-command"
```

Each request follows one of three paths:

- **Unknown `command_type`**: `parse_command` returns `None`, and the route returns HTTP `422` before calling the engine. Because command schemas grow additively across minor releases, returning `422` for unrecognized command types tells newer clients that the server does not support that command.
- **Malformed payload for a known command**: `parse_command` raises [`ContentValidationError`][osrlib.errors.ContentValidationError] (for example, on an invalid compass direction or a negative quantity), which the exception handler maps to HTTP `422`.
- **Valid command**: `session.execute(command)` runs under the session lock and returns `accepted`, `rejections`, and `events` filtered to [`Visibility.PLAYER`][osrlib.core.events.Visibility], stripping referee-only events (such as hidden surprise, morale, or detection rolls) before the response leaves the server.

`parse_command` deserializes every registered command class, including referee commands such as `AdvanceTime` or `GrantItem`. This example omits authentication and command-class filtering to stay concise; in a production server facing untrusted players, verify that the parsed command is an allowed player command class before calling `session.execute`.

## Map rejections and exceptions to HTTP status codes

Treat in-game rule rejections as normal `200 OK` responses rather than HTTP errors. When a player tries to walk into a wall or act in the wrong `SessionMode`, `session.execute` returns `CommandResult(accepted=False)` with machine-readable [`Rejection`][osrlib.core.validation.Rejection] codes (see [the rejection code reference](../reference/rejection-codes.md)) without rolling dice, advancing the clock, or modifying logs (see [Sessions, commands, and events](../guides/sessions-commands-events.md)). Returning `200` with `accepted: false` lets clients render game feedback directly instead of treating illegal moves as failed requests.

Reserve `4xx` and `5xx` status codes for failures outside the rules of play. [`osrlib.errors`][osrlib.errors] defines a typed exception hierarchy for invalid inputs and version conflicts:

- [`OsrlibError`][osrlib.errors.OsrlibError]: Base class for all library-defined exceptions.
- [`ContentValidationError`][osrlib.errors.ContentValidationError]: Malformed input at a library boundary, such as an invalid dice expression, an adventure that fails [`validate_adventure`][osrlib.crawl.adventure.validate_adventure], or a serialized document, command, or event whose structure fails validation.
- [`SaveVersionError`][osrlib.errors.SaveVersionError]: A stamped document whose `schema_version` is higher than [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION], raised by [`check_document`][osrlib.versioning.check_document] before reading unfamiliar fields.
- [`ReplayVersionError`][osrlib.errors.ReplayVersionError]: A command log passed to [`replay_game`][osrlib.persistence.replay_game] under a different `recorded_engine_version` (this example exposes no replay endpoint, though an API that replays logs should map `ReplayVersionError` to `409` as well).

Invalid Python arguments in caller code (such as wrong argument types or out-of-range seeds) raise standard `ValueError` or `TypeError` rather than `OsrlibError` so application bugs are not masked as client content errors.

`examples/fastapi_crawler/app.py` registers exception handlers for `ContentValidationError` and `SaveVersionError` alongside a `404` lookup helper:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:error-mapping"
```

| Outcome | Source | HTTP status |
| --- | --- | --- |
| Rejected command (`accepted: false`) | `GameSession.execute` rule validation | `200` |
| Unknown `command_type` | [`parse_command`][osrlib.crawl.commands.parse_command] returns `None` | `422` |
| [`ContentValidationError`][osrlib.errors.ContentValidationError] | Malformed party document, command payload, or save | `422` |
| [`SaveVersionError`][osrlib.errors.SaveVersionError] | Document `schema_version` newer than `SCHEMA_VERSION` | `409` |
| Unknown `session_id` or `save_id` | ID missing from `_sessions` or `_saves` | `404` |
| Unhandled exception | Server bug (`ValueError`, `TypeError`, or runtime failure) | `500` |

You can test `osrlib.errors` directly without starting a server:

```python
from osrlib.errors import ContentValidationError, OsrlibError, ReplayVersionError, SaveVersionError
from osrlib.persistence import load_game
from osrlib.versioning import SCHEMA_VERSION, check_document

# Every library-defined exception inherits from OsrlibError.
assert issubclass(ContentValidationError, OsrlibError)
assert issubclass(SaveVersionError, OsrlibError)
assert issubclass(ReplayVersionError, OsrlibError)

# A structurally broken document is malformed content: ContentValidationError (the app's 422).
try:
    load_game({"kind": "not-a-save"})
    raise AssertionError("expected ContentValidationError")
except ContentValidationError:
    pass

# A document stamped by a newer library is a version conflict: SaveVersionError (the app's 409).
newer = {"kind": "save", "schema_version": SCHEMA_VERSION + 1, "payload": {}}
try:
    check_document(newer, expected_kind="save")
    raise AssertionError("expected SaveVersionError")
except SaveVersionError:
    pass
```

## Serve state through PlayerView

`GET /sessions/{session_id}/view` exposes game state exclusively through [`session.view(Visibility.PLAYER)`][osrlib.crawl.session.GameSession.view], with no referee-view endpoint:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:player-view"
```

[`PlayerView`][osrlib.crawl.views.PlayerView] allowlists only fields safe for an untrusted client: explored and lit dungeon cells, entered areas, known features and transitions, public character sheets, including whether each caster can prepare spells now (`MemberView.can_prepare_now`), masked descriptions for unidentified magic items, display names for every item the view shows (`item_names` and `item_names_plural`) and the own name of each named gem and piece of jewellery (`valuable_names`), the id and count of each thing in a known pile (`PileView.contents`), encounter groups without monster hit points, `journal` entries, and active `quests` with their revealed objectives. Pass each known [`MapFeatureView.id`][osrlib.crawl.views.MapFeatureView.id] back in a treasure command's `feature_id`. [`PlayerView.features_here`][osrlib.crawl.views.PlayerView.features_here] lists the handles the treasure commands accept from where the party stands, and [`MapFeatureView.emptied`][osrlib.crawl.views.MapFeatureView.emptied] marks an authored cache the party has emptied, so the client can draw that cache opened and stop offering it. Unexplored geometry, raw ids in the feature list, cache contents, undiscovered secret doors and traps, transition destinations, monster stats, `flags`, narrator `guidance`, the player-handle key, and RNG state are excluded from the model (see [Views and visibility](../guides/views-and-visibility.md)). `PlayerView.emptied_caches` can contain a fully qualified raw feature id after the party empties that cache.

Authored narrative also reaches web clients through `POST /sessions/{session_id}/commands` responses in two places:

- **Quest and journal events**: Player-visible quest activations, objective completions, and journal entries appear in `events`, allowing incremental clients to display story updates without re-fetching `GET /sessions/{session_id}/view`.
- **Gate refusals**: When a gated door or stair blocks the party, its authored refusal message appears in `rejections[].params.refusal` inside the `accepted: false` response. Render `params.refusal` whenever it is present so players see why the passage blocked them (see [Gates, triggers, and quests](../guides/gates-triggers-quests.md)).

## Keep save documents on the server

A document returned by [`save_game`][osrlib.persistence.save_game] contains everything the HTTP API withholds from clients: `master_seed`, `rng_streams`, a supplied `player_handle_key`, referee state, and the unfiltered logs. `POST /sessions/{session_id}/save` therefore stores the save document on the server and returns only an opaque `save_id`:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:save-session"
```

To resume play, the client sends `{"save_id": "..."}` to `POST /sessions` (shown in [Create and restore sessions](#create-and-restore-sessions)), where the server calls [`load_game`][osrlib.persistence.load_game], re-registers `Interpreter` ([Register listeners on both session paths](#register-listeners-on-both-session-paths)), and returns a new `session_id`. Replacing the in-memory `_saves` dict with a database persists snapshots across restarts, while active `_sessions` and their `threading.Lock` instances remain local to one worker process (multi-worker deployments require sticky session routing or external session coordination).

## Where next

- [Sessions, commands, and events](../guides/sessions-commands-events.md) - session modes, command validation, rejections, and the event log.
- [Views and visibility](../guides/views-and-visibility.md) - field-by-field comparison of `PlayerView` and `RefereeView`.
- [The command schema reference](../reference/commands/index.md) - JSON Schemas and legal modes for every command class.
- [LLM referees](llm-referees.md) - driving a session in process with full `RefereeView` access.
