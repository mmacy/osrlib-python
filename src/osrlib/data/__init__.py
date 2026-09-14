"""The compiled rules content, and the loaders that hand it to you.

Every id-typed argument in osrlib names an entry in one of these catalogs: a class id, a
spell id, a monster id, an equipment or magic item id, a language id, a treasure-type
letter. Each `load_*` function returns one catalog, and the content-id pages list every id
that ships: [class ids][classes-index], [spell ids][spells-index],
[monster ids][monsters-index], [equipment ids][equipment-index],
[magic item ids][magic-items-index], [language ids][languages-index], and
[treasure types][treasure-types-index].

The path is the same for all of them. Call the loader, look one entry up by id, then hand
that entry to the kernel function that takes it: `load_classes().get("fighter")` gives you
the [`ClassDefinition`][osrlib.core.classes.ClassDefinition] that
[`level_up`][osrlib.core.classes.level_up] wants, and `load_monsters().get("goblin")` the
[`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] that
[`spawn_monster`][osrlib.core.monsters.spawn_monster] wants. A
[`GameSession`][osrlib.crawl.session.GameSession] loads what it needs on its own, so you
call these loaders yourself when you drive the rules without a session, or when you want
to show a player what content exists before play starts.

Each loader caches. The first call reads the data and validates it, and every later call in
the process returns that same catalog object. What comes back is frozen and shared with
every other caller, so you **cannot** edit a catalog in place. Play spawns mutable instances
from these templates instead, the way
[`spawn_monster`][osrlib.core.monsters.spawn_monster] spawns a
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance] from a template.

The data files ship inside this package, and a loader reads only what the installed package
includes. The project's compiler generates them from the Old-School Essentials SRD before
each release, and nobody edits them by hand, so a patch you apply to a JSON file in an
installed copy is gone at the next upgrade. They aren't the extension point. To add content
of your own, construct the frozen model yourself and pass it to the same kernel functions the
shipped entries go to, and bundle a custom monster or item template with the
[`Adventure`][osrlib.crawl.adventure.Adventure] that uses it. A file that's missing, or that
fails model validation, raises
[`ContentValidationError`][osrlib.errors.ContentValidationError] rather than returning a
half-built catalog.

The compiled data is Open Game Content under the Open Game License 1.0a. The license text
and Section 15 notice ship in this package as `LICENSE-OGL.md`.

Typical usage:

```python
from osrlib.data import load_classes, load_monsters

fighter = load_classes().get("fighter")
print(fighter.name, fighter.hit_die)
# Fighter 8

goblin = load_monsters().get("goblin")
print(goblin.name, goblin.ac, goblin.morale)
# Goblin 6 7
```
"""

import json
from functools import cache
from importlib import resources

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from osrlib.core.abilities import AbilityTables
from osrlib.core.classes import ClassCatalog
from osrlib.core.items import EquipmentCatalog, MagicItemCatalog
from osrlib.core.monsters import MonsterCatalog
from osrlib.core.spells import SpellCatalog
from osrlib.core.tables import CombatTables, EncounterTables
from osrlib.core.treasure import TreasureTables
from osrlib.errors import ContentValidationError

__all__ = [
    "Language",
    "LanguageCatalog",
    "load_ability_tables",
    "load_classes",
    "load_combat_tables",
    "load_encounter_tables",
    "load_equipment",
    "load_languages",
    "load_magic_items",
    "load_monsters",
    "load_spells",
    "load_treasure_tables",
]


class Language(BaseModel):
    """One spoken language from the shipped catalog.

    You get one from [`LanguageCatalog.get`][osrlib.data.LanguageCatalog.get], or by
    reading [`LanguageCatalog.languages`][osrlib.data.LanguageCatalog.languages] when you
    want to offer a player the whole list. Its `id` is what
    [`create_character`][osrlib.core.character.create_character] and
    [`validate_extra_languages`][osrlib.core.character.validate_extra_languages] accept in
    `extra_languages`.

    An alignment tongue isn't an entry here. Each one follows from
    [`Alignment`][osrlib.core.alignment.Alignment] on its own, and a character speaks the
    tongue of the alignment it has.

    The model is frozen: assigning to a field raises pydantic's `ValidationError`.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The id to pass wherever a language id is taken, like `"gnoll"` or `"common"`."""

    name: str
    """The display name to show a player, like `"Gnoll"`."""

    choosable: bool
    """Whether a character with a high enough INT may take this language as an extra.

    True for the SRD's Other Languages, the pool a high-INT character chooses from. False
    for Common, which every character speaks already and so can never be chosen again.
    """


