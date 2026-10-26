"""The three alignments every creature in the game declares.

The module has one member,
[`Alignment`][osrlib.core.alignment.Alignment]. You pass one to
[`create_character`][osrlib.core.character.create_character] when you roll a player
character. A monster template has an
[`AlignmentSpec`][osrlib.core.monsters.AlignmentSpec] that lists the alignments its kind
can have. [`spawn_monster`][osrlib.core.monsters.spawn_monster] gives the new monster the
alignment you pass it, or else the template's usual one, and leaves it `None` when the
template allows several and has no usual one.

The rules read alignment in two places. A character's alignment tongue, the secret
language that creatures of one alignment share, comes from it through
[`Character.alignment_tongue`][osrlib.core.character.Character.alignment_tongue]. And
some spells and magic items compare alignments to tell whose side a creature is on.

Alignment has a module of its own so that the character models and the monster models
can both import it without importing each other.
"""

from enum import StrEnum

__all__ = [
    "Alignment",
]


class Alignment(StrEnum):
    """The cosmic principle a creature follows: law, neutrality, or chaos.

    Use a member by name (`Alignment.LAWFUL`) when you write the value yourself, and call
    the enum on a string (`Alignment("lawful")`) when the value comes from saved data or
    from a player's input. A string that isn't one of the three values raises
    `ValueError`. Each member compares equal to its lowercase string, so
    `Alignment.LAWFUL == "lawful"` is true.

    The rules attach no mechanical penalty to acting against alignment. The SRD leaves
    that to the referee. What the rules do read is the alignment tongue that comes from
    the value, and the alignment comparisons some spells and magic items make.

    The lowercase values are what characters and saved games store, and they stay fixed
    within a `schema_version`, the version stamp that marks a serialized model's format.

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
