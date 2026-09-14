"""The rules kernel: the B/X mechanics as pure functions over frozen models.

The kernel is the half of osrlib that has no game loop. You hand a kernel function the
inputs a rule needs, plus a seeded random-number stream and a
[`Ruleset`][osrlib.core.ruleset.Ruleset] (the frozen set of optional-rule flags a game
plays under), and it returns the outcome together with the typed events that describe
it. Nothing here starts a session, keeps a turn order, or remembers where the party is
standing. You call the kernel directly when you want the rules without a game: a combat
simulator, a balance harness, a script that checks authored content. The dungeon-crawl
framework in `osrlib.crawl` is one consumer of the kernel, and yours is another. Kernel
modules never import from `osrlib.crawl`, so what you build on the kernel keeps working
whatever the crawl layer does above it.

The modules, in the order you meet them:

- [`osrlib.core.rng`][osrlib.core.rng]: a master seed in, named deterministic streams
  out. Every other module here takes one of those streams as an argument.
- [`osrlib.core.dice`][osrlib.core.dice]: a dice expression such as `"2d6+1"` and a
  stream in, the individual dice and the total out.
- [`osrlib.core.ruleset`][osrlib.core.ruleset]: optional-rule flags in, a frozen
  `Ruleset` out, which most resolution functions read.
- [`osrlib.core.clock`][osrlib.core.clock]: a span of rounds, turns, or days in, the
  new elapsed time and the turn and day boundaries crossed out.
- [`osrlib.core.alignment`][osrlib.core.alignment] and
  [`osrlib.core.validation`][osrlib.core.validation]: the two vocabularies the rest of
  the kernel shares, an alignment and a structured refusal reason.
- [`osrlib.core.abilities`][osrlib.core.abilities]: a score of 3 to 18 in, the SRD's
  modifiers, an ability check, or an adjusted score set out.
- [`osrlib.core.classes`][osrlib.core.classes],
  [`osrlib.core.monsters`][osrlib.core.monsters],
  [`osrlib.core.items`][osrlib.core.items], and
  [`osrlib.core.spells`][osrlib.core.spells]: an id from a compiled catalog in, a
  frozen template or a playable instance out.
- [`osrlib.core.tables`][osrlib.core.tables]: Hit Dice, an armour class, or a 2d6
  total in, the printed table cell out.
- [`osrlib.core.character`][osrlib.core.character] and
  [`osrlib.core.npc`][osrlib.core.npc]: a class id, a ruleset, and a stream in, a
  rolled character or a generated NPC party out.
- [`osrlib.core.combat`][osrlib.core.combat] and
  [`osrlib.core.effects`][osrlib.core.effects]: an attacker, a defender, and a context
  in, the resolution and its events out.
- [`osrlib.core.treasure`][osrlib.core.treasure]: a treasure-type letter and a stream
  in, coins, gems, jewellery, and magic items out.
- [`osrlib.core.events`][osrlib.core.events]: the base class every kernel event
  subclasses, and the rules those events follow.

The compiled SRD catalogs the kernel reads come from [`osrlib.data`][osrlib.data], and
the exceptions it raises are in [`osrlib.errors`][osrlib.errors].

Typical usage:

```python
from osrlib.core.abilities import ability_check
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.dice import roll
from osrlib.core.rng import RngStreams
from osrlib.data import load_ability_tables

# One master seed forks every stream the kernel draws from.
stream = RngStreams(master_seed=7).get("character_creation")

# Roll a strength score, then read what the SRD's table grants at that score.
strength = roll("3d6", stream)
assert strength.rolls == (2, 6, 6)
assert strength.total == 14

tables = load_ability_tables()
assert tables.melee_modifier(strength.total) == 1
assert tables.open_doors_chance(strength.total) == 3

# An ability check rolls 1d20 and succeeds on equal-or-under the score.
check = ability_check(strength.total, stream)
assert (check.roll, check.success) == (13, True)

# The clock counts rounds and reports the turn and day boundaries an advance crosses.
clock = GameClock()
crossings = clock.advance(1, TimeUnit.TURN)
assert clock.rounds == 60
assert [(crossing.unit, crossing.index) for crossing in crossings] == [(TimeUnit.TURN, 1)]
```
"""
