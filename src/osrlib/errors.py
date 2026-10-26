"""The exceptions osrlib raises, and the failures they stand for.

Three kinds of thing can go wrong when you call this library, and each needs a different
response. Something a player tried that the rules forbid, like walking into a wall or choosing
a class their scores don't qualify for, isn't an exception at all. The call returns a
refusal, a [`Rejection`][osrlib.core.validation.Rejection], with a code and the facts behind
it, so you can tell the player why. A mistake in your own code, like an ability score outside
3 to 18 or a seed out of range, raises the standard `ValueError` or `TypeError`, because it's
a bug to fix, not a state to handle. Everything else raises an exception from this module.

These exceptions are for failures that come from outside the running game: a save file
someone truncated or hand-edited, a document written by a newer version of the library than
the one reading it, a dice expression that doesn't parse, a command log replayed under
different rules. [`OsrlibError`][osrlib.errors.OsrlibError] is the base class, so one
`except OsrlibError` catches all of them. Catch the subclasses to handle each case
differently.

Each subclass stands for one failure, with the response it needs:

- [`ContentValidationError`][osrlib.errors.ContentValidationError]: a document, dice
  expression, or adventure is malformed, or a replayed command is refused. For malformed input,
  show the message and stop, because the same input fails the same way. For a refused replay,
  check that you passed the starting party, adventure, ruleset, and seed the game began with.
- [`SaveVersionError`][osrlib.errors.SaveVersionError]: a document is well formed but a newer
  osrlib wrote it. Tell the player to upgrade.
- [`ReplayVersionError`][osrlib.errors.ReplayVersionError]: a command log is being replayed
  under a different engine version than the one that recorded it. Load the save instead.

How you report a failure is up to you: an HTTP status code, a process exit code, a dialog.
A later version can add an exception type but won't remove or repurpose one, so an
`except OsrlibError` you write today keeps catching every osrlib exception.

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
    # too new: document schema_version 999 is newer than the supported 4
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

    Catch this when you want one handler for every osrlib exception, at the edge of a web
    request or a command-line program, and you don't need to tell the cases apart. Catch a
    subclass instead when you do. You can offer the player a fix for
    [`SaveVersionError`][osrlib.errors.SaveVersionError] and for
    [`ReplayVersionError`][osrlib.errors.ReplayVersionError], and none for
    [`ContentValidationError`][osrlib.errors.ContentValidationError].

    osrlib raises only the subclasses, never `OsrlibError` itself. Don't raise it from your own
    code either, because a handler that catches a bare `OsrlibError` can't tell what happened.

    It doesn't cover a player choice the rules refuse, which comes back as a
    [`Rejection`][osrlib.core.validation.Rejection] instead of being raised. It doesn't cover
    a mistake in your own call either, which raises the standard `ValueError` or `TypeError`.
    An `except OsrlibError` lets those two through, so a bug in your call still shows up as a
    bug.

    Examples:
        ```python
        from osrlib.errors import OsrlibError
        from osrlib.persistence import load_game

        try:
            load_game({"kind": "save", "schema_version": 4})  # truncated: no payload
        except OsrlibError as error:
            print(f"{type(error).__name__}: {error}")
        # ContentValidationError: document is missing required key 'payload'
        ```
    """


