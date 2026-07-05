"""Generate the licensing page: the MIT/OGC split, the trademark disclaimer, and the OGL text.

Runs under mkdocs-gen-files at build time. The Open Game License text — including the
complete Section 15 copyright notice with the osrlib compilation entry — is read from the
copy that ships inside the data package, so the site and the wheel can never disagree
about the license the data carries.
"""

from pathlib import Path

import mkdocs_gen_files

import osrlib

_PREAMBLE = """\
# Licensing

osrlib contains two kinds of material under two licenses:

- **Library code** is licensed under the [MIT license](https://github.com/mmacy/osrlib-python/blob/main/LICENSE).
- **SRD-derived content** — the compiled game data that ships inside the package as `osrlib.data`, and the scraped
  SRD text in the repository it is compiled from — is Open Game Content used under the Open Game License 1.0a,
  reproduced below with its complete Section 15 copyright notice. The data package ships its own copy of the
  license inside the built distribution.

The reference pages listing content ids (classes, spells, monsters, equipment, magic items, languages, and
treasure types) are derived from that Open Game Content and carry the same license.

osrlib is an independent project, not affiliated with or endorsed by Necrotic Gnome. "Old-School Essentials" is
a trademark of Necrotic Gnome, used here only to identify the source document; no claim of compatibility is made.

## The Open Game License

The text below is the license file that ships with the compiled data, verbatim.

"""

_license_path = Path(osrlib.__file__).parent / "data" / "LICENSE-OGL.md"
_license_text = _license_path.read_text(encoding="utf-8")

# Demote the license file's own headings so they nest under this page's structure.
_demoted = "\n".join(f"##{line}" if line.startswith("#") else line for line in _license_text.splitlines())

with mkdocs_gen_files.open("licensing.md", "w") as page:
    page.write(_PREAMBLE)
    page.write(_demoted + "\n")
