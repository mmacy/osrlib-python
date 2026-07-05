# osrlib

A Python library implementing the classic 1981 B/X (Basic/Expert) fantasy adventure game rules for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. The rules are sourced from the [Old-School Essentials System Reference Document](https://oldschoolessentials.necroticgnome.com/srd/), an Open Game Content restatement of the B/X rules. osrlib is the rules authority and game-state engine; your game supplies presentation, input, and content.

The library is headless and sans-I/O — it never renders, prompts, sleeps, or touches the network — and every game it runs is deterministic: the same seed and the same commands always replay the same game. Four kinds of consumer are first-class: a web or mobile backend (FastAPI over HTTP), a terminal game (a local TUI crawler), an LLM referee or narrator driven by structured events and typed commands, and scripts or simulations using the kernel à la carte.

**Status:** released — [osrlib on PyPI](https://pypi.org/project/osrlib/). The public API is frozen, and the [documentation site](https://mmacy.github.io/osrlib-python/) is the place to learn the library — quickstart, guides, front-end walk-throughs, and a full reference for every command, event, rejection code, and content id.

## Installation

Requires Python ≥ 3.14. The only runtime dependency is [pydantic](https://docs.pydantic.dev/).

```sh
uv add osrlib
```

or, with pip:

```sh
pip install osrlib
```

## Quickstart

```python
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

# Roll two 1st-level characters; every random draw comes from a named, seeded stream.
rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
cleric = create_character(name="Osric", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character, cleric.character])

# The smallest adventure: a town and a one-corridor dungeon, two cells joined west-east.
crypt = DungeonSpec(
    id="crypt",
    name="The Old Crypt",
    levels=(LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}),),
)
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

# A session starts in town; entering the dungeon switches it to exploring.
session = GameSession.new(party, adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING

# Commands in, events out: every rules resolution is a typed event with a message code.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every event formats to a default English line

# The whole session round-trips through JSON: same seed, same commands, same game.
document = save_game(session)
restored = load_game(document)
assert save_game(restored) == document
```

The [documentation site](https://mmacy.github.io/osrlib-python/) walks this example step by step, then builds out from it: [building an adventure](https://mmacy.github.io/osrlib-python/getting-started/building-an-adventure/), the [session and event loop](https://mmacy.github.io/osrlib-python/guides/sessions-commands-events/), and complete [front-end walk-throughs](https://mmacy.github.io/osrlib-python/front-ends/tui-crawler/) for the two example games in `examples/`.

## Determinism

Determinism is a public API guarantee. All randomness flows through named PCG64 streams forked from a master seed, so the same seed and the same key always produce the same stream — independently of any other stream:

```python
from osrlib.core.dice import roll
from osrlib.core.rng import RngStreams

streams_a = RngStreams(master_seed=42)
streams_b = RngStreams(master_seed=42)

rolls_a = [roll("2d6×10", streams_a.get("treasure")).total for _ in range(3)]
rolls_b = [roll("2d6×10", streams_b.get("treasure")).total for _ in range(3)]
assert rolls_a == rolls_b  # same seed + same key → identical sequences
```

Successive rolls on one stream differ, of course; reproducibility across derivations is the contract. Saved games replay from the seed and the command log, so a loaded game is bit-for-bit the game you saved.

## SRD data pipeline

The game data in `src/osrlib/data/` is generated from the scraped SRD markdown in `srd/` and is never hand-edited. Regenerate it with:

```sh
uv run python -m tools.srd_compile
```

CI regenerates the data and fails on any diff, so `srd/`, the compiler, and the generated data cannot silently drift. Parser corrections belong in `tools/srd_compile/overrides/`, never in the output; every override carries a reason and is recorded in the output entry's `overrides_applied` provenance list. Rules interpretations and adaptations are documented in the [adaptations register](https://mmacy.github.io/osrlib-python/adaptations/).

## Contributing

Requires Python ≥ 3.14 and [uv](https://docs.astral.sh/uv/). Install from source and run the checks the way CI does:

```sh
git clone https://github.com/mmacy/osrlib-python.git
cd osrlib-python
uv sync
uv run ruff format --check
uv run ruff check
uv run pyright
uv run pytest
uv run mkdocs build --strict
```

The design is documented in [the specification](docs/spec.md): architecture, contracts, rules scope, and the phased roadmap.

## Licensing

This repository contains two kinds of material under two licenses:

- **Library code** is licensed under the [MIT license](LICENSE).
- **SRD-derived content** — the scraped SRD text in `srd/` and the compiled game data in `src/osrlib/data/` — is Open Game Content used under the [Open Game License 1.0a](LICENSE-OGL.md), which includes the complete Section 15 copyright notice. The data package ships its own copy of the license, with the osrlib Section 15 entry, inside the built wheel.

osrlib is an independent project, not affiliated with or endorsed by Necrotic Gnome. "Old-School Essentials" is a trademark of Necrotic Gnome, used here only to identify the source document; no claim of compatibility is made.
