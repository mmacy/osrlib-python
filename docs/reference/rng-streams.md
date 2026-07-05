# RNG streams

Every random draw in osrlib comes from a named stream, and every stream is forked from
the session's master seed. Given the same master seed and the same stream key, a stream
always produces the identical sequence of draws — and it produces that sequence no
matter what any other stream does. Drawing a hundred rolls from the treasure stream
never changes what the combat stream yields next. That per-key independence is what
makes deterministic replays and saved games reliable: two sessions built from the same
seed replay identically, and adding new draws to one subsystem never shifts another
subsystem's rolls.

Each stream is identified by a plain string key, such as `"combat"` or `"treasure"`.
Code that uses the kernel functions directly — à la carte, outside of a running game —
passes an explicit stream into each function call. A [`GameSession`][osrlib.crawl.session.GameSession]
does this wiring for you: it owns an `RngStreams` container built from the session's
master seed and hands out the correctly named stream wherever a kernel function needs
one, so ordinary gameplay never requires touching a stream directly.

There are 13 named streams in total. The table below lists each one; the sections that
follow give more detail on what each stream governs.

| Stream key | Constant | Governs |
| --- | --- | --- |
| `"character_creation"` | [`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM] | Character creation: ability scores, first-level hit points, starting gold. |
| `"advancement"` | [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM] | In-play advancement: level-up hit point rolls. |
| `"combat"` | [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM] | Battle resolution: attack rolls, damage rolls, saving throws, morale checks, initiative, reaction rolls. |
| `"effects"` | [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM] | Effect-internal draws: onset and duration dice, revival delays. |
| `"monster_spawn"` | [`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM] | Monster spawning: hit point rolls when a monster instance is created. |
| `"npc_party"` | [`NPC_PARTY_STREAM`][osrlib.core.npc.NPC_PARTY_STREAM] | NPC adventuring-party generation: composition, class and level, ability scores, hit points, spell picks. |
| `"magic"` | [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM] | Spell resolution: targeting, damage, cast-time forced saves, dispel survival rolls, turning undead. |
| `"treasure"` | [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM] | Treasure generation: presence rolls, quantity dice, per-item resolution, magic item rolls. |
| `"wandering"` | [`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM] | Wandering monsters: the wandering check, group counts, variant picks. |
| `"encounter"` | [`ENCOUNTER_STREAM`][osrlib.crawl.session.ENCOUNTER_STREAM] | Encounter setup: surprise, distance, reaction rolls, distraction, and group counts for monsters or NPC parties spawned by command. |
| `"exploration"` | [`EXPLORATION_STREAM`][osrlib.crawl.session.EXPLORATION_STREAM] | Exploration: forcing doors, listening, searching, traps, tinder, and thief skill checks. |
| `"monster_action"` | [`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM] | Monster action policy during battle: which action a monster group takes each round and which target it picks. |
| `"adjudication"` | [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM] | The referee's ad-hoc adjudication rolls: freeform dice commanded through the seeded session. |

## Character streams

Character creation and in-play advancement are split into two separate streams so
that changing one never disturbs the other's draws.

The [`CHARACTER_CREATION_STREAM`][osrlib.core.character.CHARACTER_CREATION_STREAM]
(key `"character_creation"`) covers one-time creation draws: rolling ability scores,
first-level hit points, and starting gold.

The [`ADVANCEMENT_STREAM`][osrlib.core.character.ADVANCEMENT_STREAM] (key
`"advancement"`) covers hit point rolls when a character levels up during play.

## Combat and effects streams

The [`COMBAT_STREAM`][osrlib.core.combat.COMBAT_STREAM] (key `"combat"`) covers the
battle-resolution kernel: attack rolls, damage rolls, saving throws, morale checks,
initiative, and reaction rolls made in combat.

The [`EFFECTS_STREAM`][osrlib.core.effects.EFFECTS_STREAM] (key `"effects"`) is
separate from combat and covers draws internal to the effects engine: rolled onset
and duration dice for conditions such as poison or paralysis, and revival delays.
Keeping this on its own stream means a change to how battle resolves never shifts
which round an effect ticks or expires.

## Monster and NPC streams

