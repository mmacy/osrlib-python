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
view includes the log as one of its fields. Commands in
[`osrlib.crawl.commands`][osrlib.crawl.commands] take the ids from
[`MemberView.id`][osrlib.crawl.views.MemberView.id],
[`EncounterGroupView.id`][osrlib.crawl.views.EncounterGroupView.id], and
[`EncounterMemberView.id`][osrlib.crawl.views.EncounterMemberView.id].

The player view has a fixed list of fields, each one safe to show a player. It includes:

- each party member's character sheet, with every number on a B/X character sheet, and the
  party's location and facing
- the mapped cells and their edges: the cells the party walked, the cells its light has
  shown it, and what its light shows right now, with an undiscovered secret door shown
  as wall
- the dropped piles on cells the party has walked, and the caches it has emptied
- entered dungeons and areas, with only the area cells the party has walked
- known custom features, caches, transitions, and the entrance, plus the safe feature handles
  treasure commands accept
- the active effects on party members, with their remaining durations
- the elapsed clock and the session mode
- the journal, and the active quests with their revealed objectives
- the public state of the current encounter or battle
- fatigue, exhaustion, and deprivation status
- how long the party's light and food will last
- the adventure's and the town's public prose

It never includes:

- geometry the party hasn't explored, or undiscovered traps and secret doors
- monster hit points or stat internals
- the outcomes of referee-visibility rolls
- session flags, trigger fired-marks, or referee notes
- the logic of a quest (its activation clauses, patterns, conditions, and rewards), its
  hidden objectives, or any quest that isn't active, whether not yet taken on or already
  finished
- the RNG state, the master seed, or the player-handle key, which only the save contains

The referee view includes everything else the save does, except the random streams'
positions, the master seed, and the player-handle key. Use it for an LLM referee, a debugging panel, or a test. Its
fields are the groups [`session_state`][osrlib.persistence.session_state] writes, so
`view.monsters[0].current_hp` and `view.flags["key"]` have the types this reference
documents, and `view.model_dump(mode="json")` is that save data without those three keys, plus
`feature_handles`, each feature's player handle, which the session derives from the key.
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
print([member.model_dump(mode="json") for member in group.members])
# [{'id': 'monster-0001', 'status': 'alive'}, {'id': 'monster-0002', 'status': 'alive'}]

