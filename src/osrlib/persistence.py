"""Save a game, load it back, or rebuild it by replaying what the player did.

Two ways lead from a stored game to a running
[`GameSession`][osrlib.crawl.session.GameSession], and they reach the same place.

The one you want most of the time is save and load.
[`save_game`][osrlib.persistence.save_game] turns a live session into a plain dictionary you
can write as JSON, and [`load_game`][osrlib.persistence.load_game] turns that dictionary
back into a session that continues where it left off. A save is self-contained: it includes
the adventure's own content, so loading needs no other file, and you can hand a player a
save without handing them the adventure it came from.

The other way is replay. [`replay_game`][osrlib.persistence.replay_game] starts from
nothing but the master seed, the party as it stood before play began, the adventure, the
ruleset, and the list of commands the player issued, and runs the whole game again from the
first command. Because every random draw in osrlib comes from a seeded stream, the second
run lands on the same rolls as the first, and the session it produces matches the one a load
of the same game produces, field for field. Replay is for auditing a game, reproducing a bug
report, or checking that a rules change moved nothing it shouldn't have.

A save contains the session's whole state: the party, the adventure content, the explored
dungeon, the clock, the active effects, the spawned monsters and NPCs, flags, fired
triggers, the journal, quest progress, listener state, the session mode, the exploration
counters, any encounter or battle in progress, every RNG stream's position, and the master
seed. Beside that state sit two records of what happened: the log of accepted commands, and
the log of events unless you ask for it to be left out. The records are history, not
ingredients. A load rebuilds the session from the state and re-derives nothing from the
logs, which is why loading costs the same however long the game has run.

The two logs do different jobs. The command log is what
[`replay_game`][osrlib.persistence.replay_game] consumes, so a save without it can be loaded
but not replayed. The event log is the transcript a front end shows, and it's the part you
can drop, with `include_event_log=False`, when the save is only meant to be resumed.

A save is a stamped document of kind `"save"`, the envelope described in
[`osrlib.versioning`][osrlib.versioning]. Its `schema_version` is what lets an older save
still load: [`load_game`][osrlib.persistence.load_game] runs the payload through
[`MIGRATIONS`][osrlib.persistence.MIGRATIONS] on the way in, step by step, until it reaches
the shape this library reads. Its `engine_version` is what guards replay, because the same
commands under different rules can produce a different game.
[`replay_game`][osrlib.persistence.replay_game] refuses that with
[`ReplayVersionError`][osrlib.errors.ReplayVersionError] when you give it the recorded
version to compare. Loading a save across engine versions stays fine, since a load reads
state rather than re-deriving it.

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

# Keep the party as it stands before any session touches it: that is what a replay starts from.
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
    # Nothing read the ledger back, so the field is dropped rather than migrated into a
    # shape no code consumes.
    payload.pop("recovered_treasure", None)
    payload["npcs"] = []
    return payload


def _migrate_2_to_3(payload: dict) -> dict:
    """Migrate a schema 2 payload to schema 3 by rewriting a treasure trap's trigger to `"open"`.

    In schema 3, [`TrapSpec`][osrlib.crawl.dungeon.TrapSpec] rejects `trigger="enter"` on
    `kind="treasure"`. Nothing on the cache path ever read that value, so rewriting it to
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


MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _migrate_1_to_2, 2: _migrate_2_to_3}
"""The steps that bring an old save payload forward, one schema version at a time.

`MIGRATIONS[n]` rewrites a payload written at schema version `n` into the shape version
`n + 1` expects. [`load_game`][osrlib.persistence.load_game] walks the chain for you, from
whatever version the document was stamped with up to
[`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION], so a save from an older release loads
without any code of yours.

Read it when you want to know what an old save loses or gains on the way in, or to check
that a version you still have stored can be loaded at all: a version with no step in this
chain can't, and `load_game` raises
[`ContentValidationError`][osrlib.errors.ContentValidationError] naming the missing step.
Nothing here is a hook. Adding an entry doesn't extend the library, since the chain only
ever runs as far as the schema versions this release knows about.
"""


