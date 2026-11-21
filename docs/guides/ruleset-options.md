# Ruleset options

Configure optional B/X rules and referee adaptations by constructing a [`Ruleset`][osrlib.core.ruleset.Ruleset] and passing it to [`GameSession.new`][osrlib.crawl.session.GameSession.new] or to standalone `osrlib.core` functions. `Ruleset` is frozen (`extra="forbid"`), persists in saves and replays, and raises Pydantic's `ValidationError` if you pass an unrecognized flag name.

## Construct a `Ruleset`

Call `Ruleset()` with no arguments for the default B/X configuration, or override specific flags such as [`EncumbranceMode`][osrlib.core.ruleset.EncumbranceMode] to change how functions such as [`Character.movement_rate`][osrlib.core.character.Character.movement_rate] behave:

```python
from pydantic import ValidationError

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.items import ItemInstance, equip
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import EncumbranceMode, Ruleset, XpAwardTiming
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment

# Default configuration: standard B/X options plus osrlib's default adaptations.
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

# `none` disables encumbrance tracking, and passing `ruleset` fixes it for a session.
unencumbered_rules = Ruleset(encumbrance=EncumbranceMode.NONE)
assert fighter.movement_rate(unencumbered_rules) == 120

crypt = DungeonSpec(id="crypt", name="The Crypt", levels=(LevelSpec(number=1, width=1, height=1, entrance=(0, 0)),))
adventure = Adventure(name="A Delve", town=TownSpec(name="Threshold"), dungeons=(crypt,))
session = GameSession.new(Party(members=[fighter]), adventure, seed=3, ruleset=unencumbered_rules)
assert session.ruleset is unencumbered_rules

# The model is frozen and rejects unknown flags outright.
try:
    Ruleset(nonexistent_flag=True)
except ValidationError:
    pass
else:
    raise AssertionError("Ruleset should reject an unknown flag")
```

## Quick reference

Every `Ruleset` field falls into one of two categories: optional rules printed in the OSE SRD alongside standard B/X procedures, or documented adaptations that supply a deterministic rule where tabletop B/X leaves a judgment call to a human referee (see [the adaptations register](../adaptations.md)):

| Flag | Kind | Type and default | Effect |
| --- | --- | --- | --- |
| `hp_reroll_at_first_level` | Optional rule | `bool`, `False` | Reroll a 1st-level Hit Die when the raw roll is 1 or 2. |
| `encumbrance` | Optional rule | `EncumbranceMode`, `BASIC` | Select which encumbrance rule tracks carried weight and movement rate. |
| `variable_weapon_damage` | Optional rule | `bool`, `True` | Roll each weapon or combat-gear item's listed damage die instead of a flat `1d6`. |
| `individual_initiative` | Optional rule | `bool`, `False` | Roll `1d6` initiative per combatant instead of once per side. |
| `thac0_arithmetic` | Optional rule | `bool`, `False` | Compute target numbers as `THAC0 - AC` instead of looking up the attack matrix. |
| `weapon_reload` | Optional rule | `bool`, `False` | Prevent `reload`-quality weapons from firing in two consecutive rounds. |
| `hd5_counts_as_magical` | Optional rule | `bool`, `False` | Allow monsters of 5+ HD (or with silver/magic immunity) to harm silver/magic-immune targets. |
| `magic_item_death_save` | Adaptation | `bool`, `True` | Roll saving throws for a dead character's magic items against equipment-destroying deaths. |
| `xp_award_timing` | Adaptation | `XpAwardTiming`, `ON_RETURN` | Award XP upon returning to town (`ON_RETURN`) or immediately when earned (`IMMEDIATE`). |
| `deprivation_penalties` | Adaptation | `bool`, `False` | Apply attack, fatigue, movement, and hit-point penalties for going without food or water. |
| `aoe_friendly_fire` | Adaptation | `bool`, `True` | Include engaged front-rank party members in area effects targeted at melee range. |
| `formation_width_limit` | Adaptation | `bool`, `True` | Limit melee front-rank width for both sides based on room or corridor frontage. |
| `failed_trap_removal_springs` | Adaptation | `bool`, `True` | Spring a treasure trap immediately when a thief fails [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap]. |

## Optional B/X rules

These options toggle between standard and optional procedures printed in the OSE SRD:

