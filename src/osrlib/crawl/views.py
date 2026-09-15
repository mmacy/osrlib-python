"""The projection API: the player's safe whitelist and the referee's full state.

A view is the snapshot a front end renders: everything on the screen after a command,
in one frozen object.

Where the views sit. A command changes the session state that
[`GameSession`][osrlib.crawl.session.GameSession] keeps, and
[`build_player_view`][osrlib.crawl.views.build_player_view] and
[`build_referee_view`][osrlib.crawl.views.build_referee_view] read that state to build
these projections. The player view is built from session state alone and never from the
event log; the referee view is the save's own serialization, so it includes the event log
along with everything else the save keeps.
[`GameSession.view`][osrlib.crawl.session.GameSession.view] is the entry point most games
call, with a [`Visibility`][osrlib.core.events.Visibility] to pick which one. Events tell
you what just happened, and a view tells you what is true now. The ids a view includes,
[`MemberView.id`][osrlib.crawl.views.MemberView] and
[`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView], are the ids the
commands in [`osrlib.crawl.commands`][osrlib.crawl.commands] name.

The player view is an enumerated whitelist: party public sheets, location and facing, the
mapped cells with their edges (walked cells, the remembered cells the party's light has
shown it, and what its light reveals right now, with secret doors rendered as wall until
discovered), known piles and emptied caches in explored space, active effects on party
members with their remaining durations, the elapsed clock, the mode, the journal, the
active quests with their revealed objectives, the current encounter or battle's public
state, fatigue, exhaustion, and deprivation status, and the adventure's public prose.

It never includes unexplored geometry, undiscovered traps or secret doors, monster hit
points or stat internals, referee-visibility roll outcomes, session flags, trigger
fired-marks, referee notes, quest wiring such as activation clauses, patterns,
conditions, rewards, and hidden objectives, the quests that are not active, meaning both
the ones nobody has taken on yet and the ones already finished, RNG state, or the master
seed, which lives only in the save and reaches neither view.

The referee view includes everything else the save does, minus RNG internals and the seed,
for LLM referees and tests. Never trust the client with it: a networked game keeps the
session and the referee view on the server and sends only the player view, or
player-visibility events, over the wire. The guide
[Views and visibility](https://mmacy.github.io/osrlib-python/guides/views-and-visibility/)
walks the whole projection in a running front end.
"""

from pydantic import BaseModel, ConfigDict

from osrlib.core.effects import Condition, has_condition
from osrlib.core.items import MagicItemCategory, MagicItemInstance, magic_item_template
from osrlib.crawl.dungeon import Direction, EdgeKind, PartyLocation, Position, cell_ref, edge_ref
from osrlib.crawl.exploration import EXHAUSTED_KIND, FATIGUE_KIND, _light_reveal
from osrlib.crawl.session import JournalEntry

__all__ = [
    "EdgeView",
    "EncounterGroupView",
    "EncounterView",
    "ExploredLevelView",
    "MemberEffectView",
    "MemberView",
    "ObjectiveView",
    "PileView",
    "PlayerView",
    "QuestView",
    "RefereeView",
    "build_player_view",
    "build_referee_view",
]


