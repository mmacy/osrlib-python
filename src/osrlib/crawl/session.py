"""`GameSession`: command dispatch, the event log, listeners, flags, views.

The session owns what the kernel has been promising since Phase 2: the
[`RngStreams`][osrlib.core.rng.RngStreams] (master seed), the
[`IdAllocator`][osrlib.core.monsters.IdAllocator], the
[`EffectsLedger`][osrlib.core.effects.EffectsLedger], the
[`GameClock`][osrlib.core.clock.GameClock], the entity registry (characters and
live monster instances), the flag store, the listener-state store, the command and
event logs, the mode, and the crawl state.

`execute(command)` runs the pure validation pre-phase: a rejected command consumes
no draws, no clock time, mutates nothing, and is excluded from the command log —
the Phase 0 contract, enforced end to end. Accepted commands mutate, append their
events to the log, then listeners run in registration order, their events appended
to the same result and log.

Command handlers live in [`osrlib.crawl.exploration`][osrlib.crawl.exploration],
[`osrlib.crawl.encounter`][osrlib.crawl.encounter], and
[`osrlib.crawl.battle`][osrlib.crawl.battle]; each is one function
`(session, command) -> (rejections, events)` whose discipline is validation first —
no draw, no mutation, no time before the last rejection check. The session-owned
referee and town commands are handled here.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

from osrlib.core.alignment import Alignment
from osrlib.core.character import ADVANCEMENT_STREAM, Character
from osrlib.core.classes import apply_xp
from osrlib.core.clock import ROUNDS_PER_DAY, ROUNDS_PER_TURN, GameClock, TimeUnit
from osrlib.core.effects import EFFECTS_STREAM, EffectsLedger
from osrlib.core.events import (
    DamageDealtEvent,
    DeathEvent,
    EffectExpiredEvent,
    Event,
    SavingThrowRolledEvent,
    Visibility,
)
from osrlib.core.items import ItemInstance
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, MonsterInstance, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.validation import Rejection
from osrlib.crawl.adventure import Adventure, validate_adventure
from osrlib.crawl.commands import (
    AdvanceTime,
    AwardXP,
    Command,
    CommandResult,
    GrantCoins,
    GrantItem,
    IdentifyItem,
    PlaceParty,
    SessionMode,
    SetDoorState,
    SetFlag,
    SpawnMonsters,
    SpawnNpcParty,
)
from osrlib.crawl.dungeon import DungeonState, edge_ref
from osrlib.crawl.events import (
    DoorEvent,
    FlagSetEvent,
    ItemAcquiredEvent,
    LightEvent,
    LocationEnteredEvent,
    MonstersSpawnedEvent,
    TimeAdvancedEvent,
    XpAwardedEvent,
)
from osrlib.crawl.party import Party
from osrlib.data import load_equipment, load_monsters
from osrlib.versioning import SCHEMA_VERSION, engine_version

if TYPE_CHECKING:
    from osrlib.crawl.views import PlayerView, RefereeView

__all__ = [
    "ENCOUNTER_STREAM",
    "EXPLORATION_STREAM",
    "LIGHT_EFFECT_KINDS",
    "MONSTER_ACTION_STREAM",
    "WANDERING_STREAM",
    "DeathRecord",
    "DefeatedMonsterRecord",
    "DeprivationState",
    "GameSession",
    "Listener",
]

WANDERING_STREAM = "wandering"
"""Stream key for wandering-monster draws: the check die, the d20, counts, variant picks."""

ENCOUNTER_STREAM = "encounter"
"""Stream key for encounter-procedure draws: surprise, distance, reaction, distraction."""

EXPLORATION_STREAM = "exploration"
"""Stream key for exploration draws: forcing, listening, searching, traps, tinder, skills."""

MONSTER_ACTION_STREAM = "monster_action"
"""Stream key for the action policy's draws — a policy change never shifts combat draws."""

LIGHT_EFFECT_KINDS = frozenset({"light", "continual_light"})
"""The light-family effect kinds: torch/lantern attachments and the light spells."""

DARKNESS_EFFECT_KINDS = frozenset({"darkness", "continual_darkness"})
"""The darkness-family effect kinds, whose radii swallow a marching party (pinned)."""