class LanguageCatalog(BaseModel):
    """The whole language list, with lookup by id.

    [`load_languages`][osrlib.data.load_languages] returns this catalog, and
    [`get`][osrlib.data.LanguageCatalog.get] pulls one
    [`Language`][osrlib.data.Language] out of it by id. Ids are unique across the catalog,
    which the model checks when it validates.

    The model is frozen, and the loader shares one instance with every caller, so read it
    and don't try to add to it.
    """

    model_config = ConfigDict(frozen=True)

    languages: tuple[Language, ...]
    """Every language in the catalog, in the order the data file lists them.

    The shipped file is in alphabetical order by id, and Common sits among the rest. Filter
    on [`Language.choosable`][osrlib.data.Language.choosable] for the ones a high-INT
    character may take.
    """

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> LanguageCatalog:
        ids = [language.id for language in self.languages]
        if len(set(ids)) != len(ids):
            raise ValueError("language ids must be unique")
        return self

    def get(self, language_id: str) -> Language:
        """Return the language with `language_id`.

        Use this to turn a stored id back into a display name, or to check that a language
        a player picked exists before you pass it to
        [`create_character`][osrlib.core.character.create_character]. To validate a whole
        set of picks against a class and an INT score at once, call
        [`validate_extra_languages`][osrlib.core.character.validate_extra_languages]
        instead: it returns structured refusals rather than raising on the first bad id.

        Args:
            language_id: A language id from [the language id index][languages-index], such
                as `"gnoll"`.

        Returns:
            The language.

        Raises:
            ValueError: If no language has that id. An unknown id is programmer misuse,
                not a player's choice, so it raises rather than refusing.

        Examples:
            ```python
            from osrlib.data import load_languages

            gnoll = load_languages().get("gnoll")
            print(gnoll.name, gnoll.choosable)
            # Gnoll True
            ```
        """
        for language in self.languages:
            if language.id == language_id:
                return language
        raise ValueError(f"unknown language id {language_id!r}")


def _read(filename: str) -> dict[str, object]:
    resource = resources.files("osrlib.data").joinpath(filename)
    try:
        text = resource.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ContentValidationError(f"missing generated data file {filename!r}") from error
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ContentValidationError(f"{filename} must contain a JSON object at the top level")
    data.pop("_meta", None)
    return data


@cache
def load_ability_tables() -> AbilityTables:
    """Load the six ability modifier tables and the prime requisite XP table.

    The returned [`AbilityTables`][osrlib.core.abilities.AbilityTables] answers what a
    score is worth: the STR melee modifier, the STR open-doors chance, the extra languages
    and literacy INT grants, the DEX missile and initiative modifiers, the CON hit point
    modifier, the CHA reaction modifier and retainer limits, and the prime requisite XP
    percentage. Call it when you draw a character sheet, or when you resolve the rules
    without a session: [`create_character`][osrlib.core.character.create_character] and
    [`level_up`][osrlib.core.classes.level_up] read these tables for you.

    Every accessor takes a score in 3 to 18 and raises stdlib `ValueError` outside it. No
    content-id page covers this catalog, because a score is the only key it has.

    Returns:
        The frozen tables, cached: the first call validates the data and every later call
            returns the same object.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_ability_tables

        tables = load_ability_tables()
        print(tables.melee_modifier(13), tables.open_doors_chance(13))
        # 1 3
        ```
    """
    data = _read("abilities.json")
    try:
        return AbilityTables.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"abilities.json failed validation: {error}") from error


@cache
def load_classes() -> ClassCatalog:
    """Load the character class catalog.

    Call `ClassCatalog.get` on the result for the
    [`ClassDefinition`][osrlib.core.classes.ClassDefinition] that the class-level functions
    take: [`level_up`][osrlib.core.classes.level_up],
    [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct],
    [`level_title`][osrlib.core.classes.level_title],
    [`thief_skill_check`][osrlib.core.classes.thief_skill_check], and
    [`caster_profile`][osrlib.core.spells.caster_profile] all want the definition, not the
    id. [`create_character`][osrlib.core.character.create_character] is the exception: it
    takes `class_id` and looks the definition up itself, so you need this only to show a
    player the classes on offer.

    The catalog contains the classes this package ships. A class you write yourself is a
    `ClassDefinition` you build and pass to those same functions. You **cannot** add it
    here.

    Returns:
        The frozen class catalog, cached: the first call validates the data and every later
            call returns the same object. [The class id index][classes-index] lists every id
            it defines.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_classes

        fighter = load_classes().get("fighter")
        print(fighter.name, fighter.hit_die)
        # Fighter 8
        ```
    """
    data = _read("classes.json")
    try:
        return ClassCatalog.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"classes.json failed validation: {error}") from error


