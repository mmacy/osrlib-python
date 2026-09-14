"""The crawl party: marching order, group movement, and combat ranks.

You build a [`Party`][osrlib.crawl.party.Party] out of the
[`Character`][osrlib.core.character.Character]s your game rolled up, and you hand it to
[`GameSession.new`][osrlib.crawl.session.GameSession.new] beside the adventure. From then on the
session owns it: the party moves as one body, and `session.party` is where you read it back.

The member list order *is* marching order. There is no second field to keep in step with it, so the
first member is the one in front. [`ReorderParty`][osrlib.crawl.commands.ReorderParty] is the only
command that rewrites that order, and [`reorder`][osrlib.crawl.party.Party.reorder] is what it calls.

Dead members stay in the list. Their gear is still on them, so removing them would lose it, and
deciding a corpse is left behind is a game's call to make with the referee commands rather than
something the rules do for you. Nothing that counts bodies counts a dead one: movement rate, combat
ranks, ability checks, and provisions all read
[`living_members`][osrlib.crawl.party.Party.living_members].
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
    """The adventuring party, in marching order.

    Construct one with at least one member and pass it to
    [`GameSession.new`][osrlib.crawl.session.GameSession.new], which assigns each member an entity id
    and keeps the party for the life of the session. The methods here answer what the crawl procedures
    need to know about the group as a whole: who is still standing, how fast the group walks, and who
    stands where in a fight.

    A party is mutable and validates on assignment, so the session can heal, wound, and re-equip its
    members in place. The list is never empty: a party whose last member dies ends the session in
    `game_over` rather than emptying out.

    Attributes:
        members: The characters, front of the line first.

    Examples:
        ```python
        from osrlib.core.abilities import AbilityScore
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import Character
        from osrlib.crawl.party import Party

        rolled = {
            "name": "Hild",
            "class_id": "fighter",
            "race": "human",
            "level": 1,
            "xp": 0,
            "scores": {ability: 12 for ability in AbilityScore},
            "alignment": Alignment.LAWFUL,
            "max_hp": 8,
            "current_hp": 8,
        }
        party = Party(members=[Character(**rolled), Character(**{**rolled, "name": "Osric"})])
        print([member.name for member in party.living_members()])
        # ['Hild', 'Osric']
        ```
    """

    model_config = ConfigDict(validate_assignment=True)

    members: list[Character] = Field(min_length=1)
    """The party's characters, in marching order: index 0 walks in front and meets what the party
    walks into first. At least one member is required. Ids are assigned by
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] when the party joins a session, so a
    party you just built has `None` in every `Character.id` until then. Change the order with the
    [`ReorderParty`][osrlib.crawl.commands.ReorderParty] command rather than by assigning here, so
    the change is logged and replays."""

    def living_members(self) -> list[Character]:
        """Return the living members, in marching order.

        A member is living until something gives them the `dead` condition. This is the list every
        group rule works from, so a fallen member stops counting toward movement, ranks, and checks
        the moment they drop, without leaving the party.

        Returns:
            The members without the `dead` condition, in marching order. Empty when the whole party
                has fallen, which is the session's `game_over` condition.
        """
        return [member for member in self.members if not has_condition(member, Condition.DEAD)]

    def member(self, character_id: str) -> Character:
        """Return the member with `character_id`.

        Use this to turn an id out of a command or an event back into the character it names. Ids
        come from [`GameSession.new`][osrlib.crawl.session.GameSession.new], which stamps each member
        as `character-NNNN` in party order, and events carry them rather than names.

        Args:
            character_id: The member's entity id.

        Returns:
            The character. Dead members answer here too, because their gear and their record are
                still the party's.

        Raises:
            ValueError: If no member has that id. The message names the id.
        """
        for member in self.members:
            if member.id == character_id:
                return member
        raise ValueError(f"no party member with id {character_id!r}")

    def movement_rate(self, ruleset: Ruleset) -> int:
        """Return the party's exploration rate: the slowest living member's.

        B/X moves a group at the pace of its slowest member, so one overloaded character slows
        everybody. Call [`Character.movement_rate`][osrlib.core.character.Character.movement_rate]
        for one character's own allowance, and
        [`exploration_rate`][osrlib.crawl.exploration.exploration_rate] for the rate the running
        session charges the party, which computes the same minimum and halves a member's rate first
        when hunger or thirst has caught up with them under the `deprivation_penalties` ruleset flag.

        Args:
            ruleset: The ruleset whose encumbrance mode governs. Encumbrance is what turns carried
                weight into a rate, and the modes differ in what they weigh.

        Returns:
            The rate in feet per exploration turn: 120, 90, 60, 30, or 0. A rate of 0 means the party
                cannot move at all, either because its slowest living member is overloaded or because
                nobody is alive to walk.
        """
        rates = [member.movement_rate(ruleset) for member in self.living_members()]
        return min(rates, default=0)

    def ranks(self, width: int) -> list[list[Character]]:
        """Chunk the living members into combat ranks of `width`, in marching order.

        A rank is one row of the formation: the first `width` living members stand in front and take
        the melee, the rest queue behind them. Battle calls this for you with the width it measured
        from the space the party is standing in, so you rarely pass your own. Call it yourself to
        draw the formation, or to answer "who is in front" outside a fight.

        Ranks are derived on every read rather than stored, so the fallen collapse forward on their
        own: when a front-rank member dies, the next living member is in front from the next read on.

        Args:
            width: How many characters stand abreast. Battle derives this from the party's frontage
                (see [`FIGHTER_FRONTAGE_FEET`][osrlib.crawl.battle.FIGHTER_FRONTAGE_FEET]) while the
                `formation_width_limit` ruleset flag is on, and puts the whole party in one rank when
                it is off.

        Returns:
            The ranks, front first. The last rank holds the remainder and can be shorter than
                `width`. Empty when nobody is alive.

        Raises:
            ValueError: If `width` is not positive.
        """
        if width < 1:
            raise ValueError(f"rank width must be positive, got {width}")
        living = self.living_members()
        return [living[index : index + width] for index in range(0, len(living), width)]

    def reorder(self, character_ids: Sequence[str]) -> None:
        """Rewrite the marching order in place.

        This is what [`ReorderParty`][osrlib.crawl.commands.ReorderParty] calls once it has accepted
        the command. Issue that command through
        [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] rather than calling this
        yourself, so the change is logged and a replay reproduces it.

        Args:
            character_ids: Every member's id, in the new order. It has to be a permutation of the
                current membership: no id may be added, dropped, or repeated. Dead members are named
                here like anyone else, since they are still in the party.

        Raises:
            ValueError: If the ids are not exactly the current membership, or if any member has no id
                yet (a party that has not joined a session).
        """
        by_id = {member.id: member for member in self.members if member.id is not None}
        if len(by_id) != len(self.members) or sorted(character_ids) != sorted(by_id):
            raise ValueError("reorder must name every current member exactly once")
        self.members = [by_id[character_id] for character_id in character_ids]
