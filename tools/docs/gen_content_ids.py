"""Generate the content-id indexes from the data loaders.

Runs under mkdocs-gen-files at build time. One page per catalog — classes, spells,
monsters, equipment, magic items, languages, and treasure types — listing identification
columns only (ids and display names, plus the odd classifying column), never republished
stat blocks. Because the pages come from the loaders, an id that validates in play is an
id that appears here.

These indexes are SRD-derived Open Game Content, so every page carries the OGC marking.
"""

import mkdocs_gen_files

from osrlib import data

_OGC_NOTE = (
    "\nThe identifiers and names on this page are derived from the Old-School Essentials "
    "System Reference Document and are **Open Game Content** under the Open Game License "
    "1.0a. See [Licensing](../../licensing.md) for the full notice.\n"
)


def _table(page, header: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    page.write("| " + " | ".join(header) + " |\n")
    page.write("|" + "---|" * len(header) + "\n")
    for row in rows:
        page.write("| " + " | ".join(row) + " |\n")
    page.write("\n")


_PAGES: list[tuple[str, str, str]] = []  # (filename, title, loader description) for the index


def _page(filename: str, title: str, loader_ref: str, intro: str):
    _PAGES.append((filename, title, loader_ref))
    page = mkdocs_gen_files.open(f"reference/content-ids/{filename}", "w")
    # The explicit heading id makes the page an autorefs target, so docstrings can
    # link id-typed parameters here with [...][<name>-index].
    anchor = filename.removesuffix(".md")
    page.write(f"# {title} {{ #{anchor}-index }}\n\n{intro} Loaded by {loader_ref}.\n")
    page.write(_OGC_NOTE + "\n")
    return page


with _page(
    "classes.md",
    "Class ids",
    "[`load_classes`][osrlib.data.load_classes]",
    "Every character class id the engine accepts, with its display name.",
) as page:
    _table(page, ("Id", "Name"), [(f"`{c.id}`", c.name) for c in data.load_classes().classes])

with _page(
    "spells.md",
    "Spell ids",
    "[`load_spells`][osrlib.data.load_spells]",
    "Every spell id, with its display name, spell list, and level.",
) as page:
    _table(
        page,
        ("Id", "Name", "List", "Level"),
        [(f"`{s.id}`", s.name, f"`{s.spell_list}`", str(s.level)) for s in data.load_spells().spells],
    )

with _page(
    "monsters.md",
    "Monster ids",
    "[`load_monsters`][osrlib.data.load_monsters]",
    "Every monster template id, with its display name.",
) as page:
    _table(page, ("Id", "Name"), [(f"`{m.id}`", m.name) for m in data.load_monsters().monsters])

with _page(
    "equipment.md",
    "Equipment ids",
    "[`load_equipment`][osrlib.data.load_equipment]",
    "Every mundane equipment id — weapons, armour, gear, and ammunition — with its display name.",
) as page:
    catalog = data.load_equipment()
    for section_title, entries in (
        ("Weapons", catalog.weapons),
        ("Armour", catalog.armour),
        ("Gear", catalog.gear),
        ("Ammunition", catalog.ammunition),
    ):
        page.write(f"## {section_title}\n\n")
        _table(page, ("Id", "Name"), [(f"`{e.id}`", e.name) for e in entries])

with _page(
    "magic-items.md",
    "Magic item ids",
    "[`load_magic_items`][osrlib.data.load_magic_items]",
    "Every magic item template id, with its display name and category.",
) as page:
    _table(
        page,
        ("Id", "Name", "Category"),
        [(f"`{i.id}`", i.name, f"`{i.category}`") for i in data.load_magic_items().items],
    )

with _page(
    "languages.md",
    "Language ids",
    "[`load_languages`][osrlib.data.load_languages]",
    "Every language id, with its display name and whether a high-INT character may choose it.",
) as page:
    _table(
        page,
        ("Id", "Name", "Choosable"),
        [(f"`{lang.id}`", lang.name, "yes" if lang.choosable else "no") for lang in data.load_languages().languages],
    )

with _page(
    "treasure-types.md",
    "Treasure types",
    "[`load_treasure_tables`][osrlib.data.load_treasure_tables]",
    "The treasure type letters the monster listings reference, with each type's kind (a lair hoard or carried loot).",
) as page:
    _table(
        page,
        ("Type", "Kind"),
        [(f"`{t.letter}`", t.kind.value) for t in data.load_treasure_tables().treasure_types],
    )

with mkdocs_gen_files.open("reference/content-ids/index.md", "w") as index:
    index.write("# Content ids\n\n")
    index.write(
        "The compiled rules content is addressed by id everywhere in the API — commands take "
        "ids, events carry ids, and the loaders validate them. These indexes list every id that "
        "ships, one page per catalog:\n\n"
    )
    for filename, title, loader_ref in _PAGES:
        index.write(f"- [{title}]({filename}) — {loader_ref}\n")
    index.write(_OGC_NOTE)

with mkdocs_gen_files.open("reference/content-ids/SUMMARY.md", "w") as summary:
    summary.write("- [Overview](index.md)\n")
    for filename, title, _loader_ref in _PAGES:
        summary.write(f"- [{title}]({filename})\n")
