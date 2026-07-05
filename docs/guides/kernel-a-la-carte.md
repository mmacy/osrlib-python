# The kernel à la carte

`osrlib.core` is the rules kernel: dice, combat, treasure, spells, and the printed
tables, as pure functions over frozen models. None of it depends on a running game — the
dungeon-crawl framework in `osrlib.crawl` is one consumer of the kernel, built entirely on
top of it, and a mass-combat simulator, a balance harness, or a content-validation script is
just as valid a second consumer. The layering only runs one way: core never imports crawl, so
anything built against `osrlib.core` keeps working no matter what the crawl layer does above it.

Away from a session you bring your own [`RngStreams`][osrlib.core.rng.RngStreams] and pass the
stream each function asks for explicitly — there's no default stream and no hidden global RNG.
[The RNG streams reference](../reference/rng-streams.md) lists the stream keys a running
[`GameSession`][osrlib.crawl.session.GameSession] uses by convention, but à la carte code isn't
bound by them: a stream's name is just a label, and determinism only requires that the same
name draw the same sequence for a given master seed. This page tours four corners of the
kernel: rolling dice, resolving an attack, generating treasure, and looking up a reaction.
[The complete program](#the-complete-program) at the end runs all four in one script; every
fragment above it is an excerpt.

## The dice grammar

A dice expression is `NdS` — `N` dice of `S` sides — with an optional `+M`/`-M` modifier and an
optional `×K` multiplier (`x` and `*` both work as ASCII aliases for `×`). `N` defaults to 1,
`d%` is an alias for `d100`, and `S` comes from the closed set {2, 3, 4, 6, 8, 10, 12, 20, 100}.
Evaluation order is `(sum of the dice + modifier) × multiplier` — the modifier applies *before*
the multiplier, which reads differently than ordinary arithmetic precedence: `2d6+1×10` means
`(2d6 + 1) × 10`, the B/X treasure-roll convention, not `2d6 + 10`.
[`parse`][osrlib.core.dice.parse] turns the string into a frozen
[`DiceExpression`][osrlib.core.dice.DiceExpression]; [`roll`][osrlib.core.dice.roll] draws from
an explicit stream and returns a [`RollResult`][osrlib.core.dice.RollResult] carrying both the
individual dice and the total:

```{.python .no-run}
# The dice grammar: NdS, an optional +/-modifier, an optional x multiplier. The
# modifier applies before the multiplier -- (sum of dice + modifier) x multiplier.
expression = parse("2d6+1×10")
assert (expression.count, expression.sides, expression.modifier, expression.multiplier) == (2, 6, 1, 10)

# Bring your own RngStreams -- no session, no crawl import anywhere in this file.
streams = RngStreams(master_seed=2026)
rolled = roll(expression, streams.get("scratch"))
assert rolled.total == (sum(rolled.rolls) + 1) * 10
```

`roll` also accepts the plain string directly (it calls `parse` for you), so
`roll("3d6", stream)` is just as valid as parsing first.

## Resolving an attack against a spawned monster

[`create_character`][osrlib.core.character.create_character] and
[`spawn_monster`][osrlib.core.monsters.spawn_monster] both work with no session behind them: a
character needs only a [`Ruleset`][osrlib.core.ruleset.Ruleset] and a stream, and a monster
needs only its [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] from
[`load_monsters`][osrlib.data.load_monsters] (see [the monster id index][monsters-index]) plus
an id — conventionally minted by an [`IdAllocator`][osrlib.core.monsters.IdAllocator], the same
monotonic counter a `GameSession` uses internally.
[`resolve_attack`][osrlib.core.combat.resolve_attack] then runs the whole pipeline — the attack
roll, the immunity gate, the damage roll and its application — against an
[`AttackContext`][osrlib.core.combat.AttackContext] you build yourself; `attack=None` resolves
as an unarmed strike:

```{.python .no-run}
rules = Ruleset()

# Character creation is a kernel function too: no Party or GameSession required.
hero = create_character(
    name="Thistle",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=streams.get(CHARACTER_CREATION_STREAM),
).character

# Spawn a monster from the compiled catalog and resolve one unarmed attack against it.
allocator = IdAllocator()
goblin_template = load_monsters().get("goblin")
goblin = spawn_monster(goblin_template, id=allocator.allocate("monster"), stream=streams.get(MONSTER_SPAWN_STREAM))
attack = resolve_attack(hero, goblin, None, context=AttackContext(), ruleset=rules, stream=streams.get(COMBAT_STREAM))
if attack.attack_roll.hit:
    assert attack.damage is not None and attack.damage >= 1
else:
    assert attack.damage is None
```

`resolve_attack` returns an [`AttackResult`][osrlib.core.combat.AttackResult] carrying the same
typed events a session would append to its log — read them directly, or format them with
[`format_message`][osrlib.messages.format_message], entirely outside any session.

## Rolling treasure for a type letter

[`generate_treasure`][osrlib.core.treasure.generate_treasure] takes a treasure-type letter
straight off the SRD's tables (see [the treasure type index][treasure-types-index]) and rolls
its full contents — coins, gems, jewellery, and magic items — end to end from one stream, in
printed order, with an `IdAllocator` minting ids for whatever it generates. `tier` picks the
Basic or Expert magic-item columns; a session derives it from the party's highest living level,
but à la carte code just states it outright:

```{.python .no-run}
# Roll a treasure type letter's contents directly -- no dungeon, no keyed area.
treasure = generate_treasure("A", tier="basic", stream=streams.get(TREASURE_STREAM), allocator=allocator)
assert treasure.coins.value_cp >= 0
```

The result is a [`GeneratedTreasure`][osrlib.core.items.GeneratedTreasure]: `coins`,
`valuables` (gems and jewellery), and `magic_items`, every valuable and item instance carrying
its own allocated id.

## Looking up a reaction

The table lookups in [`osrlib.core.tables`][osrlib.core.tables] are plain functions over plain
data — no stream, no side effect — so composing one from a roll is just calling `roll` and then
the lookup. [`reaction_result`][osrlib.core.tables.reaction_result] resolves a 2d6 total against
the loaded [`ReactionTable`][osrlib.core.tables.ReactionTable] from
[`load_combat_tables`][osrlib.data.load_combat_tables]:

```{.python .no-run}
# A reaction roll composed from two lower-level kernel pieces: 2d6, then the table.
total = roll("2d6", streams.get("encounter")).total
reaction = reaction_result(load_combat_tables().reaction, total)
assert 2 <= total <= 12
assert reaction in ReactionResult
```

The combat kernel's own [`roll_reaction`][osrlib.core.combat.roll_reaction] and
[`check_morale`][osrlib.core.combat.check_morale] wrap this same pattern — roll the dice,
consult the table — for the common case where the intermediate total doesn't matter to the
caller.

## The complete program

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.combat import COMBAT_STREAM, AttackContext, resolve_attack
from osrlib.core.dice import parse, roll
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import ReactionResult, reaction_result
from osrlib.core.treasure import TREASURE_STREAM, generate_treasure
from osrlib.data import load_combat_tables, load_monsters

# The dice grammar: NdS, an optional +/-modifier, an optional x multiplier. The
# modifier applies before the multiplier -- (sum of dice + modifier) x multiplier.
expression = parse("2d6+1×10")
assert (expression.count, expression.sides, expression.modifier, expression.multiplier) == (2, 6, 1, 10)

# Bring your own RngStreams -- no session, no crawl import anywhere in this file.
streams = RngStreams(master_seed=2026)
rolled = roll(expression, streams.get("scratch"))
assert rolled.total == (sum(rolled.rolls) + 1) * 10

rules = Ruleset()

# Character creation is a kernel function too: no Party or GameSession required.
hero = create_character(
    name="Thistle",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=streams.get(CHARACTER_CREATION_STREAM),
).character

# Spawn a monster from the compiled catalog and resolve one unarmed attack against it.
allocator = IdAllocator()
goblin_template = load_monsters().get("goblin")
goblin = spawn_monster(goblin_template, id=allocator.allocate("monster"), stream=streams.get(MONSTER_SPAWN_STREAM))
attack = resolve_attack(hero, goblin, None, context=AttackContext(), ruleset=rules, stream=streams.get(COMBAT_STREAM))
if attack.attack_roll.hit:
    assert attack.damage is not None and attack.damage >= 1
else:
    assert attack.damage is None

# Roll a treasure type letter's contents directly -- no dungeon, no keyed area.
treasure = generate_treasure("A", tier="basic", stream=streams.get(TREASURE_STREAM), allocator=allocator)
assert treasure.coins.value_cp >= 0

# A reaction roll composed from two lower-level kernel pieces: 2d6, then the table.
total = roll("2d6", streams.get("encounter")).total
reaction = reaction_result(load_combat_tables().reaction, total)
assert 2 <= total <= 12
assert reaction in ReactionResult
```

## Where next

- [Sessions, commands, and events](sessions-commands-events.md) — how `GameSession` wires these
  same kernel functions to its own named streams automatically.
- [Determinism, saves, and replay](determinism-saves-replay.md) — the seed and stream
  guarantees this page's `RngStreams` container relies on.
- [The API reference](../reference/api/index.md) — every public symbol in `osrlib.core`,
  module by module.
