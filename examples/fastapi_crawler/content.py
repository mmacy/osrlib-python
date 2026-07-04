"""The served content: the TUI crawler's barrow, unchanged, plus the session wiring.

The adventure is imported from `examples.tui_crawler.content` verbatim — the same
content behind a terminal and an HTTP API is the spec's presentation-agnostic claim
made concrete. This module owns the game-side wiring both entry paths share: build
or restore a `GameSession` and register the fetch-quest listener (listeners are game
objects, so a restored session re-registers them — the `load_game` contract).
"""

from collections.abc import Mapping

from examples.tui_crawler.content import build_adventure
from examples.tui_crawler.quest import FetchQuestListener
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game

__all__ = ["new_session", "restore_session"]


def new_session(party: Party, *, seed: int) -> GameSession:
    """Create a session serving the barrow, with the fetch quest listening.

    Args:
        party: The party, in marching order.
        seed: The master seed — a server-side secret.

    Returns:
        The session, in town, at round 0.
    """
    session = GameSession.new(party, build_adventure(), seed=seed)
    session.register_listener(FetchQuestListener(session))
    return session


def restore_session(document: Mapping[str, object]) -> GameSession:
    """Restore a session from a save document, re-registering the quest listener.

    Args:
        document: A save document from the server-side store.

    Returns:
        The restored session.

    Raises:
        ContentValidationError: If the save document is malformed.
        SaveVersionError: If the save's schema version is newer than the engine's.
    """
    session = load_game(document)
    session.register_listener(FetchQuestListener(session))
    return session
