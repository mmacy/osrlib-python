"""The attribute surface a character or a monster instance offers the rules, as protocols.

Every rules function in [`osrlib.core.combat`][osrlib.core.combat], [`osrlib.core.spells`][osrlib.core.spells],
and [`osrlib.core.effects`][osrlib.core.effects] takes the creature it acts on as one of the three protocols
here. A [`Character`][osrlib.core.character.Character] and a [`MonsterInstance`][osrlib.core.monsters.MonsterInstance]
satisfy them structurally, so you pass either without a cast, and pyright checks that whatever else you pass
has the attributes the function reads. Nothing here is instantiated. Read a protocol to learn what a function
needs from its argument, and annotate your own code with it when you write a function that takes either kind
of creature.

[`Creature`][osrlib.core.creature.Creature] is the base: an id, a name, hit points, conditions, and stat
modifiers. [`Combatant`][osrlib.core.creature.Combatant] adds the combat numbers an attack or a saving throw reads.
[`Caster`][osrlib.core.creature.Caster] adds what memorizing and casting read. A function that needs an attribute
only one concrete type has, such as a monster's template or a character's inventory, either takes that
concrete type or reads the attribute with `getattr` and a default, and its docstring says which.

Typical usage:

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.creature import Combatant, Creature
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_monsters

streams = RngStreams(master_seed=7)
hero = create_character(
    name="Hild",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=Ruleset(),
    stream=streams.get(CHARACTER_CREATION_STREAM),
).character
goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=streams.get(MONSTER_SPAWN_STREAM))

def hurt(creature: Creature) -> bool:
    return creature.current_hp < creature.max_hp

def can_be_hit(combatant: Combatant) -> bool:
    return combatant.armour_class is not None

print(hurt(hero), hurt(goblin), can_be_hit(hero), can_be_hit(goblin))
# False False True True
```
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from osrlib.core.alignment import Alignment

if TYPE_CHECKING:
    from osrlib.core.classes import SavingThrows
    from osrlib.core.effects import ActiveCondition, ActiveModifier
    from osrlib.core.spells import MemorizedSpell

__all__ = [
    "Caster",
    "Combatant",
    "Creature",
]


@runtime_checkable
class Creature(Protocol):
    """What every rules function needs from the creature it acts on.

    A [`Character`][osrlib.core.character.Character] and a [`MonsterInstance`][osrlib.core.monsters.MonsterInstance]
    both satisfy this, and so does anything of your own with these attributes. Functions that read only this
    surface, such as [`has_condition`][osrlib.core.effects.has_condition],
    [`apply_healing`][osrlib.core.combat.apply_healing], and [`incapacitated`][osrlib.core.combat.incapacitated],
    take it. A function that also reads combat numbers takes [`Combatant`][osrlib.core.creature.Combatant].

    The hit points, conditions, and stat modifiers are writable, because the rules change them: damage lowers
    `current_hp`, a spell appends to `conditions`, an effect attaches a modifier. The id, name, and alignment are
    read-only here, because a monster's name comes from its template and a character's id is `None` until a
    session assigns one.

    Examples:
        ```python
        from osrlib.core.creature import Creature
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=stream)
        print(isinstance(goblin, Creature), goblin.name, goblin.current_hp == goblin.max_hp)
        # True Goblin True
        ```
    """

    @property
    def id(self) -> str | None:
        """The entity id a session assigned, or `None` for a character no session holds yet.

        Events name creatures by it.
        """
        ...

    @property
    def name(self) -> str:
        """The display name: the one a character was created with, or the one a monster's template gives."""
        ...

    @property
    def alignment(self) -> Alignment | None:
        """The creature's [`Alignment`][osrlib.core.alignment.Alignment].

        `None` for a monster whose template lists none.
        """
        ...

    current_hp: int
    """Hit points now. The rules lower and raise it in place."""
    max_hp: int
    """The hit-point ceiling healing cannot pass."""
    conditions: tuple[ActiveCondition, ...]
    """The active [`ActiveCondition`][osrlib.core.effects.ActiveCondition] entries, in the order they were granted.
    Read them through [`has_condition`][osrlib.core.effects.has_condition]."""
    stat_modifiers: tuple[ActiveModifier, ...]
    """The active [`ActiveModifier`][osrlib.core.effects.ActiveModifier] entries. Read them through
    [`modifier_total`][osrlib.core.effects.modifier_total] and its siblings."""


