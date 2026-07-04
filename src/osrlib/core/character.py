"""The player character: model, creation procedure, and stamped serialization.

Creation is pure functions the game drives stepwise (sans-I/O: the game owns prompting
and choice), mirroring the SRD's Creating a Character steps where they're mechanical.
Kernel functions return structured results including the raw rolls, so front ends can
show them; character creation emits no events — it is out-of-fiction and
pre-session; the first real events belong to play.

Derived values — modifiers, AC, movement rates, literacy, languages — are properties
computed from the stored state, never stored themselves, so they can never desync.
Model validation is structural only (score ranges, level within class bounds, HP
bounds); procedure legality — was the adjustment legal, were requirements met — is
enforced by the creation functions at the time of the step, because a finished
character cannot re-derive its own history.

RNG stream keys are pinned as module-level conventions (sessions adopt them):
[`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM] for
creation draws and [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM]
for level-up hit point rolls — separated so a creation-rules change never shifts
in-play advancement draws in a golden scenario.
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
from osrlib.core.rng import RngStream
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

CHARACTER_CREATION_STREAM = "character_creation"
"""Stream key convention for creation draws: ability scores, first-level hp, starting gold."""

ADVANCEMENT_STREAM = "advancement"
"""Stream key convention for in-play advancement draws: level-up hit point rolls."""

ABILITY_ROLL_ORDER = (
    AbilityScore.STR,
    AbilityScore.INT,
    AbilityScore.WIS,
    AbilityScore.DEX,
    AbilityScore.CON,
    AbilityScore.CHA,
)
"""The pinned draw order for rolling ability scores — the SRD's listing order."""


