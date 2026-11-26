"""A model's JSON Schema description is written for a reader outside Python, who sees it rendered as CommonMark.

A program that shows a schema's descriptions as Markdown, like an OpenAPI viewer, renders a model's `description`
as CommonMark. The description comes from the class docstring, which is Google style. The documentation site's
renderer turns each section of the docstring, like `Attributes:` or `Examples:`, into a titled block, but CommonMark
doesn't know the sections. It joins the lines under `Attributes:` into one paragraph, and it shows a fence indented
four spaces as an indented code block, backticks and all.

These tests hold the description of every osrlib model and every osrlib enum to this rule:

- The description starts with the docstring's text before its first section, with its cross-references reduced as
  `test_schema_descriptions.py` reduces them. For a docstring with no sections, that text is the whole description,
  as it is for a field.
- The `Modes:`, `Rejections:`, and `Events:` sections follow, in the docstring's order, because they describe what a
  command does on the wire. Each one is its label on a line of its own, a blank line, and the section's body moved
  to the left margin. Its lists and paragraphs then render in CommonMark as they do on the documentation site, and
  a wrapped list item's continuation lines, indented two spaces, stay in their item.
- Every other section is left out. `Attributes:` repeats what each property's own `description` says. `Raises:`,
  `Examples:`, `Args:`, and `Returns:` describe constructing or calling the class in Python.

A section starts at a line at the docstring's left margin that is a label, one or more words and a colon, when the
line after it is indented. A sentence that ends with a colon and introduces text at the left margin, like "There is
at most one payload:" in `StockedArea` or "Emitted by the commands that can set a trap off or look for one:" in
`TrapEvent`, is text, not a section. A section runs to the next section, or to the end of the docstring.
"""

import inspect
import re
import sys
import textwrap
from enum import Enum

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, TypeAdapter

from osrlib.core._descriptions import _StrEnum
from osrlib.core.events import DamageDealtEvent, Event, Visibility
from osrlib.core.frozen import _Model
from osrlib.crawl.adventure import TownSpec
from osrlib.crawl.commands import AnyCommand, PurchaseEquipment
from osrlib.crawl.views import PlayerView
from test_schema_descriptions import _MODELS, _ROOTS, _descriptions, _without_references

# The sections a description keeps, because they describe what a command does on the wire.
_KEPT = ("Modes", "Rejections", "Events")

# The sections a description leaves out. `Attributes:` repeats the properties' descriptions, and the rest describe
# Python use.
_DROPPED = ("Attributes", "Raises", "Examples", "Args", "Returns")

# A line that can start a section: one or more words and a colon, at the left margin.
_LABEL = re.compile(r"[A-Z][A-Za-z ]*:")

# A fenced code block, inside which indentation is code.
_FENCE = re.compile(r"^```.*?^```$", re.DOTALL | re.MULTILINE)


def _sections(docstring: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Return a docstring's text before its first section, and each section's label and source lines."""
    lines = inspect.cleandoc(docstring).split("\n")
    lead: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for index, line in enumerate(lines):
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if _LABEL.fullmatch(line) and following.startswith(" "):
            sections.append((line.removesuffix(":"), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            lead.append(line)
    return "\n".join(lead), sections


def _lead(docstring: str) -> str:
    """Return the text before the first section, reduced, as the description starts."""
    return _without_references(_sections(docstring)[0]).rstrip()


def _body(lines: list[str]) -> str:
    """Return a section's body as the description has it: reduced, and moved to the left margin."""
    return _without_references(textwrap.dedent("\n".join(lines))).strip("\n")


def _expected(docstring: str) -> str:
    """Return the description the rule gives a docstring."""
    parts = [_lead(docstring)]
    parts.extend(f"{label}:\n\n{_body(lines)}" for label, lines in _sections(docstring)[1] if label in _KEPT)
    return "\n\n".join(parts)


def _enums() -> list[type[Enum]]:
    found = {
        value
        for name, module in sys.modules.items()
        if name.startswith("osrlib.")
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, _StrEnum)
        and value is not _StrEnum
        and value.__module__.startswith("osrlib.")
    }
    return sorted(found, key=lambda enum: (enum.__module__, enum.__qualname__))


