# osrlib

osrlib is a Python library that implements the classic 1981 B/X (Basic/Expert) fantasy adventure game rules for turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale. The rules come from the [Old-School Essentials System Reference Document](https://oldschoolessentials.necroticgnome.com/srd/), an Open Game Content restatement of the B/X rules.

osrlib runs in memory with no UI or I/O: you pass typed commands in, it resolves the rules and updates game state, and your application handles rendering, player input, storage, and networking. Sessions are deterministic, so executing the same commands from the same master seed under the same engine version produces the same game state and events every time. You can bundle custom monsters, items, and spells with an adventure, gate doors and stairs behind conditions, define triggers and quests on the adventure, and register the built-in [`Interpreter`][osrlib.crawl.interpreter.Interpreter] listener to evaluate triggers and advance quests to a `victory` ending.

You can use osrlib in several ways:

- **Web or mobile backends**: Serve game sessions over HTTP (for example, with FastAPI) using the generated JSON Schemas for commands and events.
- **Terminal or desktop games**: Drive [`GameSession`][osrlib.crawl.session.GameSession] synchronously in process from a local interface.
- **LLM referees and narrators**: Pass structured events to a language model and execute the typed commands it returns.
- **Scripts and simulations**: Call functions in `osrlib.core` directly for balance testing, combat statistics, or content validation without starting a session.

## Installation

osrlib requires Python 3.14 or later, and its only runtime dependency is [pydantic](https://docs.pydantic.dev/). Install [osrlib from PyPI](https://pypi.org/project/osrlib/) with `uv`:

```sh
uv add osrlib
```

Or install with `pip`:

```sh
pip install osrlib
```

## Where to start

- [Quickstart](getting-started/quickstart.md): Roll a party, build a two-cell dungeon, execute commands, format events, and save and load a session.
- [Building an adventure](getting-started/building-an-adventure.md): Lay out a dungeon grid, place doors and transitions, stock keyed areas with encounters, traps, and treasure, and validate the adventure.
- [Gates, triggers, and quests](guides/gates-triggers-quests.md): Gate doors and transitions behind conditions, react to play events with triggers, and define quests that end in `victory`.
- [Guides](guides/sessions-commands-events.md): Work with the command and event loop, player and referee visibility, deterministic saves and replay, standalone `osrlib.core` functions, custom listeners and flags, custom content catalogs, and ruleset options.
- [Front-end walk-throughs](front-ends/tui-crawler.md) and [LLM referees](front-ends/llm-referees.md): Inspect the TUI and FastAPI example applications, or connect an LLM agent to commands and events.
- [Reference](reference/api/index.md): Look up every public symbol, command, event, rejection code, message code, RNG stream, and content ID.
- [Changelog on GitHub](https://github.com/mmacy/osrlib-python/blob/main/CHANGELOG.md): Review release notes for each version.

## What things are called

osrlib uses specific API names for common RPG and game-engine concepts:

| Common concept | osrlib term | Where to learn more |
| --- | --- | --- |
| A quest or adventure log | the journal | [Listeners and flags](guides/listeners-and-flags.md#lifecycle-commands-fired-marks-the-journal-and-notes) |
| A scripted event | a trigger | [Gates, triggers, and quests](guides/gates-triggers-quests.md#scripting-the-dungeon-with-triggers) |
| A door or stair requiring an item or condition | a gate | [Gates, triggers, and quests](guides/gates-triggers-quests.md#gating-a-door-or-a-stair) |
| Narrative text attached to an authored event | beats on a narrative block | [Gates, triggers, and quests](guides/gates-triggers-quests.md#which-beat-goes-where) |
| State safe to show to players | the player view | [Views and visibility](guides/views-and-visibility.md) |
| Seedable random rolls | named streams and draws | [Determinism, saves, and replay](guides/determinism-saves-replay.md) |
| A save file | a stamped save document | [Determinism, saves, and replay](guides/determinism-saves-replay.md#saves) |
| A win condition | a concluding quest and `victory` | [Gates, triggers, and quests](guides/gates-triggers-quests.md#the-completion-rule-and-the-ending) |

## Licensing

Library code is licensed under the MIT license, and compiled game data is Open Game Content under the Open Game License 1.0a. See [Licensing](licensing.md) for full license terms and the Section 15 copyright notice.
