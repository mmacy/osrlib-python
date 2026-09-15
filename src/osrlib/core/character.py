"""The player character: the model, creating one step by step, and saving one to a document.

Start here when you need a party. [`create_character`][osrlib.core.character.create_character] is
the entry point: hand it a name, a class id, an alignment, a
[`Ruleset`][osrlib.core.ruleset.Ruleset], and a seeded random-number stream from
[`osrlib.core.rng`][osrlib.core.rng], and it returns a first-level
[`Character`][osrlib.core.character.Character] together with the raw dice it rolled. Put the
characters you get into a [`Party`][osrlib.crawl.party.Party] to play them, or use them on their
own with the combat, magic, and item functions of the core kernel.

[`Character`][osrlib.core.character.Character] is a mutable pydantic model. Its derived values,
which are the ability modifiers, both armour classes, movement rate, literacy, and the language
list, are properties computed from stored state rather than stored fields, so they cannot fall out
of step with the state they come from. Validation on the model is structural: score ranges, a level
within the class's bounds, current hit points no higher than maximum. Whether a creation step was
legal is checked by the creation functions when the step happens, because a finished character
keeps no record of the choices that made it.

Creation follows the OSE SRD's Creating a Character steps as pure functions you drive one at a time
when a player is making the choices:
[`roll_ability_scores`][osrlib.core.character.roll_ability_scores],
[`validate_class_choice`][osrlib.core.character.validate_class_choice],
[`apply_adjustment`][osrlib.core.abilities.apply_adjustment],
[`validate_starting_spells`][osrlib.core.character.validate_starting_spells] with
[`choose_starting_spells`][osrlib.core.character.choose_starting_spells],
[`roll_hit_points`][osrlib.core.character.roll_hit_points],
[`validate_extra_languages`][osrlib.core.character.validate_extra_languages],
[`roll_starting_gold`][osrlib.core.character.roll_starting_gold], and then buying and equipping
gear through [`osrlib.core.items`][osrlib.core.items]. Each validating step returns a list of
[`Rejection`][osrlib.core.validation.Rejection] records rather than raising, so you can show the
player what went wrong and let them choose again. Creation emits no events: it happens before a
session starts, and the first events belong to play.
[`create_character`][osrlib.core.character.create_character] runs the whole sequence in one call
when every choice is known upfront, and raises instead of returning rejections.

Advancement, which is leveling up, energy drain, and experience awards, lives in
[`osrlib.core.classes`][osrlib.core.classes], the module that also defines the class a character
plays. Saving a character to disk goes through
[`to_document`][osrlib.core.character.Character.to_document] and
[`party_to_document`][osrlib.core.character.party_to_document], whose output
[`osrlib.persistence`][osrlib.persistence] writes as part of a whole-game save.

Two stream keys are the naming convention every session adopts:
[`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM] for creation draws
and [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM] for in-play level-up hit point
rolls. They stay separate so that a change to the creation rules never shifts advancement draws
already recorded in a save.

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
    Literacy,
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
"""The stream key every session uses for creation draws.

A stream key names one independent random-number sequence inside an
[`RngStreams`][osrlib.core.rng.RngStreams] set. Pass
`streams.get(CHARACTER_CREATION_STREAM)` as the `stream` argument of
[`create_character`][osrlib.core.character.create_character] and of the stepwise creation
functions, which draw ability scores, the first-level hit die, and starting gold from it in that
order.

Use a different key only when you want creation draws kept apart from the ones a session already
records, for example when you roll throwaway characters beside a live game. Pass your own key to
`streams.get`; do not change this constant, because a save replays every stream by the key that
produced it.
"""

ADVANCEMENT_STREAM = StreamName.ADVANCEMENT
"""The stream key every session uses for in-play advancement draws.

Pass `streams.get(ADVANCEMENT_STREAM)` as the `stream` argument of
[`level_up`][osrlib.core.classes.level_up], [`apply_xp`][osrlib.core.classes.apply_xp], and
[`drain_levels`][osrlib.core.classes.drain_levels], which roll hit dice on it.

