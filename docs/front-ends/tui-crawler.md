# The TUI crawler

The barrow crawler is a complete, playable game built on osrlib and nothing else — no
curses, no Textual, no web framework, just `input()`, `print()`, and the standard
library. It exists to make one claim concrete: everything a session needs to run —
rules, dice, state, the event log — lives in the library, everything a front end
supplies — rendering, input handling — is ordinary application code written against
the public surface, and the game's content, its fetch quest included, is authored
adventure data the library's own interpreter plays. The same
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
acceptance, format the events. The loop is a plain iteration over `result.events`
because the envelope already carries everything: whatever a nested listener-issued
command logged — the interpreter's reactions above all — folds into the result, in
log order, so a front end never needs to read `session.event_log` to see the whole
chain. A rejection prints its code, plus the authored refusal text when a gate wrote
one — the one rejection family carrying words the player is meant to read:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:render-events"
```

Every event carries a [`Visibility`][osrlib.core.events.Visibility]; filtering on
`Visibility.PLAYER` here is what keeps referee-only bookkeeping out of the player's
terminal. Running the milestone transcript (`--seed 21 --script
examples/tui_crawler/scripts/milestone.txt`) opens like this:

```text
> enter
  The party enters dungeon barrow (level 1).
  A new quest: The Jade Idol. The temple wants the Jade Idol off the barrow king's altar and back on its own.
> move e
  The party moves to (1, 0), facing east.
> move e
  The party moves to (2, 0), facing east.
  The party enters area guard_room (level 1).
  Encounter: 2 × Goblin at 20' — the party is surprised.
  The monsters' bearing: uncertain.
```

The second line is already the result envelope earning its keep: crossing the
threshold activated the adventure's quest, and what printed it was a command the
interpreter issued *inside* the player's `enter` — folded into the same result the
`enter` came back with.

Every printed line is [`format_message`][osrlib.messages.format_message] rendering a
typed event — a different front end could format the same events into JSON, a chat
message, or nothing at all (see [the message code reference](../reference/message-codes.md)).

## The player's view

The event-level `Visibility` check above hides individual referee-only lines. The
crawler's `status` and `journal` commands take a coarser approach: they ask the
session for a whole snapshot built for players, rather than reaching into
referee-only state themselves:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:player-view"
```

[`GameSession.view`][osrlib.crawl.session.GameSession.view] returns a frozen
`PlayerView` when called with `Visibility.PLAYER` — hit points, gold, and carried
valuables, and nothing a referee-only view would add. `_status` also walks
`PlayerView.quests`: the **active** quests only, each with its revealed objectives
and their states, which is why the closing status after victory lists no quest at
all — a finished quest leaves the projection, and its record is the journal.
`_journal` renders `PlayerView.journal`, the authored record in order of discovery,
each beat stamped with the clock round it landed at. Both verbs are pure view
reads: they execute no command, draw nothing, and log nothing, so a script may
sprinkle them anywhere without changing the game. The crawler never touches
`session.party` or `session.monsters` directly to render status; it renders the same
view any other front end would get by asking for one. [Views and visibility](../guides/views-and-visibility.md)
covers what a `PlayerView` includes and how it differs from the referee's.

## The authored adventure

`content.py` builds the game's whole world: a town, a two-level barrow, and the errand
that ends it, assembled from the same authoring models
[Building an adventure](../getting-started/building-an-adventure.md) walks through. A
keyed area binds content — descriptive text, an encounter, features — to a set of
cells; the shrine below binds prose and the cache that holds the quest's MacGuffin,
named by id so that taking it is something the quest can match on (the goblins are
keyed to a different room):

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
commands, each stamped with what it acted for: `source="quest:the-idol"` on every
command this quest causes, `source="trigger:{id}"` when an authored trigger fires,
so the command log answers *why* on its own. Two moments from the end of the same
milestone run show it, rendered from typed events by the same formatter as everything
else. Emptying the shrine cache:

```text
> take idol_shrine
  character-0001 acquires 13 gp in coin.
  character-0002 acquires 13 gp in coin.
  character-0003 acquires jade-idol and 12 gp in coin.
  character-0004 acquires 12 gp in coin.
  Quest the-idol: objective recover-idol is done. The idol comes up out of the hollow, cold as well-water.
```

