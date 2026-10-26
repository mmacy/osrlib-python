"""Save a game, load it back, or rebuild it by replaying what the player did.

A stored game becomes a running [`GameSession`][osrlib.crawl.session.GameSession] again in
one of two ways: load or replay. Save with `save_game`, then restore with one of the other two:

- [`save_game`][osrlib.persistence.save_game]: a session in, a save document out.
- [`load_game`][osrlib.persistence.load_game]: a save document in, a session ready to
  continue out. Use this to resume play.
- [`replay_game`][osrlib.persistence.replay_game]: the seed, the starting party, the
  adventure, the ruleset, and the command log in, the same session rebuilt out. Use this
  to check that the commands reproduce the game.

These members serve special cases:

- [`session_state`][osrlib.persistence.session_state]: a session in, the save's contents
  without the version stamps out.
- [`MIGRATIONS`][osrlib.persistence.MIGRATIONS]: the steps `load_game` runs to bring an
  older save forward.

Most of the time you save and load. [`save_game`][osrlib.persistence.save_game] turns a
session into a plain dictionary you can write as JSON.
[`load_game`][osrlib.persistence.load_game] turns that dictionary back into a session that
continues from where it was. A save includes the adventure content, so loading needs no
other file, and you can give a player a save without the adventure file it came from.

The other way is replay. [`replay_game`][osrlib.persistence.replay_game] starts from the
master seed, the party as it was before play began, the adventure, the ruleset, and the list
of commands the session accepted. It runs the whole game again from the first command.
Every random draw in osrlib comes from a seeded stream, so the second run makes the same
rolls as the first. The replayed session matches a load of the same game, except for what
listeners added: the events they wrote themselves and their saved state. That match also
depends on the party document being the one you took before any session used the party,
because [`GameSession.new`][osrlib.crawl.session.GameSession.new] assigns the member ids
itself. Use replay to audit a game, to reproduce a bug report, or to check that a rules
change changed nothing it shouldn't have.

A save contains the session's state: the party, the adventure content, the ruleset, the
explored dungeon, the clock, the id allocator, the active effects, the spawned monsters and
NPCs, flags, fired triggers, the journal, quest progress, listener state, death records,
defeated monsters, the session mode, the exploration counters, any encounter or battle in
progress, every RNG stream's position, and the master seed. Beside that state, the save
contains two logs of what happened: the accepted commands, and the events, unless you leave
the events out. A load rebuilds the session from the state and re-runs none of the logged
commands.

The two logs do different jobs. [`replay_game`][osrlib.persistence.replay_game] runs the
command log, which every save includes. The event log is the transcript a front end shows.
Leave it out with `include_event_log=False` when the save is only for resuming play.

A save is a stamped document of kind `"save"`, the envelope that
[`osrlib.versioning`][osrlib.versioning] describes. The envelope has two version stamps.
`schema_version` is the save format. [`load_game`][osrlib.persistence.load_game] runs an
older payload through [`MIGRATIONS`][osrlib.persistence.MIGRATIONS], one step at a time,
until it reaches the format this osrlib reads. `engine_version` is the osrlib version that
wrote the save, and it guards replay, because the same commands under different rules can
produce a different game. [`replay_game`][osrlib.persistence.replay_game] raises
[`ReplayVersionError`][osrlib.errors.ReplayVersionError] when you pass it the recorded
version and it doesn't match. Loading works across engine versions, because a load reads
the saved state rather than re-running the commands. A save with a newer `schema_version`
than this osrlib reads doesn't load.

Typical usage:

```python
import json

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character, party_to_document
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game, replay_game, save_game, session_state

rules = Ruleset()
roll = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
pc = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=roll)

# Keep the party as it is before any session uses it. A replay starts from this document.
starting_party = party_to_document([pc.character])

level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

session = GameSession.new(Party(members=[pc.character]), adventure, seed=7, ruleset=rules)
session.execute(EnterDungeon(dungeon_id="crypt"))

# Save, write it out, read it back, and continue from where the party stood.
document = save_game(session)
restored = load_game(json.loads(json.dumps(document)))
print(restored.mode.value)
# exploring

# Replay reaches the same session from the seed and the commands alone.
replayed = replay_game(7, starting_party, adventure, rules, session.command_log)
print(session_state(replayed) == session_state(session))
# True
```
"""