class Character(BaseModel):
    """A player character.

    `id` defaults to `None`: entity IDs are session-scoped, assigned when the
    character joins a session. `carrying_treasure` is basic encumbrance's referee judgment,
    set by the game. `spell_book` holds spell ids (arcane casters only; tuple order
    is acquisition order) and `memorized_spells` the prepared copies (tuple order is
    memorization order and is load-bearing: casting consumes the first matching copy
    and drain forgets newest-first). Spell slots stay derived —
    `definition.row(level).spell_slots`, never stored — so leveling and drain
    recompute them for free.
    """

    model_config = ConfigDict(validate_assignment=True, extra="ignore")

    id: str | None = None
    name: str = Field(min_length=1)
    class_id: str
    race: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    level: int = Field(ge=1)
    xp: int = Field(ge=0)
    scores: dict[AbilityScore, int]
    alignment: Alignment
    extra_languages: tuple[str, ...] = ()
    max_hp: int = Field(ge=1)
    current_hp: int = Field(ge=0)
    inventory: Inventory = Field(default_factory=Inventory)
    carrying_treasure: bool = False
    conditions: tuple[ActiveCondition, ...] = ()
    stat_modifiers: tuple[ActiveModifier, ...] = ()
    spell_book: tuple[str, ...] = ()
    memorized_spells: tuple[MemorizedSpell, ...] = ()

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
        """The character's class definition, from the loaded class catalog."""
        return load_classes().get(self.class_id)

    @property
    def thac0(self) -> int:
        """THAC0 from the progression row for the current level — derived, never stored."""
        return self.definition.row(self.level).thac0

    @property
    def attack_bonus(self) -> int:
        """Ascending-AC attack bonus from the progression row — derived, never stored."""
        return self.definition.row(self.level).attack_bonus

    @property
    def saves(self) -> SavingThrows:
        """Saving throws from the progression row for the current level — derived, never stored."""
        return self.definition.row(self.level).saves

    def _tables(self) -> AbilityTables:
        return load_ability_tables()

    @property
    def melee_modifier(self) -> int:
        """STR modifier to melee attack and damage rolls."""
        return self._tables().melee_modifier(self.scores[AbilityScore.STR])

    @property
    def open_doors_chance(self) -> int:
        """STR-derived X-in-6 chance to force a stuck door."""
        return self._tables().open_doors_chance(self.scores[AbilityScore.STR])

    @property
    def missile_modifier(self) -> int:
        """DEX modifier to missile attack rolls."""
        return self._tables().missile_modifier(self.scores[AbilityScore.DEX])

    @property
    def initiative_modifier(self) -> int:
        """DEX modifier to individual initiative (optional rule)."""
        return self._tables().initiative_modifier(self.scores[AbilityScore.DEX])

    @property
    def hit_point_modifier(self) -> int:
        """CON modifier per Hit Die rolled."""
        return self._tables().hit_point_modifier(self.scores[AbilityScore.CON])

    @property
    def magic_save_modifier(self) -> int:
        """WIS modifier to saving throws versus magical effects."""
        return self._tables().magic_save_modifier(self.scores[AbilityScore.WIS])

    @property
    def npc_reaction_modifier(self) -> int:
        """CHA modifier to NPC reactions."""
        return self._tables().npc_reaction_modifier(self.scores[AbilityScore.CHA])

    @property
    def literacy(self) -> Literacy:
        """INT-derived literacy in the character's native languages."""
        return self._tables().literacy(self.scores[AbilityScore.INT])

    @property
    def alignment_tongue(self) -> str:
        """The alignment language, derived from alignment so it can never desync.

        Alignment tongues are not `languages.json` entries; the derived identifier is
        `alignment_` plus the alignment wire value (`alignment_lawful`).
        """
        return f"alignment_{self.alignment.value}"

    @property
    def languages(self) -> tuple[str, ...]:
        """Every language the character speaks.

        The alignment tongue, then the class natives (Common first, per the class
        pages), then INT-granted extras.
        """
        return (self.alignment_tongue, *self.definition.languages, *self.extra_languages)

    def _armour_parts(self, *, ascending: bool) -> tuple[int, int]:
        """Return `(base, bonus)`: worn armour base AC and total shield/item bonuses.

        Magic armour overlays its base item's AC with the enchantment bonus (the
        cursed `AC 9 [10]` forms set the base outright); magic shields add their
        enchantment on top of the mundane shield's +1; always-active worn items
        with an AC bonus (rings of protection) join the bonus.
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
        """Descending AC: armour base (9 unarmoured), −1 per bonus, minus the DEX modifier."""
        dex_modifier = self._tables().ac_modifier(self.scores[AbilityScore.DEX])
        base, bonus = self._armour_parts(ascending=False)
        return base - bonus - dex_modifier

    @property
    def armour_class_ascending(self) -> int:
        """Ascending AC: armour base (10 unarmoured), +1 per bonus, plus the DEX modifier."""
        dex_modifier = self._tables().ac_modifier(self.scores[AbilityScore.DEX])
        base, bonus = self._armour_parts(ascending=True)
        return base + bonus + dex_modifier

    def movement_rate(self, ruleset: Ruleset) -> int:
        """Return the movement rate in feet per turn under the ruleset's encumbrance mode.

        Args:
            ruleset: The ruleset in play.

        Returns:
            The movement rate: 120, 90, 60, 30, or 0.
        """
        return movement_rate_feet(self.inventory, ruleset, self.carrying_treasure)

    def to_document(self) -> dict[str, object]:
        """Serialize to a stamped document with schema and engine versions.

        Returns:
            The stamped document envelope wrapping the serialized character.
        """
        return stamp_document("character", self.model_dump(mode="json"))

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> Character:
        """Load a character from a stamped document.

        Unknown payload fields are ignored, per the additive-schema contract.

        Args:
            document: A document produced by
                [`to_document`][osrlib.core.character.Character.to_document].

        Returns:
            The reconstructed character.

        Raises:
            ContentValidationError: If the envelope or payload is malformed or of the
                wrong kind.
            SaveVersionError: If the document's schema version is newer than this
                library understands.
        """
        payload = check_document(document, "character")
        try:
            return cls.model_validate(payload)
        except ValueError as error:
            raise ContentValidationError(f"character document payload failed validation: {error}") from error


def party_to_document(characters: Sequence[Character]) -> dict[str, object]:
    """Serialize a party — a stamped collection of characters.

    The crawl-layer party model (marching order, shared resources) is
    [`Party`][osrlib.crawl.party.Party]; this is the kernel's party-as-collection.

    Args:
        characters: The party members, in order.

    Returns:
        The stamped document envelope wrapping the serialized characters.
    """
    return stamp_document("party", {"characters": [character.model_dump(mode="json") for character in characters]})


def party_from_document(document: Mapping[str, object]) -> list[Character]:
    """Load a party from a stamped document.

    Args:
        document: A document produced by
            [`party_to_document`][osrlib.core.character.party_to_document].

    Returns:
        The reconstructed characters, in order.

    Raises:
        ContentValidationError: If the envelope or payload is malformed or of the
            wrong kind.
        SaveVersionError: If the document's schema version is newer than this library
            understands.
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
    """The rolled score set, with each score's raw 3d6 kept for display."""

    model_config = ConfigDict(frozen=True)

    scores: dict[AbilityScore, int]
    rolls: dict[AbilityScore, tuple[int, int, int]]


