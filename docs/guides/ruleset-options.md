# Ruleset options

osrlib plays OSE B/X rules-as-written by default. The only place a game can choose rules other than the baseline is [`Ruleset`][osrlib.core.ruleset.Ruleset], a frozen model of named `bool` and enum flags that a session reads when it resolves an action. You build one when you create a session, or you use the default `Ruleset()`. It stays fixed for the life of the session, and saves and replays contain it exactly as you built it. The model rejects any field it doesn't recognize, so a mistyped flag name raises a `ValidationError` instead of doing nothing.

Every flag belongs to one of two families. Some are OSE's optional rules: printed alternatives the SRD offers alongside its default procedure, like rolling initiative individually instead of by side. The rest are documented adaptations: defaults osrlib supplies where the tabletop game leaves a decision to a human referee, and a program running the rules with no referee needs a fixed answer. Each adaptation has a full entry in [the adaptations register](../adaptations.md), which is the authority for the reasoning behind its default and for the behavior it turns on.

## Quick reference

| Flag | Type, default | What it governs |
| --- | --- | --- |
| `hp_reroll_at_first_level` | `bool`, `False` | Reroll a first-level hit die that shows 1-2. |
| `encumbrance` | `EncumbranceMode`, `BASIC` | Which system tracks carried weight and sets movement rate. |
| `variable_weapon_damage` | `bool`, `True` | Each weapon and gear facet rolls the damage die listed for it instead of a flat 1d6. |
| `individual_initiative` | `bool`, `False` | Roll initiative per participant instead of per side. |
| `thac0_arithmetic` | `bool`, `False` | Compute attack rolls by subtraction instead of matrix lookup. |
| `weapon_reload` | `bool`, `False` | A reload-quality weapon can't fire in two consecutive rounds. |
| `hd5_counts_as_magical` | `bool`, `False` | Monsters of 5 or more HD, or that only silver or magic weapons can harm, count as magical. |
| `magic_item_death_save` | `bool`, `True` | A dead character's magic items get a save against destruction. |
| `xp_award_timing` | `XpAwardTiming`, `ON_RETURN` | XP pays out on return to town, or immediately as it's earned. |
| `deprivation_penalties` | `bool`, `False` | Attach mechanical penalties to hunger and thirst. |
| `aoe_friendly_fire` | `bool`, `True` | An area effect at melee range can catch the party's front rank. |
| `formation_width_limit` | `bool`, `True` | Cap the front rank at how many fit side by side in the space where the party fights. |

## SRD optional rules

Each of these mirrors an alternative procedure OSE prints alongside its default. osrlib uses the book's default for each, off or on, and implements both sides of the switch.

**`hp_reroll_at_first_level`**: off, a first-level character's starting hit-point roll stands as rolled. On, osrlib rerolls the die while the raw result (before the CON modifier) is 1 or 2.

**`variable_weapon_damage`**: on by default, every weapon and every gear item with a combat use (a torch, a flask of holy water) rolls the damage die listed for it. Off switches to the alternate combat system's flat 1d6 for every weapon and gear facet. Unarmed strikes still roll 1d2, and the flag doesn't affect monster damage.

**`individual_initiative`**: off, one initiative roll resolves an entire side for the round. On, each participant rolls 1d6. A character adds their DEX modifier, and a halfling adds their initiative bonus on top. Monsters take a modifier the caller supplies.

**`thac0_arithmetic`**: off, an attack's target number comes from the printed THAC0-vs-AC matrix. On, it comes from subtracting the defender's AC from the attacker's THAC0 directly, with no clamping. The two presentations agree everywhere the matrix's upper and lower bounds line up with plain subtraction, and differ only once modifiers push a result past those bounds.

**`weapon_reload`**: off, any weapon can fire every round. On, the attack validator rejects a shot from a reload-quality weapon (mainly crossbows) when the caller's combat context records that the weapon fired last round.

**`hd5_counts_as_magical`**: off, only weapons and effects explicitly flagged magical can harm a monster that only silver or magic weapons can harm. On, an attacking monster of 5 or more Hit Dice also counts as magical for that purpose, and so does an attacking monster that only silver or magic weapons can harm.