class MemberView(BaseModel):
    """One member's public sheet: the players know their own characters.

    You get these from [`PlayerView.party`][osrlib.crawl.views.PlayerView], in marching
    order, and every party member has one, living or dead. A member's own numbers are
    not secrets, so the sheet is full, and the one thing play hides from the players is
    the identity of an unidentified magic item in the pack.

    Examples:
        ```python
        from osrlib.crawl.views import MemberView

        sheet = MemberView(
            id="pc1",
            name="Hild",
            class_id="fighter",
            level=1,
            current_hp=7,
            max_hp=7,
            conditions=(),
            inventory={},
            memorized_spells=(),
        )
        assert sheet.id == "pc1"  # the id a command's character_id names
        ```
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The member's character id. This is the id you put in the `character_id` of a
    command such as [`GrantItem`][osrlib.crawl.commands.GrantItem] or a battle
    declaration, so a wire client can act without holding the session."""
    name: str
    """The character's name, as the player wrote it."""
    class_id: str
    """The character class id, such as `"fighter"`, from the class catalog."""
    level: int
    """The character's experience level."""
    current_hp: int
    """Current hit points. Zero or less means the member is down, and `conditions` says
    what took them out."""
    max_hp: int
    """Maximum hit points at the current level."""
    conditions: tuple[str, ...]
    """The conditions on the member right now, as
    [`Condition`][osrlib.core.effects.Condition] wire values such as `"dead"` or
    `"sleeping"`, in the order the ledger holds them."""
    inventory: dict
    """The member's pack, in the shape the inventory serializes, with the keys `items`,
    `purse`, `valuables`, `worn_armour`, `shield`, `wielded`, and `rings`. Magic items
    are masked until identified: an unidentified one shows a category display name
    instead of its true name, and an unidentified arm additionally carries the
    `qualities` and `missile_ranges` of the mundane weapon its display name already
    names, exactly as an identified one does, so a front end can classify a declaration
    without knowing the arm's bonus, curse, or template id. Charges never appear at any
    identification level."""
    memorized_spells: tuple[dict, ...]
    """The prepared spells, one dumped
    [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] per copy, in memorization
    order. Empty for a class that casts nothing."""


class MemberEffectView(BaseModel):
    """An active effect on a party member: the players track their own torches and spells.

    You get these from [`PlayerView.effects`][osrlib.crawl.views.PlayerView]. Only
    effects attached to a party member appear. An effect anchored to a dungeon cell is
    the referee's business and stays out.
    """

    model_config = ConfigDict(frozen=True)

    character_id: str
    """The id of the member the effect is attached to, matching a
    [`MemberView.id`][osrlib.crawl.views.MemberView]."""
    kind: str
    """The effect kind, such as `"light"` or `"fatigue"`, which is the same string an
    [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition] names."""
    remaining_rounds: int | None
    """Rounds left before the effect expires, never below zero. `None` means the players
    are not told: an effect with no expiry, and any effect a potion granted, because the
    referee rolls and tracks a potion's duration and never announces it."""


class EdgeView(BaseModel):
    """One visible edge: what occupies it, and a door's state.

    You get these from the `edges` of an
    [`ExploredLevelView`][osrlib.crawl.views.ExploredLevelView], keyed by canonical edge
    key. An undiscovered secret door renders as wall, so a front end that draws what it
    is given never reveals one.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    """What occupies the edge: `"open"`, `"wall"`, or `"door"`, the wire values of
    [`EdgeKind`][osrlib.crawl.dungeon.EdgeKind]. An undiscovered secret door reports
    `"wall"`."""
    door_open: bool | None = None
    """Whether the door stands open. `None` on an edge that is not a door."""
    door_wedged: bool | None = None
    """Whether the door has been wedged with an iron spike. `None` on an edge that is not
    a door."""


class PileView(BaseModel):
    """A known dropped pile in explored space.

    You get these from [`PlayerView.piles`][osrlib.crawl.views.PlayerView], keyed by cell
    reference. A pile in a cell the party has not walked is left out, so the view never
    tells the players about loot they have not found.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[str, ...]
    """One display string per thing in the pile, in the order mundane items, magic items,
    then valuables. A mundane entry reads `"{item_id}×{quantity}"`. A magic item shows its
    name once identified and its masked display name before that. A valuable shows its
    name, or its kind when it has no name."""
    coins_gp_value: int
    """The pile's coins, converted to their value in gold pieces."""


