"""Prove the built wheel installs and plays in a clean environment.

Run this with a fresh venv's interpreter after installing the wheel by path into that
venv — never against the repository checkout, and it imports nothing from `tests/` or
`tools/`. It asserts the import resolves under site-packages rather than a shadowing
checkout, that installed metadata and `engine_version()` report the expected version,
that the shipped data loads, that the quickstart path runs end-to-end, and that the
OGL license text ships readable inside the package.

Usage:
    <fresh-venv-python> tools/release/install_smoke.py EXPECTED_VERSION
"""

import argparse
import importlib.metadata
import importlib.resources
import json
from pathlib import Path


def check_resolution(expected_version: str) -> None:
    """Assert osrlib resolves from site-packages at the expected version."""
    import osrlib
    from osrlib.versioning import engine_version

    location = Path(osrlib.__file__).resolve()
    assert "site-packages" in location.parts, f"osrlib resolved outside site-packages: {location}"
    installed = importlib.metadata.version("osrlib")
    assert installed == expected_version, f"installed metadata reports {installed}, expected {expected_version}"
    running = engine_version()
    assert running == expected_version, f"engine_version() reports {running}, expected {expected_version}"
    print(f"ok: osrlib {installed} imported from {location.parent}")


def check_data_ships() -> None:
    """Assert the compiled SRD data ships and the class catalog loads from it."""
    from osrlib.data import load_classes

    fighter = load_classes().get("fighter")
    assert fighter.name, "the fighter class loaded without a name"
    print("ok: the class catalog loads from the shipped data")


def check_quickstart() -> None:
    """Run the README quickstart end-to-end against the installed package."""
    from osrlib.core.alignment import Alignment
    from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
    from osrlib.core.rng import RngStreams
    from osrlib.core.ruleset import Ruleset
    from osrlib.crawl.adventure import Adventure, TownSpec
    from osrlib.crawl.commands import EnterDungeon, MoveParty, SessionMode
    from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
    from osrlib.crawl.party import Party
    from osrlib.crawl.session import GameSession
    from osrlib.messages import format_message
    from osrlib.persistence import load_game, save_game

    rules = Ruleset()
    creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
    fighter = create_character(
        name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation
    )
    cleric = create_character(
        name="Osric", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation
    )
    party = Party(members=[fighter.character, cleric.character])

    crypt = DungeonSpec(
        id="crypt",
        name="The Old Crypt",
        levels=(LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}),),
    )
    town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
    adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

    session = GameSession.new(party, adventure, seed=7)
    session.execute(EnterDungeon(dungeon_id="crypt"))
    assert session.mode is SessionMode.EXPLORING

    result = session.execute(MoveParty(direction=Direction.EAST))
    assert result.accepted, "the quickstart move was rejected"
    lines = [format_message(event) for event in result.events]
    assert lines and all(lines), "an event failed to format"

    document = json.loads(json.dumps(save_game(session)))
    restored = load_game(document)
    assert save_game(restored) == document, "the save/load round-trip diverged"
    print("ok: the quickstart runs end-to-end and round-trips through a save")


def check_ogl_text() -> None:
    """Assert the shipped OGL license text is readable via importlib.resources."""
    text = (importlib.resources.files("osrlib.data") / "LICENSE-OGL.md").read_text(encoding="utf-8")
    assert "Open Game License" in text and "15 COPYRIGHT NOTICE" in text, "the shipped OGL text is not intact"
    print("ok: the shipped OGL text is readable and carries the Section 15 notice")


def main() -> int:
    """Run every smoke check in order.

    Returns:
        0 when the installed wheel passes; assertions abort otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the expected package version, e.g. 1.0.0")
    args = parser.parse_args()

    check_resolution(args.version)
    check_data_ships()
    check_quickstart()
    check_ogl_text()
    print(f"install smoke passed: osrlib {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
