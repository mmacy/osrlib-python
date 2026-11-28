"""Every line of a spell page's text is in the compiled spell (issue #342).

Up to 2.11.0 the SRD compiler dropped the text that a page prints after its numbered usages, such as the
**Restrictions** line of *hold monster* and the stipulations of *raise dead*, and it dropped every heading, pipe
table, and stat line in a page's body. A client that shows a spell's text couldn't show the whole page. These
tests read each page under `srd/` and check the compiled entry in `src/osrlib/data/spells.json` against it.
"""

import json
import re
from pathlib import Path

import pytest

from osrlib.core.spells import SpellTemplate
from osrlib.data import load_spells

REPO = Path(__file__).resolve().parent.parent
SRD = REPO / "srd"
SPELLS_JSON = REPO / "src" / "osrlib" / "data" / "spells.json"
LIST_PAGES = {"Cleric_Spells.md", "Magic-User_Spells.md"}

_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")  # a link target can hold one pair of parentheses
_LEVEL_LINE = re.compile(r"\*\d(?:st|nd|rd|th) Level \[(Cleric|Magic-User) Spell\]")
_LIST_IDS = {"Cleric": "cleric", "Magic-User": "magic_user"}


def _normalize(text: str) -> str:
    """Drop link targets, emphasis markers, and runs of whitespace, so page lines and compiled text compare."""
    return re.sub(r"\s+", " ", _LINK.sub(r"\1", text).replace("*", "")).strip()


def _spell_pages() -> list[str]:
    """The spell pages the compiler read, from the provenance block of the compiled data."""
    data = json.loads(SPELLS_JSON.read_text(encoding="utf-8"))
    return [page for page in data["_meta"]["source_pages"] if page not in LIST_PAGES]


def _spell_for(page: str, text: str) -> SpellTemplate:
    """Find a page's compiled spell by its printed name and the class list its level line names."""
    title = text.splitlines()[0].lstrip("# ").strip()
    title = re.sub(r"\s*\((?:C|MU)\)$", "", title)
    if "\n## Reversed:" in text:
        title = re.sub(r"\s*\([^)]*\)$", "", title)
    level = _LEVEL_LINE.search(text)
    assert level is not None, page
    matches = [
        spell for spell in load_spells().spells if spell.name == title and spell.spell_list == _LIST_IDS[level[1]]
    ]
    assert len(matches) == 1, (page, title)
    return matches[0]


def _page_lines(text: str) -> list[str]:
    """The text lines of a page's body: everything below the range line but the contents list and rules."""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("**Range:**")) + 1
    kept = []
    in_contents = False
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("# ").strip()
            in_contents = heading == "Contents"
            if in_contents or heading.startswith("Reversed: "):
                continue
            kept.append(heading)
            continue
        if in_contents or not stripped or stripped == "---":
            continue
        kept.append(re.sub(r"^(?:- |\d+\. )", "", stripped))
    return [_normalize(line) for line in kept]


_USAGE_NUMBER = re.compile(r"^\d+\. ")


def _texts(spell: SpellTemplate) -> list[str]:
    """Every text value of a compiled spell: its intro, each mode's prose, and the reverse's prose and modes."""
    texts = [spell.intro, *(mode.prose for mode in spell.modes)]
    if spell.reversed_form is not None:
        texts += [spell.reversed_form.prose, *(mode.prose for mode in spell.reversed_form.modes)]
    return texts


def _compiled_lines(spell: SpellTemplate) -> set[str]:
    """The compiled spell's lines, without the usage number that a manual mode's numbered line keeps."""
    return {_USAGE_NUMBER.sub("", _normalize(line)) for text in _texts(spell) for line in text.splitlines()}


@pytest.mark.parametrize("page", _spell_pages())
def test_every_line_of_the_page_is_in_the_compiled_spell(page):
    """Each page line is a whole line of the compiled spell, so a dropped heading or table row fails.

    The forms whose passage the compiler divides among their modes still have each whole line somewhere: the
    reverses of *light* and *continual light* in `ReversedForm.prose`, *silence 15' radius* in its `creature`
    mode, and *charm monster*'s divided first line in its `intro`. So no line needs a substring match.
    """
    text = (SRD / page).read_text(encoding="utf-8")
    spell = _spell_for(page, text)
    lines = _compiled_lines(spell)
    missing = [line for line in _page_lines(text) if line not in lines]
    assert missing == [], page


