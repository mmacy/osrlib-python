"""Player characters: the model, the functions that create one, and the documents that store one.

Start here when you need a party. [`create_character`][osrlib.core.character.create_character] is
the entry point. Pass it a name, a class id, an alignment, a [`Ruleset`][osrlib.core.ruleset.Ruleset],
and a seeded random-number stream from [`osrlib.core.rng`][osrlib.core.rng]. It returns a
first-level [`Character`][osrlib.core.character.Character] and the raw dice it rolled. Put the
characters you create into a [`Party`][osrlib.crawl.party.Party] to play them, or pass them to the
combat, magic, and item functions in [`osrlib.core`][osrlib.core] on their own.

[`Character`][osrlib.core.character.Character] is a mutable pydantic model. Its derived values are
properties computed from the stored fields each time you read them. They include THAC0 (the d20
roll needed to hit armour class 0), the saving throws, the ability modifiers, both armour classes,
and the language list. Validation on the model is
structural: the scores are in range, the level is within the class's maximum, and current hit
points are no higher than maximum. The model can't check whether a character was created legally,
because a finished character has no record of the choices that made it. The creation functions
check each choice when it's made.

To create a character one step at a time while a player makes the choices, call these functions
in the order the SRD gives for creating a character:

1. [`roll_ability_scores`][osrlib.core.character.roll_ability_scores] rolls the six ability scores.
2. [`validate_class_choice`][osrlib.core.character.validate_class_choice] checks the scores against
   the class the player picks.
3. [`validate_adjustment`][osrlib.core.abilities.validate_adjustment] and
   [`apply_adjustment`][osrlib.core.abilities.apply_adjustment] check and apply a trade of ability
   points, if the player wants one.
4. [`validate_starting_spells`][osrlib.core.character.validate_starting_spells] checks an arcane
   caster's first spell.
5. [`roll_hit_points`][osrlib.core.character.roll_hit_points] rolls first-level hit points.
6. [`validate_extra_languages`][osrlib.core.character.validate_extra_languages] checks the extra
   languages a high INT allows.
7. [`roll_starting_gold`][osrlib.core.character.roll_starting_gold] rolls the starting money.

Then construct the [`Character`][osrlib.core.character.Character] from the results. Pass the
scores as `scores`, the hit points as both `max_hp` and `current_hp`, `level=1`, `xp=0`, the class
definition's `race`, and the name, class id, alignment, and extra languages. To use scores a player
chose instead of rolled ones, skip step 1 and pass those scores. Write the spell
book with [`choose_starting_spells`][osrlib.core.character.choose_starting_spells], and buy and
equip gear with [`validate_purchase`][osrlib.core.items.validate_purchase],
[`purchase`][osrlib.core.items.purchase], and [`equip`][osrlib.core.items.equip]. Each validating
function returns a list of [`Rejection`][osrlib.core.validation.Rejection] records rather than
raising, so you can show the player what went wrong and let them choose again.
[`create_character`][osrlib.core.character.create_character] runs the same steps in one call when
every choice is known in advance, and raises `ValueError` on the first illegal choice instead. No
creation function emits events, because creation happens before a session starts.

Advancement is in [`osrlib.core.classes`][osrlib.core.classes]: experience awards, leveling up, and
energy drain, which takes levels away. To store a character on its own, call
[`to_document`][osrlib.core.character.Character.to_document]. To store a group of characters, call
[`party_to_document`][osrlib.core.character.party_to_document]. To store a whole game in progress,
characters included, call [`save_game`][osrlib.persistence.save_game].

Creation and advancement draw from two separate streams, keyed by
[`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM] and
[`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]. A stream key is the name of one
independent random-number sequence in an [`RngStreams`][osrlib.core.rng.RngStreams] set. The two
streams are separate so that a change to the creation rules never changes the level-up rolls in a
save you've already made.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.party import Party

streams = RngStreams(master_seed=2)
stream = streams.get(CHARACTER_CREATION_STREAM)
result = create_character(
    name="Rurik",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=Ruleset(),
    stream=stream,
    purchases=[("sword", 1), ("leather", 1)],
    equip_ids=["sword", "leather"],
)
party = Party(members=[result.character])
print(party.members[0].name, party.members[0].max_hp, party.members[0].armour_class)
# Rurik 8 6
```
"""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrlib.core.abilities import (
    MAX_SCORE,
    MIN_SCORE,
    AbilityAdjustment,
    AbilityScore,
    AbilityTables,
    apply_adjustment,
)
from osrlib.core.alignment import Alignment
from osrlib.core.classes import ClassDefinition, SavingThrows
from osrlib.core.dice import RollResult, roll
from osrlib.core.effects import ActiveCondition, ActiveModifier
from osrlib.core.items import Inventory, ItemInstance, equip, movement_rate_feet, purchase, validate_purchase
from osrlib.core.rng import RngStream, StreamName
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell, SpellCatalog, caster_profile
from osrlib.core.validation import Rejection
from osrlib.data import load_ability_tables, load_classes, load_equipment, load_languages, load_spells
from osrlib.errors import ContentValidationError
from osrlib.versioning import check_document, stamp_document

__all__ = [
    "ABILITY_ROLL_ORDER",
    "ADVANCEMENT_STREAM",
    "CHARACTER_CREATION_STREAM",
    "AbilityScoreRolls",
    "Character",
    "CharacterCreationResult",
    "HitPointRoll",
    "choose_starting_spells",
    "create_character",
    "party_from_document",
    "party_to_document",
    "roll_ability_scores",
    "roll_hit_points",
    "roll_starting_gold",
    "validate_class_choice",
    "validate_extra_languages",
    "validate_starting_spells",
]

CHARACTER_CREATION_STREAM = StreamName.CHARACTER_CREATION
"""The stream key for character-creation draws, `"character_creation"`.

A stream key is the name of one independent random-number sequence in an
[`RngStreams`][osrlib.core.rng.RngStreams] set. Pass `streams.get(CHARACTER_CREATION_STREAM)` as the
`stream` argument of [`create_character`][osrlib.core.character.create_character] and of the
stepwise creation functions. `create_character` draws the ability scores, the first-level hit
points, and the starting gold from it, in that order.

To roll a whole party from one seed, pass the same stream to each call. Each character's draws
continue where the previous character's stopped, so the same seed and the same calls always give
the same party.
"""

ADVANCEMENT_STREAM = StreamName.ADVANCEMENT
"""The stream key for advancement draws, `"advancement"`: the hit dice rolled when a level is gained or lost.

Pass `streams.get(ADVANCEMENT_STREAM)` as the `stream` argument of
[`apply_xp`][osrlib.core.classes.apply_xp], [`level_up`][osrlib.core.classes.level_up], and
[`drain_levels`][osrlib.core.classes.drain_levels]. A
[`GameSession`][osrlib.crawl.session.GameSession] uses this key when it awards experience and when
it resolves energy drain.

It is separate from
[`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM]. A change to the
creation rules can change how many draws creation takes. Because the streams are separate, that
change doesn't change the advancement rolls in a save you've already made.
"""

