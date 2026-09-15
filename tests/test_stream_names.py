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

import pytest

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


def _string_literals_outside_docstrings(source: str) -> list[str]:
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


@pytest.mark.xfail(reason="chunk: stream-names")
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


@pytest.mark.xfail(reason="chunk: stream-names")
def test_no_module_outside_rng_spells_a_stream_name_out():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path == SRC / "core" / "rng.py":
            continue
        literals = _string_literals_outside_docstrings(path.read_text(encoding="utf-8"))
        offenders.extend(f"{path.relative_to(REPO)}: {literal!r}" for literal in literals if literal in STREAM_KEYS)
    assert offenders == []


@pytest.mark.xfail(reason="chunk: stream-names")
def test_the_stream_page_lists_exactly_the_enum():
    page = (REPO / "docs" / "reference" / "rng-streams.md").read_text(encoding="utf-8")
    listed = set(re.findall(r'^\| `"([a-z_]+)"` \|', page, re.M))
    assert listed == STREAM_KEYS
    assert not re.search(r"\b\d+ named streams\b", page), "the prose carries a surface count"