# The referee sees each monster's hit points.
referee = session.view(Visibility.REFEREE)
print([monster.current_hp for monster in referee.monsters])
# [1, 3]
```
"""

from collections.abc import Iterable
from copy import deepcopy
from typing import Literal

from pydantic import ConfigDict, Field, SerializeAsAny, ValidationInfo, field_validator

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character, _armour_as_tested
from osrlib.core.classes import level_title
from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, EffectsLedger, has_condition
from osrlib.core.events import Event
from osrlib.core.frozen import _Model
from osrlib.core.items import (
    _MASKED_CATEGORY_NAMES,
    Coins,
    MagicItemCategory,
    MagicItemInstance,
    ValuableInstance,
    _masked_names,
    _with_article,
    magic_item_template,
)
from osrlib.core.monsters import IdAllocator, MonsterInstance, _regular_plural
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import _reverses_at, caster_profile
from osrlib.core.treasure import _ROLLED_VALUABLE_NAMES
from osrlib.core.validation import Rejection
from osrlib.crawl._feature_handles import (
    _authored_feature_id,
    _cache_available,
    _current_cache_handles,
    _generated_feature_id,
    _parse_cell_location,
    _session_feature_handles,
)
from osrlib.crawl._light import _burn_rounds, _light_carry_rejection, _light_reveal, _party_open_flame
from osrlib.crawl._party_location import _can_travel_to_town
from osrlib.crawl._spells import _cast_context
from osrlib.crawl._town import _temple_funds_gp, _temple_modes, _temple_offer, _temple_spell
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.battle import BattleState
from osrlib.crawl.commands import Command, Evade, PurchaseHealing, SessionMode
from osrlib.crawl.dungeon import (
    Direction,
    DropPile,
    DungeonState,
    EdgeKind,
    PartyLocation,
    Position,
    cell_ref,
    edge_ref,
)
from osrlib.crawl.encounter import _EVADE_DROPS, EncounterState, _evade_rejections, _member_kind
from osrlib.crawl.exploration import EXHAUSTED_KIND, FATIGUE_KIND, HEALING_SERVICES
from osrlib.crawl.party import Party
from osrlib.crawl.session import (
    LIGHT_EFFECT_KINDS,
    DeathRecord,
    DefeatedMonsterRecord,
    DeprivationState,
    JournalEntry,
    QuestState,
)

__all__ = [
    "DungeonNameView",
    "EdgeView",
    "EnteredAreaView",
    "EncounterGroupView",
    "EncounterMemberView",
    "EncounterView",
    "ExplorationCounters",
    "ExploredLevelView",
    "MapFeatureView",
    "MemberEffectView",
    "MemberView",
    "ObjectiveView",
    "PileItemView",
    "PileView",
    "PlayerView",
    "QuestView",
    "RefereeView",
    "SuppliesView",
    "TransitionView",
    "build_player_view",
    "build_referee_view",
]


class MemberView(_Model):
    """One party member's character sheet, as the players see it.

    You get these from [`PlayerView.party`][osrlib.crawl.views.PlayerView.party], in marching
    order, and every party member has one, living or dead. The players know everything about
    their characters, so the view has every number on a B/X character sheet, and a client can draw
    the sheet from it alone. What the view keeps back is what the party hasn't learned about a magic
    item yet: an unidentified item's name, its bonus, and its curse.

    The numbers are the member's current ones, read from the live
    [`Character`][osrlib.core.character.Character]. They count what the member wears right now, like
    armour, a shield, or a ring of protection, whether or not the party has identified it, because a
    B/X magic item works when it's worn. So an unidentified item can change a number while
    `inventory` still masks its name. They don't count a timed effect from a spell or a potion, like
    the *shield* spell. [`PlayerView.effects`][osrlib.crawl.views.PlayerView.effects] lists the timed
    effects on each member.

    Cursed armour and shields are the one exception, because the OSE SRD says "All types of tests
    indicate that a cursed shield or suit of armour is enchanted with a +1 bonus. It is only when the
    armour is used in deadly combat that the curse is revealed." Until the session reveals the curse,
    `armour_class` and `armour_class_ascending` count a worn cursed shield or suit of armour as the +1
    item it tests as, while the monsters roll against the real armour class. `armour_class` says
    when the real number appears.

    A STR that an item sets counts too. A member who wears gauntlets of ogre power has the numbers
    for STR 18. Six rounds after a member puts on a ring of weakness, the ring's curse takes hold,
    and the member has the numbers for STR 3. Either way, `effective_strength`, `melee_modifier`,
    and `open_doors_chance` change, and `scores` still shows the rolled STR.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The member's character id, like `"character-0001"`. It's the id you put in the
    `character_id` of a command like [`GrantItem`][osrlib.crawl.commands.GrantItem] or a battle
    declaration, so a remote client can issue commands without having the session."""
    name: str
    """The character's name, as the player wrote it."""
    class_id: str
    """The character class id, like `"fighter"`, as in
    [`Character.class_id`][osrlib.core.character.Character.class_id]. For a class your game wrote, it's
    the id of the character's [`custom_class`][osrlib.core.character.Character.custom_class]. To show
    the class, use `class_name`."""
    class_name: str = ""
    """The character class's name, like `"Fighter"`, read from
    [`Character.definition`][osrlib.core.character.Character.definition], so it's right for a custom
    class too."""
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

    A magic item is masked until it's identified. An unidentified one has a `display` name in place of its true
    name, like `"a potion"`, or `"a dagger with a faint aura"` for a magic weapon. An enchanted weapon, suit of
    armour, or shield is named after one unit of the mundane item underneath it, its
    [`display_name`][osrlib.core.items.WeaponTemplate.display_name] with an article, so enchanted arrows display as
    `"an arrow with a faint aura"` and enchanted chainmail as `"a suit of chainmail with a faint aura"`. A suit of
    enchanted armour with no mundane item recorded displays as `"a suit of armour with a faint aura"`. When the
    display name refers to a weapon, the entry also has the `qualities` of that plain weapon, as an identified one
    does, so you can tell a melee attack from a missile attack without revealing the weapon's bonus or curse. It has
    a `missile_ranges` key only when the rules give the weapon ranges, so on a sword the key is absent rather than
    empty. Every magic staff displays as `"a staff"` and has the staff's `qualities`. Any other item whose display
    name comes from its category, like `"a wand"`, has neither key, because its display name doesn't refer to a
    weapon. An identified item shows its `name`, its `template_id`, and whether a curse has been revealed. Charges
    never appear.

    To name an item in a sentence, look up the id it has here in
    [`PlayerView.item_names`][osrlib.crawl.views.PlayerView.item_names]: a mundane item's
    `template.id`, and a magic item's or a valuable's `instance_id`."""
    memorized_spells: tuple[dict, ...]
    """The prepared spells, one dumped
    [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] per copy, in memorization
    order. Empty for a class that casts nothing. To show a copy's name, look its `spell_id` up in
    [`PlayerView.spell_names`][osrlib.crawl.views.PlayerView.spell_names], or in
    [`PlayerView.reversed_spell_names`][osrlib.crawl.views.PlayerView.reversed_spell_names] for a copy
    whose `reversed` is true."""
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
    race: str = ""
    """The character's race id, like `"human"` or `"dwarf"`, as in
    [`Character.race`][osrlib.core.character.Character.race]."""
    alignment: str = ""
    """The character's alignment, as an [`Alignment`][osrlib.core.alignment.Alignment] serialized
    value: `"lawful"`, `"neutral"`, or `"chaotic"`."""
    title: str | None = None
    """The level title, like `"Veteran"` for a 1st-level fighter, as
    [`level_title`][osrlib.core.classes.level_title] returns it. `None` past the last title the
    class has, so show the class name instead."""
    xp: int = 0
    """The experience points the character has."""
    xp_next_level: int | None = None
    """The experience points the next level needs, or `None` when the character has reached the
    highest level of the class."""
    scores: dict[str, int] = {}
    """The six ability scores, keyed by [`AbilityScore`][osrlib.core.abilities.AbilityScore]
    serialized value: `"str"`, `"int"`, `"wis"`, `"dex"`, `"con"`, and `"cha"`.

    They're the scores the character rolled, as in
    [`Character.scores`][osrlib.core.character.Character.scores]. A STR that an item sets, like
    the 18 of gauntlets of ogre power, changes `effective_strength` but not `"str"`."""
    effective_strength: int = 0
    """The STR score the rules use during play, as
    [`Character.effective_strength`][osrlib.core.character.Character.effective_strength] gives it.
    `melee_modifier` and `open_doors_chance` come from it.

    It's `scores["str"]` unless an item sets the member's STR. So a fighter who rolled STR 11 and
    wears gauntlets of ogre power has 18 here and 11 in `scores`. To show a player what the item
    did, show both numbers. Like the armour class, it counts the gauntlets before the party
    identifies them, while `inventory` still shows them as `"a curious device"`."""
    melee_modifier: int = 0
    """The Strength modifier to melee attack rolls and melee damage, as
    [`Character.melee_modifier`][osrlib.core.character.Character.melee_modifier]. It's the
    modifier combat adds.

    It comes from `effective_strength`, not from `scores["str"]`. So a fighter with STR 11 who
    wears gauntlets of ogre power has +3 here."""
    missile_modifier: int = 0
    """The Dexterity modifier to missile attack rolls, as
    [`Character.missile_modifier`][osrlib.core.character.Character.missile_modifier]."""
    initiative_modifier: int = 0
    """The Dexterity modifier to the member's initiative roll, as
    [`Character.initiative_modifier`][osrlib.core.character.Character.initiative_modifier]. The
    session uses it only under individual initiative."""
    hit_point_modifier: int = 0
    """The Constitution modifier to each Hit Die rolled, as
    [`Character.hit_point_modifier`][osrlib.core.character.Character.hit_point_modifier]."""
    magic_save_modifier: int = 0
    """The Wisdom modifier to saving throws against magic, as
    [`Character.magic_save_modifier`][osrlib.core.character.Character.magic_save_modifier]."""
    npc_reaction_modifier: int = 0
    """The Charisma modifier to reaction rolls, as
    [`Character.npc_reaction_modifier`][osrlib.core.character.Character.npc_reaction_modifier]."""
    open_doors_chance: int = 0
    """The chance in 6 of forcing a stuck door open, as
    [`Character.open_doors_chance`][osrlib.core.character.Character.open_doors_chance]. It's the
    chance the session rolls against for a [`ForceDoor`][osrlib.crawl.commands.ForceDoor], and it
    comes from `effective_strength`."""
    thac0: int = 0
    """The d20 roll the member needs to hit armour class 0, as
    [`Character.thac0`][osrlib.core.character.Character.thac0]. Show it when your game uses
    descending armour class."""
    attack_bonus: int = 0
    """The bonus the member adds to an attack roll against ascending armour class, as
    [`Character.attack_bonus`][osrlib.core.character.Character.attack_bonus]."""
    armour_class: int = 0
    """The member's descending armour class, where lower is better, as the players know it.

    It counts everything the member wears, unidentified magic items included, because a magic item
    works when it's worn. So it can show that a shield is better than a plain one while `inventory`
    still calls it `"a shield with a faint aura"`. It's the number in
    [`Character.armour_class`][osrlib.core.character.Character.armour_class], the one the monsters
    roll against, with one exception.

    The exception is a worn cursed shield or suit of armour that the party hasn't identified. The
    OSE SRD says every test shows such an item as enchanted with a +1 bonus, so until the curse is
    revealed, this number counts it as its base item with a +1 enchantment, and as setting no armour
    class of its own. An unarmoured fighter with AC 9 reads 7 with an unidentified cursed shield -2,
    as with a shield +1, while the monsters roll against the real 10. In cursed chainmail -2 the same
    fighter reads 4, as in chainmail +1, while the real armour class is 7. Everything else the member
    wears, and the DEX modifier, count as usual.

    The session reveals the curse and identifies the item at the same moment: the first time the
    member is attacked in a battle, or when a referee sends
    [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem]. From then on this is the real number. It
    stays the real number after *remove curse* frees the member, because the item stays
    identified."""
    armour_class_ascending: int = 0
    """The member's ascending armour class, where higher is better, as the players know it. It
    describes the same defense as `armour_class` and counts the same items the same way, so it's
    [`Character.armour_class_ascending`][osrlib.core.character.Character.armour_class_ascending]
    except while the member wears a cursed shield or suit of armour that the party hasn't
    identified. Then it counts that item as its base item with a +1 enchantment, so the fighter
    with AC 9 [10] and an unidentified cursed shield -2 reads 12 here while the monsters roll
    against the real 9."""
    saves: dict[str, int] = {}
    """The five saving throws, keyed by the field names of
    [`SavingThrows`][osrlib.core.classes.SavingThrows]: `"death"`, `"wands"`, `"paralysis"`, `"breath"`,
    and `"spells"`. Each value is the d20 roll the member needs, as
    [`Character.saves`][osrlib.core.character.Character.saves] gives it."""
    movement_rate_feet: int = 0
    """How far the member moves in one exploration turn, in feet, as
    [`Character.movement_rate`][osrlib.core.character.Character.movement_rate] returns it under the
    session's ruleset. The party moves at the rate of its slowest living member."""
    languages: tuple[str, ...] = ()
    """The ids of the languages the member speaks, as
    [`Character.languages`][osrlib.core.character.Character.languages] lists them."""
    spell_book: tuple[str, ...] = ()
    """The ids of the spells in the member's spell book, as in
    [`Character.spell_book`][osrlib.core.character.Character.spell_book], in the order they were
    written in. Empty for a class that has no spell book. An arcane caster prepares only from the
    book, and `preparable_spells` lists the spells in it that the member has a slot for. To show a
    spell's name, look its id up in
    [`PlayerView.spell_names`][osrlib.crawl.views.PlayerView.spell_names]."""
    spell_slots: tuple[int, ...] = ()
    """How many spells the member can memorize at each spell level, lowest level first, as in the
    [`spell_slots`][osrlib.core.classes.ProgressionRow.spell_slots] of the class's progression row
    for the member's level. Entry `i` is the slot count at spell level `i + 1`. It comes from
    [`Character.definition`][osrlib.core.character.Character.definition], so it's right for a custom
    class too.

    A [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] fits these counts: at each spell level,
    select no more copies than the count there, and group `preparable_spells` by
    [`PlayerView.spell_levels`][osrlib.crawl.views.PlayerView.spell_levels] to fill them. An arcane
    caster's book has room at a spell level for this count less the spells the book already has
    there. A 1st-level cleric has a count of 0 at every level, and a class that casts nothing has no
    counts."""
    preparable_spells: tuple[str, ...] = ()
    """The ids of the spells the member may select in a
    [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells], in the order of the session's catalog,
    [`GameSession.effective_spells`][osrlib.crawl.session.GameSession.effective_spells]. Build a
    prepare dialog from it.

    For an arcane caster, they're the spells in `spell_book` at the spell levels where the member has
    a slot, so a spell that a lost level left in the book without a slot isn't here. For a divine
    caster, they're the spells on the class's spell list at every spell level where the member has a
    slot, and the spells the adventure bundles for that list are among them. A cleric has no slot
    until 2nd level, so the list is empty for a 1st-level cleric. It's empty for a class that casts
    nothing.

    A spell id is here exactly when a `PrepareSpells` that selects one copy of it in its normal form
    is refused with none of these codes, which belong to the spell and the member:
    `magic.memorize.not_a_caster`, `magic.memorize.unknown_spell`, `magic.memorize.wrong_list`,
    `magic.memorize.not_in_book`, and `magic.memorize.slots_exceeded`. The list doesn't depend on
    the moment, so it's the same before the party has slept (`magic.memorize.needs_sleep`), during an
    encounter or a battle (`session.command.wrong_mode`), and while the member can't act
    (`session.command.member_incapacitated`). The selections as a whole still have to fit
    `spell_slots` at each spell level.

    The form of a copy is up to the caster's kind. An arcane caster may select the reversed form of a
    spell that has an entry in
    [`PlayerView.reversed_spell_names`][osrlib.crawl.views.PlayerView.reversed_spell_names], and the
    session refuses the reversed form of any other spell with `magic.memorize.not_reversible`. A
    divine caster selects the normal form and chooses the form when casting, and the session refuses a
    reversed selection with `magic.memorize.divine_reverses_at_cast`. To show a spell's name, look its
    id up in [`PlayerView.spell_names`][osrlib.crawl.views.PlayerView.spell_names]."""
    learnable_spells: tuple[str, ...] = ()
    """The ids of the spells the member may write into their spell book with
    [`LearnSpell`][osrlib.crawl.commands.LearnSpell], in the order of the session's catalog,
    [`GameSession.effective_spells`][osrlib.crawl.session.GameSession.effective_spells]. Build a learn
    dialog from it.

    They're the spells on the class's spell list that aren't in `spell_book`, at each spell level
    where the book has room. The room at a spell level is the member's count there in `spell_slots`,
    less the spells the book already has at that level. The spells the adventure bundles for that list
    are among them. The list is empty for a divine caster and for a class that casts nothing,
    because neither keeps a spell book. It's also empty while the book has a spell that the session's
    catalog doesn't have, because the session can't tell that spell's level, and so can't tell how
    much room the book has left.

    A spell id is here exactly when a `LearnSpell` for it is refused with none of these codes, which
    belong to the spell and the member: `magic.book.not_arcane`, `magic.book.unknown_spell`,
    `magic.book.wrong_list`, `magic.book.duplicate`, and `magic.book.capacity_exceeded`. The list
    doesn't depend on the moment, so it's the same during an encounter or a battle
    (`session.command.wrong_mode`) and while the member can't act
    (`session.command.member_incapacitated`). Learning one spell can fill the book at that spell's
    level, so read the view again after each `LearnSpell`. To show a spell's name, look its id up in
    [`PlayerView.spell_names`][osrlib.crawl.views.PlayerView.spell_names]."""
    reverses_at: Literal["memorize", "cast"] | None = None
    """When the member chooses the form of a spell that reverses: `"memorize"`, `"cast"`, or `None` for a
    class that casts nothing.

    It comes from the kind of caster the class is, as
    [`caster_profile`][osrlib.core.spells.caster_profile] reads it from
    [`Character.definition`][osrlib.core.character.Character.definition], so it's right for a custom class
    too, whatever its spell list and whether or not the member has a spell book. The OSE SRD says of arcane
    casters that "The normal or reversed form of a spell must be selected when the spell is memorized", and
    of divine casters that they "can cast the reversed version of a spell by speaking the words and
    performing the gestures backwards when it is cast."

    - `"memorize"`, for an arcane caster: a [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] may
      select the reversed form of a spell that has an entry in
      [`PlayerView.reversed_spell_names`][osrlib.crawl.views.PlayerView.reversed_spell_names], and each
      memorized copy is cast in the form its `reversed` in `memorized_spells` gives.
    - `"cast"`, for a divine caster: the session refuses a reversed selection with
      `magic.memorize.divine_reverses_at_cast`, and every memorized copy of a spell that reverses can be cast
      in either form, with the `reversed` of [`CastSpell`][osrlib.crawl.commands.CastSpell] or of a `cast`
      [`BattleDeclaration`][osrlib.crawl.commands.BattleDeclaration].

    So offer a reversed copy in a prepare dialog only when it's `"memorize"`, and offer both forms of a
    memorized copy in a cast dialog only when it's `"cast"`."""
    can_prepare_now: bool = False
    """Whether the session accepts a [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] from the member
    now, with selections that fit `preparable_spells` and `spell_slots`. Read it to offer or withhold a
    prepare dialog.

    It's false exactly when one of the refusals that belong to the moment applies to the member:

    - `session.command.wrong_mode`: the session isn't in town or exploring.
    - `session.command.member_incapacitated`: the member can't act.
    - `magic.memorize.needs_sleep`: the party hasn't slept since the member last prepared, which
      `needs_sleep_to_prepare` gives by itself.

    It's also false for a class that casts nothing, which the session refuses with
    `magic.memorize.not_a_caster`. It doesn't depend on the slots, so it's true for a 1st-level cleric who
    has slept, whose `preparable_spells` is empty and who can prepare only an empty selection.

    The session reads the moment from its own state, never from the event log, so the field is right in a
    session restored from a save. A [`Rest`][osrlib.crawl.commands.Rest], a `PrepareSpells`, an encounter,
    or a condition can change it, so read the view again after each command."""
    needs_sleep_to_prepare: bool = False
    """Whether the member has to sleep before they can prepare spells again. Read it to tell the table which
    casters need a night's rest.

    It's true exactly when the party hasn't slept since the member last prepared, the rule by which the
    session refuses a [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells] with
    `magic.memorize.needs_sleep`. So it's true for every caster in a new session, before the party's first
    sleep, and for a caster who has prepared, until the party sleeps again. A `night` or `day`
    [`Rest`][osrlib.crawl.commands.Rest] that a wandering encounter doesn't interrupt is sleep. The OSE SRD
    says "A spell caster can memorize new spells once a day, after an uninterrupted night’s sleep."

    It reads the party's sleeps and the member's last preparation alone. During an encounter or a battle,
    and while the member can't act, the session refuses the command with `session.command.wrong_mode` or
    `session.command.member_incapacitated` before it checks the sleep, and this field still says whether the
    member will need to sleep. It's false for a class that casts nothing, which the session refuses with
    `magic.memorize.not_a_caster` whether or not the party has slept."""
    thief_skills: dict[str, int] = {}
    """The member's chance at each thief skill at their level, keyed by the skill field names of
    [`ThiefSkillRow`][osrlib.core.classes.ThiefSkillRow], like `"open_locks"` and `"move_silently"`.
    `"hear_noise"` is a chance in 6, and every other skill is a percentage. Empty for a class that has no
    thief skills."""


class MemberEffectView(_Model):
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
    [`EffectActiveCondition`][osrlib.crawl.gates.EffectActiveCondition] checks for. A spell's effect
    has the spell's id as its kind unless the spell's data gives it a kind of its own, and
    [`PlayerView.spell_names`][osrlib.crawl.views.PlayerView.spell_names] contains the name for a kind
    that is a spell id."""
    remaining_rounds: int | None
    """Rounds left before the effect expires, never below zero. `None` when the effect has no
    expiry, and for any effect a potion gave, because by the rules the referee tracks a potion's
    duration and never tells the players."""


class EdgeView(_Model):
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


class PileItemView(_Model):
    """One thing in a dropped pile, by the id the events name it by, with its count.

    You get these from [`PileView.contents`][osrlib.crawl.views.PileView.contents]. Look `item_id` up
    in [`PlayerView.item_names`][osrlib.crawl.views.PlayerView.item_names] and
    [`PlayerView.item_names_plural`][osrlib.crawl.views.PlayerView.item_names_plural] to name it. A gem or
    piece of jewellery that has a name of its own has it in
    [`PlayerView.valuable_names`][osrlib.crawl.views.PlayerView.valuable_names] too, which you show as
    written in a list, and a phrase in
    [`PlayerView.valuable_phrases`][osrlib.crawl.views.PlayerView.valuable_phrases], which you use as
    written in a sentence.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str
    """The catalog id of a mundane stack, like `"holy_water"`, or the instance id of a magic item,
    like `"magic-item-0003"`, or of a gem or piece of jewellery, like `"valuable-0007"`. It's the form
    of id that [`ItemsDroppedEvent.item_ids`][osrlib.crawl.events.ItemsDroppedEvent.item_ids] and the
    other item events use, and an unidentified magic item's template id never appears here."""
    quantity: int = 1
    """How many units: the size of a mundane stack, the
    [`quantity`][osrlib.core.items.MagicItemInstance.quantity] of a magic item, which is more than 1
    only for enchanted ammunition, and always 1 for a gem or piece of jewellery."""


class PileView(_Model):
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
    name, without any spaces around it, or its kind when its name is empty or only whitespace."""
    coins_gp_value: int
    """The pile's coins, as their value in whole gold pieces, rounded down."""
    coins: Coins = Coins()
    """The pile's coins by denomination, so a pile of copper alone isn't mistaken for an empty one."""
    contents: tuple[PileItemView, ...] = ()
    """One entry per thing in the pile, in the order of `items`, each with the id the events name
    it by and its count.

    A mundane stack has its catalog id, so a client reads `"holy_water"` and 2 here where `items` has
    the string `"holy_water×2"`. A magic item and a valuable have their instance ids. Name each entry
    from [`PlayerView.item_names`][osrlib.crawl.views.PlayerView.item_names], which has a key for
    every id here."""


class DungeonNameView(_Model):
    """The public identity of a dungeon the party has entered."""

    model_config = ConfigDict(frozen=True)

    id: str = ""
    """The dungeon id from the adventure."""
    name: str = ""
    """The dungeon's display name."""


class EnteredAreaView(_Model):
    """An authored area after the party has entered at least one of its cells."""

    model_config = ConfigDict(frozen=True)

    id: str = ""
    """The area's authored id."""
    name: str = ""
    """The area's display name."""
    description: str = ""
    """The area's authored description."""
    cells: tuple[Position, ...] = ()
    """Only the area cells the party has walked, in authored order."""
    trap_state: Literal["found", "sprung", "removed"] | None = None
    """The room trap's state as the party knows it, with the values and meaning of
    [`MapFeatureView.trap_state`][osrlib.crawl.views.MapFeatureView.trap_state]: `"found"` once a
    [`Search`][osrlib.crawl.commands.Search] has found it, and `"sprung"` once it has gone off. No
    command removes a room trap, so a room never shows `"removed"`.

    It's `None` while the party hasn't met the room's trap, and for a room with no trap, so a room
    the players know nothing about looks the same whether or not it's trapped. The view lists an area
    only once the party has entered it, so a trap found or sprung through a door of a room the party
    hasn't entered shows here once the party walks in."""


class MapFeatureView(_Model):
    """One known actionable or descriptive feature on the player map."""

    model_config = ConfigDict(frozen=True)

    id: str = ""
    """An opaque `feature:` handle. Pass this value in a feature command's `feature_id`
    instead of an internal authored or generated cache id."""
    kind: Literal["treasure_cache", "construction_trick", "custom", "generated_cache"] = "custom"
    """The feature's public kind. Generated caches use `"generated_cache"`."""
    description: str = ""
    """The feature's authored [`FeatureSpec.description`][osrlib.crawl.dungeon.FeatureSpec.description],
    or an empty string for a generated cache. It's written for the players to read. The view shows
    it from the moment the feature comes into view, and for a cache, before anyone opens it and
    after the party empties it. The feature's
    [`FeatureSpec.referee_notes`][osrlib.crawl.dungeon.FeatureSpec.referee_notes] never appear in
    the player view."""
    cell: Position | None = None
    """The feature's cell, or `None` when an area feature applies to the whole entered area."""
    area_id: str | None = None
    """The id of the area the feature is in, once the party has entered that area, otherwise `None`.
    For an authored feature, it's the area that lists the feature. For a generated cache, it's the
    area that owns the cache's cell, the area that
    [`LevelSpec.area_at`][osrlib.crawl.dungeon.LevelSpec.area_at] returns. It never names an area
    the party hasn't entered."""
    trap_state: Literal["found", "sprung", "removed"] | None = None
    """The party's known trap state, or `None` when the party knows no trap state."""
    emptied: bool = False
    """True for an authored treasure cache the party has emptied with
    [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure].

    The cache stays in the list under the same `id` and description, so you can draw it opened.
    Don't offer it to `TakeTreasure`, which refuses it with `exploration.feature.emptied`. It's
    false for every other feature, and for a cache the party hasn't emptied, however little it
    holds. A generated cache is never marked emptied, because the session removes it when the
    party takes it, so it leaves the list."""


class TransitionView(_Model):
    """A known level transition without its destination or gate."""

    model_config = ConfigDict(frozen=True)

    position: Position = (0, 0)
    """The cell where the transition stands."""
    kind: Literal["stairs_up", "stairs_down", "trapdoor", "chute", "passage"] = "stairs_up"
    """The visible transition kind."""
    side: Direction | None = None
    """The passage side, or `None` for every transition the party uses at the cell."""


class ExploredLevelView(_Model):
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
    areas: tuple[EnteredAreaView, ...] = ()
    """The authored areas the party has entered, with only their walked cells."""
    features: tuple[MapFeatureView, ...] = ()
    """The features the party can currently see or has discovered."""
    transitions: tuple[TransitionView, ...] = ()
    """The transitions on known cells, without destinations or gates."""
    entrance: Position | None = None
    """The level's entrance when the party knows that cell, otherwise `None`."""


class EncounterMemberView(_Model):
    """One monster or NPC adventurer's public identity and coarse status.

    Read these from [`EncounterGroupView.members`][osrlib.crawl.views.EncounterGroupView.members].
    Use `id` in [`BattleDeclaration.targets`][osrlib.crawl.commands.BattleDeclaration.targets] to
    select an individual creature for a spell or scroll before any combat event names it. Hit
    points, individual conditions, and other private statistics never appear.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The creature's existing session id, such as `"monster-0001"` or `"npc-0001"`.

    It matches the id in events and stays the same after save/load or a group split. Read the
    current group to find where the creature is now."""
    status: Literal["alive", "defeated", "fled"]
    """The creature's coarse status: `"defeated"` when dead, `"fled"` when alive in a group
    that has left the encounter, and `"alive"` otherwise. A creature that's running but hasn't
    left is still `"alive"`. This doesn't report whether the creature can act or is immune to a spell."""


class EncounterGroupView(_Model):
    """A monster or NPC group as the players see it: identity, roster, count, distance, and appearance.

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
    """What to call one member of the group in a sentence, like `"goblin"`. It's the same as
    [`EncounterGroup.label`][osrlib.crawl.encounter.EncounterGroup.label], which lists where each kind
    of group gets its label. It's display text, so don't match on its words. To tell what kind of
    creature the group is, read `family_id` and `categories`."""
    label_plural: str = Field(default="", validate_default=True)
    """What to call two or more members of the group, like `"goblins"` or `"wolves"`. It's the same as
    [`EncounterGroup.label_plural`][osrlib.crawl.encounter.EncounterGroup.label_plural]."""
    family_id: str | None = None
    """What kind of monster the group is, as the
    [`family_id`][osrlib.core.monsters.MonsterTemplate.family_id] its members' templates share, like
    `"fire_beetle"` or `"hellhound"`. `None` for a party of NPC adventurers, and for a group whose
    members come from more than one family.

    The value is the same however the group came to be: from a keyed area, a wandering monster check, or
    a [`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters]. So match on it, not on the words in
    `label`.

    The players see this value. A family is the creature the players see, and it's shared by
    every form the players can't tell apart, so a group of hellhounds is `"hellhound"` whatever their
    Hit Dice. For a template the adventure bundles in
    [`Adventure.monsters`][osrlib.crawl.adventure.Adventure.monsters], the value is the template's `id`
    unless its author set `family_id`. An author who disguises a creature as another, like a
    doppelganger the players see as a goblin, sets the template's `family_id` to the family the players
    should see, `"goblin"`, as well as its `display_name`. The players also see the template's
    [`categories`][osrlib.core.monsters.MonsterTemplate.categories]. A group your code opens with
    [`start_encounter`][osrlib.crawl.encounter.start_encounter] under a label of its own still reports
    its templates' family here.

    To read what the players' rulebook says about the creature, pass the value to
    [`MonsterCatalog.family`][osrlib.core.monsters.MonsterCatalog.family] on
    [`load_monsters`][osrlib.data.load_monsters]. A family that only the adventure bundles has no entry
    in the rulebook, so it isn't in `load_monsters()`, and `family` raises `ValueError` for it. A session
    finds it in [`GameSession.effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters]."""
    categories: tuple[str, ...] = ()
    """The categories every member of the group has, like `("undead",)`, as in
    [`MonsterTemplate.categories`][osrlib.core.monsters.MonsterTemplate.categories]. A party of NPC
    adventurers is `("person",)`, because *hold person* and *charm person* affect any character.

    Use it to offer what works only against one kind of creature. Turning and holy water affect a group
    with `"undead"`, and *hold person* affects a group with `"person"`. The players see these categories,
    as they see `family_id`."""
    count: int
    """How many of the group are still alive."""
    distance_feet: int
    """How far away the group is on the range track, in feet."""
    visible_conditions: tuple[str, ...]
    """The conditions visible on the living members of the group, as
    [`Condition`][osrlib.core.effects.Condition] serialized values, sorted and deduplicated so
    the same condition on six goblins appears once. `condition_counts` says how many members have
    each one."""
    condition_counts: dict[str, int] = {}
    """How many living members of the group have each visible condition, keyed by the condition's
    serialized value. Its keys are the values in `visible_conditions`, in the same order.

    When *sleep* takes 4 of 11 orcs, `count` is 11 and this is `{"asleep": 4}`, so you can show "4 of 11
    orcs asleep" and tell whether a second *sleep* is worth casting."""
    fled: bool = False
    """True once the group has run away and left the battle, as in
    [`EncounterGroup.fled`][osrlib.crawl.encounter.EncounterGroup.fled].

    A fled group stays in [`EncounterView.groups`][osrlib.crawl.views.EncounterView.groups], but the
    session refuses group-targeted declarations with `battle.declaration.unknown_group`. A spell or
    scroll that names one of its members in `targets` gets `magic.cast.unknown_target`. Don't offer
    the group or its members as targets. A group that's running but hasn't got away yet isn't fled.
    You can still close on it, shoot at it, or cast at it.

    The session emits a [`MonsterEscapedEvent`][osrlib.crawl.events.MonsterEscapedEvent] for the
    group in the command result in which `fled` becomes true, and in no other result. You can show
    the group getting away from the event stream, and then drop it from your target lists."""
    fleeing: bool = False
    """True once the group has failed a morale check and turned to run, as in
    [`EncounterGroup.fleeing`][osrlib.crawl.encounter.EncounterGroup.fleeing].

    It stays true after the group gets away, when `fled` becomes true as well. A group that's
    `fleeing` and not `fled` is running and still in the battle, so you can show it running and
    still offer it as a target."""
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
    party's ranks toward their group.

    Only the monsters' front rank makes melee attacks on these members: the group's first living
    monsters, as many as
    [`EncounterView.formation_width`][osrlib.crawl.views.EncounterView.formation_width]. So six
    skeletons in a 10' corridor strike with two at most, the smaller of `count` and the width. A
    monster in front that's asleep, paralysed, or turned to stone keeps its place and doesn't attack,
    so fewer can strike. `condition_counts` counts those conditions across the whole group, so it
    doesn't say whether they fall on the monsters in front. With the ruleset's
    `formation_width_limit` flag off, every living monster can attack."""
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
    members: tuple[EncounterMemberView, ...] = ()
    """Every member of this group in the group's existing order, including dead and fled members.

    Each entry exposes only its session id and coarse status. Use an `"alive"` entry's id in
    [`BattleDeclaration.targets`][osrlib.crawl.commands.BattleDeclaration.targets] for a spell or
    scroll aimed at individual creatures. The spell's range, target count, and touch checks still
    apply. The tuple defaults to empty when you parse a view written before rosters were available."""

    @field_validator("label_plural")
    @classmethod
    def _plural_defaults_to_regular_ending(cls, value: str, info: ValidationInfo) -> str:
        return value or _regular_plural(info.data.get("label", ""))


class EncounterView(_Model):
    """The current encounter or battle's public state.

    You get one from [`PlayerView.encounter`][osrlib.crawl.views.PlayerView.encounter], which
    is `None` when the party isn't in an encounter.

    In a battle, each party member stands at their own place on the range track, the
    one-dimensional measure of distance a battle uses. `advance_feet` gives each member's place, and
    each group in `groups` gives every member's distance to it and the members who can reach it in
    melee. Read those to draw the fight and to offer each member the moves and attacks they can make.

    The id tuples `declarers`, `immobile`, `reloading`, `can_hide`, `hiding`, and `can_back_stab`
    describe the battle round as the players see it at the table: who is able to act, who is held
    fast, who is still reloading a crossbow, who can hide, who is hiding, and who can back-stab.
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
    can_hide: tuple[str, ...] = ()
    """The declarers who can declare `hide` this round, in marching order: those whose class has
    thief skills, as the thief's does, and who don't stand in the party's front rank, the first row
    of the ranks toward any group that hasn't fled and has a living member. Empty outside a battle.

    It reads the battle as it stands, as each group's `front_rank` does. The session judges a
    `hide` where the members will stand once the round's declared moves are made, so a member in
    this list who would end up in the front rank because of another member's move is refused with
    `battle.declaration.hide_in_front_rank`. A member outside it gets
    `battle.declaration.hide_not_a_thief` or `battle.declaration.hide_in_front_rank`. To learn who
    can hide beside the declarations planned so far, read
    [`BattleRoundPreview.can_hide`][osrlib.crawl.battle.BattleRoundPreview.can_hide]."""
    hiding: tuple[str, ...] = ()
    """The members who are hiding, in marching order: each has declared a `hide` that resolved, and
    the attempt hasn't ended since. Empty outside a battle.

    A member here can declare an `attack` with
    [`BattleDeclaration.back_stab`][osrlib.crawl.commands.BattleDeclaration.back_stab] in any round
    without a place in the front rank, as long as their class has the `back_stab` ability and they
    can act, and `can_back_stab` lists them. The
    back-stab still needs a melee weapon, or bare hands, and a group within 5' of the member, as
    each group's [`member_distance_feet`][osrlib.crawl.views.EncounterGroupView.member_distance_feet]
    shows, or a `close` that brings one within 5'. The list doesn't say
    whether a hide worked: the session rolls the skills in secret, and the attack's
    [`AttackRolledEvent.back_stab`][osrlib.core.events.AttackRolledEvent.back_stab] tells the players
    whether it was a back-stab. A member leaves the list when the attempt ends, which
    `BattleDeclaration.back_stab` describes."""
    can_back_stab: tuple[str, ...] = ()
    """The declarers who can declare an `attack` with
    [`BattleDeclaration.back_stab`][osrlib.crawl.commands.BattleDeclaration.back_stab] this round, in
    marching order: those whose class has the `back_stab` ability, as the thief's does, in the
    party's free round (see `party_free_round`) or while they're in `hiding`. Empty outside a battle.

    The session refuses a back-stab by any other declarer with `battle.declaration.back_stab_no_ability`
    or `battle.declaration.back_stab_target_aware`, and never refuses one by a member here with
    either code. A member who can't act isn't a declarer, so they aren't here, even while they're in
    `hiding`. Like `hiding`, the list holds every member who has hidden, whatever the secret hide in
    shadows roll was, so it never tells the players whether a hide worked.

    The list doesn't read the declaration, so the session can still refuse a back-stab by a member
    here for what the declaration says: `battle.declaration.back_stab_not_attack` when the action
    isn't `attack`, `battle.declaration.back_stab_not_melee` for a missile weapon, or a dagger aimed
    at a group more than 5' from the member after their `close`, and the checks every attack takes,
    like `battle.declaration.unknown_group`, `battle.declaration.weapon_not_wielded`,
    `combat.attack.out_of_reach`, and `combat.attack.attacker_blind`. In the free round, a back-stab
    takes the melee checks of any attack, so a member outside the group's front rank gets
    `battle.declaration.not_in_front_rank`. In any other round, a member here is hiding and needs no
    place in the front rank. To
    learn who can back-stab beside the declarations planned so far, read
    [`BattleRoundPreview.can_back_stab`][osrlib.crawl.battle.BattleRoundPreview.can_back_stab]."""
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
    ([`FIGHTER_FRONTAGE_FEET`][osrlib.crawl.battle.FIGHTER_FRONTAGE_FEET]). The square holds only
    cells of the space the party stands in, so a door or the edge of a room bounds it. A one-cell
    corridor gives 2, and so does a crossroads of one-cell corridors. A room gives twice its shorter
    side in cells: a 4 by 4 room gives 8, and a 2 by 8 room gives 4. The width comes from the party's
    cell, so it's set in an encounter before a battle starts, and it stays the same for the whole
    fight. The ranks toward each group are rows this wide, so
    a group's [`front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank] never holds more members
    than this. Compare the two to show how many places are left in a group's front rank.

    The width limits the monsters too. Each group's front rank of monsters is its first living
    monsters, as many as this, and only they make melee attacks on the party."""
    party_free_round: bool = False
    """True in the round the party gets free after it surprised the monsters, while the monsters can't
    act. False in every other round, and outside a battle.

    It's the round in which any thief's back-stab is legal: an `attack` that sets
    [`BattleDeclaration.back_stab`][osrlib.crawl.commands.BattleDeclaration.back_stab]. In other
    rounds, only the members in `hiding` can back-stab. `can_back_stab` lists the members who can
    back-stab in this round, in either case. It's true while
    [`BattleState.monsters_hold_rounds`][osrlib.crawl.battle.BattleState.monsters_hold_rounds] is above
    zero."""
    evade_rejections: dict[str, tuple[Rejection, ...]] = {}
    """Why the session would refuse an [`Evade`][osrlib.crawl.commands.Evade] now, for each value of
    [`Evade.drop`][osrlib.crawl.commands.Evade.drop], keyed by that value: `"none"`, `"treasure"`, and `"food"`.

    Read it to disable a choice on your flee menu that the session would refuse, and to show the reason. Each value is a
    tuple of [`Rejection`][osrlib.core.validation.Rejection] models with the `code` and `params` that an `Evade` with
    that `drop` gets. An empty tuple means that the session accepts it. An accepted `Evade` can still end in a pursuit
    or a battle, because the outcome depends on the monsters, and the view makes no prediction of it.

    The codes you'll meet are these:

    - `encounter.evade.already_evading`, for every value, while a pursuit is running, as `pursuit_gap_feet` shows.
      Each round of a pursuit comes from a [`Wait`][osrlib.crawl.commands.Wait] or a
      [`DropItems`][osrlib.crawl.commands.DropItems] instead.
    - `encounter.evade.nobody_able_to_act`, for every value, when no living member can act, because each one is
      paralysed, petrified, or asleep.
    - `encounter.evade.cannot_move`, for every value, with the `character` param, when that living member is caught in
      a *web*.
    - `encounter.evade.cannot_move`, with the `reason` param `"overloaded"`, when a living member would still carry
      more than the maximum load after the drop. The `"treasure"` drop scatters every coin before the party runs, so
      an overloaded party can get the rejection for `"none"` and `"food"` and none for `"treasure"`.
    - `encounter.evade.nothing_to_drop`, with the `drop` param `"treasure"`, when no living member has a coin in
      their purse.
    - `encounter.evade.nothing_to_drop`, with the `drop` param `"food"`, when no living member carries a
      `rations_standard` or a `rations_iron`.

    A dead member's coins and rations don't count. A member who is alive but can't act, such as one who is asleep or
    paralysed, counts, as long as another member can act.

    While the session is in the `encounter` mode and no pursuit is running, the `"none"` entry is empty unless the party
    can't run: nobody can act, a member is caught in a *web*, or a member carries too much to move. Only the overload
    can clear with a drop, so read the `"treasure"` entry before you tell the player that the party can't evade.

    The map has an entry for every value while the session is in the `encounter` mode. In a battle, the map is empty,
    because the session refuses every `Evade` there with `session.command.wrong_mode`. An `EncounterView` that osrlib
    2.10.0 built, such as one in a stored feed entry, has no `evade_rejections`, and it validates with the map empty."""


class ObjectiveView(_Model):
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


class QuestView(_Model):
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


class SuppliesView(_Model):
    """How long the party's light and food will last, counted from what the living members carry.

    You get this from [`PlayerView.supplies`][osrlib.crawl.views.PlayerView.supplies]. osrlib computes each number
    by the rules the session applies, so you can show "light left" and "food left" without knowing the burn time of
    a torch or how the party eats. Every time is in rounds, as
    [`MemberEffectView.remaining_rounds`][osrlib.crawl.views.MemberEffectView.remaining_rounds] is, and a turn is
    [`ROUNDS_PER_TURN`][osrlib.core.clock.ROUNDS_PER_TURN] rounds.

    Only the items that living members carry count, because a dead member's torch or ration is of use only after a
    living member takes it.
    Items in a dropped pile don't count either. In town the summary reports the same numbers as in a dungeon, and
    you decide from [`PlayerView.location`][osrlib.crawl.views.PlayerView.location] whether to show them. When
    every member is dead, every count is 0, every flag is false, and `food_short_first_ids` is empty.
    """

    model_config = ConfigDict(frozen=True)

    light_burning_rounds: int
    """The rounds left on the timed light that will burn longest, among the lights burning now, and 0 when no
    timed light burns.

    Lights burn at the same time, so this is the longest time left, not a sum: two torches burning at once, with 40
    and 300 rounds left, give 300. It counts every light effect on a living member, which is a lit torch or lantern,
    the *light* spell, and any effect whose kind is in
    [`LIGHT_EFFECT_KINDS`][osrlib.crawl.session.LIGHT_EFFECT_KINDS]. Each light's own time left is its entry's
    `remaining_rounds` in [`PlayerView.effects`][osrlib.crawl.views.PlayerView.effects]. A light from a potion has
    no `remaining_rounds` there, because the referee keeps a potion's duration secret, so it doesn't count here or
    in `light_permanent`.

    A *darkness* effect puts the party's light out while it lasts without ending the lights, so it doesn't change
    this number. To learn whether the party has light right now, look for a `darkness` or `continual_darkness`
    effect in `effects`."""
    light_permanent: bool
    """Whether a light with no duration is burning on a living member, such as *continual light* or a sword that
    sheds light. Such a light adds nothing to `light_burning_rounds`, and while it burns the party doesn't run out
    of light."""
    light_reserve_rounds: int
    """How many rounds of light the party carries and hasn't lit yet.

    It's the living members' unlit torches times the rounds one torch burns, plus their oil flasks times the
    rounds one flask burns in a lantern. A lit torch is used up when it's lit, so `torches` counts only unlit ones
    and nothing is counted twice. Oil counts only when a living member carries a lantern, and then every living
    member's flasks count, because members can pass items to each other. So the reserve can include oil that stays
    unusable until a flask or the lantern changes hands. Read `light_reserve_usable` to learn whether the
    party holds what it needs to light something."""
    light_reserve_usable: bool
    """Whether the party has what [`LightSource`][osrlib.crawl.commands.LightSource] needs to light a carried torch
    or lantern now.

    It's true when a living member who can act carries a torch, or carries a lantern and an oil flask, and either
    carries a tinder box or stands in a party where a living member already has a flame burning. `LightSource`
    uses the same carrying check. So while the session is in a mode that accepts
    `LightSource`, `"town"` or `"exploring"`, some `LightSource` that names a torch or a lantern is accepted when
    this is true, and every one is refused when it's false. The flag doesn't depend on the mode. In an encounter or
    a battle, the session refuses `LightSource` with `session.command.wrong_mode` whatever this flag says, so read
    [`PlayerView.mode`][osrlib.crawl.views.PlayerView.mode] to learn whether the party can light anything this
    moment. When the reserve isn't zero and this is false, the likely cause is that no member who carries a torch
    or a lantern has a tinder box, that the oil and the lantern are in different packs, or that the carriers can't
    act.

    An accepted command strikes the tinder box when the party has no flame, and the strike can fail, so an
    accepted command doesn't always light the source. Setting fire to a flask dropped on the
    floor doesn't count, because a pool of burning oil on the floor isn't a light that the party carries."""
    torches: int
    """The unlit torches the living members carry."""
    oil_flasks: int
    """The oil flasks the living members carry."""
    lanterns: int
    """The lanterns the living members carry, lit or not."""
    rations: int
    """The rations the living members carry, standard and iron together."""
    rations_standard: int
    """The standard rations the living members carry."""
    rations_iron: int
    """The iron rations the living members carry."""
    food_days: int
    """How many days the party eats before the first living member goes short, as the packs are now.

    Each member eats from their own pack, one ration a day, so it's the fewest rations any living member carries.
    The session feeds every living member at the start of each new day. The party eats for `food_days` days, and
    at the feeding after those, the members in `food_short_first_ids` go short. In town nobody goes short,
    because a member with no rations is fed anyway, but this is still the number of days a member's own pack
    lasts. It's 0 when every member is dead."""
    food_days_shared: int
    """How many days the party could eat if its rations were shared out evenly: the living members' rations
    divided by the number of living members, rounded down. When it's larger than `food_days`, passing rations
    between members with [`GiveItems`][osrlib.crawl.commands.GiveItems] feeds the party longer. It's 0 when every
    member is dead."""
    food_short_first_ids: tuple[str, ...]
    """The ids of the living members who carry the fewest rations, the count that sets `food_days`, in marching
    order. They go short first. It's empty when every member is dead."""