ABILITY_ROLL_ORDER = (
    AbilityScore.STR,
    AbilityScore.INT,
    AbilityScore.WIS,
    AbilityScore.DEX,
    AbilityScore.CON,
    AbilityScore.CHA,
)
"""The order in which [`roll_ability_scores`][osrlib.core.character.roll_ability_scores] rolls the six abilities.

The order is STR, INT, WIS, DEX, CON, CHA, as the SRD lists them. Read it to show rolled scores in
the order the dice came up, or to match recorded rolls to their abilities by hand. The same stream
always gives the same six scores because the order never changes, so don't reassign this name.
"""


class Character(BaseModel):
    """A player character: the stored state of one character, and the values derived from it.

    Get one from [`create_character`][osrlib.core.character.create_character], or from
    [`from_document`][osrlib.core.character.Character.from_document] when you load a stored
    character. Construct one directly when you create a character step by step with the other
    creation functions in this module, or when you already have every value, as in a test.

    Put characters in a [`Party`][osrlib.crawl.party.Party] to explore with them. Pass one to the
    functions in [`osrlib.core.combat`][osrlib.core.combat] as an attacker or a target, to the
    functions in [`osrlib.core.spells`][osrlib.core.spells] as a caster, and to
    [`apply_xp`][osrlib.core.classes.apply_xp] or [`level_up`][osrlib.core.classes.level_up] to
    advance it. A [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] has the same combat
    attributes, so the combat functions take either.

    The model is mutable and validates on assignment. Setting a field to a value that breaks a rule
    raises a pydantic `ValidationError`. Validation is structural only. It checks that the six scores
    are present and in range, that `class_id` is the id of a class in
    [`load_classes`][osrlib.data.load_classes], that the level is within the class's maximum, and
    that current hit points don't exceed maximum hit points. It doesn't check that the character was
    created legally, because a finished character has no record of how it was created.

    Nothing derived is stored. THAC0, attack bonus, saving throws, ability modifiers, both armour
    classes, and the language list are properties recomputed from the stored fields each time you
    read them, so a level change or a swapped piece of armour shows up at once.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset

        stream = RngStreams(master_seed=2).get(CHARACTER_CREATION_STREAM)
        character = create_character(
            name="Rurik",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=stream,
        ).character
        print(character.level, character.thac0, character.saves.death)
        # 1 19 12
        character.current_hp -= 3
        print(character.current_hp, character.max_hp)
        # 5 8
        ```
    """

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    id: str | None = None
    """The character's id within a game, like `"character-0001"`, or `None` before a session assigns one.

    [`GameSession.new`][osrlib.crawl.session.GameSession.new] assigns an id to each party member whose id is `None`,
    from the session's [`IdAllocator`][osrlib.core.monsters.IdAllocator]. Commands refer to a character by this id. An
    event about a character with no id uses the character's `name` instead.
    """

    name: str = Field(min_length=1)
    """The character's name, for display. It can't be empty."""

    class_id: str
    """The id of the character's class, like `"fighter"`.

    Valid ids come from [`load_classes`][osrlib.data.load_classes] and are listed in
    [the class id index](https://mmacy.github.io/osrlib-python/reference/content-ids/classes/). Read
    [`definition`][osrlib.core.character.Character.definition] for the class itself.
    """

    race: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    """The character's race, like `"human"` or `"dwarf"`, as a lowercase identifier.

    Creation copies it from the class's [`race`][osrlib.core.classes.ClassDefinition.race]. No rules function reads it.
    What a race can do, like a dwarf's infravision, comes from the class's
    [`abilities`][osrlib.core.classes.ClassDefinition.abilities].
    """

    level: int = Field(ge=1)
    """The experience level, 1 through the class's maximum."""

    xp: int = Field(ge=0)
    """The character's total experience points.

    Award experience with [`apply_xp`][osrlib.core.classes.apply_xp], which adds to this total and levels the character
    up when it reaches the next level's threshold. Assigning a new total here doesn't change the level.
    """

    scores: dict[AbilityScore, int]
    """The six ability scores, each 3 through 18, keyed by [`AbilityScore`][osrlib.core.abilities.AbilityScore]. All six
    must be present.
    """

    alignment: Alignment
    """Lawful, neutral, or chaotic. It sets the secret language in
    [`alignment_tongue`][osrlib.core.character.Character.alignment_tongue].
    """

    extra_languages: tuple[str, ...] = ()
    """The language ids chosen at creation because of a high INT. Check the choice with
    [`validate_extra_languages`][osrlib.core.character.validate_extra_languages] before you store it.
    """

    max_hp: int = Field(ge=1)
    """Maximum hit points, at least 1."""

    current_hp: int = Field(ge=0)
    """Current hit points, from 0 up to `max_hp`.

    A character at 0 hit points is out of the fight. Whether a character is dead is the `dead` condition, which
    [`kill`][osrlib.core.effects.kill] applies, not a hit point value.
    """

    inventory: Inventory = Field(default_factory=Inventory)
    """Everything the character carries, wears, and wields, and their coins. See
    [`Inventory`][osrlib.core.items.Inventory] and the buying and equipping functions in
    [`osrlib.core.items`][osrlib.core.items].
    """

    carrying_treasure: bool = False
    """Whether the character is carrying enough treasure to slow them down, under basic encumbrance.

    The basic encumbrance rules leave the amount to the referee, so osrlib leaves it to you. Set this flag, and
    [`movement_rate`][osrlib.core.character.Character.movement_rate] drops the character to the next slower rate.
    The other encumbrance modes ignore it.
    """

    conditions: tuple[ActiveCondition, ...] = ()
    """The conditions in effect, like poisoned or paralysed.

    Apply and clear them with the functions in [`osrlib.core.effects`][osrlib.core.effects]. Test for one with
    [`has_condition`][osrlib.core.effects.has_condition].
    """

    stat_modifiers: tuple[ActiveModifier, ...] = ()
    """Timed bonuses and penalties from spells and items. The functions in
    [`osrlib.core.effects`][osrlib.core.effects] apply and remove them.
    """

    spell_book: tuple[str, ...] = ()
    """The spell ids in an arcane caster's spell book, in the order they were added.

    An arcane caster memorizes spells only from this book. It's empty for divine casters like the cleric, who memorize
    from their whole class list, and for classes that cast no spells.
    """

    memorized_spells: tuple[MemorizedSpell, ...] = ()
    """The spells the caster has memorized and can cast, as [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell]
    records in the order they were memorized.

    Fill it with [`memorize_spells`][osrlib.core.spells.memorize_spells]. Casting spends the first matching copy in
    the list, and energy drain removes copies from the end. How many spells of each level fit comes from the class's
    progression row for the current level, so it changes when the level does.
    """

    @field_validator("scores")
    @classmethod
    def _all_six_scores_in_range(cls, value: dict[AbilityScore, int]) -> dict[AbilityScore, int]:
        missing = [ability for ability in AbilityScore if ability not in value]
        if missing:
            raise ValueError(f"scores must include all six abilities; missing {missing}")
        for ability, score in value.items():
            if not MIN_SCORE <= score <= MAX_SCORE:
                raise ValueError(f"{ability} must be in {MIN_SCORE}-{MAX_SCORE}, got {score}")
        return value

    @model_validator(mode="after")
    def _structurally_consistent(self) -> Character:
        definition = self.definition
        if self.level > definition.max_level:
            raise ValueError(f"{self.class_id} is capped at level {definition.max_level}, got {self.level}")
        if self.current_hp > self.max_hp:
            raise ValueError(f"current hp {self.current_hp} exceeds max hp {self.max_hp}")
        return self

    @property
    def definition(self) -> ClassDefinition:
        """The character's class, looked up by `class_id` in [`load_classes`][osrlib.data.load_classes].

        Read it for anything that depends on the class: the progression table, the armour and weapon
        policies, the class abilities, and the level titles. Pass it as the `definition` argument of the
        functions in [`osrlib.core.classes`][osrlib.core.classes].
        [`load_classes`][osrlib.data.load_classes] loads the catalog once and caches it, so
        reading this property again costs almost nothing.
        """
        return load_classes().get(self.class_id)

    @property
    def thac0(self) -> int:
        """The d20 roll the character needs to hit armour class 0, from the current level's progression row.

        THAC0 goes with descending armour class, where a lower armour class is better. If you show
        ascending armour class instead, use
        [`attack_bonus`][osrlib.core.character.Character.attack_bonus]. The two describe the same
        attack.
        """
        return self.definition.row(self.level).thac0

    @property
    def attack_bonus(self) -> int:
        """The bonus added to an attack roll under ascending armour class, from the current level's progression row.

        It's [`thac0`][osrlib.core.character.Character.thac0] for ascending armour class, where a
        higher armour class is better. Both come from the same row, so they always agree.
        """
        return self.definition.row(self.level).attack_bonus

    @property
    def saves(self) -> SavingThrows:
        """The five saving throw targets for the current level, from the progression row.

        A save succeeds on a d20 roll equal to or higher than the target for the kind of danger.
        [`saving_throw`][osrlib.core.combat.saving_throw] rolls one for you. The targets change as
        soon as the level does.
        """
        return self.definition.row(self.level).saves

    def _tables(self) -> AbilityTables:
        return load_ability_tables()

    @property
    def melee_modifier(self) -> int:
        """The STR modifier added to melee attack rolls and to melee damage, from −3 to +3."""
        return self._tables().melee_modifier(self.scores[AbilityScore.STR])

    @property
    def open_doors_chance(self) -> int:
        """The chance in 6 of forcing a stuck door open, from STR.

        Roll 1d6, and the door opens on a roll equal to or less than this number. To roll it, pass
        it to [`detection_check`][osrlib.core.classes.detection_check]. In a session, the engine
        reads it for you when it resolves a [`ForceDoor`][osrlib.crawl.commands.ForceDoor]
        command.
        """
        return self._tables().open_doors_chance(self.scores[AbilityScore.STR])

    @property
    def missile_modifier(self) -> int:
        """The DEX modifier added to missile attack rolls, from −3 to +3. It doesn't change damage."""
        return self._tables().missile_modifier(self.scores[AbilityScore.DEX])

    @property
    def initiative_modifier(self) -> int:
        """The DEX modifier to an individual initiative roll, from −2 to +2.

        It applies only when the `individual_initiative` flag of the
        [`Ruleset`][osrlib.core.ruleset.Ruleset] is on. With group initiative, the default, each
        side rolls once, and this modifier doesn't apply.
        """
        return self._tables().initiative_modifier(self.scores[AbilityScore.DEX])

    @property
    def hit_point_modifier(self) -> int:
        """The CON modifier added to each hit die rolled, from −3 to +3.

        [`roll_hit_points`][osrlib.core.character.roll_hit_points] and
        [`level_up`][osrlib.core.classes.level_up] add it to each die, and a die always gains at
        least 1 hit point, however low the CON.
        """
        return self._tables().hit_point_modifier(self.scores[AbilityScore.CON])

    @property
    def magic_save_modifier(self) -> int:
        """The WIS modifier applied to saving throws against magical effects, from −3 to +3."""
        return self._tables().magic_save_modifier(self.scores[AbilityScore.WIS])

    @property
    def npc_reaction_modifier(self) -> int:
        """The CHA modifier to a monster reaction roll when this character talks to the monsters, from −2 to +2.

        Pass it as the `modifier` of [`roll_reaction`][osrlib.core.combat.roll_reaction]. In a
        session, the engine applies the speaker's modifier for you when it resolves a
        [`Parley`][osrlib.crawl.commands.Parley] command.
        """
        return self._tables().npc_reaction_modifier(self.scores[AbilityScore.CHA])

    @property
    def alignment_tongue(self) -> str:
        """The secret language everyone of this alignment speaks, as a language id.

        Every character speaks the tongue of their alignment and no other. The id is
        `alignment_` followed by the alignment's value, so a lawful character speaks
        `"alignment_lawful"`. These ids aren't in the language catalog that
        [`load_languages`][osrlib.data.load_languages] returns. This property builds the id from
        `alignment`, so a change of alignment changes the tongue at once.
        """
        return f"alignment_{self.alignment.value}"

    @property
    def languages(self) -> tuple[str, ...]:
        """Every language the character speaks, as language ids.

        The alignment tongue comes first. The languages the class grants come next, starting with
        `"common"`. The extra languages chosen at creation come last. To find out whether two
        characters can talk to each other, look for an id in both lists. Every id except the
        alignment tongue is a catalog id from [`load_languages`][osrlib.data.load_languages], listed
        in [the language id index](https://mmacy.github.io/osrlib-python/reference/content-ids/languages/).
        """
        return (self.alignment_tongue, *self.definition.languages, *self.extra_languages)

    def _armour_parts(self, *, ascending: bool) -> tuple[int, int]:
        """Return the worn armour's base armour class and the total bonus from shields and items.

        Magic armour adds its enchantment to the base item's printed armour class, except for the
        cursed forms, which set the base outright. A magic shield adds its enchantment on top of
        the +1 an ordinary shield gives. A worn item with an armour class bonus that is always
        active, like a ring of protection, adds to the bonus too.
        """
        from osrlib.core.items import ArmourTemplate, MagicItemInstance, magic_item_template
        from osrlib.data import load_equipment

        base = 10 if ascending else 9
        worn = self.inventory.worn_armour
        if isinstance(worn, MagicItemInstance):
            template = magic_item_template(worn)
            if template.ac_set is not None and template.ac_set_ascending is not None:
                base = template.ac_set_ascending if ascending else template.ac_set
            elif worn.base_item_id is not None:
                mundane = load_equipment().get(worn.base_item_id)
                if isinstance(mundane, ArmourTemplate) and mundane.ac is not None and mundane.ac_ascending is not None:
                    printed = mundane.ac_ascending if ascending else mundane.ac
                    base = printed + template.ac_bonus if ascending else printed - template.ac_bonus
        elif worn is not None and isinstance(worn.template, ArmourTemplate) and worn.template.ac is not None:
            printed_ascending = worn.template.ac_ascending
            base = printed_ascending if ascending and printed_ascending is not None else worn.template.ac
        bonus = 0
        shield = self.inventory.shield
        if isinstance(shield, MagicItemInstance):
            template = magic_item_template(shield)
            if template.ac_set is not None and template.ac_set_ascending is not None:
                # The cursed shield's AC 9 [10] sets the wearer's base outright.
                base = template.ac_set_ascending if ascending else template.ac_set
            else:
                mundane_shield = load_equipment().get("shield")
                mundane_bonus = (getattr(mundane_shield, "ac_bonus", None) or 0) if mundane_shield else 0
                bonus += mundane_bonus + template.ac_bonus
        elif (
            shield is not None and isinstance(shield.template, ArmourTemplate) and shield.template.ac_bonus is not None
        ):
            bonus += shield.template.ac_bonus
        for ring in self.inventory.rings:
            template = magic_item_template(ring)
            if template.always_active:
                bonus += template.ac_bonus
        return base, bonus

    @property
    def armour_class(self) -> int:
        """The character's descending armour class, where lower is better.

        An unarmoured character is 9. Worn armour sets the base. A shield's bonus, the bonus of a
        worn ring that protects all the time, and the DEX modifier each subtract from it. It's
        computed from the inventory each time you read it, so equipping or removing armour changes
        it at once. If you show ascending armour class, use
        [`armour_class_ascending`][osrlib.core.character.Character.armour_class_ascending].
        """
        dex_modifier = self._tables().ac_modifier(self.scores[AbilityScore.DEX])
        base, bonus = self._armour_parts(ascending=False)
        return base - bonus - dex_modifier

    @property
    def armour_class_ascending(self) -> int:
        """The character's ascending armour class, where higher is better.

        An unarmoured character is 10, and every bonus adds to it. It describes the same defense
        as [`armour_class`][osrlib.core.character.Character.armour_class], so the two always
        agree.
        """
        dex_modifier = self._tables().ac_modifier(self.scores[AbilityScore.DEX])
        base, bonus = self._armour_parts(ascending=True)
        return base + bonus + dex_modifier

    def movement_rate(self, ruleset: Ruleset) -> int:
        """Return how far this character moves in one exploration turn, in feet.

        What sets the rate depends on the `encumbrance` mode of the ruleset. Under `none`, every
        character moves 120 feet. Under `basic`, the rate depends on the worn armour and on the
        [`carrying_treasure`][osrlib.core.character.Character.carrying_treasure] flag. Under
        `detailed`, it depends on the total weight carried. Under `basic` and `detailed`, a
        character carrying more than the maximum load can't move, and the rate is 0.

        A party moves at the rate of its slowest living member. For a party, call
        [`Party.movement_rate`][osrlib.crawl.party.Party.movement_rate] instead of calling this for
        each member.

        Args:
            ruleset: The ruleset in play. What counts depends on its `encumbrance` mode.

        Returns:
            The rate in feet per turn: 120, 90, 60, 30, or 0.

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset

            ruleset = Ruleset()
            stream = RngStreams(master_seed=2).get(CHARACTER_CREATION_STREAM)
            character = create_character(
                name="Rurik",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=ruleset,
                stream=stream,
                purchases=[("plate_mail", 1)],
                equip_ids=["plate_mail"],
            ).character
            print(character.movement_rate(ruleset))
            # 60
            ```
        """
        return movement_rate_feet(self.inventory, ruleset, self.carrying_treasure)

    def to_document(self) -> dict[str, object]:
        """Return this character as a JSON-ready document stamped with its schema and engine versions.

        Use it to store one character on its own, like a character a player keeps between games.
        To store a group of characters, use
        [`party_to_document`][osrlib.core.character.party_to_document]. To store a game in
        progress, use [`save_game`][osrlib.persistence.save_game], which includes the characters.
        Load the document with [`from_document`][osrlib.core.character.Character.from_document].
        The version stamp lets a later release of osrlib tell whether it can read the document.

        Returns:
            A dict with the keys `kind`, `schema_version`, `engine_version`, and `payload`.
            `kind` is `"character"`, and `payload` contains the character's fields. Every value is
            a JSON type, so you can pass the result straight to [`json.dump`][json.dump].

        Examples:
            ```python
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, Character, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset

            stream = RngStreams(master_seed=2).get(CHARACTER_CREATION_STREAM)
            character = create_character(
                name="Rurik",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=Ruleset(),
                stream=stream,
            ).character
            document = character.to_document()
            print(sorted(document))
            # ['engine_version', 'kind', 'payload', 'schema_version']
            print(Character.from_document(document).name)
            # Rurik
            ```
        """
        return stamp_document("character", self.model_dump(mode="json"))

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> Character:
        """Rebuild a character from a document written by [`to_document`][osrlib.core.character.Character.to_document].

        This method ignores fields in the payload that this release doesn't recognize, so a
        document from a later release that only added fields still loads. It refuses a document
        with a newer schema version than this release supports, because the fields this release
        reads may mean something different in it.

        Args:
            document: The document [`to_document`][osrlib.core.character.Character.to_document]
                returned, or the same document read back from JSON.

        Returns:
            A new character, with every derived value computed from the loaded fields.

        Raises:
            ContentValidationError: If the document isn't a character document, or its payload
                doesn't validate as a character. The document is damaged or isn't one
                `to_document` wrote, so loading it again fails the same way.
            osrlib.errors.SaveVersionError: If the document's schema version is newer than this
                release supports. Upgrade osrlib to load it.

        Examples:
            Store a character as JSON and load it back:

            ```python
            import json
            from pathlib import Path

            from osrlib.core.alignment import Alignment
            from osrlib.core.character import CHARACTER_CREATION_STREAM, Character, create_character
            from osrlib.core.rng import RngStreams
            from osrlib.core.ruleset import Ruleset

            stream = RngStreams(master_seed=2).get(CHARACTER_CREATION_STREAM)
            rurik = create_character(
                name="Rurik",
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=Ruleset(),
                stream=stream,
                purchases=[("sword", 1)],
            ).character
            Path("rurik.json").write_text(json.dumps(rurik.to_document()))

            loaded = Character.from_document(json.loads(Path("rurik.json").read_text()))
            print(loaded.name, loaded.max_hp, loaded.thac0, [item.template.id for item in loaded.inventory.items])
            # Rurik 8 19 ['sword']
            ```
        """
        payload = check_document(document, "character")
        try:
            return cls.model_validate(payload)
        except ValueError as error:
            raise ContentValidationError(f"character document payload failed validation: {error}") from error