from collections.abc import Callable, Mapping, Sequence
from typing import cast

from osrlib.core.character import Character
from osrlib.core.clock import GameClock
from osrlib.core.effects import EffectsLedger
from osrlib.core.monsters import IdAllocator, MonsterInstance
from osrlib.core.rng import RngStreams, RngStreamState
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.battle import BattleState
from osrlib.crawl.commands import Command, SessionMode, parse_command
from osrlib.crawl.dungeon import DungeonState
from osrlib.crawl.encounter import EncounterState
from osrlib.crawl.events import parse_any_event
from osrlib.crawl.party import Party
from osrlib.crawl.session import (
    DeathRecord,
    DefeatedMonsterRecord,
    DeprivationState,
    GameSession,
    JournalEntry,
    QuestState,
)
from osrlib.errors import ContentValidationError, ReplayVersionError
from osrlib.versioning import SCHEMA_VERSION, check_document, engine_version, stamp_document

__all__ = [
    "MIGRATIONS",
    "load_game",
    "replay_game",
    "save_game",
    "session_state",
]


def _migrate_1_to_2(payload: dict) -> dict:
    """Migrate a schema 1 payload to schema 2 by dropping the recovered-treasure ledger.

    The end-of-adventure award is worked out from the valuation taken when the party left
    town, so a version-1 save drops the ledger field. A version-1 save has no NPC
    adventurers, which arrived with version 2, so the NPC list starts empty.
    """
    # osrlib never read the ledger back, so the field is dropped rather than migrated into a
    # format the code would never read.
    payload.pop("recovered_treasure", None)
    payload["npcs"] = []
    return payload


def _migrate_2_to_3(payload: dict) -> dict:
    """Migrate a schema 2 payload to schema 3 by rewriting a treasure trap's trigger to `"open"`.

    In schema 3, [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec] rejects `trigger="enter"` on
    `kind="treasure"`. The cache code never read that value, so rewriting it to
    `"open"`, the one action that springs a cache, loses nothing. The embedded adventure is
    the only part of a save with trap specs in it, and a treasure trap sits on a feature,
    either on an area or on a level.
    """
    for dungeon in payload.get("adventure", {}).get("dungeons", ()):
        for level in dungeon.get("levels", ()):
            feature_lists = [level.get("features", ())]
            feature_lists.extend(area.get("features", ()) for area in level.get("areas", ()))
            for features in feature_lists:
                for feature in features:
                    trap = feature.get("trap")
                    if isinstance(trap, dict) and trap.get("kind") == "treasure" and trap.get("trigger") == "enter":
                        trap["trigger"] = "open"
    return payload