class PlayerView(_Model):
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
    town_healing_prices: dict[str, int] = {}
    """What the town's temple charges for each of its services, in gold pieces, keyed by the
    [`HealingService`][osrlib.crawl.commands.HealingService] name, like `"raise_dead"`. Every
    service is listed. A price comes from
    [`TownSpec.healing_prices`][osrlib.crawl.adventure.TownSpec.healing_prices] where the adventure
    sets one, and from the standard prices in
    [`HEALING_SERVICES`][osrlib.crawl.exploration.HEALING_SERVICES] otherwise. Read it to show the
    temple's price list, and to check the party's gold against a price before you send a
    [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing]."""
    town_healing_rejections: dict[str, dict[str, tuple[Rejection, ...]]] = {}
    """Why the temple would refuse each of its services for each party member now, keyed by
    [`MemberView.id`][osrlib.crawl.views.MemberView.id] and then by the
    [`HealingService`][osrlib.crawl.commands.HealingService] name, like `"raise_dead"`.

    Read it to disable a temple service that a member can't use, and to show the reason. Each value is a tuple of
    [`Rejection`][osrlib.core.validation.Rejection] models with the `code` and `params` that a
    [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing] for that member and service gets with `spell_mode`
    unset, which is the spell's first usage. For a service the temple sells in more than one usage, such as *cure light
    wounds*, which also cures paralysis, read
    [`town_healing_mode_rejections`][osrlib.crawl.views.PlayerView.town_healing_mode_rejections] for each usage.
    An empty tuple means that some temple cleric can perform the service for the member, and
    [`town_healing_caster_levels`][osrlib.crawl.views.PlayerView.town_healing_caster_levels] gives the level to send.
    A tuple that isn't empty means that no temple cleric, of any level, can, and it contains the rejections that a
    purchase with `caster_level` unset gets.

    Turn a rejection into text by its code, as you do for a refused command. The code you'll meet is
    `town.healing.no_effect`, for a service that would do nothing for the member, and its `reason` param says why:

    - `dead`: *cure light wounds* or *cure serious wounds* for a dead member.
    - `weakened` or `diseased`: a cure for a member whose condition stops them from healing by magic.
    - `full_hit_points`: a cure for a member at their maximum hit points.
    - `not_diseased`: *cure disease* for a member who isn't diseased.
    - `not_poisoned`: *neutralize poison* for a member who isn't poisoned and didn't die of poison in the last 10
      rounds.
    - `not_cursed`: *remove curse* for a member with no curse that the spell lifts.
    - `not_dead`: *raise dead* for a living member.
    - `dead_too_long`: *raise dead* for a member who has been dead longer than the highest-level temple cleric
      reaches.
    - `no_death_record`: *raise dead* for a dead member whose death the session didn't record, so it can't tell how
      long ago the member died.

    A purchase whose `caster_level` is too low to raise a member whom a higher cleric could raise gets one more
    *raise dead* reason, `caster_level_too_low`. This map never shows it, because a higher level works for that
    member, and the map shows an empty tuple and the level instead.

    The fee isn't part of the answer, so a service the party can't afford is listed as one the temple can perform.
    Compare the price in `town_healing_prices` with
    [`town_healing_funds_gp`][osrlib.crawl.views.PlayerView.town_healing_funds_gp], because the session refuses a
    purchase the party can't pay for with `items.purchase.insufficient_funds` before it makes these checks.

    The map has an entry for every party member, the dead included, and every service, while the session is in the
    `"town"` mode. The town's `town_services` don't change it, because the temple serves the party in any town. In
    any other mode the map is empty, because the session refuses every `PurchaseHealing` there with
    `session.command.wrong_mode`. A `PlayerView` that osrlib 2.10.0 built, such as a stored feed entry, has no
    `town_healing_rejections`, and it validates with this map empty."""
    town_healing_caster_levels: dict[str, dict[str, int]] = {}
    """The lowest temple cleric level that can perform each service for each party member, keyed like
    [`town_healing_rejections`][osrlib.crawl.views.PlayerView.town_healing_rejections].

    Send the number as [`PurchaseHealing.caster_level`][osrlib.crawl.commands.PurchaseHealing.caster_level], and a
    party that can pay gets the service. A service that no temple cleric can perform for the member has no entry, and
    its rejections are in `town_healing_rejections`. For every service except *raise dead*, the level is the lowest
    that can cast the service, so leaving `caster_level` unset gives the same result. For *raise dead* it's the lowest
    level whose reach covers how long the member has been dead: 7 for a member who died less than a day ago, and 8
    for one dead 3 days.

    The price is the same at every level, and a higher level changes nothing but how far back *raise dead* reaches
    and the caster level recorded on the weakness that *raise dead* leaves, which *dispel magic* rolls against. So
    the level here gives the same result as any higher level, except that a *dispel magic* that a party cleric casts
    on that weakness succeeds at least as often. If your game limits its temple to clerics of some level, compare
    that limit with this number, as you compare a price with the party's funds.

    The map is empty outside the `"town"` mode, and a `PlayerView` that osrlib 2.10.0 built validates with it
    empty."""
    town_healing_mode_rejections: dict[str, dict[str, dict[str, tuple[Rejection, ...]]]] = {}
    """Why the temple would refuse each usage of a service for each party member now, for the services it sells in
    more than one usage. The map is keyed by [`MemberView.id`][osrlib.crawl.views.MemberView.id], then by the
    [`HealingService`][osrlib.crawl.commands.HealingService] name, and then by the usage's
    [`SpellMode.key`][osrlib.core.spells.SpellMode], which is what you send as
    [`PurchaseHealing.spell_mode`][osrlib.crawl.commands.PurchaseHealing.spell_mode].

    Read it to offer a usage beside the service's first one, such as "cure paralysis" beside *cure light wounds*'s
    healing. Each value means what a value of
    [`town_healing_rejections`][osrlib.crawl.views.PlayerView.town_healing_rejections] means, for a purchase that
    names that usage: an empty tuple means that some temple cleric can perform it for the member, and a tuple that
    isn't empty contains the rejections that a purchase with `caster_level` unset gets. The usages come in the order
    the spell lists them, so the first is the one that `town_healing_rejections` answers for, with the same tuple.
    The session makes the same check for the view and the command, so they can't disagree. Look a usage's display
    name up in [`town_healing_mode_names`][osrlib.crawl.views.PlayerView.town_healing_mode_names].

    The temple sells *cure light wounds* in its `heal` and `cure_paralysis` usages, and every other service in its
    first usage alone, so *cure light wounds* is the only service in this map. A refusal of `cure_paralysis` is
    `town.healing.no_effect` with the `reason` param `dead` for a dead member or `not_paralysed` for a member who
    isn't paralysed. The cleric level doesn't change the answer for either usage of cure light wounds, so leave
    `caster_level` unset, which uses the service's lowest level.

    The map has an entry for every party member, the dead included, while the session is in the `"town"` mode, and
    it's empty in any other mode. A `PlayerView` that osrlib 2.11.0 or earlier built validates with it empty."""
    town_healing_mode_names: dict[str, dict[str, str]] = {}
    """The display name of each usage of a service, for the services the temple sells in more than one usage, keyed
    by the [`HealingService`][osrlib.crawl.commands.HealingService] name and then by the usage's
    [`SpellMode.key`][osrlib.core.spells.SpellMode], such as `{"cure_light_wounds": {"heal": "Heal a living subject",
    "cure_paralysis": "Cure paralysis"}}`.

    Each name is the usage's [`SpellMode.display_name`][osrlib.core.spells.SpellMode] in the session's spell catalog,
    so a remote client can label each usage in
    [`town_healing_mode_rejections`][osrlib.crawl.views.PlayerView.town_healing_mode_rejections] without the
    catalog. The services and usages are the same as there, in the same order. The session fills it in every mode,
    like `town_healing_prices`, and a `PlayerView` that osrlib 2.11.0 or earlier built validates with it empty."""
    town_healing_funds_gp: int | None = None
    """The whole gold pieces that the party's purses can pay toward a temple fee now.

    The temple sells a service the party can pay for when the service's price in
    [`town_healing_prices`][osrlib.crawl.views.PlayerView.town_healing_prices] is at most this number, and it refuses
    one that costs more with `items.purchase.insufficient_funds`. The session computes this number with the function
    its funds check uses, so the two can't disagree. Each purse pays in whole gold pieces of its total value, across
    every coin type, and any value below a gold piece stays in the purse, so this is the sum over the party, dead
    members included, of each purse's value rounded down to a gold piece. For example, two members who each have 12
    gp and 5 sp give 24, not 25. Whoever is treated, the total is the same, because the patient's purse only pays
    first.

    The session fills it in every mode, like `town_healing_prices`. A `PlayerView` that osrlib 2.10.0 built has no
    `town_healing_funds_gp`, and it validates with `None`, which means the view doesn't state the funds, not that
    the party has none."""
    party: tuple[MemberView, ...]
    """The party's character sheets, in marching order, the dead included."""
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
    dungeons: tuple[DungeonNameView, ...] = ()
    """The dungeons the party has entered, in adventure order."""
    can_travel_to_town: bool = False
    """Whether the exploring party can use
    [`TravelToTown`][osrlib.crawl.commands.TravelToTown] from its current position."""
    features_here: tuple[str, ...] = ()
    """The handles that the feature commands accept in `feature_id` from where the party stands, in
    the order the current level's [`features`][osrlib.crawl.views.ExploredLevelView.features] lists
    them. The commands are [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure],
    [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure], and
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap].

    Each entry is the [`MapFeatureView.id`][osrlib.crawl.views.MapFeatureView.id] of a treasure cache
    the party can reach: an authored or generated cache on the party's cell, or an authored cache
    that belongs to the whole of the area the party stands in. The session resolves a command's
    `feature_id` against the same caches, so a cache that isn't listed here is refused with
    `exploration.feature.unknown`. It's empty unless the session is exploring, because the commands
    are legal only then. A custom feature or a construction trick is never listed, because no
    command acts on one.

    A command can still refuse a listed cache for another reason. `TakeTreasure` refuses an emptied
    cache with `exploration.feature.emptied`, which
    [`MapFeatureView.emptied`][osrlib.crawl.views.MapFeatureView.emptied] tells you beforehand.
    `RemoveTreasureTrap` accepts only a cache whose
    [`trap_state`][osrlib.crawl.views.MapFeatureView.trap_state] is `"found"`. To take the goods
    dropped on the party's cell, send `"pile"`, when [`piles`][osrlib.crawl.views.PlayerView.piles]
    has an entry for the cell."""
    piles: dict[str, PileView]
    """The dropped piles the party knows about, keyed by the cell reference
    [`cell_ref`][osrlib.crawl.dungeon.cell_ref] returns. A pile in a cell the party has
    not walked is left out."""
    emptied_caches: tuple[str, ...]
    """The authored treasure caches the party has emptied, as `"{dungeon}:{level}:{id}"` feature
    references, in the order the party emptied them.

    To draw an emptied cache, read
    [`MapFeatureView.emptied`][osrlib.crawl.views.MapFeatureView.emptied] instead, which marks the
    cache in `features` under its handle. Command targets come from
    [`MapFeatureView.id`][osrlib.crawl.views.MapFeatureView.id]."""
    effects: tuple[MemberEffectView, ...]
    """The active effects on party members, in ledger order."""
    spell_names: dict[str, str] = {}
    """The name of each spell whose id the view shows, keyed by spell id, like
    `{"magic_missile": "Magic Missile"}`. Show these names, not the ids.

    A spell id appears in five places in the view: in a member's
    [`spell_book`][osrlib.crawl.views.MemberView.spell_book],
    [`preparable_spells`][osrlib.crawl.views.MemberView.preparable_spells], and
    [`learnable_spells`][osrlib.crawl.views.MemberView.learnable_spells], as the `spell_id` of a copy in
    a member's [`memorized_spells`][osrlib.crawl.views.MemberView.memorized_spells], and as the
    [`kind`][osrlib.crawl.views.MemberEffectView.kind] of an effect in `effects`, because a spell's
    effect has the spell's id as its kind unless the spell's data gives it a kind of its own. Every one
    of those ids that the session's catalog,
    [`GameSession.effective_spells`][osrlib.crawl.session.GameSession.effective_spells], has is a key
    here, with the spell's [`name`][osrlib.core.spells.SpellTemplate.name]. A spell the adventure
    bundles has its name here too, which [`load_spells`][osrlib.data.load_spells] doesn't have. An
    effect kind that isn't a spell id, like a torch's `"light"`, has no entry.

    It names no spell whose id the view doesn't show. So a spell the adventure bundles for a list that
    no member casts from stays hidden, and so does a spell at a level where no member could prepare or
    learn it. A spell on a scroll is named only when a member could prepare or learn it anyway. A spell
    id on a member that the session's catalog doesn't have, like one from a party made for another
    adventure, has no entry either, and the session refuses to prepare or cast that spell. The name here
    is always the spell's normal form. For a copy prepared reversed, show its name from
    `reversed_spell_names`."""
    reversed_spell_names: dict[str, str] = {}
    """The name of each spell's reverse, keyed by spell id, like `{"light_mu": "Darkness"}`, for every
    spell in `spell_names` that has one.

    Each value is the [`name`][osrlib.core.spells.ReversedForm.name] of the spell's
    [`reversed_form`][osrlib.core.spells.SpellTemplate.reversed_form], read from the same catalog as
    `spell_names`, so a spell the adventure bundles has the name its author gave the reverse. A spell
    that doesn't reverse has no entry.

    Show this name for an arcane caster's copy whose `reversed` is true in
    [`memorized_spells`][osrlib.crawl.views.MemberView.memorized_spells]. A divine caster prepares the
    normal form and chooses the form when casting, so every memorized copy of a reversible spell can be
    cast either way. To offer that choice, show both names."""
    spell_levels: dict[str, int] = {}
    """The level of each spell in `spell_names`, keyed by spell id, like
    `{"magic_missile": 1, "web": 2}`. Group a prepare or learn dialog by it, because a member's slots
    are counted per spell level, in
    [`MemberView.spell_slots`][osrlib.crawl.views.MemberView.spell_slots].

    Each value is the spell's [`level`][osrlib.core.spells.SpellTemplate.level], read from the same
    catalog as `spell_names`, so a spell the adventure bundles has the level its author gave it. A
    spell id belongs to exactly one spell list, like `light_mu` to the magic-user's and `light_c` to
    the cleric's, so each id has one level for every class that casts from its list, like the elf,
    who casts from the magic-user's list."""
    item_names: dict[str, str] = {}
    """What to call one of each item whose id the view shows, keyed by that id, like
    `{"torch": "torch", "holy_water": "vial of holy water", "magic-item-0003": "potion"}`. Show these
    names, not the ids, and put a number or an article in front of one: "a torch". To word a count,
    like "6 torches", use `item_names_plural`.

    An item id appears in the view in four forms, and each is a key here:

    - The catalog id of a mundane stack, like `"holy_water"`, in a member's
      [`inventory`][osrlib.crawl.views.MemberView.inventory] and in a pile's
      [`contents`][osrlib.crawl.views.PileView.contents]. For a stack a member carries, its name is
      the [`display_name`][osrlib.core.items.GearTemplate.display_name] of the template the stack
      embeds, which the session gives the display names of the template its catalog,
      [`GameSession.effective_equipment`][osrlib.crawl.session.GameSession.effective_equipment], has
      under that id, as [`ItemInstance.template`][osrlib.core.items.ItemInstance.template] says. So a
      stack from a save or a party document that osrlib 2.7.0 wrote is named as the catalog names it,
      and the name here matches the stack's template in `inventory`. A bundled id can't shadow a
      built-in one, because a session refuses an adventure whose bundled id collides with a built-in
      id. A carried stack whose id the catalog doesn't have, like an item another adventure bundled,
      keeps the names its template has. For a pile, the name comes from the catalog, and a pile's
      catalog id that the catalog doesn't have has no entry.
    - The instance id of a magic item, like `"magic-item-0003"`, in an inventory and in a pile.
      Until the party identifies the item, its name is the masked name the inventory shows, without
      the article: `"potion"`, `"curious device"`, or `"shield with a faint aura"`. So a cursed item
      and the item it passes for have the same name, and the name tells nothing the inventory
      doesn't. Once the item is identified, its name is the
      [`display_name`][osrlib.core.items.MagicItemTemplate.display_name] of its template in the magic
      item catalog, like `"potion of healing"`.
    - The template id of an identified magic item, like `"potion_of_healing"`, which the inventory
      shows once the item is identified, with the same name. A pile shows a magic item by its
      instance id alone, identified or not, so an identified item's template id is a key only while
      a member carries it. An unidentified item's template id is never a key.
    - The instance id of a gem or a piece of jewellery, like `"valuable-0007"`. Its name is its kind,
      `"gem"` or `"piece of jewellery"`. The players see everything else about it from the moment
      they take it, its own name and its value in gold pieces included, in the inventory's
      `valuables`. A piece that has a name of its own, like `"Prior's chain of office"`, has it in
      `valuable_names` under the same id, and what to call it in a sentence, like
      `"the Prior's chain of office"`, in `valuable_phrases`. Show the name in place of the kind in
      a list, and the phrase in place of the kind in a sentence.

    It names no item whose id the view doesn't show. The player-visible item events, like
    [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent], name items by the same ids. An
    item the command left in a member's pack or in a pile the view shows is a key in the view you
    build after the command. An item the command used up, sold, or left behind on a trail is a key
    in the view from before it. An item that is identified and used up in the same command, like a
    potion drunk before the party knew it, has its true name on the
    [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent]."""
    item_names_plural: dict[str, str] = {}
    """What to call two or more of each item in `item_names`, with the same keys, like
    `{"torch": "torches", "holy_water": "vials of holy water", "magic-item-0003": "potions"}`.

    Each value is the plural of the name in `item_names`: the template's
    [`display_name_plural`][osrlib.core.items.GearTemplate.display_name_plural], the plural of a
    masked name, like `"shields with a faint aura"`, or `"gems"` or `"pieces of jewellery"`. Use it
    with a count from a pile's [`contents`][osrlib.crawl.views.PileView.contents], an inventory
    entry's `quantity`, or the ids an event repeats, one per unit: "2 vials of holy water"."""
    valuable_names: dict[str, str] = {}
    """The own name of each gem and piece of jewellery that has one, keyed by its instance id, like
    `{"valuable-0007": "Prior's chain of office"}`. Show it as written in a list, such as an inventory
    row. To put the piece in a sentence, use its phrase in
    [`valuable_phrases`][osrlib.crawl.views.PlayerView.valuable_phrases], which has the article and
    the case: "Sam takes the Prior's chain of office." For an id that isn't a key, use the kind in
    `item_names`, with an article: "Sam takes a gem."

    A piece has a name of its own when its `name` is neither empty, nor only whitespace, nor the
    plain name that treasure generation gives its kind, `"Gem"` for a gem or `"Jewellery"` for a piece
    of jewellery. Spaces around the name don't count. So a piece an
    adventure places in a cache with a named [`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec] is a
    key, and neither a piece the session rolls on the treasure tables nor a placed piece whose `name`
    is empty is a key.

    The keys are the instance ids of those pieces in the members'
    [`inventory`][osrlib.crawl.views.MemberView.inventory] `valuables` and in the
    [`contents`][osrlib.crawl.views.PileView.contents] of the known piles. Each value is the name that
    the inventory's `valuables` give the piece, without any spaces around it, and the string at the
    same place in the pile's [`items`][osrlib.crawl.views.PileView.items]. A piece in a pile the party
    doesn't know, or in a cache the party hasn't emptied, isn't a key, as it isn't one in `item_names`.
    Every key here is also a key in `item_names`."""
    valuable_phrases: dict[str, str] = {}
    """What to call each piece in `valuable_names` in the middle of a sentence, with its article and its case, keyed
    by the same instance id, like `{"valuable-0007": "the abbot's seal ring", "valuable-0008": "a silver altar
    candlestick", "valuable-0009": "Ossor's Eye"}`. Use it as written in a sentence: "Wren takes a silver altar
    candlestick." Use `valuable_names` in a list, such as an inventory row.

    The keys are exactly the keys of `valuable_names`, for a piece in a member's
    [`inventory`][osrlib.crawl.views.MemberView.inventory] and for a piece in a known pile alike. Each value is the
    piece's [`phrase`][osrlib.core.items.ValuableInstance.phrase] when it has one. Otherwise osrlib makes the phrase
    from the name. A name whose first word is "the", "a", or "an", in any case, keeps its words with that first word in
    lowercase, so `"The abbot's seal ring"` becomes `"the abbot's seal ring"`. Any other name has its first character in
    lowercase and the indefinite article in front, "an" before `a`, `e`, `i`, `o`, or `u`, with or without an accent,
    and "a" otherwise, so `"Silver altar candlestick"` becomes `"a silver altar candlestick"` and `"Eye of Ossor"`
    becomes `"an eye of Ossor"`. Only that first character changes case. A phrase that's only whitespace counts as no
    phrase, and no value here has spaces around it.

    A piece from a save that osrlib 2.9.0 wrote has no phrase, so the view makes its phrase from its name, and every
    key of `valuable_names` is a key here. A `PlayerView` that osrlib 2.9.0 built, such as a stored feed entry, has no
    `valuable_phrases`, and it validates with this map empty. For such a view, word each name in `valuable_names`
    yourself."""
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
    supplies: SuppliesView | None = None
    """How long the party's light and food will last, as a
    [`SuppliesView`][osrlib.crawl.views.SuppliesView]. The session always fills it in.

    A `PlayerView` that osrlib 2.10.0 built, such as a stored feed entry, has no `supplies`, and it validates with
    `supplies` set to `None`, so such a view has no supplies summary to show."""


