"""The rules kernel: the B/X rules as functions you call without a running game.

You pass a kernel function the inputs for one rule, plus a random-number stream and a
[`Ruleset`][osrlib.core.ruleset.Ruleset]. A stream is a seeded sequence of random numbers that
repeats when you use the same seed. A ruleset is a frozen set of flags that turn optional rules on
or off for a game. The function returns the outcome together with the typed events that describe
it. An event is a frozen record of one thing that happened, like an attack roll.

The kernel has no session, no turn order, and no record of where the party is. Call the kernel
directly when you want the rules without a game, for example in a combat simulator, a script that
tests game balance, or a script that checks authored content. The dungeon-crawl framework in
`osrlib.crawl` is built on the kernel. Kernel modules never import from `osrlib.crawl`, so you
can use them without it.

Most kernel functions read rules content that osrlib compiles from the OSE SRD, the Open Game
Content text of the B/X rules. That content is the class, monster, item, and spell catalogs and the
printed tables, and [`osrlib.data`][osrlib.data] loads it. The exceptions the kernel raises are in
[`osrlib.errors`][osrlib.errors].

The kernel has these modules, in the order you usually need them:

- [`osrlib.core.rng`][osrlib.core.rng]: turn a master seed into a separate stream for each
  subsystem. Every function below that rolls dice takes one of those streams.
- [`osrlib.core.dice`][osrlib.core.dice]: parse a dice expression like `"2d6+1"`, and roll it to
  get each die and the total.
- [`osrlib.core.ruleset`][osrlib.core.ruleset]: the `Ruleset` that most resolution functions read.
- [`osrlib.core.clock`][osrlib.core.clock]: count game time in rounds, and get each turn and day
  boundary that an advance crosses. A turn is 60 rounds.
- [`osrlib.core.alignment`][osrlib.core.alignment]: the three alignments that characters and
  monsters share.
- [`osrlib.core.validation`][osrlib.core.validation]: the
  [`Rejection`][osrlib.core.validation.Rejection] a rules check returns instead of raising, with
  a code for the reason. A check returns a list of them, and an empty list means the action is
  legal.
- [`osrlib.core.abilities`][osrlib.core.abilities]: look up the modifiers an ability score from 3
  to 18 gives, make ability checks, and adjust a set of scores.
- [`osrlib.core.classes`][osrlib.core.classes]: read a class's titles and level table, award
  experience points, advance levels, and make thief skill checks.
- [`osrlib.core.monsters`][osrlib.core.monsters]: read a monster template, and spawn a monster
  with rolled hit points.
- [`osrlib.core.items`][osrlib.core.items]: buy and equip items, and work out encumbrance.
- [`osrlib.core.spells`][osrlib.core.spells]: memorize and cast spells, and turn undead.
- [`osrlib.core.tables`][osrlib.core.tables]: look up a cell of the printed tables from Hit Dice,
  an armour class, or a 2d6 total.
- [`osrlib.core.character`][osrlib.core.character]: roll a 1st-level character from a class id,
  your choices, a ruleset, and a stream. To create one step at a time, use the functions that
  check each choice. They return a `Rejection` for each problem.
- [`osrlib.core.npc`][osrlib.core.npc]: generate a party of non-player characters (NPCs) from a
  kind, `"basic"` or `"expert"`, a count, and two streams.
- [`osrlib.core.creature`][osrlib.core.creature]: the protocols that a character and a monster both
  satisfy. The combat, spell, and effect functions take their creature arguments as these
  protocols, so you can pass either.
- [`osrlib.core.combat`][osrlib.core.combat]: roll initiative, attacks, damage, saving throws, and
  morale.
- [`osrlib.core.effects`][osrlib.core.effects]: run a timed effect, like a *sleep* spell, against
  the clock, and end it on time.
- [`osrlib.core.treasure`][osrlib.core.treasure]: roll coins, gems, jewellery, and magic items from
  a treasure-type letter.
- [`osrlib.core.events`][osrlib.core.events]: the base class of every kernel event, and the events
  the kernel functions return.

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