The [`MONSTER_SPAWN_STREAM`][osrlib.core.monsters.MONSTER_SPAWN_STREAM] (key
`"monster_spawn"`) covers hit point rolls at the moment a monster instance is spawned
from its template.

The [`NPC_PARTY_STREAM`][osrlib.core.npc.NPC_PARTY_STREAM] (key `"npc_party"`) covers
generating an NPC adventuring party through the same character-creation kernel player
characters use: party composition, each member's class and level, ability scores,
hit points, and spell picks. An NPC party's treasure and any Expert-class magic items
are generated on the treasure stream instead, since they are treasure procedures.

## Magic stream

The [`MAGIC_STREAM`][osrlib.core.spells.MAGIC_STREAM] (key `"magic"`) covers every
draw made while resolving a spell: targeting, damage dice, touch-attack rolls,
cast-time forced saving throws, dispel-survival rolls, and both rolls involved in
turning undead. Durations rolled when an effect attaches, and saves rolled later when
that effect ticks (such as a charm spell's periodic re-save), draw from the effects
stream instead, following the same convention as the rest of the effects engine.

## Treasure stream

The [`TREASURE_STREAM`][osrlib.core.treasure.TREASURE_STREAM] (key `"treasure"`)
covers treasure generation end to end, in the order a treasure entry is printed:
whether a treasure type is present at all, how much of it there is, and the
resolution of each individual coin hoard, gem, piece of jewellery, or magic item.

## Crawl-session streams

Five streams belong to the dungeon-crawl loop and are keyed on
[`GameSession`][osrlib.crawl.session.GameSession] rather than a kernel module, since
they only make sense in the context of a running session.

The [`WANDERING_STREAM`][osrlib.crawl.session.WANDERING_STREAM] (key `"wandering"`)
covers the wandering-monster check itself, how many monsters appear, and which
variant appears when a wandering table entry has more than one.

The [`ENCOUNTER_STREAM`][osrlib.crawl.session.ENCOUNTER_STREAM] (key `"encounter"`)
covers everything that happens once an encounter starts: surprise rolls, the
distance at which the two sides meet, reaction rolls, and mid-encounter distraction
checks. It also covers the group-size roll when a monster group or NPC party is
spawned directly by command with dice rather than a fixed count.

The [`EXPLORATION_STREAM`][osrlib.crawl.session.EXPLORATION_STREAM] (key
`"exploration"`) covers the rest of dungeon exploration: forcing doors, listening at
doors, searching for secret doors or traps, disarming or triggering traps, lighting
tinder, and thief skill checks such as picking locks.

The [`MONSTER_ACTION_STREAM`][osrlib.crawl.session.MONSTER_ACTION_STREAM] (key
`"monster_action"`) covers the action policy that drives monster groups during
battle: which action a group takes on its turn and which target it selects. Keeping
this on its own stream means swapping in a different monster action policy never
shifts the combat stream's attack and damage rolls.

The [`ADJUDICATION_STREAM`][osrlib.crawl.session.ADJUDICATION_STREAM] (key
`"adjudication"`) covers the referee's ad-hoc rolls for freeform adjudication —
dice commanded through the seeded session to resolve a chance outcome the content
model can't express, such as whether a frayed rope holds. Keeping these on their own
stream means an ad-hoc referee roll never perturbs the draw sequence of a keyed
mechanic, so the outcomes of encounters, combat, and exploration stay fixed under
replay no matter how many adjudication rolls the referee interleaves.

## Stream independence in practice

The following example creates two independent `RngStreams` containers from the same
master seed and shows that the `"combat"` stream produces the same sequence in both,
even after one of the containers draws from an unrelated `"treasure"` stream:

```python
from osrlib.core.rng import RngStreams

streams_a = RngStreams(master_seed=42)
streams_b = RngStreams(master_seed=42)

combat_a = streams_a.get("combat")
combat_b = streams_b.get("combat")

# Same master seed and same stream key always yield the same draw sequence.
assert combat_a.next_uint64() == combat_b.next_uint64()

# Drawing from an unrelated stream doesn't perturb the combat stream's sequence.
streams_a.get("treasure").next_uint64()
assert combat_a.next_uint64() == combat_b.next_uint64()
```
