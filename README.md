# osrlib

osrlib is a Python library that implements the classic 1981 B/X (Basic/Expert) fantasy adventure game rules for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. The rules come from the [Old-School Essentials System Reference Document](https://oldschoolessentials.necroticgnome.com/srd/), an Open Game Content restatement of the B/X rules.

osrlib has no UI, networking, or save-file I/O: you pass typed commands into an in-memory session, osrlib resolves the rules and updates game state, and your application handles rendering, player input, persistence, and transport. Every random roll draws from a named stream derived from a master seed, so running the same commands from the same starting state and seed under the same engine version produces the same game state and events every time.

osrlib separates standalone rules in `osrlib.core` (such as rolling characters, resolving attacks, or rolling treasure) from stateful dungeon-crawl sessions in `osrlib.crawl`, with shared top-level modules for compiled SRD data, message formatting, and persistence. In `osrlib.crawl`, you can bundle custom monsters, items, and spells with an adventure, put conditions on doors and stairs (which osrlib calls gates), define quests and scripted reactions to play events (triggers), and register the built-in `Interpreter` event listener on a session to run triggers and advance quests to a `victory` ending.

You can use osrlib in several ways:

- **Web or mobile backends**: Serve game sessions over HTTP (for example, with FastAPI) using the generated Pydantic JSON Schemas for commands and events.
- **Terminal or desktop games**: Drive `GameSession` synchronously in process from a local interface.
- **LLM referees and narrators**: Pass structured events to a language model and execute the typed commands it returns.
- **Scripts and simulations**: Call functions in `osrlib.core` directly for character generation, combat statistics, or content validation without starting a session.

## Installation

osrlib requires Python 3.14 or later, and its only runtime dependency is [pydantic](https://docs.pydantic.dev/). Public APIs follow semantic versioning. Install [osrlib from PyPI](https://pypi.org/project/osrlib/) with `uv`:

```sh
uv add osrlib
```

Or install with `pip`:

```sh
pip install osrlib
```

## Quickstart

osrlib does not re-export names at the package root, so import each class or function from the module that defines it. The example below rolls two 1st-level characters from a seeded stream, builds an adventure with a town and a two-cell dungeon, starts a `GameSession` in town, enters the dungeon and moves east, formats the resulting events as default English lines, and round-trips the session through `save_game` and `load_game`:

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

When adapting this snippet for an application, keep these behaviors in mind:

- **Player visibility**: `result.events` can include referee-only events alongside player-visible ones. Filter to `Visibility.PLAYER` (from `osrlib.core.events`) before showing events to players, and render screens from `session.view(Visibility.PLAYER)` rather than directly from `session` attributes.
- **Command rejections**: When a rule blocks a command (such as walking into a wall), `result.accepted` is `False`, `result.rejections` lists `Rejection` objects (each with a `code` and `params`), and the session state, RNG streams, and clock remain unchanged.
- **Class minimums during character creation**: If rolled ability scores miss the chosen class's requirements, `create_character` advances `stream` and raises `ValueError`. Retry with the same `stream` instance so the next attempt draws fresh rolls.

For a step-by-step walkthrough of this program, see the [Quickstart guide](https://mmacy.github.io/osrlib-python/getting-started/quickstart/).

## Determinism, saves, and replay

osrlib never calls Python's `random` module. Every random draw comes from a named PCG64 stream (`RngStream`) derived from a master seed by `RngStreams`. Each stream name has an independent sequence, so rolling treasure on `"treasure"` never shifts combat rolls on `"combat"`. Deriving a stream again from the same master seed and stream name reproduces the same sequence of draws:

```python
from osrlib.core.dice import roll
from osrlib.core.rng import RngStreams

streams_a = RngStreams(master_seed=42)
streams_b = RngStreams(master_seed=42)

rolls_a = [roll("2d6×10", streams_a.get("treasure")).total for _ in range(3)]
rolls_b = [roll("2d6×10", streams_b.get("treasure")).total for _ in range(3)]
assert rolls_a == rolls_b  # the same seed and the same key give identical sequences
```

osrlib provides two ways to restore a session:

- **Resume play with `load_game`**: `save_game` serializes a running session (including the embedded adventure, party, explored dungeon state, RNG stream positions, `command_log`, and `event_log`) to a JSON-compatible dict stamped with `schema_version` and `engine_version`. `load_game` restores that state directly without re-running commands and automatically upgrades saves written under older schema versions. Because saves store JSON data rather than Python objects, re-register any listeners (such as `Interpreter`) and custom monster AI policies (`action_policies`) on the restored session before executing new commands.
- **Audit or reproduce runs with `replay_game`**: `replay_game` starts a fresh session from the master seed, a pre-session party document (captured with `party_to_document` in `osrlib.core.character` before `GameSession.new` assigns member IDs, since saves store current rather than starting party state), the adventure, and the `Ruleset`, then re-executes `command_log` under the same `engine_version`. It reproduces all command-driven state, including commands that listeners issued during original play, but omits private `listener_state` dicts and events that listeners returned directly without executing a command.

## Documentation

Read the [documentation site](https://mmacy.github.io/osrlib-python/) for guides, walkthroughs, and full reference pages for every public symbol, command, event, rejection code, message code, RNG stream, and content ID:

- [Building an adventure](https://mmacy.github.io/osrlib-python/getting-started/building-an-adventure/): Lay out a dungeon grid with doors and a keyed encounter, review the models for features, traps, treasure, and transitions, and validate the adventure.
- [Sessions, commands, and events](https://mmacy.github.io/osrlib-python/guides/sessions-commands-events/): Handle session modes, command rejections, JSON serialization, and the event log.
- [Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/): Gate doors and transitions behind conditions, script reactions to play events with triggers, and define quests that end in `victory`.
- [Front-end walk-throughs](https://mmacy.github.io/osrlib-python/front-ends/tui-crawler/) and [LLM referees](https://mmacy.github.io/osrlib-python/front-ends/llm-referees/): Inspect the terminal and FastAPI crawler applications in `examples/`, or connect an LLM agent to commands and events.
- [Adaptations register](https://mmacy.github.io/osrlib-python/adaptations/): Review how osrlib resolves ambiguous B/X rules and tabletop referee procedures, along with the `Ruleset` flags that configure them.

## Contributing

Development requires Python 3.14 or later and [uv](https://docs.astral.sh/uv/). Clone the repository, install the development, documentation, and example dependencies with `uv sync`, and run the CI checks:

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

See [the specification](docs/spec.md) for the architecture, contracts, and rules scope.

### SRD data pipeline

The compiler in `tools/srd_compile/` parses the scraped SRD markdown in `srd/` and writes the validated JSON data files in `src/osrlib/data/`. Do not edit `srd/` or `src/osrlib/data/` by hand. After changing compiler code or overrides, regenerate the data files:

```sh
uv run python -m tools.srd_compile
```

CI runs this command and fails if `src/osrlib/data/` has uncommitted changes. Put parser corrections in `tools/srd_compile/overrides/` with a non-empty `reason` field; the compiler applies them during generation and records each modified field path in the entry's `overrides_applied` list.

## Licensing

Library code is licensed under the [MIT license](LICENSE). SRD-derived content (the scraped SRD markdown in `srd/` and the compiled game data in `src/osrlib/data/`) is Open Game Content under the [Open Game License 1.0a](LICENSE-OGL.md), which includes the complete Section 15 copyright notice. Built wheels include a copy of `LICENSE-OGL.md` with the osrlib Section 15 entry inside `osrlib/data/`.

osrlib is an independent project and is not affiliated with or endorsed by Necrotic Gnome. "Old-School Essentials" is a trademark of Necrotic Gnome, used here only to identify the source document. osrlib makes no claim of compatibility.