It is separate from
[`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM] so that a change to
the creation rules, which would consume a different number of draws, never shifts advancement rolls
a save has already recorded.
"""

ABILITY_ROLL_ORDER = (
    AbilityScore.STR,
    AbilityScore.INT,
    AbilityScore.WIS,
    AbilityScore.DEX,
    AbilityScore.CON,
    AbilityScore.CHA,
)
"""The order [`roll_ability_scores`][osrlib.core.character.roll_ability_scores] draws the six abilities.

This is the SRD's listing order, and it is fixed. Read it when you are displaying rolled scores in
the order the dice came up, or when you are reproducing a draw sequence by hand from a recorded
seed. The order is part of what makes a seeded creation reproducible, so a caller cannot change it.
"""


class Character(BaseModel):
    """A player character: the stored state of one played person, and the values derived from it.

    Get one from [`create_character`][osrlib.core.character.create_character], from the stepwise
    creation functions in this module, or from
    [`from_document`][osrlib.core.character.Character.from_document] when reloading a save.
    Construct one directly only when you already have every value, as in a test fixture.

    Put characters in a [`Party`][osrlib.crawl.party.Party] to explore with them, hand one to the
    combat functions in [`osrlib.core.combat`][osrlib.core.combat] as an attacker or a target, to
    [`osrlib.core.spells`][osrlib.core.spells] as a caster, and to
    [`level_up`][osrlib.core.classes.level_up] or [`apply_xp`][osrlib.core.classes.apply_xp] to
    advance it. A [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] exposes the same
    combatant surface, so those functions take either.

    The model is mutable and validates on assignment: setting a field that breaks a rule raises
    rather than storing the bad value. Validation is structural only. It checks that the six scores
    are present and in range, that the level is within the class's maximum, and that current hit
    points do not exceed maximum hit points. It does not check that the character was created
    legally, because a finished character keeps no record of its own creation.

    Nothing derived is stored. THAC0, attack bonus, saving throws, ability modifiers, both armour
    classes, literacy, and the language list are properties recomputed from the stored fields every
    time you read them, so a level change or a swapped piece of armour shows up immediately and
    nothing can fall out of step.

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
    """The entity id, which a [`GameSession`][osrlib.crawl.session.GameSession] assigns from its
    [`IdAllocator`][osrlib.core.monsters.IdAllocator] when the character joins. `None` until then, and events fall back
    to the name while it is unset.
    """

    name: str = Field(min_length=1)
    """The character's name. At least one character long."""

    class_id: str
    """The class this character plays, like `"fighter"`. Valid ids come from [`load_classes`][osrlib.data.load_classes];
    see [the class id index][classes-index]. Read [`definition`][osrlib.core.character.Character.definition] to get the
    class itself.
    """

    race: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    """The character's people, like `"human"` or `"dwarf"`, as a lowercase identifier. Creation copies it from the
    class. No rules procedure reads it: racial abilities resolve through the class's ability tags instead, so a new race
    needs no code.
    """

    level: int = Field(ge=1)
    """The experience level, 1 through the class's maximum."""

    xp: int = Field(ge=0)
    """Experience points accumulated. Advancement compares this against the thresholds in the class's progression table;
    award XP with [`apply_xp`][osrlib.core.classes.apply_xp] rather than by assigning here.
    """

    scores: dict[AbilityScore, int]
    """The six ability scores, each 3 through 18, keyed by [`AbilityScore`][osrlib.core.abilities.AbilityScore]. All six
    must be present.
    """

    alignment: Alignment
    """Lawful, neutral, or chaotic. It also fixes
    [`alignment_tongue`][osrlib.core.character.Character.alignment_tongue].
    """

    extra_languages: tuple[str, ...] = ()
    """The extra language ids a high INT granted at creation, validated by
    [`validate_extra_languages`][osrlib.core.character.validate_extra_languages].
    """

    max_hp: int = Field(ge=1)
    """Maximum hit points, at least 1."""

    current_hp: int = Field(ge=0)
    """Current hit points, from 0 up to `max_hp`. Reaching 0 means the character has dropped. Death itself is the
    `dead` condition, applied by [`kill`][osrlib.core.effects.kill], rather than a hit point value.
    """

    inventory: Inventory = Field(default_factory=Inventory)
    """Everything carried, worn, and wielded, plus the purse. See [`Inventory`][osrlib.core.items.Inventory] and the
    buying and equipping functions in [`osrlib.core.items`][osrlib.core.items].
    """

    carrying_treasure: bool = False
    """Whether the character is carrying enough treasure to slow them down. Basic encumbrance leaves the threshold to
    the referee, so osrlib leaves it to you: set this flag and
    [`movement_rate`][osrlib.core.character.Character.movement_rate] drops the rate a step. Ignored under the other
    encumbrance modes.
    """

    conditions: tuple[ActiveCondition, ...] = ()
    """The conditions in effect, like poisoned or paralyzed. Apply and clear them through
    [`osrlib.core.effects`][osrlib.core.effects]; test one with [`has_condition`][osrlib.core.effects.has_condition].
    """

    stat_modifiers: tuple[ActiveModifier, ...] = ()
    """Timed bonuses and penalties from spells and items, applied by [`osrlib.core.effects`][osrlib.core.effects]."""

    spell_book: tuple[str, ...] = ()
    """The spell ids an arcane caster can memorize from, in the order they were learned. Empty for clerics, whose spells
    come from their deity, and for non-casters.
    """

    memorized_spells: tuple[MemorizedSpell, ...] = ()
    """The prepared copies a caster may cast, as [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] records in
    memorization order. The order matters: casting spends the first matching copy, and energy drain forgets the newest
    first. How many of each level fit is derived from the class's progression row, not stored, so leveling up and being
    drained both recompute it.
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
        """The class this character plays, looked up from the loaded catalog by `class_id`.

        Read it for anything that depends on the class: the progression table, the armour and weapon
        policies, the class abilities, the level titles. The catalog is loaded once and cached, so
        reading this property repeatedly costs nothing.
        """
        return load_classes().get(self.class_id)

    @property
    def thac0(self) -> int:
        """The number the character must roll to hit armour class 0, from the current level's row.

        Descending armour class is the SRD's default presentation. Use
        [`attack_bonus`][osrlib.core.character.Character.attack_bonus] instead if your front end
        shows ascending armour class. The two describe the same attack.
        """
        return self.definition.row(self.level).thac0

    @property
    def attack_bonus(self) -> int:
        """The bonus added to an attack roll under ascending armour class, from the current level's row.

        The ascending presentation of [`thac0`][osrlib.core.character.Character.thac0]. Both come
        from the same progression row, so they always agree.
        """
        return self.definition.row(self.level).attack_bonus

    @property
    def saves(self) -> SavingThrows:
        """The five saving throw targets for the current level.

        Roll a d20 against the relevant field and succeed on that number or higher. Leveling and
        energy drain both change this the moment they change the level, because it is read from
        the progression row rather than stored.
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

        Roll 1d6 and succeed on this number or less. Force-door attempts in a crawl go through
        [`osrlib.crawl.exploration`][osrlib.crawl.exploration], which reads this for you.
        """
        return self._tables().open_doors_chance(self.scores[AbilityScore.STR])

    @property
    def missile_modifier(self) -> int:
        """The DEX modifier added to missile attack rolls, from −3 to +3. It does not change damage."""
        return self._tables().missile_modifier(self.scores[AbilityScore.DEX])

    @property
    def initiative_modifier(self) -> int:
        """The DEX modifier to an individual initiative roll, from −2 to +2.

        It applies only when the `individual_initiative` flag of the
        [`Ruleset`][osrlib.core.ruleset.Ruleset] is on. Group initiative, the default, rolls once
        per side and ignores it.
        """
        return self._tables().initiative_modifier(self.scores[AbilityScore.DEX])

    @property
    def hit_point_modifier(self) -> int:
        """The CON modifier added to each Hit Die rolled, from −3 to +3.

        Creation and [`level_up`][osrlib.core.classes.level_up] add it per die and floor the gain
        at 1 hit point, so a poor CON never costs a character hit points outright.
        """
        return self._tables().hit_point_modifier(self.scores[AbilityScore.CON])

    @property
    def magic_save_modifier(self) -> int:
        """The WIS modifier applied to saving throws against magical effects, from −3 to +3."""
        return self._tables().magic_save_modifier(self.scores[AbilityScore.WIS])

    @property
    def npc_reaction_modifier(self) -> int:
        """The CHA modifier applied to NPC reaction rolls, from −2 to +2.

        Reaction rolls during a crawl read it for you; see
        [`osrlib.crawl.encounter`][osrlib.crawl.encounter].
        """
        return self._tables().npc_reaction_modifier(self.scores[AbilityScore.CHA])

    @property
    def literacy(self) -> Literacy:
        """How well the character reads and writes, from INT.

        The three levels are illiterate, basic literacy, and full literacy; see
        [`Literacy`][osrlib.core.abilities.Literacy]. It applies to the languages in
        [`languages`][osrlib.core.character.Character.languages]. No rule in osrlib reads it, so
        what an illiterate character may not do is your game's decision.
        """
        return self._tables().literacy(self.scores[AbilityScore.INT])

    @property
    def alignment_tongue(self) -> str:
        """The secret language shared by everyone of this alignment, as a language id.

        Every character speaks the tongue of their own alignment and no other. The id is
        `alignment_` followed by the alignment's wire value, so a lawful character speaks
        `"alignment_lawful"`. These are not entries in the language catalog that
        [`load_languages`][osrlib.data.load_languages] returns. They are derived here, so changing
        a character's alignment changes the tongue in the same moment.
        """
        return f"alignment_{self.alignment.value}"

    @property
    def languages(self) -> tuple[str, ...]:
        """Every language the character speaks, as ids, in a fixed order.

        The alignment tongue comes first, then the languages the class grants with Common at the
        front, then the extras a high INT bought at creation. Compare against another speaker's
        list to decide whether two people can talk to each other. The ids other than the alignment
        tongue are catalog ids from [`load_languages`][osrlib.data.load_languages]; see
        [the language id index][languages-index].
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
        """The character's armour class in the descending presentation, where lower is better.

        Unarmoured is 9. Worn armour sets the base, shields and always-active magic items subtract
        their bonus, and the DEX modifier subtracts on top. Equipping or removing armour changes
        this at once, because it is computed from the inventory rather than stored. Use
        [`armour_class_ascending`][osrlib.core.character.Character.armour_class_ascending] if your
        front end shows ascending armour class.
        """
        dex_modifier = self._tables().ac_modifier(self.scores[AbilityScore.DEX])
        base, bonus = self._armour_parts(ascending=False)
        return base - bonus - dex_modifier

    @property
    def armour_class_ascending(self) -> int:
        """The character's armour class in the ascending presentation, where higher is better.

        Unarmoured is 10, and every bonus adds. It describes the same defense as
        [`armour_class`][osrlib.core.character.Character.armour_class]. The two presentations
        always agree.
        """
        dex_modifier = self._tables().ac_modifier(self.scores[AbilityScore.DEX])
        base, bonus = self._armour_parts(ascending=True)
        return base + bonus + dex_modifier

    def movement_rate(self, ruleset: Ruleset) -> int:
        """Return how far this character moves in one exploration turn, in feet.

        What the rate depends on is the ruleset's encumbrance mode: nothing at all under `none`,
        the worn armour category and the
        [`carrying_treasure`][osrlib.core.character.Character] flag under `basic`, and the total
        weight carried under `detailed`. A character loaded past the maximum cannot move and gets
        0.

        A party moves at its slowest living member's rate, so for a party call
        [`Party.movement_rate`][osrlib.crawl.party.Party.movement_rate] instead of calling this
        per member.

        Args:
            ruleset: The ruleset in play, whose encumbrance mode governs what counts.

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

        The stamp is what lets a later version of osrlib decide whether it can read the document.
        Use this to store one character on its own, and use
        [`party_to_document`][osrlib.core.character.party_to_document] for a whole party, and the
        save functions in [`osrlib.persistence`][osrlib.persistence] to store a session, which already includes
        its characters.

        Read it back with
        [`from_document`][osrlib.core.character.Character.from_document].

        Returns:
            The stamped envelope, whose payload is the serialized character. Every value is a
            JSON type, so you can hand the result straight to
            [`json.dump`][json.dump].

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

        Fields in the payload that this version does not recognize are ignored, so a document written
        by a later release that only added fields still loads. A document whose schema version is
        newer than this release understands is refused instead, because the meaning of what it
        does know may have changed.

        Args:
            document: The stamped envelope, as returned by
                [`to_document`][osrlib.core.character.Character.to_document] or read back from
                JSON.

        Returns:
            The character, with every derived value recomputed from the loaded state.

        Raises:
            ContentValidationError: If the envelope is not a character document, or the payload
                does not validate as a character.
            SaveVersionError: If the document's schema version is newer than this library
                understands.
        """
        payload = check_document(document, "character")
        try:
            return cls.model_validate(payload)
        except ValueError as error:
            raise ContentValidationError(f"character document payload failed validation: {error}") from error


def party_to_document(characters: Sequence[Character]) -> dict[str, object]:
    """Return a group of characters as one JSON-ready document, stamped with its versions.

    Use this to store a roster you build once and reuse, like a set of pre-generated characters
    a front end offers at the start of a game. It writes the characters and nothing else: marching
    order, shared light sources, and the rest of a playing party's state belong to
    [`Party`][osrlib.crawl.party.Party] and are saved with the session by
    [`osrlib.persistence`][osrlib.persistence].

    Read it back with
    [`party_from_document`][osrlib.core.character.party_from_document], which returns the
    characters in the order you passed them.

    Args:
        characters: The characters to store, in the order you want them back.

    Returns:
        The stamped envelope, whose payload contains the serialized characters under
        `"characters"`. Every value is a JSON type.

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

    Each character is validated on the way in, so a malformed member fails the whole load rather
    than producing a half-built roster.

    Args:
        document: The stamped envelope, as returned by
            [`party_to_document`][osrlib.core.character.party_to_document] or read back from JSON.

    Returns:
        The characters, in the order they were stored.

    Raises:
        ContentValidationError: If the envelope is not a party document, if its payload contains no
            `"characters"` list, or if a member does not validate as a character.
        SaveVersionError: If the document's schema version is newer than this library understands.
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
    """The six rolled ability scores, with the individual dice kept so you can show them.

    [`roll_ability_scores`][osrlib.core.character.roll_ability_scores] returns this, and
    [`create_character`][osrlib.core.character.create_character] includes it in its result. Frozen:
    a roll is history and does not change.
    """

    model_config = ConfigDict(frozen=True)

    scores: dict[AbilityScore, int]
    """The total of each ability's three dice, keyed by [`AbilityScore`][osrlib.core.abilities.AbilityScore]. All six
    are present, each 3 through 18.
    """

    rolls: dict[AbilityScore, tuple[int, int, int]]
    """The three raw d6 results behind each score, in the order they were rolled, so a character sheet can show the dice
    a player watched come up.
    """


class HitPointRoll(BaseModel):
    """A first-level hit point roll: every die that was thrown, and the total it came to.

    [`roll_hit_points`][osrlib.core.character.roll_hit_points] returns this. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    rolls: tuple[int, ...]
    """Every raw die result, in the order thrown. With the `hp_reroll_at_first_level` option on, the rejected 1s and 2s
    are here too, and the last entry is the die that stood.
    """

    hit_points: int = Field(ge=1)
    """The hit points the character starts with: the die that stood plus the CON modifier, floored at 1."""


class CharacterCreationResult(BaseModel):
    """What [`create_character`][osrlib.core.character.create_character] returns: the character and its dice.

    The rolls are here so a front end can show a player how their character came out rather than
    only the finished numbers. Frozen.
    """

    model_config = ConfigDict(frozen=True)

    character: Character
    """The finished [`Character`][osrlib.core.character.Character], at first level with its purchases bought and its
    equipment worn.
    """

    ability_rolls: AbilityScoreRolls
    """The ability scores as rolled, before any adjustment the caller asked for. Compare against `character.scores` to
    show what the adjustment moved.
    """

    hit_point_roll: HitPointRoll
    """The first-level hit die, or dice if the re-roll option was on."""

    gold_roll: RollResult
    """The 3d6 × 10 starting money roll. Its `total` is the gold the character began with, before the purchases were
    paid for.
    """


def roll_ability_scores(stream: RngStream) -> AbilityScoreRolls:
    """Roll 3d6 for each of the six abilities, in the SRD's order: STR, INT, WIS, DEX, CON, CHA.

    This is the first step of creating a character by hand. Show the result to the player, then
    pass the scores to [`validate_class_choice`][osrlib.core.character.validate_class_choice] to
    find out which classes they qualify for. If the player wants to trade points between abilities,
    run [`apply_adjustment`][osrlib.core.abilities.apply_adjustment] after the class is chosen, the
    order the SRD sets.

    Call [`create_character`][osrlib.core.character.create_character] instead when the choices are
    already known and you only want the finished character.

    The draw order never changes. It is what makes a seeded creation reproducible, so the same
    stream at the same position always yields the same six scores.

    Args:
        stream: The stream to draw from, conventionally
            `streams.get(CHARACTER_CREATION_STREAM)`. Eighteen draws are consumed.

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
    """Check whether a set of rolled scores meets a class's minimum requirements.

    Call it after [`roll_ability_scores`][osrlib.core.character.roll_ability_scores] and before the
    ability adjustment, which is the order the SRD sets: a player picks a class they qualify for,
    then trades points. Run it over every class in
    [`load_classes`][osrlib.data.load_classes]`().classes` to build the list of classes to offer.

    It reports rather than raises, so you can show a player why a class is closed to them. Each
    failure names the ability, the minimum the class requires, and the score that fell short. The
    demi-human classes are the ones with requirements: the dwarf and the halfling require CON 9, the
    elf requires INT 9, and the halfling also requires DEX 9.

    Checking before adjustment is safe for the Classic classes, because the adjustment step can
    only lower STR, INT, and WIS and never below 9, which is every requirement's minimum. A class
    added later with a higher minimum on a lowerable ability would need a second check after the
    adjustment.

    Args:
        scores: The rolled scores, before any adjustment.
        definition: The class the player chose, from
            [`load_classes`][osrlib.data.load_classes]`().get(class_id)`.

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] per unmet requirement, empty when the
        class is open to these scores.

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
    """Roll a first-level character's hit points: the class hit die plus the CON modifier, at least 1.

    Call it after the class is chosen and the scores are adjusted, so the CON modifier you pass is
    the final one. Get that modifier from
    [`load_ability_tables`][osrlib.data.load_ability_tables]`().hit_point_modifier(score)`, or read
    [`hit_point_modifier`][osrlib.core.character.Character.hit_point_modifier] off a character that
    already exists.

    Use [`level_up`][osrlib.core.classes.level_up] for hit points gained later. This function is
    for first level only, and it is the only one that honours the re-roll option.

    With `hp_reroll_at_first_level` on in the ruleset, a die showing 1 or 2 is thrown again, and
    again, until it shows 3 or more. The test is on the raw die, before the CON modifier, and every
    throw is kept in the result.

    Args:
        definition: The character's class, whose progression row gives the hit die.
        con_modifier: The CON hit point modifier for the final scores. It may be negative, and the
            total is floored at 1 either way.
        ruleset: The ruleset in play, read for the `hp_reroll_at_first_level` option.
        stream: The stream to draw from, conventionally
            `streams.get(CHARACTER_CREATION_STREAM)`.

    Returns:
        Every die thrown and the hit points to start with.

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
    """Check the extra languages a high INT lets a character pick.

    A character speaks their alignment tongue and whatever their class grants for free. An INT of
    13 or more buys extra languages on top, one to three of them. An INT of 12 or less buys none.
    Ask
    [`load_ability_tables`][osrlib.data.load_ability_tables]`().additional_languages(int_score)`
    how many the player may take, offer the choosable entries from
    [`load_languages`][osrlib.data.load_languages], then check the picks here before storing them
    on [`extra_languages`][osrlib.core.character.Character].

    A pick is refused when it is not a choosable language, when it repeats another pick, when the
    class already grants it, or when the player took more than the score allows. Each refusal
    names the language, so you can show the player which pick to change.

    Args:
        definition: The chosen class. Its own languages may not be taken again as extras.
        int_score: The final INT score, after any adjustment.
        choices: The chosen language ids, from [`load_languages`][osrlib.data.load_languages]; see
            [the language id index][languages-index].

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] per problem, empty when every pick
        stands.

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
    """Check a starting spell book against what the class may have at first level.

    An arcane caster, which in the Classic classes means the magic-user and the elf, begins play
    with as many spells written in the book as they can memorize, so at first level that is exactly
    one first-level spell. Offer the player the first-level spells of their class's list from
    [`load_spells`][osrlib.data.load_spells], check the pick here, then write it with
    [`choose_starting_spells`][osrlib.core.character.choose_starting_spells]. Whether the player or
    the referee picks is the game's decision. This function only says whether a pick is legal.

    A pick is refused when the spell id is unknown, when it repeats, when it belongs to the other
    spell list, or when the number chosen at any level does not match the capacity exactly. Too few
    is refused as well as too many, because the SRD gives a starting book a fixed size. A cleric
    starts with no book at all, since clerical spells come from a deity rather than from writing,
    so any pick for a cleric or for a non-caster is refused outright.

    Args:
        definition: The character's class.
        catalog: The spell catalog from [`load_spells`][osrlib.data.load_spells].
        spell_ids: The chosen spell ids; see [the spell id index][spells-index].

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] per problem, empty when the book is
        legal.

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

    Use it when you are creating a character step by step and the player has picked their first
    spell. It checks the pick with
    [`validate_starting_spells`][osrlib.core.character.validate_starting_spells] and refuses a
    character whose book is already written, then stores the ids on
    [`spell_book`][osrlib.core.character.Character]. Nothing is written when anything is refused,
    so the character is never left half-changed.
    [`create_character`][osrlib.core.character.create_character] does this for you when you pass
    `starting_spell_ids`.

    A written book is not yet a memorized spell. To prepare spells for casting, go through
    [`osrlib.core.spells`][osrlib.core.spells], which fills the character's memorization slots from
    the book.

    Args:
        character: The character to write to. Mutated in place when the pick is legal.
        definition: The character's class.
        catalog: The spell catalog from [`load_spells`][osrlib.data.load_spells].
        spell_ids: The chosen spell ids; see [the spell id index][spells-index].

    Returns:
        One [`Rejection`][osrlib.core.validation.Rejection] per problem, empty when the book was
        written.

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

    This is the last draw of creation. Put the total into the character's purse
    (`character.inventory.purse.gp`), then spend it with
    [`validate_purchase`][osrlib.core.items.validate_purchase] and
    [`purchase`][osrlib.core.items.purchase] from the equipment catalog.
    [`create_character`][osrlib.core.character.create_character] does all of that for you when you
    pass `purchases`.

    Args:
        stream: The stream to draw from, conventionally
            `streams.get(CHARACTER_CREATION_STREAM)`. Three draws are consumed.

    Returns:
        The [`RollResult`][osrlib.core.dice.RollResult], whose `total` is the starting gold in gold
        pieces, between 30 and 180.

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
    """Create a first-level character, making every choice you pass in one call.

    This is where a new caller starts. Give it a name, a class, an alignment, a ruleset, and a
    seeded stream, and it rolls a whole character: ability scores, hit points, starting gold, and
    the gear you asked it to buy. Put the characters you get into a
    [`Party`][osrlib.crawl.party.Party] and you have something to play with.

    To get a stream, build an [`RngStreams`][osrlib.core.rng.RngStreams] set from a seed and ask it
    for the creation stream: `RngStreams(master_seed=2).get(CHARACTER_CREATION_STREAM)`. The same
    seed always produces the same character, which is what makes a game replayable and a test
    repeatable. Pass the same stream to several calls to roll a whole party from one seed, and each
    call continues where the last one left off.

    Every decision is yours to supply, because the same call has to serve a player picking from a
    menu and a script rolling a hundred characters. Drive the stepwise functions in this module
    instead when a person is choosing as they go: those hand back
    [`Rejection`][osrlib.core.validation.Rejection] records you can show, where this function
    raises on the first illegal choice and reports nothing more.

    The steps run in the SRD's order: roll the six scores, check them against the class
    requirements, apply the ability adjustment, write the spell book, roll hit points, check the
    extra languages, roll starting gold, then buy and equip. Draws come off the stream in that
    order, so scores are always drawn first and gold last. Writing the spell book and checking
    languages consume no draws.

    Args:
        name: The character's name.
        class_id: The class to play, like `"fighter"`, from
            [`load_classes`][osrlib.data.load_classes]; see [the class id index][classes-index].
        alignment: Lawful, neutral, or chaotic.
        ruleset: The ruleset in play. Its `hp_reroll_at_first_level` flag governs whether a poor
            hit die is thrown again.
        stream: The stream for the creation draws, conventionally
            `streams.get(`[`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM]`)`.
        adjustment: An optional trade of points between abilities, built as an
            [`AbilityAdjustment`][osrlib.core.abilities.AbilityAdjustment]. It lowers one or more
            of STR, INT, and WIS to raise a prime requisite, which is how a player buys a better
            experience bonus.
        starting_spell_ids: The spells written in an arcane caster's book, from
            [`load_spells`][osrlib.data.load_spells]; see [the spell id index][spells-index]. It
            must contain exactly what the class can memorize at first level, which is one first-level
            spell for both the magic-user and the elf. Leave it empty for every other class.
        extra_languages: The extra languages a high INT bought, from
            [`load_languages`][osrlib.data.load_languages]; see
            [the language id index][languages-index]. An INT of 12 or less allows none.
        purchases: What to buy from the starting gold, as `(item_id, lots)` pairs bought in the
            order given. `item_id` comes from [`load_equipment`][osrlib.data.load_equipment]; see
            [the equipment id index][equipment-index]. A lot is the catalog's unit of sale: weapons
            and armour sell one at a time, so `lots` is how many, while gear and ammunition
            sell in fixed bundles, and one lot of torches is six torches.
        equip_ids: Which of the bought items to wear or wield, in order. An item must have been
            bought first, and the class's armour and weapon policies must allow it.

    Returns:
        The finished character together with the dice creation rolled, so you can show a player how
        they came out.

    Raises:
        ValueError: On the first illegal decision: an unknown class, spell, language, or item id;
            scores that miss the class requirements; an adjustment the class forbids; a spell book
            of the wrong size; more languages than the INT allows; a purchase the starting gold
            cannot cover; or an item the class may not equip. Drive the stepwise functions yourself
            when you need to know which choices failed and why.

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
