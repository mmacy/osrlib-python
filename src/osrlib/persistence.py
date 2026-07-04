"""Save/load, the migration framework, replay, and the load-equals-replay guarantee.

A save is a [`stamp_document`][osrlib.versioning.stamp_document] envelope of kind
`"save"` carrying the full state: party, embedded adventure content (saves are
self-contained, pinned), dungeon state, clock, ledger, allocator, registry monsters,
flags, listener state, mode, crawl counters, exported RNG stream states, the master
seed, the accepted-command log always, and the event log optionally (a saved game
restores from state alone; the logs are records, not dependencies).

A replay is seed + accepted-command log, valid only under the same engine version:
[`replay_game`][osrlib.persistence.replay_game] raises
[`ReplayVersionError`][osrlib.errors.ReplayVersionError] when the log was recorded
under a different engine. The standing test: `load(save)` equals
`replay(seed, commands)` for every crawl golden, at every checkpoint.
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
from osrlib.crawl.session import DeathRecord, DefeatedMonsterRecord, DeprivationState, GameSession
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
    """Schema 1 → 2: drop the recovered-treasure ledger.

    The departure-snapshot valuation delta replaced the ledger as the award's
    honest input, so version-1 saves simply shed the field. NPC adventurers
    arrived with version 2; a version-1 save has none.
    """
    payload.pop("recovered_treasure", None)
    payload["npcs"] = []
    return payload


MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _migrate_1_to_2}
"""Ordered save migrations: `MIGRATIONS[n]` rewrites a version-`n` payload to `n+1`."""


def session_state(session: GameSession, *, include_event_log: bool = True) -> dict:
    """Serialize a session's full state (the save payload, sans envelope).

    Args:
        session: The session to serialize.
        include_event_log: False compacts the save to state plus the command log.

    Returns:
        The JSON-ready state dict.
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
    """Serialize a session to a stamped save document.

    Args:
        session: The session to save.
        include_event_log: False compacts the save (state plus command log only).

    Returns:
        The stamped `"save"` document.
    """
    return stamp_document("save", session_state(session, include_event_log=include_event_log))


def _migrate(
    payload: dict, from_version: int, *, migrations: Mapping[int, Callable[[dict], dict]] | None = None
) -> dict:
    """Run the ordered migration chain from `from_version` to the current schema.

    Args:
        payload: The save payload at `from_version`.
        from_version: The document's recorded schema version.
        migrations: The chain to apply; defaults to
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

    Runs [`check_document`][osrlib.versioning.check_document], then the ordered
    migration chain, then rebuilds the session and restores the RNG streams via
    [`RngStream.restore`][osrlib.core.rng.RngStream.restore]. Event-log entries
    whose types this process doesn't know are preserved as raw records that
    reserialize losslessly — the log is a record, never re-derived, never lossy.

    Args:
        document: A document produced by [`save_game`][osrlib.persistence.save_game].

    Returns:
        The restored session (listeners must be re-registered by the game).

    Raises:
        ContentValidationError: If the envelope or payload is malformed.
        SaveVersionError: If the document's schema version is newer than this
            library understands.
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
    """Re-execute a command log from the seed — the determinism contract exercised.

    Args:
        seed: The master seed the session ran under.
        party_document: The starting party as a stamped `"party"` document (the
            pre-session party; the session re-assigns the same ids).
        adventure: The frozen adventure content.
        ruleset: The ruleset the session ran under.
        commands: The accepted-command log, as commands or their serialized forms.
        recorded_engine_version: The engine version the log was recorded under,
            when known (a save's stamp); a mismatch raises.

    Returns:
        The replayed session, in the exact state the original reached.

    Raises:
        ReplayVersionError: If the log was recorded under a different engine
            version — replays are valid only under the identical engine.
        ContentValidationError: If a command fails to parse, or a logged command
            is rejected on replay (divergence — the log holds accepted commands
            only).
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
