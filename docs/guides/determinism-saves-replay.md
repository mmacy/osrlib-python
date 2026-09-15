# Determinism, saves, and replay

In osrlib, a game is a pure function of its seed and its command sequence. Every random draw comes from a named [`RngStream`][osrlib.core.rng.RngStream] forked from a session's master seed, so **the same seed, the same sequence of commands, and the same engine version always produce the same game.**

You get three things out of that guarantee. A bug report needs only a seed and a short command log to reproduce a failure exactly. Golden tests can assert on exact game state instead of approximate behavior. And a saved game reconstructs byte-for-byte, or replays from scratch, and lands in the identical place either way. Every snippet below comes from [the complete program](#the-complete-program) at the end, and running that program checks every claim made here.

## The determinism contract

Randomness in osrlib never comes from the stdlib `random` module or a module-level default. Every roll takes an explicit stream, and every stream is one of the small named set forked from the session's master seed. See [RNG streams](../reference/rng-streams.md) for the full list and what each stream governs.

Two sessions built from the same seed and driven through the same accepted commands consume every stream in the same order and land on the same draws. Their saves are byte-for-byte identical, not just equivalent in effect:

```{.python .no-run}
# Same seed, same commands: two independently built sessions save identically.
session_a = play(seed=7)
session_b = play(seed=7)
assert save_game(session_a) == save_game(session_b)
```

## Saves

[`save_game`][osrlib.persistence.save_game] serializes a running [`GameSession`][osrlib.crawl.session.GameSession] to a JSON-compatible dict: the party, the embedded adventure content, dungeon state, the clock, every exported RNG stream position, the master seed, the session-state blocks the extension and authored layers write (the flag store, each registered listener's state slot, the trigger fired-marks, the journal, and quest state), the accepted-command log, and the event log. Pass `include_event_log=False` to leave the event log out. Your authored progress survives, all of it, by construction. For more about the session-state blocks, see [Listeners and flags](listeners-and-flags.md) and [Gates, triggers, and quests](gates-triggers-quests.md).

[`load_game`][osrlib.persistence.load_game] reconstructs a session from that dict by restoring each piece exactly, RNG stream positions included, so a loaded game continues drawing from precisely where the saved game left off:

```{.python .no-run}
# The whole session round-trips through JSON: save -> load -> save is the identity.
document = save_game(session_a)
restored = load_game(document)
assert save_game(restored) == document
```

The event log is the one piece a save doesn't need. It's a record for a front end to display, never a dependency `load_game` reconstructs state from, so `include_event_log=False` is safe to compact a save with: state reconstructs exactly whether the log is in the save or not.

## Replay from a seed and a command log

[`replay_game`][osrlib.persistence.replay_game] takes the same seed, the starting party, the adventure, and the accepted-command log, and re-executes every command from scratch through a fresh session, with no saved state at all. It raises [`ReplayVersionError`][osrlib.errors.ReplayVersionError] when the log's recorded engine version doesn't match the running engine. That check runs only when you pass the version a save recorded (through the `recorded_engine_version` argument). It raises [`ContentValidationError`][osrlib.errors.ContentValidationError] if a logged command is rejected on replay. That's a divergence, since the log contains only commands that were accepted the first time.

Both paths are deterministic, so they agree: restoring a session from its save, and replaying the same seed against the same command log, land in the identical state. osrlib's test suite asserts that equivalence. The practical payoff is that a bug report, an audit trail, or a spectator replay needs only the seed and the commands, not a full save file:

```{.python .no-run}
# load(save) and replay(seed, commands) are two different paths to the identical state:
# the party document must be the *pre-session* party, the same starting point the
# original session assigned ids from.
pre_session_party = party_to_document(new_party(seed=7).members)
replayed = replay_game(
    seed=7,
    party_document=pre_session_party,
    adventure=build_adventure(),
    ruleset=Ruleset(),
    commands=session_a.command_log,
)
assert save_game(replayed, include_event_log=False) == save_game(session_a, include_event_log=False)
```

Pass `replay_game` the *pre-session* party document, the output [`party_to_document`][osrlib.core.character.party_to_document] gives you before the party ever joined a session, because [`GameSession.new`][osrlib.crawl.session.GameSession.new] assigns member ids itself, in party order, the same way both times.

### Replay runs with no listeners

`replay_game` builds its session with **no listeners registered**, and that's enough. Every reaction a listener issued live, like the interpreter's trigger consequences or a game listener's awards, was an ordinary command that the session accepted and logged, so re-executing the log rebuilds every one of those effects. The commands are already in the log, and nothing needs to react again.

The rule for a load and the rule for a replay point in opposite directions. After [`load_game`][osrlib.persistence.load_game], re-register your listeners before you execute *new* commands: a restored session that keeps playing needs its code attached again. `replay_game` gives you no such choice, because it builds the session itself and takes no listeners at all. The choice comes up only when you drive a replay by hand, building your own session and feeding it the log through `execute`, and then it turns on what the listener does. A listener that only observes, by accumulating `listener_state` and returning annotation events, can be registered and reproduces its state exactly. A listener that reacts by **issuing commands**, and the [`Interpreter`][osrlib.crawl.interpreter.Interpreter] above all, must **not** be registered. The log already contains every command that listener issued live, and a second issuer would issue them again and diverge from the recorded game.

## Schema versions and migrations

The version helpers live in [`osrlib.versioning`][osrlib.versioning]. Every serialized document (a save, a command, an event) goes into an envelope that contains a `kind`, a `schema_version`, and an `engine_version`. [`stamp_document`][osrlib.versioning.stamp_document] produces the envelope and [`check_document`][osrlib.versioning.check_document] reads it back. [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] is currently `4`, one integer shared by every document kind, independent of the package's own release version.

A schema version is additive-only: within one version, only new event types and new optional fields can appear. Anything else, like a rename, a removal, or a change in what a field means, bumps `SCHEMA_VERSION`, and a bump comes with a migration. [`load_game`][osrlib.persistence.load_game] runs a document's payload through the ordered chain in [`MIGRATIONS`][osrlib.persistence.MIGRATIONS] before it rebuilds anything, so a document stamped at an older schema version still loads.

Three migrations have shipped. The step from version 1 to version 2 drops a `recovered_treasure` field that a version-2 payload no longer includes, and adds the empty `npcs` list that arrived with version 2. A payload contains the NPC roster as a list, and `load_game` rebuilds it into the session's `npcs` dict keyed by id, which is what the assertion below reads back. The step from version 2 to version 3 is a lossless rewrite: version 3 rejects `trigger="enter"` on a treasure trap, a value the cache path never read, so the migration rewrites it to `"open"`, the one springing action a cache has. The step from version 3 to version 4 is another: version 4 drops `"withdraw"` from a battle declaration's `move`, a value the round resolver never moved the party for, so the migration clears it off a logged declaration. A declaration whose action was `move` becomes `action="hold"` with no move, which is what that round played as, and a declaration that carried the value beside some other action keeps that action and loses a field nothing read. A document saved at the floor, schema version 1, runs the whole chain and loads the same way a fresh one does:

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

[`engine_version`][osrlib.versioning.engine_version] stamps the exact installed package version alongside the schema version, and the two are separate on purpose. `SCHEMA_VERSION` governs whether a *document* still parses. The engine version governs whether a *replay* still produces the same draws: pass `replay_game` the recorded engine version and it refuses a log recorded under a different one.

## The complete program

```python
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


def play(seed: int) -> GameSession:
    session = GameSession.new(new_party(seed), build_adventure(), seed=seed)
    session.execute(EnterDungeon(dungeon_id="crypt"))
    session.execute(MoveParty(direction=Direction.EAST))
    return session


# Same seed, same commands: two independently built sessions save identically.
session_a = play(seed=7)
session_b = play(seed=7)
assert save_game(session_a) == save_game(session_b)

# The whole session round-trips through JSON: save -> load -> save is the identity.
document = save_game(session_a)
restored = load_game(document)
assert save_game(restored) == document
assert document["schema_version"] == SCHEMA_VERSION
assert document["engine_version"] == engine_version()

# load(save) and replay(seed, commands) are two different paths to the identical state:
# the party document must be the *pre-session* party, the same starting point the
# original session assigned ids from.
pre_session_party = party_to_document(new_party(seed=7).members)
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

- [Using the rules without a session](rules-without-a-session.md) - the streams and kernel functions this determinism contract is built from.
- [RNG streams](../reference/rng-streams.md) - every named stream and what it governs.
- [Sessions, commands, and events](sessions-commands-events.md) - the command loop that produces the command log `replay_game` re-executes.