@cache
def load_equipment() -> EquipmentCatalog:
    """Load the mundane equipment catalog: weapons, armour, gear, ammunition, and treasure weights.

    `EquipmentCatalog.get` looks an id up across all four item lists and returns the
    template that [`purchase`][osrlib.core.items.purchase],
    [`validate_purchase`][osrlib.core.items.validate_purchase],
    [`equip`][osrlib.core.items.equip], and
    [`validate_equip`][osrlib.core.items.validate_equip] take. `treasure_weights` is the
    separate list that [`treasure_weight_coins`][osrlib.core.items.treasure_weight_coins]
    reads to weigh coins and gems, which a character picks up rather than buys.

    Magic items live in their own catalog, [`load_magic_items`][osrlib.data.load_magic_items].
    An adventure that ships item templates of its own keeps them in its own content, and a
    session answers those ids too. This catalog contains only what the package ships.

    Returns:
        The frozen equipment catalog, cached: the first call validates the data and every
            later call returns the same object. [The equipment id index][equipment-index]
            lists every id it defines.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_equipment

        sword = load_equipment().get("sword")
        print(sword.name, sword.cost_gp)
        # Sword 10
        ```
    """
    data = _read("equipment.json")
    try:
        return EquipmentCatalog.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"equipment.json failed validation: {error}") from error


@cache
def load_monsters() -> MonsterCatalog:
    """Load the monster catalog.

    `MonsterCatalog.get` returns the frozen
    [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] that
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] turns into a mutable
    [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] with rolled hit points and a
    session-unique id. The instance is what you fight. The template stays shared and
    unchanged however many you spawn from it.

    A dungeon that keys a monster by id, and an encounter table that names one, both resolve
    against this catalog plus whatever templates the adventure bundles. Read the template
    directly when you want to show a statistic block, or to plan an encounter before any
    monster exists.

    Returns:
        The frozen monster catalog, cached: the first call validates the data and every
            later call returns the same object. [The monster id index][monsters-index] lists
            every id it defines.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_monsters

        goblin = load_monsters().get("goblin")
        print(goblin.name, goblin.ac, goblin.morale)
        # Goblin 6 7
        ```
    """
    data = _read("monsters.json")
    try:
        return MonsterCatalog.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"monsters.json failed validation: {error}") from error


@cache
def load_combat_tables() -> CombatTables:
    """Load the combat tables: the attack matrix, monster saves, XP awards, turning, and reactions.

    [`monster_xp`][osrlib.core.tables.monster_xp] takes these tables and a monster's hit
    dice and returns the award. `CombatTables.save_band` and `CombatTables.xp_row` take the
    labels [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label] and
    [`xp_band_label`][osrlib.core.tables.xp_band_label] compute from hit dice, so you look a
    band up by asking for its label first rather than by matching hit dice yourself.

    Most of combat needs no table in hand: [`attack_roll`][osrlib.core.combat.attack_roll]
    and [`saving_throw`][osrlib.core.combat.saving_throw] read what they need themselves.
    Load the tables when you want to show the numbers, or to award XP outside a session.
    No content-id page covers this catalog, because its rows are keyed by hit dice and armour
    class rather than by id.

    Returns:
        The frozen combat tables, cached: the first call validates the data and every later
            call returns the same object.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.core.tables import monster_xp
        from osrlib.data import load_combat_tables, load_monsters

        goblin = load_monsters().get("goblin")
        print(monster_xp(load_combat_tables(), goblin.hit_dice))
        # 5
        ```
    """
    data = _read("combat_tables.json")
    try:
        return CombatTables.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"combat_tables.json failed validation: {error}") from error


@cache
def load_encounter_tables() -> EncounterTables:
    """Load the dungeon encounter tables, with the NPC adventuring party tables.

    `EncounterTables.for_level` takes a dungeon level number and returns the table the SRD
    prints for it, clamping anything deeper than the last printed band onto that band. Roll
    on the table for an entry, then call
    [`select_encounter_individuals`][osrlib.core.tables.select_encounter_individuals] to turn
    a monster entry and a count into the template ids that appear. The NPC party rows feed
    [`generate_npc_party`][osrlib.core.npc.generate_npc_party].

    A session rolls its own wandering monsters through
    [`wandering_check`][osrlib.crawl.exploration.wandering_check], so you load these tables
    when you stock a dungeon yourself or want to show what a level can throw at a party. No
    content-id page covers this catalog, because its rows are keyed by level and die roll.

    Returns:
        The frozen encounter tables, cached: the first call validates the data and every
            later call returns the same object.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_encounter_tables

        table = load_encounter_tables().for_level(1)
        print(table.min_level, table.max_level)
        # 1 1
        ```
    """
    data = _read("encounter_tables.json")
    try:
        return EncounterTables.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"encounter_tables.json failed validation: {error}") from error


