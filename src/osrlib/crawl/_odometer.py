"""How the exploring commands spend game time.

A step adds distance to the session's odometer, and the clock advances a turn when the odometer fills. An action that
takes a turn, like a search, advances the clock a whole turn and sets the odometer back to zero.
"""

from osrlib.core.events import Event


def _accrue_movement(session, units: int) -> list[Event]:
    """Accrue odometer units, and advance the clock one turn once a full turn's worth has built up."""
    from osrlib.crawl.exploration import exploration_rate

    session.odometer_thirds += units
    if session.odometer_thirds >= 3 * max(1, exploration_rate(session)):
        session.odometer_thirds = 0
        events, _ = session.advance_turns(1)
        return events
    return []


def _spend_turn(session, *, resting: bool = False, hidden: bool = False) -> tuple[list[Event], bool]:
    """Advance one whole turn, absorbing the partial move (odometer reset)."""
    session.odometer_thirds = 0
    return session.advance_turns(1, resting=resting, hidden=hidden)