_DELIMITER_ROW = re.compile(r"^\|(?: ?:?-{3,}:? ?\|)+$")


def _cells(row: str) -> int:
    return row.count("|") - 1


def _format_problems(text: str) -> list[str]:
    """Check one value against the format `SpellMode.prose` states, and return what breaks it."""
    problems = []
    lines = text.split("\n")
    for line in lines:
        if not line.strip():
            problems.append("an empty line")
        elif line != line.strip():
            problems.append(f"spaces around {line!r}")
        if "**" in line or line.startswith("#") or _LINK.search(line):
            problems.append(f"markup in {line!r}")
    index = 0
    while index < len(lines):
        if not lines[index].startswith("|"):
            index += 1
            continue
        end = index
        while end < len(lines) and lines[end].startswith("|"):
            end += 1
        table = lines[index:end]
        if len(table) < 2 or not _DELIMITER_ROW.match(table[1]):
            problems.append(f"a table with no delimiter row second: {table[:2]}")
        if any(_DELIMITER_ROW.match(row) for row in (table[:1] + table[2:])):
            problems.append(f"a delimiter row out of place: {table}")
        if len({_cells(row) for row in table}) != 1 or not all(row.endswith("|") for row in table):
            problems.append(f"rows with different cell counts: {table}")
        index = end
    return problems


@pytest.mark.parametrize("spell", load_spells().spells, ids=lambda spell: spell.id)
def test_every_text_value_follows_the_prose_format(spell):
    """The format `SpellMode.prose` states: lines with no markup, and a table as a header row, a delimiter row, rows."""
    problems = {index: _format_problems(text) for index, text in enumerate(_texts(spell)) if text}
    assert {index: found for index, found in problems.items() if found} == {}
    assert "\n" not in spell.intro and not spell.intro.startswith("|")


def _prose(spell_id: str, key: str) -> str:
    return load_spells().get(spell_id).mode(key).prose


@pytest.mark.parametrize("spell_id", ["hold_person_c", "hold_person_mu", "hold_monster"])
@pytest.mark.parametrize("key", ["individual", "group"])
def test_both_hold_modes_carry_the_restrictions(spell_id, key):
    prose = _prose(spell_id, key)
    assert prose.splitlines()[-1].startswith("Restrictions: Undead")
    assert prose.splitlines()[0].startswith({"individual": "Against an individual:", "group": "Against a group:"}[key])


def test_sleep_gives_its_hd_rules_to_the_hd_budget_mode_and_its_last_rules_to_both():
    single, budget = _prose("sleep", "single_4_plus"), _prose("sleep", "hd_budget")
    last_rules = [
        "Killing: A single attack with a bladed weapon can kill a creature enchanted by this spell.",
        "Awakening: Enchanted creatures can be forcefully awakened (e.g. by slapping).",
    ]
    assert single.splitlines()[1:] == last_rules
    budget_lines = budget.splitlines()
    assert budget_lines[1] == "When targeting creatures of 4 HD or less, the following rules apply:"
    assert budget_lines[2] == "Weakest first: Targets with the least HD are affected first."
    assert budget_lines[3].startswith("HD: Treat monsters with less than 1 HD as having 1 HD")
    assert budget_lines[4:] == last_rules


def test_raise_dead_gives_its_stipulations_to_restore_life_alone():
    restore, destroy = _prose("raise_dead", "restore_life"), _prose("raise_dead", "destroy_undead")
    lines = restore.splitlines()
    assert lines[0] == "Restore life: To a recently deceased human or demihuman. See below."
    assert lines[1] == "Restoring life: When used to restore life, the following stipulations apply:"
    assert lines[2].startswith("Time limit: The caster can raise a person")
    assert lines[3].startswith("Weakness: Returning from death is an ordeal.")
    assert len(lines) == 4
    assert "\n" not in destroy and destroy.startswith("Destroy undead:")


def test_a_table_is_kept_as_its_rows():
    prose = _prose("confusion", "confuse")
    assert prose.splitlines()[-5:] == [
        "| 2d6 | Behaviour |",
        "| --- | --- |",
        "| 2-5 | Attack caster’s group |",
        "| 6-8 | No action |",
        "| 9-12 | Attack subject’s group |",
    ]


def test_a_stat_line_keeps_its_special_ability_asterisk():
    prose = _prose("control_weather", "cast")
    assert "Tornado" in prose.splitlines()
    assert "AC 0 [19], HD 12* (54hp), Att 1 × blow (2d8)" in prose