class ExplorationCounters(_Model):
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


class RefereeView(_Model):
    """The referee state in a save, except seeds, random streams, and the player-handle key.

    Get one from [`GameSession.view`][osrlib.crawl.session.GameSession.view] with
    `Visibility.REFEREE`, or from [`build_referee_view`][osrlib.crawl.views.build_referee_view]. Use
    it where players can't see it: as the context an LLM referee reasons over, in a debugging panel,
    or in a test that checks state a player may not see. Never send it to a player's client, and draw
    nothing a player sees from it. [`PlayerView`][osrlib.crawl.views.PlayerView] is for that.

    Each field is the session's model rather than a dict, so you read
    `view.monsters[0].current_hp` and `view.flags["key"]` with the types this reference documents, and
    each field's docstring links the model to read next. The fields are the groups
    [`session_state`][osrlib.persistence.session_state] writes, minus the master seed, the RNG stream
    positions, and the player-handle key, so `view.model_dump(mode="json")` is that save payload
    without those keys, plus one field the save doesn't contain,
    [`feature_handles`][osrlib.crawl.views.RefereeView.feature_handles]. The seed and stream
    positions let a player predict every roll to come. The key lets someone reproduce and control the
    session's opaque feature handles, so the view gives you the handles themselves instead.

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
    feature_handles: dict[str, str] = {}
    """The player handle of every feature in the session, keyed by the reference the save uses for
    the feature. An authored feature's key is `"{dungeon}:{level}:{feature id}"`, the form in
    [`DungeonState.emptied_caches`][osrlib.crawl.dungeon.DungeonState.emptied_caches] and
    [`DungeonState.found_traps`][osrlib.crawl.dungeon.DungeonState.found_traps], and every feature the
    adventure authors has one, on a level or in an area, found or not. A generated cache's key is its
    id in [`DungeonState.generated_caches`][osrlib.crawl.dungeon.DungeonState.generated_caches], like
    `"cache-0001"`. Every cache that the session rolled or that [`load_game`][osrlib.persistence.load_game]
    restored has one, including a lair cache the players can't see yet. A cache you write into
    `DungeonState.generated_caches` yourself on a cell that doesn't exist has none.

    Each value is the [`MapFeatureView.id`][osrlib.crawl.views.MapFeatureView.id] that the player view
    shows for the feature once the party knows it. Use it to show a referee the feature the players
    see, and to send the same `feature_id` they do. This is the one field the save doesn't contain:
    the session derives each handle from the player-handle key, and neither view contains the key.
    The session removes a generated cache when the party takes it, and its entry leaves with it."""


def _masked_magic_item(instance: MagicItemInstance) -> dict:
    """One magic item as the player sees it, masked until identified.

    An unidentified item shows its category display name, and an enchanted arm shows one unit of
    its base instead, as in "a sword with a faint aura" or "an arrow with a faint aura", the
    concession made because *detect magic* exists. A display string built from that base weapon
    already includes the weapon's name, so the unidentified item also shows the `qualities` of
    the mundane weapon underneath it, exactly as an identified one does, and its `missile_ranges`
    when the rules give that weapon ranges. A weapon with no printed ranges, a sword among them,
    gets the `qualities` key and no `missile_ranges` key at all. Every magic staff reads "a
    staff", and every one is a mundane staff underneath, so the display is the name of the weapon
    and every staff shows the staff's `qualities`. A wand or other item whose display comes from its category shows
    neither field, because the display isn't the name of a weapon and the fields would single
    the item out among the items that show the same string.

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
        display = _with_article(_masked_names(instance)[0])
        if template.category in _MASKED_CATEGORY_NAMES and template.category is not MagicItemCategory.STAFF:
            # The display came from the category table, not the base weapon, so
            # attaching weapon facts here would single this one item out among the items in its
            # category that show the same string. "A staff" is the name of the weapon under every staff.
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
    members = tuple(_member_view(session, member) for member in session.party.members)
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
    features_here = _features_here_view(session, explored)
    visible_refs = _visible_cell_refs(session)
    known_piles = {ref: pile for ref, pile in session.dungeon_state.piles.items() if ref in visible_refs}
    piles = {ref: _pile_view(pile) for ref, pile in known_piles.items()}
    fatigued = any(session.ledger.active_on(member.id, FATIGUE_KIND) for member in session.party.members)
    exhausted = any(session.ledger.active_on(member.id, EXHAUSTED_KIND) for member in session.party.members)
    deprivation = {
        member_id: {"food_days": state.food_days, "water_days": state.water_days}
        for member_id, state in session.deprivation.items()
        if state.worst > 0
    }
    spell_names, reversed_spell_names, spell_levels = _spell_names(session, members, effects)
    item_names, item_names_plural, valuable_names, valuable_phrases = _item_names(session, known_piles.values())
    healing_rejections, healing_levels, mode_rejections = _town_healing_offers(session)
    return PlayerView(
        adventure_name=session.adventure.name,
        adventure_description=session.adventure.description,
        town_name=session.adventure.town.name,
        town_description=session.adventure.town.description,
        town_services=session.adventure.town.services,
        town_healing_prices={
            service: session.adventure.town.healing_prices.get(service, standard_gp)
            for service, (_, standard_gp) in HEALING_SERVICES.items()
        },
        town_healing_rejections=healing_rejections,
        town_healing_caster_levels=healing_levels,
        town_healing_mode_rejections=mode_rejections,
        town_healing_mode_names=_town_healing_mode_names(session),
        town_healing_funds_gp=_temple_funds_gp(session),
        party=members,
        location=session.dungeon_state.location,
        clock_rounds=session.clock.rounds,
        mode=session.mode.value,
        explored=explored,
        dungeons=tuple(_entered_dungeons(session)),
        can_travel_to_town=_can_travel_to_town(session),
        features_here=features_here,
        piles=piles,
        emptied_caches=tuple(session.dungeon_state.emptied_caches),
        effects=effects,
        spell_names=spell_names,
        reversed_spell_names=reversed_spell_names,
        spell_levels=spell_levels,
        item_names=item_names,
        item_names_plural=item_names_plural,
        valuable_names=valuable_names,
        valuable_phrases=valuable_phrases,
        fatigued=fatigued,
        exhausted=exhausted,
        deprivation=deprivation,
        journal=tuple(session.journal),
        quests=tuple(_quest_views(session)),
        encounter=_encounter_view(session),
        supplies=_supplies_view(session),
    )


