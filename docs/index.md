# osrlib

osrlib is a Python library that implements the classic 1981 B/X (Basic/Expert) fantasy adventure game rules for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. The rules come from the [Old-School Essentials System Reference Document](https://oldschoolessentials.necroticgnome.com/srd/), an Open Game Content restatement of the B/X rules.

osrlib is the rules authority and game-state engine. The game you build supplies presentation, input, and content. The library is headless and sans-I/O: it never renders, prompts, sleeps, or touches the network. Every game it runs is deterministic. The same seed and the same commands always replay the same game. An adventure defines its own content and behavior (bundled items, gated doors, triggers, and quests), and the library includes the interpreter that plays the adventure through to a victory ending.

osrlib is designed for four kinds of consumer:

- **A web or mobile backend** - a FastAPI service that serves a crawler over HTTP, with JSON Schema for every command and event.
- **A terminal game** - a local TUI crawler that drives the engine through synchronous calls.
- **An LLM referee or narrator** - an agent that consumes structured events and drives the engine with typed commands.
- **Scripts and simulations** - balance testing, mass-combat statistics, and content validation, which call the rules kernel with no session at all.

## Where to start

- The [quickstart](getting-started/quickstart.md) runs the whole loop in one sitting: characters, party, adventure, session, commands, events, save, and load.
- [Building an adventure](getting-started/building-an-adventure.md) teaches the dungeon itself: the grid and its edges, keyed areas, and the content each area binds.
- [Gates, triggers, and quests](guides/gates-triggers-quests.md) adds the authored behavior: a door that opens only with the right key, a lever that opens a portcullis, and a quest that ends the adventure in victory.
- The [guides](guides/sessions-commands-events.md) teach the contracts: sessions and the command/event loop, visibility, determinism, the rules without a session, listeners, authoring, and ruleset options.
- The [front end walk-throughs](front-ends/tui-crawler.md) tour the two example games that ship in the repository, and the [LLM referee page](front-ends/llm-referees.md) maps the same surface onto an agent.
- The [reference](reference/api/index.md) documents every public symbol, command, event, rejection code, message code, RNG stream, and content id.
- The [changelog on GitHub](https://github.com/mmacy/osrlib-python/blob/main/CHANGELOG.md) records what each release changed.

## What things are called

The project's vocabulary maps one-to-one onto the API names, so learn it early. The name you may already know locates the concept, and the linked page teaches osrlib's term:

| You may know it as | osrlib calls it | Taught in |
| --- | --- | --- |
| A quest log | the journal | [Listeners and flags](guides/listeners-and-flags.md#lifecycle-commands-fired-marks-the-journal-and-notes) |
| A scripted event | a trigger | [Gates, triggers, and quests](guides/gates-triggers-quests.md#wiring-the-dungeon-with-triggers) |
| A locked door that opens only with the right item | a gate | [Gates, triggers, and quests](guides/gates-triggers-quests.md#gating-a-door-or-a-stair) |
| The text that comes with an event | beats on a narrative block | [Gates, triggers, and quests](guides/gates-triggers-quests.md#which-beat-goes-where) |
| What the player is allowed to see | the player view | [Views and visibility](guides/views-and-visibility.md) |
| Seedable randomness | named streams and draws | [Determinism, saves, and replay](guides/determinism-saves-replay.md) |
| A save file | a stamped document | [Determinism, saves, and replay](guides/determinism-saves-replay.md#saves) |
| A win condition | a concluding quest and `victory` | [Gates, triggers, and quests](guides/gates-triggers-quests.md#the-completion-rule-and-the-ending) |

## Installation

Install [osrlib from PyPI](https://pypi.org/project/osrlib/). You'll need Python ≥ 3.14. The only runtime dependency is [pydantic](https://docs.pydantic.dev/).

```sh
uv add osrlib
```

or, with pip:

```sh
pip install osrlib
```

## Licensing

Library code is MIT-licensed. The compiled game data is Open Game Content under the Open Game License 1.0a. The [licensing page](licensing.md) has the full split and the Section 15 notice.
