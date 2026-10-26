"""The two version stamps on every osrlib document, and the envelope that contains them.

Anything osrlib writes for you to keep is a stamped document: a save, a character, a
party, a content pack. A stamped document is a dict with the same four keys every time.
`kind` is the type of thing inside, `schema_version` and `engine_version` are the two
version stamps, and `payload` is the serialized content. osrlib calls the four keys the
envelope. [`stamp_document`][osrlib.versioning.stamp_document] builds one and
[`check_document`][osrlib.versioning.check_document] checks one. You rarely call either
yourself: [`save_game`][osrlib.persistence.save_game] and
[`load_game`][osrlib.persistence.load_game] call them for saves, and
[`Character.to_document`][osrlib.core.character.Character.to_document],
[`party_to_document`][osrlib.core.character.party_to_document], and
[`ContentPack.to_document`][osrlib.crawl.content_pack.ContentPack.to_document] call
`stamp_document` for characters, parties, and content packs.

The members, in the order you use them:

- [`stamp_document`][osrlib.versioning.stamp_document]: takes a kind and a payload, and
  returns a stamped document.
- [`check_document`][osrlib.versioning.check_document]: takes a stamped document and the
  kind you expect, and returns the payload.
- [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION]: the format version this library
  writes.
- [`engine_version`][osrlib.versioning.engine_version]: returns the installed osrlib
  version.

The two stamps have different purposes.

Use [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] to find out whether osrlib can
still read a document. It's one integer shared by every document kind, and it changes only when the format of
stored data changes in a way that would break a reader. osrlib reads a document stamped lower
than the running library's number and upgrades it. For a document stamped higher, it raises
[`SaveVersionError`][osrlib.errors.SaveVersionError], because this version can't read what a
later version added.

Use [`engine_version`][osrlib.versioning.engine_version] to find out whether you can still
reproduce a game. It's the installed package version, and it changes with every release, including
releases that change no document format. Rules can change between releases, so a recorded
command log run again under a different engine can resolve differently. When you pass the
recorded version, [`replay_game`][osrlib.persistence.replay_game] raises an error on a mismatch
instead of producing a game that differs with no warning. Loading a save works across engine
versions, because a save contains the state instead of computing it again.

If you store documents, keep both numbers with them. From `schema_version` you know whether
the installed library can open the file, and from `engine_version` whether a replay of
it is still valid.

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
number. osrlib stamps saves, characters, parties, and content packs with it, and the same
number versions the formats of commands and events. There's one schema version for the whole
library, not one per kind.

Compare a stored document's `schema_version` with this number to know what you can do
with it:

- Lower: the document still loads. osrlib upgrades it when it reads it, through
  [`MIGRATIONS`][osrlib.persistence.MIGRATIONS] for a save, so you don't need code of your
  own for old files.
- Equal: it loads as written.
- Higher: a later osrlib wrote it, and reading it raises
  [`SaveVersionError`][osrlib.errors.SaveVersionError].

The number changes only when a change would break a reader: a field renamed, a field
removed, a value that now means something different. Additions don't change it. A document
from an earlier release with the same schema version can lack fields that newer documents
include, and readers fill those with their defaults.

What each version changes in an older save:

- Version 2 drops the recovered-treasure ledger from the save payload. The
  session works out the end-of-adventure award from the valuation it records when the party leaves town.
- Version 3 allows only `"open"` as a treasure trap's `trigger`, the one action that
  springs a cache. The migration rewrites an `"enter"` trigger to `"open"`, and a content
  pack gets the same rewrite when it loads.
- Version 4 drops `"withdraw"` from a battle declaration's `move`. The round resolver
  never acted on that value, so a member who declared it stood still. The migration
  clears it from each logged declaration that has it. If that declaration's action is
  `"move"`, the action becomes `"hold"`.

No step drops anything osrlib reads back.

Don't assign to it. It describes the library instead of configuring it, and a new value
changes the version your documents are stamped with without changing what's in them.
"""


def engine_version() -> str:
    """Return the version of the installed osrlib package.

    This is the second stamp on every document, and `replay_game` uses it to detect that the
    rules may have changed. [`stamp_document`][osrlib.versioning.stamp_document] calls it for you, so
    you usually read the value out of a document instead of calling this function. Pass that
    value to [`replay_game`][osrlib.persistence.replay_game] as `recorded_engine_version`
    when you want it to raise an error if the engine version has changed since the log was recorded.

    Call it directly to label a bug report, or to compare with a stamp you stored
    elsewhere. It isn't the schema version. This number changes with every release, including
    releases that change no document format, so you can't use it to tell whether a document
    still parses. Use [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION] for that.

    Returns:
        The installed package version, as the packaging metadata reports it, like `"2.0.0"`.

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

    Use this when you serialize something of your own and want it stored the way osrlib
    documents are, so [`check_document`][osrlib.versioning.check_document] can check it when
    you read it back, and a later reader can tell what's inside. For a save, a party, a
    character, or a content pack, call [`save_game`][osrlib.persistence.save_game],
    [`party_to_document`][osrlib.core.character.party_to_document],
    [`Character.to_document`][osrlib.core.character.Character.to_document], or
    [`ContentPack.to_document`][osrlib.crawl.content_pack.ContentPack.to_document] instead.
    Each one stamps the right kind and builds the payload for you.

    The result is ready for `json.dumps` if the payload is. Pass a payload you already
    turned into JSON-compatible values, which for a pydantic model means
    `model_dump(mode="json")`. This function copies the mapping one level deep and converts
    nothing inside it.

    Args:
        kind: What the document contains, like `"character"` or `"party"`. When you read the
            document back, pass the same string to `check_document`, which raises an error for
            a document of any other kind. Pick one name per document type and keep it.
        payload: The serialized content, in JSON-compatible values.

    Returns:
        A new dict with `kind`, `schema_version`, `engine_version`, and `payload` keys. The
            payload is a shallow copy, so later edits to the mapping you passed don't change
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

    Call this first when you read a document back, before you read any field of the
    payload. It confirms that the envelope's keys are there and well formed, that the
    document is of the kind you expect, and that a later osrlib didn't write it.

    It checks the envelope, not the payload. A payload that passes here can still fail when
    you validate it into a model. [`load_game`][osrlib.persistence.load_game] and
    [`party_from_document`][osrlib.core.character.party_from_document] call this function
    and then validate the payload, so call it directly only for a document kind you
    stamped yourself with [`stamp_document`][osrlib.versioning.stamp_document].

    A document with an older schema version passes, and you upgrade its payload yourself, as
    `load_game` does through [`MIGRATIONS`][osrlib.persistence.MIGRATIONS]. The function
    ignores extra keys in the envelope, so you can still read a document that a later release
    wrote with the same schema version. It doesn't check the `engine_version` key.

    Args:
        document: A mapping produced by
            [`stamp_document`][osrlib.versioning.stamp_document], usually parsed back from
            JSON.
        expected_kind: The `kind` string you expect, like `"character"`. The function raises
            an error for a document of any other kind, so a party document can't be read as a
            save.

    Returns:
        A new dict with the document's payload, copied one level deep, still at whatever
            schema version the document was written under.

    Raises:
        ContentValidationError: If the document is not a mapping, is missing `kind`,
            `schema_version`, or `payload`, has a non-integer `schema_version` or a
            non-mapping payload, or is of a kind other than `expected_kind`.
        SaveVersionError: If the document's `schema_version` is higher than
            [`SCHEMA_VERSION`][osrlib.versioning.SCHEMA_VERSION], meaning a newer osrlib
            wrote it.

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