def _town_healing_offers(
    session,
) -> tuple[
    dict[str, dict[str, tuple[Rejection, ...]]],
    dict[str, dict[str, int]],
    dict[str, dict[str, dict[str, tuple[Rejection, ...]]]],
]:
    """The view's `town_healing_rejections`, `town_healing_caster_levels`, and `town_healing_mode_rejections`.

    Each answer comes from `_temple_offer`, which asks `_healing_rejections`, the check `PurchaseHealing` makes after
    its funds check, so the view and the command can't disagree. The first two maps answer for each service's first
    usage, and the third for every usage of each service that `_temple_modes` gives more than one, reusing the first
    usage's answer. Outside the modes that accept `PurchaseHealing` every map is empty, and the view does no work for
    them.
    """
    if session.mode not in PurchaseHealing.allowed_modes:
        return {}, {}, {}
    spells = {service: _temple_spell(session, service) for service in HEALING_SERVICES}
    modes = {service: _temple_modes(spells[service]) for service in HEALING_SERVICES}
    rejections: dict[str, dict[str, tuple[Rejection, ...]]] = {}
    levels: dict[str, dict[str, int]] = {}
    by_mode: dict[str, dict[str, dict[str, tuple[Rejection, ...]]]] = {}
    for member in session.party.members:
        context = _cast_context(session, [member], in_combat=False)
        rejections[member.id] = {}
        levels[member.id] = {}
        by_mode[member.id] = {}
        for service in HEALING_SERVICES:
            first, *others = modes[service]
            level, refused = _temple_offer(session, member, service, spells[service], first, context)
            rejections[member.id][service] = tuple(refused)
            if level is not None:
                levels[member.id][service] = level
            if others:
                by_mode[member.id][service] = {first.key: tuple(refused)}
                for mode in others:
                    _, refused = _temple_offer(session, member, service, spells[service], mode, context)
                    by_mode[member.id][service][mode.key] = tuple(refused)
    return rejections, levels, by_mode


