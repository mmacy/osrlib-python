# Adaptations and interpretations

osrlib follows OSE rules-as-written (RAW). Every place where that isn't enough is recorded here, as one of two kinds of entry:

- **Interpretations**, where the SRD text is ambiguous or silent, and osrlib commits to one reading.
- **Documented adaptations**, where the tabletop game assumes a human referee, and osrlib provides a default behind a `Ruleset` flag.

Look here instead of searching docstrings. Every entry is also stated in the docstring of the member it applies to and locked by a test.

Each entry also cites the source files and test names that lock it. Those citations point into [the osrlib repository](https://github.com/mmacy/osrlib-python), not to pages on this site.

## Terms used here

- **Template and instance** - A template is the frozen catalog entry for a monster or an item, like a [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]. An instance is one copy of it in play, like a [`MonsterInstance`][osrlib.core.monsters.MonsterInstance], with its current hit points and conditions.
- **Effects ledger** - The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] contains every running effect, like a spell, a poison, or a lit torch, and resolves each one as the game clock moves. On this page, "the ledger" means the effects ledger.
- **Draw and stream** - A draw is one random number the engine takes. Every draw comes from a named stream, so one subsystem's rolls never shift another's. A step that "consumes no draw" leaves every later roll as it would have been. See [RNG streams](reference/rng-streams.md).
- **Kernel and session** - The kernel is `osrlib.core`, the rules functions you can call directly (see [Rules without a session](guides/rules-without-a-session.md)). The session is a [`GameSession`][osrlib.crawl.session.GameSession], which runs a whole adventure through commands and calls the kernel for you.
- **Policy** - An [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] chooses what a monster group does each round. The default is [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy], and a game can register a different one.
- **Group** - An [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup] is a group of monsters in an encounter. A group has one distance from the party, checks morale as a whole, and is what a party member's attack aims at.
- **Front rank and back rank** - In battle, the session cuts the living marching order into rows as wide as the formation width. The first row is the front rank, and every member behind it is in the back rank. Melee reaches only the front rank. See [The battle round](#the-battle-round).
- **Player view and referee view** - What a session shows the players and the referee: a [`PlayerView`][osrlib.crawl.views.PlayerView] and a [`RefereeView`][osrlib.crawl.views.RefereeView]. See [Views and visibility](guides/views-and-visibility.md).
- **Keyed encounter** - Monsters that an adventure places in an area in advance, as a [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter]. They spawn when the party enters the area.
- **`manual` ability** - A [`MonsterAbility`][osrlib.core.monsters.MonsterAbility] marked `manual` is text for the referee to read and act on. osrlib doesn't apply it.

## Interpretations

### Multi-prime-requisite XP tiers have no penalties

The SRD applies the standard XP-modifier table "to characters with a single prime requisite", and the elf's and halfling's class descriptions note only bonuses (+5%, +10%). osrlib's reading: elf and halfling get exactly their stated bonus tiers and no penalty tiers. An elf with INT 9 and STR 9 has a 0% modifier, never −10%. Locked by `test_classes.py::TestXpModifier::test_multi_prime_requisite_classes_have_no_penalties`.

### XP tiers evaluate best-first, first-match-wins

The XP-modifier tiers are one uniform representation for all classes: ordered tiers of `{modifier_pct, minimum scores}`, evaluated best-first, and the first tier whose minimums all hold wins. The standard table's penalty rows only work under first-match-wins (a prime requisite of 7 must fall past the +10/+5/none tiers and reach −10%), and the halfling's "at least 13 in one prime requisite" +5% tier is expressed as two single-minimum tiers under the same rule. Locked by `test_classes.py::TestXpModifier`.

### XP percentage results floor

The SRD never says how to round a ±5/10/20% XP modifier. osrlib's reading: the modified award is floored (integer arithmetic, toward negative infinity). Locked by `test_classes.py::TestApplyXp::test_modifier_floors`.

### First-level HP re-roll repeats until the die shows 3 or more

The optional rule says a roll of "1 or 2" may be re-rolled but not whether a re-rolled 1-2 is re-rolled again. osrlib's reading: with `hp_reroll_at_first_level` on, the raw die (before the CON modifier) is re-rolled *while* it shows 1-2, each re-roll consuming a draw. Locked by `test_character.py::TestRollHitPoints`.

### Adjustment reductions are even per lowered score

The SRD says "For every two points by which an ability score is reduced, one point may be added", and doesn't say whether a single score may be lowered by an odd amount as long as the total is even. osrlib's reading: the two-for-one trade is per score, so each lowered score drops by an even amount and no point of a reduction goes unused. The total raise must equal exactly the sum of reductions divided by two. Locked by `test_abilities.py::TestAdjustment`.

### Gear combat facets are exempt from class weapon policies

Torch, holy water, and burning oil appear on both the weapon table and the gear list. They compile as gear with an embedded combat facet (one entry per physical item, no item has two ids). Class weapon policies govern the weapons list only: a strict quality-tag reading would forbid a cleric holy water or a torch. One uniform rule applies instead: any class may buy, hold, and use all three. That grants more than a strict reading would, and on purpose: a magic-user (RAW: dagger only) may also throw oil or swing a torch. Locked by `test_items.py::TestEquipLegality`.

### Basic encumbrance tracks treasure weight against the general 1,600-coin cap

Under basic and detailed encumbrance alike, a character whose tracked weight is above 1,600 coins can't move (movement 0). The one exception is a character who wears gauntlets of ogre power under detailed encumbrance, described in [the next entry](#gauntlets-of-ogre-power-raise-every-detailed-encumbrance-band).

The SRD states the maximum load rule in the general encumbrance text: "The maximum load any character can carry is 1,600 coins of weight". Basic encumbrance says "The weight of treasure carried is tracked to make sure that the character's maximum load is not exceeded." So osrlib reads the cap as general, not as an extra that only detailed encumbrance has.

Whether a character is "carrying a significant amount of treasure" under basic encumbrance stays a referee judgment. The game sets the plain `carrying_treasure` boolean, and osrlib invents no weight threshold for it. Locked by `test_items.py::TestMovementRates` and the encumbrance property tests.

### Gauntlets of ogre power raise every detailed encumbrance band

Under detailed encumbrance, a character who wears gauntlets of ogre power adds the template's `max_load_bonus_coins`, 1,000 coins, to every movement band and to the maximum load. Movement rates are in feet per turn, with the encounter rate per round in parentheses, as the SRD prints them:

| Movement rate | Without the gauntlets | Wearing the gauntlets |
| --- | --- | --- |
| 120' (40') | Up to 400 coins | Up to 1,400 coins |
| 90' (30') | Up to 600 coins | Up to 1,600 coins |
| 60' (20') | Up to 800 coins | Up to 1,800 coins |
| 30' (10') | Up to 1,600 coins | Up to 2,600 coins |

When the engine spreads a recovered haul across the party, it loads the wearer up to 2,600 coins.

The reason is the gauntlets' page: "The character's ability to carry weight is increased by 1,000 coins. (i.e. the character can carry 1,400 coins without becoming encumbered.)" The 1,400 is the first detailed band, 400 coins, plus the bonus. The page's encumbrance bullet also refers to the detailed rules.

Basic encumbrance doesn't change. Its movement rates depend on armour, not weight, and its cap stays at 1,600 coins. Gauntlets that a character carries but doesn't wear add nothing. Locked by `test_item_rules.py::TestGauntletsOfOgrePower`.

### Miscellaneous gear is a flat 80 coins under detailed encumbrance

The SRD gives adventuring gear no per-item weights and says gear "may be counted as 80 coins of weight". osrlib's reading: any miscellaneous gear carried adds a flat 80 coins, once, regardless of how much gear it is. Items with a listed weight of `-` (holy water, burning oil) have no separate tracked weight. They are gear, covered by the flat 80. Locked by `test_items.py::TestWeights`.

### Ammunition weighs 0

The ammunition table has no weight column, and the SRD states "The listed weight of missile weapons already includes the weight of the ammunition and its container." osrlib's reading: ammunition compiles with weight 0. Locked by `test_srd_data.py::TestEquipmentData`.

### Sling stones cost 0, lot size 1

The sling stones cost cell reads `Free`. osrlib's reading: cost 0, purchase lot 1. Locked by `test_srd_data.py::TestEquipmentData`.

### INT-granted languages come from the Other Languages table and may not duplicate natives

The SRD says high-INT characters "may also choose additional languages from the list of languages available in the setting" (at the referee's discretion). osrlib's reading: choices come from the twenty Other Languages, and a choice may not duplicate a class native, so a dwarf cannot spend an INT language on Dwarvish. Locked by `test_character.py::TestExtraLanguages`.

### The attack matrix is clamp(THAC0 − AC, 2, 20) and extends beyond the printed columns

Every printed matrix cell equals `clamp(THAC0 − AC, 2, 20)`, locked by a property test over the compiled table, and AC values outside the printed −3..9 columns extend by the same formula: the printed bounds are a limit of the page layout, not of the rules. The clamping is what distinguishes matrix mode from the `thac0_arithmetic` flag once modifiers push totals past the plateaus. Locked by `test_monster_data.py::TestCombatTables` and `test_combat.py::TestAttackRoll::test_matrix_versus_arithmetic_divergence_at_the_plateaus`.

### Initiative ties re-roll

RAW offers "re-roll or resolve simultaneously". Simultaneous resolution is a different combat model the engine doesn't attempt. osrlib's reading: tied sides, and tied individuals among themselves, re-roll in stable input order until distinct, each re-roll consuming draws. Locked by `test_combat.py::TestInitiative`.

### Slow actors act after all non-slow actors

osrlib reads "always act last, as if they had lost initiative" this way for the general case: slow-weapon actors act after every non-slow actor, ordered among themselves by their side's initiative (each actor's result under individual initiative), then stable order. Locked by `test_combat.py::TestInitiative::test_slow_actors_act_last_by_side_initiative`.

### Helpless defenders are hit automatically in melee, consuming no attack draw

A melee attack on a paralysed, sleeping, or petrified defender hits automatically. The engine makes no attack roll, so it consumes no draw, and rolls only damage. A missile attack on the same defender rolls to hit as usual.