class ExploredLevelView(BaseModel):
    """One level's explored map: the cells the party knows, and the edges around them.

    You get these from [`PlayerView.explored`][osrlib.crawl.views.PlayerView], one per
    level the party has any knowledge of. This is the map to draw: nothing outside it is
    knowledge the party has.
    """

    model_config = ConfigDict(frozen=True)

    dungeon_id: str
    """The id of the dungeon this level belongs to."""
    level_number: int
    """The 1-based level number."""
    cells: tuple[Position, ...]
    """The known cells as `(x, y)` pairs: the cells the party has walked, then the cells
    it remembers having seen, then the cells its light shows from where it stands right
    now. Lighting a torch redraws the room in the next view, with no footstep in
    between."""
    edges: dict[str, EdgeView]
    """The edges around those cells, keyed by the canonical edge key that
    [`edge_key`][osrlib.crawl.dungeon.edge_key] returns, in the form `"{x},{y}:north"` or
    `"{x},{y}:west"`. Each physical edge appears once, so a wall between two known cells
    is one entry, not two."""


class EncounterGroupView(BaseModel):
    """A monster group as the players see it: what it is, how many, how far, how it looks.

    You get these from [`EncounterView.groups`][osrlib.crawl.views.EncounterView]. Hit
    points and stat internals never appear, because the players work from what they can
    see across the room.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The group's id, which is the command vocabulary: a battle declaration names its
    `target_group_id` with it, so a wire client needs it to fight at all. It is an
    allocator ordinal rather than a secret, the same way a
    [`MemberView.id`][osrlib.crawl.views.MemberView] is."""
    label: str
    """What the group is called, such as `"goblins"`, for the line a front end prints."""
    count: int
    """How many of the group are still standing. The dead are not counted."""
    distance_feet: int
    """How far away the group is on the range track, in feet."""
    visible_conditions: tuple[str, ...]
    """The conditions visible on the living members of the group, as
    [`Condition`][osrlib.core.effects.Condition] wire values, sorted and deduplicated so
    the same condition on six goblins reads once."""


class EncounterView(BaseModel):
    """The current encounter or battle's public state.

    You get one from [`PlayerView.encounter`][osrlib.crawl.views.PlayerView], and `None`
    there means nothing is happening.

    Four id tuples, `declarers`, `front_rank`, `immobile`, and `reloading`, describe the
    round's shape as the players know it at the table: who is able to act, who stands
    close enough to swing, who is held fast, and who is still cranking a windlass. Each
    one corresponds to a rejection the engine would otherwise raise against the whole
    round, so a front end that reads all four offers only the declarations the engine will
    accept. Without them a front end has to assume a rank width, and a wrong assumption
    costs the party its turn.
    """

    model_config = ConfigDict(frozen=True)

    groups: tuple[EncounterGroupView, ...]
    """The monster groups in the encounter, in the order the encounter holds them."""
    stance: str | None
    """How the monsters are behaving, as a [`ReactionResult`][osrlib.core.tables.ReactionResult]
    wire value: `"attacks"`, `"hostile"`, `"uncertain"`, `"indifferent"`, or `"friendly"`.
    `None` before the reaction roll has settled it."""
    in_battle: bool
    """Whether the encounter has become a battle with rounds and declarations."""
    battle_round: int | None = None
    """The current battle round, counting from the first. `None` outside a battle."""
    pursuit_gap_feet: int | None = None
    """How far ahead of its pursuers the fleeing party is, in feet. `None` when nobody is
    pursuing."""
    declarers: tuple[str, ...] = ()
    """Every member who has to declare this round, in marching order: the ones living and
    able to act. A [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound]
    naming any other roster, a slept or paralysed member included, is rejected whole with
    `battle.declaration.roster_mismatch`."""
    front_rank: tuple[str, ...] = ()
    """The living members close enough to attack in melee, in marching order. That is the
    party's first rank at the current formation width, or every living member when the
    ruleset's `formation_width_limit` flag is off. A melee attack declared for anyone
    else is rejected with `battle.declaration.not_in_front_rank`, and inside melee reach a
    weapon that is both melee and missile counts as a melee weapon."""
    immobile: tuple[str, ...] = ()
    """The declarers who cannot move this round, which in practice means the entangled,
    since the states that stop a move otherwise stop a declaration as well. Their `close`
    and `withdraw` moves are rejected with `battle.declaration.cannot_move`."""
    reloading: tuple[str, ...] = ()
    """The members who may not fire a `reload` weapon this round, because they fired one
    last round (`combat.attack.reload`). Empty when the ruleset's `weapon_reload` flag is
    off, so a front end can combine this list with the weapon's own qualities and never
    read the flag itself."""