def _migrate_3_to_4(payload: dict) -> dict:
    """Migrate a schema 3 payload to schema 4 by clearing a logged `withdraw` off every declaration.

    Schema 4 drops `"withdraw"` from
    [`BattleDeclaration.move`][osrlib.crawl.commands.BattleDeclaration.move], leaving the two
    defensive moves the OSE SRD prints, so a declaration that still has the value no longer
    parses. Schema 3 accepted it on any declaration, since no validator checked `move` against
    `action`, and the migration clears it wherever it sits. A `move` declaration becomes
    `action="hold"` with no move, which is what that round played as: the round resolver had no
    branch for a `withdraw`, so the member who declared one stood there. On any other action the
    `move` field was never read, so it is cleared and the action stands. The command log is the
    only place a declaration is stored, since a battle's state contains its round bookkeeping and not
    the round's declarations.

    Schema 4 also drops two fields osrlib never wrote, the boolean on
    [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup] that marked a monster group as having
    given itself up and `discovered_features` on the dungeon state, and with them a character's
    computed `literacy` property, which was never a field and never reached a payload at all.
    Neither field needs clearing here. The models that read those payloads ignore a key they do not
    declare, so a schema 3 save with either one loads with the value dropped and nothing else
    changed. The message code that outcome had, `battle.side` followed by the name of that removed
    `EncounterGroup` field, is gone with it:
    [`MonsterFledEvent`][osrlib.crawl.events.MonsterFledEvent] no longer accepts the code, so an
    event with it fails to parse. No log contains one, because osrlib never set the field.
    """
    for entry in payload.get("command_log", ()):
        if entry.get("command_type") != "resolve_battle_round":
            continue
        for declaration in entry.get("declarations", ()):
            if declaration.get("move") != "withdraw":
                continue
            if declaration.get("action") == "move":
                declaration["action"] = "hold"
            declaration["move"] = None
    return payload


MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _migrate_1_to_2, 2: _migrate_2_to_3, 3: _migrate_3_to_4}
"""The steps that bring an old save payload forward, one schema version at a time.

Each key is a schema version `n`, and its value is a function that takes a payload written
at version `n` and returns it in the format of version `n + 1`.
[`load_game`][osrlib.persistence.load_game] runs the steps for you, from the version the
document was stamped with up to [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION], so a
save from an older release loads with no code of yours.

Read it to check that a version you still have stored can load. A version with no step here
can't, and `load_game` raises
[`ContentValidationError`][osrlib.errors.ContentValidationError], and its message gives the
versions of the missing step. Nothing here is a hook. Adding an entry doesn't extend the library, since
the chain only runs up to [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION].
"""


def session_state(session: GameSession, *, include_event_log: bool = True) -> dict:
    """Serialize a session's whole state, without the document envelope around it.

    This is the payload that [`save_game`][osrlib.persistence.save_game] wraps in its
    envelope. Use it when you don't want the envelope: to embed a session in a larger document
    of your own, to compare two sessions field by field, or to inspect what a session contains.
    Call `save_game` instead whenever you store the result. A payload with no envelope has no
    version stamps, so [`load_game`][osrlib.persistence.load_game] can't read it, and nobody
    can tell later which osrlib wrote it.

    The call doesn't change the session. You can keep the result and serialize it whenever you
    like. If you mean to edit it, copy it first with `copy.deepcopy` or a JSON round trip.
    Some nested values in the result are objects the session also refers to, so an edit to
    one of them changes the session too. These are the values inside a listener's
    state and an event log entry that [`load_game`][osrlib.persistence.load_game] kept as a
    dict.

    Args:
        session: The session to serialize, in any mode, including during an encounter or a
            battle.
        include_event_log: Whether to include the event log. Pass False to leave it out, which
            makes a long game's payload much smaller. The command log is included either way,
            because a replay needs it.

    Returns:
        A new dict of JSON-compatible values, ready for `json.dumps`. It contains the session's
            state, the accepted commands under `command_log`, and, when `include_event_log` is
            True, the events under `event_log`.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession
        from osrlib.persistence import session_state

        rules = Ruleset()
        roll = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        pc = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=roll)

        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
        adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

        session = GameSession.new(Party(members=[pc.character]), adventure, seed=7, ruleset=rules)

        state = session_state(session, include_event_log=False)
        print(state["master_seed"], state["mode"], "event_log" in state)
        # 7 town False
        ```
    """
    payload: dict = {
        "master_seed": session.master_seed,
        "ruleset": session.ruleset.model_dump(mode="json"),
        "party": session.party.model_dump(mode="json"),
        "adventure": session.adventure.model_dump(mode="json"),
        "mode": session.mode.value,
        "clock_rounds": session.clock.rounds,
        "allocator": session.allocator.model_dump(mode="json"),
        "ledger": session.ledger.model_dump(mode="json"),
        "dungeon_state": session.dungeon_state.model_dump(mode="json"),
        "monsters": [instance.model_dump(mode="json") for instance in session.monsters.values()],
        "npcs": [npc.model_dump(mode="json") for npc in session.npcs.values()],
        "flags": dict(session.flags),
        "fired_triggers": list(session.fired_triggers),
        "journal": [entry.model_dump(mode="json") for entry in session.journal],
        "quests": {quest_id: state.model_dump(mode="json") for quest_id, state in session.quests.items()},
        "listener_state": {key: dict(value) for key, value in session.listener_state.items()},
        "death_records": {key: record.model_dump(mode="json") for key, record in session.death_records.items()},
        "defeated_monsters": [record.model_dump(mode="json") for record in session.defeated_monsters],
        "deprivation": {key: state.model_dump(mode="json") for key, state in session.deprivation.items()},
        "treasure_snapshot_cp": session.treasure_snapshot_cp,
        "exploration": {
            "odometer_thirds": session.odometer_thirds,
            "turns_since_rest": session.turns_since_rest,
            "wandering_counter": session.wandering_counter,
            "noise_since_check": session.noise_since_check,
            "sleep_count": session.sleep_count,
            "last_prepared_sleep": dict(session.last_prepared_sleep),
            "alerted_areas": list(session.alerted_areas),
            "heard_areas": list(session.heard_areas),
            "provisions_day": session._provisions_day,
        },
        "encounter": session.encounter.model_dump(mode="json") if session.encounter is not None else None,
        "battle": session.battle.model_dump(mode="json") if session.battle is not None else None,
        "rng_streams": {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()},
        "command_log": [command.model_dump(mode="json") for command in session.command_log],
    }
    if include_event_log:
        payload["event_log"] = [
            entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in session.event_log
        ]
    return payload