class DeathRecord(BaseModel):
    """When and how a character died — the honest inputs for revival windows.

    `cause` is `"poison"` when the killing resolution was a poison save or a
    poison-delay expiry (feeding *neutralize poison*'s round window), else the
    source kind; *raise dead*'s day count reads `round` regardless of cause.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    cause: str


class DefeatedMonsterRecord(BaseModel):
    """One defeated monster — the Phase 5 XP-award input."""

    model_config = ConfigDict(frozen=True)

    monster_id: str
    template_id: str
    outcome: str
    xp: int


class DeprivationState(BaseModel):
    """One member's food and water deprivation counters (worse track applies)."""

    model_config = ConfigDict(validate_assignment=True)

    food_days: int = 0
    water_days: int = 0

    @property
    def worst(self) -> int:
        """The worse track — deprivation doesn't stack (pinned)."""
        return max(self.food_days, self.water_days)


class Listener(Protocol):
    """The extension-point protocol: games register listeners on the session.

    Listeners never mutate game state — they react by executing ordinary
    commands. `handle` receives the accumulated events of the command (earlier
    listeners' included) and the listener's own state snapshot, and returns the
    events to append plus the new state (snapshotted into saves under `key`).
    """

    key: str

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        """React to one command's events."""
        ...


class GameSession:
    """A running game: the single entry point for command execution and views."""

    def __init__(
        self,
        *,
        party: Party,
        adventure: Adventure,
        ruleset: Ruleset,
        streams: RngStreams,
        master_seed: int,
    ) -> None:
        """Internal constructor — use [`GameSession.new`][osrlib.crawl.session.GameSession.new] or `load_game`."""
        self.party = party
        self.adventure = adventure
        self.ruleset = ruleset
        self.streams = streams
        self.master_seed = master_seed
        self.allocator = IdAllocator()
        self.ledger = EffectsLedger()
        self.clock = GameClock()
        self.mode = SessionMode.TOWN
        self.dungeon_state = DungeonState()
        self.monsters: dict[str, MonsterInstance] = {}
        self.npcs: dict[str, Character] = {}
        self.flags: dict[str, str | int | bool] = {}
        self.listener_state: dict[str, dict] = {}
        self.listeners: list[Listener] = []
        self.command_log: list[Command] = []
        self.event_log: list[Event | dict] = []
        self.death_records: dict[str, DeathRecord] = {}
        self.defeated_monsters: list[DefeatedMonsterRecord] = []
        self.deprivation: dict[str, DeprivationState] = {}
        self.treasure_snapshot_cp: int | None = None
        # Exploration bookkeeping (all serialized into saves).
        self.odometer_thirds = 0
        self.turns_since_rest = 0
        self.wandering_counter = 0
        self.noise_since_check = False
        self.sleep_count = 0
        self.last_prepared_sleep: dict[str, int] = {}
        self.alerted_areas: list[str] = []
        self.heard_areas: list[str] = []
        self.encounter = None  # EncounterState | None (set by osrlib.crawl.encounter)
        self.battle = None  # BattleState | None (set by osrlib.crawl.battle)
        self._provisions_day = 0
        # Runtime extension points, re-registered by the game like listeners —
        # never serialized (policies are code).
        self.action_policies: dict[str, object] = {}

    @classmethod
    def new(cls, party: Party, adventure: Adventure, *, seed: int, ruleset: Ruleset | None = None) -> GameSession:
        """Create a new session, validating the adventure and assigning member ids.

        Character ids assign as `character-NNNN` from the session's allocator in
        party order — the pinned prefix, closing the Phase 1 seam. Members that
        already carry ids keep them (a party loaded from an earlier session).

        Args:
            party: The party, in marching order.
            adventure: The frozen adventure content.
            seed: The master seed.
            ruleset: The ruleset in play; defaults to a stock `Ruleset()`.

        Returns:
            The session, in town, at round 0.

        Raises:
            ContentValidationError: If the adventure has dangling references.
        """
        validate_adventure(adventure, load_monsters(), load_equipment())
        session = cls(
            party=party,
            adventure=adventure,
            ruleset=ruleset if ruleset is not None else Ruleset(),
            streams=RngStreams(master_seed=seed),
            master_seed=seed,
        )
        for member in party.members:
            if member.id is None:
                member.id = session.allocator.allocate("character")
        return session

    @property
    def metadata(self) -> dict[str, object]:
        """The front-end handshake: the schema and engine versions."""
        return {"schema_version": SCHEMA_VERSION, "engine_version": engine_version()}

    # ------------------------------------------------------------------ dispatch

    def execute(self, command: Command) -> CommandResult:
        """Execute one command: the pure validation pre-phase, then apply and log.

        Args:
            command: The command to execute.

        Returns:
            The result envelope; rejected commands carry rejections and no events.
        """
        if self.mode not in type(command).allowed_modes:
            return CommandResult(
                accepted=False,
                rejections=(
                    Rejection(
                        code="session.command.wrong_mode",
                        params={"command": command.command_type, "mode": self.mode.value},
                    ),
                ),
            )
        handler = _handlers().get(type(command))
        if handler is None:
            raise ValueError(f"no handler for command type {command.command_type!r}")
        rejections, events = handler(self, command)
        if rejections:
            return CommandResult(accepted=False, rejections=tuple(rejections))
        self.command_log.append(command)
        self._record_deaths(events)
        self.event_log.extend(events)
        accumulated = list(events)
        for listener in self.listeners:
            emitted, state = listener.handle(tuple(accumulated), self.listener_state.get(listener.key, {}))
            self.listener_state[listener.key] = state
            accumulated.extend(emitted)
            self.event_log.extend(emitted)
        return CommandResult(accepted=True, events=tuple(accumulated))

    def register_listener(self, listener: Listener) -> None:
        """Register a listener; it runs after each command in registration order.

        Args:
            listener: The listener; its state snapshots into saves under its key.
        """
        self.listeners.append(listener)
        self.listener_state.setdefault(listener.key, {})

    # ------------------------------------------------------------------ registry

    def registry(self) -> dict[str, object]:
        """Live entities by id: party members (marching order), then monsters, then NPCs."""
        entities: dict[str, object] = {member.id: member for member in self.party.members}
        entities.update(self.monsters)
        entities.update(self.npcs)
        return entities

    def combatant(self, combatant_id: str) -> object | None:
        """Return the monster or NPC with `combatant_id`, or `None`.

        The encounter side model's lookup: `EncounterGroup.monster_ids` is the
        combatant-id list it always structurally was, spanning monsters and NPC
        adventurers.

        Args:
            combatant_id: The combatant's entity id.

        Returns:
            The live instance, or `None` when the id is unknown.
        """
        found = self.monsters.get(combatant_id)
        if found is not None:
            return found
        return self.npcs.get(combatant_id)

    def member(self, character_id: str) -> Character:
        """Return the party member with `character_id` (see [`Party.member`][osrlib.crawl.party.Party.member])."""
        return self.party.member(character_id)

    def spawn(self, template_id: str, count: int, *, alignment: Alignment | None = None) -> list[MonsterInstance]:
        """Spawn `count` instances into the registry, ids from the session allocator.

        Args:
            template_id: The monster template id.
            count: How many to spawn.
            alignment: An alignment pin from keyed content.

        Returns:
            The spawned instances, in spawn order.
        """
        template = load_monsters().get(template_id)
        spawned = []
        for _ in range(count):
            instance = spawn_monster(
                template,
                id=self.allocator.allocate("monster"),
                stream=self.streams.get(MONSTER_SPAWN_STREAM),
                alignment=alignment,
            )
            self.monsters[instance.id] = instance
            spawned.append(instance)
        return spawned

    # ------------------------------------------------------------------ time

    def advance_rounds(self, n: int) -> list[Event]:
        """Advance the clock `n` rounds through the ledger, translating light expiries.

        A light-kind expiry is referee visibility; the session appends the
        player-facing `exploration.light.expired` with the source kind (the pinned
        mechanism). Day-boundary crossings consume provisions.

        Args:
            n: How many rounds.

        Returns:
            The ledger's events plus the player-facing translations.
        """
        member_ids = {member.id for member in self.party.members}
        light_sources = {
            effect.effect_id: (str(effect.definition.params.get("source", effect.definition.kind)), effect.target_ref)
            for effect in self.ledger.effects
            if effect.definition.kind in LIGHT_EFFECT_KINDS
        }
        events = self.ledger.advance(
            self.clock,
            n,
            TimeUnit.ROUND,
            self.registry(),
            stream=self.streams.get(EFFECTS_STREAM),
            allocator=self.allocator,
        )
        out: list[Event] = []
        for event in events:
            out.append(event)
            if isinstance(event, EffectExpiredEvent) and event.effect_id in light_sources:
                source, bearer = light_sources[event.effect_id]
                out.append(
                    LightEvent(
                        code="exploration.light.expired",
                        character_id=bearer if bearer in member_ids else None,
                        source=source,
                    )
                )
        while self.clock.rounds // ROUNDS_PER_DAY > self._provisions_day:
            from osrlib.crawl import exploration

            self._provisions_day += 1
            out.extend(exploration.consume_provisions(self))
        return out

    def advance_turns(
        self, turns: int, *, resting: bool = False, field: bool | None = None
    ) -> tuple[list[Event], bool]:
        """Advance whole turns one at a time, running the per-turn bookkeeping.

        A mid-turn clock snaps to the next turn boundary first — turn-costing
        actions absorb partial round-time (the pinned odometer bookkeeping). Each
        turn: the ledger advances, day boundaries consume provisions, the rest
        cadence counts (unless `resting`), and — in the field — the wandering
        cadence may fire a check that starts an encounter, which stops the
        advance.

        Args:
            turns: How many turns to advance.
            resting: True during a `Rest` (the cadence doesn't count, and the
                wandering chance takes the resting −1).
            field: Whether the wandering cadence runs; defaults to "exploring in a
                dungeon" (town time and travel are abstract, no wandering there).

        Returns:
            The events, and True when a wandering encounter interrupted the span.
        """
        from osrlib.crawl import exploration

        events: list[Event] = []
        for _ in range(turns):
            events.extend(self.advance_rounds(ROUNDS_PER_TURN - self.clock.rounds % ROUNDS_PER_TURN))
            in_field = field if field is not None else self.mode is SessionMode.EXPLORING
            if in_field and not resting:
                # The rest cadence is a dungeon rule ("must rest for one turn every
                # hour in the dungeon") — town time and overland travel don't accrue.
                self.turns_since_rest += 1
                events.extend(exploration.check_fatigue(self))
            if in_field:
                self.wandering_counter += 1
                if self.wandering_counter >= exploration.wandering_interval(self):
                    self.wandering_counter = 0
                    check_events, encountered = exploration.wandering_check(self, resting=resting)
                    events.extend(check_events)
                    if encountered:
                        return events, True
        return events, False

    # ------------------------------------------------------------------ light queries

    def party_light(self) -> tuple[bool, bool]:
        """Return `(lit, infravision_allowed)` for the party as a whole.

        Lit means any living member carries an active light-family effect — unless
        a darkness-family effect on any member suppresses the party's light while
        it runs (the printed radii swallow a marching party, pinned). Darkness
        with `blocks_infravision` disables infravision too.

        Returns:
            The pair of party-level light facts.
        """
        living_ids = [member.id for member in self.party.living_members()]
        darkness = [
            effect
            for member_id in living_ids
            for effect in self.ledger.effects
            if effect.target_ref == member_id and effect.definition.kind in DARKNESS_EFFECT_KINDS
        ]
        if darkness:
            blocks = any(bool(effect.definition.params.get("blocks_infravision")) for effect in darkness)
            return False, not blocks
        lit = any(
            effect.target_ref in living_ids and effect.definition.kind in LIGHT_EFFECT_KINDS
            for effect in self.ledger.effects
        )
        return lit, True

    def bright_light(self) -> bool:
        """Whether the party carries daylight-bright light (*continual light*'s data).

        RAW modifies the wandering chance for "bright light sources"; the
        torch/lantern flame is the baseline the printed 1-in-6 already assumes
        (pinned), so only `brightness == "daylight"` counts.
        """
        living_ids = {member.id for member in self.party.living_members()}
        return any(
            effect.target_ref in living_ids
            and effect.definition.kind in LIGHT_EFFECT_KINDS
            and effect.definition.params.get("brightness") == "daylight"
            for effect in self.ledger.effects
        )

    def member_has_infravision(self, member: Character) -> bool:
        """Whether one member sees in the dark: the class tag or a spell effect."""
        if any(ability.tag == "infravision" for ability in member.definition.abilities):
            return True
        return any(
            effect.target_ref == member.id and effect.definition.kind == "infravision" for effect in self.ledger.effects
        )

    # ------------------------------------------------------------------ the XP award

    def party_valuation_cp(self) -> int:
        """The party's treasure valuation in copper pieces — the award's exact unit.

        All members count, including the dead (their carried treasure that made it
        back is the party's recovery): coin value in cp plus every valuable's
        `value_gp`. Magic items and mundane equipment count zero — magical
        treasure grants no XP per RAW, and mundane-gear salvage is below the
        simulation floor (registered).
        """
        total = 0
        for member in self.party.members:
            total += member.inventory.purse.value_cp
            total += sum(valuable.value_gp * 100 for valuable in member.inventory.valuables)
        return total

    def snapshot_treasure(self) -> None:
        """Record the departure valuation — `EnterDungeon`'s bookkeeping."""
        self.treasure_snapshot_cp = self.party_valuation_cp()

    def award_adventure_xp(self) -> list[Event]:
        """The end-of-adventure award: defeated monsters plus the valuation delta.

        The treasure XP is the delta between the party's valuation now and the
        departure snapshot — floored to gp once from the cp total, never negative
        (clamped at zero: a party that lost money learned nothing monetarily,
        pinned). The total divides evenly among living members (floor division,
        remainder dropped — RAW divides evenly and B/X arithmetic is integer) and
        applies through `apply_xp` directly (a command whose handler executed
        further commands would double-log; `AwardXP` remains the referee and game
        surface). Dead members' recovered treasure counts toward the pool; dead
        members receive no share. A TPK never awards — no one returned. The
        defeated-monsters ledger clears and the next departure snapshots anew.
        """
        from osrlib.crawl.events import AdventureXpAwardEvent

        survivors = self.party.living_members()
        events: list[Event] = []
        monster_xp = sum(record.xp for record in self.defeated_monsters)
        current = self.party_valuation_cp()
        baseline = self.treasure_snapshot_cp if self.treasure_snapshot_cp is not None else current
        treasure_xp = max(0, (current - baseline) // 100)
        self.defeated_monsters = []
        self.treasure_snapshot_cp = None
        if not survivors:
            return events
        total = monster_xp + treasure_xp
        if total <= 0:
            return events
        share = total // len(survivors)
        events.append(
            AdventureXpAwardEvent(
                monster_xp=monster_xp,
                treasure_xp=treasure_xp,
                share=share,
                survivors=tuple(member.id for member in survivors),
            )
        )
        if share > 0:
            for member in survivors:
                result = apply_xp(member, member.definition, share, self.streams.get(ADVANCEMENT_STREAM))
                events.append(
                    XpAwardedEvent(
                        character_id=member.id,
                        award=result.award,
                        modified_award=result.modified_award,
                        level_after=result.level_after,
                    )
                )
        return events

    def award_immediate_xp(self, amount: int) -> list[Event]:
        """The `immediate` timing's division: apply one award pool now.

        Same division and events as the return award: evenly among living
        members, floor division, remainder dropped.
        """
        survivors = self.party.living_members()
        if not survivors or amount <= 0:
            return []
        share = amount // len(survivors)
        if share <= 0:
            return []
        events: list[Event] = []
        for member in survivors:
            result = apply_xp(member, member.definition, share, self.streams.get(ADVANCEMENT_STREAM))
            events.append(
                XpAwardedEvent(
                    character_id=member.id,
                    award=result.award,
                    modified_award=result.modified_award,
                    level_after=result.level_after,
                )
            )
        return events

    # ------------------------------------------------------------------ death records

    def _record_deaths(self, events: Sequence[Event]) -> None:
        """Record party deaths with the clock round and the cause just resolved.

        Pinned: `poison` when the killing resolution was a poison save (a failed
        death-category save immediately preceding the death) or a poison-delay
        expiry; else the nearest preceding cause-bearing event's kind. Only the
        poison/non-poison distinction is consumed (by *neutralize poison*).
        """
        member_ids = {member.id for member in self.party.members}
        cause = "unknown"
        for event in events:
            if isinstance(event, SavingThrowRolledEvent) and event.category == "death":
                if event.code == "combat.save.failed":
                    cause = "poison"
            elif isinstance(event, EffectExpiredEvent) and "poison" in event.kind:
                cause = "poison"
            elif isinstance(event, DamageDealtEvent):
                cause = "damage"
            if isinstance(event, DeathEvent) and event.target_id in member_ids:
                self.death_records[event.target_id] = DeathRecord(round=self.clock.rounds, cause=cause)

    # ------------------------------------------------------------------ views

    def view(self, visibility: Visibility) -> PlayerView | RefereeView:
        """Return the projection for a visibility level.

        Args:
            visibility: `PLAYER` for the safe whitelist, `REFEREE` for everything
                but RNG internals.

        Returns:
            The frozen view.
        """
        from osrlib.crawl.views import build_player_view, build_referee_view

        if visibility is Visibility.PLAYER:
            return build_player_view(self)
        return build_referee_view(self)


# ---------------------------------------------------------------------- referee handlers


def _handle_grant_item(session: GameSession, command: GrantItem) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    try:
        template = load_equipment().get(command.item_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_item", params={"item": command.item_id})], []
    member.inventory.items.append(ItemInstance(template=template, quantity=command.quantity))
    return [], [ItemAcquiredEvent(character_id=member.id, item_ids=(command.item_id,) * command.quantity)]


def _handle_grant_coins(session: GameSession, command: GrantCoins) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) + getattr(command.coins, denomination))
    return [], [ItemAcquiredEvent(character_id=member.id, coins_gp_value=command.coins.value_gp)]


