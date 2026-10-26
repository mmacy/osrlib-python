# The TUI crawler

The barrow crawler is a complete, playable game built on osrlib alone: no curses, no Textual, no web framework, only `input()`, `print()`, and the standard library. Everything a session needs to run (rules, dice, state, the event log) is in the library. Everything a front end supplies (rendering and input handling) is ordinary application code that calls the public API. The game's content, including its fetch quest, is authored adventure data that the library's interpreter plays. The same [`GameSession`][osrlib.crawl.session.GameSession] this example drives could sit behind a web API or a graphical client instead, and nothing about it assumes a terminal.

Every code snippet below comes from the crawler's source. For the commands the game accepts and how to run it yourself, see the example's [README on GitHub](https://github.com/mmacy/osrlib-python/tree/main/examples/tui_crawler). One command starts an interactive game: `uv run python -m examples.tui_crawler`.

## Reading commands, rendering events

The crawler's loop is a `dispatch` function that turns one line of typed text into a command, and a `run` helper that executes it and prints whatever comes back. The game does all the parsing, because the library accepts typed commands, not sentences:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:parse-command"
```

`_DIRECTIONS` maps single letters to the compass words [`MoveParty`][osrlib.crawl.commands.MoveParty] takes. Once a command exists, running it is the same three steps as everywhere else in osrlib: execute, check acceptance, format the events. The loop is a plain iteration over `result.events` because the result envelope already contains everything. When a listener issues a command, as the interpreter does when it reacts to the player, the session adds that command's events to the result in log order, so a front end never needs to read `session.event_log` to see the whole chain. For a rejection, the crawler prints the code, plus the authored refusal text when a gate has one. Gate refusals are the only rejections that include text written for the player:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:render-events"
```

Every event has a [`Visibility`][osrlib.core.events.Visibility]. The crawler filters on `Visibility.PLAYER` here to keep referee-only bookkeeping out of the player's terminal. When you run the milestone script (`--seed 21 --script examples/tui_crawler/scripts/milestone.txt`), the game opens like this:

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

The second line shows why the result envelope exists. When the party crossed the threshold, the interpreter activated the adventure's quest with a command it issued *inside* the player's `enter`, and the session added that command's events to the result of the `enter`.

[`format_message`][osrlib.messages.format_message] renders every printed line from a typed event. A different front end could format the same events into JSON, a chat message, or nothing at all (see [the message code reference](../reference/message-codes.md)).

## The player's view

The event-level `Visibility` check above hides individual referee-only lines. The crawler's `status` and `journal` commands take a coarser approach: rather than reaching into referee-only state itself, the crawler asks the session for a whole snapshot built for players:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:player-view"
```

[`GameSession.view`][osrlib.crawl.session.GameSession.view] returns a frozen `PlayerView` when called with `Visibility.PLAYER`: hit points, gold, and carried valuables, and nothing a referee-only view would add. `_status` also walks `PlayerView.quests`: the **active** quests only, each with its revealed objectives by display name and state, which is why the closing status after victory lists no quest at all. A finished quest isn't in the view. Its record is in the journal. `_journal` renders `PlayerView.journal`, the authored record in order of discovery, each beat with the clock round when it was recorded. Both crawler commands only read the view: they execute no session command, draw nothing, and log nothing, so a script can use them anywhere without changing the game. The crawler never touches `session.party` or `session.monsters` directly to render status. It renders the same view any other front end would get by asking for one. [Views and visibility](../guides/views-and-visibility.md) covers what a `PlayerView` includes and how it differs from the referee's.

## The authored adventure

`content.py` builds the game's whole world: a town, a two-level barrow, and the fetch quest that ends the adventure, assembled from the same authoring models [Building an adventure](../getting-started/building-an-adventure.md) walks through. A keyed area attaches content (descriptive text, an encounter, features) to a set of cells. The shrine below has prose and the cache that contains the quest's MacGuffin. The cache refers to the MacGuffin by id, so the quest can match on the party taking it (the goblins are keyed to a different room):

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:idol-shrine-area"
```

Level 1 also has a guard room with goblins in it, but level 2 has no keyed monsters at all: its only area is an unguarded vault. Instead, level 2's [`WanderingSpec`][osrlib.crawl.dungeon.WanderingSpec] overrides both the odds and the interval so a check happens on *every* turn, against a custom [`EncounterTable`][osrlib.core.tables.EncounterTable] of rival adventuring parties rather than the compiled monster table:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:wandering-table"
```

Level 1's `WanderingSpec(chance_in_six=0)` disables wandering checks there entirely, so every encounter on that level is the keyed goblins, and every encounter on level 2 is a rolled rival party. The guard room is an ordinary [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] and the rival table an ordinary [`EncounterTable`][osrlib.core.tables.EncounterTable]. Nothing about authoring either one is specific to a terminal front end.

## Building the party

`create.py` drives character creation two ways: an interactive one that prompts for a name, class, and alignment per slot, and a scripted one that builds a fixed roster from starting gold. Both call the same [`create_character`][osrlib.core.character.create_character] function used in the [quickstart](../getting-started/quickstart.md), and differ only in where the choices come from. The scripted party is one of each core class (fighter, cleric, thief, and magic-user), each equipped from that character's starting gold. That's the party the non-interactive `--script` mode always builds, which is why it plays back identically every time:

```{.python .no-run}
--8<-- "examples/tui_crawler/create.py:script-party-roster"
```

```{.python .no-run}
--8<-- "examples/tui_crawler/create.py:scripted-party-fn"
```

## The fetch quest is authored data

The barrow's hook ("the temple pays 200 gp for the Jade Idol's return") is part of the adventure, not part of the crawler. The idol is a bundled [`GearTemplate`][osrlib.core.items.GearTemplate] that the shop doesn't stock. The shrine cache refers to it by id, so when the party picks it up, the event includes a catalog id that triggers and quests can match on:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:bundled-idol"
```

The quest itself is a [`QuestSpec`][osrlib.crawl.quests.QuestSpec] in the same file: an activation clause, two objectives, three rewards, and the field that ends the adventure when the quest is finished:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:fetch-quest"
```

The crawler doesn't track the quest. `__main__.py` registers the library's [`Interpreter`][osrlib.crawl.interpreter.Interpreter] on the session right after creating it, alongside a call that copies the stream positions from character creation into the session, so the session's streams continue from where character creation stopped:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:register-interpreter"
```

The interpreter is an ordinary [`Listener`][osrlib.crawl.session.Listener]: it runs after every command, matches the events against the adventure's triggers and quests, and acts the only way code outside the engine can, by executing referee commands. The interpreter stamps each command with its reason: `source="quest:the-idol"` on every command it issues for this quest, and `source="trigger:{id}"` when an authored trigger fires, so the command log records why each command ran. Here are two moments from the end of the same milestone run, rendered from typed events by the same formatter. First, the party empties the shrine cache:

```text
> take idol_shrine
  character-0001 acquires jade-idol and 17 gp in coin.
  character-0003 acquires 17 gp in coin.
  character-0004 acquires 16 gp in coin.
  Quest The Jade Idol: objective Recover the idol is done. The idol comes up out of the hollow, cold as well-water.
```

Then, four `move w` steps later, the homecoming:

```text
> town
  The party enters town town.
  The adventure ends: 0 XP from monsters and 50 XP from treasure — 16 XP to each of 3 survivor(s).
  character-0001 gains 16 XP (base 16), now level 1.
  character-0003 gains 17 XP (base 16), now level 1.
  character-0004 gains 17 XP (base 16), now level 1.
  Quest The Jade Idol: objective Bring it home is done. Threshold's gate shuts behind you with the idol inside it.
  Quest complete: The Jade Idol. The almoner counts out the reward without looking up. The idol is home.
  The adventure is over: The Jade Idol is finished. The almoner counts out the reward without looking up. The idol is home.
  character-0001 acquires 200 gp in coin.
  character-0001 gains 1260 XP (base 1200), now level 1.
  character-0003 gains 1320 XP (base 1200), now level 2.
  character-0003 advances to level 2 (Footpad): +4 hp (rolled 4).
  character-0004 gains 1320 XP (base 1200), now level 1.
```

Two details of that output need explaining. The engine spreads the cache across the party by the ordinary loot rules, so which character walks home with the idol is whatever the split produced. Here it's the fighter. The cleric isn't in the list because the cleric died in the vault. The objective's `has_item` condition tests whether *the party* has the idol, not which member does. The completion beat then appears twice, on the quest's event and again on the adventure's, because each event includes the authored line and the formatter appends the beat on whatever event it's given.

### Why the milestone makes two trips

The homecoming objective is a `town_entered` pattern narrowed by a `has_item` condition, so when the party walks back empty-handed, the objective doesn't fire. That condition is why `scripts/milestone.txt` makes four legs:

1. **Down**, for the goblins, their lair hoard, and the rival party prowling level 2.
2. **Home without the idol.** Returning to town resolves the end-of-adventure award, and the party sells its haul and buys a temple healing. Those town commands are legal here and nowhere later, because the adventure has not ended yet. Each coin counts as one coin of weight toward encumbrance, so the character who sold the haul uses `give` to spread the coins across the party before anyone moves again.
3. **Down again**, for the idol alone.
4. **Home with it**, which completes the second objective, completes the quest, and, because the quest has `concludes_adventure=True`, ends the session in `victory`. The rewards arrive *after* that transition: the 200 gp, the party's XP, and the `quest.idol` flag the crawler prints on its way out.

A concluded session still accepts referee commands and rejects play commands, so the closing `status` reads `[victory]` and any further `move` would be `wrong_mode`. [`SessionMode.terminal`][osrlib.crawl.commands.SessionMode] is the loop condition a front end checks. It's true in `victory` and `game_over` alike, so a front end reads one property to learn whether the session has ended. [The LLM referee page](llm-referees.md#the-schemas-are-the-tool-definitions) shows it as the condition of an agent loop. This crawler doesn't break on it. The loop stays open after victory so the script's closing `journal` and `status` can still read the session, which a terminal mode allows.

The order of the rewards suggests two authoring habits. Put the town business before the concluding return, while play commands are still legal. And give the quest's reward as `AwardXP` rather than as coin: under the default on-return timing, treasure converts to XP when the party comes home, and the award for the concluding return is already resolved when the interpreter issues the quest's rewards. The temple's 200 gp arrives as real, spendable coin, but it never converts to XP.

[Listeners and flags](../guides/listeners-and-flags.md) covers the listener contract the interpreter follows, and [Gates, triggers, and quests](../guides/gates-triggers-quests.md) covers authoring quests of your own.

## Where next

- [Building an adventure](../getting-started/building-an-adventure.md) - the dungeon geometry and authoring models the barrow is built from.
- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) - the authored layer behind the fetch quest, and how to write your own.
- [Views and visibility](../guides/views-and-visibility.md) - what a player's view includes, and how it's built from referee-only state.
- [Listeners and flags](../guides/listeners-and-flags.md) - registering listeners, the flag store, and the contract quest and achievement systems rely on.
- [The FastAPI pattern](fastapi-pattern.md) and [LLM referees](llm-referees.md) - the same `GameSession`, driven by different front ends.