def save_game(session: GameSession, *, include_event_log: bool = True) -> dict:
    """Serialize a session to a save document you can store.

    To write a game to disk, pass the result to `json.dumps` and store the string wherever
    you keep saves. Restore it later with [`load_game`][osrlib.persistence.load_game]. Call
    this as often as you like. It reads the session and changes nothing, so you can save in
    any mode, including during an encounter or a battle.

    The result is a stamped document of kind `"save"`, the envelope that
    [`osrlib.versioning`][osrlib.versioning] describes. The session state is under `payload`,
    beside the schema and engine versions. `load_game` checks the schema version before it
    reads the payload. [`session_state`][osrlib.persistence.session_state] gives you the
    payload without the envelope, for when you embed it in a document of your own rather
    than store it.

    Args:
        session: The session to save.
        include_event_log: Whether to include the event log. Pass False to keep only the state
            and the command log, which is all a load needs. A replay also needs the starting
            party, which no save contains.

    Returns:
        A new dict of JSON-compatible values with `kind`, `schema_version`, `engine_version`,
            and `payload` keys.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession
        from osrlib.persistence import save_game

        rules = Ruleset()
        roll = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        pc = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=roll)

        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
        adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

        session = GameSession.new(Party(members=[pc.character]), adventure, seed=7, ruleset=rules)

        document = save_game(session)
        print(document["kind"], sorted(document))
        # save ['engine_version', 'kind', 'payload', 'schema_version']
        ```
    """
    return stamp_document("save", session_state(session, include_event_log=include_event_log))


def _migrate(
    payload: dict, from_version: int, *, migrations: Mapping[int, Callable[[dict], dict]] | None = None
) -> dict:
    """Run the ordered migration chain from `from_version` to the current schema.

    Args:
        payload: The save payload at `from_version`.
        from_version: The document's recorded schema version.
        migrations: The chain to apply. Defaults to
            [`MIGRATIONS`][osrlib.persistence.MIGRATIONS] (tests inject synthetic
            chains here).

    Returns:
        The payload at the current schema version.

    Raises:
        ContentValidationError: If a required migration step is missing.
    """
    chain = MIGRATIONS if migrations is None else migrations
    for version in range(from_version, SCHEMA_VERSION):
        step = chain.get(version)
        if step is None:
            raise ContentValidationError(f"no migration from schema version {version} to {version + 1}")
        payload = step(payload)
    return payload


