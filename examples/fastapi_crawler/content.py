"""The served content: the TUI crawler's barrow, unchanged, plus the session wiring.

The adventure is imported from `examples.tui_crawler.content` verbatim. A terminal
and an HTTP API use the same content, and the fetch quest travels with it because
the quest is adventure data rather than front-end code. This module owns the
game-side wiring both entry paths share: build or restore a `GameSession` and register the
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] that plays the adventure's
triggers and quests. Listeners are code, so a restored session re-registers them
under the `load_game` contract.
"""

from collections.abc import Mapping

from examples.tui_crawler.content import build_adventure
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.persistence import load_game

__all__ = ["new_session", "restore_session"]


# --8<-- [start:new-session]
def new_session(party: Party, *, seed: int, player_handle_key: bytes) -> GameSession:
    """Create a session serving the barrow, with the adventure's quest in play.

    Args:
        party: The party, in marching order.
        seed: The master seed, which stays on the server.
        player_handle_key: An independent server-side key for player feature handles.

    Returns:
        The session, in town, at round 0.
    """
    session = GameSession.new(party, build_adventure(), seed=seed, player_handle_key=player_handle_key)
    session.register_listener(Interpreter(session))
    return session


# --8<-- [end:new-session]


# --8<-- [start:restore-session]
def restore_session(document: Mapping[str, object]) -> GameSession:
    """Restore a session from a save document, re-registering the interpreter.

    Args:
        document: A save document from the server-side store.

    Returns:
        The restored session.

    Raises:
        ContentValidationError: If the save document is malformed.
        SaveVersionError: If the save's schema version is newer than the engine's.
    """
    session = load_game(document)
    session.register_listener(Interpreter(session))
    return session


# --8<-- [end:restore-session]