@cache
def load_spells() -> SpellCatalog:
    """Load the spell catalog.

    [`memorize_spells`][osrlib.core.spells.memorize_spells] and
    [`add_spell_to_book`][osrlib.core.spells.add_spell_to_book] take this catalog whole, so
    they can check a chosen id against the caster's list and level. `SpellCatalog.get`
    returns one [`SpellTemplate`][osrlib.core.spells.SpellTemplate] by id, and
    `SpellCatalog.by_list` returns every spell on a class's list, optionally at one spell
    level, which is what you show a player choosing spells to memorize.

    A reversed spell has its own id. The catalog contains both forms, and the template says
    which is which. A spell you write yourself is a `SpellTemplate` you build and pass to the
    same functions.

    Returns:
        The frozen spell catalog, cached: the first call validates the data and every later
            call returns the same object. [The spell id index][spells-index] lists every id it
            defines.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_spells

        magic_missile = load_spells().get("magic_missile")
        print(magic_missile.name, magic_missile.spell_list, magic_missile.level)
        # Magic Missile magic_user 1
        ```
    """
    data = _read("spells.json")
    try:
        return SpellCatalog.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"spells.json failed validation: {error}") from error


@cache
def load_magic_items() -> MagicItemCatalog:
    """Load the magic item catalog: the templates, the generation sub-tables, and the sword tables.

    `MagicItemCatalog.get` returns the frozen
    [`MagicItemTemplate`][osrlib.core.items.MagicItemTemplate] behind an id, and
    [`magic_item_template`][osrlib.core.items.magic_item_template] does the same lookup for a
    [`MagicItemInstance`][osrlib.core.items.MagicItemInstance] you already have. The
    sub-tables, the armour-type table, the scroll spell-level table, and the sentient sword
    tables are what [`generate_magic_item`][osrlib.core.treasure.generate_magic_item] rolls
    on, so you rarely read them yourself.

    An id names a kind of item, not a particular one. Two potions of healing share a template
    and differ as instances, each with its own instance id, charges, and identification
    state. Mundane gear lives in [`load_equipment`][osrlib.data.load_equipment].

    Returns:
        The frozen magic item catalog, cached: the first call validates the data and every
            later call returns the same object.
            [The magic item id index][magic-items-index] lists every id it defines.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_magic_items

        potion = load_magic_items().get("potion_of_healing")
        print(potion.name, potion.category)
        # Potion of Healing potion
        ```
    """
    data = _read("magic_items.json")
    try:
        return MagicItemCatalog.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"magic_items.json failed validation: {error}") from error


@cache
def load_treasure_tables() -> TreasureTables:
    """Load the treasure tables: the treasure types, gem values, magic item types, stocking, and unguarded hoards.

    `TreasureTables.treasure_type` returns the table behind a letter, which is what a
    monster's `treasure` field names and what you read to show a player, or a referee, what a
    hoard can contain before anything is rolled.

    To roll an actual hoard, call [`generate_treasure`][osrlib.core.treasure.generate_treasure]
    with the letter instead: it loads these tables itself and returns a
    [`GeneratedTreasure`][osrlib.core.items.GeneratedTreasure] with coins, valuables, and
    magic items already drawn from a named stream.
    [`roll_room_contents`][osrlib.core.treasure.roll_room_contents] and
    [`generate_unguarded_treasure`][osrlib.core.treasure.generate_unguarded_treasure] do the
    same for the stocking and unguarded tables.

    Returns:
        The frozen treasure tables, cached: the first call validates the data and every later
            call returns the same object. [The treasure type index][treasure-types-index]
            lists every letter they key on.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_treasure_tables

        hoard = load_treasure_tables().treasure_type("A")
        print(hoard.letter, hoard.kind)
        # A hoard
        ```
    """
    data = _read("treasure.json")
    try:
        return TreasureTables.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"treasure.json failed validation: {error}") from error


@cache
def load_languages() -> LanguageCatalog:
    """Load the language catalog.

    Read [`LanguageCatalog.languages`][osrlib.data.LanguageCatalog.languages] to offer a
    player the languages a high-INT character may add, keeping the entries whose
    [`choosable`][osrlib.data.Language.choosable] is True, and
    [`LanguageCatalog.get`][osrlib.data.LanguageCatalog.get] to turn one id back into a
    display name. Pass the chosen ids to
    [`create_character`][osrlib.core.character.create_character] as `extra_languages`, or
    check them first with
    [`validate_extra_languages`][osrlib.core.character.validate_extra_languages], which
    counts them against what the character's INT allows.

    Returns:
        The frozen language catalog, cached: the first call validates the data and every
            later call returns the same object. [The language id index][languages-index]
            lists every id it defines.

    Raises:
        ContentValidationError: If the generated data is missing or fails validation.

    Examples:
        ```python
        from osrlib.data import load_languages

        catalog = load_languages()
        print(catalog.get("common").name, catalog.get("common").choosable)
        # Common False
        ```
    """
    data = _read("languages.json")
    try:
        return LanguageCatalog.model_validate(data)
    except ValidationError as error:
        raise ContentValidationError(f"languages.json failed validation: {error}") from error
