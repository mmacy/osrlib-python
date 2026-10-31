"""The adventuring party: marching order, group movement, and combat ranks.

You build a [`Party`][osrlib.crawl.party.Party] from
[`Character`][osrlib.core.character.Character] objects, usually ones
[`create_character`][osrlib.core.character.create_character] made, and pass it to
[`GameSession.new`][osrlib.crawl.session.GameSession.new] with the adventure. From then on the
session moves the party as one group. `session.party` is the same object you passed.

The order of the member list is the marching order, and it isn't recorded anywhere else, so the
first member is the one in front. In a fight, the front rank is the living members at the front of
the marching order, as many as fit side by side, and only they can attack in melee. With the
`formation_width_limit` ruleset flag off, every living member is in the front rank.
[`ranks`][osrlib.crawl.party.Party.ranks] splits the party into ranks. Outside a fight,
[`ReorderParty`][osrlib.crawl.commands.ReorderParty] sets the order, and the session calls
[`reorder`][osrlib.crawl.party.Party.reorder] when it accepts one. In a battle, a member who makes a
fighting withdrawal or a retreat alone trades places with the member named in
[`BattleDeclaration.replacement_id`][osrlib.crawl.commands.BattleDeclaration.replacement_id]. The
new order lasts after the battle. [`AddMember`][osrlib.crawl.commands.AddMember] adds a new member
at the back. It's a referee command, one your game logic issues rather than a player.

Dead members stay in the list, with their gear still on them. No command removes a member. The
rules that count party members skip the dead: movement rate, combat ranks, traps, and food and
water all use [`living_members`][osrlib.crawl.party.Party.living_members].
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
    [`GameSession.new`][osrlib.crawl.session.GameSession.new], which assigns each member an id and
    keeps the party for the life of the session. To add a member during play, execute
    [`AddMember`][osrlib.crawl.commands.AddMember]. The methods return facts about the group as a
    whole: who is still alive, how fast the group walks, and who stands where in a fight.

    A party is mutable. The session heals, wounds, and re-equips its members in place, and assigning
    a new list to `members` validates it. The list is never empty: when the last member dies, the
    session ends in `game_over` and the dead stay in the list.

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
    """The party's characters, in marching order: index 0 walks in front and meets whatever the party
    walks into first. At least one member is required.
    [`GameSession.new`][osrlib.crawl.session.GameSession.new] assigns ids when the party joins a
    session, so a member you just built has `None` as its `id` until then.

    Don't change this list directly. Use a command, so the change is logged and a replay
    reproduces it:

    - To add a member during play, execute [`AddMember`][osrlib.crawl.commands.AddMember]. It
      assigns the new member's id and puts them at the back.
    - To change the order outside a fight, execute
      [`ReorderParty`][osrlib.crawl.commands.ReorderParty].
    - In a battle, a member who withdraws alone names the member to trade places with in
      [`BattleDeclaration.replacement_id`][osrlib.crawl.commands.BattleDeclaration.replacement_id].
    """

    def living_members(self) -> list[Character]:
        """Return the living members, in marching order.

        A member is living until they have the `dead` condition. Every rule that applies to the group
        uses this list, so a member who dies stops counting toward movement, ranks, and checks at
        once, without leaving the party.

        A member turned to stone isn't dead, so they stay in this list and keep their rank. The
        session's `game_over` check asks a narrower question: whether any member is neither dead
        nor petrified.

        Returns:
            The members without the `dead` condition, in marching order. Empty when every member
                is dead.
        """
        return [member for member in self.members if not has_condition(member, Condition.DEAD)]

    def member(self, character_id: str) -> Character:
        """Return the member with `character_id`.

        Use this to turn an id from a command or an event into the character it refers to. Events
        identify characters by id, not by name. The ids come from
        [`GameSession.new`][osrlib.crawl.session.GameSession.new], which gives each member an id like
        `character-0001`, in party order. [`AddMember`][osrlib.crawl.commands.AddMember] gives a
        member who joins during play the next id. Inside a session,
        [`GameSession.member`][osrlib.crawl.session.GameSession.member] does the same lookup.

        Args:
            character_id: The member's id, like `"character-0001"`.

        Returns:
            The character. Dead members are returned too, because their gear and their record are
                still the party's.

        Raises:
            ValueError: If no member has that id. The message includes the id.

        Examples:
            ```python
            from osrlib.core.abilities import AbilityScore
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import Character
            from osrlib.crawl.adventure import Adventure, TownSpec
            from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
            from osrlib.crawl.party import Party
            from osrlib.crawl.session import GameSession

            members = [
                Character(
                    name=name,
                    class_id="fighter",
                    race="human",
                    level=1,
                    xp=0,
                    scores={ability: 12 for ability in AbilityScore},
                    alignment=Alignment.LAWFUL,
                    max_hp=8,
                    current_hp=8,
                )
                for name in ("Hild", "Osric")
            ]
            level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
            crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
            adventure = Adventure(name="A First Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
            session = GameSession.new(Party(members=members), adventure, seed=7)

            # Ids come from the session; an event about the second member carries "character-0002".
            print(session.party.member("character-0002").name)
            # Osric
            ```
        """
        for member in self.members:
            if member.id == character_id:
                return member
        raise ValueError(f"no party member with id {character_id!r}")

    def movement_rate(self, ruleset: Ruleset) -> int:
        """Return the party's exploration rate, which is the slowest living member's rate.

        B/X moves a group at the pace of its slowest member, so one overloaded character slows
        everybody. Call [`Character.movement_rate`][osrlib.core.character.Character.movement_rate]
        for one character's rate. For the rate a running session uses, call
        [`exploration_rate`][osrlib.crawl.exploration.exploration_rate]. It takes the same minimum,
        but first halves the rate of a member who has gone too long without food or water, when the
        `deprivation_penalties` ruleset option is on.

        Args:
            ruleset: The ruleset whose encumbrance option applies. Encumbrance is the rule that
                turns what a character carries into a movement rate, and the options differ in what
                they count.

        Returns:
            The rate in feet per exploration turn: 120, 90, 60, 30, or 0. A rate of 0 means the party
                cannot move at all, either because its slowest living member is overloaded or because
                nobody is alive to walk.

        Examples:
            ```python
            from osrlib.core.abilities import AbilityScore
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import Character
            from osrlib.core.ruleset import Ruleset
            from osrlib.crawl.party import Party

            hild = Character(
                name="Hild",
                class_id="fighter",
                race="human",
                level=1,
                xp=0,
                scores={ability: 12 for ability in AbilityScore},
                alignment=Alignment.LAWFUL,
                max_hp=8,
                current_hp=8,
            )
            party = Party(members=[hild])
            print(party.movement_rate(Ruleset()))
            # 120
            ```
        """
        rates = [member.movement_rate(ruleset) for member in self.living_members()]
        return min(rates, default=0)

    def ranks(self, width: int) -> list[list[Character]]:
        """Chunk the living members into combat ranks of `width`, in marching order.

        A rank is one row of the formation. The first `width` living members stand in front and
        fight in melee, and the rest stand behind them. During a battle, the session calls this
        with a width it works out from the space the party stands in, so you rarely choose a width
        yourself. Call it to draw the formation, or to find out who is in front outside a fight.

        The ranks aren't stored. Each call works them out again, so when a front-rank member dies,
        the next living member is in front from the next call on.

        Args:
            width: How many characters stand side by side. In a battle, the session works this out
                from the width of the space the party stands in (see
                [`FIGHTER_FRONTAGE_FEET`][osrlib.crawl.battle.FIGHTER_FRONTAGE_FEET]) while the
                `formation_width_limit` ruleset option is on, and puts the whole party in one rank
                when it's off.

        Returns:
            The ranks, front first. The last rank contains the remainder and can be shorter than
                `width`. Empty when nobody is alive.

        Raises:
            ValueError: If `width` is not positive.

        Examples:
            ```python
            from osrlib.core.abilities import AbilityScore
            from osrlib.core.alignment import Alignment
            from osrlib.core.character import Character
            from osrlib.crawl.party import Party

            members = [
                Character(
                    name=name,
                    class_id="fighter",
                    race="human",
                    level=1,
                    xp=0,
                    scores={ability: 12 for ability in AbilityScore},
                    alignment=Alignment.LAWFUL,
                    max_hp=8,
                    current_hp=8,
                )
                for name in ("Hild", "Osric", "Brand")
            ]
            party = Party(members=members)
            for number, rank in enumerate(party.ranks(2), start=1):
                print(number, [member.name for member in rank])
            # 1 ['Hild', 'Osric']
            # 2 ['Brand']
            ```
        """
        if width < 1:
            raise ValueError(f"rank width must be positive, got {width}")
        living = self.living_members()
        return [living[index : index + width] for index in range(0, len(living), width)]

    def reorder(self, character_ids: Sequence[str]) -> None:
        """Rewrite the marching order in place.

        The session calls this when it accepts a
        [`ReorderParty`][osrlib.crawl.commands.ReorderParty] command. Execute that command with
        [`GameSession.execute`][osrlib.crawl.session.GameSession.execute] rather than calling this
        method yourself, so the change is logged and a replay reproduces it.

        Args:
            character_ids: Every member's id, in the new order. It must list each current member
                exactly once, with no other ids. Include the dead members, because they're still in
                the party.

        Raises:
            ValueError: If the ids aren't exactly the current members' ids, or if any member has no id
                yet because the party hasn't joined a session. The order doesn't change.
        """
        by_id = {member.id: member for member in self.members if member.id is not None}
        if len(by_id) != len(self.members) or sorted(character_ids) != sorted(by_id):
            raise ValueError("reorder must name every current member exactly once")
        self.members = [by_id[character_id] for character_id in character_ids]


def _anyone_standing(party: Party) -> bool:
    """Whether any member stands, which means they are neither dead nor petrified.

    The session's `game_over` check and a battle's defeat check ask this. A statue still counts
    among [`living_members`][osrlib.crawl.party.Party.living_members] for every other rule, so a
    party turned wholly to stone has living members but nobody standing.
    """
    return any(
        not has_condition(member, Condition.DEAD) and not has_condition(member, Condition.PETRIFIED)
        for member in party.members
    )