def _town_healing_mode_names(session) -> dict[str, dict[str, str]]:
    """The view's `town_healing_mode_names`: each usage's display name, for the services sold in more than one."""
    names: dict[str, dict[str, str]] = {}
    for service in HEALING_SERVICES:
        modes = _temple_modes(_temple_spell(session, service))
        if len(modes) > 1:
            names[service] = {mode.key: mode.display_name for mode in modes}
    return names


_TRACKED_SUPPLIES = frozenset({"torch", "lantern", "oil_flask", "tinder_box", "rations_standard", "rations_iron"})


def _supplies_view(session) -> SuppliesView:
    """The view's `supplies`, from one pass over the living members' packs and one over the effects ledger.

    `light_reserve_usable` asks `_light_carry_rejection`, the carrying check `LightSource` applies, so the two can't
    disagree. The flag leaves the mode out, and a client reads `PlayerView.mode` for that.
    """
    living = session.party.living_members()
    living_ids = {member.id for member in living}
    now = session.clock.rounds
    burning = 0
    permanent = False
    for effect in session.ledger.effects:
        if effect.target_ref not in living_ids or effect.definition.kind not in LIGHT_EFFECT_KINDS:
            continue
        if effect.definition.params.get("item_source") == "potion":
            continue
        if effect.expires_round is None:
            permanent = True
        else:
            burning = max(burning, effect.expires_round - now)
    open_flame = _party_open_flame(session)
    totals = dict.fromkeys(_TRACKED_SUPPLIES, 0)
    usable = False
    member_rations: list[tuple[str, int]] = []
    for member in living:
        counts = dict.fromkeys(_TRACKED_SUPPLIES, 0)
        for instance in member.inventory.all_instances():
            if isinstance(instance, MagicItemInstance):
                continue
            item_id = instance.template.id
            if item_id in counts:
                counts[item_id] += instance.quantity
        for item_id, count in counts.items():
            totals[item_id] += count
        member_rations.append((member.id, counts["rations_standard"] + counts["rations_iron"]))
        if not usable and not incapacitated(member):
            carried = {item_id for item_id, count in counts.items() if count > 0}
            usable = any(
                _light_carry_rejection(member.id, source, carried, open_flame=open_flame) is None
                for source in ("torch", "lantern")
            )
    reserve = totals["torch"] * _burn_rounds("torch")
    if totals["lantern"] > 0:
        reserve += totals["oil_flask"] * _burn_rounds("lantern")
    rations = totals["rations_standard"] + totals["rations_iron"]
    food_days = min((count for _, count in member_rations), default=0)
    return SuppliesView(
        light_burning_rounds=burning,
        light_permanent=permanent,
        light_reserve_rounds=reserve,
        light_reserve_usable=usable,
        torches=totals["torch"],
        oil_flasks=totals["oil_flask"],
        lanterns=totals["lantern"],
        rations=rations,
        rations_standard=totals["rations_standard"],
        rations_iron=totals["rations_iron"],
        food_days=food_days,
        food_days_shared=rations // len(living) if living else 0,
        food_short_first_ids=tuple(member_id for member_id, count in member_rations if count == food_days),
    )


