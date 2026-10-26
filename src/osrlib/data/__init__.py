"""Load the compiled B/X rules content: classes, spells, monsters, items, languages, and tables.

A content id identifies an entry in one of these catalogs: a class id, a spell id, a monster id,
an equipment or magic item id, a language id, or a treasure-type letter. An adventure can also
bundle monster and item templates, with ids that aren't in these catalogs. Each `load_*` function returns one
catalog, and the catalog's entries are the valid ids:
`[entry.id for entry in load_classes().classes]` lists every class id, and the other catalogs
work the same way. The content-id pages on the documentation site list the same ids:

- [Class ids](https://mmacy.github.io/osrlib-python/reference/content-ids/classes/)
- [Spell ids](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/)
- [Monster ids](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/)
- [Equipment ids](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/)
- [Magic item ids](https://mmacy.github.io/osrlib-python/reference/content-ids/magic-items/)
- [Language ids](https://mmacy.github.io/osrlib-python/reference/content-ids/languages/)
- [Treasure types](https://mmacy.github.io/osrlib-python/reference/content-ids/treasure-types/)

The loaders, one per catalog:

- [`load_classes`][osrlib.data.load_classes]: the character classes, by class id.
- [`load_ability_tables`][osrlib.data.load_ability_tables]: what each ability score from 3 to 18 is worth.
- [`load_languages`][osrlib.data.load_languages]: the spoken languages, by language id.
- [`load_equipment`][osrlib.data.load_equipment]: weapons, armour, gear, and ammunition, by item id.
- [`load_magic_items`][osrlib.data.load_magic_items]: magic items, by item id.
- [`load_spells`][osrlib.data.load_spells]: spells, by spell id.
- [`load_monsters`][osrlib.data.load_monsters]: monster templates, by monster id.
- [`load_combat_tables`][osrlib.data.load_combat_tables]: the attack matrix, monster saves, XP awards,
  turning, and reactions.
- [`load_encounter_tables`][osrlib.data.load_encounter_tables]: wandering monsters by dungeon level.
- [`load_treasure_tables`][osrlib.data.load_treasure_tables]: the treasure types, by letter.

To use a catalog with ids, call the loader, look one entry up by id, then pass that entry to
the function that takes it. `load_classes().get("fighter")` gives you the
[`ClassDefinition`][osrlib.core.classes.ClassDefinition] that
[`level_up`][osrlib.core.classes.level_up] takes, and `load_monsters().get("goblin")` gives you
the [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] that
[`spawn_monster`][osrlib.core.monsters.spawn_monster] takes. A
[`GameSession`][osrlib.crawl.session.GameSession] loads what it needs itself. Call these
loaders when you run the rules without a session, or to show a player what content exists
before play starts.

Each loader caches its result. The first call reads the data and validates it, and every
later call in the process returns the same catalog object. That object is a frozen model,
shared with every other caller. During play, osrlib creates mutable instances from these templates, the way
[`spawn_monster`][osrlib.core.monsters.spawn_monster] creates a
[`MonsterInstance`][osrlib.core.monsters.MonsterInstance] from a template.

The data files are part of this package, and a loader reads only what the installed package
includes. The project generates them from the Old-School Essentials SRD, so a change you make
to a JSON file in an installed copy is lost at the next upgrade. To add content of your own,
construct the model yourself, like a [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate],
and pass it to the same functions you pass the included entries to. To use your own monster or
item template in a session, bundle it with the [`Adventure`][osrlib.crawl.adventure.Adventure]
that uses it. If a data file is missing, has no JSON object at its top level, or fails validation, the loader
raises [`ContentValidationError`][osrlib.errors.ContentValidationError] instead of returning a
partial catalog.

The compiled data is Open Game Content under the Open Game License 1.0a. The license text
and Section 15 notice are in this package as `osrlib/data/LICENSE-OGL.md`.

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
    """One spoken language from the language catalog.

    You get one from [`LanguageCatalog.get`][osrlib.data.LanguageCatalog.get], or from
    [`LanguageCatalog.languages`][osrlib.data.LanguageCatalog.languages] when you want to
    offer a player the whole list. Pass its `id` in the `extra_languages` argument of
    [`create_character`][osrlib.core.character.create_character], or in the `choices` argument
    of [`validate_extra_languages`][osrlib.core.character.validate_extra_languages].

    The alignment languages aren't in the catalog. A character speaks the language of its
    [`Alignment`][osrlib.core.alignment.Alignment] without choosing it.

    The model is frozen: assigning to a field raises pydantic's `ValidationError`.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    """The id to pass wherever a function takes a language id, like `"gnoll"` or `"common"`."""

    name: str
    """The display name to show a player, like `"Gnoll"`."""

    choosable: bool
    """Whether a character with a high enough INT may take this language as an extra.

    True for the languages the SRD lists under Other Languages, which a high-INT character
    chooses from. False for Common, which every character already speaks.
    """


