# Ruleset options

osrlib plays OSE B/X rules-as-written by default. The one place the engine lets a game
deliberately choose something other than the baseline is [`Ruleset`][osrlib.core.ruleset.Ruleset]:
a frozen model of named `bool` and enum flags that a session reads at resolution time. You build
one — or accept the all-defaults `Ruleset()` — when you create a session, and it stays fixed for
that session's whole life: it serializes into saves and replays exactly as authored, and the model
rejects any field it doesn't recognize, so a mistyped flag name fails loudly instead of silently
doing nothing.

Every flag falls into one of two families. Seven are OSE's own optional rules — printed
alternatives the SRD offers alongside its default procedure, such as rolling initiative
individually instead of by side. The remaining five are documented adaptations: defaults osrlib
supplies for places where the tabletop game explicitly hands a decision to a human referee, and a
computer running the rules unattended needs *something* concrete to do instead. Every one of
those five has a full entry — including the reasoning behind its default and the exact mechanical
behavior it turns on — in **the adaptations register** (../adaptations.md); this page is the quick
reference, that page is the source of truth.

## Quick reference

| Flag | Type, default | What it governs |
| --- | --- | --- |
| `hp_reroll_at_first_level` | `bool`, `False` | Reroll a first-level hit die that shows 1–2. |
| `encumbrance` | `EncumbranceMode`, `BASIC` | Which system tracks carried weight and sets movement rate. |
| `variable_weapon_damage` | `bool`, `True` | Each weapon/gear facet rolls its own damage die vs. a flat 1d6. |
| `individual_initiative` | `bool`, `False` | Roll initiative per participant instead of per side. |
| `thac0_arithmetic` | `bool`, `False` | Compute attack rolls by subtraction instead of matrix lookup. |
| `weapon_reload` | `bool`, `False` | A reload-quality weapon can't fire two rounds running. |
| `hd5_counts_as_magical` | `bool`, `False` | 5+ HD (and other gate-bypassing) monsters count as magical. |
| `magic_item_death_save` | `bool`, `True` | A dead character's magic items get a save against destruction. |
| `xp_award_timing` | `XpAwardTiming`, `ON_RETURN` | XP pays out on return to town, or immediately as it's earned. |
| `deprivation_penalties` | `bool`, `False` | Attach mechanical penalties to hunger and thirst. |
| `aoe_friendly_fire` | `bool`, `True` | An area effect at melee range can catch the party's front rank. |
| `formation_width_limit` | `bool`, `True` | Cap how many combatants can fight in the same rank at once. |

## SRD optional rules

These seven mirror an alternative procedure OSE prints alongside its default; osrlib picks the
book's own default for each, off or on, and implements both sides of the switch.

**`hp_reroll_at_first_level`** — off, a first-level character's starting hit-point roll stands as
rolled. On, a raw die (before the CON modifier) that shows 1 or 2 rerolls, repeatedly, until it
shows 3 or higher.

**`variable_weapon_damage`** — on by default, every weapon and every gear item with a combat use
(a torch, a flask of holy water) rolls its own listed damage die. Off switches to the alternate
combat system's flat 1d6 for every weapon and gear facet; unarmed strikes still roll 1d2 regardless,
and monster damage is never affected by this flag either way.

**`individual_initiative`** — off, one initiative roll resolves an entire side for the round. On,
every participant rolls their own 1d6, DEX-modified for characters (plus a halfling's initiative
bonus), with monsters taking a caller-supplied modifier.

**`thac0_arithmetic`** — off, an attack's target number comes from the printed THAC0-vs-AC matrix.
On, it comes from subtracting the defender's AC from the attacker's THAC0 directly, with no
clamping. The two presentations agree everywhere the matrix's own upper and lower bounds line up
with plain subtraction, and diverge only once modifiers push a result past those printed edges.

**`weapon_reload`** — off, any weapon can fire every round. On, a reload-quality weapon (mainly
crossbows) is rejected from firing two rounds in a row, when the caller's combat context says it
fired last round.

**`hd5_counts_as_magical`** — off, only weapons and effects explicitly flagged magical get past a
silver-or-magic weapon gate. On, a monster of 5 or more Hit Dice — or any monster that already
bypasses such a gate for some other reason — counts as magical enough to bypass one too.

**`encumbrance`** sits partway between the two families: it's not a toggle but a choice of *which*
printed system applies, as an [`EncumbranceMode`][osrlib.core.ruleset.EncumbranceMode]. `NONE`
tracks nothing and every character moves at the base 120'/turn. `BASIC` (the default) sets movement
rate from worn-armor category — unarmored, light, or heavy — adjusted down further when the
character is carrying a significant amount of treasure, a call the game makes explicitly per
character. `DETAILED` instead totals tracked coin-weight (armor, gear, and treasure) and sets
movement rate from banded weight thresholds. Both tracking modes share the same maximum-load cap:
tracked weight above it means the character cannot move at all, in either mode.

