# Determinism, saves, and replay

osrlib's central promise is that a game is a pure function of its seed and its command
sequence: every random draw comes from a named [`RngStream`][osrlib.core.rng.RngStream] forked
from a session's master seed, so **the same seed, the same sequence of commands, and the same
engine version always produce the same game.** That guarantee pays for itself several times
over: a bug report needs only a seed and a short command log to reproduce exactly, golden tests
can assert on exact game state instead of approximate behavior, and a saved game reconstructs
byte-for-byte — or replays from scratch — and lands in the identical place either way. [The
complete program](#the-complete-program) at the end of this page runs every claim made here;
every fragment above it is an excerpt.

## The determinism contract

Randomness in osrlib never comes from the stdlib `random` module or a module-level default —
every roll takes an explicit stream, and every stream is one of the small named set forked from
the session's master seed (see [RNG streams](../reference/rng-streams.md) for the full list and
what each one governs). Two sessions built from the same seed and driven through the same
accepted commands consume every stream in the same order and land on the same draws, so their
saves are byte-for-byte identical, not merely equivalent in effect:

```{.python .no-run}
# Same seed, same commands: two independently built sessions save identically.
session_a = play(seed=7)
session_b = play(seed=7)
assert save_game(session_a) == save_game(session_b)
```

## Saves

[`save_game`][osrlib.persistence.save_game] serializes a running
[`GameSession`][osrlib.crawl.session.GameSession] to a JSON-compatible dict: the party, the
embedded adventure content, dungeon state, the clock, every exported RNG stream position, the
master seed, the accepted-command log, and — unless called with `include_event_log=False` — the
event log. [`load_game`][osrlib.persistence.load_game] reconstructs a session from that dict by
restoring each piece exactly, RNG stream positions included, so a loaded game continues drawing
from precisely where the saved game left off:

```{.python .no-run}
# The whole session round-trips through JSON: save -> load -> save is the identity.
document = save_game(session_a)
restored = load_game(document)
assert save_game(restored) == document
```

The event log is the one piece a save doesn't need: it's a record for a front end to display,
never a dependency `load_game` reconstructs state from, which is why `include_event_log=False`
is safe to compact a save with — state reconstructs exactly whether the log rides along or not.

## Replay from a seed and a command log

[`replay_game`][osrlib.persistence.replay_game] takes the same seed, the starting party, the
adventure, and the accepted-command log, and re-executes every command from scratch through a
fresh session — no saved state at all. It raises
[`ReplayVersionError`][osrlib.errors.ReplayVersionError] when the log's recorded engine version
doesn't match the running engine, and
[`ContentValidationError`][osrlib.errors.ContentValidationError] if a logged command is rejected
on replay — a divergence, since the log holds only commands that were accepted the first time.

Because both paths are deterministic, they meet in the middle: restoring a session from its
save, and replaying the same seed against the same command log, land in the identical state.
That equivalence is the standing test the engine holds itself to, and it's also the practical
payoff for consumers — a bug report, an audit trail, or a spectator replay needs only the tiny
seed-plus-commands pair, not a full save file:

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

`replay_game` wants the *pre-session* party document —
[`party_to_document`][osrlib.core.character.party_to_document]'s output from before the party
ever joined a session — because [`GameSession.new`][osrlib.crawl.session.GameSession.new]
assigns member ids itself, in party order, the same way both times.

## Schema versions and migrations

This page is the documented home of [`osrlib.versioning`][osrlib.versioning]. Every serialized
document — saves, commands, events — is wrapped in an envelope carrying a `kind`, a
`schema_version`, and an `engine_version`, produced by
[`stamp_document`][osrlib.versioning.stamp_document] and read back by
[`check_document`][osrlib.versioning.check_document].
[`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] is currently `2`, and it's one integer
shared by every document kind, independent of the package's own release version.

The promise a schema version makes is additive-only: within one version, only new event types
and new optional fields are allowed to appear. Anything else — a rename, a removal, a change in
what a field means — bumps `SCHEMA_VERSION`, and a bump comes with a migration:
[`load_game`][osrlib.persistence.load_game] runs a document's payload through the ordered chain
in [`MIGRATIONS`][osrlib.persistence.MIGRATIONS] before touching it, so a document stamped at an
older schema version still loads. Version 1's single migration is concrete: it drops a
`recovered_treasure` field the version-2 payload no longer carries, and adds the empty `npcs`
list that arrived with version 2. A document saved back at the current floor, schema version 1,
loads the same way a fresh one does:

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

[`engine_version`][osrlib.versioning.engine_version] stamps the exact installed package version
alongside the schema version — separate from it on purpose. `SCHEMA_VERSION` governs whether a
*document* still parses; the engine version governs whether a *replay* still produces the same
draws, since `replay_game` refuses to run a command log recorded under a different engine.

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

- [The kernel à la carte](kernel-a-la-carte.md) — the streams and kernel functions this
  determinism contract is built from.
- [RNG streams](../reference/rng-streams.md) — every named stream and what it governs.
- [Sessions, commands, and events](sessions-commands-events.md) — the command loop that
  produces the command log this page replays.