class ContentValidationError(OsrlibError):
    """Raised when content handed to the library is malformed.

    osrlib can't understand what it read, so it can't recover. Show the message, which says
    what was wrong, and stop using that input. Retrying with the same input fails the same way.

    It comes from the places where osrlib reads something from outside itself:

    - [`parse`][osrlib.core.dice.parse], on a dice expression that doesn't match the grammar.
    - [`load_game`][osrlib.persistence.load_game],
      [`check_document`][osrlib.versioning.check_document],
      [`Character.from_document`][osrlib.core.character.Character.from_document],
      [`party_from_document`][osrlib.core.character.party_from_document], and
      [`ContentPack.from_document`][osrlib.crawl.content_pack.ContentPack.from_document], on a
      document whose envelope, kind, or payload isn't what they expect.
    - [`parse_command`][osrlib.crawl.commands.parse_command],
      [`parse_event`][osrlib.core.events.parse_event], and
      [`parse_any_event`][osrlib.crawl.events.parse_any_event], on a command or event of a
      known type whose fields don't fit that type.
    - [`validate_adventure`][osrlib.crawl.adventure.validate_adventure] and
      [`GameSession.new`][osrlib.crawl.session.GameSession.new], on an adventure whose
      structure is inconsistent or whose bundled monsters or items reuse a built-in id.
    - The loaders in [`osrlib.data`][osrlib.data], when the data included in the package fails
      model validation.
    - [`replay_game`][osrlib.persistence.replay_game], when the log contains a command type
      this version doesn't recognize, or when a logged command is refused on replay. A refusal
      means the replay has diverged from the game it was meant to reproduce.

    A document that's well formed but stamped with a newer schema version raises
    [`SaveVersionError`][osrlib.errors.SaveVersionError] instead, and the player can fix that
    one by upgrading.

    Examples:
        ```python
        from osrlib.core.dice import parse
        from osrlib.errors import ContentValidationError

        try:
            parse("2d")
        except ContentValidationError as error:
            print(error)
        # invalid dice expression: '2d'
        ```
    """


class SaveVersionError(OsrlibError):
    """Raised when a document was written by a newer version of osrlib than the one reading it.

    Every document osrlib writes includes a `schema_version`, the number of its format, and
    [`check_document`][osrlib.versioning.check_document] compares it with
    [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] before any field is read. A higher
    number than the running library's means the document can contain structures this version
    doesn't recognize, so the read stops there instead of misreading them.

    Catch this separately from
    [`ContentValidationError`][osrlib.errors.ContentValidationError] where a player is
    watching, because the player can fix it: the file is fine, and the library is behind. Tell
    them to upgrade osrlib and try again. An older document needs no handling from you, because
    the osrlib readers bring it up to the current format.

    It reaches you from [`load_game`][osrlib.persistence.load_game],
    [`Character.from_document`][osrlib.core.character.Character.from_document],
    [`party_from_document`][osrlib.core.character.party_from_document],
    [`replay_game`][osrlib.persistence.replay_game] by way of its party document,
    [`ContentPack.from_document`][osrlib.crawl.content_pack.ContentPack.from_document], and
    any other reader that calls `check_document` first.

    Examples:
        ```python
        from osrlib.errors import SaveVersionError
        from osrlib.persistence import load_game

        from_the_future = {"kind": "save", "schema_version": 999, "engine_version": "99.0.0", "payload": {}}
        try:
            load_game(from_the_future)
        except SaveVersionError:
            print("This save needs a newer osrlib. Upgrade and try again.")
        # This save needs a newer osrlib. Upgrade and try again.
        ```
    """


class ReplayVersionError(OsrlibError):
    """Raised when a command log is replayed under an engine version other than the one that recorded it.

    Only [`replay_game`][osrlib.persistence.replay_game] raises it, and only when you pass
    `recorded_engine_version`. Leave that argument out and the replay runs without the check.

    A replay runs the recorded commands again from the same seed, and it depends on the rules
    resolving them the same way. A rules change between releases can change an outcome, and
    the replayed game would then differ from the game that was played, with no error. The
    version check raises this exception before the first command runs instead.

    When you catch it, load the save instead of replaying its log.
    [`load_game`][osrlib.persistence.load_game] restores the recorded state directly and
    works across engine versions. A retry under the same installed version fails the same way.

    Examples:
        ```python
        from osrlib.core.alignment import Alignment
        from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character, party_to_document
        from osrlib.core.rng import RngStreams
        from osrlib.core.ruleset import Ruleset
        from osrlib.crawl.adventure import Adventure, TownSpec
        from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
        from osrlib.errors import ReplayVersionError
        from osrlib.persistence import replay_game

        rules = Ruleset()
        roll = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
        pc = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=roll)
        level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
        adventure = Adventure(
            name="A First Delve",
            town=TownSpec(name="Threshold"),
            dungeons=(DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,)),),
        )

        try:
            replay_game(7, party_to_document([pc.character]), adventure, rules, [], recorded_engine_version="0.0.1")
        except ReplayVersionError:
            print("Recorded under another engine version; load the save instead.")
        # Recorded under another engine version; load the save instead.
        ```
    """