def _spell_names(session, members, effects) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    """The view's `spell_names`, `reversed_spell_names`, and `spell_levels`, for the spell ids the view shows.

    Only the ids the view already shows are named: those in the members' books, memorized copies, and the two lists
    of what each member may prepare or learn, and the effect kinds. So a spell that the members can't choose stays
    hidden, such as one on a scroll or one on a list that the party's classes don't cast from. An id the session's
    catalog lacks, and an effect kind that is no spell's id, get no entry.
    """
    catalog = {spell.id: spell for spell in session.effective_spells.spells}
    shown = [
        *(spell_id for member in members for spell_id in member.spell_book),
        *(copy["spell_id"] for member in members for copy in member.memorized_spells),
        *(effect.kind for effect in effects),
        *(spell_id for member in members for spell_id in member.preparable_spells),
        *(spell_id for member in members for spell_id in member.learnable_spells),
    ]
    names: dict[str, str] = {}
    reversed_names: dict[str, str] = {}
    levels: dict[str, int] = {}
    for spell_id in shown:
        spell = catalog.get(spell_id)
        if spell is None or spell_id in names:
            continue
        names[spell_id] = spell.name
        levels[spell_id] = spell.level
        if spell.reversed_form is not None:
            reversed_names[spell_id] = spell.reversed_form.name
    return names, reversed_names, levels


def _pile_view(pile: DropPile) -> PileView:
    """One known pile as the players see it, with each entry's display string and its id and count."""
    magic_items = [_masked_magic_item(item) for item in pile.magic_items]
    return PileView(
        items=(
            *(f"{entry.item_id}×{entry.quantity}" for entry in pile.items),
            *(str(item.get("name", item.get("display"))) for item in magic_items),
            *(valuable.name.strip() or valuable.kind for valuable in pile.valuables),
        ),
        coins_gp_value=pile.coins.value_gp,
        coins=pile.coins,
        contents=(
            *(PileItemView(item_id=entry.item_id, quantity=entry.quantity) for entry in pile.items),
            *(PileItemView(item_id=item.instance_id, quantity=item.quantity) for item in pile.magic_items),
            *(PileItemView(item_id=valuable.instance_id) for valuable in pile.valuables),
        ),
    )


_VALUABLE_NAMES = {"gem": ("gem", "gems"), "jewellery": ("piece of jewellery", "pieces of jewellery")}
"""What the players call a valuable of each kind, whatever name the piece has."""


_ARTICLES = frozenset({"the", "a", "an"})
"""The first words that make a valuable's name a phrase already, in lowercase."""


def _valuable_phrase(valuable: ValuableInstance) -> str:
    """Return what to call a piece with a name of its own in the middle of a sentence.

    The view uses the piece's `phrase`, trimmed, unless it's empty or only whitespace. Otherwise a name whose first
    word is "the", "a", or "an" keeps its words with that first word in lowercase, and any other name gets its first
    character in lowercase and the indefinite article in front. The name is trimmed first.
    """
    if phrase := valuable.phrase.strip():
        return phrase
    name = valuable.name.strip()
    first = name.split(maxsplit=1)[0]
    if first.lower() in _ARTICLES:
        return first.lower() + name[len(first) :]
    return _with_article(name[0].lower() + name[1:])


