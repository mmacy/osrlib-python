# Using the rules without a session

Use `osrlib.core` directly when you want to roll dice, resolve combat, generate treasure, or query B/X tables from a script, combat simulator, or content validator without creating an adventure or a [`GameSession`][osrlib.crawl.session.GameSession]. `osrlib.core` never imports `osrlib.crawl`. Catalogs, templates (`MonsterTemplate`, `ClassDefinition`, `SpellTemplate`), and `Ruleset` are frozen models, whereas spawned combatants (`Character` and `MonsterInstance`) are mutable instances that combat and advancement functions update in place.

Outside a session, create an [`RngStreams`][osrlib.core.rng.RngStreams] instance from a master seed and pass an explicit stream to each function that rolls dice. [The RNG streams reference](../reference/rng-streams.md) lists the stream names `GameSession` uses by convention, though standalone scripts can pass any stream name: a given master seed and stream name always produce the same sequence of draws.

## Parse and roll dice

Write dice expressions as `NdS` (`N` dice of `S` sides), with an optional `+M` or `-M` modifier and an optional `×K` multiplier (`x` and `*` also work as ASCII aliases for `×`). `N` defaults to `1`, `d%` is an alias for `d100`, and `S` must be one of `2`, `3`, `4`, `6`, `8`, `10`, `12`, `20`, or `100`.

osrlib evaluates `(sum of dice + modifier) × multiplier`, applying the modifier *before* the multiplier in accordance with B/X treasure tables (so `2d6+1×10` evaluates `(2d6 + 1) × 10` rather than `2d6 + 10`). Call [`parse`][osrlib.core.dice.parse] to validate a string into a [`DiceExpression`][osrlib.core.dice.DiceExpression], and call [`roll`][osrlib.core.dice.roll] with a stream to get a [`RollResult`][osrlib.core.dice.RollResult] containing both `rolls` and `total` (you can also pass a raw string such as `"3d6"` directly to `roll`):

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

## Create combatants and resolve an attack

Call [`create_character`][osrlib.core.character.create_character] with a [`Ruleset`][osrlib.core.ruleset.Ruleset] and a stream to roll a character, and call [`spawn_monster`][osrlib.core.monsters.spawn_monster] with a [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate] from [`load_monsters`][osrlib.data.load_monsters] (see [the monster id index][monsters-index]) and a unique ID from [`IdAllocator`][osrlib.core.monsters.IdAllocator] to instantiate a monster.

Pass both combatants and an [`AttackContext`][osrlib.core.combat.AttackContext] to [`resolve_attack`][osrlib.core.combat.resolve_attack] (`attack=None` resolves an unarmed strike). `resolve_attack` runs the attack roll, weapon-immunity check, and damage roll, and deducts any damage from `defender.current_hp` in place, so do not subtract `attack.damage` a second time:

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
hp_before = goblin.current_hp
attack = resolve_attack(hero, goblin, None, context=AttackContext(), ruleset=rules, stream=streams.get(COMBAT_STREAM))
if attack.attack_roll.hit:
    assert attack.damage is not None and attack.damage >= 1
    assert goblin.current_hp == hp_before - attack.damage
else:
    assert attack.damage is None
    assert goblin.current_hp == hp_before
```

`resolve_attack` returns an [`AttackResult`][osrlib.core.combat.AttackResult] whose `events` field contains typed kernel events. Inspect them directly or pass them to [`format_message`][osrlib.messages.format_message].

## Generate treasure by type

Call [`generate_treasure`][osrlib.core.treasure.generate_treasure] with a B/X treasure-type letter selecting a hoard table (see [the treasure type index][treasure-types-index]), a `tier` (`"basic"` or `"expert"` for magic-item subtables, which `GameSession` derives from the highest living party level), an RNG stream, and an `IdAllocator`:

```{.python .no-run}
# Roll a treasure type letter's contents directly -- no dungeon, no keyed area.
treasure = generate_treasure("A", tier="basic", stream=streams.get(TREASURE_STREAM), allocator=allocator)
assert treasure.coins.value_cp >= 0
```

`generate_treasure` rolls coins, gems, jewellery, and magic items in table order and returns a [`GeneratedTreasure`][osrlib.core.items.GeneratedTreasure] (`coins`, with `value_cp` giving total coin value in copper pieces, `valuables`, and `magic_items`) with distinct IDs assigned from `allocator`.

## Look up combat tables

Functions in [`osrlib.core.tables`][osrlib.core.tables] perform pure table lookups without touching an RNG stream, so you can roll dice separately or feed fixed totals in tests. For example, [`reaction_result`][osrlib.core.tables.reaction_result] resolves a 2d6 total against [`ReactionTable`][osrlib.core.tables.ReactionTable] from [`load_combat_tables`][osrlib.data.load_combat_tables] into a [`ReactionResult`][osrlib.core.tables.ReactionResult] (such as `HOSTILE` or `FRIENDLY`):

```{.python .no-run}
# A reaction roll composed from two lower-level kernel pieces: 2d6, then the table.
total = roll("2d6", streams.get("encounter")).total
reaction = reaction_result(load_combat_tables().reaction, total)
assert 2 <= total <= 12
assert reaction in ReactionResult
```

When you don't need to supply or inspect the raw roll separately, call [`roll_reaction`][osrlib.core.combat.roll_reaction] or [`check_morale`][osrlib.core.combat.check_morale] in `osrlib.core.combat`, which roll 2d6 and look up the table result in one step.

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
hp_before = goblin.current_hp
attack = resolve_attack(hero, goblin, None, context=AttackContext(), ruleset=rules, stream=streams.get(COMBAT_STREAM))
if attack.attack_roll.hit:
    assert attack.damage is not None and attack.damage >= 1
    assert goblin.current_hp == hp_before - attack.damage
else:
    assert attack.damage is None
    assert goblin.current_hp == hp_before

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

- [Sessions, commands, and events](sessions-commands-events.md) - running `GameSession` with managed RNG streams.
- [Determinism, saves, and replay](determinism-saves-replay.md) - seed and stream guarantees built on `RngStreams`.
- [The API reference](../reference/api/index.md) - module-by-module reference for `osrlib.core`.