class ObjectiveView(BaseModel):
    """One revealed objective as the players know it: what it is called, and whether it is done.

    You get these from [`QuestView.objectives`][osrlib.crawl.views.QuestView]. A hidden
    objective has no view at all: one nobody has been told about is absent from the list
    rather than listed as unknown, which is why `state` needs only the two values a
    visible objective can be in. The authored source is
    [`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec] and the live state is
    [`ObjectiveState`][osrlib.crawl.session.ObjectiveState], neither of which a wire
    client holds.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The objective's authored id, scoped to its quest."""
    name: str
    """The objective's display label: its authored `name`, or its id when the
    document authors none. It is never empty, because the view's job is saying what the
    objective is called."""
    state: str
    """`"incomplete"` or `"complete"`."""


class QuestView(BaseModel):
    """One active quest as the players know it: the charge, who gave it, and where it stands.

    You get these from [`PlayerView.quests`][osrlib.crawl.views.PlayerView]. The wiring
    that starts a quest, checks it off, and pays it, meaning its clauses, patterns,
    conditions, and rewards, never appears: that is the game's secret exactly as a
    trigger's is. Read the authored quest itself from
    [`QuestSpec`][osrlib.crawl.quests.QuestSpec] and its live state from
    [`QuestState`][osrlib.crawl.session.QuestState] when you hold the session.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The quest's authored id."""
    name: str
    """The quest's authored display name."""
    narrative: str
    """The quest's authored offer beat, or the empty string when unauthored. The words
    themselves travel, because a wire client holds no adventure document to resolve them
    from."""
    speaker: str
    """Who is speaking the offer, such as `"Sister Halda"`, or the empty string when the
    block authors no attribution."""
    objectives: tuple[ObjectiveView, ...]
    """The revealed objectives, in the order the quest authored them."""


class PlayerView(BaseModel):
    """The safe projection: an enumerated whitelist of exactly the fields a player may see.

    Build one with [`build_player_view`][osrlib.crawl.views.build_player_view], or with
    [`GameSession.view`][osrlib.crawl.session.GameSession.view] and
    `Visibility.PLAYER`. This is the object a networked game sends over the wire: every
    field on it is safe to show at the table, and nothing the party has not learned is
    on it. Rebuild it after every command, because it is a frozen snapshot and nothing
    updates it in place.
    """

    model_config = ConfigDict(frozen=True)

    adventure_name: str
    """The adventure's title."""
    adventure_description: str
    """The adventure's public prose, the blurb a front end shows on the title screen."""
    town_name: str
    """The base town's name."""
    town_description: str
    """The base town's public prose."""
    town_services: tuple[str, ...]
    """The services the town offers, as authored prose for a front end to list."""
    party: tuple[MemberView, ...]
    """The party's public sheets, in marching order, the dead included."""
    location: PartyLocation
    """Where the party is: the town, or a dungeon cell with its facing."""
    clock_rounds: int
    """The elapsed game clock in rounds, counting from the start of the session."""
    mode: str
    """The session mode as a [`SessionMode`][osrlib.crawl.commands.SessionMode] wire
    value: `"town"`, `"exploring"`, `"encounter"`, `"battle"`, `"game_over"`, or
    `"victory"`. It tells a front end which commands are legal right now."""
    explored: tuple[ExploredLevelView, ...]
    """The party's map, one entry per level it knows anything about."""
    piles: dict[str, PileView]
    """The dropped piles the party knows about, keyed by the cell reference
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref] returns. A pile in a cell the party has
    not walked is left out."""
    emptied_caches: tuple[str, ...]
    """The treasure caches the party has already emptied, as `"{dungeon}:{level}:{id}"`
    references, so a front end can draw a looted cache as looted."""
    effects: tuple[MemberEffectView, ...]
    """The active effects on party members, in ledger order."""
    fatigued: bool
    """Whether any member is fatigued."""
    exhausted: bool
    """Whether any member is exhausted."""
    deprivation: dict[str, dict[str, int]]
    """Food and water deprivation, keyed by character id, each value holding
    `food_days` and `water_days`. A member with no deprivation on either track is left
    out, so an empty dict means the party has been eating and drinking."""
    journal: tuple[JournalEntry, ...]
    """The session journal, shipped as written: the players' own record of the
    adventure, in order of discovery, each beat with the clock position it landed
    at. The wiring behind the beats, meaning trigger fired-marks and referee notes, stays
    out."""
    quests: tuple[QuestView, ...]
    """The quests in play, in the order the adventure authored them: active ones only.
    A quest nobody has taken on yet is not the party's business, and a finished one
    leaves the list, and its record is the journal, which keeps every beat it wrote."""
    encounter: EncounterView | None = None
    """The current encounter or battle's public state, or `None` when the party is not in
    one."""