class HitPointRoll(BaseModel):
    """A first-level hit point roll: every raw die (re-rolls included) and the final total."""

    model_config = ConfigDict(frozen=True)

    rolls: tuple[int, ...]
    hit_points: int = Field(ge=1)


class CharacterCreationResult(BaseModel):
    """A created character plus the raw rolls creation consumed, for display."""

    model_config = ConfigDict(frozen=True)

    character: Character
    ability_rolls: AbilityScoreRolls
    hit_point_roll: HitPointRoll
    gold_roll: RollResult


def roll_ability_scores(stream: RngStream) -> AbilityScoreRolls:
    """Roll 3d6 for each ability, drawn in the SRD's order STR INT WIS DEX CON CHA.

    The draw order is pinned — part of the determinism contract for the
    `character_creation` stream.

    Args:
        stream: The RNG stream to draw from.

    Returns:
        The rolled scores and each score's individual dice.
    """
    scores: dict[AbilityScore, int] = {}
    rolls: dict[AbilityScore, tuple[int, int, int]] = {}
    for ability in ABILITY_ROLL_ORDER:
        result = roll("3d6", stream)
        scores[ability] = result.total
        rolls[ability] = (result.rolls[0], result.rolls[1], result.rolls[2])
    return AbilityScoreRolls(scores=scores, rolls=rolls)


