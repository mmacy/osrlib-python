"""`GameSession`: the command loop, the event log, listeners, flags, and views.

The session is the front end's one object. Build it with
[`GameSession.new`][osrlib.crawl.session.GameSession.new] (or restore one with
`load_game`), feed player intent to
[`GameSession.execute`][osrlib.crawl.session.GameSession.execute] as typed
commands from [`osrlib.crawl.commands`][osrlib.crawl.commands], and render the
[`Event`][osrlib.core.events.Event]s that come back. The session owns what the
kernel leaves to its caller: the [`RngStreams`][osrlib.core.rng.RngStreams]
(master seed), the [`IdAllocator`][osrlib.core.monsters.IdAllocator], the
[`EffectsLedger`][osrlib.core.effects.EffectsLedger], the
[`GameClock`][osrlib.core.clock.GameClock], the entity registry (characters and
live monster instances), the flag store, the trigger fired-marks, the journal, the
listener-state store, the command and event logs, the mode, and the crawl state.

`execute(command)` runs a pure validation pre-phase: a rejected command consumes
no RNG draws, no clock time, mutates nothing, and is excluded from the command
log. Accepted commands mutate, append their events to the log, then registered
listeners run in registration order, their events appended to the same result and
log. Each command class documents its legal modes, rejection codes, and events.

For presentation, [`GameSession.view`][osrlib.crawl.session.GameSession.view]
projects the state at player or referee visibility — render from views and
events, never from raw session internals.
"""
# Command handlers live in osrlib.crawl.exploration, osrlib.crawl.encounter, and
# osrlib.crawl.battle; each is one function (session, command) -> (rejections,
# events) whose discipline is validation first — no draw, no mutation, no time
# before the last rejection check. The session-owned referee and town commands
# are handled at the bottom of this module.

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.alignment import Alignment
from osrlib.core.character import ADVANCEMENT_STREAM, Character
from osrlib.core.classes import XpAwardResult, apply_xp, level_title
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
from osrlib.core.items import EquipmentCatalog, ItemInstance
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, MonsterCatalog, MonsterInstance, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.validation import Rejection
from osrlib.crawl.adventure import Adventure, _effective_equipment, _effective_monsters, validate_adventure
from osrlib.crawl.commands import (
    AddJournalEntry,
    AdvanceTime,
    AwardXP,
    Command,
    CommandResult,
    GrantCoins,
    GrantItem,
    IdentifyItem,
    MarkTriggerFired,
    PlaceParty,
    RecordNote,
    RollDice,
    SessionMode,
    SetDoorState,
    SetFlag,
    SpawnMonsters,
    SpawnNpcParty,
)
from osrlib.crawl.dungeon import DungeonState, edge_ref
from osrlib.crawl.events import (
    CharacterLeveledUpEvent,
    DiceRolledEvent,
    DoorEvent,
    FlagSetEvent,
    GameOverEvent,
    ItemAcquiredEvent,
    JournalEntryAddedEvent,
    LightEvent,
    LocationEnteredEvent,
    MonstersSpawnedEvent,
    NoteRecordedEvent,
    TimeAdvancedEvent,
    TriggerFiredEvent,
    XpAwardedEvent,
)
from osrlib.crawl.party import Party
from osrlib.data import load_equipment, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.versioning import SCHEMA_VERSION, engine_version

if TYPE_CHECKING:
    from osrlib.crawl.battle import BattleState
    from osrlib.crawl.encounter import EncounterState
    from osrlib.crawl.views import PlayerView, RefereeView

__all__ = [
    "ADJUDICATION_STREAM",
    "DARKNESS_EFFECT_KINDS",
    "DeathRecord",
    "DefeatedMonsterRecord",
    "DeprivationState",
    "ENCOUNTER_STREAM",
    "EXPLORATION_STREAM",
    "GameSession",
    "JournalEntry",
    "LIGHT_EFFECT_KINDS",
    "Listener",
    "MONSTER_ACTION_STREAM",
    "WANDERING_STREAM",
]