class RefereeView(BaseModel):
    """The full state projection minus RNG internals, for LLM referees and tests.

    Build one with [`build_referee_view`][osrlib.crawl.views.build_referee_view], or with
    [`GameSession.view`][osrlib.crawl.session.GameSession.view] and `Visibility.REFEREE`.
    Use it behind the screen: for the context an LLM referee reasons over, for a
    debugging panel, for a test that asserts on state a player may not see. Never send it
    to a player's client, which is what [`PlayerView`][osrlib.crawl.views.PlayerView] is
    for.
    """

    model_config = ConfigDict(frozen=True)

    state: dict
    """The whole session state as the save serializes it, including the event log, minus
    the RNG stream positions and the master seed.

    The keys are the save's keys, so `state["flags"]` is the flag store,
    `state["command_log"]` the command log, and `state["dungeon_state"]` the map overlay.
    [`session_state`][osrlib.persistence.session_state] is the function that builds the
    dict and names every key, and [`osrlib.persistence`][osrlib.persistence] describes
    what a save holds."""


_MASKED_CATEGORY_NAMES = {
    MagicItemCategory.POTION: "a potion",
    MagicItemCategory.SCROLL: "a scroll",
    MagicItemCategory.RING: "a ring",
    MagicItemCategory.WAND: "a wand",
    MagicItemCategory.STAFF: "a staff",
    MagicItemCategory.ROD: "a rod",
    MagicItemCategory.MISC: "a curious device",
}


def _masked_magic_item(instance: MagicItemInstance) -> dict:
    """One magic item as the player sees it, masked until identified.

    An unidentified item shows its category display name, and an enchanted arm shows its
    base instead, as in "a sword with a faint aura", the concession made because *detect
    magic* exists. That display string already names the base weapon, so an unidentified
    arm also shows the `qualities` and `missile_ranges` of the mundane weapon underneath
    it, exactly as an identified one does: how far the arm reaches, and in what manner.
    Both are rulebook facts about the weapon the display string already names, not facts
    about the enchantment, and they are what lets a front end tell a melee declaration
    from a missile one. Without them an enchanted dagger is unclassifiable where a plain
    dagger is not. An identified item additionally shows its true name, id, and whether a
    curse has been revealed. The bonus, the curse, the template id, and the name stay
    hidden until identified, and charges, sentience, and per-item state never appear at
    any identification level, because by the rules as written charges are undiscoverable.
    """
    from osrlib.core.combat import attack_facet
    from osrlib.data import load_equipment

    template = magic_item_template(instance)
    facet = attack_facet(instance)
    if instance.identified:
        payload = {
            "instance_type": "magic_item",
            "instance_id": instance.instance_id,
            "template_id": instance.template_id,
            "name": template.name,
            "quantity": instance.quantity,
            "identified": True,
            "cursed": instance.cursed_revealed,
        }
    else:
        display = _MASKED_CATEGORY_NAMES.get(template.category)
        if display is None:
            base_id = instance.base_item_id or template.base_item_id
            base_name = load_equipment().get(base_id).name.lower() if base_id is not None else "arm"
            display = f"a {base_name} with a faint aura"
        payload = {
            "instance_type": "magic_item",
            "instance_id": instance.instance_id,
            "display": display,
            "quantity": instance.quantity,
            "identified": False,
        }
    if facet is not None:
        payload["qualities"] = [quality.value for quality in facet.qualities]
        if facet.missile_ranges is not None:
            payload["missile_ranges"] = facet.missile_ranges.model_dump(mode="json")
    return payload