def party_to_document(characters: Sequence[Character]) -> dict[str, object]:
    """Return a group of characters as one JSON-ready document, stamped with its schema and engine versions.

    Use it to store a group of characters you create once and use again, like a set of ready-made
    characters a player can pick from. [`replay_game`][osrlib.persistence.replay_game] also
    takes one, as the party the game started with. For that, call this before you pass the
    characters to [`GameSession.new`][osrlib.crawl.session.GameSession.new], which assigns their
    ids.

    The document contains only the characters. Marching order and the rest of a
    [`Party`][osrlib.crawl.party.Party]'s state belong to a game in progress, which
    [`save_game`][osrlib.persistence.save_game] stores. Load the document with
    [`party_from_document`][osrlib.core.character.party_from_document].

    Args:
        characters: The characters to store, in the order you want them back.

    Returns:
        A dict with the keys `kind`, `schema_version`, `engine_version`, and `payload`. `kind`
        is `"party"`, and `payload` contains the characters as a list under `"characters"`. Every
        value is a JSON type.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import (
            CHARACTER_CREATION_STREAM,
            create_character,
            party_from_document,
            party_to_document,
        )
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset

        stream = RngStreams(master_seed=2).get(CHARACTER_CREATION_STREAM)
        roster = [
            create_character(
                name=name,
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=Ruleset(),
                stream=stream,
            ).character
            for name in ("Rurik", "Alia")
        ]
        document = party_to_document(roster)
        print([member.name for member in party_from_document(document)])
        # ['Rurik', 'Alia']
        ```
    """
    return stamp_document("party", {"characters": [character.model_dump(mode="json") for character in characters]})


