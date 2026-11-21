# osrlib

osrlib implements the 1981 B/X (Basic/Expert) fantasy adventure rules in Python. Use it to build turn-based, grid-based dungeon crawlers in the style of the original Bard's Tale, or to run individual rules in scripts and simulations. The rules come from the [Old-School Essentials System Reference Document](https://oldschoolessentials.necroticgnome.com/srd/), an Open Game Content restatement of B/X.

Your application supplies the interface, player input, and adventure content. osrlib resolves the rules and updates the game state through synchronous Python calls. It doesn't provide a UI, prompt for input, sleep, or make network requests. You can call it from a web or mobile backend, a terminal game, or a large language model (LLM) acting as a referee or narrator.

## Installation

You need Python 3.14 or later. Install [osrlib from PyPI](https://pypi.org/project/osrlib/) in your Python project with uv:

```sh
uv add osrlib
```

If you use pip instead:

```sh
pip install osrlib
```

The installer also installs [pydantic](https://docs.pydantic.dev/), osrlib's only direct runtime dependency.

## Where to start

- **Run a first game:** Follow the [quickstart](getting-started/quickstart.md) to create a party, enter a dungeon, move, read events, and save and restore the session.
- **Write a dungeon:** Follow [Building an adventure](getting-started/building-an-adventure.md) to lay out rooms, corridors, and doors, then add monsters, traps, and treasure.
- **Add game behavior:** Use [Gates, triggers, and quests](guides/gates-triggers-quests.md) to require a key at a door, open a portcullis with a lever, or end an adventure when the party completes a quest.
- **Build an interface:** Start with the [front end walk-throughs](front-ends/tui-crawler.md) for the terminal and HTTP examples, or [LLM referees](front-ends/llm-referees.md) for an agent that sends commands and narrates events.
- **Look up an API:** Use the [reference](reference/api/index.md) for public symbols, command and event JSON Schemas, rejection and message codes, random-number streams, and content ids.
- **Check release changes:** Read the [changelog on GitHub](https://github.com/mmacy/osrlib-python/blob/main/CHANGELOG.md).

## Choose a guide { #what-things-are-called }

Once you have a running session, choose a guide for the task you're working on:

| Task | Guide |
| --- | --- |
| Send commands, handle rejections, and read events | [Sessions, commands, and events](guides/sessions-commands-events.md) |
| Show players only what they're allowed to know, using the player view | [Views and visibility](guides/views-and-visibility.md) |
| Reproduce rolls with named random-number streams | [Determinism, saves, and replay](guides/determinism-saves-replay.md) |
| Store and restore a game as a versioned save document | [Saves](guides/determinism-saves-replay.md#saves) |
| Record player-visible entries in the journal | [Listeners and flags](guides/listeners-and-flags.md#lifecycle-commands-fired-marks-the-journal-and-notes) |
| React to game events with triggers | [Scripting the dungeon with triggers](guides/gates-triggers-quests.md#scripting-the-dungeon-with-triggers) |
| Require an item or another condition at a door or stair with a gate | [Gating a door or a stair](guides/gates-triggers-quests.md#gating-a-door-or-a-stair) |
| Display authored text snippets, called beats, grouped in a narrative block | [Which beat goes where](guides/gates-triggers-quests.md#which-beat-goes-where) |
| End the game in `victory` with a concluding quest | [The completion rule and the ending](guides/gates-triggers-quests.md#the-completion-rule-and-the-ending) |

To reproduce a game, use the same starting party, adventure, ruleset, master seed, accepted commands, and engine version. Register osrlib's interpreter on a session to run the adventure's triggers and track its quests, including a quest that ends the game in victory.

## Licensing

The library code is MIT-licensed. The compiled game data is Open Game Content under the Open Game License 1.0a. See [Licensing](licensing.md) for the license terms and Section 15 notice.
