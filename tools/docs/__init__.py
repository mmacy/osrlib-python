"""Documentation build tooling: mkdocs-gen-files page generators and their shared scanners.

Everything in this package runs at site build time (wired up in `mkdocs.yml`) or inside
the test suite; nothing here ships in the osrlib package, and nothing it generates is
checked in. The one hand-maintained input is `rejection_codes.json`, the description
catalog the rejection-code reference merges with the source scan.
"""
