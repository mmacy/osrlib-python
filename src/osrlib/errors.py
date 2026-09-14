"""The exceptions osrlib raises, and the failures they stand for.

Three different things can go wrong when you call this library, and each has its own
answer. Something a player tried that the rules forbid, like walking into a wall or choosing
a class their scores don't qualify for, isn't an exception at all. The call returns a
refusal, a [`Rejection`][osrlib.core.validation.Rejection], which gives you a code and the
facts behind it so you can tell the player why. Something you got wrong in your own code,
like an ability score outside 3 to 18 or a seed out of range, raises the stdlib `ValueError`
or `TypeError`, because that's a bug to fix rather than a state to handle. Everything else
raises from the hierarchy here.

That leaves these exceptions for the failures that come from outside the running game: a
save file someone truncated or hand-edited, a document written by a newer version of the
library than the one reading it, a dice expression that doesn't parse, a command log
replayed under different rules.
[`OsrlibError`][osrlib.errors.OsrlibError] is the base class, so a single `except
OsrlibError` catches all of them, and the three subclasses let you separate the cases that
deserve different answers.

Which one you see depends on where you are. Reading a document raises
[`ContentValidationError`][osrlib.errors.ContentValidationError] when the document is
malformed and [`SaveVersionError`][osrlib.errors.SaveVersionError] when it's only too new.
Replaying a command log raises
[`ReplayVersionError`][osrlib.errors.ReplayVersionError] when the engine underneath has
changed. The difference matters: you can't recover from the first, the second means telling
the player to upgrade, and the third means loading the save instead of replaying it.

How you report a failure is yours to choose: an HTTP status code, a process exit code, a
dialog. The hierarchy grows by addition, so a later version can add an exception type but
won't remove or repurpose one, and an `except OsrlibError` you write today keeps catching
everything.

Examples:
    ```python
    from osrlib.errors import OsrlibError, SaveVersionError
    from osrlib.versioning import check_document

    future = {"kind": "save", "schema_version": 999, "payload": {}}
    try:
        check_document(future, "save")
    except SaveVersionError as error:
        print(f"too new: {error}")
    except OsrlibError:
        print("unreadable")
    # too new: document schema_version 999 is newer than the supported 3
    ```
"""

__all__ = [
    "ContentValidationError",
    "OsrlibError",
    "ReplayVersionError",
    "SaveVersionError",
]


class OsrlibError(Exception):
    """The base class every osrlib exception inherits from.

    Catch this when you want one handler for anything the library refuses to do, at the edge
    of a web request or a command-line program, and you don't need to tell the cases apart.
    Catch a subclass instead when you do. A caller can offer a repair for
    [`SaveVersionError`][osrlib.errors.SaveVersionError] and for
    [`ReplayVersionError`][osrlib.errors.ReplayVersionError], and none for
    [`ContentValidationError`][osrlib.errors.ContentValidationError].

    Nothing raises `OsrlibError` itself, and don't raise it from your own code. It exists to
    be caught, and a bare instance tells a handler nothing about what happened.

    It doesn't cover a player choice the rules refuse, which comes back as a
    [`Rejection`][osrlib.core.validation.Rejection] rather than being raised, nor a mistake
    in your own call, which raises the stdlib `ValueError` or `TypeError`. Catching
    `OsrlibError` alone leaves both of those to travel on, which is what you want.
    """


class ContentValidationError(OsrlibError):
    """Raised when content handed to the library is malformed.

    This is the failure with no repair. Whatever was read can't be understood, so there's
    nothing to fall back to. Show the message, which names what was wrong, and go no further
    with that input.

    It comes from the boundaries where osrlib accepts something from outside itself:

    - [`parse`][osrlib.core.dice.parse], on a dice expression that doesn't match the grammar.
    - [`load_game`][osrlib.persistence.load_game],
      [`check_document`][osrlib.versioning.check_document], and
      [`party_from_document`][osrlib.core.character.party_from_document], on a document whose
      envelope, kind, or payload isn't what they expect.
    - [`parse_command`][osrlib.crawl.commands.parse_command] and
      [`parse_any_event`][osrlib.crawl.events.parse_any_event], by way of the loaders that
      call them.
    - [`validate_adventure`][osrlib.crawl.adventure.validate_adventure], on an adventure whose
      own structure doesn't hold together.
    - The loaders in [`osrlib.data`][osrlib.data], when the data shipped in the package fails
      model validation.
    - [`replay_game`][osrlib.persistence.replay_game], when a logged command is refused the
      second time. That means the replay has diverged from the game it was meant to reproduce.

    A document that's well formed but stamped with a newer schema raises
    [`SaveVersionError`][osrlib.errors.SaveVersionError] instead, and you can recover from
    that one by telling the player to upgrade.
    """


class SaveVersionError(OsrlibError):
    """Raised when a document was written by a newer version of osrlib than the one reading it.

    Every document osrlib writes includes a `schema_version`, and
    [`check_document`][osrlib.versioning.check_document] compares it against
    [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] before any field is read. A number
    higher than the running library's means the document can contain shapes this code has
    never seen, so the read stops there rather than guessing and misreading it.

    Catch this separately from
    [`ContentValidationError`][osrlib.errors.ContentValidationError] where a player is
    watching, because it has an answer: the file is fine, the library is behind. Tell them to
    upgrade osrlib and try again. An older document needs no handling from you, because
    [`load_game`][osrlib.persistence.load_game] migrates it forward.

    It reaches you from [`load_game`][osrlib.persistence.load_game],
    [`party_from_document`][osrlib.core.character.party_from_document],
    [`ContentPack.from_document`][osrlib.crawl.content_pack.ContentPack.from_document], and
    any other reader that calls `check_document` first.
    """


class ReplayVersionError(OsrlibError):
    """Raised when a command log is replayed under an engine version other than the one that recorded it.

    Only [`replay_game`][osrlib.persistence.replay_game] raises it, and **only** when you
    pass `recorded_engine_version`, so you decide whether the check happens at all.

    Replay runs the recorded commands again from the same seed and relies on the rules
    resolving them the same way. A change to those rules can move an outcome, which would
    make the replayed game differ from the one that was played. Comparing the versions turns
    that into a failure you can see instead of a difference you can't.

    When you catch it, load the save rather than replaying its log.
    [`load_game`][osrlib.persistence.load_game] restores the recorded state directly and
    works across engine versions. Replay is the stricter path, and this is what it trades for
    that strictness.
    """
