# osrlib

osrlib is a Python library implementing the classic 1981 B/X (Basic/Expert) fantasy adventure game rules for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. The rules are sourced from the [Old-School Essentials System Reference Document](https://oldschoolessentials.necroticgnome.com/srd/), an Open Game Content restatement of the B/X rules.

osrlib is the rules authority and game-state engine; your game supplies presentation, input, and content. The library is headless and sans-I/O — it never renders, prompts, sleeps, or touches the network — and every game it runs is deterministic: the same seed and the same commands always replay the same game.

Four kinds of consumer are first-class:

- **A web or mobile backend** — a FastAPI service serving a crawler over HTTP, with JSON Schema for every command and event
- **A terminal game** — a local TUI crawler driving the engine through synchronous calls
- **An LLM referee or narrator** — an agent that consumes structured events and drives the engine with typed commands
- **Scripts and simulations** — balance testing, mass-combat statistics, and content validation using the kernel à la carte

## Where to start

- The [quickstart](getting-started/quickstart.md) runs the whole loop — characters, party, adventure, session, commands, events, save, and load — in one sitting.
- [Building an adventure](getting-started/building-an-adventure.md) assembles a small dungeon model by model.
- The [guides](guides/sessions-commands-events.md) teach the contracts: sessions and the command/event loop, visibility, determinism, the kernel, listeners, authoring, and ruleset options.
- The [front end walk-throughs](front-ends/tui-crawler.md) tour the two example games that ship in the repository, and the [LLM referee page](front-ends/llm-referees.md) maps the same surface onto an agent.
- The [reference](reference/api/index.md) documents every public symbol, command, event, rejection code, message code, RNG stream, and content id.

## Installation

Install [osrlib from PyPI](https://pypi.org/project/osrlib/). The library requires Python ≥ 3.14 and its only runtime dependency is [pydantic](https://docs.pydantic.dev/).

```sh
uv add osrlib
```

or, with pip:

```sh
pip install osrlib
```

## Licensing

Library code is MIT-licensed; the compiled game data is Open Game Content under the Open Game License 1.0a. The [licensing page](licensing.md) has the full split and the Section 15 notice.
