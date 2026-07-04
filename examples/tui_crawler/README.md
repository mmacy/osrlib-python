# The barrow crawler

A minimal terminal dungeon crawl on osrlib and the plain standard library — no
curses, no Textual, no dependencies. It is the Phase 5 milestone: character
creation to leveling up, entirely through `GameSession.execute`.

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
heal character-0002 cure_light_wounds   # buy a temple service
status               # party summary
quit
```

Non-interactive mode replays a transcript with a fixed party and seed:

```sh
uv run python -m examples.tui_crawler --seed 5 --script examples/tui_crawler/scripts/milestone.txt
```

That transcript is the milestone playthrough: the delve, a generated goblin-lair
hoard, a rival adventuring party fought and looted, the Jade Idol recovered, the
return to town, the end-of-adventure XP award, and a character reaching level 2.
`tests/test_example_crawler.py` drives exactly this run as the integration test.

## The quest pattern

The fetch quest lives entirely in this example's own code, on the library's
listener/flags extension surface — the proof that games don't need library
changes for game-design systems:

- `quest.py` registers a listener keyed `fetch_quest`. It watches
  `ItemAcquiredEvent` for the Jade Idol and `LocationEnteredEvent` for the town
  return, and keeps its objective state in the session's listener store (so it
  snapshots into saves).
- It reacts by executing ordinary referee commands: `GrantCoins` for the recovery
  reward **the moment the idol is acquired, in the dungeon** — where the next
  award's valuation delta honors it. A reward granted at the town-return event
  would land after the award fired and before the next snapshot, earning nothing;
  the timing is part of the pattern.
- On the town return it executes `SetFlag("quest.idol", "recovered")` and an
  `AwardXP` quest bonus per member.

The listener never mutates game state directly; everything it causes goes through
logged commands, so replays and saves stay honest.