def load_game(document: Mapping[str, object]) -> GameSession:
    """Restore a session from a save document.

    Pass it the save document, parsed back from JSON, and you get a
    [`GameSession`][osrlib.crawl.session.GameSession] in the state it had when
    [`save_game`][osrlib.persistence.save_game] wrote it. The party is in the same place, with
    the same clock, hit points, and RNG streams, so the next roll is the roll the saved game
    was about to make.

    Code you attached to the session doesn't come back, because a save can't store code. The
    restored session has no listeners and no
    [`action_policies`][osrlib.crawl.session.GameSession.action_policies]. Before you execute
    another command, call [`register_listener`][osrlib.crawl.session.GameSession.register_listener]
    again for each listener, including the [`Interpreter`][osrlib.crawl.interpreter.Interpreter]
    if your game uses it, and set your action policies again. Each listener's state is in the
    save, under its key, and the restored session hands it to the listener on its next call.

    An older save needs nothing from you. The call checks the document, then brings the
    payload forward through [`MIGRATIONS`][osrlib.persistence.MIGRATIONS], one schema version
    at a time, before it rebuilds anything. An event in the event log whose type this osrlib
    doesn't define is kept as a dict and written back out unchanged by the next save, so the log
    loses no entries when an older osrlib loads it.

    Args:
        document: A document that [`save_game`][osrlib.persistence.save_game] produced,
            usually parsed back from JSON.

    Returns:
        The restored session, with no listeners or action policies registered.

    Raises:
        ContentValidationError: If the document isn't a save, if the envelope or the payload
            is malformed, if a logged command is of a type this osrlib doesn't define, or if no
            migration step exists for the document's schema version. A retry with the same
            document fails the same way.
        osrlib.errors.SaveVersionError: If the document's schema version is newer than this osrlib
            reads. Tell the player to upgrade osrlib. The file itself is fine.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession
        from osrlib.persistence import load_game, save_game

        rules = Ruleset()
        roll = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        pc = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=roll)
        party = Party(members=[pc.character])

        level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
        adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

        session = GameSession.new(party, adventure, seed=7)
        document = save_game(session)
        restored = load_game(document)
        assert save_game(restored) == document
        ```
    """
    payload = check_document(document, "save")
    payload = _migrate(payload, int(cast(int, document["schema_version"])))
    try:
        master_seed = int(payload["master_seed"])
        session = GameSession(
            party=Party.model_validate(payload["party"]),
            adventure=Adventure.model_validate(payload["adventure"]),
            ruleset=Ruleset.model_validate(payload["ruleset"]),
            streams=RngStreams(master_seed=master_seed),
            master_seed=master_seed,
        )
        session.mode = SessionMode(str(payload["mode"]))
        session.clock = GameClock(rounds=int(payload["clock_rounds"]))
        session.allocator = IdAllocator.model_validate(payload["allocator"])
        session.ledger = EffectsLedger.model_validate(payload["ledger"])
        session.dungeon_state = DungeonState.model_validate(payload["dungeon_state"])
        session.monsters = {}
        for entry in payload["monsters"]:
            instance = MonsterInstance.model_validate(entry)
            session.monsters[instance.id] = instance
        session.npcs = {}
        for entry in payload["npcs"]:
            npc = Character.model_validate(entry)
            if npc.id is None:
                raise ContentValidationError("an NPC in the save carries no id")
            session.npcs[npc.id] = npc
        session.flags = dict(payload["flags"])
        # Both blocks arrived after schema version 3 and load with empty defaults, so
        # an older save restores unchanged and records them from then on.
        session.fired_triggers = [str(entry) for entry in payload.get("fired_triggers", [])]
        session.journal = [JournalEntry.model_validate(entry) for entry in payload.get("journal", [])]
        if "quests" in payload:
            # For a payload without the block, the session keeps the block the constructor seeded from
            # the save's adventure, which is right for a save from a release whose
            # adventures had no quests: that seed is the empty block anyway.
            session.quests = {key: QuestState.model_validate(value) for key, value in payload["quests"].items()}
        session.listener_state = {key: dict(value) for key, value in payload["listener_state"].items()}
        session.death_records = {
            key: DeathRecord.model_validate(value) for key, value in payload["death_records"].items()
        }
        session.defeated_monsters = [
            DefeatedMonsterRecord.model_validate(entry) for entry in payload["defeated_monsters"]
        ]
        session.deprivation = {
            key: DeprivationState.model_validate(value) for key, value in payload["deprivation"].items()
        }
        snapshot = payload.get("treasure_snapshot_cp")
        session.treasure_snapshot_cp = int(snapshot) if snapshot is not None else None
        exploration = payload["exploration"]
        session.odometer_thirds = int(exploration["odometer_thirds"])
        session.turns_since_rest = int(exploration["turns_since_rest"])
        session.wandering_counter = int(exploration["wandering_counter"])
        session.noise_since_check = bool(exploration["noise_since_check"])
        session.sleep_count = int(exploration["sleep_count"])
        session.last_prepared_sleep = {key: int(value) for key, value in exploration["last_prepared_sleep"].items()}
        session.alerted_areas = list(exploration["alerted_areas"])
        session.heard_areas = list(exploration["heard_areas"])
        session._provisions_day = int(exploration["provisions_day"])
        session.encounter = (
            EncounterState.model_validate(payload["encounter"]) if payload.get("encounter") is not None else None
        )
        session.battle = BattleState.model_validate(payload["battle"]) if payload.get("battle") is not None else None
        session.streams.restore_states(
            {key: RngStreamState.model_validate(value) for key, value in payload["rng_streams"].items()}
        )
        session.command_log = []
        for entry in payload["command_log"]:
            command = parse_command(entry)
            if command is None:
                raise ContentValidationError(f"save command log carries unknown command type {entry!r}")
            session.command_log.append(command)
        session.event_log = []
        for entry in payload.get("event_log", []):
            event = parse_any_event(entry)
            session.event_log.append(event if event is not None else dict(entry))
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ContentValidationError):
            raise
        raise ContentValidationError(f"save payload failed validation: {error}") from error
    return session