**`encumbrance`** sits partway between the two families. It isn't a toggle. It chooses *which* printed system applies, as an [`EncumbranceMode`][osrlib.core.ruleset.EncumbranceMode]. `NONE` tracks nothing, and every character moves at the base 120'/turn. `BASIC` (the default) sets movement rate from worn-armor category (unarmored, light, or heavy), reduced further when the character carries a significant amount of treasure. Your game decides that for each character by setting its [`carrying_treasure`][osrlib.core.character.Character.carrying_treasure] flag. `DETAILED` instead totals tracked coin-weight (armor, gear, and treasure) and sets movement rate from banded weight thresholds. `BASIC` and `DETAILED` share the same maximum-load cap: a character whose tracked weight goes above it can't move at all.

## Documented adaptations

These flags exist because OSE leaves each of these rules to the referee's judgment, and a session running without a referee needs a fixed answer. For the reasoning behind each default, see [the adaptations register](../adaptations.md).

**`magic_item_death_save`** (default on): when a character's death also destroys equipment (lightning, disintegration, and similar sources), osrlib rolls a saving throw for each of that character's magic items instead of destroying it with its owner. The save value is the dead owner's for the source's category, improved by the item's best combat bonus. Items that survive end up in a drop pile at the character's cell. Off restores OSE's default, where a character's magic items are destroyed along with their owner.

**`xp_award_timing`** (an [`XpAwardTiming`][osrlib.core.ruleset.XpAwardTiming], default `on_return`): rules-as-written, monster XP and treasure XP both pay out only once the party gets back to town, and the treasure share comes from the change in the party's carried valuation since departure. Set it to `immediate` for the continuous-play alternative: monster XP pays out the moment an encounter ends, and treasure XP pays out at each acquisition. The return to town then pays out nothing further, and treasure lost afterward never reduces an award that already happened.

**`deprivation_penalties`** (default off): osrlib tracks food and water consumption whether the flag is on or off. The flag adds the mechanical penalties OSE leaves to referee discretion. On, a full day without food or water applies an attack penalty and doubles how often fatigue sets in. A second day also halves movement. From the third day on, the character loses hit points every day. Hunger and thirst never stack against each other: whichever track is worse is the one that applies.

**`aoe_friendly_fire`** (default on): an area effect (a fireball, a dragon's breath) that lands on a monster group already fighting the party at melee range can catch the party's engaged front rank in the blast, alongside the monsters. Off, area effects never include party members among their targets.

**`formation_width_limit`** (default on): caps how many combatants can fight in the same rank at once, at what actually fits in the space the party stands in. OSE gives one number and leaves the rest to the referee: at most 2-3 fit side by side in a 10-foot passage. osrlib takes the conservative end of that, five feet of frontage each, and measures the widest square of unbroken floor around the party. Two fit in a one-cell passage however far it runs, four in a room two cells square, and eight in a room four cells across. The same cap bounds how much of an area effect's footprint a formation absorbs. Off removes the cap: every combatant in the front rank fights, and formation width no longer bounds an area effect's footprint.

## Constructing a `Ruleset`

To build one, construct the model with the flags you want to change. Every field you omit uses OSE's default. The encumbrance flag has an effect you can see: switching it changes a character's [`movement_rate`][osrlib.core.character.Character.movement_rate] directly, and nothing else in play has to change.

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

- [The adaptations register](../adaptations.md) - the reasoning and rule text behind every documented adaptation, plus the settled readings of ambiguous SRD text that apply regardless of any flag.
- [Listeners and flags](listeners-and-flags.md) - session flags and the listeners your game defines, the game-defined state a `Ruleset` doesn't cover.
- [Gates, triggers, and quests](gates-triggers-quests.md) - authored triggers and quests, the behavior an adventure document defines.
- [Sessions, commands, and events](sessions-commands-events.md) - how a `Ruleset` reaches a running session and stays fixed for its lifetime.