def _name(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


_ENUMS = _enums()
_SECTIONED_MODELS = [cls for cls in _MODELS if _sections(cls.__doc__ or "")[1]]
_PLAIN_MODELS = [cls for cls in _MODELS if not _sections(cls.__doc__ or "")[1]]
_SECTIONED_ENUMS = [enum for enum in _ENUMS if _sections(enum.__doc__ or "")[1]]
_PLAIN_ENUMS = [enum for enum in _ENUMS if not _sections(enum.__doc__ or "")[1]]


def _description(cls: type) -> str:
    if isinstance(cls, type) and issubclass(cls, BaseModel):
        return cls.model_json_schema()["description"]
    return TypeAdapter(cls).json_schema()["description"]


def _violations(description: str) -> list[str]:
    """Return each way a description breaks the rule's checks on its form, which need no docstring."""
    found: list[str] = []
    lines = description.split("\n")
    if not description.strip():
        found.append("the description is empty")
    found.extend(f"keeps the {line!r} section" for line in lines if line.removesuffix(":") in _DROPPED)
    if "```python" in description:
        found.append("has a Python code block")
    found.extend(
        f"line indented 4 or more spaces, which CommonMark reads as code or as nested: {line!r}"
        for line in _FENCE.sub("", description).split("\n")
        if line.startswith("    ")
    )
    found.extend(f"line ending in whitespace: {line!r}" for line in lines if line != line.rstrip())
    return found


def test_every_section_label_is_classified() -> None:
    # A label that's neither kept nor dropped needs a decision before it can reach a description, and a line at the
    # left margin inside a section would make the section's end ambiguous.
    unclassified = {}
    stray = []
    for cls in [*_MODELS, *_ENUMS]:
        for label, lines in _sections(cls.__doc__ or "")[1]:
            if label not in _KEPT + _DROPPED:
                unclassified[_name(cls)] = label
            stray.extend(f"{_name(cls)} {label}: {line!r}" for line in lines if line and not line.startswith(" "))
    assert unclassified == {}
    assert stray == []


@pytest.mark.parametrize("cls", _SECTIONED_MODELS + _SECTIONED_ENUMS, ids=_name)
def test_a_description_follows_the_rule(cls: type) -> None:
    description = _description(cls)
    docstring = cls.__doc__ or ""

    assert _violations(description) == [], _name(cls)
    assert description.startswith(_lead(docstring)), "the text before the first section is unchanged"
    for label, lines in _sections(docstring)[1]:
        if label in _KEPT:
            assert f"\n\n{label}:\n\n{_body(lines)}" in description, f"the {label} section is its label and its body"
    assert description == _expected(docstring)


@pytest.mark.parametrize("cls", _PLAIN_MODELS + _PLAIN_ENUMS, ids=_name)
def test_a_description_of_a_docstring_without_sections_is_the_reduced_docstring(cls: type) -> None:
    # This holds before the rule for sections and after it. A sentence that ends with a colon, like the one that
    # introduces the commands in `TrapEvent`, isn't a section, so it doesn't end the description.
    description = _description(cls)

    assert description == _without_references(inspect.cleandoc(cls.__doc__ or ""))
    assert _violations(description) == [], _name(cls)


def test_no_field_description_ends_a_line_with_whitespace() -> None:
    # A field docstring of one line whose closing quotes sit on the next line ends in a line of spaces.
    offenders = [
        f"{_name(cls)}.{name}"
        for cls in _MODELS
        for name, prop in cls.model_json_schema().get("properties", {}).items()
        if any(line != line.rstrip() for line in prop.get("description", "").split("\n"))
    ]
    assert offenders == []


# What the description of `PurchaseEquipment` becomes: the text before the first section, then `Modes:`,
# `Rejections:`, and `Events:`, each moved to the left margin. The `Examples:` section, a Python session, is left out.
_PURCHASE_EQUIPMENT = """\
Buy equipment in town for one member, one purchase lot for each `item_ids` entry (takes no game time).

The party must be in town. The session adds up the price of every entry first, and if
the member can't afford the total, nothing is bought. The shop stocks only the built-in
equipment from `load_equipment` (see the
[equipment id index](https://mmacy.github.io/osrlib-python/reference/content-ids/equipment/)).
An item the adventure bundles isn't for sale, even when the party has seen its id, and
the session refuses it as not stocked rather than as unknown.

Modes:

`town`

Rejections:

- `session.command.wrong_mode` - the party is not in town.
- `session.command.unknown_member` - `character_id` matches no party member.
- `session.command.member_incapacitated` - the member can't act.
- `session.command.unknown_item` - an entry matches no equipment item.
- `items.purchase.not_stocked` - an entry is an item the adventure
  bundles, which the shop doesn't sell.
- `items.purchase.insufficient_funds` - the member's purse can't cover the total.

Events:

`ItemAcquiredEvent` listing the
purchases, `origin` `"purchase"`."""

# What the description of `TownSpec` becomes: the text before `Attributes:`, `Raises:`, and `Examples:`, which are all
# left out. Each property describes its field, and each constraint that `Raises:` lists is in its field's description.
_TOWN_SPEC = """\
The base town: where the party is safe, buys gear, and comes back to.

Every adventure has exactly one town, and a session starts there. The town is not a map: it has
no grid, no rooms, and nothing to explore. It is where the rules that need a safe place apply.
In town the party can rest, buy equipment, sell treasure, and pay a temple for healing. Under
the default ruleset, the party earns the experience for a delve only once it is back in town."""

# What the description of `Event`, the base class of every event, becomes: the text before `Examples:`.
_EVENT = """\
The base class of every osrlib event: a frozen record of one thing the rules did.

Use it as the type you annotate with, as in `list[Event]`. The two fields declared here are the two you can
read on any event: `code`, which identifies what happened, and `visibility`, which is who may see it. The
other fields are on each subclass. Check `event_type` or use `isinstance` to find out which subclass you have.

Subclass it to add events of your own, for rules osrlib doesn't cover. Give the subclass structured fields
only, report each outcome as a code rather than a sentence, and give it an `event_type` that differs from every
osrlib event's. `parse_event` reads only osrlib's events and returns `None` for
yours, so read yours back with your class's `model_validate`. Defining a subclass that turns off `frozen`, or
sets `extra` to anything but `"ignore"`, raises `TypeError`. Either one would break two promises osrlib
makes about every event: the event never changes, and osrlib ignores its unknown fields.

Events never change once made, so you can keep a list of them as a log and read it at any time."""

# What the description of the `Visibility` enum becomes: the text before `Examples:`. Every event has a `visibility`,
# so this description is in the schema of every event.
_VISIBILITY = """\
Who may see an event: the players, or only the referee.

Every event has one of these values, because in B/X the referee makes some rolls behind a screen. Filter
a log on it before you show anything to a player, or call
`GameSession.view`, which builds the players' view and the referee's
view for you. A narrator that plays the referee reads every event.

A serialized event contains the lowercase value. The values stay fixed within a `schema_version`, the version
stamp of the saved format."""


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_a_command_description_keeps_its_modes_rejections_and_events(mode: str) -> None:
    assert PurchaseEquipment.model_json_schema(mode=mode)["description"] == _PURCHASE_EQUIPMENT
    definitions = TypeAdapter(AnyCommand).json_schema(mode=mode)["$defs"]
    assert definitions["PurchaseEquipment"]["description"] == _PURCHASE_EQUIPMENT


def test_the_town_description_leaves_out_attributes_raises_and_examples() -> None:
    assert TownSpec.model_json_schema()["description"] == _TOWN_SPEC


def test_the_event_base_description_leaves_out_its_example() -> None:
    assert Event.model_json_schema()["description"] == _EVENT


def test_an_enum_description_leaves_out_its_example() -> None:
    assert TypeAdapter(Visibility).json_schema()["description"] == _VISIBILITY
    assert DamageDealtEvent.model_json_schema()["$defs"]["Visibility"]["description"] == _VISIBILITY


def _model_descriptions(schema: dict) -> dict[str, str]:
    """Return the description of each model and enum in a schema, and of the root, without property descriptions."""
    return {path: text for path, text in _descriptions(schema) if "/properties/" not in path}


@pytest.mark.parametrize("mode", ["validation", "serialization"])
@pytest.mark.parametrize("root", _ROOTS)
def test_a_published_schema_follows_the_rule(root: str, mode: str) -> None:
    schema = TypeAdapter(_ROOTS[root]).json_schema(mode=mode)
    offenders = {path: found for path, text in _model_descriptions(schema).items() if (found := _violations(text))}
    assert offenders == {}


def test_an_openapi_document_follows_the_rule() -> None:
    app = FastAPI()

    @app.post("/commands")
    def execute(command: AnyCommand) -> PlayerView:
        """Run a command and return what the players see."""
        raise NotImplementedError

    schemas = app.openapi()["components"]["schemas"]
    offenders = {
        name: found
        for name, schema in schemas.items()
        if isinstance(text := schema.get("description"), str) and (found := _violations(text))
    }
    assert offenders == {}
    assert schemas["PurchaseEquipment"]["description"] == _PURCHASE_EQUIPMENT


# The edge cases below are models and enums a library user could define. Each docstring is a plain string with escapes,
# because a formatter strips the whitespace that some of them need.


# `Modes:` is a section, because the line after it is indented, but its body is only spaces.
class EmptyKeptSection(_Model):
    "Lead.\n\nModes:\n    \nEvents:\n    The party moves."


# `inspect.cleandoc` measures the indentation from the second line on, so the second line keeps four spaces and the
# third line has none. The second line makes `Attributes:` a section, the third line belongs to it, and the docstring
# has nothing a description keeps. The enum below is built the same way.
class OnlyDroppedSections(_Model):
    "Attributes:\n            value: The value.\n        Set it to 1 or more."

    value: int = 1
    """The value."""


class OnlyDroppedKind(_StrEnum):
    "Examples:\n            ```python\n        OnlyDroppedKind.ONE\n        ```"

    ONE = "one"


class HoldsTheKind(_Model):
    """A model with a field of the enum whose docstring has nothing a description keeps."""

    kind: OnlyDroppedKind = OnlyDroppedKind.ONE
    """The kind."""


def test_a_kept_section_with_an_empty_body_is_left_out() -> None:
    # A label with nothing under it says nothing on the wire, and alone it would read as the start of the next part.
    assert EmptyKeptSection.model_json_schema()["description"] == "Lead.\n\nEvents:\n\nThe party moves."


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_a_model_whose_docstring_reduces_to_nothing_has_no_description(mode: str) -> None:
    # A model with no docstring has no `description` key, so neither does one whose docstring the rule empties.
    assert "description" not in OnlyDroppedSections.model_json_schema(mode=mode)
    assert OnlyDroppedSections.model_json_schema(mode=mode)["properties"]["value"]["description"] == "The value."


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_an_enum_whose_docstring_reduces_to_nothing_has_no_description(mode: str) -> None:
    assert "description" not in TypeAdapter(OnlyDroppedKind).json_schema(mode=mode)
    assert "description" not in HoldsTheKind.model_json_schema(mode=mode)["$defs"]["OnlyDroppedKind"]


# Two or more spaces between two characters that aren't spaces. Indentation at the start of a line, like a list
# item's continuation, doesn't match.
_SPACE_RUN = re.compile(r"\S {2,}\S")

# A code span or a fenced code block, inside which spaces are code.
_CODE = re.compile(r"```.*?```|~~~.*?~~~|``.+?``|`[^`\n]+`", re.DOTALL)


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_no_description_has_a_run_of_spaces_between_words(mode: str) -> None:
    # A paragraph break in a description is a blank line, `"\n\n"`. A viewer that shows each line break as a space
    # shows it as two spaces, but the description itself has a single space between any two words.
    offenders = {
        f"{_name(cls)}{path}": runs
        for cls in [*_MODELS, *_ENUMS]
        for path, text in _descriptions(TypeAdapter(cls).json_schema(mode=mode))
        if (runs := _SPACE_RUN.findall(_CODE.sub("code", text)))
    }
    assert offenders == {}