## Documented adaptations

These five exist because OSE's printed text hands the referee an open-ended judgment call, and a
session running without one needs a fixed, printed answer. Read
[the adaptations register](../adaptations.md) for the reasoning behind each default; here's what
each one does.

**`magic_item_death_save`** (default on) — when a character's death also destroys equipment
(lightning, disintegration, and similar sources), each of that character's magic items rolls its
own saving throw — the dead owner's save value for the source's category, improved by the item's
best combat bonus — rather than being destroyed along with its owner automatically. Survivors land
in a drop pile at the character's cell instead of vanishing. Off restores OSE's default: a
character's magic items share their owner's fate.

**`xp_award_timing`** (an [`XpAwardTiming`][osrlib.core.ruleset.XpAwardTiming], default
`on_return`) — rules-as-written: monster XP and treasure XP both pay out only once the party gets
back to town, the treasure share computed from the change in the party's carried valuation since
departure. Setting it to `immediate` switches to a continuous-play alternative: monster XP pays
out the moment an encounter ends, treasure XP pays out the moment it's acquired, and the return to
town pays out nothing further — treasure subsequently lost is never clawed back from an award that
already happened.

**`deprivation_penalties`** (default off) — food and water consumption is tracked either way; this
flag attaches the mechanical bite OSE leaves to referee discretion. On, going a full day without
food or water applies an attack penalty and doubles how often fatigue sets in; a second day also
halves movement; from the third day on, hit points drain on a recurring basis. Hunger and thirst
never stack against each other — whichever track is worse is the one that applies.

**`aoe_friendly_fire`** (default on) — an area effect (a fireball, a dragon's breath) that lands on
a monster group already fighting the party at melee range can catch the party's own engaged front
rank in the blast, alongside the monsters. Off keeps party members out of every area effect's
candidate list, full stop.

**`formation_width_limit`** (default on) — caps how many combatants can fight in the same rank at
once: three abreast inside a keyed room or cave, two inside a bare corridor cell, following OSE's
own note about how many characters a 10-foot passage holds side by side. The same cap bounds how
much of an area effect's footprint a formation absorbs. Off removes the cap entirely — every
combatant in the front rank fights, and an area effect's footprint is unbounded by formation width.

## Constructing a Ruleset

Building one is just constructing the model with the flags you want to change; every field you
omit takes OSE's own default. The encumbrance flag is one of the few with an easy, visible
before/after: switching it changes a character's
[`movement_rate`][osrlib.core.character.Character.movement_rate] directly, with nothing else in
play needing to change.

```python
from pydantic import ValidationError

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.items import ItemInstance, equip
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import EncumbranceMode, Ruleset, XpAwardTiming
from osrlib.data import load_equipment

# Rules-as-written by default: nothing here is a documented adaptation.
default_rules = Ruleset()
assert default_rules.encumbrance is EncumbranceMode.BASIC
assert default_rules.variable_weapon_damage is True
assert default_rules.xp_award_timing is XpAwardTiming.ON_RETURN

creation = RngStreams(master_seed=3).get(CHARACTER_CREATION_STREAM)
fighter = create_character(
    name="Rurik", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=default_rules, stream=creation
).character

# An unarmored fighter moves at the base rate under basic encumbrance.
assert fighter.movement_rate(default_rules) == 120

plate_mail = load_equipment().get("plate_mail")
fighter.inventory.items.append(ItemInstance(template=plate_mail, quantity=1))
equip(fighter.inventory, fighter.definition, fighter.inventory.items[-1])

# Heavy armor caps basic-encumbrance movement at 60 feet per turn.
assert fighter.movement_rate(default_rules) == 60

# `none` turns encumbrance tracking off outright: movement is always the base rate.
unencumbered_rules = Ruleset(encumbrance=EncumbranceMode.NONE)
assert fighter.movement_rate(unencumbered_rules) == 120

# The model is frozen and rejects unknown flags outright.
try:
    Ruleset(nonexistent_flag=True)
except ValidationError:
    pass
else:
    raise AssertionError("Ruleset should reject an unknown flag")
```

## Where next

- [The adaptations register](../adaptations.md) — the reasoning and rule text behind every
  documented adaptation, plus the settled readings of ambiguous SRD text that apply regardless of
  any flag.
- [Listeners and flags](listeners-and-flags.md) — the game-defined state a `Ruleset` doesn't cover:
  quests, triggers, and other content-specific logic.
- [Sessions, commands, and events](sessions-commands-events.md) — how a `Ruleset` reaches a running
  session and stays fixed for its lifetime.
