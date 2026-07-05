"""The crawl party: marching order, group movement, and combat ranks.

The member list order **is** marching order — there is no separate order field to
desync; `reorder` swaps in place, and `ReorderParty` is the only command that
mutates it. Dead members stay in the party (their gear is carried state; excluding
them is a game decision via referee commands) but never count toward movement rate,
ranks, checks, or provisions.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.character import Character
from osrlib.core.effects import Condition, has_condition
from osrlib.core.ruleset import Ruleset

__all__ = [
    "Party",
]


class Party(BaseModel):
    """The adventuring party, in marching order."""

    model_config = ConfigDict(validate_assignment=True)

    members: list[Character] = Field(min_length=1)

    def living_members(self) -> list[Character]:
        """Return the living members, in marching order."""
        return [member for member in self.members if not has_condition(member, Condition.DEAD)]

    def member(self, character_id: str) -> Character:
        """Return the member with `character_id`.

        Args:
            character_id: The member's entity id.

        Returns:
            The character.

        Raises:
            ValueError: If no member has that id.
        """
        for member in self.members:
            if member.id == character_id:
                return member
        raise ValueError(f"no party member with id {character_id!r}")

    def movement_rate(self, ruleset: Ruleset) -> int:
        """Return the party's exploration rate: the slowest living member's (SRD group rule).

        Args:
            ruleset: The ruleset whose encumbrance mode governs.

        Returns:
            The rate in feet per turn; 0 when nobody is alive.
        """
        rates = [member.movement_rate(ruleset) for member in self.living_members()]
        return min(rates, default=0)

    def ranks(self, width: int) -> list[list[Character]]:
        """Chunk the living members into combat ranks of `width`, in marching order.

        The fallen collapse forward by construction: ranks derive from the living
        marching order each time they're read.

        Args:
            width: The formation width (3 in a keyed area, 2 in corridor under the
                `formation_width_limit` flag).

        Returns:
            The ranks, front first.

        Raises:
            ValueError: If `width` is not positive.
        """
        if width < 1:
            raise ValueError(f"rank width must be positive, got {width}")
        living = self.living_members()
        return [living[index : index + width] for index in range(0, len(living), width)]

    def reorder(self, character_ids: Sequence[str]) -> None:
        """Rewrite the marching order — `ReorderParty`'s apply step.

        Args:
            character_ids: Every member's id, in the new order (a permutation).

        Raises:
            ValueError: If the ids are not exactly the current membership.
        """
        by_id = {member.id: member for member in self.members if member.id is not None}
        if len(by_id) != len(self.members) or sorted(character_ids) != sorted(by_id):
            raise ValueError("reorder must name every current member exactly once")
        self.members = [by_id[character_id] for character_id in character_ids]
