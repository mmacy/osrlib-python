# Determinism, saves, and replay

Every random draw in osrlib comes from an explicit, named [`RngStream`][osrlib.core.rng.RngStream] forked from a session's master seed rather than from Python's `random` module or global state (see [RNG streams](../reference/rng-streams.md)). Given the same master seed, starting party, adventure, `Ruleset`, accepted command sequence, player-handle key, and engine version, osrlib produces an identical game state every time. Omitting `player_handle_key` from [`GameSession.new`][osrlib.crawl.session.GameSession.new] deterministically derives it from the seed:

```{.python .no-run}
def play(seed: int) -> tuple[dict, GameSession]:
    party = new_party(seed)
    pre_session_party = party_to_document(party.members)  # capture before GameSession.new assigns IDs
    session = GameSession.new(party, build_adventure(), seed=seed)
    session.execute(EnterDungeon(dungeon_id="crypt"))
    session.execute(MoveParty(direction=Direction.EAST))
    return pre_session_party, session


pre_session_party, session_a = play(seed=7)
_, session_b = play(seed=7)
assert save_game(session_a) == save_game(session_b)
```

osrlib provides two ways to restore a game:

- **Load a save** ([`load_game`][osrlib.persistence.load_game]) to resume play from a serialized save document, restoring exact RNG stream positions, `listener_state`, and `event_log` across engine versions.
- **Replay commands** ([`replay_game`][osrlib.persistence.replay_game]) to reconstruct command-driven session state from scratch for bug reports, audits, or golden tests, using the master seed, pre-session party document, adventure, `Ruleset`, and `command_log` under the same `engine_version`.

## Saves

Call [`save_game`][osrlib.persistence.save_game] to serialize a running [`GameSession`][osrlib.crawl.session.GameSession] to a JSON-compatible dict. The save includes the party, the embedded adventure, dungeon exploration state, the clock, the master seed, every RNG stream's current position, referee and authored progress (`flags`, `listener_state`, `fired_triggers`, `journal`, and `quests`), `command_log`, and `event_log` (see [Listeners and flags](listeners-and-flags.md) and [Gates, triggers, and quests](gates-triggers-quests.md)). If the session has a supplied `player_handle_key`, the save stores it as hex so a restore produces the same opaque feature handles. A full save is a server or referee artifact because it also contains the master seed and other hidden state. Don't send it as a player export.

Call [`load_game`][osrlib.persistence.load_game] to restore a `GameSession` from that dict (or decoded JSON), including its exact RNG stream positions, so play continues drawing from where it stopped:

```{.python .no-run}
document = save_game(session_a)
restored = load_game(json.loads(json.dumps(document)))
assert save_game(restored) == document
```

`load_game` rebuilds the session directly from saved state rather than from `event_log`. Pass `include_event_log=False` to `save_game` when you want smaller save files and do not need the past event transcript. Because listeners are Python objects rather than JSON data, `load_game` returns a session with no registered listeners. Re-register your listeners on the restored session before executing new commands.

## Replay from a seed and command log

Call [`replay_game`][osrlib.persistence.replay_game] with the master seed, the pre-session party document, the adventure, the `Ruleset`, and the recorded `command_log` to rebuild a session from scratch without a save file. When the log contains feature handles created under a supplied key, pass that key as `player_handle_key`. A replay with no key derives one from the master seed, which matches old sessions and new sessions that omitted the key. Capture `pre_session_party = party_to_document(party.members)` ([`party_to_document`][osrlib.core.character.party_to_document]) *before* passing `party` to [`GameSession.new`][osrlib.crawl.session.GameSession.new], because `GameSession.new` assigns session-scoped member IDs in party order when play begins:

```{.python .no-run}
replayed = replay_game(
    seed=7,
    party_document=pre_session_party,
    adventure=build_adventure(),
    ruleset=Ruleset(),
    commands=session_a.command_log,
)
assert save_game(replayed, include_event_log=False) == save_game(session_a, include_event_log=False)
```

For a networked game, generate an independent random key of at least 32 bytes and keep it on the server. osrlib uses HMAC-SHA-256 to turn a fully qualified authored feature reference or a generated cache key into an opaque `feature:` handle. A server key prevents a client that supplies the master seed from controlling the handles. The seed-derived fallback is a deterministic pseudonym, not confidential from someone who knows or can guess the seed.

Pass `recorded_engine_version` to `replay_game` when you want it to verify that the installed engine version matches the version that recorded the log. On a mismatch, `replay_game` raises [`ReplayVersionError`][osrlib.errors.ReplayVersionError]. If any logged command is rejected during replay, `replay_game` raises [`ContentValidationError`][osrlib.errors.ContentValidationError], because `command_log` records only accepted commands.

### Listeners during replay

`replay_game` runs with no listeners registered. Commands that listeners issued during original play (such as trigger consequences from [`Interpreter`][osrlib.crawl.interpreter.Interpreter] or XP awards from a custom listener) are already in `command_log`, so re-executing the log reproduces all state changes those commands caused.

Replay does not rebuild two listener outputs that bypass `command_log`: a listener's private `listener_state` dict and any events a listener returned directly from `handle`. In a replayed session, `listener_state` is empty and `event_log` omits listener-returned events:

- Use [`load_game`][osrlib.persistence.load_game] instead of `replay_game` when resuming play and preserving `listener_state` and `event_log`.
- Have listeners record durable state through referee commands such as [`SetFlag`][osrlib.crawl.commands.SetFlag] when `replay_game` must reproduce that state (see [Listeners and flags](listeners-and-flags.md)).

If you write a custom replay loop that creates a `GameSession` and feeds logged commands to `execute` manually, you may register purely observational listeners (listeners that update `listener_state` or return annotation events without calling `execute`). Do not register command-issuing listeners (especially [`Interpreter`][osrlib.crawl.interpreter.Interpreter]) during a manual replay, because `command_log` already includes the commands they issued during play and running them again would duplicate those commands.

## Schema versions and migrations

Serialized saves, characters, parties, and content packs use a four-key envelope (`kind`, `schema_version`, `engine_version`, and `payload`) managed by [`stamp_document`][osrlib.versioning.stamp_document] and [`check_document`][osrlib.versioning.check_document] in [`osrlib.versioning`][osrlib.versioning].

The two version stamps check different guarantees:

- [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] (currently `4`) tracks the serialized data format across all document kinds. Additive changes (new event classes, new message codes, or new optional fields) retain the same `SCHEMA_VERSION`, whereas breaking format changes (renames, removals, or semantic changes) increment it and add an upgrade step to [`MIGRATIONS`][osrlib.persistence.MIGRATIONS].
- [`engine_version`][osrlib.versioning.engine_version] records the installed osrlib package version. Loading a save works across engine versions because `load_game` restores stored state, whereas `replay_game` checks `recorded_engine_version` because rule changes between releases can alter RNG draws or outcomes.

When `load_game` reads a save with an older `schema_version`, it copies `payload` (leaving the caller's dict unchanged) and runs the migration chain in `MIGRATIONS` up to the current `SCHEMA_VERSION`. If a document has a `schema_version` newer than `SCHEMA_VERSION`, `load_game` raises [`SaveVersionError`][osrlib.errors.SaveVersionError]:

```{.python .no-run}
# A version-1 document -- no "npcs" key, and the ledger field version 2 dropped --
# still loads: the migration adds npcs=[] and discards the stale field.
legacy_payload = dict(document["payload"])
legacy_payload.pop("npcs")
legacy_payload["recovered_treasure"] = {"gp": 100}
legacy_document = {
    "kind": "save",
    "schema_version": 1,
    "engine_version": document["engine_version"],
    "payload": legacy_payload,
}
migrated = load_game(legacy_document)
assert migrated.npcs == {}
```

## The complete program

```python
import json

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character, party_to_document
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game, replay_game, save_game
from osrlib.versioning import SCHEMA_VERSION, engine_version


def build_adventure() -> Adventure:
    # The smallest adventure: a town and a one-corridor dungeon, two cells joined west-east.
    passage = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(passage,))
    town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
    return Adventure(name="A First Delve", town=town, dungeons=(crypt,))


def new_party(seed: int) -> Party:
    # Rolling from the same seed always rolls the same character: no session involved yet.
    rules = Ruleset()
    creation = RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM)
    fighter = create_character(
        name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation
    )
    return Party(members=[fighter.character])


def play(seed: int) -> tuple[dict, GameSession]:
    party = new_party(seed)
    pre_session_party = party_to_document(party.members)  # capture before GameSession.new assigns IDs
    session = GameSession.new(party, build_adventure(), seed=seed)
    session.execute(EnterDungeon(dungeon_id="crypt"))
    session.execute(MoveParty(direction=Direction.EAST))
    return pre_session_party, session


# Same seed, same commands: two independently built sessions save identically.
pre_session_party, session_a = play(seed=7)
_, session_b = play(seed=7)
assert save_game(session_a) == save_game(session_b)

# The whole session round-trips through JSON: save -> load -> save is the identity.
document = save_game(session_a)
restored = load_game(json.loads(json.dumps(document)))
assert save_game(restored) == document
assert document["schema_version"] == SCHEMA_VERSION
assert document["engine_version"] == engine_version()

# load(save) and replay(seed, commands) reach identical state from the pre-session party document.
replayed = replay_game(
    seed=7,
    party_document=pre_session_party,
    adventure=build_adventure(),
    ruleset=Ruleset(),
    commands=session_a.command_log,
)
assert save_game(replayed, include_event_log=False) == save_game(session_a, include_event_log=False)

# A version-1 document -- no "npcs" key, and the ledger field version 2 dropped --
# still loads: the migration adds npcs=[] and discards the stale field.
legacy_payload = dict(document["payload"])
legacy_payload.pop("npcs")
legacy_payload["recovered_treasure"] = {"gp": 100}
legacy_document = {
    "kind": "save",
    "schema_version": 1,
    "engine_version": document["engine_version"],
    "payload": legacy_payload,
}
migrated = load_game(legacy_document)
assert migrated.npcs == {}
```

## Where next

- [Using the rules without a session](rules-without-a-session.md) - calling kernel functions directly with `RngStreams`.
- [RNG streams](../reference/rng-streams.md) - named RNG streams used by `GameSession`.
- [Sessions, commands, and events](sessions-commands-events.md) - executing commands that record to `command_log`.
