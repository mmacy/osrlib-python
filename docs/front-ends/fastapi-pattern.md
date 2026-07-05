# The FastAPI pattern

The library's second example front end puts the [TUI crawler's](tui-crawler.md) barrow adventure behind an HTTP API — the same authored content behind a terminal and a web server, which is the point: osrlib doesn't care what's on the other side of the [`GameSession`][osrlib.crawl.session.GameSession]. This page teaches the server patterns the example exists to demonstrate: the per-session lock, player visibility enforced at the wire, saves that never leave the server, and the mapping from osrlib's typed exceptions to HTTP statuses — this last one makes the page the home of [`osrlib.errors`][osrlib.errors]. Run instructions live in [the example's README on GitHub](https://github.com/mmacy/osrlib-python/tree/main/examples/fastapi_crawler).

The example is small — five endpoints in `examples/fastapi_crawler/app.py` — and every server fragment below is excerpted directly from that file, so the page cannot drift from the code it teaches. Server fragments don't run standalone; the page's one self-contained runnable block is [the exception demonstration](#the-exception-hierarchy-and-the-status-map).

## One session, one lock

A [`GameSession`][osrlib.crawl.session.GameSession] executes one command at a time and is not safe to share across threads — while FastAPI runs plain `def` endpoints in a threadpool, so any two requests may execute concurrently. The store resolves that tension by pairing every session with its own lock:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:session-store"
```

The session's lock is held across every `execute` and every view read, so one session's commands serialize while separate sessions proceed in parallel; the outer `_store_lock` only guards the dictionaries themselves. The endpoints are deliberately synchronous: the engine is synchronous and CPU-bound, so an async facade would add nothing — the threadpool provides the concurrency, and the lock provides the safety.

## Creating and restoring sessions

A session begins with a stamped party document — the JSON envelope [`party_to_document`][osrlib.core.character.party_to_document] produces and [`party_from_document`][osrlib.core.character.party_from_document] validates — or with a save id from an earlier server-side snapshot. Exactly one of the two, which the request model enforces before the handler ever runs:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:create-session"
```

Two details carry the trust story:

- **The master seed is a server secret.** By default the server draws it (`secrets.randbits(63)`) and no response ever contains it — a client that knows the seed can predict every roll the dungeon will ever make. The optional `seed` field exists for reproducible demos and tests; even when the client supplies it, it never comes back.
- **The response is the schema handshake.** `schema_version` and `engine_version` come from [`osrlib.versioning`][osrlib.versioning], so a client can detect a server whose wire schema is ahead of its own before sending anything else. [Determinism, saves, and replay](../guides/determinism-saves-replay.md) covers what each version stamp guarantees.

## The command endpoint

One endpoint accepts every command in the engine's registry — all 44 of them, each a typed model with its own JSON Schema (see [the command schema reference](../reference/commands/index.md)). [`parse_command`][osrlib.crawl.commands.parse_command] turns the wire payload into a typed command, returning `None` for a `command_type` it has never heard of:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:execute-command"
```

Three distinct fates for a request, in order:

- An **unknown `command_type`** is a 422 before the engine is ever consulted: schemas grow additively, so a newer client may know commands this server doesn't, and the honest answer is "I don't understand", not a guess.
- A **known command with a malformed payload** (a direction that doesn't exist, a negative quantity) raises [`ContentValidationError`][osrlib.errors.ContentValidationError] inside [`parse_command`][osrlib.crawl.commands.parse_command], which the exception handler below maps to 422.
- A **well-formed command** executes under the session lock and returns the [`CommandResult`][osrlib.crawl.commands.CommandResult] envelope: `accepted`, the rejections, and the events — filtered to [`Visibility.PLAYER`][osrlib.core.events.Visibility] on their way out. Referee-visibility events (hidden rolls, referee bookkeeping) never cross the wire.

## Rejections are results, not errors

The split that decides every status code on this page: **an in-fiction rejection is a 200.** When the party tries to walk through a wall, the game said no — that's a rules outcome the client should render, not a transport failure. The response arrives with `accepted: false` and a machine-readable rejection code (see [the rejection code reference](../reference/rejection-codes.md)), and it costs the client nothing: a rejected command draws no dice, advances no clock, and appends nothing to the log, so a confused client can probe freely without corrupting the game. [Sessions, commands, and events](../guides/sessions-commands-events.md) teaches the rejection contract in depth.

Exceptions are the opposite case: the caller broke the *out-of-fiction* contract — sent a malformed document, replayed an incompatible save — and those map to 4xx/5xx statuses.

## The exception hierarchy and the status map

[`osrlib.errors`][osrlib.errors] is a deliberately small, typed hierarchy reserved for out-of-fiction failures:

- [`OsrlibError`][osrlib.errors.OsrlibError] — the root. Catching it catches everything osrlib raises on its own authority.
- [`ContentValidationError`][osrlib.errors.ContentValidationError] — malformed rules content at a library boundary: a dice expression that doesn't parse, an adventure that fails [`validate_adventure`][osrlib.crawl.adventure.validate_adventure], a serialized document whose structure or kind isn't what the loader expects.
- [`SaveVersionError`][osrlib.errors.SaveVersionError] — a document whose `schema_version` is newer than this library understands, raised by [`check_document`][osrlib.versioning.check_document] rather than silently misreading the payload.
- [`ReplayVersionError`][osrlib.errors.ReplayVersionError] — a command log replayed under a different engine version, where any rules change may legitimately alter outcomes. The example never raises it (it exposes no replay endpoint), but a front end that replays command logs owns the same mapping decision.

Two failure families are deliberately *outside* the hierarchy: programmer misuse (bad argument types, out-of-range values) raises stdlib `ValueError` or `TypeError` — a bug in the calling code, not a condition to map — and in-fiction refusals, as above, aren't exceptions at all.

The example registers one handler per exception type it expects, plus a 404 helper for ids that miss the store:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:error-mapping"
```

Everything the wire can answer, in one table:

| Outcome | Where it comes from | Status |
| --- | --- | --- |
| Rejected command | the game said no: `accepted` is `false` | 200 |
| Unknown `command_type` | [`parse_command`][osrlib.crawl.commands.parse_command] returns `None` | 422 |
| [`ContentValidationError`][osrlib.errors.ContentValidationError] | malformed party document, command payload, or save | 422 |
| [`SaveVersionError`][osrlib.errors.SaveVersionError] | a document stamped by a newer library | 409 |
| Unknown session or save id | the store lookup misses | 404 |
| Anything else | a bug, by definition | 500 |

The hierarchy is easy to exercise without a server — this block runs as written:

```python
from osrlib.errors import ContentValidationError, OsrlibError, ReplayVersionError, SaveVersionError
from osrlib.persistence import load_game
from osrlib.versioning import SCHEMA_VERSION, check_document

# The typed hierarchy: every failure osrlib raises on its own authority is an OsrlibError.
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

## The player view at the wire

The only game-state read the API offers is the player projection — [`session.view(Visibility.PLAYER)`][osrlib.crawl.session.GameSession.view], serialized verbatim:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:player-view"
```

There is no referee-view endpoint at all, and that absence is the pattern: never trust the client. The [`PlayerView`][osrlib.crawl.views.PlayerView] is an enumerated whitelist — explored cells, public character sheets, masked magic items, monster groups without hit points — so unexplored geometry, undiscovered secret doors, monster internals, session flags, and the seed can't leak, because they were never in the projection to begin with. A client that renders only what this endpoint returns literally cannot cheat. [Views and visibility](../guides/views-and-visibility.md) walks the whitelist field by field.

## Saves stay on the server

A save document contains everything the wire withholds — the master seed, referee state, the full logs — so the example never sends one anywhere. Snapshots go into a server-side store, and only an opaque id crosses the wire:

```{.python .no-run}
--8<-- "examples/fastapi_crawler/app.py:save-session"
```

Restoring is the `save_id` path through `POST /sessions` [above](#creating-and-restoring-sessions): the server calls [`load_game`][osrlib.persistence.load_game], re-registers its listeners (listeners are live game objects, so a restored session needs them attached again), and hands back a fresh session id. The in-memory store is a deliberate simplification — swapping in a database changes nothing about the pattern.

## Where next

- [Sessions, commands, and events](../guides/sessions-commands-events.md) — the command loop this API wraps: modes, rejections, the event log.
- [Views and visibility](../guides/views-and-visibility.md) — exactly what the player projection contains and why.
- [The command schema reference](../reference/commands/index.md) — every command this endpoint accepts, with its JSON Schema and legal modes.
- [LLM referees](llm-referees.md) — the consumer on the other side of the visibility doctrine: an agent that's *supposed* to see everything.