@runtime_checkable
class Combatant(Creature, Protocol):
    """A creature with the numbers an attack, an initiative roll, or a saving throw reads.

    [`attack_roll`][osrlib.core.combat.attack_roll], [`resolve_attack`][osrlib.core.combat.resolve_attack],
    [`saving_throw`][osrlib.core.combat.saving_throw], and
    [`participant_modifier`][osrlib.core.combat.participant_modifier] take this. Both concrete creature types
    satisfy it. Every number is read-only, because each one is derived: a character's from class, level, and
    gear, a monster's from its template and its drained hit dice.

    Examples:
        ```python
        from osrlib.core.creature import Combatant
        from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
        from osrlib.core.rng import RngStreams
        from osrlib.data import load_monsters

        stream = RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM)
        goblin = spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=stream)
        print(isinstance(goblin, Combatant), goblin.thac0, goblin.armour_class)
        # True 19 6
        ```
    """

    @property
    def thac0(self) -> int:
        """The descending-AC to-hit number the attack matrix is entered with."""
        ...

    @property
    def attack_bonus(self) -> int:
        """The ascending-AC equivalent of `thac0`."""
        ...

    @property
    def armour_class(self) -> int | None:
        """Descending armour class, or `None` for a creature an attack roll cannot hit."""
        ...

    @property
    def armour_class_ascending(self) -> int | None:
        """The ascending form of `armour_class`, or `None` on the same terms."""
        ...

    @property
    def saves(self) -> SavingThrows:
        """The five saving-throw targets as [`SavingThrows`][osrlib.core.classes.SavingThrows]."""
        ...

    @property
    def melee_modifier(self) -> int:
        """The bonus a melee attack and its damage take, from strength or from an effect."""
        ...

    @property
    def missile_modifier(self) -> int:
        """The bonus a missile attack takes, from dexterity. A monster's is 0."""
        ...

    @property
    def initiative_modifier(self) -> int:
        """The bonus an individual initiative roll takes. A monster's is 0."""
        ...


@runtime_checkable
class Caster(Creature, Protocol):
    """A creature that memorizes and casts spells.

    [`memorize_spells`][osrlib.core.spells.memorize_spells], [`validate_cast`][osrlib.core.spells.validate_cast],
    [`cast_spell`][osrlib.core.spells.cast_spell], and [`disrupt_casting`][osrlib.core.spells.disrupt_casting]
    take this. A [`Character`][osrlib.core.character.Character] satisfies it and a
    [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] does not, because monsters in the compiled data never
    cast. The class that decides which list and how many slots is not part of the surface: those functions take
    the [`ClassDefinition`][osrlib.core.classes.ClassDefinition] or the
    [`CasterProfile`][osrlib.core.spells.CasterProfile] as a separate argument.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
        from osrlib.core.creature import Caster
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset

        stream = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        zelia = create_character(
            name="Zelia",
            class_id="magic_user",
            alignment=Alignment.NEUTRAL,
            ruleset=Ruleset(),
            stream=stream,
            starting_spell_ids=["sleep"],
        ).character
        print(isinstance(zelia, Caster), zelia.level, zelia.spell_book, zelia.memorized_spells)
        # True 1 ('sleep',) ()
        ```
    """

    level: int
    """The caster's level, which sets slots, range, and effect size. A scroll is read at the scroll's own level
    rather than the reader's."""
    spell_book: tuple[str, ...]
    """The spell ids an arcane caster owns copies of. Empty for a divine caster."""
    memorized_spells: tuple[MemorizedSpell, ...]
    """The prepared copies as [`MemorizedSpell`][osrlib.core.spells.MemorizedSpell] entries, spent as they are cast."""