def _handle_award_xp(session: GameSession, command: AwardXP) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    result = apply_xp(member, member.definition, command.amount, session.streams.get(ADVANCEMENT_STREAM))
    return [], [
        XpAwardedEvent(
            character_id=member.id,
            award=result.award,
            modified_award=result.modified_award,
            level_after=result.level_after,
        )
    ]


def _handle_set_flag(session: GameSession, command: SetFlag) -> tuple[list[Rejection], list[Event]]:
    session.flags[command.key] = command.value
    return [], [FlagSetEvent(key=command.key, value=command.value)]


def _handle_spawn_monsters(session: GameSession, command: SpawnMonsters) -> tuple[list[Rejection], list[Event]]:
    from osrlib.core.dice import roll
    from osrlib.crawl import encounter as encounter_module

    try:
        load_monsters().get(command.template_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_monster", params={"template": command.template_id})], []
    if session.encounter is not None or session.battle is not None:
        return [Rejection(code="session.command.encounter_in_progress")], []
    if session.dungeon_state.location.kind != "dungeon":
        # Encounters live on the dungeon grid: the combat space, silence cells,
        # and formation widths all need a party cell to stand on.
        return [Rejection(code="session.command.not_in_dungeon")], []
    if command.count_fixed is not None:
        count = command.count_fixed
    else:
        count = roll(command.count_dice, session.streams.get(ENCOUNTER_STREAM)).total
    count = max(1, count)
    instances = session.spawn(command.template_id, count)
    events: list[Event] = [
        MonstersSpawnedEvent(template_id=command.template_id, monster_ids=tuple(instance.id for instance in instances))
    ]
    events.extend(
        encounter_module.start_encounter(
            session,
            groups=[(command.template_id, instances)],
            kind="spawned",
            distance_feet=command.distance_feet,
        )
    )
    return [], events


def _handle_spawn_npc_party(session: GameSession, command: SpawnNpcParty) -> tuple[list[Rejection], list[Event]]:
    from osrlib.core.dice import roll
    from osrlib.crawl import encounter as encounter_module
    from osrlib.crawl import exploration
    from osrlib.data import load_encounter_tables

    if session.encounter is not None or session.battle is not None:
        return [Rejection(code="session.command.encounter_in_progress")], []
    if session.dungeon_state.location.kind != "dungeon":
        return [Rejection(code="session.command.not_in_dungeon")], []
    if command.count_dice is not None:
        count_dice = command.count_dice
    else:
        count_dice = next(
            composition.count_dice
            for composition in load_encounter_tables().npc_compositions
            if composition.kind == command.party_kind
        )
    count = max(1, roll(count_dice, session.streams.get(ENCOUNTER_STREAM)).total)
    party, bundle, events = exploration.field_npc_party(session, command.party_kind, count)
    label = "Basic Adventurers" if command.party_kind == "basic" else "Expert Adventurers"
    events.extend(
        encounter_module.start_encounter(
            session,
            groups=[(label, party.members)],
            kind="spawned",
            distance_feet=command.distance_feet,
        )
    )
    exploration._assign_carried(session, [({}, bundle)])
    return [], events


def _handle_identify_item(session: GameSession, command: IdentifyItem) -> tuple[list[Rejection], list[Event]]:
    from osrlib.crawl import exploration

    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    instance = member.inventory.magic_item(command.item_id)
    if instance is None:
        return [Rejection(code="session.command.unknown_item", params={"item": command.item_id})], []
    return [], exploration.identify_item_events(session, member, instance)


def _handle_set_door_state(session: GameSession, command: SetDoorState) -> tuple[list[Rejection], list[Event]]:
    try:
        dungeon = session.adventure.dungeon(command.dungeon_id)
        level = dungeon.level(command.level_number)
    except ValueError:
        return [Rejection(code="session.command.unknown_location", params={"dungeon": command.dungeon_id})], []
    from osrlib.crawl.dungeon import EdgeKind

    edge = level.edge((command.x, command.y), command.direction)
    if edge.kind is not EdgeKind.DOOR:
        return [Rejection(code="session.command.no_door", params={"x": command.x, "y": command.y})], []
    ref = edge_ref(command.dungeon_id, command.level_number, (command.x, command.y), command.direction)
    state = session.dungeon_state.door(ref)
    events: list[Event] = []
    if command.open is not None and command.open != state.open:
        state.open = command.open
        code = "exploration.door.opened" if command.open else "exploration.door.closed"
        events.append(
            DoorEvent(
                code=code, x=command.x, y=command.y, direction=command.direction.value, visibility=Visibility.REFEREE
            )
        )
    if command.wedged is not None:
        state.wedged = command.wedged
    if command.discovered is not None:
        state.discovered = command.discovered
    if command.unlocked is not None:
        state.unlocked = command.unlocked
    return [], events


def _handle_place_party(session: GameSession, command: PlaceParty) -> tuple[list[Rejection], list[Event]]:
    if session.encounter is not None or session.battle is not None:
        return [Rejection(code="session.command.encounter_in_progress")], []
    location = command.location
    if location.kind == "dungeon":
        try:
            level = session.adventure.dungeon(location.dungeon_id).level(location.level_number)
        except ValueError:
            return [
                Rejection(code="session.command.unknown_location", params={"dungeon": str(location.dungeon_id)})
            ], []
        if not level.in_bounds(location.position):
            return [Rejection(code="session.command.out_of_bounds")], []
    session.dungeon_state.location = location
    events: list[Event] = []
    if location.kind == "dungeon":
        session.dungeon_state.mark_explored(location.dungeon_id, location.level_number, location.position)
        session.mode = SessionMode.EXPLORING
        events.append(
            LocationEnteredEvent(
                location_kind="dungeon", location_id=location.dungeon_id, level_number=location.level_number
            )
        )
    else:
        session.mode = SessionMode.TOWN
        events.append(LocationEnteredEvent(location_kind="town", location_id="town"))
    return [], events


def _handle_advance_time(session: GameSession, command: AdvanceTime) -> tuple[list[Rejection], list[Event]]:
    if command.unit is TimeUnit.ROUND:
        events = session.advance_rounds(command.n)
    else:
        turns = command.n * (1 if command.unit is TimeUnit.TURN else 144)
        # Referee time passes with full bookkeeping but no wandering cadence —
        # the referee controls encounters (pinned).
        events, _ = session.advance_turns(turns, field=False)
    events.append(TimeAdvancedEvent(n=command.n, unit=command.unit.value, rounds_total=session.clock.rounds))
    return [], events


_REFEREE_HANDLERS = {
    GrantItem: _handle_grant_item,
    GrantCoins: _handle_grant_coins,
    AwardXP: _handle_award_xp,
    SetFlag: _handle_set_flag,
    SpawnMonsters: _handle_spawn_monsters,
    SpawnNpcParty: _handle_spawn_npc_party,
    SetDoorState: _handle_set_door_state,
    IdentifyItem: _handle_identify_item,
    PlaceParty: _handle_place_party,
    AdvanceTime: _handle_advance_time,
}

_HANDLERS_CACHE: dict | None = None


def _handlers() -> Mapping[type[Command], object]:
    """The command-type → handler map, assembled lazily to avoid import cycles."""
    global _HANDLERS_CACHE
    if _HANDLERS_CACHE is None:
        from osrlib.crawl import battle, encounter, exploration

        _HANDLERS_CACHE = {
            **_REFEREE_HANDLERS,
            **exploration.HANDLERS,
            **encounter.HANDLERS,
            **battle.HANDLERS,
        }
    return _HANDLERS_CACHE