def session_state(session: GameSession, *, include_event_log: bool = True) -> dict:
    """Serialize a session's whole state, without the document envelope around it.

    This is the payload [`save_game`][osrlib.persistence.save_game] stamps, offered on its
    own for when the envelope is in your way: embedding a session inside a larger document of
    your own, comparing two sessions field by field, or inspecting what a session contains.
    Call `save_game` instead whenever you mean to store the result, because a payload with no
    envelope has no version stamps, and nothing can tell later which osrlib wrote it.

    Nothing on the session changes, and the result shares no mutable structure with it, so
    you can keep it, edit it, and serialize it whenever you like.

    Args:
        session: The session to serialize. It may be in any mode, mid-encounter or
            mid-battle included.
        include_event_log: Whether to include the transcript. Pass False to leave the event
            log out, which makes a long game's save much smaller. The accepted-command log is
            included either way, because a replay needs it.

    Returns:
        A new dict of JSON-compatible values, ready for `json.dumps`, with the session's
            state, the accepted-command log under `command_log`, and the transcript under
            `event_log` when `include_event_log` is True.

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

    This is how you write a game to disk: take the result, hand it to `json.dumps`, and put
    it wherever you keep saves. Call it as often as you like. It reads the session and
    changes nothing, so saving mid-encounter or mid-battle is as safe as saving in town.

    The result is a stamped document of kind `"save"`, described in
    [`osrlib.versioning`][osrlib.versioning]. The session state sits under `payload`, wrapped
    in the schema and engine versions that tell a later
    [`load_game`][osrlib.persistence.load_game] what it's reading.
    [`session_state`][osrlib.persistence.session_state] gives you the payload without the
    envelope, for when you're embedding it in a document of your own rather than storing it.

    Args:
        session: The session to save.
        include_event_log: Whether to include the transcript. Pass False to leave the event
            log out and keep only the state and the accepted-command log, which is all a
            resume or a replay needs.

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

    Hand it what you read back from storage and you get a live
    [`GameSession`][osrlib.crawl.session.GameSession], standing where it stood when
    [`save_game`][osrlib.persistence.save_game] wrote it: same position, same clock, same hit
    points, same RNG streams, so the next roll is the roll the saved game was about to make.

    One thing doesn't come back. Listeners are your code, and a save can't store code, so the
    restored session has none registered. Call
    [`register_listener`][osrlib.crawl.session.GameSession.register_listener] again for each
    one, including the [`Interpreter`][osrlib.crawl.interpreter.Interpreter] if your game
    uses it, before you execute another command. Each listener's own state was saved and is
    waiting under its key.

    An older save needs nothing from you. The document is checked, then walked forward
    through [`MIGRATIONS`][osrlib.persistence.MIGRATIONS] one schema version at a time before
    anything is rebuilt. An event in the transcript whose type this version of osrlib doesn't
    recognize is kept as it was found and written back out unchanged on the next save, so a
    log loses no entries by passing through an older library.

    Args:
        document: A document produced by [`save_game`][osrlib.persistence.save_game], usually
            parsed back from JSON.

    Returns:
        The restored session, with no listeners registered.

    Raises:
        ContentValidationError: If the envelope or the payload is malformed, if a logged
            command is of a type this version doesn't know, or if no migration step exists
            for the document's schema version.
        SaveVersionError: If a newer osrlib wrote the document. Tell the player to upgrade.
            There's nothing to repair in the file.

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
        # an older save restores unchanged and starts remembering from there.
        session.fired_triggers = [str(entry) for entry in payload.get("fired_triggers", [])]
        session.journal = [JournalEntry.model_validate(entry) for entry in payload.get("journal", [])]
        if "quests" in payload:
            # A payload without the block keeps the seed the constructor built from
            # the save's own adventure, which is right for a save from a release whose
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

    Where [`load_game`][osrlib.persistence.load_game] restores a stored state, this plays the
    game a second time: a fresh session on the same master seed, then every command in the
    log, in order. Each random draw comes from a seeded stream, so the rolls fall the same
    way, and the session you get back matches the one a load of the same save produces.

    Use it to audit a game, to reproduce a player's bug report from their save, or to check
    that a rules change you made moved nothing it shouldn't have. Use `load_game` for
    everything else, including resuming play, because a replay costs the whole game again and
    gives you nothing a load doesn't.

    Four of the five inputs come straight out of a save document's payload, under
    `master_seed`, `adventure`, `ruleset`, and `command_log`. The fifth, `party_document`, is
    the one you have to plan for. It must be the party as it stood before any session touched
    it, because [`GameSession.new`][osrlib.crawl.session.GameSession.new] assigns member ids
    itself, in party order, the same way both times. Take that document with
    [`party_to_document`][osrlib.core.character.party_to_document] when you roll the party,
    and keep it beside your saves.

    The replayed session gets no listeners, and needs none. Everything a listener did during
    the original game, whether an interpreter firing a trigger's consequences or your own
    code awarding a prize, it did by issuing a command the session accepted and logged. Those
    commands are in the log already, and re-executing them rebuilds every effect. A listener
    registered on a replay would issue them a second time and pull the game off course.

    Args:
        seed: The master seed the original session ran under, from the save's `master_seed`.
        party_document: The starting party, stamped by
            [`party_to_document`][osrlib.core.character.party_to_document] before the party
            joined any session.
        adventure: The adventure the game was played in.
        ruleset: The ruleset the game was played under. A different one can change outcomes,
            and nothing here detects that.
        commands: The accepted commands, in order, either as
            [`Command`][osrlib.crawl.commands.Command] objects or as the dicts a save stores
            under `command_log`.
        recorded_engine_version: The `engine_version` from the save this log came from. Pass
            it to have the replay refuse to run under different rules. Leave it out and the
            replay runs unchecked.

    Returns:
        The replayed session, in the state the original reached, with no listeners registered.

    Raises:
        ReplayVersionError: If `recorded_engine_version` is given and doesn't match the
            running [`engine_version`][osrlib.versioning.engine_version]. Load the save
            instead, which works across engine versions.
        ContentValidationError: If a logged command is of a type this version doesn't know,
            or if a logged command is refused this time. The log contains only commands that
            were accepted the first time, so a refusal means the replay has diverged from the
            game it was meant to reproduce.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character, party_to_document
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import EnterDungeon
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.crawl.party import Party
        from osrlib.crawl.session import GameSession
        from osrlib.persistence import replay_game, session_state

        rules = Ruleset()
        roll = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        pc = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=roll)
        starting_party = party_to_document([pc.character])

        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
        town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
        adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

        session = GameSession.new(Party(members=[pc.character]), adventure, seed=7, ruleset=rules)
        session.execute(EnterDungeon(dungeon_id="crypt"))

        replayed = replay_game(7, starting_party, adventure, rules, session.command_log)
        print(session_state(replayed) == session_state(session))
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