WANDERING_STREAM = "wandering"
"""Stream key for wandering-monster draws: the check die, the d20, counts, variant picks."""

ENCOUNTER_STREAM = "encounter"
"""Stream key for encounter-procedure draws: surprise, distance, reaction, distraction."""

EXPLORATION_STREAM = "exploration"
"""Stream key for exploration draws: forcing, listening, searching, traps, tinder, skills."""

MONSTER_ACTION_STREAM = "monster_action"
"""Stream key for the action policy's draws — a policy change never shifts combat draws."""

ADJUDICATION_STREAM = "adjudication"
"""Stream key for the referee's ad-hoc adjudication rolls — kept off the mechanical
streams so a freeform roll never shifts a keyed mechanic's draw sequence."""

LIGHT_EFFECT_KINDS = frozenset({"light", "continual_light"})
"""The light-family effect kinds: torch/lantern attachments and the light spells."""

DARKNESS_EFFECT_KINDS = frozenset({"darkness", "continual_darkness"})
"""The darkness-family effect kinds — the printed radii swallow a marching party."""


class DeathRecord(BaseModel):
    """When and how a character died — the honest inputs for revival windows.

    `cause` is `"poison"` when the killing resolution was a poison save or a
    poison-delay expiry (feeding *neutralize poison*'s round window), else the
    source kind; *raise dead*'s day count reads `round` regardless of cause.
    """

    model_config = ConfigDict(frozen=True)

    round: int
    cause: str


class JournalEntry(BaseModel):
    """One journal beat: the authored text and the clock position it landed at.

    The journal is append-only — entries are never rewritten and never derived from
    other state — and each entry carries its own `rounds` stamp because the moment
    of appending is the only moment it can be captured: a front end renders "when"
    from the view alone, and a save compacted of its event log still knows when
    every beat landed.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    rounds: int = Field(ge=0)


class DefeatedMonsterRecord(BaseModel):
    """One defeated monster — the XP award's input."""

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
        """The worse track — the two deprivation tracks don't stack."""
        return max(self.food_days, self.water_days)


def _member_id(member: Character) -> str:
    """A member's session id — assigned at session entry; absence is programmer misuse."""
    if member.id is None:
        raise ValueError(f"{member.name} has no session-assigned id")
    return member.id


def _xp_award_events(member: Character, result: XpAwardResult) -> list[Event]:
    """`XpAwardedEvent` plus, when the award crossed a threshold, the level event.

    Every `apply_xp` call site reports through here, so the ordering — the level
    event immediately after the same member's award event — is structural rather
    than repeated at each surface.
    """
    events: list[Event] = [
        XpAwardedEvent(
            character_id=_member_id(member),
            award=result.award,
            modified_award=result.modified_award,
            level_after=result.level_after,
        )
    ]
    if result.level_up is not None:
        events.append(
            CharacterLeveledUpEvent(
                character_id=_member_id(member),
                level_before=result.level_before,
                level_after=result.level_after,
                hp_gained=result.level_up.hp_gained,
                hp_roll=result.level_up.hp_roll,
                con_applied=result.level_up.con_applied,
                title=level_title(member.definition, result.level_after),
            )
        )
    return events


class Listener(Protocol):
    """The extension-point protocol: games register listeners on the session.

    Listeners never mutate game state — they react by executing ordinary
    commands. `handle` receives the accumulated events of the command (earlier
    listeners' included) and the listener's own state snapshot, and returns the
    events to append plus the new state (snapshotted into saves under `key`).

    A listener that reacts by executing commands returns no events: those commands
    logged their own, and the result envelope picks them up from the log. Returning
    them again would log them twice. The returned list is for events a listener
    *authors* directly.
    """

    key: str

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        """React to one command's events."""
        ...