class LanguageCatalog(BaseModel):
    """The whole language list, with lookup by id.

    [`load_languages`][osrlib.data.load_languages] returns this catalog, and
    [`get`][osrlib.data.LanguageCatalog.get] returns one
    [`Language`][osrlib.data.Language] from it by id. Each id appears once, and a catalog with a
    repeated id fails validation.

    The model is frozen, and the loader returns the same instance on every call.
    """

    model_config = ConfigDict(frozen=True)

    languages: tuple[Language, ...]
    """Every language in the catalog, in the order the data file lists them.

    The data file in this package lists them in alphabetical order by id, with Common among
    the rest. Keep the ones whose [`Language.choosable`][osrlib.data.Language.choosable] is True
    for the languages a high-INT character may take.
    """

    @model_validator(mode="after")
    def _ids_must_be_unique(self) -> LanguageCatalog:
        ids = [language.id for language in self.languages]
        if len(set(ids)) != len(ids):
            raise ValueError("language ids must be unique")
        return self

    def get(self, language_id: str) -> Language:
        """Return the language with `language_id`.

        Use this to turn a stored id back into a display name. To check a player's picks
        before you pass them to [`create_character`][osrlib.core.character.create_character],
        call [`validate_extra_languages`][osrlib.core.character.validate_extra_languages]
        instead. It checks the whole set against a class and an INT score, and returns a
        rejection for each problem instead of raising on the first unknown id.

        Args:
            language_id: A language id, like `"gnoll"`. Any `id` in
                [`languages`][osrlib.data.LanguageCatalog.languages] works, and the
                [language id index](https://mmacy.github.io/osrlib-python/reference/content-ids/languages/)
                lists them all.

        Returns:
            The language.

        Raises:
            ValueError: If no language has that id. Pass an id from the catalog.

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
    """Load the ability score modifier tables and the prime requisite XP table.

    The returned [`AbilityTables`][osrlib.core.abilities.AbilityTables] gives what a score is
    worth. It has a method for each modifier: the STR melee modifier and open-doors chance, the
    INT extra languages and literacy, the WIS modifier to saves against magic, the DEX armour
    class, missile, and initiative modifiers, the CON hit point modifier, the CHA reaction
    modifier, retainer limit, and retainer loyalty, and the prime requisite XP percentage.
    Call it to show these values on a character sheet.
    [`create_character`][osrlib.core.character.create_character] and
    [`level_up`][osrlib.core.classes.level_up] read the tables themselves.

    Every method takes a score from 3 to 18 and raises `ValueError` for any other score.
    No content-id page covers this catalog, because a score is its only key.

    Returns:
        The frozen tables. The first call validates the data, and every later call returns
            the same object.

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

    Call [`ClassCatalog.get`][osrlib.core.classes.ClassCatalog.get] on the result for a
    [`ClassDefinition`][osrlib.core.classes.ClassDefinition].
    [`level_up`][osrlib.core.classes.level_up],
    [`xp_modifier_pct`][osrlib.core.classes.xp_modifier_pct],
    [`level_title`][osrlib.core.classes.level_title],
    [`thief_skill_check`][osrlib.core.classes.thief_skill_check], and
    [`caster_profile`][osrlib.core.spells.caster_profile] take the definition, not the id.
    [`create_character`][osrlib.core.character.create_character] takes `class_id` and looks the
    definition up itself, so to create a character you need this loader only to show a player
    the classes they can choose.

    The catalog contains only the classes in this package. To use a class of your own, build a
    `ClassDefinition` and pass it to the same functions.

    Returns:
        The frozen class catalog. The first call validates the data, and every later call
            returns the same object. Its `classes` tuple contains every class.
            [The class id index](https://mmacy.github.io/osrlib-python/reference/content-ids/classes/)
            lists the same ids.

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

    [`EquipmentCatalog.get`][osrlib.core.items.EquipmentCatalog.get] looks an id up across the
    `weapons`, `armour`, `gear`, and `ammunition` lists and returns its template. Pass the
    template to [`validate_purchase`][osrlib.core.items.validate_purchase] and
    [`purchase`][osrlib.core.items.purchase]. `purchase` returns an
    [`ItemInstance`][osrlib.core.items.ItemInstance], which is what
    [`validate_equip`][osrlib.core.items.validate_equip] and
    [`equip`][osrlib.core.items.equip] take. `treasure_weights` is a separate list, which
    [`treasure_weight_coins`][osrlib.core.items.treasure_weight_coins] reads to weigh the coins
    and gems a character finds instead of buying.

    Magic items are in a separate catalog, from [`load_magic_items`][osrlib.data.load_magic_items].
    This catalog contains only the items in this package. A session also looks up the item
    templates an adventure bundles.

    Returns:
        The frozen equipment catalog. The first call validates the data, and every later
            call returns the same object. Its `weapons`, `armour`, `gear`, and `ammunition`
            tuples contain every item.
            [The equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/)
            lists the same ids.

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

    [`MonsterCatalog.get`][osrlib.core.monsters.MonsterCatalog.get] returns the frozen
    [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] that
    [`spawn_monster`][osrlib.core.monsters.spawn_monster] turns into a mutable
    [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] with rolled hit points and a
    session-unique id. The instance is the creature that fights. The template stays shared and
    unchanged however many instances you spawn from it.

    A session looks up a monster id from a dungeon or an encounter table in this catalog and
    in the templates the adventure bundles. Read a template directly to show a monster's
    statistics, or to plan an encounter before any monster exists.

    Returns:
        The frozen monster catalog. The first call validates the data, and every later call
            returns the same object. Its `monsters` tuple contains every monster.
            [The monster id index](https://mmacy.github.io/osrlib-python/reference/content-ids/monsters/)
            lists the same ids.

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
    dice and returns its XP award. To read a row yourself, get its label from the monster's
    hit dice first. Pass the label from
    [`monster_save_band_label`][osrlib.core.tables.monster_save_band_label] to
    [`CombatTables.save_band`][osrlib.core.tables.CombatTables.save_band], and the label from
    [`xp_band_label`][osrlib.core.tables.xp_band_label] to
    [`CombatTables.xp_row`][osrlib.core.tables.CombatTables.xp_row].

    You don't pass tables for most of combat: [`attack_roll`][osrlib.core.combat.attack_roll]
    and [`saving_throw`][osrlib.core.combat.saving_throw] read the tables they need themselves.
    Load the tables to show the numbers, or to award XP without a session. No content-id page
    covers this catalog, because hit dice and armour class are its keys, not ids.

    Returns:
        The frozen combat tables. The first call validates the data, and every later call
            returns the same object.

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

    [`EncounterTables.for_level`][osrlib.core.tables.EncounterTables.for_level] takes a dungeon
    level number from 1 up and returns the table the SRD prints for it. Level 8 and every deeper
    level share the last table. Roll on the table for an entry, then call
    [`select_encounter_individuals`][osrlib.core.tables.select_encounter_individuals] with a
    monster entry and a count to get the template ids that appear.
    [`generate_npc_party`][osrlib.core.npc.generate_npc_party] rolls on the NPC party tables.

    A session rolls wandering monsters itself, through
    [`wandering_check`][osrlib.crawl.exploration.wandering_check]. Load these tables when you
    stock a dungeon yourself, or to show which monsters a level can have. No content-id page
    covers this catalog, because dungeon level and die roll are its keys.

    Returns:
        The frozen encounter tables. The first call validates the data, and every later call
            returns the same object.

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
    they can check a chosen id against the caster's spell list and level.
    [`SpellCatalog.get`][osrlib.core.spells.SpellCatalog.get] returns one
    [`SpellTemplate`][osrlib.core.spells.SpellTemplate] by id.
    [`SpellCatalog.by_list`][osrlib.core.spells.SpellCatalog.by_list] returns every spell on one
    spell list, like `"cleric"`, and takes an optional spell level. Show its result to a player
    who is choosing spells to memorize.

    A reversible spell has one id. Its reversed form, like *blight* for *bless*, is the
    template's `reversed_form`, and you cast it by passing `reversed=True` to
    [`cast_spell`][osrlib.core.spells.cast_spell]. To use a spell of your own, build a
    `SpellTemplate` and pass it to the same functions.

    Returns:
        The frozen spell catalog. The first call validates the data, and every later call
            returns the same object. Its `spells` tuple contains every spell.
            [The spell id index](https://mmacy.github.io/osrlib-python/reference/content-ids/spells/)
            lists the same ids.

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

    [`MagicItemCatalog.get`][osrlib.core.items.MagicItemCatalog.get] returns the frozen
    [`MagicItemTemplate`][osrlib.core.items.MagicItemTemplate] for an id.
    [`magic_item_template`][osrlib.core.items.magic_item_template] does the same lookup for a
    [`MagicItemInstance`][osrlib.core.items.MagicItemInstance] you already have.
    [`generate_magic_item`][osrlib.core.treasure.generate_magic_item] rolls on the catalog's
    other tables, like the sub-tables, the armour-type table, the scroll spell-level table, and
    the sentient sword tables, so you rarely read them yourself.

    An id identifies a kind of item, not one particular item. Two potions of healing share a template.
    As instances, each has a separate instance id, charges, and identification state. Mundane gear
    is in [`load_equipment`][osrlib.data.load_equipment].

    Returns:
        The frozen magic item catalog. The first call validates the data, and every later
            call returns the same object. Its `items` tuple contains every magic item.
            [The magic item id index](https://mmacy.github.io/osrlib-python/reference/content-ids/magic-items/)
            lists the same ids.

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

    [`TreasureTables.treasure_type`][osrlib.core.treasure.TreasureTables.treasure_type] returns
    the table for a letter. A monster's letters are in the `letters` of its
    [`MonsterTemplate.treasure`][osrlib.core.monsters.MonsterTemplate.treasure]. Read the table
    to show what a hoard can contain before anything is rolled.

    To roll a hoard, call [`generate_treasure`][osrlib.core.treasure.generate_treasure] with the
    letter instead. It loads these tables itself and returns a
    [`GeneratedTreasure`][osrlib.core.items.GeneratedTreasure] with its coins, valuables, and
    magic items already rolled from the stream you pass.
    [`roll_room_contents`][osrlib.core.treasure.roll_room_contents] rolls on the room stocking
    table, and [`generate_unguarded_treasure`][osrlib.core.treasure.generate_unguarded_treasure]
    on the unguarded treasure table.

    Returns:
        The frozen treasure tables. The first call validates the data, and every later call
            returns the same object. Its `treasure_types` tuple contains every type, each with
            its `letter`.
            [The treasure type index](https://mmacy.github.io/osrlib-python/reference/content-ids/treasure-types/)
            lists the same letters.

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

    To offer a player the languages a high-INT character may add, read
    [`LanguageCatalog.languages`][osrlib.data.LanguageCatalog.languages] and keep the entries
    whose [`choosable`][osrlib.data.Language.choosable] is True. Call
    [`LanguageCatalog.get`][osrlib.data.LanguageCatalog.get] to turn an id back into a display
    name. Check the chosen ids with
    [`validate_extra_languages`][osrlib.core.character.validate_extra_languages], which counts
    them against what the character's INT allows, then pass them to
    [`create_character`][osrlib.core.character.create_character] as `extra_languages`.

    Returns:
        The frozen language catalog. The first call validates the data, and every later call
            returns the same object. Its `languages` tuple contains every language.
            [The language id index](https://mmacy.github.io/osrlib-python/reference/content-ids/languages/)
            lists the same ids.

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
