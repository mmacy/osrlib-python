"""The two views of a session: the fields a player may see, and the referee's full state.

A view is the snapshot you render: everything on the screen after a command,
in one frozen object.

Call [`GameSession.view`][osrlib.crawl.session.GameSession.view] with a
[`Visibility`][osrlib.core.events.Visibility] after each command. `Visibility.PLAYER`
returns a [`PlayerView`][osrlib.crawl.views.PlayerView], what a player may see, and
`Visibility.REFEREE` returns a [`RefereeView`][osrlib.crawl.views.RefereeView], the full
state. [`build_player_view`][osrlib.crawl.views.build_player_view] and
[`build_referee_view`][osrlib.crawl.views.build_referee_view] are the builders that
method calls. The other classes here are the parts of a player view, and
[`ExplorationCounters`][osrlib.crawl.views.ExplorationCounters] is a part of the referee
view.

Events report what just happened, and a view shows what is true now. A view is built
from the session's current state, never by reading the event log, though the referee
view includes the log as one of its fields. The ids a view includes,
[`MemberView.id`][osrlib.crawl.views.MemberView] and
[`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView], are the ids the
commands in [`osrlib.crawl.commands`][osrlib.crawl.commands] take.

The player view has a fixed list of fields, each one safe to show a player. It includes:

- each party member's public sheet, and the party's location and facing
- the mapped cells and their edges: the cells the party walked, the cells its light has
  shown it, and what its light shows right now, with an undiscovered secret door shown
  as wall
- the dropped piles on cells the party has walked, and the caches it has emptied
- the active effects on party members, with their remaining durations
- the elapsed clock and the session mode
- the journal, and the active quests with their revealed objectives
- the public state of the current encounter or battle
- fatigue, exhaustion, and deprivation status
- the adventure's and the town's public prose

It never includes:

- geometry the party hasn't explored, or undiscovered traps and secret doors
- monster hit points or stat internals
- the outcomes of referee-visibility rolls
- session flags, trigger fired-marks, or referee notes
- the logic of a quest (its activation clauses, patterns, conditions, and rewards), its
  hidden objectives, or any quest that isn't active, whether not yet taken on or already
  finished
- the RNG state or the master seed, which only the save contains

The referee view includes everything else the save does, except the random streams'
positions and the master seed. Use it for an LLM referee, a debugging panel, or a test. Its
fields are the groups [`session_state`][osrlib.persistence.session_state] writes, so
`view.monsters[0].current_hp` and `view.flags["key"]` have the types this reference
documents, and `view.model_dump(mode="json")` is that save data without those two keys.
Don't send it to a player's client. In a networked game, keep the session and the referee view
on the server, and send only the player view, or the player-visibility events. The guide [Views
and visibility](https://mmacy.github.io/osrlib-python/guides/views-and-visibility/) shows both
views in a running front end.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, SpawnMonsters
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

rules = Ruleset()
stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
hero = create_character(
    name="Hild",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=stream,
).character
level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
session = GameSession.new(Party(members=[hero]), adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))

# The player sees the party's sheets and the goblins across the room, not their hit points.
player = session.view(Visibility.PLAYER)
for member in player.party:
    print(member.name, member.current_hp, member.max_hp)
# Hild 3 3
group = player.encounter.groups[0]
print(group.label, group.count, group.distance_feet)
# goblin 2 30

# The referee sees each monster's hit points.
referee = session.view(Visibility.REFEREE)
print([monster.current_hp for monster in referee.monsters])
# [1, 3]
```
"""

from copy import deepcopy

from pydantic import BaseModel, ConfigDict, SerializeAsAny

from osrlib.core.character import Character
from osrlib.core.effects import Condition, EffectsLedger, has_condition
from osrlib.core.events import Event
from osrlib.core.items import MagicItemCategory, MagicItemInstance, magic_item_template
from osrlib.core.monsters import IdAllocator, MonsterInstance
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.battle import BattleState
from osrlib.crawl.commands import Command, SessionMode
from osrlib.crawl.dungeon import Direction, DungeonState, EdgeKind, PartyLocation, Position, cell_ref, edge_ref
from osrlib.crawl.encounter import EncounterState
from osrlib.crawl.exploration import EXHAUSTED_KIND, FATIGUE_KIND, _light_reveal
from osrlib.crawl.party import Party
from osrlib.crawl.session import (
    DeathRecord,
    DefeatedMonsterRecord,
    DeprivationState,
    JournalEntry,
    QuestState,
)