Then, four `move w` steps later, the homecoming:

```text
> town
  The party enters town town.
  The adventure ends: 0 XP from monsters and 50 XP from treasure — 12 XP to each of 4 survivor(s).
  character-0001 gains 12 XP (base 12), now level 1.
  character-0002 gains 9 XP (base 12), now level 1.
  character-0003 gains 13 XP (base 12), now level 1.
  character-0004 gains 13 XP (base 12), now level 1.
  Quest the-idol: objective return-home is done. Threshold's gate shuts behind you with the idol inside it.
  Quest complete: The Jade Idol. The almoner counts out the reward without looking up. The idol is home.
  The adventure is over: the-idol is finished. The almoner counts out the reward without looking up. The idol is home.
  character-0001 acquires 200 gp in coin.
  character-0001 gains 1260 XP (base 1200), now level 1.
  character-0002 gains 960 XP (base 1200), now level 1.
  character-0003 gains 1320 XP (base 1200), now level 2.
  character-0003 advances to level 2 (Footpad): +4 hp (rolled 4).
  character-0004 gains 1320 XP (base 1200), now level 1.
```

Two details of that output are the whole chapter in miniature. The cache spreads across
the party by the ordinary loot rules, so the thief is the one carrying the idol when the
party walks home — and the objective's `has_item` condition asks whether *the party*
carries it, not who. And the completion beat appears twice, on the quest's own event and
again on the adventure's, because each event carries the authored line and the formatter
appends whatever beat rides the event it is given.

### Why the milestone makes two trips

The homecoming objective is a `town_entered` pattern narrowed by a `has_item`
condition, so walking back empty-handed is not a return — the objective simply does
not fire. That one clause is what gives `scripts/milestone.txt` its shape:

1. **Down**, for the goblins, their lair hoard, and the rival party prowling level 2.
2. **Home without the idol.** The return banks the end-of-adventure award, and the
   party sells its haul and buys a temple healing — town commands that are legal here
   and nowhere later, because the adventure has not ended yet. Coin weighs a coin
   apiece, so the seller spreads the purse with `give` before anybody walks again.
3. **Down again**, for the idol alone.
4. **Home with it**, which completes the second objective, completes the quest, and
   — the quest carrying `concludes_adventure=True` — ends the session in `victory`.
   The rewards land *after* that transition: the 200 gp, the party's XP, and the
   `quest.idol` flag the crawler prints on its way out.

A concluded session still takes referee commands and refuses play, so the closing
`status` reads `[victory]` and any further `move` would be `wrong_mode`.
[`SessionMode.terminal`][osrlib.crawl.commands.SessionMode] is the loop condition a
front end checks — true in `victory` and `game_over` alike, it answers "has this
session ended?" in one read, and [the LLM referee page](llm-referees.md#the-schemas-are-the-tool-definitions)
shows it guarding an agent loop. This crawler deliberately does *not* break on it:
the loop stays open after victory so the script's closing `journal` and `status` can
still be read, which is exactly the referee-side access a terminal mode preserves.

Two beats of authoring discipline fall out of the reward ordering and are worth
copying. Put the town business before the concluding return, while play commands are
still legal. And put the story's thanks in `AwardXP` rather than in coin: under the
default on-return timing, treasure converts to XP when the party comes home, and the
concluding return's award has already resolved by the time the rewards issue — so
the temple's 200 gp arrives as real, spendable coin, but no XP will ever be minted
from it.

[Listeners and flags](../guides/listeners-and-flags.md) covers the listener contract
the interpreter follows, and [Gates, triggers, and quests](../guides/gates-triggers-quests.md)
covers authoring quests of your own.

## Where next

- [Building an adventure](../getting-started/building-an-adventure.md) — the dungeon
  geometry and authoring models the barrow is built from.
- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) — the authored
  layer behind the fetch quest, and how to write your own.
- [Views and visibility](../guides/views-and-visibility.md) — what a player's view
  includes, and how it's built from referee-only state.
- [Listeners and flags](../guides/listeners-and-flags.md) — registering listeners,
  the flag store, and the contract quest and achievement systems rely on.
- [The FastAPI pattern](fastapi-pattern.md) and [LLM referees](llm-referees.md) — the
  same `GameSession`, driven by different front ends entirely.
