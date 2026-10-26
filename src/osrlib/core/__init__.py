"""The rules kernel: the B/X mechanics as functions over models, with no game loop.

The kernel is the half of osrlib that has no game loop. You hand a kernel function the inputs a
rule needs, plus a random-number stream and a [`Ruleset`][osrlib.core.ruleset.Ruleset]. A stream
is a seeded sequence of random numbers that repeats when you use the same seed. A ruleset is the
frozen set of optional-rule flags a game plays under. The function returns the outcome together
with the typed events that describe it. An event is a frozen record of one thing the rules did,
like an attack roll.

Nothing here starts a session, keeps a turn order, or tracks where the party is standing. Call the
kernel directly when you want the rules without a game: a combat simulator, a balance harness, or a
script that checks authored content. The dungeon-crawl framework in `osrlib.crawl` is built on the
kernel. Kernel modules never import from `osrlib.crawl`, so you can use them without it.

Most kernel functions read rules content that osrlib compiles from the OSE SRD, the Open Game
Content text of the B/X rules: the class, monster, item, and spell catalogs and the printed tables.
[`osrlib.data`][osrlib.data] loads them. The exceptions the kernel raises are in
[`osrlib.errors`][osrlib.errors].

The kernel has these modules, in the order a reader usually needs them:

- [`osrlib.core.rng`][osrlib.core.rng]: a master seed in, one named stream per subsystem out.
  Every module below that rolls dice takes one of those streams as an argument.
- [`osrlib.core.dice`][osrlib.core.dice]: a dice expression like `"2d6+1"` and a stream in, the
  individual dice and the total out.
- [`osrlib.core.ruleset`][osrlib.core.ruleset]: optional-rule flags in, a frozen `Ruleset` out.
  Most resolution functions read it.
- [`osrlib.core.clock`][osrlib.core.clock]: a span of rounds, turns, or days in, the new elapsed
  time and the turn and day boundaries crossed out.
- [`osrlib.core.alignment`][osrlib.core.alignment]: the three alignments that characters and
  monsters share.
- [`osrlib.core.validation`][osrlib.core.validation]: the
  [`Rejection`][osrlib.core.validation.Rejection] a rules check returns instead of raising,
  with a code that says why.
- [`osrlib.core.abilities`][osrlib.core.abilities]: an ability score from 3 to 18 in, the
  modifiers the tables grant, an ability check, or an adjusted set of scores out.
- [`osrlib.core.classes`][osrlib.core.classes]: a class definition and a character in, titles,
  experience points, level advancement, and thief skill checks out.
- [`osrlib.core.monsters`][osrlib.core.monsters]: a monster template in, a spawned monster with
  rolled hit points out.
- [`osrlib.core.items`][osrlib.core.items]: item templates and an inventory in, purchases,
  equipment, and encumbrance out.
- [`osrlib.core.spells`][osrlib.core.spells]: a caster and a spell in, memorization, casting, and
  turning undead out.
- [`osrlib.core.tables`][osrlib.core.tables]: Hit Dice, an armour class, or a 2d6 total in, the
  printed table cell out.
- [`osrlib.core.character`][osrlib.core.character]: a class id, creation choices, a ruleset, and
  a stream in, a rolled 1st-level character out. Its step-by-step functions return the reasons a
  choice is refused.
- [`osrlib.core.npc`][osrlib.core.npc]: `"basic"` or `"expert"`, a count, and two streams in, a
  generated party of non-player characters (NPCs) out.
- [`osrlib.core.creature`][osrlib.core.creature]: the protocols that a character and a monster
  both satisfy. The combat, spell, and effect functions take their creature arguments as these
  protocols, so you can pass either.
- [`osrlib.core.combat`][osrlib.core.combat]: combatants and a stream in, initiative, attacks,
  damage, saving throws, and morale out.
- [`osrlib.core.effects`][osrlib.core.effects]: a timed effect, like a *sleep* spell, in, a ledger
  that runs it against the clock and ends it on time out.
- [`osrlib.core.treasure`][osrlib.core.treasure]: a treasure-type letter and a stream in, coins,
  gems, jewellery, and magic items out.
- [`osrlib.core.events`][osrlib.core.events]: the base class every kernel event inherits, and the
  events the kernel functions return.

Typical usage:

```python
from osrlib.core.abilities import ability_check
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.dice import roll
from osrlib.core.rng import RngStreams, StreamName
from osrlib.data import load_ability_tables

# One master seed forks every stream the kernel draws from.
stream = RngStreams(master_seed=7).get(StreamName.CHARACTER_CREATION)

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