def _masked_instance(instance) -> dict:
    if isinstance(instance, MagicItemInstance):
        return _masked_magic_item(instance)
    return instance.model_dump(mode="json")


def _masked_inventory(member) -> dict:
    """The inventory as the player sees it: valuables exact, magic items masked."""
    inventory = member.inventory
    return {
        "items": [_masked_instance(instance) for instance in inventory.items],
        "purse": inventory.purse.model_dump(mode="json"),
        "valuables": [valuable.model_dump(mode="json") for valuable in inventory.valuables],
        "worn_armour": _masked_instance(inventory.worn_armour) if inventory.worn_armour is not None else None,
        "shield": _masked_instance(inventory.shield) if inventory.shield is not None else None,
        "wielded": [_masked_instance(instance) for instance in inventory.wielded],
        "rings": [_masked_instance(instance) for instance in inventory.rings],
    }


def _effect_remaining_rounds(session, effect) -> int | None:
    """Remaining rounds for the member-effect view, with potion durations hidden.

    By the rules as written the referee rolls and tracks a potion's duration and never
    tells the player how long it will last, so a potion-sourced effect always reports
    `None` here.
    """
    if effect.definition.params.get("item_source") == "potion":
        return None
    if effect.expires_round is None:
        return None
    return max(0, effect.expires_round - session.clock.rounds)


