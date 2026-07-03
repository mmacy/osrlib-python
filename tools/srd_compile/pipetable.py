"""Shared parsing helpers: pipe tables, text cleanup, and SRD numeric formats.

The SRD's tables are regular enough that a hand-rolled parser beats a markdown
dependency, but the text has hazards the helpers here normalize: numbers use commas
(`1,050,000`), ranges use en-dashes (`4–5`), prose uses typographic apostrophes
(`5’–80’`), and cells wrap markdown links whose targets are noise.
"""

import re
import unicodedata

# Link targets can themselves contain one level of parentheses (`Dwarf_(Monster)`),
# so the target pattern allows balanced inner parens.
_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_HEADING = re.compile(r"(#{1,6})\s+(.*)")
_SEPARATOR_CELL = re.compile(r":?-{3,}:?")


def strip_links(text: str) -> str:
    """Replace markdown links with their link text."""
    return _LINK.sub(r"\1", text)


def clean_cell(text: str) -> str:
    """Normalize a table cell: strip links and collapse whitespace.

    Emphasis markers are left alone — an asterisk can be data (`9d8+3*`).
    """
    return re.sub(r"\s+", " ", strip_links(text)).strip()


def strip_emphasis(text: str) -> str:
    """Strip bold/italic markers from prose (never call on numeric cells)."""
    return text.replace("***", "").replace("**", "").replace("*", "")


def parse_tables(markdown: str) -> list[list[list[str]]]:
    """Parse every pipe table in a markdown document, in order.

    Returns:
        A list of tables; each table is a list of rows of cleaned cells, with
        separator rows removed. Header rows are kept — the SRD's multi-row spanned
        headers are the caller's business.
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [clean_cell(cell) for cell in stripped.strip("|").split("|")]
            if all(_SEPARATOR_CELL.fullmatch(cell) for cell in cells if cell):
                continue
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def tables_after_heading(markdown: str, heading: str) -> list[list[list[str]]]:
    """Parse the pipe tables that appear after the first heading exactly matching `heading`.

    Exact matching matters: a suffix match would confuse the `Weapons and Armour` page
    title with its `Armour` section.

    Args:
        markdown: The page text.
        heading: The exact heading text to find (any level), after cell cleanup.

    Returns:
        The tables from that point to the end of the document.

    Raises:
        ValueError: If no heading matches.
    """
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match and clean_cell(match[2]) == heading:
            return parse_tables("\n".join(lines[index + 1 :]))
    raise ValueError(f"no heading {heading!r} found")


def section_prose(markdown: str, heading: str) -> str:
    """Return a section's prose: from its heading to the next heading, tables skipped.

    Links are resolved to their text and emphasis markers stripped; bullet markers are
    dropped but bullet text is kept. Paragraphs are joined with single newlines.

    Args:
        markdown: The page text.
        heading: The exact section heading text (any level).

    Returns:
        The cleaned prose.

    Raises:
        ValueError: If the heading is not found or the section has no prose.
    """
    lines = markdown.splitlines()
    collecting = False
    collected: list[str] = []
    for line in lines:
        match = _HEADING.match(line)
        if match:
            if collecting:
                break
            if clean_cell(match[2]) == heading:
                collecting = True
            continue
        if not collecting or line.strip().startswith("|"):
            continue
        text = re.sub(r"\s+", " ", strip_emphasis(strip_links(line))).strip()
        text = re.sub(r"^- ", "", text)
        if text:
            collected.append(text)
    if not collecting:
        raise ValueError(f"section heading {heading!r} not found")
    if not collected:
        raise ValueError(f"section {heading!r} has no prose")
    return "\n".join(collected)


def parse_int(text: str) -> int:
    """Parse an SRD integer, tolerating thousands commas (`1,050,000`)."""
    return int(text.replace(",", ""))


def parse_range(text: str) -> tuple[int, int]:
    """Parse an SRD range: en-dashed (`4–5`) or a single value (`18`)."""
    if "–" in text:
        low, high = text.split("–")
        return parse_int(low.strip()), parse_int(high.strip())
    value = parse_int(text.strip())
    return value, value


def parse_modifier(text: str) -> int:
    """Parse a modifier cell: `None` is 0, otherwise a signed integer (`-3`, `+2`)."""
    if text == "None":
        return 0
    return int(text.replace("+", ""))


def slugify(name: str) -> str:
    """Derive a stable lowercase ASCII snake_case id from an SRD name.

    Diacritics fold to ASCII (Doppelgänger → doppelganger); runs of non-alphanumerics
    become single underscores. IDs appear in saves and are stable forever once
    shipped.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")
    if not slug:
        raise ValueError(f"cannot derive an id from {name!r}")
    return slug