def replay_game(
    seed: int,
    party_document: Mapping[str, object],
    adventure: Adventure,
    ruleset: Ruleset,
    commands: Sequence[Command | Mapping[str, object]],
    *,
    recorded_engine_version: str | None = None,
) -> GameSession:
    """Rebuild a session by running its recorded commands again from the seed.

    [`load_game`][osrlib.persistence.load_game] restores a stored state. This call plays the
    game a second time instead: it starts a new session on the same master seed, then
    executes every command in the log, in order. Each random draw comes from a seeded stream,
    so the rolls come out the same, and the session you get back matches a load of the same
    save, apart from what listeners added.

    Use it to audit a game, to reproduce a player's bug report from their save, or to check
    that a rules change you made changed nothing it shouldn't have. Use `load_game` for
    everything else, including resuming play. A replay re-runs the whole game, and gives you
    nothing a load doesn't.

    To replay a stored game, load its save with `load_game` first. Take the seed, the
    adventure, the ruleset, and the command log from the restored session's `master_seed`,
    `adventure`, `ruleset`, and `command_log`. Loading brings an older save up to the
    current schema, so those values are in the format this osrlib reads. Take
    `recorded_engine_version` from the save document's `engine_version`.

    The save doesn't contain the starting party, so keep it yourself. `party_document` must be
    the party as it was before any session used it, because
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] assigns member ids itself, in
    party order, the same way both times. Make that document with
    [`party_to_document`][osrlib.core.character.party_to_document] when you create the party,
    and keep it beside your saves.

    The replayed session gets no listeners. Each command a listener executed during the
    original game, like an [`Interpreter`][osrlib.crawl.interpreter.Interpreter] running a
    trigger's consequences, was accepted and logged, so the replay executes it again from the
    log. A listener registered on a replay would execute those commands a second time, and the
    replay would no longer match the original game. Two things a listener produces aren't
    commands, so a replay doesn't rebuild them: the events it returns itself, and its state.
    The replayed session's `event_log` lacks those events, and its `listener_state` is empty.

    Args:
        seed: The master seed the original session ran under, from the save's `master_seed`.
        party_document: The starting party, from
            [`party_to_document`][osrlib.core.character.party_to_document], made before the
            party joined any session.
        adventure: The adventure the game was played in.
        ruleset: The ruleset the game was played under. A different one can change outcomes,
            and this call doesn't detect that.
        commands: The accepted commands, in order, either as
            [`Command`][osrlib.crawl.commands.Command] objects or as the dicts a save stores
            under `command_log`.
        recorded_engine_version: The `engine_version` from the save the log came from. Pass
            it to have the replay refuse to run under a different osrlib version. Leave it out
            and the replay runs without that check.

    Returns:
        The replayed session, in the state the original reached, with no listeners registered.

    Raises:
        ReplayVersionError: If `recorded_engine_version` is given and doesn't match the
            installed [`engine_version`][osrlib.versioning.engine_version]. The call raises
            before it runs any command. Load the save instead, which works across engine
            versions.
        ContentValidationError: If `party_document` isn't a valid party document, if
            `adventure` fails the checks [`GameSession.new`][osrlib.crawl.session.GameSession.new]
            runs, if a logged command is of a type this osrlib doesn't define or its fields
            don't fit that type, or if the session rejects a logged command. The log contains
            only commands the original session accepted, so a rejection means the replay no
            longer matches the game it was meant to reproduce. Check that you passed the
            starting party, the same ruleset, and the same seed.
        osrlib.errors.SaveVersionError: If a newer osrlib wrote `party_document`.

    Examples:
        ```python
        import json

        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character, party_to_document
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession
        from osrlib.persistence import load_game, replay_game, save_game, session_state

        rules = Ruleset()
        roll = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        pc = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=roll)
        # Keep the party document from before play: the save doesn't contain it.
        starting_party = party_to_document([pc.character])

        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
        adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

        session = GameSession.new(Party(members=[pc.character]), adventure, seed=7, ruleset=rules)
        session.execute(EnterDungeon(dungeon_id="crypt"))
        stored = json.dumps(save_game(session))

        # Later: read the save back, load it, and replay its command log from the seed.
        document = json.loads(stored)
        saved = load_game(document)
        replayed = replay_game(
            saved.master_seed,
            starting_party,
            saved.adventure,
            saved.ruleset,
            saved.command_log,
            recorded_engine_version=document["engine_version"],
        )
        print(session_state(replayed) == session_state(saved))
        # True
        ```
    """
    if recorded_engine_version is not None and recorded_engine_version != engine_version():
        raise ReplayVersionError(
            f"command log recorded under engine {recorded_engine_version}, running {engine_version()}"
        )
    from osrlib.core.character import party_from_document

    party = Party(members=party_from_document(party_document))
    session = GameSession.new(party, adventure, seed=seed, ruleset=ruleset)
    for entry in commands:
        if isinstance(entry, Command):
            command = entry
        else:
            command = parse_command(entry)
            if command is None:
                raise ContentValidationError(f"replay log carries unknown command type {entry!r}")
        result = session.execute(command)
        if not result.accepted:
            codes = [rejection.code for rejection in result.rejections]
            raise ContentValidationError(f"replayed command {command.command_type!r} was rejected: {codes}")
    return session