def build_player_view(session) -> PlayerView:
    """Build the player view from session state, never from the event log.

    Call it after every accepted command to get the snapshot your front end renders, and
    send that object rather than the session to any client you do not control.
    [`GameSession.view`][osrlib.crawl.session.GameSession.view] with `Visibility.PLAYER`
    calls this for you, so use it when you already hold the session and reach for this
    function when you want the builder itself.

    The call reads session state and mutates nothing, so building a view twice gives two
    equal snapshots and costs the party no game time.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        The frozen whitelist projection, holding only what the party has learned.

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
        from osrlib.crawl.views import build_player_view

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
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)

        player = build_player_view(session)
        print(player.mode)
        # town
        print(player.party[0].id)
        # character-0001
        assert "flags" not in player.model_dump()  # session flags are the game's secret
        ```
    """
    members = tuple(
        MemberView(
            id=member.id,
            name=member.name,
            class_id=member.class_id,
            level=member.level,
            current_hp=member.current_hp,
            max_hp=member.max_hp,
            conditions=tuple(active.condition.value for active in member.conditions),
            inventory=_masked_inventory(member),
            memorized_spells=tuple(copy.model_dump(mode="json") for copy in member.memorized_spells),
        )
        for member in session.party.members
    )
    member_ids = {member.id for member in session.party.members}
    effects = tuple(
        MemberEffectView(
            character_id=effect.target_ref,
            kind=effect.definition.kind,
            remaining_rounds=_effect_remaining_rounds(session, effect),
        )
        for effect in session.ledger.effects
        if effect.target_ref in member_ids
    )
    explored = tuple(_explored_levels(session))
    visible_refs = _visible_cell_refs(session)
    piles = {
        ref: PileView(
            items=tuple(
                (
                    *(f"{entry.item_id}×{entry.quantity}" for entry in pile.items),
                    *(
                        str(_masked_magic_item(item).get("name", _masked_magic_item(item).get("display")))
                        for item in pile.magic_items
                    ),
                    *(valuable.name or valuable.kind for valuable in pile.valuables),
                )
            ),
            coins_gp_value=pile.coins.value_gp,
        )
        for ref, pile in session.dungeon_state.piles.items()
        if ref in visible_refs
    }
    fatigued = any(session.ledger.active_on(member.id, FATIGUE_KIND) for member in session.party.members)
    exhausted = any(session.ledger.active_on(member.id, EXHAUSTED_KIND) for member in session.party.members)
    deprivation = {
        member_id: {"food_days": state.food_days, "water_days": state.water_days}
        for member_id, state in session.deprivation.items()
        if state.worst > 0
    }
    return PlayerView(
        adventure_name=session.adventure.name,
        adventure_description=session.adventure.description,
        town_name=session.adventure.town.name,
        town_description=session.adventure.town.description,
        town_services=session.adventure.town.services,
        party=members,
        location=session.dungeon_state.location,
        clock_rounds=session.clock.rounds,
        mode=session.mode.value,
        explored=explored,
        piles=piles,
        emptied_caches=tuple(session.dungeon_state.emptied_caches),
        effects=effects,
        fatigued=fatigued,
        exhausted=exhausted,
        deprivation=deprivation,
        journal=tuple(session.journal),
        quests=tuple(_quest_views(session)),
        encounter=_encounter_view(session),
    )


def _quest_views(session):
    """The active quests, in document order, each with its revealed objectives.

    The walk is over the authored specs rather than the state block, so the order the view
    ships is the order the adventure wrote, and a quest the block does not know is absent
    rather than an error, the same way an unresolvable level is.
    """
    for quest in session.adventure.quests:
        state = session.quests.get(quest.id)
        if state is None or state.status != "active":
            continue
        objectives = []
        for objective in quest.objectives:
            objective_state = state.objectives.get(objective.id)
            if objective_state is None or not objective_state.revealed:
                continue
            objectives.append(
                ObjectiveView(
                    id=objective.id,
                    name=objective.name or objective.id,
                    state="complete" if objective_state.complete else "incomplete",
                )
            )
        narrative = quest.narrative
        yield QuestView(
            id=quest.id,
            name=quest.name,
            narrative=narrative.offer if narrative is not None else "",
            speaker=narrative.speaker if narrative is not None else "",
            objectives=tuple(objectives),
        )


def _visible_cell_refs(session) -> set[str]:
    refs: set[str] = set()
    for key, cells in session.dungeon_state.explored.items():
        dungeon_id, level_number = key.rsplit(":", 1)
        for cell in cells:
            refs.add(cell_ref(dungeon_id, int(level_number), cell))
    return refs


