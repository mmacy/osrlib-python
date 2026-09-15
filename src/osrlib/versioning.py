"""The two version stamps on every osrlib document, and the envelope that contains them.

Anything osrlib writes for you to keep goes out as a stamped document: a save, a character,
a party, a content pack. The envelope is the same shape every time. It names the `kind` of
thing inside, the two version numbers, and the `payload`, which is the serialized content.
[`stamp_document`][osrlib.versioning.stamp_document] builds one and
[`check_document`][osrlib.versioning.check_document] checks one, and you rarely call either
yourself: [`save_game`][osrlib.persistence.save_game] and
[`load_game`][osrlib.persistence.load_game] wrap them for saves, and
[`party_to_document`][osrlib.core.character.party_to_document] does for parties.

The two stamps answer two different questions.

[`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] answers whether a document can still be
read. It's one integer shared by every document kind, and it moves only when the shape of
stored data changes in a way a reader would trip over. A document stamped lower than the
running library's number is read and brought forward. One stamped higher is refused with
[`SaveVersionError`][osrlib.errors.SaveVersionError], because the reader can't know what a
later version put in there.

[`engine_version`][osrlib.versioning.engine_version] answers whether a game can still be
reproduced. It's the installed package version, and it moves with every release, including
releases that change no document shape. Rules can change between releases, so a recorded
command log rerun under a different engine can resolve differently.
[`replay_game`][osrlib.persistence.replay_game] refuses the mismatch rather than producing a
game that differs without saying so. Loading a save across engine versions is fine, because
a save contains the state instead of re-deriving it.

If you store documents, keep both numbers with them. `schema_version` tells you whether the
library in front of you can open the file, and `engine_version` tells you whether a replay
of it still holds.

Typical usage:

```python
from osrlib.versioning import SCHEMA_VERSION, check_document, stamp_document

document = stamp_document("note", {"text": "found the crypt"})
print(document["kind"], document["schema_version"] == SCHEMA_VERSION)
# note True

print(check_document(document, "note"))
# {'text': 'found the crypt'}
```
"""

from collections.abc import Mapping
from importlib import metadata

from osrlib.errors import ContentValidationError, SaveVersionError

__all__ = [
    "SCHEMA_VERSION",
    "check_document",
    "engine_version",
    "stamp_document",
]

SCHEMA_VERSION = 4
"""The schema version this library writes, and the highest it can read.

Every document [`stamp_document`][osrlib.versioning.stamp_document] produces includes this
number, and saves, commands, events, characters, parties, and content packs all share it.
There's one schema version for the whole library, not one per kind.

Compare a stored document's `schema_version` against this to know what you can do with it.
Lower means the document still loads, and the reader brings it forward through
[`MIGRATIONS`][osrlib.persistence.MIGRATIONS] on the way in, so you need no code of your own
for old files. Equal means it loads as written. Higher means a later osrlib wrote it, and
reading it raises [`SaveVersionError`][osrlib.errors.SaveVersionError].

The number moves only when a change would break a reader: a field renamed, a field removed,
a value that now means something different. Additions don't move it, so a document from an
earlier release of the same schema version can be missing fields that newer documents
include, and readers fill those with their defaults.

Three changes are behind the current number, and each is worth knowing if you keep old saves.
Version 2 dropped the recovered-treasure ledger from the save payload, since the
end-of-adventure award is worked out from the valuation taken when the party left town.
Version 3 narrowed a treasure trap's `trigger` to `"open"`, the one action that springs a
cache. Earlier documents could say `"enter"`, which nothing ever read, and the migration
rewrites it, and a content pack gets the same trigger rewrite when it loads. Version 4
dropped `"withdraw"` from a battle declaration's `move`, a value the round resolver never
moved anybody for, and the migration rewrites a logged one into the hold it played as. No
step loses anything.

This is a fact about the library, not a setting. Assigning to it changes what your documents
claim to be without changing what's in them.
"""


def engine_version() -> str:
    """Return the version of the installed osrlib package.

    This is the second stamp on every document, and it's what makes a replay trustworthy.
    [`stamp_document`][osrlib.versioning.stamp_document] calls it for you, so usually you
    read this value out of a document rather than calling the function, then pass it to
    [`replay_game`][osrlib.persistence.replay_game] as `recorded_engine_version` when you
    want the replay refused if the rules underneath have moved.

    Call it directly to label a bug report, or to compare against a stamp you stored
    elsewhere. It isn't the schema version. This number changes with every release, including
    releases that change no document shape, so it tells you nothing about whether a document
    still parses. [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] answers that.

    Returns:
        The installed package version, as the packaging metadata reports it, like `"0.9.1"`.

    Examples:
        ```python
        from osrlib.versioning import engine_version, stamp_document

        document = stamp_document("note", {"text": "found the crypt"})
        assert document["engine_version"] == engine_version()
        ```
    """
    return metadata.version("osrlib")


