# The TUI crawler

The barrow crawler is a complete, playable game built on osrlib and nothing else — no
curses, no Textual, no web framework, just `input()`, `print()`, and the standard
library. It exists to make one claim concrete: everything a session needs to run —
rules, dice, state, the event log — lives in the library, and everything a front end
supplies — rendering, input handling, authored content, even a whole quest — is
ordinary application code written against the public surface. The same
[`GameSession`][osrlib.crawl.session.GameSession] this example drives could sit behind
a web API or a graphical client instead; nothing about it assumes a terminal.

This page walks that split section by section, excerpting the crawler's real source.
For the commands the game understands and how to run it yourself, see the example's
own [README on GitHub](https://github.com/mmacy/osrlib-python/tree/main/examples/tui_crawler)
— one command starts an interactive game: `uv run python -m examples.tui_crawler`.

## Reading commands, rendering events

The crawler's loop is a `dispatch` function that turns one line of typed text into a
command, and a `run` helper that executes it and prints whatever comes back. Parsing
is entirely the game's problem — the library has no idea `"move e"` is a sentence:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:parse-command"
```

`_DIRECTIONS` maps single letters to the compass words
[`MoveParty`][osrlib.crawl.commands.MoveParty] expects. Once a command exists,
running it is the same three steps as everywhere else in osrlib — execute, check
acceptance, format the events — with one addition: the crawler prints the *delta* of
the session's event log rather than just the result's own events, so a quest
listener's reactions (nested commands it executes on the game's behalf) show up in
the transcript too:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:render-events"
```

Every event carries a [`Visibility`][osrlib.core.events.Visibility]; filtering on
`Visibility.PLAYER` here is what keeps referee-only bookkeeping out of the player's
terminal. Running the milestone transcript (`--seed 203 --script
examples/tui_crawler/scripts/milestone.txt`) opens like this:

```text
> enter
  The party enters dungeon barrow (level 1).
> move e
  The party moves to (1, 0), facing east.
> move e
  The party moves to (2, 0), facing east.
  The party enters area guard_room (level 1).
  Encounter: 2 × Goblin at 50' — the party is surprised.
  The monsters' bearing: uncertain.
  The monsters' bearing: hostile.
```

Every printed line is [`format_message`][osrlib.messages.format_message] rendering a
typed event — a different front end could format the same events into JSON, a chat
message, or nothing at all (see [the message code reference](../reference/message-codes.md)).

## The player's view

The event-level `Visibility` check above hides individual referee-only lines. The
crawler's status command takes a coarser approach: it asks the session for a whole
snapshot built for players, rather than reaching into referee-only state itself:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:player-view"
```

[`GameSession.view`][osrlib.crawl.session.GameSession.view] returns a frozen
`PlayerView` when called with `Visibility.PLAYER` — hit points, gold, and carried
valuables, and nothing a referee-only view would add. The crawler never touches
`session.party` or `session.monsters` directly to render status; it renders the same
view any other front end would get by asking for one. [Views and visibility](../guides/views-and-visibility.md)
covers what a `PlayerView` includes and how it differs from the referee's.

## The authored adventure

`content.py` builds the game's whole world: a town and a two-level barrow, assembled
from the same authoring models [Building an adventure](../getting-started/building-an-adventure.md)
walks through. A keyed area binds descriptive text, an encounter, and a feature to a
set of cells — here, the shrine room holding the quest's MacGuffin, a named valuable
tucked inside a treasure cache:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:idol-shrine-area"
```

Level 1 also keys a goblin-guarded guard room, but level 2 keys no monsters at all —
its only area is an unguarded vault. Instead, level 2's
[`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec] overrides both the odds and the
interval so a check happens on *every* turn, against a custom
[`EncounterTable`][osrlib.core.tables.EncounterTable] of rival adventuring parties
rather than the compiled monster table:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:wandering-table"
```

Level 1's own `WanderingSpec(chance_in_six=0)` disables wandering checks there
entirely — every encounter on that level is the keyed goblins, and every encounter on
level 2 is a rolled rival party. Both are ordinary
[`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] and
[`EncounterTable`][osrlib.core.tables.EncounterTable] instances; nothing about
authoring them is specific to a terminal front end.

## Building the party

`create.py` drives character creation two ways: an interactive one that prompts for a
name, class, and alignment per slot, and a scripted one that builds a fixed roster
from starting gold. Both call the same
[`create_character`][osrlib.core.character.create_character] function used in the
[quickstart](../getting-started/quickstart.md); only where the choices come from
differs. The scripted party — one of each core class, fighter, cleric, thief, and
magic-user, kitted out from its own starting gold — is what the non-interactive
`--script` mode always builds, which is why it plays back identically every time:

```{.python .no-run}
--8<-- "examples/tui_crawler/create.py:script-party-roster"
```

```{.python .no-run}
--8<-- "examples/tui_crawler/create.py:scripted-party-fn"
```

## The fetch quest: authored data, not front-end code

The barrow's hook — "the temple pays 200 gp for the Jade Idol's return" — is part of
the adventure, not part of the crawler. The idol is a bundled
[`GearTemplate`][osrlib.core.items.GearTemplate] the shop never stocks, dropped into
the shrine cache by id, so picking it up reports a catalog id anything can match on:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:bundled-idol"
```

The quest itself is a [`QuestSpec`][osrlib.crawl.quests.QuestSpec] in the same file —
an activation clause, two objectives, three rewards, and the marker that says
finishing it finishes the adventure:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:fetch-quest"
```

Nothing in the crawler tracks any of it. `__main__.py` registers the library's
[`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session right after
creating it, alongside the housekeeping that lines up the session's RNG streams with
the ones character creation already drew from:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:register-interpreter"
```

The interpreter is an ordinary [`Listener`][osrlib.crawl.session.Listener]: it runs
after every command, matches the events against the adventure's triggers and quests,
and acts the only way anything outside the engine may — by executing referee
commands, each stamped with the quest it acted for. The transcript shows the beats as
they land, rendered from typed events by the same formatter as everything else:

```text
> take idol_shrine
  character-0001 acquires jade-idol and 50 gp in coin.
  Quest the-idol: objective recover-idol is done. The idol comes up out of the hollow, cold as well-water.
> town
  The party enters town town.
  The adventure ends: 0 XP from monsters and 50 XP from treasure — 12 XP to each of 4 survivor(s).
  Quest the-idol: objective return-home is done. Threshold's gate shuts behind you with the idol inside it.
  Quest complete: The Jade Idol. The almoner counts out the reward without looking up. The idol is home.
  The adventure is over: the-idol is finished.
  character-0001 acquires 200 gp in coin.
```

The homecoming objective is a `town_entered` pattern narrowed by a `has_item`
condition, so walking back empty-handed is not a return — which is exactly why the
milestone script makes two trips, doing its selling and healing on the first one.
The second return ends the adventure in `victory`, and the temple pays afterwards:
a concluded session takes referee commands but no play.
[Listeners and flags](../guides/listeners-and-flags.md) covers the listener contract
the interpreter follows, and [Building an adventure](../getting-started/building-an-adventure.md)
covers authoring quests of your own.

## Where next

- [Building an adventure](../getting-started/building-an-adventure.md) — the dungeon
  geometry and authoring models the barrow is built from.
- [Views and visibility](../guides/views-and-visibility.md) — what a player's view
  includes, and how it's built from referee-only state.
- [Listeners and flags](../guides/listeners-and-flags.md) — registering listeners,
  the flag store, and the contract quest and achievement systems rely on.
- [The FastAPI pattern](fastapi-pattern.md) and [LLM referees](llm-referees.md) — the
  same `GameSession`, driven by different front ends entirely.
