"""The three alignments every creature in the game declares.

The module has one member,
[`Alignment`][osrlib.core.alignment.Alignment]. You pass one to
[`create_character`][osrlib.core.character.create_character] when you roll a player
character, and you read one off a monster: a template has an
[`AlignmentSpec`][osrlib.core.monsters.AlignmentSpec] of the alignments its kind can
have, and [`spawn_monster`][osrlib.core.monsters.spawn_monster] settles on one for the
individual. The rules read alignment in two places: a character's alignment tongue, the
secret language its adherents share, is derived from it by
[`Character.alignment_tongue`][osrlib.core.character.Character.alignment_tongue], and
spells and magic items that care whose side a creature is on compare alignments.

Alignment sits in its own module so that both the character models and the monster
models can import it without importing each other.
"""

from enum import StrEnum

__all__ = [
    "Alignment",
]


class Alignment(StrEnum):
    """The cosmic principle a creature follows: law, neutrality, or chaos.

    Reference a member by name (`Alignment.LAWFUL`) when you write the value yourself,
    and call the enum on a string (`Alignment("lawful")`) when the value arrives from
    saved data or from a player's input. The member compares equal to its own lowercase
    string, so `character.alignment == "lawful"` is true as well.

    The rules attach no mechanical penalty to acting against alignment. The SRD leaves
    that to the referee. What the rules do read is the alignment tongue derived from the
    value, and the alignment comparisons a handful of spells and magic items make.

    The lowercase values serialize into characters and saved games. Changing one is a
    `schema_version` bump, the version stamp that marks a serialized model's shape.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment

        assert Alignment("lawful") is Alignment.LAWFUL
        assert Alignment.CHAOTIC == "chaotic"
        ```
    """

    LAWFUL = "lawful"
    """Goodness, order, truth, and justice as the natural order of the universe.

    Lawful creatures are trustworthy, protect others, and act for the good of the group.
    """

    NEUTRAL = "neutral"
    """A balance between law and chaos, with neither side dominant.

    Neutral creatures cooperate with others as long as it costs them nothing, and live by
    their own talents rather than relying on anyone else.
    """

    CHAOTIC = "chaotic"
    """The individual's own desires above all, in a universe the creature believes is random.

    Chaotic creatures lie and use others to their own ends, break laws, and follow their
    whims.
    """
