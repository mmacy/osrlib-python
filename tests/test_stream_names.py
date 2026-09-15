"""Every RNG stream name has one home: `StreamName` in `osrlib.core.rng`.

The thirteen stream keys are split today between eight `*_STREAM` constants in the kernel and five in
`crawl/session.py`, and kernel functions draw from crawl-named streams by string literal, so the rule
that the kernel never imports the crawl layer holds only because a string is not an import. With one
`StrEnum` in `core/rng.py`, the constants keep their names and take their values from it, no module
spells a stream name out, and the published stream page cannot list a key the enum lacks. The string
values do not change, so no draw sequence and no golden file moves.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "osrlib"

STREAM_KEYS = {
    "character_creation",
    "advancement",
    "combat",
    "effects",
    "monster_spawn",
    "npc_party",
    "magic",
    "treasure",
    "wandering",
    "encounter",
    "exploration",
    "monster_action",
    "adjudication",
}


def _is_streams(receiver: ast.expr) -> bool:
    """Whether a `.get(...)` receiver is a stream container: `streams`, `self.streams`, or `session.streams`."""
    return (isinstance(receiver, ast.Name) and receiver.id == "streams") or (
        isinstance(receiver, ast.Attribute) and receiver.attr == "streams"
    )


def _stream_key_literals(source: str) -> list[str]:
    """Every string literal used as a stream key: passed to a streams `.get(...)` call or bound to a `*_STREAM` name."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_streams(node.func.value)
        ):
            for argument in node.args[:1]:
                if isinstance(argument, ast.Constant) and argument.value in STREAM_KEYS:
                    found.append(argument.value)
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if (
                isinstance(value, ast.Constant)
                and value.value in STREAM_KEYS
                and any(isinstance(target, ast.Name) and target.id.endswith("_STREAM") for target in targets)
            ):
                found.append(value.value)
    return found


def test_the_enum_names_every_stream_and_the_constants_take_their_values_from_it():
    from enum import StrEnum

    from osrlib.core import character, combat, effects, monsters, npc, spells, treasure
    from osrlib.core.rng import StreamName
    from osrlib.crawl import session

    assert issubclass(StreamName, StrEnum)
    assert {member.value for member in StreamName} == STREAM_KEYS
    constants = {
        character.CHARACTER_CREATION_STREAM: "character_creation",
        character.ADVANCEMENT_STREAM: "advancement",
        combat.COMBAT_STREAM: "combat",
        effects.EFFECTS_STREAM: "effects",
        monsters.MONSTER_SPAWN_STREAM: "monster_spawn",
        npc.NPC_PARTY_STREAM: "npc_party",
        spells.MAGIC_STREAM: "magic",
        treasure.TREASURE_STREAM: "treasure",
        session.WANDERING_STREAM: "wandering",
        session.ENCOUNTER_STREAM: "encounter",
        session.EXPLORATION_STREAM: "exploration",
        session.MONSTER_ACTION_STREAM: "monster_action",
        session.ADJUDICATION_STREAM: "adjudication",
    }
    for constant, key in constants.items():
        assert isinstance(constant, StreamName)
        assert constant == key


def test_no_module_outside_rng_uses_a_stream_name_literal():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "core" / "rng.py":
            continue
        literals = _stream_key_literals(path.read_text(encoding="utf-8"))
        offenders.extend(f"{path.relative_to(REPO)}: {literal!r}" for literal in literals)
    assert offenders == []


def test_the_stream_page_lists_exactly_the_enum():
    page = (REPO / "docs" / "reference" / "rng-streams.md").read_text(encoding="utf-8")
    listed = set(re.findall(r'^\| `"([a-z_]+)"` \|', page, re.M))
    assert listed == STREAM_KEYS
    assert not re.search(r"\b\d+ named streams\b", page), "the prose carries a surface count"
