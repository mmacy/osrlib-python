"""The three alignments a creature in the game can have.

The module has one member,
[`Alignment`][osrlib.core.alignment.Alignment]. You pass one to
[`create_character`][osrlib.core.character.create_character] when you roll a player
character. A monster template has an
[`AlignmentSpec`][osrlib.core.monsters.AlignmentSpec] that lists the alignments its kind
can have. [`spawn_monster`][osrlib.core.monsters.spawn_monster] gives the new monster the
alignment you pass it, or else the template's usual one, and leaves it `None` when the
template allows several and has no usual one.

osrlib uses alignment in two places. A character's alignment tongue, the secret
language that creatures of one alignment share, comes from it through
[`Character.alignment_tongue`][osrlib.core.character.Character.alignment_tongue]. And
the rules for some spells and magic items compare alignments to tell whose side a
creature is on.

Alignment is in a separate module so that the character models and the monster models
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

    osrlib applies no mechanical penalty for acting against alignment. The SRD leaves
    that to the referee. What osrlib does use is the alignment tongue that comes from the
    value, and the alignment comparisons in the rules for some spells and magic items.

    Characters and saved games contain the lowercase values, and they stay fixed within a
    `schema_version`, the version stamp that identifies a serialized model's format.

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

    Neutral creatures cooperate with others as long as it costs them nothing, and rely on
    their talents instead of on anyone else.
    """

    CHAOTIC = "chaotic"
    """Personal desires above all, in a universe the creature believes is random.

    Chaotic creatures lie and use others to get what they want, break laws, and follow
    their whims.
    """