def party_from_document(document: Mapping[str, object]) -> list[Character]:
    """Rebuild the characters in a document written by [`party_to_document`][osrlib.core.character.party_to_document].

    This function validates each character as it loads. If one fails, the whole load fails, so you never get
    part of the group. Pass the list to [`Party`][osrlib.crawl.party.Party] as its `members` to
    play them.

    Args:
        document: The document [`party_to_document`][osrlib.core.character.party_to_document]
            returned, or the same document read back from JSON.

    Returns:
        New characters, in the order they were stored.

    Raises:
        ContentValidationError: If the document isn't a party document, its payload has no
            `"characters"` list, or a character in it doesn't validate. The document is damaged
            or isn't one `party_to_document` wrote, so loading it again fails the same way.
        osrlib.errors.SaveVersionError: If the document's schema version is newer than this
            release supports. Upgrade osrlib to load it.

    Examples:
        Save a roster to a JSON file and load it back:

        ```python
        import json
        from pathlib import Path

        from osrlib.core.alignment import Alignment
        from osrlib.core.character import (
            CHARACTER_CREATION_STREAM,
            create_character,
            party_from_document,
            party_to_document,
        )
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset

        stream = RngStreams(master_seed=2).get(CHARACTER_CREATION_STREAM)
        roster = [
            create_character(
                name=name,
                class_id="fighter",
                alignment=Alignment.LAWFUL,
                ruleset=Ruleset(),
                stream=stream,
            ).character
            for name in ("Rurik", "Alia")
        ]
        Path("roster.json").write_text(json.dumps(party_to_document(roster)))

        characters = party_from_document(json.loads(Path("roster.json").read_text()))
        print([(member.name, member.max_hp) for member in characters])
        # [('Rurik', 8), ('Alia', 1)]
        ```
    """
    payload = check_document(document, "party")
    characters = payload.get("characters")
    if not isinstance(characters, list):
        raise ContentValidationError("party document payload must carry a 'characters' list")
    loaded: list[Character] = []
    for entry in characters:
        try:
            loaded.append(Character.model_validate(entry))
        except ValueError as error:
            raise ContentValidationError(f"party document member failed validation: {error}") from error
    return loaded


