"""The fetch quest — the spec's extension-surface proof, in game code only.

The listener is keyed `fetch_quest`, watches `ItemAcquiredEvent` for the MacGuffin
and `LocationEnteredEvent` for the town return, keeps its objective state in the
listener store, and reacts by executing ordinary referee commands:

- `GrantCoins` for the recovery reward the moment the idol is acquired, *in the
  dungeon*, where the next award's valuation delta honors it — a reward granted at
  the town-return event would land after the award fired and before the next
  snapshot, earning nothing. The timing is part of the quest pattern the example
  teaches.
- `SetFlag("quest.idol", "recovered")` and an `AwardXP` quest bonus on the town
  return.

No library change: the listener reads session state, never mutates it directly,
and everything it causes goes through logged commands. Because those nested
executes append their own events to the session log, the listener returns no
events of its own — returning the nested results would double-log them. The
returned-events channel is for events a listener *authors* directly.
"""

from collections.abc import Sequence

from osrlib.core.events import Event
from osrlib.core.items import Coins
from osrlib.crawl.commands import AwardXP, GrantCoins, SetFlag
from osrlib.crawl.events import ItemAcquiredEvent, LocationEnteredEvent

from .content import IDOL_NAME, QUEST_BONUS_XP, QUEST_REWARD_GP


# --8<-- [start:fetch-quest-listener]
class FetchQuestListener:
    """Recover the Jade Idol and bring it home — a quest tracker as a listener."""

    key = "fetch_quest"

    def __init__(self, session) -> None:
        """Bind the listener to the session it issues referee commands through."""
        self._session = session
        self._reacting = False

    def _idol_carrier(self):
        for member in self._session.party.members:
            for valuable in member.inventory.valuables:
                if valuable.name == IDOL_NAME:
                    return member
        return None

    def handle(self, events: Sequence[Event], state: dict) -> tuple[list[Event], dict]:
        """React to one command's events (see the session listener contract)."""
        if self._reacting:
            return [], state
        state = dict(state)
        acquired = any(isinstance(event, ItemAcquiredEvent) for event in events)
        if acquired and not state.get("reward_granted"):
            carrier = self._idol_carrier()
            if carrier is not None:
                state["reward_granted"] = True
                self._reacting = True
                try:
                    self._session.execute(GrantCoins(character_id=carrier.id, coins=Coins(gp=QUEST_REWARD_GP)))
                finally:
                    self._reacting = False
        returned_to_town = any(
            isinstance(event, LocationEnteredEvent) and event.location_kind == "town" for event in events
        )
        if returned_to_town and state.get("reward_granted") and not state.get("completed"):
            state["completed"] = True
            self._reacting = True
            try:
                self._session.execute(SetFlag(key="quest.idol", value="recovered"))
                for member in self._session.party.living_members():
                    self._session.execute(AwardXP(character_id=member.id, amount=QUEST_BONUS_XP))
            finally:
                self._reacting = False
        return [], state


# --8<-- [end:fetch-quest-listener]
