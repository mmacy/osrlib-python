# The barrow crawler

A minimal terminal dungeon crawl on osrlib and the plain standard library — no
curses, no Textual, no dependencies. It plays a complete adventure end to end —
character creation to leveling up — entirely through `GameSession.execute`.

## Running it

```sh
uv run python -m examples.tui_crawler
```

Create four adventurers at the prompts, then type commands:

```text
enter                # travel from town into the barrow
move e               # move a cell (n/s/e/w)
fight                # engage and auto-resolve battle rounds
take idol_shrine     # empty a cache (or `take pile` for dropped loot)
take cache-0001      # generated hoards are caches in the state overlay
stairs               # take the stairs on this cell
use character-0001 magic-item-0001   # drink, read, or activate a magic item
rest turn            # rest (turn / night / day)
town                 # return to town from the entrance
sell all             # sell carried valuables at full value
give character-0001 character-0002 550  # hand coin to a companion (coin weighs!)
heal character-0002 cure_light_wounds   # buy a temple service
status               # party summary, active quests included
journal              # the party's journal, in order of discovery
quit
```

Non-interactive mode replays a transcript with a fixed party and seed:

```sh
uv run python -m examples.tui_crawler --seed 21 --script examples/tui_crawler/scripts/milestone.txt
```

That transcript is the milestone playthrough, in two trips: the delve, a generated
goblin-lair hoard, a rival adventuring party fought and looted, the first return
with its XP award and the town business, then back down for the Jade Idol and home
again — which completes the quest, pays the temple's reward, takes a character to
level 2, and ends the adventure in `victory`.
`tests/test_example_crawler.py` drives exactly this run as the integration test.

## The quest, as adventure data

The fetch quest is content, not code. `content.py` bundles the Jade Idol as a
`GearTemplate` the shop never stocks, drops it into the shrine cache by id, and
authors a `QuestSpec` beside the dungeons:

- **Activation** — a `dungeon_entered` clause: crossing the barrow's threshold puts
  the errand in play, and its offer beat lands in the party's journal.
- **Objectives** — `recover-idol` matches the acquisition of `jade-idol` by catalog
  id; `return-home` matches a `town_entered` crossing narrowed by a `has_item`
  condition, so walking back without the idol is not a return.
- **Rewards**, issued after the quest completes: 200 gp to the lead survivor, an XP
  award to the whole party, and `SetFlag("quest.idol", "recovered")`.
- **`concludes_adventure=True`** — finishing the quest ends the adventure in
  `victory`, which is why the script does its selling and healing on the first trip.

`__main__.py` registers `Interpreter(session)` and nothing else: the library's own
listener matches the clauses, issues every lifecycle and reward command stamped
`source="quest:the-idol"`, and holds no state of its own. A game that wants its own
quest system instead writes a listener on the same surface — see
[Listeners and flags](https://mmacy.github.io/osrlib-python/guides/listeners-and-flags/).