class AbilityScoreRolls(BaseModel):
    """The six rolled ability scores, with the dice behind each, so you can show them.

    [`roll_ability_scores`][osrlib.core.character.roll_ability_scores] returns one, and
    [`create_character`][osrlib.core.character.create_character] includes one in its result. Pass
    `scores` to [`validate_class_choice`][osrlib.core.character.validate_class_choice]. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    scores: dict[AbilityScore, int]
    """The total of each ability's three dice, keyed by [`AbilityScore`][osrlib.core.abilities.AbilityScore]. All six
    abilities are present, each from 3 to 18.
    """

    rolls: dict[AbilityScore, tuple[int, int, int]]
    """The three d6 results behind each score, keyed the same way, in the order they were rolled."""


class HitPointRoll(BaseModel):
    """A first-level hit point roll: every die thrown, and the hit points the character starts with.

    [`roll_hit_points`][osrlib.core.character.roll_hit_points] returns one, and
    [`create_character`][osrlib.core.character.create_character] includes one in its result. Use
    `hit_points` as the new character's `max_hp` and `current_hp`. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    rolls: tuple[int, ...]
    """Every die result, before the CON modifier, in the order thrown.

    With the `hp_reroll_at_first_level` option of [`Ruleset`][osrlib.core.ruleset.Ruleset] on, the 1s and 2s that were
    thrown again are here too, and the last entry is the die that counts. Otherwise there's one entry.
    """

    hit_points: int = Field(ge=1)
    """The hit points the character starts with: the last die plus the CON modifier, and at least 1."""


class CharacterCreationResult(BaseModel):
    """What [`create_character`][osrlib.core.character.create_character] returns: the character, and the dice behind it.

    Use `character`, and show the rolls if you want a player to see how the character came out.
    Frozen.
    """

    model_config = ConfigDict(frozen=True)

    character: Character
    """The new [`Character`][osrlib.core.character.Character], at first level, with its purchases in its inventory and
    its equipment worn or wielded.
    """

    ability_rolls: AbilityScoreRolls
    """The ability scores as rolled, before any adjustment. Compare them with `character.scores` to show what the
    adjustment changed.
    """

    hit_point_roll: HitPointRoll
    """The first-level hit point roll, with every die thrown."""

    gold_roll: RollResult
    """The 3d6 × 10 roll for starting money. Its `total` is the gold pieces the character had before paying for the
    purchases.
    """


def roll_ability_scores(stream: RngStream) -> AbilityScoreRolls:
    """Roll 3d6 for each of the six abilities, in the SRD's order: STR, INT, WIS, DEX, CON, CHA.

    This is the first step of creating a character one step at a time. Show the result to the
    player, then pass the scores to
    [`validate_class_choice`][osrlib.core.character.validate_class_choice] to find out which
    classes they qualify for. If the player wants to trade points between abilities after choosing
    a class, check the trade with [`validate_adjustment`][osrlib.core.abilities.validate_adjustment]
    and apply it with [`apply_adjustment`][osrlib.core.abilities.apply_adjustment].

    Call [`create_character`][osrlib.core.character.create_character] instead when the choices are
    already known and you want only the finished character.

    The same stream at the same position always gives the same six scores.

    Args:
        stream: The stream to draw from, usually `streams.get(CHARACTER_CREATION_STREAM)`. It
            advances by 18 dice.

    Returns:
        The six scores and the three dice behind each.

    Examples:
        ```python
        from osrlib.core.character import CHARACTER_CREATION_STREAM, roll_ability_scores
        from osrlib.core.rng import RngStreams

        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        rolled = roll_ability_scores(stream)
        print({ability.value: score for ability, score in rolled.scores.items()})
        # {'str': 14, 'int': 6, 'wis': 9, 'dex': 10, 'con': 11, 'cha': 11}
        ```
    """
    scores: dict[AbilityScore, int] = {}
    rolls: dict[AbilityScore, tuple[int, int, int]] = {}
    for ability in ABILITY_ROLL_ORDER:
        result = roll("3d6", stream)
        scores[ability] = result.total
        rolls[ability] = (result.rolls[0], result.rolls[1], result.rolls[2])
    return AbilityScoreRolls(scores=scores, rolls=rolls)