- **`hp_reroll_at_first_level`** (`False` by default): When `False`, a 1st-level character's initial Hit Die roll stands as rolled. When `True`, osrlib rerolls the raw die (before adding the CON modifier) until it shows 3 or higher.
- **`encumbrance`** ([`EncumbranceMode.BASIC`][osrlib.core.ruleset.EncumbranceMode] by default): Selects how carried items affect movement rate. `NONE` disables tracking so characters move at the base 120'/turn regardless of load. `BASIC` sets movement rate from worn armour category (unarmoured, light, or heavy), stepped down when [`Character.carrying_treasure`][osrlib.core.character.Character.carrying_treasure] is `True`. `DETAILED` sums the coin weight of armour, gear, and treasure and sets movement rate from weight bands. Both `BASIC` and `DETAILED` enforce the same maximum coin-weight cap, immobilizing characters who exceed it.
- **`variable_weapon_damage`** (`True` by default): When `True`, weapons and combat-usable gear (such as torches or holy water) roll their listed damage dice. When `False`, weapons and combat gear deal `1d6` (while unarmed strikes still deal `1d2` and monsters still roll their listed damage).
- **`individual_initiative`** (`False` by default): When `False`, each side rolls `1d6` once per round. When `True`, each combatant rolls `1d6` individually, adding DEX modifiers for characters, the class initiative bonus for halflings, and any caller-supplied modifier for monsters.
- **`thac0_arithmetic`** (`False` by default): When `False`, attack target numbers come from the printed B/X attack matrix. When `True`, target numbers equal `THAC0 - AC` without clamping, differing from the matrix only when modifiers push `THAC0 - AC` outside the matrix's `2-20` bounds.
- **`weapon_reload`** (`False` by default): When `False`, missile weapons can fire every round. When `True`, attack validation rejects firing a `reload`-quality weapon (such as a crossbow) if the combat context records that it fired on the previous round.
- **`hd5_counts_as_magical`** (`False` by default): When `False`, only magic or silver weapons and effects harm creatures immune to mundane weapons. When `True`, attacking monsters with 5 or more remaining Hit Dice (or monsters that themselves require silver or magic weapons to harm) can also hit targets protected by silver/magic immunity.

## Documented adaptations

These options govern rules where tabletop B/X leaves the outcome to referee judgment (see [the adaptations register](../adaptations.md) for the rationale behind each default):

- **`magic_item_death_save`** (`True` by default): When `True` and a character dies to an equipment-destroying effect (such as lightning or disintegration), each carried magic item rolls a saving throw using the owner's save target for that category improved by the item's best combat bonus. Surviving items drop into a pile on the character's cell. When `False`, destructive deaths destroy all carried magic items automatically.
- **`xp_award_timing`** ([`XpAwardTiming.ON_RETURN`][osrlib.core.ruleset.XpAwardTiming] by default): With `ON_RETURN`, monster and treasure XP are awarded only when survivors return to town, calculated from defeated monsters and net recovered treasure value. With `IMMEDIATE` (designed for continuous delves without town trips), monster XP pays out when an encounter ends and treasure XP pays out on acquisition. Returning to town awards nothing extra, and losing treasure later does not revoke earned XP.
- **`deprivation_penalties`** (`False` by default): osrlib tracks daily food and water consumption regardless of this setting. When `True`, going one full day without food or water applies `-1` to attack rolls and halves the rest interval before fatigue sets in (from 6 turns to 3). A second day also halves movement rate, and from the third day onward, the character also loses `1d4` hit points per day. Hunger and thirst do not stack, so only the further-advanced track applies.
- **`aoe_friendly_fire`** (`True` by default): When `True`, an area effect (such as *fireball* or dragon breath) aimed at a monster group engaged in melee with the party can also hit party members in that group's front rank. When `False`, area effects aimed at monsters never target party members.
- **`formation_width_limit`** (`True` by default): When `True`, osrlib caps front-rank width at 5 feet per combatant across the widest unbroken square of floor around the party (2 abreast in a 10-foot corridor, 4 in a `2x2`-cell room, 8 in a `4x4`-cell room). Only a monster group's front rank (up to `min(count, formation_width)`) can make melee attacks, and formation width bounds how much of an area effect's footprint hits a rank. When `False`, all party members within 5 feet of a group and all living monsters in that group can fight in melee simultaneously.
- **`failed_trap_removal_springs`** (`True` by default): When `True`, a thief who fails [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap] immediately springs the trap on themselves. When `False`, a failed attempt emits [`TrapEvent`][osrlib.crawl.events.TrapEvent] (`exploration.trap.removal_failed`) and leaves the discovered trap armed: that thief cannot retry, another thief may try, and taking the treasure still has a 2-in-6 chance to spring the trap.

## Where next

- [The adaptations register](../adaptations.md) - rule citations and design rationale for each documented adaptation and settled SRD ambiguity.
- [Listeners and flags](listeners-and-flags.md) - custom game state in `session.flags` and event listeners.
- [Gates, triggers, and quests](gates-triggers-quests.md) - adventure-authored behavior on doors, transitions, triggers, and quests.
- [Sessions, commands, and events](sessions-commands-events.md) - passing a `Ruleset` into `GameSession.new`.