def _explored_levels(session):
    reveal_key, reveal_cells = _light_reveal(session)
    dungeon_state = session.dungeon_state
    keys = list(dungeon_state.explored)
    keys.extend(key for key in dungeon_state.seen if key not in dungeon_state.explored)
    for key in keys:
        dungeon_id, level_text = key.rsplit(":", 1)
        level_number = int(level_text)
        try:
            level = session.adventure.dungeon(dungeon_id).level(level_number)
        except ValueError:
            continue
        # Visible equals walked cells, plus the persisted seen cells the party's
        # light has shown it (map memory; see `DungeonState.seen`), plus what its
        # light reveals from the current cell right now, so lighting a torch draws
        # the room immediately, without a footstep or even a command between the
        # ledger and the view.
        visible = list(dungeon_state.explored.get(key, []))
        known = set(visible)
        for cell in dungeon_state.seen.get(key, []):
            if cell not in known:
                visible.append(cell)
                known.add(cell)
        if key == reveal_key:
            visible.extend(cell for cell in reveal_cells if cell not in known)
        edges: dict[str, EdgeView] = {}
        for cell in visible:
            for direction in Direction:
                key_text = _canonical_edge(cell, direction)
                if key_text in edges:
                    continue
                edge = level.edge(cell, direction)
                if edge.kind is EdgeKind.DOOR:
                    ref = edge_ref(dungeon_id, level_number, cell, direction)
                    state = session.dungeon_state.doors.get(ref)
                    if edge.door.kind == "secret" and (state is None or not state.discovered):
                        edges[key_text] = EdgeView(kind="wall")
                        continue
                    edges[key_text] = EdgeView(
                        kind="door",
                        door_open=bool(state.open) if state is not None else edge.door.starts_open,
                        door_wedged=bool(state.wedged) if state is not None else False,
                    )
                else:
                    edges[key_text] = EdgeView(kind=edge.kind.value)
        yield ExploredLevelView(dungeon_id=dungeon_id, level_number=level_number, cells=tuple(visible), edges=edges)


def _canonical_edge(cell: Position, direction: Direction) -> str:
    from osrlib.crawl.dungeon import edge_key

    return edge_key(cell, direction)


def _encounter_view(session) -> EncounterView | None:
    from osrlib.core.combat import cannot_move
    from osrlib.crawl.battle import _able_declarers, _party_front_rank

    state = session.encounter
    if state is None:
        return None
    groups = []
    for group in state.groups:
        living = [
            session.combatant(monster_id)
            for monster_id in group.monster_ids
            if not has_condition(session.combatant(monster_id), Condition.DEAD)
        ]
        conditions = sorted({active.condition.value for monster in living for active in monster.conditions})
        groups.append(
            EncounterGroupView(
                id=group.id,
                label=group.label,
                count=len(living),
                distance_feet=group.distance_feet,
                visible_conditions=tuple(conditions),
            )
        )
    declarers = _able_declarers(session)
    battle = session.battle
    fired = battle.fired_last_round if battle is not None and session.ruleset.weapon_reload else ()
    return EncounterView(
        groups=tuple(groups),
        stance=state.stance,
        in_battle=battle is not None,
        battle_round=battle.round if battle is not None else None,
        pursuit_gap_feet=state.pursuit.gap_feet if state.pursuit is not None else None,
        declarers=tuple(member.id for member in declarers),
        front_rank=tuple(member.id for member in _party_front_rank(session)),
        immobile=tuple(member.id for member in declarers if cannot_move(member)),
        reloading=tuple(fired),
    )


def build_referee_view(session) -> RefereeView:
    """Build the referee view: everything but RNG internals and the seed.

    Use it for the context an LLM referee reasons over, for a debugging panel, or for a
    test that asserts on state a player may not see.
    [`GameSession.view`][osrlib.crawl.session.GameSession.view] with `Visibility.REFEREE`
    calls this for you. Never hand the result to a player's client: that is what
    [`build_player_view`][osrlib.crawl.views.build_player_view] is for.

    The state it returns is the save's own serialization, including the event log, so a
    view of a long session is a large object. Build it when you need it rather than once
    per command.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        The full-state projection, minus the RNG stream positions and the master seed.

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
        from osrlib.crawl.views import build_referee_view

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
        adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
        session = GameSession.new(Party(members=[hero.character]), adventure, seed=7)

        referee = build_referee_view(session)
        print(referee.state["mode"], referee.state["clock_rounds"])
        # town 0
        assert "flags" in referee.state  # the wiring a player never sees
        assert "master_seed" not in referee.state  # the seed lives only in the save
        ```
    """
    from osrlib.persistence import session_state

    state = session_state(session, include_event_log=True)
    state.pop("rng_streams", None)
    state.pop("master_seed", None)
    return RefereeView(state=state)