def validate_class_choice(scores: dict[AbilityScore, int], definition: ClassDefinition) -> list[Rejection]:
    """Check whether a set of rolled scores meets a class's minimum ability scores.

    Call it after [`roll_ability_scores`][osrlib.core.character.roll_ability_scores] and before the
    ability adjustment. That's the order the SRD sets: a player picks a class they qualify for, then
    trades points. To build the list of classes to offer, call it for each class in
    [`load_classes`][osrlib.data.load_classes]`().classes` and keep the ones with no rejections.

    It returns rejections rather than raising, so you can show a player why a class is closed to
    them. Each rejection's `params` include the ability, the minimum the class requires, and the
    score that fell short. Among the built-in classes, only the demi-humans have requirements: the
    dwarf and the halfling require CON 9, the elf requires INT 9, and the halfling also requires
    DEX 9.

    For the built-in classes, checking before the adjustment is enough. The adjustment can lower
    only STR, INT, and WIS, never below 9, and no built-in requirement is above 9. If you write a
    class with a minimum above 9 in STR, INT, or WIS, check the scores again after the adjustment.

    Args:
        scores: The rolled scores, before any adjustment, with all six abilities present.
        definition: The class the player chose, from
            [`load_classes`][osrlib.data.load_classes]`().get(class_id)`.

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] for each unmet requirement, or an
        empty list when the scores qualify for the class.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityScore
        from osrlib.core.character import validate_class_choice
        from osrlib.data import load_classes

        scores = dict.fromkeys(AbilityScore, 12)
        scores[AbilityScore.CON] = 7
        rejections = validate_class_choice(scores, load_classes().get("dwarf"))
        print([(rejection.code, rejection.params) for rejection in rejections])
        # [('creation.class.requirements_not_met', {'class': 'dwarf', 'ability': 'con', 'minimum': 9, 'score': 7})]
        print(validate_class_choice(scores, load_classes().get("fighter")))
        # []
        ```
    """
    rejections: list[Rejection] = []
    for ability, minimum in definition.requirements.items():
        if scores[ability] < minimum:
            rejections.append(
                Rejection(
                    code="creation.class.requirements_not_met",
                    params={"class": definition.id, "ability": ability, "minimum": minimum, "score": scores[ability]},
                )
            )
    return rejections


def roll_hit_points(
    definition: ClassDefinition, con_modifier: int, ruleset: Ruleset, stream: RngStream
) -> HitPointRoll:
    """Roll a first-level character's hit points: the class's hit die plus the CON modifier, and at least 1.

    Call it after the class is chosen and the scores are adjusted, so the CON modifier you pass is
    the final one. Get the modifier from
    [`load_ability_tables`][osrlib.data.load_ability_tables]`().hit_point_modifier(score)`. Use the
    result's `hit_points` as the new character's `max_hp` and `current_hp`.

    This function is for first level only, and it's the only one that applies the
    `hp_reroll_at_first_level` option. For the hit points a character gains at later levels, use
    [`level_up`][osrlib.core.classes.level_up].

    With `hp_reroll_at_first_level` on in the ruleset, this function throws a die that shows 1 or 2
    again until it shows 3 or more. The test is on the die before the CON modifier, and the result
    includes every throw.

    Args:
        definition: The character's class. Its first-level progression row gives the hit die.
        con_modifier: The CON hit point modifier for the final scores. It may be negative, and the
            hit points are at least 1 either way.
        ruleset: The ruleset in play, read for the `hp_reroll_at_first_level` option.
        stream: The stream to draw from, usually `streams.get(CHARACTER_CREATION_STREAM)`.

    Returns:
        Every die thrown, and the hit points the character starts with.

    Examples:
        ```python
        from osrlib.core.character import CHARACTER_CREATION_STREAM, roll_hit_points
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_classes

        fighter = load_classes().get("fighter")
        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        print(roll_hit_points(fighter, 0, Ruleset(), stream))
        # rolls=(2,) hit_points=2

        rerolling = Ruleset(hp_reroll_at_first_level=True)
        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        print(roll_hit_points(fighter, 0, rerolling, stream))
        # rolls=(2, 8) hit_points=8
        ```
    """
    die = definition.row(1).hit_dice.die
    rolls = [stream.randbelow(die) + 1]
    if ruleset.hp_reroll_at_first_level:
        while rolls[-1] <= 2:
            rolls.append(stream.randbelow(die) + 1)
    return HitPointRoll(rolls=tuple(rolls), hit_points=max(1, rolls[-1] + con_modifier))


def validate_extra_languages(definition: ClassDefinition, int_score: int, choices: Sequence[str]) -> list[Rejection]:
    """Check the extra languages a character with a high INT chooses.

    Every character speaks their alignment tongue and the languages their class grants. An INT of
    13 or more allows one to three extra languages, and an INT of 12 or less allows none. To find
    out how many a player may choose, call
    [`load_ability_tables`][osrlib.data.load_ability_tables]`().additional_languages(int_score)`.
    Offer the languages whose `choosable` is True in
    [`load_languages`][osrlib.data.load_languages], check the choices here, then store them in
    [`extra_languages`][osrlib.core.character.Character.extra_languages].

    This function rejects a choice when it isn't a choosable language, when it repeats another choice, or when
    the class already grants it. Each of these rejections includes the language, so you can show
    the player which choice to change. If the player chooses more languages than the INT allows,
    you get one rejection for the whole list, with the number allowed and the number chosen.

    Args:
        definition: The chosen class. The languages it grants can't be chosen again as extras.
        int_score: The final INT score, after any adjustment.
        choices: The chosen language ids, from [`load_languages`][osrlib.data.load_languages] and
            listed in [the language id index](https://mmacy.github.io/osrlib-python/reference/content-ids/languages/).

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] for each problem, or an empty list
        when every choice is allowed.

    Examples:
        ```python
        from osrlib.core.character import validate_extra_languages
        from osrlib.data import load_ability_tables, load_classes

        fighter = load_classes().get("fighter")
        print(load_ability_tables().additional_languages(13))
        # 1
        print(validate_extra_languages(fighter, 13, ["elvish"]))
        # []
        rejections = validate_extra_languages(fighter, 9, ["elvish"])
        print([(rejection.code, rejection.params) for rejection in rejections])
        # [('creation.languages.too_many', {'allowed': 0, 'chosen': 1})]
        ```
    """
    rejections: list[Rejection] = []
    allowed = load_ability_tables().additional_languages(int_score)
    if len(choices) > allowed:
        rejections.append(
            Rejection(code="creation.languages.too_many", params={"allowed": allowed, "chosen": len(choices)})
        )
    catalog = load_languages()
    choosable = {language.id for language in catalog.languages if language.choosable}
    seen: set[str] = set()
    for choice in choices:
        if choice in seen:
            rejections.append(Rejection(code="creation.languages.duplicate_choice", params={"language": choice}))
            continue
        seen.add(choice)
        if choice not in choosable:
            rejections.append(Rejection(code="creation.languages.not_available", params={"language": choice}))
        elif choice in definition.languages:
            rejections.append(Rejection(code="creation.languages.duplicates_native", params={"language": choice}))
    return rejections


