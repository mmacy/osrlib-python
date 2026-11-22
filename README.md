# osrlib

osrlib is a Python library that implements the classic 1981 B/X (Basic/Expert) fantasy adventure game rules for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. The rules come from the [Old-School Essentials System Reference Document](https://oldschoolessentials.necroticgnome.com/srd/) (SRD), an Open Game Content restatement of the B/X rules.

osrlib has no UI, networking, or save-file I/O: you pass typed commands into an in-memory session, osrlib resolves the rules and updates game state, and your application handles rendering, player input, persistence, and transport. Every random roll draws from a named stream derived from a master seed, so running the same commands from the same starting state and seed under the same engine version produces the same game state and events every time.

osrlib separates standalone rules in `osrlib.core` (such as rolling characters, resolving attacks, or rolling treasure) from stateful dungeon-crawl sessions in `osrlib.crawl`, with shared top-level modules for compiled SRD data, message formatting, and persistence. In `osrlib.crawl`, you can bundle custom monsters, items, and spells with an adventure, put conditions on doors and stairs (which osrlib calls gates), and define quests and scripted reactions to play events (triggers).

You can use osrlib in several ways:

- **Web or mobile backends**: Serve game sessions over HTTP (for example, with FastAPI) using the generated Pydantic JSON Schemas for commands and events.
- **Terminal or desktop games**: Drive `GameSession` synchronously in process from a local interface.
- **LLM referees and narrators**: Pass structured events to a language model and execute the typed commands it returns.
- **Scripts and simulations**: Call functions in `osrlib.core` directly for character generation, combat statistics, or content validation without starting a session (see [Determinism, saves, and replay](#determinism-saves-and-replay) for a standalone dice roll and [Using the rules without a session](https://mmacy.github.io/osrlib-python/guides/rules-without-a-session/)).

## Installation

osrlib requires Python 3.14 or later, and its only runtime dependency is [pydantic](https://docs.pydantic.dev/). Public APIs follow semantic versioning.

In an existing [uv](https://docs.astral.sh/uv/) project (run `uv init` first in a new directory), install [osrlib from PyPI](https://pypi.org/project/osrlib/):

```sh
uv add osrlib
```

Or install into an active virtual environment with `pip`:

```sh
python -m pip install osrlib
```

## Quickstart

osrlib does not re-export names at the package root, so import each class or function from the module that defines it. Save the example below as `quickstart.py` to roll a party, move one cell east through a dungeon, print the movement message, and round-trip the session through JSON:

```python
import json

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
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
session = GameSession.new(party, adventure, seed=7, ruleset=rules)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING

# Commands in, events out: filter to player-visible events before rendering.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events if event.visibility is Visibility.PLAYER]
print(lines[0])
# The party moves to (1, 0), facing east.

# The whole session round-trips through JSON.
document = save_game(session)
restored = load_game(json.loads(json.dumps(document)))
assert save_game(restored) == document
```

Run `quickstart.py` with `uv`:

```sh
uv run quickstart.py
```

Or run it with the Python interpreter from your activated virtual environment:

```sh
python quickstart.py
```

Running the script prints `The party moves to (1, 0), facing east.` and checks that the restored session serializes identically to the saved JSON. When adapting this snippet, keep these details in mind:

- **Map edges and travel**: Unlisted cell boundaries are walls. Setting `"1,0:west"` to `EdgeKind.OPEN` opens the west wall of `(1, 0)`, which is the shared boundary crossed when moving east from `entrance=(0, 0)`. `"crypt"` in `travel_turns={"crypt": 1}` matches `DungeonSpec(id="crypt", ...)` and `EnterDungeon(dungeon_id="crypt")`, advancing the clock by `1` ten-minute exploration turn on entry.
- **Session streams vs. creation streams**: `GameSession.new(..., seed=7)` creates a fresh set of session RNG streams from `seed`, separate from the `creation` stream used before play. If rolled ability scores miss a class's minimums, `create_character` advances `stream` and raises `ValueError`; retry with the same `stream` instance so the next attempt draws fresh rolls.
- **Player visibility and command rejections**: Filter `result.events` to `Visibility.PLAYER` before showing events to players so referee-only events stay hidden, and render screens from `session.view(Visibility.PLAYER)` rather than directly from `session` attributes. When a rule blocks a command (such as walking into a wall), `result.accepted` is `False`, `result.rejections` lists `Rejection` objects (each with a `code` and `params`), and session state, RNG streams, and the clock remain unchanged.
- **Capturing starting party state for replay**: `GameSession.new` assigns session-scoped IDs to `party.members`. If you plan to audit or replay the run from scratch with `replay_game` later, call `party_to_document(party.members)` (from `osrlib.core.character`) *before* calling `GameSession.new`.

For a step-by-step walkthrough of this program, see the [Quickstart guide](https://mmacy.github.io/osrlib-python/getting-started/quickstart/).

## Determinism, saves, and replay

osrlib never calls Python's `random` module. Every random draw comes from a named PCG64 stream (`RngStream`) managed by `RngStreams`. Calling `streams.get("treasure")` repeatedly on the same `RngStreams` instance returns the same stateful stream, advancing on each roll without shifting other streams such as `"combat"`. Creating a new `RngStreams` with the same `master_seed` reproduces the sequence from the first draw:

```python
from osrlib.core.dice import roll
from osrlib.core.rng import RngStreams

treasure_a = RngStreams(master_seed=42).get("treasure")
rolls_a = [roll("2d6×10", treasure_a).total for _ in range(3)]
print(rolls_a)
# [60, 80, 100]

treasure_b = RngStreams(master_seed=42).get("treasure")
rolls_b = [roll("2d6×10", treasure_b).total for _ in range(3)]
assert rolls_a == rolls_b
```

Choose how you persist a session based on whether you need to continue play or audit a run:

- **To continue playing later, save the session (`save_game` / `load_game`)**: `save_game` serializes a running session (including the embedded adventure, current party, explored dungeon state, RNG stream positions, `command_log`, and `event_log`) to a JSON-compatible dict stamped with `schema_version` and `engine_version`. `load_game` restores that state directly without re-running commands and automatically upgrades saves written under older schema versions. Because saves store JSON data rather than Python objects, re-register any event listeners and custom monster AI policies (`action_policies`) on the restored session before executing new commands.
- **To audit or reproduce a run from scratch, also save the starting inputs (`replay_game`)**: Under the same `engine_version`, `replay_game` starts a fresh session from the master seed, a pre-session party document (captured with `party_to_document` before `GameSession.new` assigns member IDs), the adventure, and the `Ruleset`, then re-executes `command_log`. It reproduces all command-driven state, including commands that listeners issued during original play, but omits private `listener_state` dicts and events that listeners returned directly without executing a command.

## Documentation

Read the [documentation site](https://mmacy.github.io/osrlib-python/) for guides, walkthroughs, and full reference pages for every public symbol, command, event, rejection code, message code, RNG stream, and content ID:

- **Build a dungeon**: [Building an adventure](https://mmacy.github.io/osrlib-python/getting-started/building-an-adventure/) covers laying out a dungeon grid with doors and a keyed encounter, adding features, traps, treasure, and transitions, and validating the adventure.
- **Run the command and event loop**: [Sessions, commands, and events](https://mmacy.github.io/osrlib-python/guides/sessions-commands-events/) covers session modes, command rejections, JSON serialization, and the event log.
- **Add conditional doors, scripted events, and quests**: [Gates, triggers, and quests](https://mmacy.github.io/osrlib-python/guides/gates-triggers-quests/) covers gating doors and transitions, scripting triggers, defining quests, and registering the built-in `Interpreter` listener to advance quests to a `victory` ending.
- **Call rules without a session**: [Using the rules without a session](https://mmacy.github.io/osrlib-python/guides/rules-without-a-session/) covers calling `osrlib.core` functions directly for combat, character creation, spells, and treasure.
- **Connect a UI, HTTP server, or LLM**: [The TUI crawler](https://mmacy.github.io/osrlib-python/front-ends/tui-crawler/) and [The FastAPI pattern](https://mmacy.github.io/osrlib-python/front-ends/fastapi-pattern/) walk through the terminal and HTTP applications in `examples/`, and [LLM referees](https://mmacy.github.io/osrlib-python/front-ends/llm-referees/) shows how to connect an LLM agent to commands and events.
- **Configure B/X rule options and adaptations**: [Ruleset options](https://mmacy.github.io/osrlib-python/guides/ruleset-options/) lists configurable rule flags on `Ruleset`, and the [Adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) documents how osrlib resolves ambiguous B/X rules and tabletop referee procedures.

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

See [the specification](https://github.com/mmacy/osrlib-python/blob/main/docs/spec.md) for the architecture, contracts, and rules scope.

### SRD data pipeline

The compiler in `tools/srd_compile/` parses the scraped SRD markdown in `srd/` and writes the validated JSON data files in `src/osrlib/data/`. Do not edit `srd/` or `src/osrlib/data/` by hand. After changing compiler code or overrides, regenerate the data files and commit the updated files in `src/osrlib/data/` with your change:

```sh
uv run python -m tools.srd_compile
```

CI runs this command and fails if `src/osrlib/data/` has uncommitted changes. Put parser corrections in `tools/srd_compile/overrides/` with a non-empty `reason` field; the compiler applies them during generation and records each modified field path in the entry's `overrides_applied` list.

## Licensing

Library code is licensed under the [MIT license](https://github.com/mmacy/osrlib-python/blob/main/LICENSE). SRD-derived content (the scraped SRD markdown in `srd/` and the compiled game data in `src/osrlib/data/`) is Open Game Content under the [Open Game License 1.0a](https://github.com/mmacy/osrlib-python/blob/main/LICENSE-OGL.md), which includes the complete Section 15 copyright notice. Built wheels include a copy of `LICENSE-OGL.md` with the osrlib Section 15 entry inside `osrlib/data/`.

osrlib is an independent project and is not affiliated with or endorsed by Necrotic Gnome. "Old-School Essentials" is a trademark of Necrotic Gnome, used here only to identify the source document. osrlib makes no claim of compatibility.
