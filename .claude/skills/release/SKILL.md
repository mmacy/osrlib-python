---
name: release
description: Cut an osrlib release — version bump, changelog, the annotated tag that drives release.yml, the local dry run before tagging, and recovery when a release fails. Use when cutting a release, bumping the version, or working on release.yml.
---

# Releasing osrlib

- The version lives in `pyproject.toml` alone; `osrlib.versioning.engine_version()` reads installed metadata at runtime. The bump procedure is: edit the version, run `uv lock`, and nothing else. The golden comparisons normalize the `engine_version` stamp, so **regenerating a golden file for a version bump is a defect by definition** — the goldens keep the stamp of the engine that produced them.
- A PR that changes user-visible behavior adds its bullet to the `[Unreleased]` section of `CHANGELOG.md` in the same PR. A release renames that section to the version and date.
- A release is an annotated `vX.Y.Z` tag on the merge commit (`git tag -a vX.Y.Z -m "osrlib X.Y.Z"`, then push the tag). `release.yml` does the rest: fails fast if the tag doesn't match the pyproject version, re-runs the full standing gate, builds once, audits the artifact with `tools/release/check_dist.py`, smoke-tests the wheel in a fresh venv on both OSes with `tools/release/install_smoke.py`, publishes to PyPI via trusted publishing (no tokens anywhere in the repository), and creates the GitHub Release from the tagged version's changelog section.
- The local dry run before tagging: `uv build`, then `uv run python tools/release/check_dist.py dist X.Y.Z`, then install the wheel into a fresh venv and run `tools/release/install_smoke.py X.Y.Z` with that venv's interpreter.
- Recovery: any failure before the publish job leaves PyPI untouched — delete the tag, fix on a branch, re-tag. Once publish succeeds, that version's filenames are burned on PyPI and the next attempt is a new version.
- Versioned documentation is not adopted; Pages-from-`main` is the whole deployment. The adoption trigger: the first post-1.0 release whose published docs must describe behavior different from `main` — practically, the first minor or major with behavior or API changes — adopts mike or equivalent in that release's own plan. Patch and docs-only releases do not trigger it.