def validate_starting_spells(
    definition: ClassDefinition, catalog: SpellCatalog, spell_ids: Sequence[str]
) -> list[Rejection]:
    """Check the spells chosen for an arcane caster's starting spell book.

    An arcane caster, which among the built-in classes means the magic-user and the elf, starts with
    as many spells in their book as they can memorize. At first level that's exactly one
    first-level spell. To offer the player the first-level spells on their class's list, pass the
    `spell_list` from [`caster_profile`][osrlib.core.spells.caster_profile] and the level 1 to
    [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list]. Check the choice here, then
    write it with [`choose_starting_spells`][osrlib.core.character.choose_starting_spells]. Whether the
    player or the referee chooses is up to you. This function only checks the choice.

    This function rejects a choice when the spell id is unknown, when it repeats, when the spell is on another
    class's list, or when the number of spells at any spell level doesn't match the book's size at
    that level. It rejects too few as well as too many, because the SRD gives a starting book a
    fixed size. A cleric has no spell book, because clerics receive their spells from a deity, so
    it rejects any choice for a cleric or for a class that casts no spells.

    Args:
        definition: The character's class.
        catalog: The spell catalog from [`load_spells`][osrlib.data.load_spells].
        spell_ids: The chosen spell ids, from `catalog` and listed in [the spell id
            index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/).

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] for each problem, or an empty list
        when the book is legal. A class with no spell book and no spells chosen gives an empty
        list.

    Examples:
        ```python
        from osrlib.core.character import validate_starting_spells
        from osrlib.data import load_classes, load_spells

        catalog = load_spells()
        magic_user = load_classes().get("magic_user")
        print(validate_starting_spells(magic_user, catalog, ["sleep"]))
        # []
        rejections = validate_starting_spells(magic_user, catalog, ["sleep", "magic_missile"])
        print([(rejection.code, rejection.params) for rejection in rejections])
        # [('magic.book.capacity_mismatch', {'spell_level': 1, 'capacity': 1, 'chosen': 2})]
        cleric = load_classes().get("cleric")
        print([rejection.code for rejection in validate_starting_spells(cleric, catalog, ["cure_light_wounds"])])
        # ['magic.book.not_arcane']
        ```
    """
    profile = caster_profile(definition)
    if profile is None or profile.kind != "arcane":
        if spell_ids:
            return [Rejection(code="magic.book.not_arcane", params={"class": definition.id})]
        return []
    rejections: list[Rejection] = []
    counts: dict[int, int] = {}
    seen: set[str] = set()
    for spell_id in spell_ids:
        if spell_id in seen:
            rejections.append(Rejection(code="magic.book.duplicate", params={"spell": spell_id}))
            continue
        seen.add(spell_id)
        try:
            template = catalog.get(spell_id)
        except ValueError:
            rejections.append(Rejection(code="magic.book.unknown_spell", params={"spell": spell_id}))
            continue
        if template.spell_list != profile.spell_list:
            rejections.append(
                Rejection(code="magic.book.wrong_list", params={"spell": spell_id, "list": template.spell_list})
            )
            continue
        counts[template.level] = counts.get(template.level, 0) + 1
    slots = definition.row(1).spell_slots
    for spell_level in range(1, max((*counts, len(slots)), default=0) + 1):
        allowed = slots[spell_level - 1] if spell_level <= len(slots) else 0
        chosen = counts.get(spell_level, 0)
        if chosen != allowed:
            rejections.append(
                Rejection(
                    code="magic.book.capacity_mismatch",
                    params={"spell_level": spell_level, "capacity": allowed, "chosen": chosen},
                )
            )
    return rejections


def choose_starting_spells(
    character: Character, definition: ClassDefinition, catalog: SpellCatalog, spell_ids: Sequence[str]
) -> list[Rejection]:
    """Write an arcane caster's starting spell book onto the character.

    Use it when you create a character step by step and the player has chosen their first spell.
    It refuses a character whose book already has spells in it, checks the choice with
    [`validate_starting_spells`][osrlib.core.character.validate_starting_spells], and then stores
    the ids in [`spell_book`][osrlib.core.character.Character.spell_book]. When it refuses
    anything, it writes nothing. [`create_character`][osrlib.core.character.create_character]
    writes the book for you when you pass `starting_spell_ids`.

    A spell in the book can't be cast yet. To prepare spells for casting, call
    [`memorize_spells`][osrlib.core.spells.memorize_spells]. An arcane caster memorizes spells
    only from the book.

    Args:
        character: The character to write to. Changed in place when the choice is legal.
        definition: The character's class.
        catalog: The spell catalog from [`load_spells`][osrlib.data.load_spells].
        spell_ids: The chosen spell ids, from `catalog` and listed in [the spell id
            index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/).

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] for each problem, or an empty list
        when the book was written.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import (
            CHARACTER_CREATION_STREAM,
            choose_starting_spells,
            create_character,
        )
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.data import load_classes, load_spells

        magic_user = load_classes().get("magic_user")
        stream = RngStreams(master_seed=3).get(CHARACTER_CREATION_STREAM)
        character = create_character(
            name="Miri",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=stream,
            starting_spell_ids=["sleep"],
        ).character
        rejections = choose_starting_spells(character, magic_user, load_spells(), ["magic_missile"])
        print([rejection.code for rejection in rejections])
        # ['magic.book.already_chosen']
        print(character.spell_book)
        # ('sleep',)
        ```
    """
    if character.spell_book:
        return [Rejection(code="magic.book.already_chosen", params={"character": character.name})]
    rejections = validate_starting_spells(definition, catalog, spell_ids)
    if rejections:
        return rejections
    character.spell_book = tuple(spell_ids)
    return []


def roll_starting_gold(stream: RngStream) -> RollResult:
    """Roll a new character's starting money: 3d6 × 10 gold pieces.

    This is the last roll of creation. Put the total in the character's purse,
    `character.inventory.purse.gp`. Then buy equipment with it: check each purchase with
    [`validate_purchase`][osrlib.core.items.validate_purchase] and make it with
    [`purchase`][osrlib.core.items.purchase].
    [`create_character`][osrlib.core.character.create_character] does all of this for you when you
    pass `purchases`.

    Args:
        stream: The stream to draw from, usually `streams.get(CHARACTER_CREATION_STREAM)`. It
            advances by three dice.

    Returns:
        The roll. Its `total` is the starting money in gold pieces, from 30 to 180.

    Examples:
        ```python
        from osrlib.core.character import CHARACTER_CREATION_STREAM, roll_starting_gold
        from osrlib.core.rng import RngStreams

        stream = RngStreams(master_seed=11).get(CHARACTER_CREATION_STREAM)
        rolled = roll_starting_gold(stream)
        print(rolled.rolls, rolled.total)
        # (3, 1, 3) 70
        ```
    """
    return roll("3d6×10", stream)