def stamp_document(kind: str, payload: Mapping[str, object]) -> dict[str, object]:
    """Wrap a serialized payload in the stamped-document envelope.

    Use this when you serialize something of your own and want it to travel the way osrlib's
    own documents do, so [`check_document`][osrlib.versioning.check_document] can check it on
    the way back in and a later reader can tell what's inside. For a save, a party, or a
    content pack, call [`save_game`][osrlib.persistence.save_game],
    [`party_to_document`][osrlib.core.character.party_to_document], or the pack's own writer
    instead. Each one stamps its own kind and fills the payload correctly.

    The result is plain data, ready for `json.dumps`, as long as what you put in it is. Pass
    a payload you already turned into JSON-compatible values, which for a pydantic model
    means `model_dump(mode="json")`. This function copies the mapping one level deep and
    converts nothing inside it.

    Args:
        kind: What the document contains, like `"character"` or `"party"`. The reader passes
            the same string to `check_document`, which refuses a document of any other kind,
            so pick one name per document type and keep it.
        payload: The serialized content, in JSON-compatible values.

    Returns:
        A new dict with `kind`, `schema_version`, `engine_version`, and `payload` keys. The
            payload is a shallow copy, so later edits to the mapping you passed do not reach
            the document, though edits to objects nested inside it do.

    Raises:
        ValueError: If `kind` is empty.

    Examples:
        ```python
        from osrlib.versioning import stamp_document

        document = stamp_document("note", {"text": "found the crypt"})
        print(sorted(document))
        # ['engine_version', 'kind', 'payload', 'schema_version']
        ```
    """
    if not kind:
        raise ValueError("document kind must be non-empty")
    return {
        "kind": kind,
        "schema_version": SCHEMA_VERSION,
        "engine_version": engine_version(),
        "payload": dict(payload),
    }


def check_document(document: Mapping[str, object], expected_kind: str) -> dict[str, object]:
    """Check a stamped document's envelope and return the payload inside it.

    Call this first thing when you read a document back, before you touch a single field of
    the payload. It confirms the envelope is there and well formed, that the document
    contains what you think it does, and that a later osrlib didn't write it. What you get
    back is a payload you can trust to be the right kind of thing.

    It checks the envelope, not the contents. A payload that passes here can still fail when
    you validate it into a model, which is where [`load_game`][osrlib.persistence.load_game]
    and [`party_from_document`][osrlib.core.character.party_from_document] take it next, and
    both of those call this function for you. Call it directly only for a document kind you
    stamped yourself with [`stamp_document`][osrlib.versioning.stamp_document].

    A document older than the current schema passes. The envelope is accepted and the caller
    migrates the payload forward, as `load_game` does through
    [`MIGRATIONS`][osrlib.persistence.MIGRATIONS]. Extra keys in the envelope are ignored, so
    a document written by a later release of the same schema version still reads.

    Args:
        document: A mapping produced by
            [`stamp_document`][osrlib.versioning.stamp_document], usually parsed back from
            JSON.
        expected_kind: The `kind` string you expect, like `"character"`. A document of any
            other kind is refused, which is what stops a party document from being read as a
            save.

    Returns:
        A new dict with the document's payload, copied one level deep, still at whatever
            schema version the document was written under.

    Raises:
        ContentValidationError: If the document is not a mapping, is missing `kind`,
            `schema_version`, or `payload`, has a non-integer `schema_version` or a
            non-mapping payload, or is of a kind other than `expected_kind`.
        SaveVersionError: If the document's `schema_version` is higher than
            [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION], meaning it was written by a
            newer osrlib.

    Examples:
        ```python
        from osrlib.errors import ContentValidationError
        from osrlib.versioning import check_document, stamp_document

        document = stamp_document("note", {"text": "found the crypt"})
        print(check_document(document, "note"))
        # {'text': 'found the crypt'}

        try:
            check_document(document, "save")
        except ContentValidationError as error:
            print(error)
        # expected a 'save' document, got kind 'note'
        ```
    """
    if not isinstance(document, Mapping):
        raise ContentValidationError(f"document must be a mapping, got {type(document).__name__}")
    for key in ("kind", "schema_version", "payload"):
        if key not in document:
            raise ContentValidationError(f"document is missing required key {key!r}")
    kind = document["kind"]
    if kind != expected_kind:
        raise ContentValidationError(f"expected a {expected_kind!r} document, got kind {kind!r}")
    schema_version = document["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ContentValidationError(f"schema_version must be an integer, got {schema_version!r}")
    if schema_version > SCHEMA_VERSION:
        raise SaveVersionError(f"document schema_version {schema_version} is newer than the supported {SCHEMA_VERSION}")
    payload = document["payload"]
    if not isinstance(payload, Mapping):
        raise ContentValidationError(f"document payload must be a mapping, got {type(payload).__name__}")
    return dict(payload)