def _item_names(
    session, piles: Iterable[DropPile]
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    # The view's `item_names`, `item_names_plural`, `valuable_names`, and `valuable_phrases`, in one walk over what
    # the view shows.
    # Each id the inventories and the known piles show gets one entry, from the first place it appears. A
    # carried stack is named from the template it embeds, which the session gave the catalog's names, and a
    # pile's stack from the session's catalog, which has the items the adventure bundles. An unidentified magic
    # item keeps its mask, and a template id is a key only for an identified item a member carries, because a
    # pile shows a magic item by its instance id alone.
    names: dict[str, str] = {}
    plurals: dict[str, str] = {}
    valuable_names: dict[str, str] = {}
    valuable_phrases: dict[str, str] = {}
    equipment = session.effective_equipment
    catalog = {
        template.id: template
        for template in (*equipment.weapons, *equipment.armour, *equipment.gear, *equipment.ammunition)
    }

    def add(item_id: str, singular: str, plural: str) -> None:
        if item_id not in names:
            names[item_id] = singular
            plurals[item_id] = plural

    def add_valuable(valuable: ValuableInstance) -> None:
        add(valuable.instance_id, *_VALUABLE_NAMES[valuable.kind])
        # A piece has a name of its own unless its trimmed name is empty or the one treasure generation gives its
        # kind.
        name = valuable.name.strip()
        if name and name != _ROLLED_VALUABLE_NAMES[valuable.kind]:
            valuable_names[valuable.instance_id] = name
            valuable_phrases[valuable.instance_id] = _valuable_phrase(valuable)

    def add_magic_item(instance: MagicItemInstance, *, carried: bool) -> None:
        if instance.identified:
            template = magic_item_template(instance)
            add(instance.instance_id, template.display_name, template.display_name_plural)
            if carried:
                add(instance.template_id, template.display_name, template.display_name_plural)
        else:
            add(instance.instance_id, *_masked_names(instance))

    for member in session.party.members:
        for instance in member.inventory.all_instances():
            if isinstance(instance, MagicItemInstance):
                add_magic_item(instance, carried=True)
            else:
                add(instance.template.id, instance.template.display_name, instance.template.display_name_plural)
        for valuable in member.inventory.valuables:
            add_valuable(valuable)
    for pile in piles:
        for entry in pile.items:
            template = catalog.get(entry.item_id)
            if template is not None:
                add(entry.item_id, template.display_name, template.display_name_plural)
        for instance in pile.magic_items:
            add_magic_item(instance, carried=False)
        for valuable in pile.valuables:
            add_valuable(valuable)
    return names, plurals, valuable_names, valuable_phrases


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


def _entered_dungeons(session):
    entered_ids: set[str] = set()
    for key in session.dungeon_state.explored:
        try:
            dungeon_id, level_text = key.rsplit(":", 1)
            int(level_text)
        except TypeError, ValueError:
            continue
        entered_ids.add(dungeon_id)
    for dungeon in session.adventure.dungeons:
        if dungeon.id in entered_ids:
            yield DungeonNameView(id=dungeon.id, name=dungeon.name)


def _trap_state(session, ref: str, trapped: bool) -> Literal["found", "sprung", "removed"] | None:
    if not trapped:
        return None
    state = session.dungeon_state
    if ref in state.removed_traps:
        return "removed"
    if ref in state.sprung_traps:
        return "sprung"
    if ref in state.found_traps:
        return "found"
    return None


def _features_here_view(session, explored) -> tuple[str, ...]:
    """The handles of the caches the feature commands accept here, in the current level's feature order.

    The handles come from the resolution the commands run, and the function keeps only the features
    the view lists, so the list never shows a handle the players can't already see.
    """
    accepted = _current_cache_handles(session)
    if not accepted:
        return ()
    location = session.dungeon_state.location
    for level in explored:
        if (level.dungeon_id, level.level_number) == (location.dungeon_id, location.level_number):
            return tuple(feature.id for feature in level.features if feature.id in accepted)
    return ()


def _authored_feature_views(session, dungeon_id, level_number, level, known, entered_area_ids):
    features = []
    visible_features = [
        *((None, feature) for feature in level.features),
        *((area, feature) for area in level.areas for feature in area.features),
    ]
    for area, feature in visible_features:
        if feature.cell is None:
            if area is None or area.id not in entered_area_ids:
                continue
        else:
            if feature.cell not in known:
                continue
            if area is not None and level.area_at(feature.cell) is not area:
                continue
        ref = f"{dungeon_id}:{level_number}:{feature.id}"
        if feature.kind == "construction_trick" and ref not in session.dungeon_state.found_tricks:
            continue
        features.append(
            MapFeatureView(
                id=_authored_feature_id(session, dungeon_id, level_number, feature.id),
                kind=feature.kind,
                description=feature.description,
                cell=feature.cell,
                area_id=area.id if area is not None and area.id in entered_area_ids else None,
                trap_state=_trap_state(session, ref, feature.trap is not None),
                emptied=feature.kind == "treasure_cache" and ref in session.dungeon_state.emptied_caches,
            )
        )
    return features


def _generated_feature_views(session, dungeon_id, level_number, level, known, entered_area_ids):
    features = []
    for cache_id, cache in session.dungeon_state.generated_caches.items():
        location = _parse_cell_location(session, cache.cell_ref)
        if location is None or location[:2] != (dungeon_id, level_number):
            continue
        if location[2] not in known or not _cache_available(session, cache):
            continue
        area = level.area_at(location[2])
        features.append(
            MapFeatureView(
                id=_generated_feature_id(session, dungeon_id, level_number, cache_id),
                kind="generated_cache",
                description="",
                cell=location[2],
                area_id=area.id if area is not None and area.id in entered_area_ids else None,
                trap_state=None,
            )
        )
    return sorted(features, key=lambda feature: feature.id)


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
        known = set(visible)
        walked = set(dungeon_state.explored.get(key, []))
        areas = []
        entered_area_ids = set()
        for area in level.areas:
            walked_cells = tuple(cell for cell in area.cells if cell in walked and level.area_at(cell) is area)
            if not walked_cells:
                continue
            entered_area_ids.add(area.id)
            areas.append(
                EnteredAreaView(
                    id=area.id,
                    name=area.name,
                    description=area.description,
                    cells=walked_cells,
                    trap_state=_trap_state(session, f"{dungeon_id}:{level_number}:{area.id}", area.trap is not None),
                )
            )
        features = [
            *_authored_feature_views(session, dungeon_id, level_number, level, known, entered_area_ids),
            *_generated_feature_views(session, dungeon_id, level_number, level, known, entered_area_ids),
        ]
        transitions = tuple(
            TransitionView(position=transition.position, kind=transition.kind, side=transition.side)
            for transition in level.transitions
            if transition.position in known
        )
        entrance = level.entrance if level.entrance in known else None
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
        yield ExploredLevelView(
            dungeon_id=dungeon_id,
            level_number=level_number,
            cells=tuple(visible),
            edges=edges,
            areas=tuple(areas),
            features=tuple(features),
            transitions=transitions,
            entrance=entrance,
        )


def _canonical_edge(cell: Position, direction: Direction) -> str:
    from osrlib.crawl.dungeon import edge_key

    return edge_key(cell, direction)


def _member_view(session, member) -> MemberView:
    """One member's sheet, read from the live character."""
    from osrlib.crawl._spells import _learnable_spells, _preparable_spells, _prepare_now

    definition = member.definition
    profile = caster_profile(definition)
    can_prepare_now, needs_sleep_to_prepare = _prepare_now(session, member, profile)
    thief_skills = {}
    if definition.thief_skills:
        thief_skills = definition.thief_skills[member.level - 1].model_dump(exclude={"level"})
    shown = _armour_as_tested(member)
    return MemberView(
        id=member.id,
        name=member.name,
        class_id=member.class_id,
        class_name=definition.name,
        level=member.level,
        current_hp=member.current_hp,
        max_hp=member.max_hp,
        conditions=tuple(active.condition.value for active in member.conditions),
        inventory=_masked_inventory(member),
        memorized_spells=tuple(copy.model_dump(mode="json") for copy in member.memorized_spells),
        encounter_rate_feet=_encounter_rate_feet(session, member),
        race=member.race,
        alignment=member.alignment.value,
        title=level_title(definition, member.level),
        xp=member.xp,
        xp_next_level=definition.row(member.level + 1).xp if member.level < definition.max_level else None,
        scores={ability.value: member.scores[ability] for ability in AbilityScore},
        effective_strength=member.effective_strength,
        melee_modifier=member.melee_modifier,
        missile_modifier=member.missile_modifier,
        initiative_modifier=member.initiative_modifier,
        hit_point_modifier=member.hit_point_modifier,
        magic_save_modifier=member.magic_save_modifier,
        npc_reaction_modifier=member.npc_reaction_modifier,
        open_doors_chance=member.open_doors_chance,
        thac0=member.thac0,
        attack_bonus=member.attack_bonus,
        armour_class=shown.armour_class,
        armour_class_ascending=shown.armour_class_ascending,
        saves=member.saves.model_dump(),
        movement_rate_feet=member.movement_rate(session.ruleset),
        languages=member.languages,
        spell_book=member.spell_book,
        spell_slots=definition.row(member.level).spell_slots,
        preparable_spells=_preparable_spells(session, member),
        learnable_spells=_learnable_spells(session, member),
        reverses_at=None if profile is None else _reverses_at(profile),
        can_prepare_now=can_prepare_now,
        needs_sleep_to_prepare=needs_sleep_to_prepare,
        thief_skills=thief_skills,
    )


def _encounter_rate_feet(session, member) -> int:
    """The member's encounter rate in feet per round, doubled by *haste*, as a battle moves them."""
    from osrlib.crawl.battle import _encounter_rate, _haste_multiplier

    return _encounter_rate(member, session) * _haste_multiplier(session, member.id, "movement_multiplier")


def _encounter_view(session) -> EncounterView | None:
    from osrlib.core.combat import cannot_move
    from osrlib.crawl.battle import (
        _able_declarers,
        _back_stabbers,
        _formation_width,
        _front_rank_toward_nearest,
        _group_lit,
        _hide_rejection,
        _members_seeing,
        _placement,
        _reachable_front_rank,
        _signed_distance,
    )

    state = session.encounter
    if state is None:
        return None
    battle = session.battle
    placement = _placement(session)
    living_ids = [member.id for member in session.party.living_members()]
    groups = []
    for group in state.groups:
        members = [session.combatant(monster_id) for monster_id in group.monster_ids]
        living = [monster for monster in members if not has_condition(monster, Condition.DEAD)]
        held = [{active.condition.value for active in monster.conditions} for monster in living]
        conditions = sorted(set().union(*held))
        # The family and the categories describe every member the players saw, so the dead count too.
        kinds = [_member_kind(member) for member in members]
        first_family, first_categories = kinds[0]
        family_id = first_family if all(family == first_family for family, _ in kinds) else None
        categories = tuple(category for category in first_categories if all(category in other for _, other in kinds))
        # Positions, reach, and sight exist only in a battle. Each reads the party's own state and the group's
        # distance, which the view already shows, so none of them tells the players anything new about the group.
        distances: dict[str, int] = {}
        front_rank: tuple[str, ...] = ()
        lit: bool | None = None
        seen_by: tuple[str, ...] = ()
        if battle is not None:
            distances = {member_id: _signed_distance(session, member_id, group) for member_id in living_ids}
            front_rank = tuple(_reachable_front_rank(group, placement))
            lit = _group_lit(session, group, placement)
            seen_by = tuple(_members_seeing(session, group, placement, lit=lit))
        groups.append(
            EncounterGroupView(
                id=group.id,
                label=group.label,
                label_plural=group.label_plural,
                family_id=family_id,
                categories=categories,
                count=len(living),
                distance_feet=group.distance_feet,
                visible_conditions=tuple(conditions),
                condition_counts={condition: sum(condition in values for values in held) for condition in conditions},
                fled=group.fled,
                fleeing=group.fleeing,
                member_distance_feet=distances,
                front_rank=front_rank,
                lit=lit,
                seen_by=seen_by,
                members=tuple(
                    EncounterMemberView(
                        id=member.id,
                        status=(
                            "defeated" if has_condition(member, Condition.DEAD) else "fled" if group.fled else "alive"
                        ),
                    )
                    for member in members
                ),
            )
        )
    declarers = _able_declarers(session)
    fired = battle.fired_last_round if battle is not None and session.ruleset.weapon_reload else ()
    can_hide: tuple[str, ...] = ()
    hiding: tuple[str, ...] = ()
    can_back_stab: tuple[str, ...] = ()
    if battle is not None:
        # The lists come from the checks the round runs, so the view never offers what the session refuses.
        can_hide = tuple(member.id for member in declarers if _hide_rejection(session, member, placement) is None)
        # The player view lists who is hiding. How each roll went stays in `BattleState.hiding`, for the referee.
        hiding = tuple(member.id for member in session.party.members if member.id in battle.hiding)
        can_back_stab = _back_stabbers(session, declarers)
    return EncounterView(
        groups=tuple(groups),
        stance=state.stance,
        in_battle=battle is not None,
        battle_round=battle.round if battle is not None else None,
        pursuit_gap_feet=state.pursuit.gap_feet if state.pursuit is not None else None,
        declarers=tuple(member.id for member in declarers),
        front_rank=tuple(_front_rank_toward_nearest(session, placement)),
        immobile=tuple(member.id for member in declarers if cannot_move(member)),
        reloading=tuple(fired),
        can_hide=can_hide,
        hiding=hiding,
        can_back_stab=can_back_stab,
        advance_feet=(
            {member_id: battle.advance_feet.get(member_id, 0) for member_id in living_ids} if battle is not None else {}
        ),
        formation_width=_formation_width(session),
        party_free_round=battle is not None and battle.monsters_hold_rounds > 0,
        # The answers come from the check the handler runs, so the view never offers a drop the session refuses.
        evade_rejections=(
            {drop: tuple(_evade_rejections(session, drop)) for drop in _EVADE_DROPS}
            if session.mode in Evade.allowed_modes
            else {}
        ),
    )


def build_referee_view(session) -> RefereeView:
    """Build the referee view from a save's groups, except its seed, streams, and handle key.

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
            master seed, the random streams' positions, and the player-handle key, with each
            feature's player handle in `feature_handles`.

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
        feature_handles=_session_feature_handles(session),
    )