def create_character(
    *,
    name: str,
    class_id: str,
    alignment: Alignment,
    ruleset: Ruleset,
    stream: RngStream,
    adjustment: AbilityAdjustment | None = None,
    starting_spell_ids: Sequence[str] = (),
    extra_languages: Sequence[str] = (),
    purchases: Sequence[tuple[str, int]] = (),
    equip_ids: Sequence[str] = (),
) -> CharacterCreationResult:
    """Create a first-level character in one call, with every choice passed in.

    Start here to make characters. Pass a name, a class id, an alignment, a ruleset, and a seeded
    stream, and it rolls the whole character: ability scores, hit points, and starting gold. It
    also makes the choices you pass: a trade of ability points, the first spell, extra languages,
    and the gear to buy and equip. Put the characters you create into a
    [`Party`][osrlib.crawl.party.Party], and pass the party to
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] to start a game.

    To get a stream, make an [`RngStreams`][osrlib.core.rng.RngStreams] set from a seed and ask it
    for the creation stream: `RngStreams(master_seed=2).get(CHARACTER_CREATION_STREAM)`. The same
    seed and the same arguments always create the same character. To roll a whole party from one
    seed, pass the same stream to each call, and each call continues where the last one stopped.

    You make every choice before the call, which suits a script or a ready-made character. When a
    player chooses as they go, call the step-by-step functions in this module instead. Those return
    [`Rejection`][osrlib.core.validation.Rejection] records you can show the player. This function
    raises `ValueError` on the first illegal choice and reports nothing more.

    The steps run in the SRD's order: roll the six scores, check them against the class's
    requirements, apply the ability adjustment, check the spell book, roll hit points, check the
    extra languages, roll starting gold, then buy and equip. The stream advances in that order, so
    the scores are always rolled first and the gold last. The checks take no draws. The scores are
    rolled before they're checked against the class, so scores that miss the requirements raise
    after the stream has advanced. Call again with the same stream to roll new scores, or choose
    another class.

    Args:
        name: The character's name.
        class_id: The class to play, like `"fighter"`, from
            [`load_classes`][osrlib.data.load_classes] and listed in [the class id
            index](https://mmacy.github.io/osrlib-python/reference/content-ids/classes/).
        alignment: Lawful, neutral, or chaotic.
        ruleset: The ruleset in play. When its `hp_reroll_at_first_level` flag is on, a hit die
            showing 1 or 2 is thrown again.
        stream: The stream for the creation draws, usually
            `streams.get(`[`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM]`)`.
        adjustment: A trade of ability points, as an
            [`AbilityAdjustment`][osrlib.core.abilities.AbilityAdjustment]. It lowers one or more
            of STR, INT, and WIS to raise a prime requisite, which earns the character a larger
            experience bonus. With `None`, the scores stay as rolled.
        starting_spell_ids: The spells in an arcane caster's starting spell book, from
            [`load_spells`][osrlib.data.load_spells] and listed in [the spell id
            index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/). The magic-user and the elf must
            have exactly one first-level spell here. Leave it empty for every other class.
        extra_languages: The extra languages a high INT allows, from
            [`load_languages`][osrlib.data.load_languages] and listed in
            [the language id index](https://mmacy.github.io/osrlib-python/reference/content-ids/languages/). An INT of
            12 or less allows none.
        purchases: What to buy with the starting gold, as `(item_id, lots)` pairs, bought in the
            order given. `item_id` comes from [`load_equipment`][osrlib.data.load_equipment] and is
            listed in [the equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/).
            A lot is the unit an item is sold in. Weapons and armour sell one at a time, so `lots` is how many. Gear and
            ammunition sell in fixed bundles, so one lot of torches is six torches.
        equip_ids: The ids of bought items to wear or wield, equipped in the order given. Each one
            must be in `purchases`, and the class's armour and weapon rules must allow it.

    Returns:
        The new character, and the dice rolled to create it.

    Raises:
        ValueError: On the first illegal choice. Illegal choices are an unknown class, spell,
            language, or item id, scores that miss the class's requirements, an adjustment the
            rules forbid, and a spell book of the wrong size. So are more languages than the INT
            allows, a purchase the starting gold can't cover, an item in `equip_ids` that wasn't
            bought, and an item the class can't equip. The message includes the choice that
            failed. To find out every choice that fails and why, call the step-by-step functions
            instead.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.party import Party

        streams = RngStreams(master_seed=2)
        stream = streams.get(CHARACTER_CREATION_STREAM)
        result = create_character(
            name="Rurik",
            class_id="fighter",
            alignment=Alignment.LAWFUL,
            ruleset=Ruleset(),
            stream=stream,
            purchases=[("sword", 1), ("leather", 1)],
            equip_ids=["sword", "leather"],
        )
        character = result.character
        print(character.name, character.level, character.max_hp, character.armour_class)
        # Rurik 1 8 6
        print(character.inventory.worn_armour.template.id, character.inventory.purse.gp)
        # leather 30
        print(result.gold_roll.total, result.hit_point_roll.rolls)
        # 60 (7,)

        party = Party(members=[character])
        print(party.movement_rate(Ruleset()))
        # 90
        ```
    """
    definition = load_classes().get(class_id)
    ability_rolls = roll_ability_scores(stream)
    choice_rejections = validate_class_choice(ability_rolls.scores, definition)
    if choice_rejections:
        raise ValueError(f"illegal class choice: {[rejection.code for rejection in choice_rejections]}")
    scores = dict(ability_rolls.scores)
    if adjustment is not None:
        scores = apply_adjustment(scores, adjustment, definition.prime_requisites, definition.may_not_lower)
    profile = caster_profile(definition)
    if starting_spell_ids or (profile is not None and profile.kind == "arcane"):
        spell_rejections = validate_starting_spells(definition, load_spells(), starting_spell_ids)
        if spell_rejections:
            raise ValueError(f"illegal starting spells: {[rejection.code for rejection in spell_rejections]}")
    con_modifier = load_ability_tables().hit_point_modifier(scores[AbilityScore.CON])
    hit_point_roll = roll_hit_points(definition, con_modifier, ruleset, stream)
    language_rejections = validate_extra_languages(definition, scores[AbilityScore.INT], extra_languages)
    if language_rejections:
        raise ValueError(f"illegal language choices: {[rejection.code for rejection in language_rejections]}")
    gold_roll = roll_starting_gold(stream)
    inventory = Inventory()
    inventory.purse.gp = gold_roll.total
    equipment = load_equipment()
    for item_id, lots in purchases:
        template = equipment.get(item_id)
        purchase_rejections = validate_purchase(inventory.purse, template, lots)
        if purchase_rejections:
            raise ValueError(f"illegal purchase: {[rejection.code for rejection in purchase_rejections]}")
        purchase(inventory, template, lots)
    for item_id in equip_ids:
        instance = next(
            (
                candidate
                for candidate in inventory.items
                if isinstance(candidate, ItemInstance) and candidate.template.id == item_id
            ),
            None,
        )
        if instance is None:
            raise ValueError(f"cannot equip {item_id!r}: no such item in the inventory")
        equip(inventory, definition, instance)
    character = Character(
        name=name,
        class_id=definition.id,
        race=definition.race,
        level=1,
        xp=0,
        scores=scores,
        alignment=alignment,
        extra_languages=tuple(extra_languages),
        max_hp=hit_point_roll.hit_points,
        current_hp=hit_point_roll.hit_points,
        inventory=inventory,
        spell_book=tuple(starting_spell_ids),
    )
    return CharacterCreationResult(
        character=character,
        ability_rolls=ability_rolls,
        hit_point_roll=hit_point_roll,
        gold_roll=gold_roll,
    )