- The rule is RAW: "Helpless opponents, such as those magically paralysed or frozen, can be automatically hit in melee".
- A sleeping creature can't defend itself, so osrlib counts it as helpless too. For what else sleep does in combat, see [Asleep grants auto-hit and dies-to-a-blade](#asleep-grants-auto-hit-and-dies-to-a-blade-dozing-is-a-caller-modifier).
- A petrified creature is frozen in stone, so it counts as helpless ([#166](https://github.com/mmacy/osrlib-python/issues/166)).

A defender whose AC is `No hit roll required`, like green slime or yellow mould, is also hit without a roll. That applies to missile attacks too. Locked by `test_combat.py::TestAttackRoll::test_helpless_defender_auto_hit_consumes_no_draw` and `test_rules_calls.py::TestMeleeHitsAPetrifiedCreature`.

### Morale "incapacitated" means dead, paralysed, petrified, or asleep

The RAW trigger text says "slain, paralysed, etc". osrlib uses this reading for the morale trigger that fires when half the side is incapacitated, and for the rule that a sleeping creature dies to a blade. Situational morale adjustments clamp to ±2 per RAW and never apply to ML 2 or less (never fights) or ML 12 or more (never checks). Two passed checks mean no further checks. Locked by `test_combat.py::TestMorale`.

### Energy drain reverses level_up

Energy drain takes levels from a character, or Hit Dice from a monster, by reversing what gaining them did.

For a character, each drained level undoes `level_up`:

- Above name level, the character loses the flat hit point bonus that level gave, with no roll and no CON modifier.
- Otherwise, the engine rolls the class hit die plus the CON modifier (minimum 1 per die) and subtracts the result. This is the RAW-faithful reading of "loses one Hit Die of hit points", and it means a character needs no record of the hit points each level gave.
- While the character keeps at least one level, maximum and current hit points don't drop below 1. A character dies of drain only by losing the last level.
- The drained XP depends on the `xp_policy` param of the monster's `energy_drain` ability. A wight's `"halfway"` leaves the character halfway between the XP threshold of the old level and that of the new level, rounded down: a fighter drained from level 3 (4,000 XP) to level 2 (2,000 XP) is left with 3,000 XP. The `"level_minimum"` of a wraith, spectre, or vampire leaves the character at exactly the new level's threshold.
- A drain of two levels, like a spectre's, applies the procedure twice and sets XP once.

For a monster, each drained Hit Die costs a rolled d8 of hit points, and its THAC0 and saves become those of the reduced Hit Dice. A monster drained below 1 HD dies.

The wight's page says a drain takes "all other benefits due to the drained level". So wherever a rule measures a creature by its Hit Dice, a drained monster counts the Hit Dice it has left, plus its template's modifier:

- *Sleep* counts them against its Hit Dice budget, and to decide whether the monster is a 4+1 creature.
- *Confusion* checks them to decide whether a subject of 2+1 HD or greater re-saves.
- A protection scroll's ward counts the monster in its 1-3, 4-5, or 6+ HD band.
- The `hd5_counts_as_magical` optional rule checks them for "a monster with 5 HD or greater".

Two things stay with the template. The turning column does, because the turning table reads "Hit Dice of Monster Type" (see [The turning column](#the-turning-column-is-the-hd-count-2-only-at-count-2-counts-above-9-unturnable)). The XP value does too, because a stat block's XP is "Pre-calculated XP award for defeating the monster".

The drain event has a field for the spawn consequence, like rising as a wight in 1d4 days. The engine doesn't act on it: the kernel kills the victim, and the game narrates what the victim becomes. The SRD gives the consequence for "A person drained of all levels", so only a `combat.drain.slain` event for a character sets the field. When the character survives the drain, the field is `None`.

The event's other fields, for anyone reading it:

- `levels_lost` counts the level that killed. A spectre drains two levels, so when it drains a level-2 fighter, the fighter loses level 2, then dies losing level 1, and the event reports 2.
- `hp_lost` is the drop in maximum hit points: the maximum before the drain minus the maximum after. Because of the floor of 1, it can be smaller than the hit points rolled for the lost levels.

Locked by `test_drain.py`, `test_turning_and_staff.py`, `test_drain_and_regen.py::TestSleepOnDrainedMonsters`, and `test_drain_and_area.py`.

### The damage pipeline order

The gate resolves first, then the damage roll (plus STR for melee), then the doublings (brace, charge, back-stab), then the minimum of 1, then the reductions (floored, never below 1). If the defender's `harmed_only_by` or energy defenses exclude the source, no damage is rolled and the event reports it. Save-for-half halving also floors. Back-stab adds +4 to hit and doubles damage, both halves from the thief tag's params, when the caller asserts an unaware target attacked from behind. Locked by `test_combat.py::TestDamagePipeline`.

### The WIS save modifier applies to magical non-breath saves only

osrlib reads "does not normally include saves against breath attacks" this way. The caller supplies any further referee discretion as a modifier. Locked by `test_combat.py::TestSavingThrows`.

### `hd5_counts_as_magical` implements the whole invulnerabilities optional rule

The flag's name covers only the 5+ HD half of the rule, but the flag governs both bullets of the SRD optional rule it cites. A monster of 5+ HD *and* another invulnerable monster therefore bypass the gate, because implementing half an optional rule would be an undocumented deviation. The boundary follows the rule's wording: the flag touches only weapon-material gates whose keys are a subset of {silver, magic}, and "another invulnerable monster" means a monster bearing such a gate. Element-keyed and mixed gates (the mummy's fire-or-magic, the black pudding's fire-only) are unaffected. Locked by `test_combat.py::TestDamagePipeline`.

### The `holy` damage key is admitted through any gate on undead targets, and only there

"Holy water inflicts damage on undead monsters": the specific rule overrides the general immunity. Otherwise the wight's silver-or-magic gate would absorb the one weapon made for it. Against anything living, the throw resolves normally and the damage pipeline reports no effect. That is deliberately *not* a validator rejection: rejections are free (no roll, no time, no log entry), and a rejection would be a zero-cost undead detector. Locked by `test_combat.py::TestSplashWeapons`.

### Variable weapon damage off means 1d6 for weapons and gear facets

RAW: "PC attacks inflict 1d6 damage", meaning weapons *and* gear combat facets. Unarmed attacks stay 1d2 (the specific unarmed rule, not a weapon) and monster damage is unaffected (monsters "deal the damage indicated in the description"). Locked by `test_combat.py::TestDamagePipeline::test_variable_damage_flag_off`.

### Splash damage is two applications

osrlib reads "Inflicted for two rounds" as two applications: the hit's damage now, and the douse effect's expiry applies the listed damage once more at the next round boundary. Unlit oil deals no damage. The caller may compile it into a location-attached pool (3-foot pool, burns 1 turn once lit, 1d8 to creatures passing through), and the caller asserts who passes through. In a session, the crawl layer anchors the pool to a cell, as [Light, darkness, and location-bound effects](#light-darkness-and-location-bound-effects) describes. `uses_fire` monsters ignore burning oil outright. Locked by `test_combat.py::TestSplashWeapons`.

### Torch and burning oil deal fire damage, and holy water has the holy key

osrlib decides the damage sources of the three dual-listed gear items. A torch or oil hit is fire damage, so it goes into the troll's non-regenerable ledger. Locked by `test_combat.py::TestSplashWeapons::test_douse_applies_twice_then_expires`.

### Breath-weapon daily limits are per-monster data

The three-per-day limit is per-monster data. The SRD prints it for the dragons, the dragon turtle, and the chimera. The hellhound has no daily limit: its 2-in-6 per-round chance is data that the default action policy reads, and the kernel resolves the breath whenever it's invoked. osrlib treats every breath weapon as a destructive death: the SRD's destruction-of-items examples ("a lightning bolt spell or a dragon's breath") illustrate energy deaths generally, so the hellhound's fire kill destroys the victim's mundane equipment too. A dragon's defense against its variant's breath element(s) is immunity to nonmagical sources plus an automatic save against magical ones. Locked by `test_combat.py::TestBreathAndGaze`.

### The HD-budget targeting mode consumes weakest-first

Candidates sort weakest-first by effective HD, with sub-1 HD rounding up to 1, fixed hit-point bonuses dropped, and characters counting their level, ties broken by stable input order. The budget spends whole creatures, and a target whose HD exceed the remainder is skipped while selection continues. For the *sleep* spell's arithmetic, see [Sleep's mode arithmetic](#sleeps-mode-arithmetic). Locked by `test_combat.py::TestTargeting`.

### Monster XP validation: above-21-HD inflation and the negative-modifier band

Printed XP is authoritative and cross-validated at compile time: base by HD row plus asterisks × the bonus column, where above 21 HD *both* amounts first gain 250 per HD above 21. The dragon turtle (HD 30*, XP 9,000) matches this reading. Negative hit-point modifiers map to the *lower* band: the goblin's 1-1 validates against "Less than 1" (XP 5) while still using the unmodified "Up to 1" attack-matrix row (THAC0 19 [0]). The attack-as-1-HD-higher rule is for bonus modifiers only. Table mismatches fail the build unless recorded as overrides. Locked by `test_monster_data.py::TestCombatTables`.

### Poison failure is death, with optional onset; riders leave the damage unaffected

Save versus death or poison, and failure is death, either immediate or an onset-delay effect for forms like the giant rattler's "death in 1d6 turns". Poison riders on damaging attacks leave the damage unaffected by the save, per RAW. Locked by `test_effects.py::TestLedger::test_delayed_death_poison`.

### Petrification suspends the target's other effects

While a target is petrified its other attached effects suspend, with no ticks and durations frozen, so an adventurer who was poisoned before turning to stone is still poisoned after *stone to flesh*. Petrification is permanent but recoverable, because a petrified creature isn't dead. Locked by `test_effects.py::TestLedger::test_petrification_suspends_other_effects`.

### A party turned wholly to stone has fallen

The SRD doesn't say what happens when no adventurer is left but statues, so this is a judgement (pinned). A member stands while they are neither dead nor petrified. When a command's events kill or petrify a member and no member is left standing, the session ends in `game_over` exactly as it does when the last member dies, with `battle.ended.defeat` first in a battle. A statue isn't dead, so it still counts among `Party.living_members()` and keeps its rank, and the check is tied to the killing or petrifying event, not to the party's state. So a stone party carried to town with `PlaceParty` stays in `town`. The temple's services are clerical and *stone to flesh* is a magic-user's spell, so the way back is a magic-user who joins with `AddMember` and casts it. Locked by `test_petrified_party.py`.

### The canonical tick order

At each round boundary, expirations resolve before ticks, and simultaneous effects resolve in attachment order, tie-broken by effect id. Only the effects engine (and the kernel's death routine, for `dead`) writes creature conditions. Locked by `test_effects.py::TestLedger`.

### Troll damage splits into regenerable and non-regenerable ledgers

A troll keeps fire and acid damage apart from its other damage, in a separate total on the instance, `nonregen_damage`. Regeneration never heals that total. (These totals aren't the effects ledger.)

- A troll is permanently dead only when its fire and acid total alone reaches its maximum hit points.
- Otherwise, a troll that dies at 0 hp revives 2d6 rounds later, counted from the round of the killing damage, and rises with 1 hp. The 2d6 is rolled on the effects stream.

Locked by `test_effects.py::TestRegeneration` and the troll battle golden.

A character who wears a ring of regeneration has the same fire and acid total, in `Character.nonregen_damage`, because the ring's page says "Fire or acid damage: Cannot be regenerated." The ring heals 1 hp a round, up to maximum hit points minus that total. It heals nothing once the wearer is at 0 hp.

Other healing, like a potion or a cure spell, heals fire and acid damage as well as the rest. Healing doesn't lower the total, so the total can end up larger than the damage the wearer still has. The next time the wearer takes damage, `deal_damage` first lowers the total to the damage the wearer had before that hit, then adds any fire or acid damage from the new hit. For example, a wearer with 10 maximum hit points takes 6 fire damage, so the total is 6. A potion heals the wearer back to 9 hp, 1 point of damage. When the next hit lands, `deal_damage` lowers the total from 6 to 1 before it applies the hit.

`deal_damage` lowers a troll's total the same way. Regeneration alone can't make a troll's total larger than its damage, because regeneration heals only the damage that isn't fire or acid.

The field defaults to 0, so a save made before the field existed loads under the same `schema_version`. Locked by `test_drain_and_regen.py::TestRegenerationRing`.

### Mummy rot heals naturally once per ten full rest days and blocks magical healing

"Magical healing is ineffective; natural healing is ten times slower" is read as one 1d3 recovery per ten consecutive full rest days, tracked on the effect. *Cure disease* removes it. `apply_healing` defaults its source to `magical` (osrlib treats instantaneous healing as magical healing), so a caller that forgets to name the source still respects the block. Locked by `test_effects.py::TestMummyRot`.

### Asleep grants auto-hit and dies-to-a-blade; dozing is a caller modifier

The asleep condition, which the *sleep* spell inflicts, has two combat hooks: a sleeping defender is hit automatically in melee, and "a single attack with a bladed weapon can kill", so the melee hit kills outright with no damage roll. osrlib's reading: "bladed" means a weapon (not a gear facet, a monster's natural attack, or an unarmed strike) with the melee quality and without the blunt quality, since the SRD's blunt list exists to separate crushing weapons from edged ones. The hook is melee-use only, and the immunity gate still applies first (a sword absorbed by the black pudding's fire-only gate kills nothing). The dragons' "may be attacked for one round with a +2 bonus" dozing is the caller-supplied situational modifier, not a condition. Locked by `test_combat.py::TestDamagePipeline::test_sleeping_defender_dies_to_a_blade` and its neighbors.

### Falling damage floors per full 10 feet

The engine rolls 1d6 per full 10 feet fallen, and nothing at all for a fall under 10 feet. Locked by `test_combat.py::TestHealingAndFalling`.

### Reload is enforced from caller context

A reload-quality weapon may not fire two rounds running: under the `weapon_reload` flag the attack validator rejects the attack when the caller-supplied context shows that the weapon fired last round. In a session, the battle machine tracks which members fired last round and supplies that context. A direct caller of the kernel supplies it instead, and the kernel enforces the rule only as far as that context is accurate. Locked by `test_combat.py::TestValidateAttack`.

### Two-handed weapons and shields conflict at equip time

`validate_equip` rejects wielding a two-handed weapon with a shield equipped, or equipping the second of the pair, with `items.equip.two_handed_with_shield`. osrlib refuses the conflict at equip time, rather than silently ignoring the shield at resolution. Locked by `test_items.py::TestTwoHandedShieldConflict`.

### Monster spawn HP is minimum 1, ½ HD is 1d4, fixed forms are exact

Spawned HP is the sum of the HD count in d8s (d4 for ½ HD) plus the signed modifier, minimum 1. The engine rolls nothing for `1hp` and for the hydra's 8-hp-per-HD forms. Locked by `test_monster_data.py::TestSpawnHitPoints`.

### Compiled monster data conventions

The SRD compiler turns the SRD's monster pages into monster templates, with these conventions:

- An AC of `No hit roll required` means attacks hit automatically.
- `Varies` morale compiles to no morale score.
- A page that packs several variants into one stat block expands to a separate entry for each, under numbered ids like `vampire_7`, because each frozen template has to be spawnable.
- The `undead` tag comes automatically from the standard bullet the SRD prints on undead pages. The shadow is excluded, because its page says "incorporeal (but not undead)".
- The `person` tag comes from the `General.md` §Persons list, the SRD's default for a rule otherwise left to the referee's judgment.
- The `enchanted` tag is curated by hand in the overrides, with a reason for each entry.
- The XP values printed in a stat block are authoritative.
- A compound alignment compiles to a model of the options.
- A leader's or chieftain's XP compiles to structured notes, and the leader's stats stay as prose.

Locked by `test_monster_data.py`.

Every attack routine has a `use` label, which the default policy reads (see [The default action policy and morale](#the-default-action-policy-and-morale)):

- The first routine is `primary`.
- A later routine whose attacks are a breath, a gaze, or a spell is `special`. The compiler labels it by the attacks' names.
- Every other later routine is labelled by hand from the monster's text. It's `alternative` where the SRD's "or" offers a free choice each round, and `situational` where the text ties the routine to a range, a chance, water, a ship, a form, or a loadout.

The compiler applies the labels after the overrides, so routines that an override splits, like the bone golem's, get labels too. When a later routine has no label, the compiler fails the build and reports the monster. The stat blocks printed on spell pages get labels the same way. Locked by `test_attack_routines.py::TestTheCatalog` and `TestTheCompilerRefusesAnUnlabelledRoutine`.

### The event discriminator is `event_type`, with outcome-bearing codes

Every kernel event class declares a single-valued `event_type` Literal (snake_case, schema-stable, additive-only) that the `KernelEvent` union discriminates on. The message `code` stays free to be outcome-bearing from a per-class closed set (`combat.attack.hit` and `combat.attack.missed`), so formatters select on codes while consumers discriminate on `event_type` and skip unknown values mechanically. The default English formatter is total: unknown codes format to the code string itself. Locked by `test_events_kernel.py`.

### The combat kernel streams

The combat kernel's stream keys are `"combat"` (battle resolution: attacks, damage, saves, morale, initiative), `"effects"` (effect-internal randomness: durations, onsets, revival delays, natural-healing rolls), and `"monster_spawn"` (spawned hit points), scoped so a combat-rules change never shifts spawned HP or effect draws in a golden. Locked by the Phase 2 milestone goldens.

### Spell ids are page-derived slugs, dual pages take `_c`/`_mu`, reversed forms are entry data

A spell's id is the slugified primary name with the reversed parenthetical dropped (`cure_light_wounds`). The nine concepts printed as separate `(C)` and `(MU)` pages compile as two entries with `_c`/`_mu` suffixes because the pairs differ mechanically (*hold person* is 2nd-level cleric at 9 turns/180' but 3rd-level magic-user at 1 turn per level/120'), and a reversible spell's reverse is a `ReversedForm` on its entry, never a separate catalog entry. The *invisible stalker* page's `(MU)` marker distinguishes the monster page rather than a dual pair, so its id stays plain. Locked by `test_spell_data.py::TestCensus`.

### `Spells.md` is the single magic-rules source, and spell pages are identified by the level line

`Rules_of_Magic.md` has identical content, and the compiler never reads it. Spell pages are identified by the italic level line (`*3rd Level [Magic-User Spell](...)*`), never by the `**Duration:**` or `**Range:**` labels, which also appear on potion, hazard, and water-vessel pages. Locked by the compiler census (106 pages, 34 + 72 list entries).

### Memorization is a full-replacement preparation with the timing gates deferred

`memorize_spells` models the daily preparation as a whole-list replacement. The once-a-day, after-uninterrupted-sleep, and one-hour gates are exploration procedure that the crawl layer (`osrlib.crawl`) enforces, and a caller outside the crawl layer can call `memorize_spells` freely. Duplicate selections are legal per RAW. Locked by `test_memorization.py::TestMemorizeSpells`.

### Arcane casters fix normal/reversed at memorization; divine casters choose at cast

Per `Spells.md`: the arcane form "must be selected when the spell is memorized", while divine casters reverse "when it is cast", so divine copies never have a reversed flag (`magic.memorize.divine_reverses_at_cast`) and a divine reversed cast consumes any copy of the spell. Locked by `test_memorization.py` and `test_casting.py::TestCastingPipeline`.

### Casting consumes the first matching memorized copy, and touch attacks consume it hit or miss

Consumption takes the lowest tuple index, and disruption removes a copy identically ("as if it had been cast"). *Cause wounds* touch attacks take a melee attack roll only in combat ("In combat, a melee attack roll is required", and osrlib reads the inverse: outside combat, the touch needs no roll), and the copy is spent whether or not the touch lands, because RAW has no rule for holding the charge. Locked by `test_casting.py::TestCuresAndRestoration`.

### The holy symbol is not a mechanical gate on casting or turning

`Cleric.md` states "A cleric must carry a holy symbol" as a class edict alongside deity faithfulness, not as a precondition on any procedure. The holy symbol is in the gear data, and games wanting the stricter reading check inventory themselves. As a result, a cleric without a holy symbol can still turn undead and cast spells. Locked by `test_turning.py::TestValidation::test_holy_symbol_is_not_a_gate`.

### Starting books are filled to capacity, book capacity is per level, and books never auto-shrink

Arcane casters begin play with exactly as many spells as they can memorize, one first-level spell at level 1. Clerics start with nothing, because their level-1 row has no slots. `add_spell_to_book` caps the book, per spell level, at the current slot count for that level (the RAW "exactly the number of spells that the character is capable of memorizing", read per level). The book is a physical object and never auto-shrinks: a drained character may have a book over capacity and cannot add more. Drain forgets excess *memorized* copies newest-first per level (RAW is silent, and newest-first is deterministic without new state). Locked by `test_memorization.py`.

### Category and immunity gates resolve as no-effect, never as rejections

The holy-water reasoning applies here too: rejections are free and would leak hidden state, so casting *charm person* at a disguised doppelgänger or *sleep* at a wight must not be a zero-cost detector. Casting at an ineligible target consumes the copy and reports `magic.cast.no_effect`. Immune and excluded creatures consume no HD budget, because exclusion happens inside resolution rather than as a validator refusal. Locked by `test_casting.py::TestCastingPipeline::test_no_effect_still_consumes_the_copy` and `TestSleep`.

### Sleep's mode arithmetic

*Sleep* has two modes. Neither allows a saving throw, and neither affects undead.

Mode 1 affects "A single creature with 4+1 Hit Dice". osrlib reads that as a monster whose HD count is exactly 4 and whose fixed modifier is positive, like 4+1 or 4+3.

Mode 2 affects "A total of 2d8 Hit Dice of creatures of 4 HD or lower each". That excludes mode 1's creatures, which have more than 4 HD. Mode 2:

- Costs each creature its HD count, with fixed bonuses dropped, as the spell page says: "a 3+2 HD monster would be treated as having 3 HD". A creature with less than 1 HD counts as 1.
- Spends the budget weakest first through the HD-budget targeting mode, with ties in stable order.

A creature of 5 HD or more is beyond both modes.

Both modes judge a drained monster by the Hit Dice it has left, plus its template's modifier. An ogre drained from 4+1 to 3+1 is no longer a mode 1 creature, and mode 2 takes it for 3 HD. A 5 HD grizzly drained to 4 HD becomes eligible for mode 2. Locked by `test_casting.py::TestSleep` and `test_drain_and_regen.py::TestSleepOnDrainedMonsters`.

### Magic missile is 1 + 2×⌊(level−1)/5⌋ missiles, resolved instantly

osrlib derives the count from "three missiles at 6th–10th level, five missiles at 11th–15th level". Each missile takes one supplied target (repeats stack), hits unerringly with no attack roll and no save, and resolves at cast, so the printed 1-turn duration has no effect in the engine. Locked by `test_casting.py::TestMagicMissile`.

### Lightning bolt kills destroy equipment; disintegrate kills permanently and destroys carried gear

*Lightning bolt* is a destructive death source. *Disintegrate* destroys "the material form ... instantly and permanently", which osrlib reads as permanent death plus equipment destruction, because the material form includes what the creature carries. Locked by `test_casting.py::TestFireBallAndLightningBolt` and `TestSaveOrDie`.

### Spell damage presents the `magic` key

A damage-dealing spell passes weapon-material gates whose keys include `magic`: a wight's silver-or-magic gate admits *magic missile*, a gargoyle's magic-only gate admits *fire ball*. Without the key, those gates would block spell damage. Locked by the Phase 3 spell-battle golden (three missiles damaging the wight).

### The hold/charm person gate admits any character and only `person`-category monsters

Undead are never affected (their exclusion resolves as no effect). *Hold monster* and *charm monster* affect anything but undead. *Charm monster*'s single mode affects one creature of more than 3 HD, and its group mode affects 3d6 creatures of 3 HD or less. Locked by `test_casting.py::TestHoldAndCharm`.

### Charm re-saves are monthly/weekly/daily by INT, with month = 30 days

INT 3-8 saves monthly, 9-12 weekly, and 13-18 daily, with a month set to 30 days and a week to 7. Monsters have no INT score and default to the middle weekly band (override-correctable per monster). The re-save is a tick-time draw on the `effects` stream, and a passed save releases the charm. Locked by `test_casting.py::TestHoldAndCharm`.

### The cumulative-effects rule: largest bonus plus largest penalty per statistic

`Spells.md`: "Multiple spells affecting the same game statistic do not combine." osrlib's reading: at consultation time only the single largest bonus and single largest penalty apply, so a *bless* and a *blight* offset and two *blesses* don't stack. Spell modifiers combine freely with non-spell sources (the RAW carve-out for magic items). Spell morale modifiers use `check_morale`'s existing modifier argument inside the morale rule's ±2 clamp and ML 2/12 exemptions: one uniform adjustment rule rather than a second channel. Locked by `test_casting.py::TestBuffsAndWards` and `test_spell_properties.py`.

### Shield's AC values apply as better-of

"AC 2 [17] against missiles, AC 4 [15] against other attacks": the target's effective AC is the better of the target's AC and the set value, never worse, so a target in plate armour keeps its plate AC. Locked by `test_casting.py::TestBuffsAndWards`.

### Striking applies to weapon attacks of any kind, never unarmed, and attaches to the wielder

RAW names no weapon type, so missile weapons qualify, and unarmed strikes gain nothing. The enchantment attaches to the *wielder* because item instances have no ids. In the corner case where the wielder hands the weapon away mid-duration, the enchantment stays with the wielder. The wielder counts as magical for weapon-material gates while the enchantment lasts. Locked by `test_casting.py::TestBuffsAndWards`.

### Protection from evil: the melee ban is data, and monster alignment resolves at spawn

The enchanted/constructed/summoned melee ban is structured params that the battle machine reads (the kernel can't see who initiates melee). The ward's ±1 applies against creatures of another alignment, which needs a resolved alignment where `MonsterTemplate.alignment` is multi-option: `MonsterInstance.alignment` resolves at spawn (caller's choice, else the template's `usual`, else its sole option), and an unresolved alignment counts as differing, so the ±1 applies to a creature whose alignment is unresolved too. Locked by `test_casting.py::TestBuffsAndWards`.

### Small nonmagical missiles are character weapon missiles and thrown splash items

osrlib draws *protection from normal missiles*' boundary from the spell page's examples ("no protection is granted against hurled boulders or enchanted missiles"): missile weapon uses and thrown splash items set the `DamageSource.missile` flag. The engine never sets the flag on a monster attack by itself. The caller sets `AttackContext.monster_missile` when the attack is a small missile in the fiction (a hobgoblin's arrow). Locked by `test_casting.py::TestBuffsAndWards::test_protection_from_normal_missiles_boundary`.

### All spell-attached effects are dispellable, monster effects are not, and survival is 5% per level of deficit

RAW says *dispel magic* "ends spell effects" without qualification: `permanent=True` means "no duration expiry", not "undispellable" (*continual light* and *flesh to stone* can be dispelled). Effects attached by `cast_spell` record the caster's level, and per effect, when the recorded level exceeds the dispelling caster's, the effect survives on a d100 at or under 5% per level of difference. Monster-inflicted effects (poison, rot, regeneration) are non-dispellable by construction, and magic items are exempt. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### Raise dead: 0 days at level 7, a 14-elapsed-day weakness, and permanent undead destruction

The time limit is 4 days per caster level above 7th, which is 0 days at level 7, RAW-faithful. Revival sets 1 hp and attaches the weakness, set to 14 elapsed days as the simplification of "two full weeks of bed rest" (rest tracking belongs to the crawl layer, and games wanting strict bed-rest semantics extend or release via the ledger). While it runs, the subject cannot attack, cast, or turn undead (RAW bans "other class abilities" too), moves at half rate (a marker param the crawl layer reads), and healing from *every* source is blocked, because RAW says the subject "has 1 hit point" until the recovery completes and no magical healing shortens it. The hit point returns to normal recovery when the weakness ends. Only characters are raisable (all four Classic races are human or demihuman, and monsters are not). The destroy-undead usage kills permanently on a failed save, matching turning's `D`. Locked by `test_casting.py::TestCuresAndRestoration::test_raise_dead_level_seven_allows_zero_days`.

### Neutralize poison's revival window is caller-attested, and only characters revive

"A **character** who has died from poisoning can be revived, if *neutralize poison* is cast within ten rounds": the kernel has no cause-of-death model, so supplying `CastContext.rounds_since_death` *is* the caller's attestation that the target died of poison within that many rounds. Omit it for any other death. Only a `Character` is revivable (the page's usage is titled "Characters"), and revival stands the subject up at 1 hp (osrlib's choice, since RAW names no hit point total). Locked by `test_casting.py::TestCuresAndRestoration`.

### Cure light wounds removes paralysis from any source

*Cure light wounds* in its `cure_paralysis` mode ends paralysis whatever caused it, as the spell page says: "Curing paralysis: Paralysing effects are negated." The mode releases each effect in the ledger that paralyses the target. Then it removes any `paralysed` condition the target still has, like one a game granted with `grant_condition` with no effect behind it. It removes no other condition.

The other cures, like *cure disease* and *neutralize poison*, lift a condition only by releasing the ledger effect that granted it ([#166](https://github.com/mmacy/osrlib-python/issues/166)). Locked by `test_rules_calls.py::TestCureLightWoundsLiftsParalysis`.

### Silence automates both forms: moving on a failed save, stationary on a passed one

A failed save attaches `silenced` (the casting validator consumes it) and the silence moves with the creature. On a passed save, RAW leaves a *stationary* area the creature can step out of. Location-bound effects anchor to cells, so the save-passed outcome attaches a stationary silence effect to the target's cell, which osrlib takes to be the party's current cell (the encounter's abstract location). Creatures in that cell cannot cast while there, battles included. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`, `test_exploration.py::TestStationarySilence`, and `test_battle.py::TestSilencedCellInBattle`.

### Web escape tiers depend on STR, with the augmented and giant tiers caller-asserted

Normal strength escapes in 2d4 turns, rolled at attach on the effects stream. Magically augmented STR above 18 escapes in 4 rounds and giant strength in 2 rounds, asserted via `CastContext.strength_tiers` until such effects exist. The blocking cube is a location effect (see [Light, darkness, and location-bound effects](#light-darkness-and-location-bound-effects)), and the flammability rule stays manual. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### Feeblemind targets only `arcane_magic` casters

The spell page's target is "an arcane spell caster (e.g. a magic-user or elf)": the gate is the target bearing the `arcane_magic` class tag, and anything else resolves as no effect. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### The turning column is the HD count, `2*` only at count 2, counts above 9 unturnable

A cleric turns undead by rolling against the turning table in the cleric's class description. The table's rows are the cleric's level, and its columns are the monster's Hit Dice. A monster's column is its `hit_dice.count` as a string, with three exceptions:

- A count of 2 with a special ability (asterisked HD) maps to `2*`, as the table's footnote says.
- Counts 7 to 9 share the `7-9` column.
- Counts above 9 have no column, so turning fails.

Modifiers never shift columns, so the mummy's 5+1 turns on column 5. The asterisk changes the column only at a count of 2. A wight's HD is `3*`, and it turns on column 3, like any 3 HD monster.

osrlib applies the printed table as it is. The table's footnote lets the referee expand it for more powerful undead, and osrlib leaves that to the game's content.

The column belongs to the kind of monster, because the SRD compares the roll "against the HD of the type of undead monsters targeted". So a drained monster turns on its template's column, and a wight drained to 2 HD still turns on column 3. Locked by `test_turning.py::TestColumnMapping`, the verbatim table fidelity test, and `test_drain_and_area.py::TestTheTurningColumnStaysWithTheType`.

### The turning pool spends lowest-first, stops at the first unaffordable monster, and always affects one

A turning takes two kinds of 2d6 roll:

1. The turn roll. The engine rolls 2d6 for each candidate *type* of monster and compares it with that type's cell on the turning table. A number in the cell succeeds when the roll meets it. `T` always succeeds, and `D` succeeds and destroys the monsters.
2. The pool roll. On any success, the engine rolls 2d6 once, for the total Hit Dice the turning affects.

The pool spends its Hit Dice like this:

- It affects eligible monsters lowest HD first, with ties in candidate order.
- Each monster costs its HD count, with a minimum of 1 and fixed bonuses dropped, as *sleep* counts them.
- The pool stops at the first monster it can't afford, because RAW says excess Hit Dice "are wasted", not reallocated.
- A success always affects at least one undead. osrlib picks the cheapest eligible monster.

A monster whose cell is `D` is destroyed permanently. Every other affected monster gains the `turned` condition, which has no set duration and can't be dispelled, and the encounter releases it when it ends. Only monsters in the `undead` category are candidates, and any other monster is unaffected.

In a session, a turning's candidates are the living monsters of the encounter's groups that haven't fled. The candidate order is the encounter's group order, then each group's monster order, both in battle and before it. A monster killed earlier in the round or the encounter doesn't make its type a candidate, and nor does one whose group left the battle. The reason is the SRD's "At least one undead monster will always be affected on a successful turning". That can't hold for a type with no monster left to affect. Locked by `test_turning.py`, `test_spell_properties.py`, and `test_turning_and_staff.py`.

### The `magic` RNG stream, and the attach-time/tick-time split

Every draw inside spell resolution (targeting dice, damage dice, touch-attack rolls, cast-time forced saves, dispel survival rolls, and both turning rolls) comes from the `"magic"` stream, so a combat-rules change never shifts spell goldens and vice versa. Effect-internal draws (rolled durations at attach, *web* escape dice, tick-time saves like the charm re-save) stay on the `"effects"` stream per [The combat kernel streams](#the-combat-kernel-streams), which is why `cast_spell` takes both streams. Spell resolution has no `Ruleset` flags: every choice is rules-as-written or an interpretation, never a referee-optional variant. Locked by the Phase 3 milestone goldens.

### Encounter tables: hand-mapped ids, table counts override, variant pools

The encounter-table cell names hand-map to compiled monster ids with compile-time resolution (the printed links are unresolvable), and the printed count dice override the monster description's number appearing, per the SRD's note. Packed-variant cells (`Veteran`, `Vampire`, `Hellhound`) compile with the full id tuple and each spawned individual picks uniformly on the wandering stream. The two hydra cells have `variant_dice`, so the engine rolls the printed HD dice once on the wandering stream and selects the template. NPC-adventurer rows compile faithfully as structured `npc_party` entries, and the session fields a generated party for them (see [NPC parties in play](#npc-parties-in-play-encounter-sides-like-any-other)). The "Basic Adventures" typo and the singular "Expert Adventurer" labels normalize via `overrides/encounter_tables.json` with provenance. Locked by `test_encounter_data.py` and `test_exploration.py::TestWandering`.

### Encounter-table and reaction models live in core/tables.py

This placement follows from layering: `osrlib/data/` loaders import the modules that define their models, and `core` modules import the loaders, so defining these models under `crawl/` would give `load_encounter_tables()` a transitive import from core through data into crawl. The reaction table clamps totals into its outer bands ("2 or less" / "12 or more") and reaction rolls are referee-visible (players learn reactions from behavior, as they learn morale results). A dungeon level's encounter table is its number clamped into the printed bands (1, 2, 3, 4-5, 6-7, 8+). Locked by `test_encounter_data.py` and `test_kernel_checks.py::TestRollReaction`.

### The crawl streams

The crawl layer's stream keys are `"wandering"` (the check die, the d20, count dice, variant picks, NPC re-rolls), `"encounter"` (surprise, distance, reaction, distraction), `"exploration"` (forcing, listening, searching, trap springs and trap resolution, tinder, thief skills), and `"monster_action"` (every policy draw, so a substituted policy never shifts attack or damage draws). Trap resolution draws (saves, damage, volley counts) run on the exploration stream, because they are part of the trap procedure, while attach-time condition durations stay on the effects stream per [The combat kernel streams](#the-combat-kernel-streams). Locked by the Phase 4 delve golden and `test_battle.py::TestDefaultPolicy`.

### Detection checks: precedence, zero-chance, and pick-pockets arithmetic

The detection-chance precedence is thief row, then class tag, then the universal 1-in-6 baseline. Construction tricks are the exception, dwarf-only with a **zero** baseline, because the SRD grants that perception to dwarves alone. A zero chance consumes no draw. Pick pockets caps at 99 ("always at least a 1% chance of failure"), takes the victim's over-5th-level penalty as a caller modifier, and a roll of more than twice the effective chance is noticed. Locked by `test_kernel_checks.py`.

### The time-cost census and the thirds odometer

Zero time: `TurnParty`, `ReorderParty`, `OpenDoor`, `CloseDoor`, `WedgeDoor`, `ForceDoor` (forcing takes a moment, and its cost is the noise), `ListenAtDoor` (the once-per-door cap is the limiter), equip/unequip, `DropItems`, `ExtinguishSource`, `PurchaseEquipment` (town time is abstract), and all referee commands. One round: `CastSpell` and `TurnUndead` outside battle, `LightSource` (per tinder attempt too, RAW). One turn: `Search`, `InspectTreasure`, `RemoveTreasureTrap` (osrlib's choice of a symmetric cost, where RAW is silent), `PickLock` (osrlib's choice, where RAW is silent and the skill is delicate work), `TakeTreasure` (RAW's packing-bags turn), `Rest(turn)`. On the odometer: `MoveParty` per cell, a passage at the cost of a step onto its landing cell, and `UseStairs` at one unexplored-cell cost (osrlib's choice). Stated durations: `Rest(night)` 48 turns, `Rest(day)` 144, `PrepareSpells` 6, and travel the turns that the town's `travel_turns` lists for the dungeon. Movement accrues in thirds-of-feet, an unexplored cell 30 units and an explored cell 10, implementing the SRD's three-times-through-familiar-areas rule exactly, and turn-costing actions snap the clock to the next boundary, absorbing the partial move. Locked by `test_exploration.py::TestTimeCostCensus` and `TestOdometer`.

### Rest, fatigue, sleep, and provisions

Resting clears fatigue and running exhaustion, a full night's sleep lets casters prepare spells, and the party eats and drinks once a day.

Fatigue and exhaustion:

- The engine attaches fatigue (−1 to attack and damage rolls) after six consecutive turns without rest, which is the dungeon rule. Time in town and travel time don't count toward it.
- A full turn of rest clears fatigue, even when a wandering monster interrupts the rest on a later turn.
- An interrupted rest's full turns are the whole turns before the wandering-monster check that brought the monsters, because that check is the first step of the next turn. So a rest broken during its first turn clears nothing.
- Each full turn of rest also counts toward the three that clear running exhaustion, and the next rest adds to that count.

Sleep and spell preparation:

- A night is 48 turns, which is 8 hours. The SRD has no time-of-day model, so a night is a length of time, not a time of day.
- Spell preparation requires an uninterrupted night's sleep. An interrupted night isn't sleep, even when its full turns clear fatigue.
- Preparation happens at most once per sleep, and costs six turns for divine and arcane casters alike.
- A wandering encounter during preparation doesn't undo the memorization already written. The SRD doesn't say, so this is osrlib's reading.
- An interrupted day of rest heals nothing, which is RAW.

Food and water:

- At each day boundary, each member eats a standard ration before an iron ration. RAW is silent on the order, and fresh food spoils first.
- A carried waterskin covers the day, because the engine doesn't track water by the pint.
- In town, members still eat their carried rations, but nobody goes short.

A living entangled member also blocks the party's movement. The party doesn't leave a member behind on its own, and leaving someone is a decision for a referee command. Locked by `test_exploration.py::TestRestAndFatigue` and `TestProvisions`, and by `test_rest_credit.py`.

### Doors: forcing, noise, swing-shut, locks

Any door-forcing attempt sets the noise flag for the next wandering check (the banging is identical either way) while only a *failed* attempt denies the party surprise against the room beyond (RAW, exactly as written). Doors the party opened swing shut when the party is no longer in either adjoining cell unless wedged: RAW's "likely to swing shut" becomes always, so a party that wants a door to stay open behind it has to wedge it. A forced stuck door is stuck again once shut. A failed pick-lock locks that thief out of that lock until the next level. Listening is once per character per door, free, and rolled regardless of occupants. Undead make no noise, so silence is ambiguous (the rule that a result never reveals hidden state, applied to exploration). Locked by `test_exploration.py::TestDoors` and `TestListening`.

### Searching and traps

A search covers the party's current cell, costs a turn, and reveals every matching hidden feature on the cell. Finding a trap protects the party from a room trap but not from a treasure trap.

Searching:

- The cell is the SRD's 10' × 10' search area.
- Each character may search a cell once for each kind of search: `secret_doors`, `room_traps`, and `construction`.
- The engine rolls a search whatever the cell contains.
- A `room_traps` search also covers the searched cell's door edges. So the party can find an `open`-trigger trap on the far side of a known door from the side it threatens, before opening the door.
- An undiscovered secret door hides its trap along with itself, because a search result never reveals hidden state.
- Discovering a secret door clears the `room_traps` attempts of both cells that the door's edge joins. Without that, a cell searched before the discovery would keep its trap hidden for good, because each character searches a cell once. The discovery is new information about both cells' 10' squares, so osrlib lets the party search again on either side. That holds whichever side the discovery named, and whether it came from a `secret_doors` search or from the referee's `SetDoorState` with `discovered=True`. Every other attempt, on those two cells or anywhere else, stays as it was.

Finding a trap depends on the trap's kind, because in B/X anyone can find and avoid a room trap, and only a thief can take a treasure trap out:

- A found room trap never springs, at its area's edge or at one of its doors. The party walks around the known pit.
- A found treasure trap still rolls its 2-in-6 on every `TakeTreasure`, until a thief removes it.
- Each character may try once to find a treasure trap and once to remove it. A failed removal springs the trap, which is the classic reading, since RAW says only "attempted once per trap".
- The engine reports the player-visible `exploration.trap.safe` only for a known treasure trap whose trigger resolved without springing. Past a found room trap, the engine rolls no die, so there's nothing to report. An unknown trap's spring die is visible only to the referee.

How a room trap springs:

- A room trap's `trigger` is the action that springs it. `enter` rolls when the party enters a cell of the area. `open` rolls when the party opens a door of the area, through `OpenDoor` or a successful `ForceDoor`. The same 2-in-6 rolls on every opening, until the trap springs once.
- A door belongs to the area from either adjoining cell, so the blade over the lintel drops on the way out too.
- If one door joins two trapped areas, the far side rolls first.
- Each spring hits the opener: the forcing character, or else the first living member in marching order. If an earlier spring killed the opener, it hits the next member standing.
- A spring needs a living victim, so the engine rolls no spring die for a party with no one left.
- A spring that carries the party away from the door, like a chute, cancels the springs behind it, because the party is no longer opening the door.

The SRD doesn't settle these spring rules, so they're osrlib's readings.

What a trap does:

- A trap with a transition, like a slide, moves the whole party, because the party model has one location. When the trap allows a save, it moves the party only if at least one victim fails the save, so a party that passes every save stays on the trapped cell. A transition with no save always moves the party ([#167](https://github.com/mmacy/osrlib-python/issues/167)).
- The darts volley resolves as one damage application whose rolls are every dart's die.
- A treasure trap that `TakeTreasure` springs hits the character who reaches in: the command's `recipient_id` when it names one, and otherwise the first living member in marching order. This is osrlib's reading.

A treasure trap springs only when its cache is opened, so the model rejects `trigger="enter"` on `kind="treasure"`. Schema version 3 added that rejection, and its lossless migration rewrites the unused value to `"open"` on load. Locked by `test_exploration.py::TestSearching`, `TestTreasureTraps`, `TestDoorTraps`, `TestTwoTrappedAreas`, `TestTrapResolutionCensus`, `TestDiscoveringASecretDoorRefundsTheCellsTrapSearch`, and `TestDiscoveringASecretDoorRefundsBothCells`.

### A recovered haul spreads across the party: usable items, even wealth, everyone mobile

When the party takes treasure, the engine spreads it across the party. Items go to members who can use them, each member gets an even share of the wealth, and nobody is loaded past their maximum load. This is a documented adaptation.

RAW never says who physically carries the loot. It says the split is the players' business: "Awarded XP is always divided evenly, irrespective of how the players decide to divide the treasure" (*Awarding XP*). What RAW does state are the consequences: "The maximum load any character can carry is 1,600 coins of weight. Characters carrying more than this cannot move", and "The movement rate of the party as a whole is determined by the speed of the slowest member" (*Time, Weight, Movement*). So if one character took the whole haul, a single pickup could stop the whole party. There's no tabletop referee to say "spread it out", so the engine does.

The engine places the haul in three passes: items, then valuables, then coins. Items go first, while every carrier still has room, because only some classes can use them. Valuables and coins follow, to even out the shares.

- **Items go where they are useful.** The engine prefers a carrier whose class may use the item, judged by the same class policies `validate_equip` enforces, plus a magic item's `usable_by`: the fighter takes the plate mail, the magic-user the arcane wand, the cleric the divine staff. Equipped state is *not* consulted, because the question is where the item belongs, not whether the carrier's hands are full at that moment. When nobody's class can use an item, it still goes in somebody's pack.
- **Gems and jewellery divide by worth, not by count.** Three 50 gp gems against one 300 gp ring is even in objects and lopsided in wealth, and wealth is what a share means. Value is also cheap to move, since a 1,000 gp gem weighs one coin, so balancing it costs the party nothing in mobility. The engine deals pieces richest first, each to whoever currently has the least by value.
- **Coins divide evenly, denomination by denomination.** Every coin weighs 1 whatever its metal, so an even split of a denomination is even in worth *and* in weight. It's the one place where a fair share and a mobile party come to the same thing. The engine doesn't use coins to make up for a big gem: matching a 1,000 gp gem in coin means 1,000 coins of weight, which would immobilise the carrier.
- **Nothing is ever loaded past the maximum load.** The maximum load is 1,600 coins, unless the carrier wears gauntlets of ogre power. A carrier who would go over their maximum load doesn't take the piece, and the engine tries another carrier. So a take can't immobilise anyone whom some other arrangement would have kept moving.
- **What won't fit stays where it lies**, in the drop pile on the party's cell, with an `exploration.item.left_behind` event. The drop pile is the treasure lying on a cell, which the party can pick up later. Treasure is never destroyed, so the party can lighten its load and come back for it. Within the coins, the richest denomination goes first, so a party out of capacity keeps the platinum and leaves the copper.

The whole pass is deterministic and spends no draw: ties break on marching order, never on a die. It's automatic bookkeeping, not a ruling. `GiveItems` and `DropItems` rearrange it freely, and the XP award doesn't change either way, since `party_valuation_cp` sums the whole party. `TakeTreasure` also accepts an explicit `recipient_id`, which narrows the carriers to that one member, still capped at that member's maximum load. Locked by `test_exploration.py::TestTreasureDistribution`.

### Light, darkness, and location-bound effects

Lighting consumes the consumable (one torch, one oil flask) and extinguishing forfeits the remainder, with no partial-burn bookkeeping, and relighting starts a fresh consumable. When a light-kind effect expires the session appends `exploration.light.expired` for players (the ledger's expiry event is referee visibility). The tinder box gates lighting when the party has no open flame (an active `brightness="flame"` effect), 2-in-6 per round, RAW. A darkness-family effect on any member suppresses the whole party's light while it runs (the printed radii cover a marching party) and `blocks_infravision` disables infravision too. Following RAW, `Search`, `InspectTreasure`, `PickLock`, and reading require light, while searching and listening alone also work on the actor's infravision. `MoveParty` in darkness is legal, but the party is surprised on 1-3 instead of 1-2 when unlit and not every living member has infravision (the blind-party adaptation). A cell is visible when it's explored or is the party's current cell, a simplification of the spec's visible flag. Location effects anchor to `cell:{dungeon}:{level}:{x},{y}` references with enter hooks in attachment order: the burning-oil pool (when the party drops oil with `DropItems` and then lights it with `LightSource`, the engine compiles the pool from [Splash damage is two applications](#splash-damage-is-two-applications) onto the cell, and every living member entering while it burns takes its 1d8), stationary *silence* (see [Silence automates both forms](#silence-automates-both-forms-moving-on-a-failed-save-stationary-on-a-passed-one)), and *web*'s blocking cube (entering attaches the entangled escape countdown, a web cast on a location lasts the spell's duration on the cell, and the escape params are on the effect). Web flammability, *cloudkill*, and the walls stay manual. Locked by `test_exploration.py::TestLight` and `TestLocationEffects`.

### Wandering monsters

Each level has a chance of wandering monsters, which the SRD prints as 1-in-6. osrlib adjusts that chance with these modifiers:

- +1 for noise since the last check.
- +1 for light as bright as daylight, which is the brightness of *continual light* in osrlib's spell data. A torch or lantern flame is the baseline that the printed 1-in-6 already assumes.
- −1 while the party hides.

The chance is clamped to 0-6, and a chance of zero skips the roll. RAW says only that the referee "may increase" or "may decrease" the chance and gives no amounts, so osrlib sets each modifier at 1.

The decrease is for hiding, not for resting, because the SRD ties it to an out-of-the-way place: "If the party rests quietly in an out-of-the-way location, the referee may decrease the chance of wandering monsters being encountered."

- A plain `Rest` checks at the level's chance, plus noise and light, so wandering monsters can interrupt a rest in a dungeon.
- A `Rest` with `hidden` set takes the −1 on every check during the rest. The game or the referee decides whether a place is out of the way, and the session accepts the flag as given.
- On a level with the printed 1-in-6 chance, a hidden rest brings the chance to zero, so the session rolls no check. On a level with a higher chance, or with noise or bright light, it still rolls.
- In town, the session accepts `hidden`, and it changes nothing there, because no checks run in town.

The checks stop during encounters and battles, and start again after. Wandering monsters are never surprised, because they come "moving in the direction of the party", but the party may be. Locked by `test_exploration.py::TestWandering` and `test_rest_hiding.py`.

### The encounter procedure

Every encounter, keyed ones included, opens with three rolls: surprise, distance, and reaction. Rooms use the same abstract range track as corridors.

Distance:

- The engine rolls 2d6 × 10' and caps the result at the longest unobstructed straight sight line from the party's cell. RAW rolls 2d6 × 10' only "if there is uncertainty", because "the situation in which the encounter occurs often determines how far away the monster is". osrlib reads the walls around the party's cell as that situation.
- The sight line runs in the four grid directions, through the edges that sight crosses: open floor, and open doors that aren't secret. In a rectangular room, that's the room's extent from the party's cell. Down a straight corridor, it's the whole passage, so the printed 20'-120' range stays available wherever the space allows it.
- The shortest distance is one cell, 10', so a sealed cell never gives 0'.
- Darkness never shortens the line, because light decides surprise, not distance. A monster the party can't see is still exactly as far away as it is.
- A distance the caller supplies isn't capped, and every referee spawn supplies one.
- The cap draws nothing from the stream.

Surprise:

- A keyed encounter's monsters expect the party when the area's flag says so.
- A failed attempt to force a door alerts the room.
- When the party carries light in darkness, the monsters don't roll for surprise at all.
- A successful listen at a door makes the party aware of the monsters.
- Surprise gives the other side one free round, however the battle starts. Surprised monsters give the party a free battle round, in which the monsters hold while the party acts. A surprised party loses an encounter round, in which hostile or attacking monsters can open battle with a surprise round that the battle machine runs.

Reaction: the SRD's reaction table assumes a referee who decides what each result means in play. osrlib sets the monsters' stance, their attitude toward the party, from the 2d6 roll:

- 2 or less: the monsters attack at once.
- 3-5, hostile: the monsters attack at the end of the next encounter round, unless the party has started to evade or a parley has improved the stance.
- 6-8, uncertain: the monsters wait, and the engine rolls the reaction again next round, with no modifier.
- 9-11, indifferent: the party can pass freely.
- 12 or more: the monsters are friendly.

Only monsters in the attacking and hostile stances pursue a party that evades. The party can parley any number of times, and each parley is a fresh reaction roll with the speaker's CHA modifier.

In an encounter, every offensive act goes through `EngageBattle`. Turn undead is the exception: it has a separate command, used in an encounter before any battle. Using it in front of onlookers that aren't undead starts a battle, which is osrlib's reading.

Time and the end of an encounter:

- An encounter takes at least one full turn, as the SRD says. It ends at `max(next turn boundary, start + one turn)`: the next turn boundary, or one full turn after it opened, whichever is later. So an encounter that opened partway through a turn can also end partway through a turn.
- When an encounter ends, it releases `turned` effects and every other effect on its monsters, because the story moves on. A dead troll's pending revival becomes narration after the battle.
- When an encounter opens, `start_encounter` attaches the regeneration effect to each monster whose template has the `regeneration` ability, so a troll heals and revives only while its encounter runs.
- A keyed encounter that the party evaded triggers again, from the start, the next time the party enters the area.

Locked by `test_encounter.py`, the free rounds by `test_encounter.py::TestSurpriseFreeRounds`, the distance bound by `test_encounter.py::TestEncounterDistance`, and the regeneration effect by `test_monster_regeneration.py`.

### Evasion and pursuit

Pursuit compares the party's slowest running rate (full feet per round) against the pursuers' slowest base ground mode (the mode with no descriptor, else the first printed, so a flying mode doesn't count). Slowest-of-pursuers mirrors slowest-of-party, and the engine doesn't model a pack that strings out. `Evade(drop="treasure")` scatters every living member's coins and `"food"` one ration per member (osrlib's reading), and mid-pursuit `DropItems` re-baits round by round. The distraction is 3-in-6 when the bait matches: treasure for monsters whose treasure ref has letters (the intelligence proxy, override-correctable per encounter) and food for everything else. One roll stops the whole side (pack behavior). Pursuit ends on distraction success, at melee contact (gap ≤ 5', battle at 5'), or at 30 rounds, when both sides are spent and the monsters give up (the limit that ends every pursuit: with the party no faster the gap never grows, so no sight-loss distance is needed). Running exhaustion (−2 attack, damage, and AC, the AC penalty applied through `attack_penalty_of_attackers` +2) lasts until three rested turns. Locked by `test_encounter.py::TestEvasionAndPursuit`.

### The battle round

A battle runs in rounds. Each round, the party sends one command with one declaration for each living, able member, and each monster group acts through its policy.

#### Declarations and ranks

- The session rejects the whole round if any declaration is invalid, because accepting part of a round would complicate replay.
- Under `formation_width_limit`, the formation width is the frontage the space around the party offers: the widest square of unbroken floor that includes the party's cell, with five feet of frontage for each member. The SRD says "2–3 characters in a 10' passage". osrlib takes the lower number, so each member needs 5', and applies that to whatever space the party stands in. Two members fit side by side in a 10'-wide passage, however long it runs, and in a room the rank is as wide as the room's shorter side.
- At the start of each round, the session works out the ranks again from the living marching order. Melee reaches only the front rank, in both directions. There's no penalty for firing into melee, because the SRD has none.
- A party melee attack hits the first living, visible monster of the group's reachable rank, with no draw. Monsters pick their targets with equal chance, on the monster-action stream (see [Monsters pass over statues](#monsters-pass-over-statues-and-ghouls-and-thouls-turn-from-the-members-they-paralyse)).
- A staff of striking is "Used as a magical melee weapon", so under `formation_width_limit` only a front-rank member may use one. The session judges that against the ranks the round's swaps produce, as it does a melee attack, and refuses a back-rank member's use with `battle.declaration.not_in_front_rank`.
- Under individual initiative, the machine still resolves each side as a block, and orders the blocks by each side's best individual total, because the SRD's phase sequence is per side.

#### Formation moves

The party moves as a formation when every declarer agrees, which is the Bard's Tale convention:

- When every declarer retreats, the formation moves off at the full encounter rate (Combat.md's "full encounter movement rate"). The running pursuit begins when the battle turns into a pursuit at the end of the round.
- When every declarer makes a fighting withdrawal, the formation backs off at half the encounter rate.
- The first `close` declaration advances the formation on the group it names, stopping at 5'. `close` needs no agreement, so a lone `close` advances the formation.
- A round whose only declarer declares a defensive move is a formation move, because everyone agreed.

The move set is the SRD's two defensive moves and `close`. Schema versions before 4 had a fourth move, `withdraw`. When osrlib loads an older command log, its lossless migration removes `withdraw` from the declaration and leaves the member holding, which is how that round played out.

#### A member who withdraws alone

A `fighting_withdrawal` or a `retreat` that not every declarer makes belongs to the declaring member alone, as Combat.md describes both moves: "The combatant moves backwards at up to half their encounter movement rate" and "The combatant turns and flees from melee". It changes the ranks and leaves the range track alone. The member steps back out of the front rank and trades places in the marching order with the back-rank member named in the declaration's `replacement_id`.

Both moves need that replacement, because a rank has no empty places. The ranks are the living marching order cut into rows of the formation width, so a member leaves the front rank only by trading places with someone behind it. For a fighting withdrawal, this is how osrlib applies the SRD's "There must be clear space behind the combatant".

A withdrawal alone is the only way the marching order changes in battle, and the session refuses `ReorderParty` in battle. The changed order lasts after the battle, until a `ReorderParty` outside an encounter sets it again.

Only a front-rank member may withdraw alone. The replacement must be a living, able member behind the front rank, and must meet all of these conditions:

- They can move.
- They haven't declared a `cast` (Combat.md: "The spell caster cannot move and cast a spell in the same round") or a defensive move.
- No withdrawing member earlier in marching order has named them. When an earlier member names a replacement, the replacement is reserved even if the session refuses that earlier member's withdrawal.

Each rank stands 5' behind the one in front of it, so a replacement k ranks back is k × 5' away. Both members must cover that distance:

- The withdrawing member covers it at up to half their encounter rate for a fighting withdrawal, or their full encounter rate for a retreat.
- The replacement covers it at up to their full encounter rate, because outside melee "Combatants can move up to their encounter movement rate each round".
- *Haste* on a member doubles that member's limit.

The session refuses a withdrawal alone with the first of these codes that applies, so each withdrawing member gets at most one of them:

1. `battle.declaration.not_in_front_rank`, when the member stands in the back rank.
2. `battle.declaration.no_replacement`, when the member names no replacement.
3. `battle.declaration.replacement_unavailable`, when the named replacement can't step up.
4. `battle.declaration.replacement_too_far`, when either member can't cover the distance.

With `formation_width_limit` off, every living member stands in the one rank, so no member can withdraw alone. A withdrawal alone needs no group in melee, and it can share a round with a `close`.

The swaps resolve in the party's movement step, in the marching order of the withdrawing members and before the formation's `close`. Each swap reports a `MemberWithdrewEvent` (`battle.member.withdrew` or `battle.member.retreated`). A side that wins initiative over the party therefore strikes at the ranks as they stood when the round was declared.

When the movement step comes, a swap happens only if all of these are still true:

- The withdrawing member stands in the front rank.
- The replacement stands behind it.
- Both members can move.
- The replacement can act on the party's behalf. A member who is incapacitated, charmed, or confused can't.

Otherwise the two stay where they are, and no event reports a swap.

From the movement step on, the replacement stands in the front rank, where monsters acting later can strike them. The replacement may declare a melee attack in the round they step up, because "Movement and attacking may be combined in the same round". The session judges that declaration against the ranks the round's swaps produce, and the attack resolves only if the swap happened. A melee attack that the monster side chose against a member who has since withdrawn is skipped, because it's no longer possible.

A retreat's penalties fall only on the members who retreat, and they last the whole round, whichever side acts first: "This round: ... the opponent gains a +2 bonus to all attacks against the retreating combatant and ignores any AC bonus due to the combatant's shield". A formation retreat is every declarer retreating, so it exposes every member to those penalties, including against monsters that win initiative and strike before the party moves off. A retreat alone doesn't end the battle.

#### Moving and attacking

A member who makes a fighting withdrawal or a retreat makes no attack that round, alone or with the formation, because a member declares one thing per round and the move is that thing. For a retreat, that's RAW: "the retreating combatant may not attack".

A fighting withdrawal takes nothing away in RAW, and "Movement and attacking may be combined in the same round". However, the round sequence resolves movement before missile attacks and melee. By the melee step, a member who withdrew alone stands behind the front rank, where melee doesn't reach, and a formation that withdrew stands out of melee range. RAW would still allow a missile attack after the move. osrlib doesn't represent that: a withdrawing member's declaration that also names a weapon or a group makes no attack. Locked by `test_single_withdrawal.py`.

#### Turn undead, disruption, and concentration

- Turn undead resolves in the magic phase, needs no declaration posting, and can't be disrupted, because it's a class ability, not a spell. It names no group. It reaches every living monster in the groups that haven't fled when it resolves (see [the turning pool](#the-turning-pool-spends-lowest-first-stops-at-the-first-unaffordable-monster-and-always-affects-one)).
- The machine detects disruption by the RAW trigger: a declared caster is successfully attacked, or fails a save, before acting.
- A caster who declares anything other than `cast` or `hold` stops concentrating. A turning counts, so a cleric who declares one stops concentrating, even when confusion stops the turning before it resolves.
- A caster maintains one concentration spell at a time: casting a second ends the first, and a spell that needs no concentration leaves it alone ([#167](https://github.com/mmacy/osrlib-python/issues/167)).

Locked by `test_rules_calls.py::TestTurningEndsConcentration` and `test_rules_calls.py::TestOneConcentrationSpellAtATime`.

#### An attack that names no weapon

An `attack` declaration that names no weapon uses the wielded weapon that suits the attack ([#162](https://github.com/mmacy/osrlib-python/issues/162)). The SRD decides melee or missile by distance alone: "Melee: Close quarters weapon (5' or less)" and "Missile: Thrown or fired weapon (greater than 5' distance)". So the group's distance when the round is accepted decides it: a melee attack at 5' or less, and a missile attack beyond.

- The session takes the first wielded item with the `melee` or `missile` quality, in the order the member equipped them.
- For a missile attack, it prefers the first whose long range reaches the group. When none reaches, it takes the first missile weapon, and then refuses the attack with `combat.attack.out_of_range`.
- A real weapon comes before an improvised one, and equip order holds within each kind. An improvised weapon is a gear item whose stats come from its combat facet, like a torch. A member who lights a torch and then draws a sword, the usual dungeon setup, swings the sword. The torch still counts when nothing else suits the attack, because the weapon table gives it the melee quality.
- A splash weapon never counts, because a `use_item` declaration throws it. A magic item with no base weapon, like a wand, has no weapon stats.
- A member with nothing that suits the attack strikes unarmed.

The session makes the choice once, when it accepts the round. The round then plays out exactly as if the player had named that weapon, even when the distance changes before the attack resolves. The command log contains the declaration as it was sent. Locked by `test_weapon_default_and_device_recheck.py`.

#### The magic phase re-check

The session judges a declaration twice: once when it accepts the round, and again in the magic phase immediately before the declaration resolves. The second time, it runs every check the declaration passed the first time, because the phases between the two can change what those checks read. Failing any one of them fizzles the declaration, whatever the round changed to cause the failure. Two cases that occur:

- An ally's *silence 15' radius* anchors on the party's cell earlier in the same magic phase. The re-check refuses a cast with `magic.cast.silenced_area`.
- The party's last torch goes out as its bearer dies in the missile phase. The re-check refuses a scroll read with `exploration.action.requires_light`.

A cast or scroll read that the re-check refuses isn't cast, and it never raises an exception out of the round. The caster loses the memorized copy, or the scroll loses its spell, exactly as a disruption loses it. The round reports it with `magic.cast.fizzled` on `SpellDisruptedEvent`, whose `reason` field contains the first rejection code the re-check produced. A caster the round already disrupted reports `magic.cast.disrupted` instead, because the disruption check runs first.

A wand, staff, or rod use and a turning get the same re-check ([#145](https://github.com/mmacy/osrlib-python/issues/145)):

- A staff of striking aimed at a group that ran out of melee reach fizzles with `combat.attack.out_of_reach`.
- A wand aimed at a group that fled the battle fizzles with `battle.declaration.unknown_group`.
- A staff of striking in the hands of a replacement whose swap didn't happen fizzles with `battle.declaration.not_in_front_rank`, because the re-check reads the ranks as they stand.
- A turning fizzles with `battle.declaration.no_target` when no living monster is left in any group that hasn't fled, because the missile phase or an earlier declaration in the magic phase killed the monsters or drove them off. That check asks whether any monster is left, not whether any undead is, so it tells the player nothing about which monsters are undead.

`DeclarationFizzledEvent` reports a device use or a turning that fizzles, with the code `magic.device.fizzled` or `magic.turning.fizzled` and the same `reason` field. `SpellDisruptedEvent` stays for spells and scrolls, so its `spell_id` always identifies a spell. A fizzled device use costs what a `hold` costs: the device was never used, so it keeps its charge, isn't identified, and breaks no invisibility. A fizzled turning draws nothing and costs nothing.

Each battle round advances the clock one round through the ledger. Locked by `test_battle.py`, the formation moves by `test_battle.py::TestAFormationMoveNeedsEveryDeclarer`, a member's own withdrawal by `test_single_withdrawal.py`, and the re-check by `test_battle.py::TestTheMagicPhaseRechecksADeclaration` and, for devices and turnings, `test_weapon_default_and_device_recheck.py` and `test_turning_and_staff.py`.

### Spell effect consumption in battle

*Haste* resolves the bearer's attack declaration twice and doubles track moves (the party's consolidated move doubles when every living member bears the multiplier, which is osrlib's reading). Invisible combatants are untargetable, and a rejection here leaks nothing, since the player already knows the target is invisible, and the machine releases the effect when the bearer attacks or turns undead. Mirror images pop one per incoming attack with no attack roll. Confusion overrides declarations with its 2d6 behavior roll on the combat stream (a forced rules roll, not a policy choice) and re-saves on the magic stream per its params, both machine-run round rolls rather than ledger ticks, so the tick-time convention in [The `magic` RNG stream](#the-magic-rng-stream-and-the-attach-timetick-time-split) is unchanged. `entangled` blocks declared and policy moves while an entangled front-ranker still fights, and a `weakened` creature cannot attack or cast (the kernel validators reject those actions, and the machine reports the rejections). The *protection from evil* melee ban: a monster whose template bears a warded category may not initiate melee against a warded target of differing alignment, so the default policy's target pick skips them while missiles and breath land, and the ban breaks for a target who has engaged the barred creature in melee, the ±1 modifiers persisting (RAW's clause). Locked by `test_battle.py::TestEffectConsumption`.

### The area footprint

An area effect aimed at a monster group affects as many of the group as its shape covers. osrlib works that out from the shape's size, because the combat space is an abstract range track, not a map. The SRD leaves this to the referee, so it's a documented adaptation.

- An area holds `ceil(span / 10) × width` creatures. The span is how far the shape reaches into the group, counted in 10' ranks.
- A radius shape spans its diameter, because a circle reaches twice its radius across. Lines, cubes, and clouds span their length. A cone spans its length minus the group's distance, because a cone starts at the caster.
- The width is the formation width. With `formation_width_limit` off, the width has no limit.
- The area takes the group's creatures in spawn order, the fixed order in which the session created them, up to its capacity.
- Under `aoe_friendly_fire`, an area that lands on a group in melee range also catches the members of the party's front rank who are fighting that group. They come after the monsters, in marching order, and use up whatever capacity is left.
- A breath weapon aimed at the party covers `ceil(reach-limited span / 10)` ranks, counting from the front. A breath that hits one target, like the hellhound's, picks from the front rank with equal chance, on the monster-action stream.

Locked by `test_battle.py::TestAreaFootprint`.

A spell in an HD-budget mode can also aim at a group. A `cast` in an `hd_budget` mode with a `target_group_id` and no `targets` takes its candidates from that group, and the targeting mode spends the budget on them weakest first. The player view shows group ids but no monster ids, so a front end built on the player view casts *sleep* in battle this way.

Which members of the group are candidates depends on whether the spell has an area:

- The SRD's *sleep* page gives the spell a range and a Hit Dice budget but no area. So a group-aimed *sleep* takes every living member of the group, in spawn order. That's the same cast as naming each of those members in `targets`.
- *Death spell*'s page gives it "a 60' cube area". A group-aimed *death spell*, like any `hd_budget` mode with an area, takes the candidates its area would take under the footprint rule above: the group's living members in spawn order, up to the area's capacity, then the engaged front rank under `aoe_friendly_fire`.

When a declaration sets both `target_group_id` and `targets`, the session uses the named `targets`, with no footprint, so a recorded command log replays as it did. The session refuses a declaration that sets neither with `magic.cast.target_count`. Locked by `test_battle_player_view.py::TestAnHdBudgetSpellAimedAtAGroup` and `test_drain_and_area.py::TestAnHdBudgetSpellWithAnAreaAimedAtAGroup`.

### The default action policy and morale

The default policy, `ScriptedPolicy`, follows a monster's scripted pattern when its data has one, and otherwise closes and attacks. It draws only from the monster-action stream.

Scripted patterns and movement:

- A breath weapon with `uses_per_day` opens with breath, then breath or melee with equal chance while daily uses remain, as the dragons do in RAW.
- For a `per_round_chance_in_six` gate, like the hellhound's, the policy rolls the chance each round.
- Otherwise, a group farther away than 5' closes, and at 5' it melees.
- The monster data has no ranges for monster missile attacks, so they work like melee: the monster closes, then attacks. So the spitting cobra spits from 5', which is osrlib's reading.
- Groups never cast, because casting tags are `manual` data.

Attack routines (see [Compiled monster data conventions](#compiled-monster-data-conventions) for the `use` labels):

- A monster that melees picks its target, then one of its attack routines. It picks with equal chance among the routines whose `use` is `primary` or `alternative`: the thoul claws or swings a weapon, the mule kicks or bites, and the spitting cobra spits or bites. A monster with one such routine draws nothing for the pick.
- The policy never picks a `situational` routine. The SRD ties such a routine to a situation osrlib doesn't model: a range, a charge or a trample chance, water, a ship, a form, or a loadout. So the default policy never rolls the 3-in-4 trample chance of the elephant and its kin, or the cave locust's 50% jump.
- A policy that your game supplies can name a situational routine in `MonsterAction.routine` when the game decides the situation applies. Battle then rolls that routine's attacks as melee attacks, including a thrown missile in a later routine, like a giant's boulder.
- A `special` routine is a breath, a gaze, or a spell. Battle rolls no attack for one, even when a policy names it. It also never rolls an attack entry named `gaze` or `breath` as an attack, because a breath and a gaze resolve another way.
- A confused monster that attacks uses its first routine, which is osrlib's reading.

Morale:

- Battle checks morale automatically at the start of each monster block, through a tracker for each battle. The triggers are the side's first death and half the side incapacitated.
- A monster's alternate morale score applies when the round meets its condition, like fear of fire when the round's damage included fire. The spell morale modifier applies under the cumulative-effects rule.
- A group with ML 2 routs when battle starts, or as it joins a battle already running.
- A failed check means retreat exposure that round, then flight at full speed. The group leaves the battle as routed once it's past 120', the sight distance that pursuit uses, which applies the same way to both sides.
- A group whose living members are all turned or afraid retreats as a whole, and turned members of a mixed group don't act. That's osrlib's reading.

A fled group stays in the player view's `groups`, in order, with `fled` set. The session refuses every declaration aimed at it with `battle.declaration.unknown_group`: an attack, a `close` move, a thrown item, a device, or a spell. The party can still declare against a group that's running but still inside 120', because the party can chase the group or shoot at it. A cast aimed at a group that flees earlier in the same round fizzles at the magic phase's re-check (see [The battle round](#the-battle-round)).

Chasing routed monsters is left to the game. osrlib would treat it as the evasion procedure with the party as the pursuer, and that isn't built yet.

No group in the compiled data has ML 2, so tests force both the battle-start branch and the join branch. Locked by `test_battle.py::TestDefaultPolicy` and `TestMoraleAndEnds`, for the routine pick by `test_attack_routines.py`, for a group that joins by `test_spawn_into_encounter.py::TestAGroupThatWillNotFight`, and for a fled group by `test_battle_player_view.py::TestAFledGroup`.

### Monsters pass over statues, and ghouls and thouls turn from the members they paralyse

A monster that melees picks a target with equal chance among the members of the party's front rank it can reach. Two rules narrow that choice, and the policy applies them in this order:

1. A member turned to stone is a statue and out of the fight. While a target that isn't stone is in reach, no monster picks a statue. The SRD doesn't say this, so it's osrlib's judgement.
2. A ghoul or a thoul also passes over a paralysed member, while a target that is neither paralysed nor stone is in reach. The ghoul's entry says "After paralysing a target, ghouls will attack others", and the thoul's entry says the same of thouls.

So a ghoul whose reach holds only a paralysed member and a statue attacks the paralysed member, because rule 1 removes the statue first.

The second rule comes from data, not from the monster's id. The ghoul's and the thoul's `paralysis` abilities have the param `attacks_others`, and the policy passes over paralysed members for any monster whose `paralysis` ability has it. The carcass crawler's and the gelatinous cube's entries have no such clause, so they keep attacking the members they paralyse.

NPC adventurers use the built-in [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy]. They pass over statues the same way. They still attack a paralysed member, because the second rule comes from the ghoul's and the thoul's monster entries, and an NPC adventurer isn't a ghoul. This is osrlib's reading.

When every member in reach is stone, the monster attacks a statue. It doesn't hold instead, because a party that is all stone declares nothing and could never end the battle. It doesn't reach past the statues to the ranks behind, because melee reaches only the front rank, in both directions. A dead member leaves the ranks at once, so a front rank is never made of the dead.

A monster picks one target a round and makes all its attacks on that target. A ghoul has two claws and a bite, so a ghoul whose first claw paralyses its target still spends the rest of that round's attacks on the same target. When none of the members in reach is paralysed or stone, or when all of them are stone, these rules leave the choice as it is, so the monster's draw doesn't change. Locked by `test_monster_tactics.py`.

### Battle applies six kinds of monster attack effect and the petrifying gaze, and leaves the rest to the referee

When a monster's attack hits in battle, the session applies each effect the attack lists, if both of these are true:

- The effect is `paralysis`, `poison`, `disease`, `energy_drain`, `petrification`, or `blindness`.
- The monster's ability for that effect isn't `manual`, which would leave it to the referee.

The effects apply once for each hit, so a target hit by two paralysing claws saves twice. A dead or petrified target takes none of these effects.

The saves work like this:

- The target saves versus paralysis against paralysis and petrification.
- The target saves versus death, with the ability's modifier, against poison. A failed save against poison kills at once, or when the poison's onset runs out.
- Mummy rot and energy drain allow no save.
- An elf, whose class has `ghoul_paralysis_immunity`, rolls no save against the ghoul's or the thoul's paralysis, and isn't paralysed. osrlib has no model of size, so the same paralysis also affects a creature larger than an ogre, which the SRD exempts.

Blindness comes from the spitting cobra, whose entry says "Blinding spit: Range: 6'. A hit causes permanent blindness (save vs poison)." A spit hit makes the target save versus death, the category for poison, or be blinded for good. The blindness is permanent and can't be dispelled, and no spell that osrlib runs ends it, so a cure is the referee's call. The SRD's combat rules say "A blind character is unable to attack", so the session refuses an attack from a blind member with `combat.attack.attacker_blind`.

The basilisk and the medusa have a petrifying gaze. The session runs it through `resolve_gaze`, once a round while the gazer's group is at 5', before the group acts. The gaze reaches the party's front rank and takes no attack roll. The session never rolls the basilisk's `gaze` attack entry as an attack. A gaze works through sight, so it never reaches a blind member, including the surprise gaze described below. That's osrlib's reading.

The SRD's basilisk says "Unless averting eyes or using a mirror, characters in melee are affected each round". So a member's `BattleDeclaration` can set `gaze_defense` to `avert_eyes` or `mirror` for one round, alongside any action:

- **`avert_eyes`** - the member has the `averted_eyes` condition from the moment the round is accepted until it ends, and `resolve_gaze` spares them. The basilisk's and the medusa's entries give the cost: "-4 penalty to-hit; the basilisk gains a +2 bonus to attack". The −4 applies to every attack roll the member makes that round, whatever it's aimed at, because the SRD gives it without naming a target. The +2 applies only to a gazer's attacks on the member, because the SRD names the gazer for it.
- **`mirror`** - the gaze spares the member too, and every attack roll they make that round takes the −1 that the basilisk's entry gives for "Fighting by looking in a mirror". No monster gains a bonus against them. The SRD's equipment list has the mirror, "Useful for looking around corners or for reflecting a gaze attack", so the member has to carry one. The session refuses a `mirror` stance from a member who doesn't carry a mirror with `battle.declaration.no_mirror`.

Neither stance draws anything or emits a separate event, so a round with no stance draws the same dice it would if stances didn't exist. A back-rank member is out of the gaze's reach and needs no stance. A member who can't act, and so doesn't declare, is already spared by `resolve_gaze`.

The basilisk's entry also says "Characters surprised by a basilisk meet its gaze". osrlib applies that once, as the encounter opens:

- When a basilisk surprises the party, every living member, back rank included, meets its gaze, at whatever distance the encounter opened.
- The gaze comes whatever the basilisk's stance, so it comes even in an encounter that never becomes a battle.
- It also comes when the basilisk is surprised too, because the clause is about the characters and a gaze takes no action.
- The party declares nothing before the encounter opens, so no gaze stance can spare a member from it.
- A battle that opens from that encounter brings no second surprise gaze. If the battle opens at once with the monsters' free round, the basilisk doesn't gaze again in that round, so a member at 5' meets its gaze once that round. A battle that opens later gazes at the front rank in each round the basilisk is in melee, as any battle does.
- A basilisk that joins an encounter already open, like one a referee spawns into it, surprised no one. So it doesn't gaze at the party as it joins, even when the party was surprised as the encounter opened.

The medusa's entry has no surprise clause. A medusa that surprises the party gazes in its free round only if it's already at 5', and then only at the front rank. That's osrlib's reading.

The SRD says a basilisk or a medusa that sees its reflection saves or turns to stone, and osrlib leaves that to the referee. The basilisk's entry gives it a 2-in-6 chance and the medusa's gives no chance at all, so both monsters' `mirrors` abilities are `manual`. The session applies the basilisk's `surprise` ability and both monsters' `averting_eyes` abilities, so those aren't `manual`. The medusa's disguise is also left to the referee. Locked by `test_avert_eyes.py`, and the surprise gaze outside a battle by `test_monster_tactics.py`.

An effect in a later attack routine applies when battle resolves that routine. The spitting cobra's poison bite is an alternative routine, so under the default policy the cobra bites in about half the rounds it fights and spits in the rest. The vampire's charming gaze is a special routine, which battle never rolls, so the referee applies it.

The session applies only the six kinds above and the petrifying gaze, so the referee applies the attack effects and gazes below from the monster's description:

- `1d10_heat`: bronze golem.
- `1d10_hull_damage`: lesser sea serpent, giant squid.
- `2_hull_damage`: giant squid.
- `6d6_hull_damage`: sperm whale.
- `blistering`: oil beetle.
- `blood_drain`: giant leech.
- `blood_sucking`: stirge, giant weasel.
- `charm`: devil swine, dryad, harpy, nixie, vampire.
- `charming_gaze`: vampire.
- `choking`: yellow mould.
- `confusion`: normal bat.
- `constriction`: giant octopus, rock python, giant squid.
- `consume_flesh`: green slime.
- `curse`: sprite.
- `disease`, where the ability is `manual`: giant rat, normal rat.
- `grab`: rhagodessa.
- `knock_down`: horned chameleon.
- `lodged_stinger`: killer bee.
- `petrify`: gorgon.
- `poison`, where the ability is `manual`: giant centipede, sea snake, tarantella.
- `rusting`: rust monster.
- `stench`: cave locust.
- `strength_drain`: shadow.
- `stun`: bull shark, freshwater termite, saltwater termite, swamp termite.
- `unconsciousness`: giant vampire bat.

Locked by `test_monster_attack_effects.py`.

### The session's context

The session adds bookkeeping, visibility rules, and referee commands on top of the kernel.

Characters and deaths:

- The session allocator assigns character ids as `character-NNNN`, in party order.
- Dead members stay in the party but count for nothing.
- The session records the clock round and the cause of each character's death. The cause is `poison` when the killing resolution was a poison save or the end of a poison's delay, and otherwise it's the kind of source.
- *Raise dead* always uses that record for its day count, and *neutralize poison* uses it for its round window only after a death by poison. A game that calls the kernel's spell functions without a session has to supply those values itself, like `CastContext.rounds_since_death` (see [Neutralize poison's revival window](#neutralize-poisons-revival-window-is-caller-attested-and-only-characters-revive)).

Visibility:

- Session flags are visible only to the referee, because how the game's content uses them is secret.
- Neither view includes the seed, which is only in the save.
- The player view contains only an enumerated list of fields, and a property test checks that nothing else leaks into it. It shows an undiscovered secret door as a wall.
- `SetDoorState` emits door events that only the referee sees.

Referee commands:

- `PlaceParty` triggers no hooks, traps, or keyed encounters, because it's a referee tool.
- A referee's `AdvanceTime` runs full bookkeeping but no wandering-monster checks, since the referee controls encounters.
- `SpawnMonsters` works only in a dungeon, because the combat space needs a party cell. It rolls its count dice on the encounter stream and opens a standard encounter, with both sides rolling surprise.
- While an encounter or battle is already running, `SpawnMonsters` adds one group to that encounter instead. That's how a referee brings in the allies an SRD monster summons. The group rolls no surprise or reaction, and it shares the encounter's stance and surprise. The spawn costs no time, so the group first acts in the next round.
- During a pursuit, the session refuses the spawn with `encounter.spawn.mid_pursuit`, because the chase runs at the pace of its slowest group.
- `SpawnNpcParty` sets the size of the band in one of three ways. `count_fixed` gives an exact size and draws nothing. `count_dice` rolls the size on the encounter stream. With neither set, the session rolls the B/X number for the band's kind. Setting both `count_fixed` and `count_dice` fails validation, as it does on `SpawnMonsters` ([#167](https://github.com/mmacy/osrlib-python/issues/167)).
- The session rejects `SpawnNpcParty` and `PlaceParty` while an encounter is running.

Locked by `test_session.py`, `test_crawl_properties.py`, `test_spawn_into_encounter.py`, and `test_rules_calls.py::TestSpawnNpcPartyTakesAFixedSize`.

### Persistence and replay

Saves are self-contained (adventure content embedded) with the accepted-command log always and the event log optional, and the event log preserves unknown event types losslessly (the log is a record and is never re-derived). Replay under a different engine version raises `ReplayVersionError`, while loading a save across engine versions remains legal. A synthetic in-test migration at schema version 1 exercises the migration framework. The standing test: `load(save)` equals `replay(seed, commands)` for the delve golden at every checkpoint. Locked by `test_persistence.py` and `test_phase4_goldens.py`.

### The treasure compiler: entry grammar, magic clauses, and TreasureRef semantics

Treasure-type entries parse on a fixed grammar: an optional `NN%:` presence gate, quantity dice with multipliers and glued coin suffixes, gem and jewellery counts, and structured magic clauses. A clause's parts are `any`, a named category, or a pool, a count or count dice, exclusions, and `+ 1 potion`-style extras. Pools pick uniformly among their categories before the sub-table roll, and an exclusion re-rolls exactly the named master-table category with draws consumed (the wandering-re-roll precedent). `TreasureRef` letters resolve by SRD section, and parenthetical letters are lair treasure. `extra_gp` joins the hoard, `multiplier` repeats the whole roll, `special` and `see_below` generate nothing, and the small-lair reduction stays prose for the referee. The engine rolls the d20 value table per gem and 3d6 × 100 gp per piece of jewellery, and both values fix at generation, with appraisal instantaneous and exact (B/X prices treasure for the XP economy, and a haggling or appraisal minigame is left to the game). Locked by `test_treasure_data.py` and `test_treasure_kernel.py`.

### Treasure generation order, tiers, and the treasure stream

Generation follows printed-entry order, depth-first per item: each gem's value, each jewellery's value, and each magic item fully resolved (sub-table, armour-type d8 for generic armour, charges at creation, ammunition quantities, scroll contents, and sword sentience last, with the special-purpose 1-in-20 first and then the printed procedure) before the next entry, all on the `"treasure"` stream, so draws are reproducible from the stream alone. Tier selects the printed B/X sub-table columns from the party's highest living level, with 4+ as expert. À la carte callers choose explicitly and pass a stream and an allocator. Locked by `test_treasure_kernel.py`, `test_magic_items.py`, and the phase 5 hoard golden.

### Hoards generate lazily, appear as engine-created caches, and are untrapped

The engine rolls treasure when it's first needed, not when the adventure loads, and never changes the adventure you wrote to do it.

- A keyed encounter's lair hoard, the treasure kept in the monsters' lair, is rolled once, when the group first spawns.
- The engine puts the hoard in a cache, a container of treasure the party can take, on the first cell in the area's list of cells. It records the cache in the dungeon state, the overlay where the session records what play has changed, so the authored specs stay frozen. Piles and features gain fields for valuables and magic items, which default to empty.
- When the party evades a keyed encounter and comes back, the monsters spawn again, but the hoard doesn't. The session records the area in `DungeonState.generated_lair_hoards` when it rolls the hoard, and doesn't roll it again. The party finds the cache from the first visit, or nothing if it already took that treasure.
- Carried treasure is rolled when the monsters spawn. `SpawnMonsters` rolls no carried treasure, because the referee grants carried loot explicitly.
- An area's treasure specs are rolled the first time the party enters the area. Unguarded treasure uses the bands on the SRD's *Designing a Dungeon* page.
- Generated hoards are untrapped, because a trap on treasure is authored content, not something generation produces.

For one keyed group, the order of draws on the treasure stream is the carried treasure for each printed line, then the lair hoard. Locked by `test_exploration.py::TestTreasure`, `test_loot_and_traps.py::TestLairHoardOnce`, and the milestone golden.

### Loot drops: slain sides drop, routed sides keep

At battle's end, slain groups drop their carried treasure and group bundles into a pile on the party's cell, while a routed (fled) group keeps everything and leaves with it. Defeated NPC adventurers dump their kit and share of the group's U + V bundle the same way. Locked by `test_encounter.py` and the milestone golden.

### The end-of-adventure award is the departure-snapshot valuation delta

`EnterDungeon` snapshots the party's valuation (coins plus carried valuables at exact value, magic and mundane gear at zero) and `TravelToTown` awards the delta: floored once from copper, clamped at zero, added to the defeated-monster ledger's XP, divided evenly among living members with floor division and the remainder dropped, applied through `apply_xp` (class modifiers and the one-level clamp apply per member). Dead members' carried treasure counts toward the delta but draws no share, and a party with no survivors gets no award. The award clears the defeated-monsters ledger and the snapshot. There's no recovered-treasure ledger: schema version 2 removed it, and the migration drops the field from version-1 saves. Selling in town after the award changes nothing. Locked by `test_award.py` and `test_persistence.py`.

### Treasure weight covers coins, valuables, and the five priced magic categories

`treasure_weight_coins` counts the weight of a character's treasure, in coins: the purse, valuables, and some magic items.

- The magic items it counts are the kinds that the SRD's encumbrance table gives a weight: potion, scroll, rod, staff, and wand. Those rows are [`TreasureWeight`][osrlib.core.items.TreasureWeight].
- Rings and miscellaneous items weigh nothing unless their page gives a figure.
- The Bag of Holding's printed loaded weight of 600 coins counts while the bag contains anything.
- Enchanted weapons and armour count as equipment, alongside their mundane bases, not as treasure, so basic encumbrance's treasure tracking stays accurate. They weigh what the base weighs, except enchanted armour, which weighs half, as the SRD's armour page says. The base is the instance's `base_item_id`, or the template's when the instance has none.
- A spent stack, which is a magic item at quantity 0, weighs nothing, because the character no longer carries it.

Locked by `test_items.py` and the closed-form property in `test_crawl_properties.py`.

### The magic item catalog: 164 templates, automated mechanics, and manual prose

The compiler builds every item on the eight printed sub-tables (both B and X probability columns, with the sparse basic column compiled as printed), with hand-curated mechanics in `_MAGIC_ITEM_MECHANICS` (as spells have in `_SPELL_MECHANICS`), and every other item compiles as prose tagged `manual`. Versus clauses compile as structured category and tag references (lycanthropes and undead as category tags, spell users and regenerating creatures as ability-derived template sets), never string-matched prose. The girdle of giant strength is the one automated item whose mechanics branch on a `Ruleset` flag (`variable_weapon_damage`), named as such in the tests. The regeneration ring gates `while_alive`, the displacer cloak penalizes melee attackers only, and the energy-drain sword drains automatically on every hit at the `level_minimum` XP policy (osrlib's default for a computer game, over RAW's "may command"). Truncated SRD text completes via `overrides/magic_items.json` with provenance (`helm_of_telepathy`). Locked by `test_srd_data.py` and the `test_magic_items.py` census.

### Magic items enter play unidentified, identify on first meaningful use, and curses stick

Instances mask behind category display names (an enchanted arm is "a {base} with a faint aura", a miscellaneous item "a curious device") until identified: first meaningful use, the referee's `IdentifyItem`, a worn item's effect attaching, or being attacked while wearing enchanted defenses (the bonus shows itself in battle). A cursed item reveals at the same trigger and sticks: the engine rejects `UnequipItem` for a cursed sword, weapon, armour, shield, or ring alike (`items.curse.stuck`) until *remove curse*. A cursed ring reveals the moment it is equipped, because its onset is automatic. Charges are rolled at creation and referee-only forever after (RAW "undiscoverable"): the player view never shows a charge count, a potion's remaining duration, per-item state, sentience, or a hoard not yet found. Locked by `test_magic_items.py`, `test_exploration.py`, and the leak property test.

### Item bonuses use a separate channel, outside the cumulative caps

Equipped-item bonuses (enchanted arms, rings of protection, the displacer cloak) compute from equipped inventory at query time, never as ledger effects, so unequipping is instant and nothing needs dispelling. Item-sourced ledger effects (potion durations, wards, the weakness onset) attach with `from_item=True`, are undispellable, and sit outside the cumulative largest-bonus-plus-largest-penalty cap, which applies among spell-kind effects only: `modifier_total` is the capped spell contribution plus the summed item contribution. `strength_set`, `ac_bonus`, and the damage-multiplier kinds join `MODIFIER_KINDS`. Locked by `test_effects.py`, `test_combat.py`, and `test_magic_items.py`.

### Potions: hidden 1d6+6-turn durations, mixing, and the weekly inversion

A drunk potion runs 1d6 + 6 turns, rolled on the effects stream and hidden from the player. Drinking while another potion runs cancels both and disables potions for 3 turns, except for instantaneous or permanent forms. Invulnerability inverts on the clock's day: used more than once in a week, it applies −2 instead of +2, tracked per character. Locked by `test_exploration.py::TestUseItem`.

### Scrolls: per-spell burn, minimum caster level, the thief fizzle, wards, and curses

Spell scrolls burn per spell cast. They are validated and resolved alike at the minimum class level able to cast the spell per the compiled progression (a `_ScrollReader` proxy, since the character is never mutated), so the scroll's caster level settles how many targets a mode demands and how far it reaches. They also waive the arcane magical-script reading gate (osrlib's reading: the scroll itself is the medium). A 10th-level thief reads arcane scrolls with the printed 10% error chance, resolved as a fizzle: the spell burns and nothing resolves. A cursed scroll triggers on reading and rolls uniformly among the six compiled curses (energy drain and slow healing automated, the rest manual): energy drain resolves through `drain_levels` at the `halfway` XP policy (the curse's wording), and slow healing doubles the natural rest-day cadence while halving healing spells, floored. The curse halves healing where *cause disease* blocks it, so the curse is no disease and attaches no condition. Protection scrolls attach one party-wide ward effect to the reader with per-HD-band counts as structured params (`affected_1_3`, `affected_4_5`, and `affected_6_plus`), and the ward bars the listed creatures from melee (the *protection from evil* machinery) and breaks for the whole party when any member melees a barred monster. Locked by `test_casting.py`, `test_exploration.py`, and `test_battle.py`.

### Devices spend a charge and a round, exhaust to inert, and never recharge

Wand, staff, and rod activations spend one charge and the user's action, resolve in the magic phase through the kernel damage/save path with the item's printed area shape under [the area footprint](#the-area-footprint) rule, and an exhausted device goes inert, so further activation is a rejection (`items.device.inert`), never a silent no-op. Devices never recharge and never show charges. Device areas are destructive, like *lightning bolt*: a kill destroys the victim's equipment. Locked by `test_battle.py` and `test_exploration.py`.

### Rings cap at two, worn on hands

`MAX_RINGS_WORN = 2` (RAW: one on each hand), the third rejected at equip validation (`items.ring.hands_full`). The ring of protection's 5'-radius variant extends its AC bonus to the wearer's rank-mates against attacks in battle, because the battle space has no finer adjacency than the rank and the printed example ("two characters fighting beside the wearer") is exactly melee defense. The aura arrives as caller-asserted context (`AttackContext.defender_ally_ac_bonus`), never stacks across rings, and the wearer's +1 goes through the equipped-item channel as usual. Saving throws and out-of-battle resolution honor the wearer alone, because saves resolve inside the character-scoped kernel, and outside battle the party has no spatial model. Locked by `test_items.py` and `test_magic_items.py`.

### The item streams

The item and treasure stream keys are `"treasure"` (every generation draw: presence gates, quantity dice, values, sub-table rolls, charges, sentience) and `"npc_party"` (NPC composition: alignment, class rows, level dice, scores, hit dice, spell picks). Use-time item resolution (device damage and saves, scroll-cast targeting, the potion-effect dice) draws from the `"magic"` stream beside spells. Attach-time rolled durations and the cursed-scroll curse pick stay on `"effects"` per [The combat kernel streams](#the-combat-kernel-streams), and energy-drain sword drains and the drain curse resolve on `"advancement"` beside `drain_levels`. Locked by the phase 5 goldens and `test_treasure_kernel.py`.

### NPC adventuring parties: generation

One d6 alignment roll for the whole party, then per member on the npc-party stream: the d8 class-and-level row, the level dice by kind (basic 1d3, expert per row, demi-human caps asserted at compile), 3d6-in-order scores with class ability requirements skipped (the printed d8 row is authoritative, and a requirement re-roll would contradict it), the first-level hit die and one `level_up` roll per level above first, and uniform spell-slot picks (books match memorization for arcane casters). Members wear fixed class kits, the SRD's "Basic equipment appropriate to their class" made concrete, with plate upgrades for expert clerics, dwarves, and fighters. The dwarf's battle axe is two-handed, so the kit shield is carried rather than wielded. The kit's generic Axe and Bow resolve to `battle_axe` and `long_bow`. Expert members roll Expert magic items at 5% per level per suitable sub-table (basic parties take none, per RAW's Expert-only note), unusable rolls ignored and better arms equipped, and the group shares one U + V treasure bundle on the treasure stream. Locked by `test_npc.py` and the NPC-party golden.

### NPC parties in play: encounter sides like any other

`SpawnNpcParty` and wandering `npc_party` rows field a real generated party as an encounter side (count dice stay on the wandering stream), and `EncounterGroup.monster_ids` contains combatant ids spanning monsters and NPCs. The roster event is referee-visibility, so players learn compositions by fighting. NPC groups check morale at the Veteran's ML 9, are always intelligent for distraction, and are worth level-as-HD XP recorded under `npc:<class_id>`. The default `NpcPartyPolicy` heals a below-half ally with the highest cure available, drinks a healing potion when no cure remains, casts automated attack spells highest-level-first, shoots beyond 5', and melees otherwise. NPC casts declare at the round's top and disrupt exactly like the party's. Locked by `test_npc.py` and `test_battle.py`.

### Town services: full-value selling and the temple's six services

`SellTreasure` pays a valuable's exact rolled value to its carrier, because appraisal is exact (see [the treasure compiler](#the-treasure-compiler-entry-grammar-magic-clauses-and-treasureref-semantics)). Magic items have no fixed sale value, so the session refuses to sell one with `town.sell.no_fixed_value`. Pricing one is left to the game.

`PurchaseHealing` offers six services at prices osrlib invented, recorded here:

- Cure light wounds, 25 gp.
- Cure serious wounds, 100 gp.
- Cure disease, 150 gp.
- Neutralize poison, 150 gp.
- Remove curse, 200 gp.
- Raise dead, 1,500 gp.

Each service casts the real spell through the kernel, at the lowest cleric level able to cast it, and honours the windows in the death record: raise dead's day limit and the 14-day weakness. Remove curse also unsticks cursed equipment. Every service works in any town, whatever its `services` list says, because town size and cleric rosters are left to the game.

The temple refuses a service that would do nothing, with `town.healing.no_effect` ([#167](https://github.com/mmacy/osrlib-python/issues/167)):

- Raise dead on a living member.
- Cure disease on a member who isn't diseased.
- Neutralize poison on a member who is neither poisoned nor dead of poison within the spell's 10-round window.

The funds check runs first, so a party that can't pay gets `items.purchase.insufficient_funds`, not `town.healing.no_effect`. Like every refusal, this one takes no coin, no draw, and no time.

The temple charges the party, not the patient:

- The fee comes from the treated member's purse first, then from the other members' purses in marching order, dead members included.
- Each purse pays in whole gold pieces. It pays the smaller of two amounts: what's still owed, or its total value rounded down to a whole gold piece. It keeps any value below a gold piece.
- Once the fee is paid, the purses after that pay nothing.

So what the party can spend is the whole gold pieces in each purse, not the purses' total worth. A party whose whole gold pieces fall short is refused with `items.purchase.insufficient_funds` and keeps every coin. For example, at 10 sp to 1 gp, two members who each have 12 gp and 5 sp are worth 25 gp between them, the fee for cure light wounds. Each purse can pay only 12 whole gold pieces, though, so together they pay 24 gp, and the party can't buy the service.

Raise dead is the reason the party pays. The patient is a corpse, no rule gives a corpse coin, and a party that splits its treasure never has 1,500 gp in one purse. So the documented revival flow (game over, `PlaceParty` to town, raise dead) works only if the party's purses can pay together. Locked by `test_award.py::TestTownServices`, `test_award.py::TestPooledHealingFees`, and `test_rules_calls.py::TestTempleRefusesAUselessService`.

### Listener-returned events are authored events

The session listener contract's returned-events channel is for events a listener authors directly. A listener that reacts by executing referee commands (the fetch-quest pattern) must return none of those commands' events, because the nested `execute` already logged them and returning them would double-log. Command-log replays therefore run listener-free and converge with the original event-for-event, and the listener's state store belongs to the game: saves restore it, the game re-registers the listener, and replay never re-derives it. Locked by `test_session.py` and the phase 5 milestone golden.

## Documented adaptations

`Ruleset`-flagged deviations from rules-as-written. The flags `hp_reroll_at_first_level`, `encumbrance`, `variable_weapon_damage`, `individual_initiative`, `thac0_arithmetic`, `weapon_reload`, and `hd5_counts_as_magical` are SRD optional rules rather than adaptations, so this section doesn't list them.

### `deprivation_penalties` (default off)

Consumption is tracked regardless. With the flag on, the SRD's "at the referee's discretion, for example" starvation list gets defaults drawn from the SRD's examples: after one full day without food or water, −1 to attack rolls (an engine-written modifier effect) and the rest cadence doubles (fatigue after three unrested turns instead of six). After two days, movement also halves, and from the third day on a daily 1d4 hit-point loss ticks on the effects stream. Water and food deprivation don't stack, and the worse track applies. The schedule's numbers are invented over the SRD's open list. Locked by `test_exploration.py::TestProvisions`.

### `aoe_friendly_fire` (default on)

Areas overlapping a melee catch friends: an area landing on a group at melee range (≤ 5') appends the engaged party front rank after the monsters, in marching order, consuming remaining footprint capacity. Off means areas never include party members among a monster group's candidates. Locked by `test_battle.py::TestAreaFootprint`.

### `formation_width_limit` (default on)

Available space caps combatants fighting abreast. RAW prints one number and leaves the rest to judgement. The SRD says: "The referee should judge the number of opponents that can attack a single combatant, bearing in mind the combatant's size and the available space around them. **10' passage:** Enough space for at most 2–3 characters to fight side-by-side". osrlib uses the conservative end, two in ten feet, and measures the space instead of naming a number per kind of cell. The rank width is the widest square of unbroken floor including the party's cell, five feet of frontage to a combatant: two in a one-cell passage however far it runs and at a junction of two such passages, four in a room two cells square, eight in one four cells across. The measured space stops at walls, at doors (a doorway is a threshold, not room to fight abreast), and where the space changes, so a room's open mouth never counts the corridor beyond it. The same cap bounds how much of an area effect's footprint a formation absorbs. Off lifts the cap, so every combatant may melee and area footprints are unbounded. Locked by `test_battle.py::TestFormationWidth`.

### `magic_item_death_save` (default on)

The SRD gives the referee an optional save for a dead character's magic items, and osrlib makes it the default. When a kill destroys equipment, as lightning and disintegration do, the engine rolls a save for each carried magic item:

- The engine rolls 1d20 for the item. The item saves when the roll reaches the owner's saving throw target for the source's category: breath as breath, spells as spells, devices as wands, and anything else as death.
- The item adds its best combat bonus to the roll, the highest of its attack, damage, and AC bonuses, so a better item is more likely to survive. A cursed item adds its penalty instead.

Items that survive go to the drop pile instead of vanishing, and `EquipmentDestroyedEvent.saved_items` lists them. When the victim is a party member, the session moves the items to the drop pile on the party's cell in the command that killed the member, because no command can take anything from a dead member's inventory. With the flag off, items share their owner's fate. Locked by `test_combat.py::TestDestroyEquipment` and `test_loot_and_traps.py::TestBreathSavedItems`.

### `xp_award_timing` (default `on_return`)

`on_return` is RAW: monsters and treasure pay out at the adventure's end, the departure-snapshot valuation delta divided among the survivors. `immediate` is the CRPG convention offered as an adaptation: monster XP at encounter end and treasure XP at acquisition (take, quest grant), each divided among the living at that moment, and drops never refund, so the return then awards nothing. Neither timing double-pays. Locked by `test_award.py::TestImmediateTiming`.
