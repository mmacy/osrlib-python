"""Parser for the SRD's Languages page → `languages.json`.

Compiles Common plus the twenty Other Languages (21 ids referenced by class natives
and INT-granted choices). Alignment tongues are not data entries — they derive from
the alignment enum. `choosable` marks the Other Languages table's entries, available
to characters with high INT.
"""

from pathlib import Path

from .pipetable import slugify, tables_after_heading

SOURCE_PAGE = "Languages.md"


def compile_languages(srd_dir: Path) -> dict[str, object]:
    """Compile the language list into the `languages.json` structure (sans `_meta`).

    Args:
        srd_dir: The directory holding the scraped SRD markdown.

    Returns:
        The raw dict ready for `LanguageCatalog` validation, entries sorted by id.
    """
    page = (srd_dir / SOURCE_PAGE).read_text(encoding="utf-8")
    table = tables_after_heading(page, "Other Languages")[0]
    header_index = next(index for index, row in enumerate(table) if row and row[0] == "d20")
    languages = [{"id": "common", "name": "Common", "choosable": False}]
    for row in table[header_index + 1 :]:
        name = row[1]
        languages.append({"id": slugify(name), "name": name, "choosable": True})
    if len(languages) != 21:
        raise ValueError(f"expected Common plus twenty Other Languages, got {len(languages)}")
    return {"languages": sorted(languages, key=lambda entry: entry["id"])}