def validate_class_choice(scores: dict[AbilityScore, int], definition: ClassDefinition) -> list[Rejection]:
    """Validate a class choice against the class's minimum score requirements.

    Args:
        scores: The rolled scores. Requirements are checked before adjustment,
            mirroring the SRD's step order (choose class, then adjust). For Classic
            data the adjustment step can never break them — every requirement minimum
            is 9, only STR, INT, and WIS may be lowered, and never below 9 — but an
            Advanced class with a higher minimum on a lowerable non-prime ability
            would need a re-check after adjustment.
        definition: The chosen class.

    Returns:
        Structured rejections; empty when the choice is legal.
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
    """Roll first-level hit points: the class hit die plus the CON modifier, minimum 1.

    With the `hp_reroll_at_first_level` flag on, the die is re-rolled while the raw
    die shows 1–2 (before the CON modifier), each re-roll consuming a draw — the
    pinned reading of the SRD's "re-rolling 1s and 2s".

    Args:
        definition: The character's class.
        con_modifier: The CON hit point modifier for the (adjusted) scores.
        ruleset: The ruleset in play.
        stream: The RNG stream to draw from.

    Returns:
        Every raw die rolled and the final hit point total.
    """
    die = definition.row(1).hit_dice.die
    rolls = [stream.randbelow(die) + 1]
    if ruleset.hp_reroll_at_first_level:
        while rolls[-1] <= 2:
            rolls.append(stream.randbelow(die) + 1)
    return HitPointRoll(rolls=tuple(rolls), hit_points=max(1, rolls[-1] + con_modifier))


def validate_extra_languages(definition: ClassDefinition, int_score: int, choices: Sequence[str]) -> list[Rejection]:
    """Validate INT-granted extra language choices.

    Extras must come from the Other Languages table (the twenty choosable languages in
    `languages.json`), may not duplicate a class native (pinned), may not repeat, and
    may not exceed the INT table's additional-languages allowance.

    Args:
        definition: The chosen class, whose natives the choices may not duplicate.
        int_score: The character's (adjusted) INT score.
        choices: The chosen extra language ids.

    Returns:
        Structured rejections; empty when the choices are legal.
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
    """Validate a starting spell-book choice against class and capacity rules.

    Arcane casters "begin play with as many spells in their spell book as they are
    able to memorize" (`Spell_Books.md`) — the per-level counts must equal the
    level-1 slot counts exactly, which for both magic-user and elf means one
    first-level spell. The caller supplies the choice: "The referee may choose these
    spells or may allow the player to select" — the game owns the decision, the
    kernel validates it. Clerics (and non-casters) start with nothing: any selection
    for them is rejected.

    Args:
        definition: The character's class.
        catalog: The loaded spell catalog.
        spell_ids: The chosen spell ids.

    Returns:
        Structured rejections; empty when the choice is legal.
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
    """Fill an arcane caster's starting spell book — the last creation step.

    The stepwise creation surface: validates the choice (see
    [`validate_starting_spells`][osrlib.core.character.validate_starting_spells]) and
    a still-empty book, then writes it. Creation stays event-less like every other
    creation step.

    Args:
        character: The created character; its `spell_book` is written.
        definition: The character's class.
        catalog: The loaded spell catalog.
        spell_ids: The chosen spell ids.

    Returns:
        Structured rejections; empty when the book was written.
    """
    if character.spell_book:
        return [Rejection(code="magic.book.already_chosen", params={"character": character.name})]
    rejections = validate_starting_spells(definition, catalog, spell_ids)
    if rejections:
        return rejections
    character.spell_book = tuple(spell_ids)
    return []


def roll_starting_gold(stream: RngStream) -> RollResult:
    """Roll starting money: 3d6 × 10 gold pieces, via the dice grammar.

    Args:
        stream: The RNG stream to draw from.

    Returns:
        The roll, whose total is the starting gold in gp.
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
    """Create a 1st-level character with all decisions supplied upfront.

    A convenience for scripts and tests: calls the same stepwise creation functions in
    the SRD's order — roll scores, validate the class choice, adjust scores, choose
    the spell book (the SRD's step 6, before hit points; it consumes no draws), roll
    hit points, validate languages, roll starting gold, buy and equip — drawing
    scores, hit points, and gold from `stream` in that pinned order.

    Args:
        name: The character's name.
        class_id: The chosen class id.
        alignment: The chosen alignment.
        ruleset: The ruleset in play.
        stream: The RNG stream for creation draws, conventionally the
            `character_creation` stream.
        adjustment: The optional ability score adjustment.
        starting_spell_ids: The arcane starting spell book (exactly the level-1
            memorization capacity — one first-level spell for magic-user and elf).
        extra_languages: INT-granted extra language choices.
        purchases: `(item_id, lots)` pairs bought in order from the starting gold.
        equip_ids: Item ids to equip after purchase, in order.

    Returns:
        The created character and the raw creation rolls.

    Raises:
        ValueError: If any decision is illegal for the rolled scores — unknown ids, a
            failed class requirement, an illegal adjustment, spell choices, language
            choices, an unaffordable purchase, or a forbidden equip. Callers wanting
            structured reasons drive the stepwise functions themselves.
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