class GameSession:
    """A running game: the single entry point for command execution and views.

    The loop: [`execute`][osrlib.crawl.session.GameSession.execute] one command at
    a time and render its [`CommandResult`][osrlib.crawl.commands.CommandResult];
    read state through [`view`][osrlib.crawl.session.GameSession.view] (player or
    referee visibility) rather than session attributes; extend the game with
    [`register_listener`][osrlib.crawl.session.GameSession.register_listener] and
    session flags. Everything that happened is on `event_log`, every accepted
    command on `command_log`, and `save_game`/`load_game` round-trip the whole
    session deterministically: same seed, same commands, same game.

    The trigger fired-marks (`fired_triggers`, in first-fired order) and the
    `journal` are engine-owned session state beside the flag store: the lifecycle
    commands [`MarkTriggerFired`][osrlib.crawl.commands.MarkTriggerFired] and
    [`AddJournalEntry`][osrlib.crawl.commands.AddJournalEntry] are their only
    writers, so a replay — which runs with no listeners registered — rebuilds both
    by re-executing the command log.
    """

    def __init__(
        self,
        *,
        party: Party,
        adventure: Adventure,
        ruleset: Ruleset,
        streams: RngStreams,
        master_seed: int,
    ) -> None:
        """Internal constructor — use [`GameSession.new`][osrlib.crawl.session.GameSession.new] or `load_game`.

        Raises:
            ContentValidationError: If the adventure bundles monster or item ids
                that collide with the shipped catalogs or each other — the typed
                backstop for `load_game`, which trusts saved content otherwise.
        """
        self.party = party
        self.adventure = adventure
        catalog, colliding = _effective_monsters(adventure, load_monsters())
        if colliding:
            raise ContentValidationError(
                "adventure bundles colliding monster ids: " + ", ".join(repr(monster_id) for monster_id in colliding)
            )
        self._monster_catalog = catalog
        equipment, colliding_items = _effective_equipment(adventure, load_equipment())
        if colliding_items:
            raise ContentValidationError(
                "adventure bundles colliding item ids: " + ", ".join(repr(item_id) for item_id in colliding_items)
            )
        self._equipment_catalog = equipment
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
        self.fired_triggers: list[str] = []
        self.journal: list[JournalEntry] = []
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
        self.encounter: EncounterState | None = None
        self.battle: BattleState | None = None
        self._provisions_day = 0
        # Runtime extension points, re-registered by the game like listeners —
        # never serialized (policies are code).
        self.action_policies: dict[str, object] = {}

    @classmethod
    def new(cls, party: Party, adventure: Adventure, *, seed: int, ruleset: Ruleset | None = None) -> GameSession:
        """Create a new session, validating the adventure and assigning member ids.

        Character ids assign as `character-NNNN` from the session's allocator in
        party order. Members that already carry ids keep them (a party loaded from
        an earlier session).

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

    @property
    def effective_monsters(self) -> MonsterCatalog:
        """The session's monster catalog: the shipped catalog plus the adventure's bundled templates.

        Every engine site that resolves a template id — spawning, keyed
        encounters, wandering rows, listen checks — resolves against this
        catalog. For an adventure that bundles nothing it *is* the shipped
        catalog ([`load_monsters`][osrlib.data.load_monsters]'s cached object).
        """
        return self._monster_catalog

    @property
    def effective_equipment(self) -> EquipmentCatalog:
        """The session's equipment catalog: the shipped catalog plus the adventure's bundled templates.

        Every engine site that resolves an authored item id — treasure caches,
        `GrantItem`, drop-pile recovery — resolves against this catalog. For an
        adventure that bundles nothing it *is* the shipped catalog
        ([`load_equipment`][osrlib.data.load_equipment]'s cached object). The town
        shop is the exception: it stocks the shipped equipment lists, so a bundled
        item is never on sale.
        """
        return self._equipment_catalog

    # ------------------------------------------------------------------ dispatch

    def execute(self, command: Command) -> CommandResult:
        """Execute one command: the pure validation pre-phase, then apply and log.

        An accepted command's own bookkeeping runs before its events reach the log
        and the listeners: party deaths are recorded with their cause, and a
        command whose events killed the last living member ends the session in
        `game_over` with a
        [`GameOverEvent`][osrlib.crawl.events.GameOverEvent] closing its result —
        whatever killed the party, and from whichever mode. A session already in a
        terminal mode is left alone.

        The result carries everything the command caused, in event-log order: the
        handler's own events, then, per listener in registration order, the events
        of the commands that listener executed — however deeply nested — followed by
        the events it authored itself.

        Args:
            command: The command to execute.

        Returns:
            The result envelope; rejected commands carry rejections and no events.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            rng = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=rng,
            )
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            town = TownSpec(name="Threshold")
            adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)

            result = session.execute(EnterDungeon(dungeon_id="crypt"))
            assert result.accepted and result.events

            again = session.execute(EnterDungeon(dungeon_id="crypt"))  # already inside
            assert not again.accepted
            assert again.rejections[0].code == "session.command.wrong_mode"
            ```
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
        if self._record_deaths(events):
            events.extend(self._end_on_party_wipe())
        self.event_log.extend(events)
        accumulated = list(events)
        self._persist_sight()
        for listener in self.listeners:
            mark = len(self.event_log)
            emitted, state = listener.handle(tuple(accumulated), self.listener_state.get(listener.key, {}))
            self.listener_state[listener.key] = state
            # Everything the listener's own commands logged while it ran — their
            # events and any deeper listener reactions, each already in the log
            # exactly once, in log order — then the events it authored itself.
            # (The log holds serialized entries only for a session restored from a
            # save; nothing executing appends one.)
            accumulated.extend(entry for entry in self.event_log[mark:] if isinstance(entry, Event))
            accumulated.extend(emitted)
            self.event_log.extend(emitted)
        return CommandResult(accepted=True, events=tuple(accumulated))

    def _persist_sight(self) -> None:
        """Fold the party's current light reveal into the seen map memory.

        Runs after every accepted command — the one hook that covers every
        reveal-changing action uniformly (entering, moving, stairs, doors,
        lighting, placement, discovery, battle-flight relocation) — and calls
        [`mark_seen`][osrlib.crawl.dungeon.DungeonState.mark_seen] with what
        `_light_reveal` shows from the party's cell. Rejected commands change no
        state, so they never reach it.

        It runs *before* the listeners, so that the map a live session remembers is
        the map a replay rebuilds. A listener that relocates the party — an authored
        teleport — executes its own command, which folds its own destination in
        turn; folding this command's reveal afterwards instead would fold the
        destination's view over the move the party actually made, while a replay,
        running the same commands with no listeners, folds both in order.
        """
        from osrlib.crawl.exploration import _light_reveal

        key, cells = _light_reveal(self)
        if key is None or not cells:
            return
        dungeon_id, level_text = key.rsplit(":", 1)
        self.dungeon_state.mark_seen(dungeon_id, int(level_text), cells)

    def register_listener(self, listener: Listener) -> None:
        """Register a listener; it runs after each command in registration order.

        Args:
            listener: The listener; its state snapshots into saves under its key.
        """
        self.listeners.append(listener)
        self.listener_state.setdefault(listener.key, {})

    # ------------------------------------------------------------------ registry

    def registry(self) -> dict[str, Any]:
        """Live entities by id: party members (marching order), then monsters, then NPCs."""
        entities: dict[str, Any] = {_member_id(member): member for member in self.party.members}
        entities.update(self.monsters)
        entities.update(self.npcs)
        return entities

    def combatant(self, combatant_id: str) -> object | None:
        """Return the monster or NPC with `combatant_id`, or `None`.

        The encounter side's lookup: an
        [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup]'s combatant ids
        span monsters and NPC adventurers, and this resolves both.

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
            template_id: Any id in the session's
                [`effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters]
                catalog — shipped (see [the monster id index][monsters-index]) or
                bundled by the adventure.
            count: How many to spawn.
            alignment: An alignment override from keyed content.

        Returns:
            The spawned instances, in spawn order.
        """
        template = self.effective_monsters.get(template_id)
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
        player-facing `exploration.light.expired` with the source kind.
        Day-boundary crossings consume provisions.

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
        actions absorb partial round-time. Each turn: the ledger advances, day
        boundaries consume provisions, the rest cadence counts (unless `resting`),
        and — in the field — the wandering cadence may fire a check that starts an
        encounter, which stops the advance.

        A field span also stops the moment it leaves nobody standing: the
        cadences belong to the living, so a rest that starves the party out ends
        at the turn it happened rather than running its remaining hours. The key
        is `field`, not the mode — everything a non-field span does is ledger
        bookkeeping, and a revival window measured in elapsed time has to keep
        elapsing while the party lies dead.

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
            if in_field and not self.party.living_members():
                # The cadences belong to the living: a span that kills the last
                # member — a rest that starves the party out — stops at the turn
                # it happened. Out of the field there is nothing to stop, and a
                # revival window measured in elapsed time has to keep elapsing.
                break
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
        it runs (the printed radii swallow a marching party). Darkness with
        `blocks_infravision` disables infravision too.

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

        RAW modifies the wandering chance for "bright light sources"; osrlib adopts
        the reading that the torch/lantern flame is the baseline the printed 1-in-6
        already assumes, so only `brightness == "daylight"` counts.
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
        treasure grants no XP per RAW, and mundane-gear salvage sits below the
        simulation floor by design.
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
        (clamped at zero: a party that lost money learned nothing monetarily).
        The total divides evenly among living members (floor division,
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
                survivors=tuple(_member_id(member) for member in survivors),
            )
        )
        if share > 0:
            for member in survivors:
                result = apply_xp(member, member.definition, share, self.streams.get(ADVANCEMENT_STREAM))
                events.extend(_xp_award_events(member, result))
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
            events.extend(_xp_award_events(member, result))
        return events

    # ------------------------------------------------------------------ death records

    def _record_deaths(self, events: Sequence[Event]) -> bool:
        """Record party deaths with the clock round and the cause just resolved.

        The cause is `poison` when the killing resolution was a poison save (a
        failed death-category save immediately preceding the death) or a
        poison-delay expiry; else the nearest preceding cause-bearing event's
        kind. Only the poison/non-poison distinction is consumed (by *neutralize
        poison*).

        Args:
            events: The just-executed command's events, in order.

        Returns:
            True when a party member died in them — the edge
            [`_end_on_party_wipe`][osrlib.crawl.session.GameSession._end_on_party_wipe]
            triggers on, identified by this same walk. Monsters and NPC
            adventurers carry non-member ids and never count.
        """
        member_ids = {member.id for member in self.party.members}
        cause = "unknown"
        died = False
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
                died = True
        return died

    def _end_on_party_wipe(self) -> list[Event]:
        """End the session when the death just recorded left nobody standing.

        The one entrance to `game_over`, whatever killed the party: a lost battle,
        a trap, a fall, starvation, a poison that finished the last member under a
        referee's clock. Any open encounter or battle clears — a concluded session
        holds no live play state — and the ending is reported as one
        [`GameOverEvent`][osrlib.crawl.events.GameOverEvent].

        The trigger is the death, not the state: this runs only for a command whose
        own events killed a member, so ferrying an already-fallen party to town
        (the revival flow's first step) never re-enters game-over. A terminal mode
        is left alone, so a party that dies after the adventure concluded stays in
        `victory` and a second death among the fallen ends nothing twice.

        Returns:
            The ending event, or nothing when a member still lives.
        """
        if self.mode.terminal or self.party.living_members():
            return []
        self.battle = None
        self.encounter = None
        self.mode = SessionMode.GAME_OVER
        return [GameOverEvent(reason="the party has fallen")]

    # ------------------------------------------------------------------ views

    def view(self, visibility: Visibility) -> PlayerView | RefereeView:
        """Return the projection for a visibility level.

        The player view is an enumerated whitelist safe to show at the table; the
        referee view is the full state minus RNG internals. Neither carries the
        master seed — it lives only in the save.

        Args:
            visibility: `PLAYER` for the safe whitelist, `REFEREE` for everything
                but RNG internals.

        Returns:
            The frozen view.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.events import Visibility
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.commands import EnterDungeon
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            rules = Ruleset()
            rng = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
            hero = create_character(
                name="Hild",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=rules,
                stream=rng,
            )
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            town = TownSpec(name="Threshold")
            adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
            session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)
            session.execute(EnterDungeon(dungeon_id="crypt"))

            player = session.view(Visibility.PLAYER)
            referee = session.view(Visibility.REFEREE)
            assert player.mode == "exploring"
            # The referee sees session flags; the player whitelist has no such field.
            assert "flags" in referee.state
            assert "flags" not in player.model_dump()
            # Neither view leaks the master seed — it lives only in the save.
            assert "master_seed" not in referee.state
            ```
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
        template = session.effective_equipment.get(command.item_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_item", params={"item": command.item_id})], []
    member.inventory.items.append(ItemInstance(template=template, quantity=command.quantity))
    events: list[Event] = [
        ItemAcquiredEvent(character_id=_member_id(member), item_ids=(command.item_id,) * command.quantity)
    ]
    return [], events


def _handle_grant_coins(session: GameSession, command: GrantCoins) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    purse = member.inventory.purse
    for denomination in ("pp", "gp", "ep", "sp", "cp"):
        setattr(purse, denomination, getattr(purse, denomination) + getattr(command.coins, denomination))
    events: list[Event] = [ItemAcquiredEvent(character_id=_member_id(member), coins_gp_value=command.coins.value_gp)]
    return [], events


def _handle_award_xp(session: GameSession, command: AwardXP) -> tuple[list[Rejection], list[Event]]:
    try:
        member = session.member(command.character_id)
    except ValueError:
        return [Rejection(code="session.command.unknown_member", params={"character": command.character_id})], []
    result = apply_xp(member, member.definition, command.amount, session.streams.get(ADVANCEMENT_STREAM))
    return [], _xp_award_events(member, result)


def _handle_set_flag(session: GameSession, command: SetFlag) -> tuple[list[Rejection], list[Event]]:
    session.flags[command.key] = command.value
    return [], [FlagSetEvent(key=command.key, value=command.value)]


def _handle_mark_trigger_fired(session: GameSession, command: MarkTriggerFired) -> tuple[list[Rejection], list[Event]]:
    # Fired-marks answer "has this trigger fired", so a repeat mark adds nothing;
    # the event fires every time, because a repeatable trigger's every firing is
    # marked and the log is the record of each one.
    if command.trigger_id not in session.fired_triggers:
        session.fired_triggers.append(command.trigger_id)
    return [], [TriggerFiredEvent(trigger_id=command.trigger_id, narrative=command.narrative)]


def _handle_add_journal_entry(session: GameSession, command: AddJournalEntry) -> tuple[list[Rejection], list[Event]]:
    entry = JournalEntry(text=command.text, rounds=session.clock.rounds)
    session.journal.append(entry)
    return [], [JournalEntryAddedEvent(text=entry.text, rounds=entry.rounds)]


def _handle_record_note(session: GameSession, command: RecordNote) -> tuple[list[Rejection], list[Event]]:
    return [], [NoteRecordedEvent(text=command.text)]


def _handle_spawn_monsters(session: GameSession, command: SpawnMonsters) -> tuple[list[Rejection], list[Event]]:
    from osrlib.core.dice import roll
    from osrlib.crawl import encounter as encounter_module

    try:
        session.effective_monsters.get(command.template_id)
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
    elif command.count_dice is not None:
        count = roll(command.count_dice, session.streams.get(ENCOUNTER_STREAM)).total
    else:  # unreachable: the command model requires exactly one of the two
        raise ValueError("SpawnMonsters carries neither count_dice nor count_fixed")
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
    party, bundle, npc_events = exploration._field_npc_party(session, command.party_kind, count)
    events: list[Event] = list(npc_events)
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
    return [], exploration._identify_item_events(session, member, instance)


def _handle_set_door_state(session: GameSession, command: SetDoorState) -> tuple[list[Rejection], list[Event]]:
    try:
        dungeon = session.adventure.dungeon(command.dungeon_id)
        level = dungeon.level(command.level_number)
    except ValueError:
        return [Rejection(code="session.command.unknown_location", params={"dungeon": command.dungeon_id})], []
    from osrlib.crawl import exploration
    from osrlib.crawl.dungeon import EdgeKind

    edge = level.edge((command.x, command.y), command.direction)
    if edge.kind is not EdgeKind.DOOR:
        return [Rejection(code="session.command.no_door", params={"x": command.x, "y": command.y})], []
    if command.open is command.wedged is command.discovered is command.unlocked is None:
        # A write with nothing to write is legal and does nothing at all — it must
        # not leave an overlay entry behind for a door nobody has touched.
        return [], []
    ref = edge_ref(command.dungeon_id, command.level_number, (command.x, command.y), command.direction)
    # The referee writes through the same seeded materializer the play handlers
    # use, so a first write to an authored-open door does not store it shut.
    state = exploration._store_door_state(session, edge, ref)
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
    events: list[Event] = []
    if location.kind == "dungeon":
        dungeon_id, level_number, position = location.dungeon_id, location.level_number, location.position
        if dungeon_id is None or level_number is None or position is None:
            # Unreachable: the location model validates that dungeon fields travel together.
            raise ValueError("a dungeon location carries dungeon_id, level_number, and position")
        try:
            level = session.adventure.dungeon(dungeon_id).level(level_number)
        except ValueError:
            return [Rejection(code="session.command.unknown_location", params={"dungeon": dungeon_id})], []
        if not level.in_bounds(position):
            return [Rejection(code="session.command.out_of_bounds")], []
        session.dungeon_state.location = location
        session.dungeon_state.mark_explored(dungeon_id, level_number, position)
        session.mode = SessionMode.EXPLORING
        events.append(LocationEnteredEvent(location_kind="dungeon", location_id=dungeon_id, level_number=level_number))
    else:
        session.dungeon_state.location = location
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


def _handle_roll_dice(session: GameSession, command: RollDice) -> tuple[list[Rejection], list[Event]]:
    from osrlib.core.dice import roll

    # The command's field validator already guaranteed the expression parses, so the
    # draw happens unconditionally here — validation is the pure pre-phase, the roll is
    # the only side effect, and it lands on its own stream to leave keyed draws untouched.
    result = roll(command.expression, session.streams.get(ADJUDICATION_STREAM))
    return [], [DiceRolledEvent(expression=command.expression, total=result.total, rolls=result.rolls)]


_REFEREE_HANDLERS = {
    GrantItem: _handle_grant_item,
    GrantCoins: _handle_grant_coins,
    AwardXP: _handle_award_xp,
    SetFlag: _handle_set_flag,
    MarkTriggerFired: _handle_mark_trigger_fired,
    AddJournalEntry: _handle_add_journal_entry,
    RecordNote: _handle_record_note,
    SpawnMonsters: _handle_spawn_monsters,
    SpawnNpcParty: _handle_spawn_npc_party,
    SetDoorState: _handle_set_door_state,
    IdentifyItem: _handle_identify_item,
    PlaceParty: _handle_place_party,
    AdvanceTime: _handle_advance_time,
    RollDice: _handle_roll_dice,
}

_HANDLERS_CACHE: dict | None = None


def _handlers() -> Mapping[type[Command], Any]:
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
