# The TUI crawler

[`examples/tui_crawler`](https://github.com/mmacy/osrlib-python/tree/main/examples/tui_crawler) is a playable command-line dungeon crawler built with `osrlib` and Python's standard library (`input()` and `print()`, with no terminal UI framework). It drives [`GameSession`][osrlib.crawl.session.GameSession] synchronously in process: `osrlib` resolves rules, dice rolls, dungeon state, and authored quests, while the front end parses text input and prints formatted output. Because `GameSession` performs no I/O, you can connect the same session loop and adventure data to a web API or graphical client.

From a clone of the [osrlib repository](https://github.com/mmacy/osrlib-python), start an interactive game with `uv` (the first run installs project dependencies automatically):

```sh
uv run python -m examples.tui_crawler
```

Or run the deterministic milestone script non-interactively with a fixed party and seed:

```sh
uv run python -m examples.tui_crawler --seed 39 --script examples/tui_crawler/scripts/milestone.txt
```

Every code snippet on this page is included from `examples/tui_crawler/`. See the example's [README on GitHub](https://github.com/mmacy/osrlib-python/tree/main/examples/tui_crawler) for the full list of CLI commands.

## Roll the party and start the session

`examples/tui_crawler/create.py` builds a four-character party in two ways: `interactive_party` prompts you for each character's name, class, and alignment, whereas `scripted_party` builds a fixed fighter, cleric, thief, and magic-user outfitted from starting gold so `--script` runs play identically every time. Both functions pass a seeded [`RngStream`][osrlib.core.rng.RngStream] (`CHARACTER_CREATION_STREAM`) to [`create_character`][osrlib.core.character.create_character], as in the [Quickstart](../getting-started/quickstart.md):

```{.python .no-run}
--8<-- "examples/tui_crawler/create.py:script-party-roster"
```

```{.python .no-run}
--8<-- "examples/tui_crawler/create.py:scripted-party-fn"
```

After rolling the party, `main()` in `__main__.py` passes `seed=arguments.seed` and `ruleset=ruleset` to [`GameSession.new`][osrlib.crawl.session.GameSession.new]. Because `GameSession.new` initializes fresh RNG streams from `seed`, `main()` calls `session.streams.restore_states(streams.export_states())` so `CHARACTER_CREATION_STREAM` continues from where party creation stopped, and then registers [`Interpreter`][osrlib.crawl.interpreter.Interpreter] to evaluate the adventure's authored quests and triggers after each accepted command:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:register-interpreter"
```

## Parse input and render command results

`GameSession.execute` accepts typed [`Command`][osrlib.crawl.commands.Command] objects rather than raw strings, so `_dispatch_command` in `__main__.py` maps each CLI verb to a command instance (`_DIRECTIONS` expands `n`, `s`, `e`, and `w` into the compass words [`MoveParty`][osrlib.crawl.commands.MoveParty] expects):

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:parse-command"
```

Separate input-parsing errors from in-game rule rejections. When a player mistypes an argument (such as `move sideways` or a non-numeric coin amount), Pydantic raises `ValidationError` (or `int()` raises `ValueError`) before any `Command` object reaches the session. `_dispatch` catches both exceptions, prints the error, and returns to the prompt:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:validate-input"
```

Once a well-formed `Command` is constructed, `_run` passes it to `session.execute(command)` and prints the outcome from the returned [`CommandResult`][osrlib.crawl.commands.CommandResult]. When a game rule blocks a well-formed command (`not result.accepted`, such as walking into a wall), `_run` prints each rejection code along with `rejection.params["refusal"]` if a gated door or stair supplied authored refusal text. When the command succeeds, `_run` iterates over `result.events`, keeps only events with [`Visibility.PLAYER`][osrlib.core.events.Visibility] so referee-only rolls stay hidden, and formats each event with [`format_message`][osrlib.messages.format_message] (see [the message code reference](../reference/message-codes.md)):

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:render-events"
```

You do not need to read `session.event_log` to capture reactions from listeners. When a listener such as `Interpreter` executes referee commands in response to a player move, `GameSession.execute` appends those nested events to `result.events` in log order. Running `--seed 39 --script examples/tui_crawler/scripts/milestone.txt` shows that reaction chain on the opening `enter` command:

```text
> enter
  The party enters dungeon barrow (level 1).
  A new quest: The Jade Idol. The temple wants the Jade Idol off the barrow king's altar and back on its own.
> move e
  The party moves to (1, 0), facing east.
> move e
  The party moves to (2, 0), facing east.
  The party enters area guard_room (level 1).
  Encounter: 2 goblins at 20'; the monsters are surprised.
  The monsters' bearing: hostile.
```

Entering the barrow matched the quest's activation clause, so `Interpreter` executed [`ActivateQuest`][osrlib.crawl.commands.ActivateQuest] during `EnterDungeon`, and `result.events` returned both lines together. A web or graphical front end can serialize or render those same structured events instead of calling `format_message`.

## Inspect state with PlayerView

While event filtering hides individual referee-only rolls, the crawler's `status` and `journal` commands render whole-session state by calling [`session.view(Visibility.PLAYER)`][osrlib.crawl.session.GameSession.view] rather than reading `session.party` or `session.monsters` directly:

```{.python .no-run}
--8<-- "examples/tui_crawler/__main__.py:player-view"
```

[`PlayerView`][osrlib.crawl.views.PlayerView] is a frozen snapshot containing only player-safe fields:

- `_status` prints the current mode, elapsed rounds, each party member's hit points, gold, and carried valuables, and `view.quests`. Because `view.quests` lists only **active** quests and their revealed objectives, a completed quest disappears from `status` once finished, while its narrative history remains in `view.journal`.
- `_journal` prints `view.journal`, displaying each authored beat in discovery order alongside the clock round when it was recorded.

Neither `status` nor `journal` calls `session.execute`, consumes RNG draws, or writes to the session logs, so you can run them at any point in a script without altering gameplay. See [Views and visibility](../guides/views-and-visibility.md) for a full comparison of `PlayerView` and `RefereeView`.

## Author the barrow adventure and fetch quest

`examples/tui_crawler/content.py` defines the town (`Threshold`), a two-level barrow dungeon, a bundled quest item, and the concluding fetch quest using the models from [Building an adventure](../getting-started/building-an-adventure.md) and [Gates, triggers, and quests](../guides/gates-triggers-quests.md).

### Dungeon levels and wandering rivals

Level 1 disables wandering-monster checks with `WanderingSpec(chance_in_six=0)`, keys two goblins to `guard_room`, and places a `"treasure_cache"` feature in `shrine` containing 50 gp and the quest item's catalog ID (`IDOL_ID`):

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:idol-shrine-area"
```

Level 2 contains an unguarded vault (`AreaTreasureSpec(unguarded=True)`) with no keyed monsters. Instead, its `LevelSpec` sets `wandering=WanderingSpec(chance_in_six=6, interval_turns=1, table=_rival_party_table())`, checking a custom [`EncounterTable`][osrlib.core.tables.EncounterTable] that spawns a pair of rival Basic NPC adventurers on every exploration turn:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:wandering-table"
```

### Bundled quest item and QuestSpec

`build_adventure()` passes `items=(JADE_IDOL,)` and `quests=(_fetch_quest(),)` on the [`Adventure`][osrlib.crawl.adventure.Adventure]. `JADE_IDOL` is a bundled [`GearTemplate`][osrlib.core.items.GearTemplate] (`id="jade-idol"`, `cost_gp=0`). Because the town shop stocks only built-in SRD equipment, players cannot buy `jade-idol`, and looting it from the shrine cache emits an [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent] referencing `"jade-idol"` by catalog ID:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:bundled-idol"
```

`_fetch_quest()` defines the temple's errand as a [`QuestSpec`][osrlib.crawl.quests.QuestSpec] with an activation rule, two objectives, three reward commands, and `concludes_adventure=True`:

```{.python .no-run}
--8<-- "examples/tui_crawler/content.py:fetch-quest"
```

The crawler contains no custom quest-tracking code. The registered `Interpreter` listens to command events, advances the quest, and executes its lifecycle and reward commands stamped with `source="quest:the-idol"` (and authored triggers stamp `source="trigger:{id}"`) so `session.command_log` records why each referee command ran.

## Play through the two-trip milestone to victory

Objective `return-home` pairs `TownEnteredPattern()` with `HasItemCondition(item_id="jade-idol")`, so returning to town without the idol does not complete the quest. Because `_fetch_quest()` sets `concludes_adventure=True`, finishing the quest immediately switches `session.mode` to `victory`, where play commands such as `sell`, `heal`, and `give` are rejected with `session.command.wrong_mode`. `examples/tui_crawler/scripts/milestone.txt` therefore splits the playthrough into two trips:

1. **First trip (loot, healing, and encumbrance)**: Enter the barrow, defeat the keyed goblins on level 1 and loot their generated lair hoard, descend to level 2 to loot the vault and fight the rival adventurers, and return to town *without* taking the idol. Returning to town awards XP for that expedition's defeated monsters and recovered treasure (`session.xp.adventure_award`) while keeping the quest active and the session in `town` mode, allowing the party to sell valuables (`sell all`), buy temple healing (`heal`), and redistribute heavy sale coins with `give` (each coin adds one coin of encumbrance weight, and the party moves at its slowest member's speed).
2. **Second trip (recovering the idol)**: Re-enter the barrow, walk straight to `shrine`, take the idol, and return to town to complete the quest.

Emptying the shrine cache on the second trip splits the 50 gp and assigns `jade-idol` using standard loot distribution (here `character-0002`, the cleric, receives the idol, and `HasItemCondition` checks whether *any* party member carries it):

```text
> take idol_shrine
  character-0001 acquires 13 gp in coin.
  character-0002 acquires jade-idol and 13 gp in coin.
  character-0003 acquires 12 gp in coin.
  character-0004 acquires 12 gp in coin.
  Quest The Jade Idol: objective Recover the idol is done. The idol comes up out of the hollow, cold as well-water.
```

Four `move w` steps bring the party back to the entrance, where `town` resolves the second trip's treasure XP and completes the quest:

```text
> town
  The party enters town town.
  The adventure ends: 0 XP from monsters and 50 XP from treasure, and each of 4 survivor(s) gets 12 XP.
  character-0001 gains 12 XP (base 12), now level 1.
  character-0002 gains 12 XP (base 12), now level 1.
  character-0003 gains 12 XP (base 12), now level 1.
  character-0004 gains 10 XP (base 12), now level 1.
  Quest The Jade Idol: objective Bring it home is done. Threshold's gate shuts behind you with the idol inside it.
  Quest complete: The Jade Idol. The almoner counts out the reward without looking up. The idol is home.
  The adventure is over: The Jade Idol is finished. The almoner counts out the reward without looking up. The idol is home.
  character-0001 acquires 200 gp in coin.
  character-0001 gains 1260 XP (base 1200), now level 2.
  character-0001 advances to level 2 (Warrior): +9 hp (rolled 8).
  character-0002 gains 1200 XP (base 1200), now level 2.
  character-0002 advances to level 2 (Adept): +4 hp (rolled 3).
  character-0003 gains 1200 XP (base 1200), now level 2.
  character-0003 advances to level 2 (Footpad): +4 hp (rolled 2).
  character-0004 gains 1080 XP (base 1200), now level 1.
```

Notice how those lines are ordered:

- **Completion narrative**: `narrative.completion` appears on both [`QuestCompletedEvent`][osrlib.crawl.events.QuestCompletedEvent] and [`AdventureCompletedEvent`][osrlib.crawl.events.AdventureCompletedEvent], so `format_message` appends the completion beat on both lines.
- **Reward timing and XP**: Under the default `on_return` XP rule, `TravelToTown` awards treasure XP *before* `Interpreter` runs `QuestSpec.rewards`. The temple's `200` gp reward arrives afterward as spendable coin without converting to treasure XP, so `_fetch_quest()` pairs `GrantCoins` with an explicit `AwardXP` command (`1200` base XP, adjusted by each survivor's prime-requisite modifier).
- **Terminal session mode**: `CompleteQuest` switches `session.mode` to `victory` ([`SessionMode.terminal`][osrlib.crawl.commands.SessionMode] is `True` in both `victory` and `game_over`; see the agent loop in [LLM referees](llm-referees.md#define-tools-from-the-json-schemas)). `examples/tui_crawler` keeps its prompt loop open until EOF or `quit` so the script's closing `journal` and `status` commands can still read `PlayerView` in `[victory]` mode before `main()` prints `quest.idol = 'recovered'`.

## Where next

- [Building an adventure](../getting-started/building-an-adventure.md) - dungeon grids, keyed areas, features, transitions, and adventure validation.
- [Gates, triggers, and quests](../guides/gates-triggers-quests.md) - authoring gated passages, event triggers, and quests run by `Interpreter`.
- [Views and visibility](../guides/views-and-visibility.md) - what `PlayerView` includes and how it filters referee-only state.
- [Listeners and flags](../guides/listeners-and-flags.md) - writing custom event listeners and reading or setting `session.flags`.
- [The FastAPI pattern](fastapi-pattern.md) and [LLM referees](llm-referees.md) - serving the same `GameSession` over HTTP or driving it with an LLM referee.