__all__ = [
    "EdgeView",
    "EncounterGroupView",
    "EncounterView",
    "ExplorationCounters",
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
    """One party member's character sheet, as the players see it.

    You get these from [`PlayerView.party`][osrlib.crawl.views.PlayerView.party], in marching
    order, and every party member has one, living or dead. The players know everything about
    their characters, so the sheet is complete. The only thing missing is the identity of an
    unidentified magic item in the pack.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The member's character id, like `"character-0001"`. It's the id you put in the
    `character_id` of a command like [`GrantItem`][osrlib.crawl.commands.GrantItem] or a battle
    declaration, so a remote client can issue commands without having the session."""
    name: str
    """The character's name, as the player wrote it."""
    class_id: str
    """The character class id, like `"fighter"`, from the class catalog."""
    level: int
    """The character's experience level."""
    current_hp: int
    """Current hit points. Zero or less means the member is down, and `conditions` shows
    what took them out."""
    max_hp: int
    """Maximum hit points at the current level."""
    conditions: tuple[str, ...]
    """The conditions on the member right now, as
    [`Condition`][osrlib.core.effects.Condition] serialized values like `"dead"` or
    `"asleep"`, in the order the member gained them."""
    inventory: dict
    """The member's pack, as a JSON-ready dict with the keys `items`, `purse`, `valuables`,
    `worn_armour`, `shield`, `wielded`, and `rings`. Mundane items appear as the inventory
    serializes them.

    A magic item is masked until it's identified. An unidentified one has a `display` name in
    place of its true name, like `"a potion"`, or `"a dagger with a faint aura"` for a magic
    weapon. When the display name refers to a weapon, the entry also has the `qualities` of that
    plain weapon, as an identified one does, so you can tell a melee attack from a missile
    attack without revealing the weapon's bonus or curse. It has a `missile_ranges` key only
    when the rules give the weapon ranges, so on a sword the key is absent rather than empty. An
    item whose display name comes from its category, like `"a staff"`, has neither key, even
    when it's a weapon, because its display name doesn't refer to the weapon. An identified item
    shows its `name`, its `template_id`, and whether a curse has been revealed. Charges never
    appear."""
    memorized_spells: tuple[dict, ...]
    """The prepared spells, one dumped
    [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] per copy, in memorization
    order. Empty for a class that casts nothing."""
    encounter_rate_feet: int = 0
    """How far the member can move in one round of a battle or an encounter, in feet.

    It's a third of the member's movement rate after encumbrance, doubled while *haste* is on them.
    In a battle, a member who declares `move="close"` moves up to this far toward the group, and
    stops 5' from it. So a member can close on a group and attack it in the same round when their
    distance to it, in
    [`EncounterGroupView.member_distance_feet`][osrlib.crawl.views.EncounterGroupView.member_distance_feet],
    is at most this rate plus 5'. Use it to offer "close in and attack" only to the members who can
    reach.

    It doesn't account for conditions. A member who can't move this round, like one held by a web,
    still has a rate here, and
    [`EncounterView.immobile`][osrlib.crawl.views.EncounterView.immobile] lists them."""


class MemberEffectView(BaseModel):
    """One active effect on a party member, like a burning torch or a running spell.

    You get these from [`PlayerView.effects`][osrlib.crawl.views.PlayerView.effects]. Only
    effects on a party member appear. An effect attached to a dungeon cell, like a pool of
    burning oil, is for the referee and doesn't appear.
    """

    model_config = ConfigDict(frozen=True)

    character_id: str
    """The id of the member the effect is attached to, matching a
    [`MemberView.id`][osrlib.crawl.views.MemberView]."""
    kind: str
    """The effect kind, like `"light"` or `"fatigue"`. It's the same string an
    [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition] checks for."""
    remaining_rounds: int | None
    """Rounds left before the effect expires, never below zero. `None` when the effect has no
    expiry, and for any effect a potion gave, because by the rules the referee tracks a potion's
    duration and never tells the players."""


class EdgeView(BaseModel):
    """One visible edge: what occupies it, and a door's state.

    You get these from [`ExploredLevelView.edges`][osrlib.crawl.views.ExploredLevelView.edges].
    An undiscovered secret door appears as a wall, so if you draw what the view contains, you
    never reveal one.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    """What occupies the edge: `"open"`, `"wall"`, or `"door"`, the serialized values of
    [`EdgeKind`][osrlib.crawl.dungeon.EdgeKind]. For an undiscovered secret door, it's `"wall"`.
    On a cell side that has a passage, it's `"open"`. A passage is a level transition that leads
    through a wall: the level's edges have a wall there, but a
    [`MoveParty`][osrlib.crawl.commands.MoveParty] across that side takes the party to the
    passage's destination."""
    door_open: bool | None = None
    """Whether the door stands open. `None` on an edge that is not a door."""
    door_wedged: bool | None = None
    """Whether the door has been wedged with an iron spike. `None` on an edge that is not
    a door."""


class PileView(BaseModel):
    """A known dropped pile in explored space.

    You get these from [`PlayerView.piles`][osrlib.crawl.views.PlayerView.piles], keyed by
    cell reference. A pile in a cell the party has not walked is left out, so the view never
    shows the players loot they haven't found.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[str, ...]
    """One display string per thing in the pile, in the order mundane items, magic items,
    then valuables. A mundane entry has the form `"{item_id}×{quantity}"`. A magic item shows its
    name once identified and its masked display name before that. A valuable shows its
    name, or its kind when it has no name."""
    coins_gp_value: int
    """The pile's coins, as their value in whole gold pieces, rounded down."""


class ExploredLevelView(BaseModel):
    """One level's explored map: the cells the party knows, and the edges around them.

    You get these from [`PlayerView.explored`][osrlib.crawl.views.PlayerView.explored], one
    for each level the party knows anything about. This is the map to draw. The party knows
    nothing outside it.
    """

    model_config = ConfigDict(frozen=True)

    dungeon_id: str
    """The id of the dungeon this level belongs to."""
    level_number: int
    """The 1-based level number."""
    cells: tuple[Position, ...]
    """The known cells as `(x, y)` pairs: the cells the party has walked, then the cells
    it remembers having seen, then the cells its light shows from where it stands right
    now. When a member lights a torch, the next view includes the lit room, even if the party
    hasn't moved."""
    edges: dict[str, EdgeView]
    """The edges around those cells, keyed by the canonical edge key that
    [`edge_key`][osrlib.crawl.dungeon.edge_key] returns, in the form `"{x},{y}:north"` or
    `"{x},{y}:west"`. Each physical edge appears once, so a wall between two known cells
    is one entry, not two."""


class EncounterGroupView(BaseModel):
    """A monster group as the players see it: what it is, how many, how far, how it looks.

    You get these from [`EncounterView.groups`][osrlib.crawl.views.EncounterView.groups]. Hit
    points and other statistics never appear, because the players know only what they can see
    across the room.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The group's id, like `"group-0001"`. You put it in the `target_group_id` of a battle
    declaration, so a remote client needs it to fight at all. It's a session id that counts up,
    not a secret, the same as a [`MemberView.id`][osrlib.crawl.views.MemberView.id]."""
    label: str
    """What the group is called, for the line you show. It's the same as
    [`EncounterGroup.label`][osrlib.crawl.encounter.EncounterGroup], which lists where each kind
    of group gets its label."""
    count: int
    """How many of the group are still alive."""
    distance_feet: int
    """How far away the group is on the range track, in feet."""
    visible_conditions: tuple[str, ...]
    """The conditions visible on the living members of the group, as
    [`Condition`][osrlib.core.effects.Condition] serialized values, sorted and deduplicated so
    the same condition on six goblins appears once."""
    fled: bool = False
    """True once the group has run away and left the battle, as in
    [`EncounterGroup.fled`][osrlib.crawl.encounter.EncounterGroup.fled].

    A fled group stays in [`EncounterView.groups`][osrlib.crawl.views.EncounterView.groups], but the
    session refuses every declaration aimed at it with `battle.declaration.unknown_group`. So don't
    offer it as a target. A group that's running but hasn't got away yet isn't fled. You can still
    close on it, shoot at it, or cast at it."""
    member_distance_feet: dict[str, int] = {}
    """Each living party member's distance to this group, in feet, keyed by member id. Empty outside a
    battle.

    A battle measures each group's `distance_feet` from the party line, where the party stood when
    the encounter opened or where the line has moved back to since, and each member stands at their
    own advance from that line, in
    [`EncounterView.advance_feet`][osrlib.crawl.views.EncounterView.advance_feet]. This is the group's
    distance minus the member's advance. At 5' or less, the member and the group can reach each other
    in melee. Beyond that, a member attacks the group with a missile weapon, or declares
    `move="close"` to move toward it first. A missile's range band and a spell's range are measured
    from this distance."""
    front_rank: tuple[str, ...] = ()
    """The members who can make a melee attack on this group this round without moving, and whom this
    group's melee reaches, in marching order. Empty outside a battle, and empty while nobody stands
    within 5' of the group.

    These are the members in the party's front rank toward this group who stand within 5' of it. The
    ranks toward a group are the living members sorted by their distance to it, nearest first, and cut
    into rows of [`EncounterView.formation_width`][osrlib.crawl.views.EncounterView.formation_width].
    A row never mixes distances. Arrival order, then marching order, decides who is in the front row,
    so the member who got there in an earlier round stands in front of one who arrives later. This
    tuple then lists that row's members in marching order. It's always the same value as
    [`group_front_rank`][osrlib.crawl.battle.group_front_rank] for this group.

    The session refuses a melee attack on this group by any other member with
    `battle.declaration.not_in_front_rank`, or with `combat.attack.out_of_reach` when the member is
    farther than 5' away, unless the same declaration's `move="close"` brings them into it. When this
    tuple has fewer members than `formation_width`, a member who can reach the group this round can
    still take a place in it. [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round]
    answers that for a plan you're building.

    Each of this group's monsters picks its melee target from these members, leaving out invisible
    members and members whose *protection from evil* bars it. A monster with a missile attack closes
    and uses it at 5' too, so the members behind this rank are out of a monster group's reach. NPC
    adventurers with missile weapons shoot from farther away, at the members in the first row of the
    party's ranks toward their group."""
    lit: bool | None = None
    """Whether a light the party carries reaches this group, or `None` outside a battle.

    The group is lit when a living member with a burning light, like a torch or a lantern, stands
    within that light's radius of it, 30' for a torch or a lantern. A darkness effect that puts out
    the party's light, as [`GameSession.party_light`][osrlib.crawl.session.GameSession.party_light]
    reports, leaves every group unlit. The session refuses no declaration because a group is unlit, so
    use this field and `seen_by` to decide what your game offers or how it describes the fight."""
    seen_by: tuple[str, ...] = ()
    """The living members who can see this group, in marching order. Empty outside a battle.

    A member sees the group when it's `lit`, or when the member has working infravision and the group
    is within its range. A dwarf's and an elf's infravision reaches 60', and so does infravision an
    effect grants. A darkness effect that stops infravision, as
    [`GameSession.party_light`][osrlib.crawl.session.GameSession.party_light] reports, leaves it
    working for nobody. A blind member sees nothing."""


class EncounterView(BaseModel):
    """The current encounter or battle's public state.

    You get one from [`PlayerView.encounter`][osrlib.crawl.views.PlayerView.encounter], which
    is `None` when the party isn't in an encounter.

    In a battle, each party member stands at their own place on the range track, the
    one-dimensional measure of distance a battle uses. `advance_feet` gives each member's place, and
    each group in `groups` gives every member's distance to it and the members who can reach it in
    melee. Read those to draw the fight and to offer each member the moves and attacks they can make.

    The id tuples `declarers`, `immobile`, and `reloading` describe the battle round as the players
    see it at the table: who is able to act, who is held fast, and who is still reloading a crossbow.
    Each group's [`front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank] says who can attack
    that group in melee this round. Each of these matches a rejection that would refuse the whole
    round, so if you read them, you offer only the declarations the session accepts. To check a
    choice against the choices already made for the round, call
    [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round].
    """

    model_config = ConfigDict(frozen=True)

    groups: tuple[EncounterGroupView, ...]
    """The monster groups in the encounter, in the order the session's encounter state lists
    them."""
    stance: str | None
    """How the monsters are behaving, as a [`ReactionResult`][osrlib.core.tables.ReactionResult]
    serialized value: `"attacks"`, `"hostile"`, `"uncertain"`, `"indifferent"`, or `"friendly"`.
    `None` before the reaction roll has settled it."""
    in_battle: bool
    """Whether the encounter has become a battle with rounds and declarations."""
    battle_round: int | None = None
    """The current battle round, counting from the first. `None` outside a battle."""
    pursuit_gap_feet: int | None = None
    """How far ahead of its pursuers the fleeing party is, in feet. `None` when the party
    isn't being pursued."""
    declarers: tuple[str, ...] = ()
    """Every member who has to declare this round, in marching order: the ones living and
    able to act. A [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] with any
    other roster, a slept or paralysed member included, is rejected whole with
    `battle.declaration.roster_mismatch`."""
    front_rank: tuple[str, ...] = ()
    """The first row of the party's ranks toward the nearest group, in marching order.

    The nearest group is the group that hasn't fled and has a living member, and that stands closest
    to any living member. When two are equally near, it's the one that comes first in `groups`. The
    ranks toward a group are the living members sorted by their distance to it and cut into rows of
    `formation_width`, and this is the first row, however far away it stands. With the ruleset's
    `formation_width_limit` flag off, the row holds every living member at the nearest distance.
    Outside a battle, every member stands on the party line, so it's the party's first rank in
    marching order.

    In a fight with one group, before any member has moved, it's the members who can attack that
    group in melee once it's within 5'. As soon as members stand at different distances, or the party
    fights more than one group, read each group's
    [`front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank] instead, which says exactly who
    can attack that group in melee this round."""
    immobile: tuple[str, ...] = ()
    """The declarers who can't move this round. In practice they're the entangled members,
    because the other conditions that stop a move stop every declaration as well. A `move`
    declaration of any kind from one of them is rejected with
    `battle.declaration.cannot_move`, and so is an `attack` from one of them that sets
    `move="close"`."""
    reloading: tuple[str, ...] = ()
    """The members who may not fire a `reload` weapon this round, because they fired one
    last round (`combat.attack.reload`). Empty when the ruleset's `weapon_reload` flag is
    off, so you can combine this list with the weapon's qualities and never read the flag."""
    advance_feet: dict[str, int] = {}
    """Each living member's place on the range track, keyed by member id, in feet forward of the party
    line. Empty outside a battle.

    The party line is where the party stood when the encounter opened. Each group's `distance_feet`
    is measured from it. Every member starts a battle on the line, at zero. A member who closes on a
    group moves forward, and a member who falls back alone moves back, below zero once they're behind
    the line. When every member the monsters can see is behind the line, the line moves back to the
    foremost of them. This field and each group's `distance_feet` then show the moved values, and no
    distance between a member and a group changes. Use it to draw where each member stands. Each group's
    [`member_distance_feet`][osrlib.crawl.views.EncounterGroupView.member_distance_feet] gives the
    distance it leaves between the member and that group."""
    formation_width: int | None = None
    """How many members can fight side by side where the party stands, or `None` when the ruleset's
    `formation_width_limit` flag is off and a rank has no width limit.

    The width is the side of the largest square of open cells that includes the party's cell, times
    10', divided by 5' for each combatant
    ([`FIGHTER_FRONTAGE_FEET`][osrlib.crawl.battle.FIGHTER_FRONTAGE_FEET]). A one-cell corridor gives
    2, and so does a crossroads of one-cell corridors. A room gives twice its shorter side in cells: a
    4 by 4 room gives 8, and a 2 by 8 room gives 4. The ranks toward each group are rows this wide, so
    a group's [`front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank] never holds more members
    than this. Compare the two to show how many places are left in a group's front rank."""
    party_free_round: bool = False
    """True in the round the party gets free after it surprised the monsters, while the monsters can't
    act. False in every other round, and outside a battle.

    It's the round in which a thief's back-stab is legal: an `attack` that sets
    [`BattleDeclaration.back_stab`][osrlib.crawl.commands.BattleDeclaration.back_stab]. It's true while
    [`BattleState.monsters_hold_rounds`][osrlib.crawl.battle.BattleState.monsters_hold_rounds] is above
    zero."""


class ObjectiveView(BaseModel):
    """One revealed objective as the players know it: what it's called, and whether it's done.

    You get these from [`QuestView.objectives`][osrlib.crawl.views.QuestView.objectives]. An
    objective the players haven't been told about is absent from the list, not listed as
    unknown, so `state` has only the two values a revealed objective can have. The adventure
    defines the objective in an [`ObjectiveSpec`][osrlib.crawl.quests.ObjectiveSpec], and the
    session tracks it in an [`ObjectiveState`][osrlib.crawl.session.ObjectiveState]. A remote
    client has neither.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The objective's id, unique within its quest."""
    name: str
    """The objective's display name: its `name` in the adventure, or its id when the adventure
    gives it no name. It's never empty."""
    state: str
    """`"incomplete"` or `"complete"`."""


class QuestView(BaseModel):
    """One active quest as the players know it: the task, who gave it, and where it stands.

    You get these from [`PlayerView.quests`][osrlib.crawl.views.PlayerView.quests]. The rules
    that start a quest, complete it, and reward it never appear, because they're hidden from
    the players the same way a trigger's rules are. When you have the session, read the quest's
    definition from [`QuestSpec`][osrlib.crawl.quests.QuestSpec] and its state from
    [`QuestState`][osrlib.crawl.session.QuestState].
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The quest's id in the adventure."""
    name: str
    """The quest's display name."""
    narrative: str
    """The adventure's text for offering the quest, its narrative's `offer`, or the empty
    string when it has none. The view includes the text because a remote client has no
    adventure document to look it up in."""
    speaker: str
    """Who speaks the offer, like `"Sister Halda"`, or the empty string when the adventure gives
    no speaker."""
    objectives: tuple[ObjectiveView, ...]
    """The revealed objectives, in the order the quest lists them."""


class PlayerView(BaseModel):
    """What a player may see of a session: a fixed list of fields, every one safe to show.

    Get one from [`GameSession.view`][osrlib.crawl.session.GameSession.view] with
    `Visibility.PLAYER`, or from [`build_player_view`][osrlib.crawl.views.build_player_view].
    In a networked game, send this object to a player's client. Every field is safe to show at
    the table, and it contains only what the party has learned. Get a new one after every
    command, because a view is a frozen snapshot that doesn't change.
    """

    model_config = ConfigDict(frozen=True)

    adventure_name: str
    """The adventure's title."""
    adventure_description: str
    """The adventure's description, for a title screen."""
    town_name: str
    """The base town's name."""
    town_description: str
    """The base town's description."""
    town_services: tuple[str, ...]
    """The services the town offers, as text from the adventure for you to list."""
    party: tuple[MemberView, ...]
    """The party's public sheets, in marching order, the dead included."""
    location: PartyLocation
    """Where the party is: the town, or a dungeon cell with its facing."""
    clock_rounds: int
    """How much game time has passed since the session began, in rounds."""
    mode: str
    """The session mode as a [`SessionMode`][osrlib.crawl.commands.SessionMode] serialized
    value: `"town"`, `"exploring"`, `"encounter"`, `"battle"`, `"game_over"`, or
    `"victory"`. Read it to decide which commands to offer, because each mode
    accepts different commands."""
    explored: tuple[ExploredLevelView, ...]
    """The party's map, one entry per level it knows anything about."""
    piles: dict[str, PileView]
    """The dropped piles the party knows about, keyed by the cell reference
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref] returns. A pile in a cell the party has
    not walked is left out."""
    emptied_caches: tuple[str, ...]
    """The treasure caches the party has already emptied, as `"{dungeon}:{level}:{id}"`
    references, so you can draw a looted cache as looted."""
    effects: tuple[MemberEffectView, ...]
    """The active effects on party members, in ledger order."""
    fatigued: bool
    """Whether any member is fatigued."""
    exhausted: bool
    """Whether any member is exhausted."""
    deprivation: dict[str, dict[str, int]]
    """Food and water deprivation, keyed by character id, each value containing
    `food_days` and `water_days`. A member with no deprivation on either track is left
    out, so an empty dict means the party has been eating and drinking."""
    journal: tuple[JournalEntry, ...]
    """The session journal, the players' record of the adventure, in the order the entries were
    written. Each entry has the clock time when it was written. The record of which triggers
    fired, and the referee's notes, stay out."""
    quests: tuple[QuestView, ...]
    """The active quests, in the order the adventure lists them. A quest the party hasn't
    taken on yet is left out. A finished quest leaves the list, and the journal still contains
    every entry it added."""
    encounter: EncounterView | None = None
    """The current encounter or battle's public state, or `None` when the party is not in
    one."""


class ExplorationCounters(BaseModel):
    """The exploration counters a session keeps between commands: distance, rest, wandering, noise, sleep, food.

    You get one from [`RefereeView.exploration`][osrlib.crawl.views.RefereeView.exploration]. These
    are the counters [`GameSession`][osrlib.crawl.session.GameSession] keeps as attributes, under the
    same names, and a save contains them under its `exploration` key. They're for the referee, and none
    of them reaches [`PlayerView`][osrlib.crawl.views.PlayerView], which reports the party's fatigue,
    exhaustion, and deprivation as yes-or-no status instead.
    """

    model_config = ConfigDict(frozen=True)

    odometer_thirds: int
    """How far the party has walked during the current turn, in thirds of a foot. When it reaches three
    times the party's exploration rate, the clock advances one turn and this resets to zero."""
    turns_since_rest: int
    """Turns since the party last rested. The fatigue rule counts these, and a
    [`Rest`][osrlib.crawl.commands.Rest] resets them."""
    wandering_counter: int
    """Turns since the last wandering-monster check. Reaching the level's interval fires the check and
    resets this to zero."""
    noise_since_check: bool
    """Whether the party has made noise since the last wandering check. Every attempt to force a door
    makes noise, whether or not the door opens. Noise raises the next check's chance by 1 in 6, and
    the check clears it."""
    sleep_count: int
    """How many nights or days the party has slept through. Preparing spells needs a sleep the caster
    has not already prepared from."""
    last_prepared_sleep: dict[str, int]
    """The `sleep_count` at which each caster last prepared spells, keyed by character id. The session
    uses it to allow one preparation per sleep."""
    alerted_areas: tuple[str, ...]
    """The keyed areas whose monsters have been alerted, as `"{dungeon}:{level}:{area}"` references.
    Monsters that heard the party coming are not surprised when it walks in."""
    heard_areas: tuple[str, ...]
    """The keyed areas the party has heard something in, as `"{dungeon}:{level}:{area}"` references.
    A party that knows what stands behind the door is not surprised by it."""
    provisions_day: int
    """How many whole game days of food and water the session has charged the party for, counting from
    the start of the session. Each time the clock passes into a new day, the session charges the
    party for the day and adds one to this."""


class RefereeView(BaseModel):
    """The full state of a session except the random streams, one typed field for each group of data in a save.

    Get one from [`GameSession.view`][osrlib.crawl.session.GameSession.view] with
    `Visibility.REFEREE`, or from [`build_referee_view`][osrlib.crawl.views.build_referee_view]. Use
    it where players can't see it: as the context an LLM referee reasons over, in a debugging panel,
    or in a test that checks state a player may not see. Never send it to a player's client, and draw
    nothing a player sees from it. [`PlayerView`][osrlib.crawl.views.PlayerView] is for that.

    Each field is the session's model rather than a dict, so you read
    `view.monsters[0].current_hp` and `view.flags["key"]` with the types this reference documents, and
    each field's docstring links the model to read next. The fields are the groups
    [`session_state`][osrlib.persistence.session_state] writes, minus the master seed and the RNG stream
    positions, so `view.model_dump(mode="json")` is that save payload without those two keys. The seed
    and the stream positions live only in the save, because knowing them would let a player predict
    every roll to come.

    The view is a snapshot of the moment it was built. The session's mutable state is copied into it,
    so later play leaves the view as it was, and editing what you find on a view changes nothing on
    the session. Being frozen fixes the view's fields rather than their contents, so
    rebinding `view.flags` raises while `view.flags["key"] = 1` and `view.monsters[0].current_hp = 0`
    edit the view's copies and are allowed. Build a fresh view after each command rather than
    editing one. The command log, the event log, and the journal are shared with the session instead of
    copied, because a command, an event, and a journal entry are frozen records of something that has
    already happened.
    """

    model_config = ConfigDict(frozen=True)

    ruleset: Ruleset
    """The options this session plays under, as a [`Ruleset`][osrlib.core.ruleset.Ruleset], whose
    flags turn the optional rules on and off."""
    party: Party
    """The party, as a [`Party`][osrlib.crawl.party.Party] of full
    [`Character`][osrlib.core.character.Character] sheets in marching order, the dead included. Every
    number is here, the spell book and the true names of magic items among them, so render a player's
    sheet from [`PlayerView.party`][osrlib.crawl.views.PlayerView.party] instead, which masks what
    the party has not identified."""
    adventure: Adventure
    """The whole adventure document, as an [`Adventure`][osrlib.crawl.adventure.Adventure]: the town, the
    dungeons with their complete geometry and keyed areas, the triggers, and the quests. It is the map
    with nothing hidden, so draw the party's map from
    [`PlayerView.explored`][osrlib.crawl.views.PlayerView.explored] instead. The document is frozen and
    the view copies it even so, because the tree under it contains dicts you can edit in place, the
    town's travel turns and each level's edges among them."""
    mode: SessionMode
    """The [`SessionMode`][osrlib.crawl.commands.SessionMode] the session is in. The mode sets which
    commands the session accepts right now."""
    clock_rounds: int
    """How much game time has passed since the session began, in rounds."""
    allocator: IdAllocator
    """The source of session ids, as an [`IdAllocator`][osrlib.core.monsters.IdAllocator]. It hands out
    each `<kind>-NNNN` id by counting up, so two runs of the same commands assign the same ids."""
    ledger: EffectsLedger
    """The active effects, as an [`EffectsLedger`][osrlib.core.effects.EffectsLedger]: spells running,
    conditions, a torch burning down, each with the round it expires at. It includes the effects
    attached to dungeon cells as well as the ones on members, and the true duration of a potion's
    effect, which the players aren't allowed to know."""
    dungeon_state: DungeonState
    """What the session has recorded on top of the adventure's map during play, as a
    [`DungeonState`][osrlib.crawl.dungeon.DungeonState]: where the party stands, the cells it has walked
    and seen, door state, found and sprung traps, drop piles, and generated caches."""
    monsters: tuple[MonsterInstance, ...]
    """Every monster spawned this session, as
    [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] values in the order they were spawned,
    the defeated ones included, so a later event can still refer to them. Their hit points and other
    statistics are here. The players see monsters only as
    [`EncounterGroupView`][osrlib.crawl.views.EncounterGroupView] values."""
    npcs: tuple[Character, ...]
    """The NPC adventurers in play, as [`Character`][osrlib.core.character.Character] sheets in the order
    they joined. They are characters rather than monsters, and they fight by the same rules as the party."""
    flags: dict[str, str | int | bool]
    """The session flags: the key-value store that [`SetFlag`][osrlib.crawl.commands.SetFlag] writes and
    an adventure's triggers, quests, and door and exit conditions read. Flags are part of the
    adventure's hidden rules, so no flag ever reaches a player view."""
    fired_triggers: tuple[str, ...]
    """The ids of the triggers that have fired, in the order they first fired. Use it to check whether a
    trigger has fired before. It's for the referee only. The players learn what a trigger did from the
    journal entry it added, if any."""
    journal: tuple[JournalEntry, ...]
    """The journal, as [`JournalEntry`][osrlib.crawl.session.JournalEntry] values in the order they were
    written. The players see the same list, and you read it for them from
    [`PlayerView.journal`][osrlib.crawl.views.PlayerView.journal]."""
    quests: dict[str, QuestState]
    """The state of every quest in the adventure, as [`QuestState`][osrlib.crawl.session.QuestState]
    values keyed by quest id, in the order the adventure lists them. It includes the inactive and
    completed quests as well as the active ones, and every objective, revealed or hidden. The players'
    version is [`PlayerView.quests`][osrlib.crawl.views.PlayerView.quests]."""
    listener_state: dict[str, dict]
    """Each registered listener's state, keyed by the listener's `key`, as that listener's `handle`
    returned it. The session stores it and never reads it, so the listener decides what it means."""
    death_records: dict[str, DeathRecord]
    """When and how each dead party member died, as [`DeathRecord`][osrlib.crawl.session.DeathRecord]
    values keyed by character id. When the engine resolves *raise dead*, it reads the day count from
    here, and for *neutralize poison*, the round window."""
    defeated_monsters: tuple[DefeatedMonsterRecord, ...]
    """The creatures defeated since the last experience award, as
    [`DefeatedMonsterRecord`][osrlib.crawl.session.DefeatedMonsterRecord] values in the order they fell.
    [`GameSession.award_adventure_xp`][osrlib.crawl.session.GameSession.award_adventure_xp] adds up their
    `xp` and clears the list."""
    deprivation: dict[str, DeprivationState]
    """The food and water counts, as [`DeprivationState`][osrlib.crawl.session.DeprivationState] values
    keyed by character id. There's one for each member the session has charged for food and water at
    the start of a new day, including members whose counts are zero.
    [`PlayerView.deprivation`][osrlib.crawl.views.PlayerView.deprivation] reports the same counts, but
    only for members whose counts aren't zero."""
    treasure_snapshot_cp: int | None
    """What the party's treasure was worth in copper pieces when it left town, or `None` when no delve
    is under way. The adventure award pays for the difference between this and what comes back."""
    exploration: ExplorationCounters
    """The exploration counters, as [`ExplorationCounters`][osrlib.crawl.views.ExplorationCounters]:
    distance walked, turns since a rest, turns since a wandering check, noise, sleep, and food."""
    encounter: EncounterState | None
    """The encounter under way, as an [`EncounterState`][osrlib.crawl.encounter.EncounterState], or
    `None` when nothing is happening. It contains each group's monster ids, its distance, the stance the
    reaction roll settled, and any chase in progress. The players' reading of the same encounter is
    [`PlayerView.encounter`][osrlib.crawl.views.PlayerView.encounter]."""
    battle: BattleState | None
    """The battle under way, as a [`BattleState`][osrlib.crawl.battle.BattleState], or `None` outside
    one. It contains the round number and the state that persists from round to round,
    including who fired a weapon that needs reloading last round."""
    command_log: tuple[SerializeAsAny[Command], ...]
    """Every accepted command, in order, each one the [`Command`][osrlib.crawl.commands.Command]
    subclass it was issued as, so its fields are there to read. Refused commands are absent, because
    they changed nothing, and [`replay_game`][osrlib.persistence.replay_game] re-executes this list from
    the master seed to rebuild the session."""
    event_log: tuple[SerializeAsAny[Event] | dict, ...]
    """Everything that has happened, in order, each entry the [`Event`][osrlib.core.events.Event]
    subclass that was emitted, including the referee-visibility events a player never sees. An entry
    restored from a save whose event type this library has no class for stays the raw mapping it
    arrived as, so check for a `dict` before reading an entry's attributes."""


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
    magic* exists. A display string built from that base weapon already includes the weapon's name,
    so the unidentified item also shows the `qualities` of the mundane weapon underneath
    it, exactly as an identified one does, and its `missile_ranges` when the rules give that
    weapon ranges. A weapon with no printed ranges, a sword among them, gets the `qualities` key
    and no `missile_ranges` key at all. A staff, wand, or other item whose display comes from
    its category instead shows neither field, even when it resolves to a weapon, because the
    display never included that weapon's name and the fields would single the item out among the
    items that show the same string. A `staff_of_striking` reads "a staff", and so does every
    other staff.

    Both fields are rulebook facts about the weapon the display string already includes,
    not facts about the enchantment, and they let you tell a melee declaration from a missile
    one. Without them an enchanted dagger is unclassifiable where a plain dagger is not. An
    identified item shows its true name, its id, and whether a curse has been revealed, and it
    shows the weapon facts whenever a weapon underlies it, because the item's true name is
    already shown. The bonus, the curse, the template id, and the name stay hidden until
    identified, and charges, sentience, and per-item state never appear at any identification
    level, because by the rules as written charges are undiscoverable.
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
        else:
            # The display came from the category table, not the base weapon, so
            # attaching weapon facts here would single this one item out among the items in its
            # category that show the same string.
            facet = None
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
    tells the player how long it will last, so this function always returns `None` for an effect
    from a potion.
    """
    if effect.definition.params.get("item_source") == "potion":
        return None
    if effect.expires_round is None:
        return None
    return max(0, effect.expires_round - session.clock.rounds)


def build_player_view(session) -> PlayerView:
    """Build the player view of a session from its current state, never from the event log.

    [`GameSession.view`][osrlib.crawl.session.GameSession.view] with `Visibility.PLAYER` calls
    this function and returns the same view, so you usually call that method instead. Build a
    view after each accepted command, and render it. Send the view, never the session, to a
    client you don't control.

    The call reads the session and changes nothing, so building a view twice gives two equal
    snapshots and costs the party no game time.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        A frozen [`PlayerView`][osrlib.crawl.views.PlayerView] that contains only what the party
            has learned.

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
        assert "flags" not in player.model_dump()  # a player view never includes the session flags
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

    The walk is over the authored specs rather than the state block, so the order in the view
    is the order in the adventure, and a quest missing from the block is absent
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
        # light has shown it (map memory, in `DungeonState.seen`), plus what its
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
        # A passage's side is a wall in the level's edges, but the party walks through it, so
        # the view reports it as open.
        passage_edges = {
            _canonical_edge(transition.position, transition.side)
            for transition in level.transitions
            if transition.kind == "passage" and transition.side is not None
        }
        edges: dict[str, EdgeView] = {}
        for cell in visible:
            for direction in Direction:
                key_text = _canonical_edge(cell, direction)
                if key_text in edges:
                    continue
                if key_text in passage_edges:
                    edges[key_text] = EdgeView(kind="open")
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
                fled=group.fled,
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
    """Build the referee view: every group of data in a save, typed, except the seed and the random streams.

    Use it for the context an LLM referee reasons over, for a debugging panel, or for a test that
    checks state a player may not see.
    [`GameSession.view`][osrlib.crawl.session.GameSession.view] with `Visibility.REFEREE` calls this
    function and returns the same view, so you usually call that method instead. Never send the
    result to a player's client. [`build_player_view`][osrlib.crawl.views.build_player_view] is for
    that. To store a session rather than read it, call
    [`save_game`][osrlib.persistence.save_game], which includes the seed and the stream positions a
    restored game needs.

    The call reads the session and changes nothing. It returns a snapshot, not a live window onto the
    session. The session's mutable models are copied into it, so later play leaves the view as it
    was, and editing what you find on the view changes nothing on the session.
    A frozen model whose containers cannot be edited goes in as it is, which covers the ruleset,
    the commands, the events, the journal entries, and the death and defeat records. The adventure is
    frozen as well and is copied anyway, because the authored tree contains dicts you can edit in
    place, the town's travel turns and each level's edges among them.

    Args:
        session (osrlib.crawl.session.GameSession): The running session.

    Returns:
        A frozen [`RefereeView`][osrlib.crawl.views.RefereeView] of the whole session, except the
            master seed and the random streams' positions.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.commands import SetFlag
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
        session.execute(SetFlag(key="gate_raised", value=True))

        referee = build_referee_view(session)
        print(referee.mode, referee.clock_rounds)
        # town 0
        print(referee.party.members[0].id, referee.party.members[0].current_hp)
        # character-0001 3
        print(referee.flags)  # flags never reach a player view
        # {'gate_raised': True}
        ```
    """
    return RefereeView(
        ruleset=session.ruleset,
        party=session.party.model_copy(deep=True),
        adventure=session.adventure.model_copy(deep=True),
        mode=session.mode,
        clock_rounds=session.clock.rounds,
        allocator=session.allocator.model_copy(deep=True),
        ledger=session.ledger.model_copy(deep=True),
        dungeon_state=session.dungeon_state.model_copy(deep=True),
        monsters=tuple(instance.model_copy(deep=True) for instance in session.monsters.values()),
        npcs=tuple(npc.model_copy(deep=True) for npc in session.npcs.values()),
        flags=dict(session.flags),
        fired_triggers=tuple(session.fired_triggers),
        journal=tuple(session.journal),
        quests={quest_id: state.model_copy(deep=True) for quest_id, state in session.quests.items()},
        listener_state={key: deepcopy(value) for key, value in session.listener_state.items()},
        death_records=dict(session.death_records),
        defeated_monsters=tuple(session.defeated_monsters),
        deprivation={key: state.model_copy(deep=True) for key, state in session.deprivation.items()},
        treasure_snapshot_cp=session.treasure_snapshot_cp,
        exploration=ExplorationCounters(
            odometer_thirds=session.odometer_thirds,
            turns_since_rest=session.turns_since_rest,
            wandering_counter=session.wandering_counter,
            noise_since_check=session.noise_since_check,
            sleep_count=session.sleep_count,
            last_prepared_sleep=dict(session.last_prepared_sleep),
            alerted_areas=tuple(session.alerted_areas),
            heard_areas=tuple(session.heard_areas),
            provisions_day=session._provisions_day,
        ),
        encounter=session.encounter.model_copy(deep=True) if session.encounter is not None else None,
        battle=session.battle.model_copy(deep=True) if session.battle is not None else None,
        command_log=tuple(session.command_log),
        event_log=tuple(deepcopy(entry) if isinstance(entry, dict) else entry for entry in session.event_log),
    )
