# Adaptations and interpretations

osrlib follows OSE rules-as-written (RAW). Every place where that isn't enough is recorded here, as one of two kinds of entry:

- **Interpretations**, where the SRD text is ambiguous or silent, and osrlib commits to one reading.
- **Documented adaptations**, where the tabletop game assumes a human referee, and osrlib provides a default behind a `Ruleset` flag.

Look here instead of searching docstrings. Every entry is also stated in the docstring of the member it applies to and locked by a test.

Each entry also cites the source files and test names that lock it. Those citations point into [the osrlib repository](https://github.com/mmacy/osrlib-python), not to pages on this site.

## Terms used here

- **Template and instance** - A template is the frozen catalog entry for a monster or an item, like a [`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]. An instance is one copy of it in play, like a [`MonsterInstance`][osrlib.core.monsters.MonsterInstance], with its current hit points and conditions.
- **Effects ledger** - The [`EffectsLedger`][osrlib.core.effects.EffectsLedger] contains every running effect, like a spell, a poison, or a lit torch, and resolves each one as the game clock moves. On this page, "the ledger" means the effects ledger.
- **Round and turn** - A round is 10 seconds and a turn is 10 minutes, as in the SRD. Battles run in rounds, and exploration runs in turns.
- **Cell** - One 10' × 10' square of a dungeon level. An entry in a printed table is called a table entry here, not a cell.
- **Draw and stream** - A draw is one random number the engine takes. Every draw comes from a named stream, so one subsystem's rolls never shift another's. A step that "consumes no draw" leaves every later roll as it would have been. See [RNG streams](reference/rng-streams.md).
- **Stable order** - When the engine sorts things that tie, it keeps them in the order it was given them, so the result never depends on chance.
- **Kernel and session** - The kernel is `osrlib.core`, the rules functions you can call directly (see [Rules without a session](guides/rules-without-a-session.md)). The session is a [`GameSession`][osrlib.crawl.session.GameSession], which runs a whole adventure through commands and calls the kernel for you.
- **Caller** - The code that calls a kernel function: your game, or the session. A value "the caller supplies" is an argument that code passes, because the kernel can't work it out.
- **Ruleset flags** - Options on [`Ruleset`][osrlib.core.ruleset.Ruleset]. Some switch the SRD's optional rules on or off, and others switch the defaults that [Documented adaptations](#documented-adaptations) lists.
- **Marching order** - The order of the party's members. It decides the battle ranks, and which member a rule means by "the first living member".
- **Front rank and back rank** - In battle, the session cuts the living marching order into rows as wide as the formation width. The first row is the front rank, and every member behind it is in the back rank. Melee reaches only the front rank. See [The battle round](#the-battle-round).
- **Range track** - In battle, each monster group has a separate distance from the party, in the style of The Bard's Tale, instead of a position on a map.
- **Declaration** - What one party member will do in a battle round, like attack a group or cast a spell. The party sends every member's declaration in one `ResolveBattleRound` command.
- **Movement step and magic phase** - Parts of a battle round. The side that wins initiative acts first. In the party's turn, the session resolves movement, then missile attacks, then magic, then melee.
- **Policy** - An [`ActionPolicy`][osrlib.crawl.battle.ActionPolicy] chooses what a monster group does each round. The default is [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy], and a game can register a different one.
- **Group** - An [`EncounterGroup`][osrlib.crawl.encounter.EncounterGroup] is a group of monsters in an encounter. A group has one distance from the party, checks morale as a whole, and is what a party member's attack aims at.
- **Stance** - The monsters' attitude toward the party in an encounter, which the reaction roll sets. See [The encounter procedure](#the-encounter-procedure).
- **Morale score** - A monster's ML in its stat block, from 2 to 12. A morale check rolls 2d6, and a roll over the score breaks the group.
- **Player view and referee view** - What a session shows the players and the referee: a [`PlayerView`][osrlib.crawl.views.PlayerView] and a [`RefereeView`][osrlib.crawl.views.RefereeView]. See [Views and visibility](guides/views-and-visibility.md).
- **Keyed encounter** - Monsters that an adventure places in an area in advance, as a [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter]. They spawn when the party enters the area.
- **`manual` ability** - A [`MonsterAbility`][osrlib.core.monsters.MonsterAbility] marked `manual` is text for the referee to read and act on. osrlib doesn't apply it.
- **Gate** - A condition something has to pass. An immunity gate on a monster lets damage through only from the sources it lists, like silver or magic weapons.
- **Destructive death** - A death from a source that also destroys what the victim carries, like a lightning bolt or a breath weapon.
- **Clamp** - To keep a value inside a range: a value below the range becomes the lowest value, and one above it becomes the highest.

## Interpretations

### Multi-prime-requisite XP tiers have no penalties

The elf and the halfling get their stated XP bonuses and never an XP penalty. An elf with INT 9 and STR 9 has a 0% modifier, never −10%.

The SRD applies the standard XP-modifier table "to characters with a single prime requisite", and the elf's and halfling's class descriptions list only bonuses (+5%, +10%). So osrlib reads the two classes as having bonus tiers and no penalty tiers. Locked by `test_classes.py::TestXpModifier::test_multi_prime_requisite_classes_have_no_penalties`.

### XP tiers evaluate best-first, first-match-wins

Every class's XP modifier uses one representation: a list of tiers, each a `{modifier_pct, minimum scores}` pair. The engine checks the tiers from best to worst, and the first tier whose minimums all hold wins.

- The standard table's penalty rows work only this way. A prime requisite of 7 has to fall past the +10%, +5%, and no-modifier tiers to reach −10%.
- The halfling's +5% tier for "at least 13 in one prime requisite" becomes two tiers, each with one minimum, under the same rule.

Locked by `test_classes.py::TestXpModifier`.

### XP percentage results floor

osrlib rounds a modified XP award down. The SRD never says how to round a ±5/10/20% XP modifier, so osrlib uses integer arithmetic that rounds toward negative infinity. Locked by `test_classes.py::TestApplyXp::test_modifier_floors`.

### First-level HP re-roll repeats until the die shows 3 or more

With `hp_reroll_at_first_level` on, a first-level character re-rolls their class hit die while it shows 1 or 2. The engine checks the raw die, before the CON modifier, and each re-roll consumes a draw.

The optional rule says a roll of "1 or 2" may be re-rolled, but not whether a re-rolled 1 or 2 is re-rolled again, so osrlib keeps re-rolling. The loop always ends, because the smallest class hit die is a d4, which can show 3 or 4. Locked by `test_character.py::TestRollHitPoints`.

### Adjustment reductions are even per lowered score

When a player lowers ability scores to raise another, each lowered score has to drop by an even amount, and the total raise must be exactly half the total reduction.

The SRD says "For every two points by which an ability score is reduced, one point may be added". It doesn't say whether one score may drop by an odd amount as long as the total is even. osrlib applies the two-for-one trade to each score, so no point of a reduction goes unused. Locked by `test_abilities.py::TestAdjustment`.

### Gear combat facets are exempt from class weapon policies

Any class may buy, carry, and use a torch, holy water, and burning oil, whatever its weapon restrictions.

- All three items appear on both the weapon table and the gear list. Each compiles as one gear item with an embedded combat facet, the weapon stats it uses in a fight, so no item has two ids.
- Class weapon policies cover only the weapons list. A strict reading of the quality tags would forbid a cleric holy water or a torch, so osrlib applies one rule to all three items instead.
- That allows more than a strict reading would, and on purpose: a magic-user, who may use only a dagger in RAW, may also throw oil or swing a torch.

Locked by `test_items.py::TestEquipLegality`.

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

Under detailed encumbrance, a character who carries any miscellaneous gear adds a flat 80 coins of weight, once, however much gear it is.

- The SRD gives adventuring gear no weight for each item, and says gear "may be counted as 80 coins of weight". osrlib reads that as one flat amount.
- Items whose listed weight is `-`, like holy water and burning oil, have no separate weight. They're gear, so the flat 80 covers them.

Locked by `test_items.py::TestWeights`.

### Ammunition weighs 0

Ammunition compiles with a weight of 0. The ammunition table has no weight column, and the SRD says "The listed weight of missile weapons already includes the weight of the ammunition and its container." Locked by `test_srd_data.py::TestEquipmentData`.

### Sling stones cost 0, lot size 1

Sling stones cost 0 and are bought one at a time. The table entry for their cost reads `Free`, so osrlib sets the cost to 0 and the purchase lot to 1. Locked by `test_srd_data.py::TestEquipmentData`.

### INT-granted languages come from the Other Languages table and may not duplicate natives

A character with high INT picks extra languages from the twenty Other Languages, and can't pick a language their class already speaks. So a dwarf can't spend an INT language on Dwarvish.

The SRD says such a character "may also choose additional languages from the list of languages available in the setting", at the referee's discretion. osrlib reads the setting's list as the Other Languages table. Locked by `test_character.py::TestExtraLanguages`.

### The attack matrix is clamp(THAC0 − AC, 2, 20) and extends beyond the printed columns

The roll an attacker needs is `clamp(THAC0 − AC, 2, 20)`: THAC0 minus the defender's AC, raised to 2 if it's lower and lowered to 20 if it's higher.

- Every entry in the printed attack matrix equals that formula, and a property test over the compiled table checks it.
- An AC outside the printed columns, −3 to 9, uses the same formula. The printed range is a limit of the page layout, not of the rules.
- The limits of 2 and 20 are what make matrix mode differ from the `thac0_arithmetic` flag, which uses THAC0 − AC with no limits, once modifiers push a total past them.

Locked by `test_monster_data.py::TestCombatTables` and `test_combat.py::TestAttackRoll::test_matrix_versus_arithmetic_divergence_at_the_plateaus`.

### Initiative ties re-roll

Tied sides re-roll initiative until they differ, and so do tied individuals among themselves. They re-roll in stable order, and each re-roll consumes draws.

RAW offers "re-roll or resolve simultaneously". Resolving at the same time would need a different combat model, which the engine doesn't attempt, so osrlib always re-rolls. Locked by `test_combat.py::TestInitiative`.

### Slow actors act after all non-slow actors

A combatant with a slow weapon acts after every combatant without one. Among themselves, slow actors go in their side's initiative order, or in each actor's own initiative under individual initiative, then in stable order.

That's how osrlib reads "always act last, as if they had lost initiative" for the general case. Locked by `test_combat.py::TestInitiative::test_slow_actors_act_last_by_side_initiative`.

### Helpless defenders are hit automatically in melee, consuming no attack draw

A melee attack on a paralysed, sleeping, or petrified defender hits automatically. The engine makes no attack roll, so it consumes no draw, and rolls only damage. A missile attack on the same defender rolls to hit as usual.

- The rule is RAW: "Helpless opponents, such as those magically paralysed or frozen, can be automatically hit in melee".
- A sleeping creature can't defend itself, so osrlib counts it as helpless too. For what else sleep does in combat, see [Asleep grants auto-hit and dies-to-a-blade](#asleep-grants-auto-hit-and-dies-to-a-blade-dozing-is-a-caller-modifier).
- A petrified creature is frozen in stone, so it counts as helpless ([#166](https://github.com/mmacy/osrlib-python/issues/166)).

A defender whose AC is `No hit roll required`, like green slime or yellow mould, is also hit without a roll. That applies to missile attacks too. Locked by `test_combat.py::TestAttackRoll::test_helpless_defender_auto_hit_consumes_no_draw` and `test_rules_calls.py::TestMeleeHitsAPetrifiedCreature`.

### Morale "incapacitated" means dead, paralysed, petrified, or asleep

A creature counts as incapacitated when it's dead, paralysed, petrified, or asleep. The RAW trigger text says "slain, paralysed, etc", and osrlib uses this reading for two rules:

- The morale check that a side makes when half of it is incapacitated.
- The rule that a sleeping creature dies to a blade.

Other morale rules follow RAW:

- Situational adjustments to a morale check are clamped to ±2.
- They never apply to a morale score of 2 or less, which means the monster never fights, or 12 or more, which means it never checks.
- After two passed checks, a monster makes no more checks.

Locked by `test_combat.py::TestMorale`.

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

Damage resolves in a fixed order:

1. The gate. If the defender's `harmed_only_by` or energy defenses exclude the source, the engine rolls no damage, and the event reports that.
2. The damage roll, plus STR for melee.
3. The doublings: brace, charge, and back-stab.
4. The minimum of 1.
5. The reductions, rounded down and never below 1.

Halving for a successful save-for-half also rounds down. A back-stab adds +4 to hit and doubles damage, both from the thief tag's params, when the caller says the target is unaware and attacked from behind. Locked by `test_combat.py::TestDamagePipeline`.

### The WIS save modifier applies to magical non-breath saves only

The WIS modifier applies to saving throws against magic, except saves against breath attacks. That's how osrlib reads "does not normally include saves against breath attacks". Any further referee discretion is a modifier the caller supplies. Locked by `test_combat.py::TestSavingThrows`.

### `hd5_counts_as_magical` implements the whole invulnerabilities optional rule

The `hd5_counts_as_magical` flag applies the whole SRD optional rule it cites, not only the half its name describes. With the flag on, two kinds of monster get through a gate that admits only silver or magic weapons:

- A monster of 5 HD or more.
- "another invulnerable monster", in the rule's words, which means a monster that has such a gate itself.

Applying half an optional rule would be a departure from the SRD that nothing documents. The flag follows the rule's wording, so it affects only weapon gates whose keys are silver, magic, or both. Gates keyed to an element, and mixed gates, are unaffected, like the mummy's fire-or-magic gate and the black pudding's fire-only gate. Locked by `test_combat.py::TestDamagePipeline`.

### The `holy` damage key is admitted through any gate on undead targets, and only there

Holy water damages an undead target through any gate. Against a living target, the throw resolves normally, and the damage pipeline reports no effect.

- "Holy water inflicts damage on undead monsters": the specific rule overrides the general immunity. Otherwise the wight's silver-or-magic gate would stop the one weapon made for it.
- Throwing holy water at a living target is deliberately *not* refused. A refusal is free (no roll, no time, no log entry), so it would be a way to test for undead at no cost.

Locked by `test_combat.py::TestSplashWeapons`.

### Variable weapon damage off means 1d6 for weapons and gear facets

With variable weapon damage off, every PC attack with a weapon *or* a gear item's combat facet deals 1d6, as RAW says: "PC attacks inflict 1d6 damage".

- Unarmed attacks stay 1d2, because the unarmed rule is a specific rule, not a weapon.
- Monster damage doesn't change, because monsters "deal the damage indicated in the description".

Locked by `test_combat.py::TestDamagePipeline::test_variable_damage_flag_off`.

### Splash damage is two applications

A splash weapon deals its damage twice: once when it hits, and again at the next round boundary, when the douse effect expires. That's how osrlib reads "Inflicted for two rounds".

- Unlit oil deals no damage.
- A caller can turn spilled oil into a pool attached to a location: a 3-foot pool that burns for 1 turn once lit and deals 1d8 to creatures passing through. The caller says who passes through.
- In a session, the session anchors the pool to a cell, as [Light, darkness, and location-bound effects](#light-darkness-and-location-bound-effects) describes.
- Monsters with `uses_fire` ignore burning oil.

Locked by `test_combat.py::TestSplashWeapons`.

### Torch and burning oil deal fire damage, and holy water has the holy key

A torch or burning oil deals fire damage, and holy water deals `holy` damage. The three items are listed both as gear and as weapons, and osrlib sets their damage sources. Because a torch or oil hit is fire damage, it counts toward a troll's fire and acid total. Locked by `test_combat.py::TestSplashWeapons::test_douse_applies_twice_then_expires`.

### Breath-weapon daily limits are per-monster data

A breath weapon's limit of three uses a day is data on each monster, not a general rule.

- The SRD prints the limit for the dragons, the dragon turtle, and the chimera.
- The hellhound has no daily limit. Its 2-in-6 chance each round is data that the default policy reads, and the kernel resolves the breath whenever it's used.
- osrlib treats every breath weapon as a destructive death. The SRD's examples of destroyed items ("a lightning bolt spell or a dragon's breath") illustrate energy deaths in general, so a hellhound's fire kill destroys the victim's mundane equipment too.
- A dragon is immune to nonmagical sources of its breath's element, or elements, and saves automatically against magical ones.

Locked by `test_combat.py::TestBreathAndGaze`.

### The HD-budget targeting mode consumes weakest-first

A spell with a Hit Dice budget affects the weakest creatures first.

- Candidates sort by effective HD. Less than 1 HD counts as 1, fixed hit point bonuses are dropped, and a character counts their level. Ties keep stable order.
- The budget spends whole creatures. A target whose HD exceed what's left is skipped, and selection goes on to the next.

For the *sleep* spell's arithmetic, see [Sleep's mode arithmetic](#sleeps-mode-arithmetic). Locked by `test_combat.py::TestTargeting`.

### Monster XP validation: above-21-HD inflation and the negative-modifier band

The XP printed in a stat block is authoritative. When the compiler builds the data, it checks each printed value against the XP table:

- The expected XP is the base for the HD row, plus the number of asterisks times the bonus column. Above 21 HD, *both* amounts first gain 250 for each HD above 21. The dragon turtle (HD 30*, XP 9,000) matches this reading.
- A negative hit point modifier maps to the *lower* band. The goblin's 1-1 checks against "Less than 1" (XP 5), and still uses the unmodified "Up to 1" row of the attack matrix (THAC0 19 [0]). Attacking as 1 HD higher applies only to a positive modifier.
- A mismatch with the table fails the build unless an override records it.

Locked by `test_monster_data.py::TestCombatTables`.

### Poison failure is death, with optional onset; riders leave the damage unaffected

A failed save against poison is death, either at once or after an onset delay.

- The target saves versus death or poison.
- A form with a delay, like the giant rattler's "death in 1d6 turns", becomes an effect that kills when the delay runs out.
- When poison comes with a damaging attack, the save doesn't change the damage, per RAW.

Locked by `test_effects.py::TestLedger::test_delayed_death_poison`.

### Petrification suspends the target's other effects

While a target is petrified, its other effects pause: they don't tick, and their durations stop. So an adventurer who was poisoned before turning to stone is still poisoned after *stone to flesh*. Petrification is permanent but recoverable, because a petrified creature isn't dead. Locked by `test_effects.py::TestLedger::test_petrification_suspends_other_effects`.

### A party turned wholly to stone has fallen

When a command's events kill or petrify a member and nobody in the party is left standing, the session ends in `game_over`, as it does when the last member dies. The SRD doesn't say what happens when nobody is left but statues, so this is osrlib's judgement.

- A member stands while they're neither dead nor petrified.
- In a battle, `battle.ended.defeat` comes before the end of the game.
- A statue isn't dead, so it still counts among `Party.living_members()` and keeps its rank.
- The check runs only for a command whose events killed or petrified a member, not whenever the party has nobody standing. So a stone party carried to town with `PlaceParty` stays in `town`.
- The temple's services are clerical, and *stone to flesh* is a magic-user's spell. So the way back is a magic-user who joins with `AddMember` and casts it.

Locked by `test_petrified_party.py`.

### The canonical tick order

At each round boundary, the ledger resolves expirations before ticks, the periodic steps of effects like a poison that deals damage each round. Effects due at the same moment resolve in the order they were attached, with ties broken by effect id.

Only the effects engine writes creature conditions, except `dead`, which the kernel's death routine writes. Locked by `test_effects.py::TestLedger`.

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

Mummy rot blocks magical healing and slows natural healing: the victim recovers 1d3 hit points once per ten consecutive full days of rest, and the effect counts the days. That's how osrlib reads "Magical healing is ineffective; natural healing is ten times slower". *Cure disease* removes it.

`apply_healing` treats healing as `magical` unless the caller names another source, because osrlib treats instantaneous healing as magical healing. So a caller that forgets to name the source still respects the block. Locked by `test_effects.py::TestMummyRot`.

### Asleep grants auto-hit and dies-to-a-blade; dozing is a caller modifier

The asleep condition, which the *sleep* spell causes, has two effects in combat:

- A sleeping defender is hit automatically in melee.
- "a single attack with a bladed weapon can kill", so a melee hit with a bladed weapon kills outright, with no damage roll.

The decisions behind the second effect:

- osrlib reads "bladed" as a weapon with the melee quality and without the blunt quality, because the SRD's blunt list exists to separate crushing weapons from edged ones. A gear item's combat facet, a monster's natural attack, and an unarmed strike don't count.
- The kill works only in melee, and the immunity gate still applies first, so a sword that the black pudding's fire-only gate stops kills nothing.

A dragon that's dozing "may be attacked for one round with a +2 bonus". That's a situational modifier the caller supplies, not a condition. Locked by `test_combat.py::TestDamagePipeline::test_sleeping_defender_dies_to_a_blade` and its neighbors.

### Falling damage floors per full 10 feet

The engine rolls 1d6 for each full 10 feet fallen, and nothing at all for a fall of less than 10 feet. Locked by `test_combat.py::TestHealingAndFalling`.

### Reload is enforced from caller context

Under the `weapon_reload` flag, a weapon with the reload quality can't fire in two rounds running. The attack validator refuses the attack when the context the caller supplies shows that the weapon fired last round.

- In a session, the session tracks which members fired last round and supplies that context.
- A game that calls the kernel directly supplies the context itself, and the kernel enforces the rule only as far as that context is accurate.

Locked by `test_combat.py::TestValidateAttack`.

### Two-handed weapons and shields conflict at equip time

`validate_equip` refuses a two-handed weapon and a shield together, with `items.equip.two_handed_with_shield`, whichever of the two the character equips second. osrlib refuses the conflict when the character equips, rather than ignoring the shield when an attack resolves. Locked by `test_items.py::TestTwoHandedShieldConflict`.

### Monster spawn HP is minimum 1, ½ HD is 1d4, fixed forms are exact

A spawned monster's hit points are its HD count in d8s, or 1d4 for ½ HD, plus its signed modifier, with a minimum of 1. The engine rolls nothing for a `1hp` monster or for the hydra's forms with 8 hit points per HD. Locked by `test_monster_data.py::TestSpawnHitPoints`.

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

Every kernel event class has an `event_type` field, typed as a `Literal` with one value, and the `KernelEvent` union tells events apart by it. The value is snake_case, it stays the same across schema versions, and new values are only ever added.

- The message `code` can name the outcome, from a closed set for each class, like `combat.attack.hit` and `combat.attack.missed`. So formatters choose their text by code, while consumers tell events apart by `event_type` and skip values they don't know.
- The default English formatter handles every code: an unknown code formats as the code string itself.

Locked by `test_events_kernel.py`.

### The combat kernel streams

The combat kernel uses three streams:

- `"combat"` for battle resolution: attacks, damage, saves, morale, and initiative.
- `"effects"` for randomness inside effects: durations, onsets, revival delays, and natural-healing rolls.
- `"monster_spawn"` for spawned hit points.

They're separate so that a change to the combat rules never shifts spawned hit points or effect draws. Locked by the Phase 2 milestone goldens.

### Spell ids are page-derived slugs, dual pages take `_c`/`_mu`, reversed forms are entry data

A spell's id is its primary name as a slug, without the reversed form in parentheses, like `cure_light_wounds`.

- Some spells are printed as separate `(C)` and `(MU)` pages. Each pair compiles as two entries, with `_c`/`_mu` suffixes, because the two differ mechanically: *hold person* is a 2nd-level cleric spell at 9 turns and 180', but a 3rd-level magic-user spell at 1 turn per level and 120'.
- A reversible spell's reverse is a `ReversedForm` on its entry, never a separate catalog entry.
- The *invisible stalker* page's `(MU)` marker distinguishes the monster page, not a pair of spells, so its id stays plain.

Locked by `test_spell_data.py::TestCensus`.

### `Spells.md` is the single magic-rules source, and spell pages are identified by the level line

The compiler reads the rules of magic from `Spells.md` alone. `Rules_of_Magic.md` has identical content, and the compiler never reads it.

The compiler identifies a spell page by its italic level line, like `*3rd Level [Magic-User Spell](...)*`. It never uses the `**Duration:**` or `**Range:**` labels, because those also appear on potion, hazard, and water-vessel pages. Locked by the compiler census (106 pages, 34 + 72 list entries).

### Memorization is a full-replacement preparation with the timing gates deferred

`memorize_spells` replaces a caster's whole list of memorized spells in one call, as the daily preparation.

- The SRD's limits on when a caster can prepare, once a day, after uninterrupted sleep, and taking an hour, are exploration procedure. The session (`osrlib.crawl`) enforces them. A game that calls `memorize_spells` directly can call it at any time.
- A caster may memorize the same spell more than once, per RAW.

Locked by `test_memorization.py::TestMemorizeSpells`.

### Arcane casters fix normal/reversed at memorization; divine casters choose at cast

An arcane caster chooses a spell's normal or reversed form when memorizing it, and a divine caster chooses when casting. Per `Spells.md`, the arcane form "must be selected when the spell is memorized", while divine casters reverse "when it is cast".

- So a divine caster's memorized copies never have a reversed flag, and setting one is refused with `magic.memorize.divine_reverses_at_cast`.
- A divine caster's reversed cast uses any memorized copy of the spell.

Locked by `test_memorization.py` and `test_casting.py::TestCastingPipeline`.

### Casting consumes the first matching memorized copy, and touch attacks consume it hit or miss

Casting uses up the first matching memorized copy, the one with the lowest index. A disruption removes a copy the same way ("as if it had been cast").

*Cause wounds* is cast by touch:

- In combat, the touch needs a melee attack roll ("In combat, a melee attack roll is required"). osrlib reads the inverse too: outside combat, the touch needs no roll.
- The copy is spent whether or not the touch lands, because RAW has no rule for holding the charge.

Locked by `test_casting.py::TestCuresAndRestoration`.

### The holy symbol is not a mechanical gate on casting or turning

A cleric doesn't need a holy symbol to turn undead or cast spells. `Cleric.md` states "A cleric must carry a holy symbol" as a class edict, beside faithfulness to a deity, not as a condition of any procedure. The holy symbol is in the gear data, so a game that wants the stricter reading can check the cleric's inventory itself. Locked by `test_turning.py::TestValidation::test_holy_symbol_is_not_a_gate`.

### Starting books are filled to capacity, book capacity is per level, and books never auto-shrink

A caster's spell book holds, at each spell level, as many spells as the caster can memorize at that level.

- An arcane caster starts play with a full book: one first-level spell at level 1.
- A cleric starts with no spells, because the cleric's level-1 row has no slots.
- `add_spell_to_book` limits the book, for each spell level, to the current number of slots at that level. That's how osrlib reads RAW's "exactly the number of spells that the character is capable of memorizing".
- The book is a physical object and never shrinks on its own. A drained character may have more spells in their book than the limit, and can't add more.
- Drain makes a caster forget the memorized copies that no longer fit, newest first at each level. RAW is silent, and newest first is deterministic without new state.

Locked by `test_memorization.py`.

### Category and immunity gates resolve as no-effect, never as rejections

A spell cast at a target that its category or an immunity excludes has no effect, and the cast isn't refused.

- The reason is the same as for holy water: a refusal is free and would reveal hidden state. Casting *charm person* at a disguised doppelgänger, or *sleep* at a wight, mustn't be a way to detect them at no cost.
- Casting at an ineligible target uses up the copy and reports `magic.cast.no_effect`.
- Immune and excluded creatures use none of an HD budget, because the exclusion happens during resolution, not as a validator refusal.

Locked by `test_casting.py::TestCastingPipeline::test_no_effect_still_consumes_the_copy` and `TestSleep`.

### Sleep's mode arithmetic

*Sleep* has two modes. Neither allows a saving throw, and neither affects undead.

Mode 1 affects "A single creature with 4+1 Hit Dice". osrlib reads that as a monster whose HD count is exactly 4 and whose fixed modifier is positive, like 4+1 or 4+3.

Mode 2 affects "A total of 2d8 Hit Dice of creatures of 4 HD or lower each". Mode 2 excludes mode 1's creatures. Mode 2:

- Costs each creature its HD count, with fixed bonuses dropped, as the spell page says: "a 3+2 HD monster would be treated as having 3 HD". A creature with less than 1 HD counts as 1.
- Spends the budget weakest first through the HD-budget targeting mode, with ties in stable order.

A creature of 5 HD or more is beyond both modes.

Both modes judge a drained monster by the Hit Dice it has left, plus its template's modifier. An ogre drained from 4+1 to 3+1 is no longer a mode 1 creature, and mode 2 takes it for 3 HD. A 5 HD grizzly drained to 4 HD becomes eligible for mode 2. Locked by `test_casting.py::TestSleep` and `test_drain_and_regen.py::TestSleepOnDrainedMonsters`.

### Magic missile is 1 + 2×⌊(level−1)/5⌋ missiles, resolved instantly

*Magic missile* fires 1 + 2 × ⌊(level − 1) / 5⌋ missiles: one up to 5th level, three at 6th to 10th, and five at 11th to 15th. osrlib derives the count from "three missiles at 6th–10th level, five missiles at 11th–15th level".

- Each missile takes one target that the caller supplies, and missiles at the same target stack.
- Each missile hits with no attack roll and no save.
- The spell resolves when it's cast, so the printed 1-turn duration does nothing in the engine.

Locked by `test_casting.py::TestMagicMissile`.

### Lightning bolt kills destroy equipment; disintegrate kills permanently and destroys carried gear

A creature that *lightning bolt* kills dies a destructive death, so what it carries is destroyed too. *Disintegrate* destroys "the material form ... instantly and permanently", which osrlib reads as permanent death that also destroys carried equipment, because the material form includes what the creature carries. Locked by `test_casting.py::TestFireBallAndLightningBolt` and `TestSaveOrDie`.

### Spell damage presents the `magic` key

Spell damage counts as magic at a weapon gate: it passes any gate whose keys include `magic`. So a wight's silver-or-magic gate lets *magic missile* through, and a gargoyle's magic-only gate lets *fire ball* through. Without the key, those gates would block spell damage. Locked by the Phase 3 spell-battle golden (three missiles damaging the wight).

### The hold/charm person gate admits any character and only `person`-category monsters

*Hold person* and *charm person* affect any character, and only monsters in the `person` category.

- Undead are never affected. Excluding them resolves as no effect.
- *Hold monster* and *charm monster* affect anything but undead.
- *Charm monster* has two modes: one creature of more than 3 HD, or 3d6 creatures of 3 HD or less.

Locked by `test_casting.py::TestHoldAndCharm`.

### Charm re-saves are monthly/weekly/daily by INT, with month = 30 days

A charmed creature saves again at an interval that its INT sets:

- INT 3-8: monthly.
- INT 9-12: weekly.
- INT 13-18: daily.

A month is 30 days and a week is 7. Monsters have no INT score, so they use the middle, weekly band, and an override can correct that for a monster. The engine rolls the re-save on the `effects` stream when the effect ticks, and a passed save ends the charm. Locked by `test_casting.py::TestHoldAndCharm`.

### The cumulative-effects rule: largest bonus plus largest penalty per statistic

When several spells change the same statistic, only the largest bonus and the largest penalty apply. `Spells.md` says "Multiple spells affecting the same game statistic do not combine.", and osrlib applies that whenever the engine reads the statistic. So a *bless* and a *blight* offset each other, and two *blesses* don't stack.

- Spell modifiers combine freely with modifiers from other sources, as RAW's exception for magic items allows.
- A spell's morale modifier goes through `check_morale`'s existing modifier argument, inside the morale rule's ±2 clamp and its exemptions for morale scores of 2 and 12. That keeps one rule for adjustments instead of a second channel.

Locked by `test_casting.py::TestBuffsAndWards` and `test_spell_properties.py`.

### Shield's AC values apply as better-of

*Shield* never makes a target's AC worse. The target's AC is the better of its own AC and the value the spell gives, "AC 2 [17] against missiles, AC 4 [15] against other attacks", so a target in plate armour keeps its plate AC. Locked by `test_casting.py::TestBuffsAndWards`.

### Striking applies to weapon attacks of any kind, never unarmed, and attaches to the wielder

*Striking* works on any weapon attack, missile weapons included, and never on an unarmed strike, because RAW names no weapon type.

- The enchantment attaches to the *wielder*, because item instances have no ids. If the wielder hands the weapon away while the spell lasts, the enchantment stays with the wielder.
- While the enchantment lasts, the wielder counts as magical at weapon gates.

Locked by `test_casting.py::TestBuffsAndWards`.

### Protection from evil: the melee ban is data, and monster alignment resolves at spawn

*Protection from evil* has two parts:

- Enchanted, constructed, and summoned creatures can't start a melee with the ward's bearer. That ban is structured params that the session reads in battle, because the kernel can't see who starts a melee.
- The ward's ±1 applies against creatures of another alignment.

The ±1 needs each monster's alignment to be settled, even when its template, `MonsterTemplate.alignment`, allows more than one. So `MonsterInstance.alignment` is resolved when the monster spawns: the caller's choice, else the template's `usual`, else its only option. An unresolved alignment counts as different, so the ±1 applies against it too. Locked by `test_casting.py::TestBuffsAndWards`.

### Small nonmagical missiles are character weapon missiles and thrown splash items

The heading names what *protection from normal missiles* stops: a character's missile weapons and thrown splash items, and a monster's attack only when the caller says it's a small missile. osrlib draws that boundary from the spell page's examples ("no protection is granted against hurled boulders or enchanted missiles"):

- A missile weapon use or a thrown splash item sets the `DamageSource.missile` flag.
- The engine never sets the flag on a monster's attack by itself. The caller sets `AttackContext.monster_missile` when the attack is a small missile in the story, like a hobgoblin's arrow.

Locked by `test_casting.py::TestBuffsAndWards::test_protection_from_normal_missiles_boundary`.

### All spell-attached effects are dispellable, monster effects are not, and survival is 5% per level of deficit

*Dispel magic* can end any effect that a spell attached, but no effect that a monster caused, and magic items are exempt.

- RAW says *dispel magic* "ends spell effects" without qualification. `permanent=True` means the effect has no duration that expires, not that it can't be dispelled, so *continual light* and *flesh to stone* can be dispelled.
- `cast_spell` records the caster's level on each effect it attaches. When that level is higher than the dispelling caster's, the effect survives on a d100 roll at or under 5% for each level of difference. The engine rolls for each effect.
- Effects that monsters cause, like poison, rot, and regeneration, can't be dispelled, by design.

Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### Raise dead: 0 days at level 7, a 14-elapsed-day weakness, and permanent undead destruction

*Raise dead* works within 4 days per caster level above 7th, which is 0 days at level 7, as RAW says. Only characters can be raised: all four races in the Classic rules are human or demihuman, and monsters aren't.

The revived character:

- Has 1 hp, and a weakness effect that lasts 14 days of elapsed time. That simplifies "two full weeks of bed rest", because tracking rest belongs to the session. A game that wants strict bed rest can extend or release the effect through the ledger.
- Can't attack, cast, or turn undead while the weakness lasts, because RAW bans "other class abilities" too.
- Moves at half rate, through a marker param that the session reads.
- Can't be healed by *any* source, because RAW says the subject "has 1 hit point" until the recovery completes, and no magical healing shortens it. The hit point returns to normal recovery when the weakness ends.

Used against undead, the spell destroys a creature that fails its save, permanently, like turning's `D`. Locked by `test_casting.py::TestCuresAndRestoration::test_raise_dead_level_seven_allows_zero_days`.

### Neutralize poison's revival window is caller-attested, and only characters revive

*Neutralize poison* revives a character who died of poison within ten rounds, when the caller states that's how and when the character died. The page says "A **character** who has died from poisoning can be revived, if *neutralize poison* is cast within ten rounds". The kernel has no record of the cause of death, so passing `CastContext.rounds_since_death` *is* the caller's statement that the target died of poison that many rounds ago. Leave it out for any other death.

- Only a `Character` can be revived, because the page's usage is titled "Characters".
- Revival brings the character back at 1 hp, which is osrlib's choice, since RAW names no hit point total.

In a session, the session supplies the value from its death record (see [The session's context](#the-sessions-context)). Locked by `test_casting.py::TestCuresAndRestoration`.

### Cure light wounds removes paralysis from any source

*Cure light wounds* in its `cure_paralysis` mode ends paralysis whatever caused it, as the spell page says: "Curing paralysis: Paralysing effects are negated." The mode releases each effect in the ledger that paralyses the target. Then it removes any `paralysed` condition the target still has, like one a game granted with `grant_condition` with no effect behind it. It removes no other condition.

The other cures, like *cure disease* and *neutralize poison*, lift a condition only by releasing the ledger effect that granted it ([#166](https://github.com/mmacy/osrlib-python/issues/166)). Locked by `test_rules_calls.py::TestCureLightWoundsLiftsParalysis`.

### Silence automates both forms: moving on a failed save, stationary on a passed one

osrlib runs *silence 15' radius* for both outcomes of the target's save:

- On a failed save, the target gains `silenced`, which the casting validator checks, and the silence moves with the creature.
- On a passed save, RAW leaves a *stationary* area that the creature can step out of. Effects bound to a location anchor to cells, so the engine attaches a stationary silence effect to the target's cell, which osrlib takes to be the party's current cell, the encounter's abstract location. Creatures in that cell can't cast while they're there, in battle too.

Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`, `test_exploration.py::TestStationarySilence`, and `test_battle.py::TestSilencedCellInBattle`.

### Web escape tiers depend on STR, with the augmented and giant tiers caller-asserted

How long a creature takes to escape a *web* depends on its STR:

- Normal strength escapes in 2d4 turns, rolled on the effects stream when the effect attaches.
- STR raised by magic above 18 escapes in 4 rounds, and giant strength in 2 rounds. Until effects that grant those exist, the caller states them through `CastContext.strength_tiers`.

The blocking cube is a location effect (see [Light, darkness, and location-bound effects](#light-darkness-and-location-bound-effects)). The web's flammability stays manual, for the referee. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### Feeblemind targets only `arcane_magic` casters

*Feeblemind* affects only a target with the `arcane_magic` class tag. The spell page's target is "an arcane spell caster (e.g. a magic-user or elf)", and any other target resolves as no effect. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

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

A monster whose cell is `D` is destroyed permanently. Every other affected monster gains the `turned` condition, which has no set duration and can't be dispelled, and the encounter releases it when it ends. A turned monster attacks no one and runs from the battle if it can (see [The default action policy and morale](#the-default-action-policy-and-morale)). Only monsters in the `undead` category are candidates, and any other monster is unaffected.

In a session, a turning's candidates are the living monsters of the encounter's groups that haven't fled. The candidate order is the encounter's group order, then each group's monster order, both in battle and before it. A monster killed earlier in the round or the encounter doesn't make its type a candidate, and nor does one whose group left the battle. The reason is the SRD's "At least one undead monster will always be affected on a successful turning". That can't hold for a type with no monster left to affect. Locked by `test_turning.py`, `test_spell_properties.py`, and `test_turning_and_staff.py`.

### The `magic` RNG stream, and the attach-time/tick-time split

Every draw inside spell resolution comes from the `"magic"` stream: targeting dice, damage dice, touch-attack rolls, forced saves when the spell is cast, dispel survival rolls, and both turning rolls. So a change to the combat rules never shifts spell results, and a change to spells never shifts combat.

Draws inside an effect stay on the `"effects"` stream, as [The combat kernel streams](#the-combat-kernel-streams) describes. Those are durations rolled when the effect attaches, *web* escape dice, and saves made when the effect ticks, like the charm re-save. That's why `cast_spell` takes both streams.

Spell resolution has no `Ruleset` flags: every choice is rules-as-written or an interpretation, never a variant the referee picks. Locked by the Phase 3 milestone goldens.

### Encounter tables: hand-mapped ids, table counts override, variant pools

The compiler turns the SRD's encounter tables into data:

- Each table entry's monster name is mapped by hand to a compiled monster id, and the compiler checks the mapping, because the printed links don't resolve.
- The count dice printed in the table override the monster description's number appearing, as the SRD's note says.
- An entry that packs variants, like `Veteran`, `Vampire`, or `Hellhound`, compiles with every variant's id. Each spawned monster picks one with equal chance, on the wandering stream.
- The two hydra entries have `variant_dice`, so the engine rolls the printed HD dice once, on the wandering stream, and picks the template from the result.
- Rows for NPC adventurers compile as structured `npc_party` entries, and the session fields a generated party for them (see [NPC parties in play](#npc-parties-in-play-encounter-sides-like-any-other)).
- The "Basic Adventures" typo and the singular "Expert Adventurer" labels are normalized in `overrides/encounter_tables.json`, with a note of where each correction comes from.

Locked by `test_encounter_data.py` and `test_exploration.py::TestWandering`.

### Encounter-table and reaction models live in core/tables.py

Encounter tables and reaction rolls follow these rules:

- The reaction table clamps a total into its outer bands, "2 or less" and "12 or more".
- Reaction rolls are visible only to the referee. Players learn reactions from how the monsters behave, as they learn morale results.
- A dungeon level's encounter table is the level number clamped into the printed bands: 1, 2, 3, 4-5, 6-7, and 8+.

The models for both are in `core/tables.py` because of layering. `osrlib/data/` loaders import the modules that define their models, and `core` modules import the loaders. Defining these models under `crawl/` would make `load_encounter_tables()` import from core, through data, into crawl. Locked by `test_encounter_data.py` and `test_kernel_checks.py::TestRollReaction`.

### The crawl streams

The session uses four streams of its own:

- `"wandering"`: the check die, the d20, count dice, variant picks, and NPC re-rolls.
- `"encounter"`: surprise, distance, reaction, and distraction.
- `"exploration"`: forcing doors, listening, searching, trap springs and trap resolution, tinder, and thief skills.
- `"monster_action"`: every policy draw, so a different policy never shifts attack or damage draws.

Trap resolution draws, like saves, damage, and the number of darts in a volley, are on the exploration stream, because they're part of the trap procedure. Durations of conditions rolled when an effect attaches stay on the effects stream, as [The combat kernel streams](#the-combat-kernel-streams) describes. Locked by the Phase 4 delve golden and `test_battle.py::TestDefaultPolicy`.

### Detection checks: precedence, zero-chance, and pick-pockets arithmetic

A detection check uses the first chance that applies, in this order: the thief's skill row, then a class tag, then the 1-in-6 that everyone has.

- Spotting construction tricks is the exception. Only dwarves can, and the baseline is **zero**, because the SRD gives that perception to dwarves alone.
- A zero chance consumes no draw.
- Pick pockets is capped at 99 ("always at least a 1% chance of failure"). The penalty for a victim above 5th level is a modifier the caller supplies. A roll of more than twice the effective chance is noticed.

Locked by `test_kernel_checks.py`.

### The time-cost census and the thirds odometer

Every exploration command has a fixed cost in game time. A round is 10 seconds and a turn is 10 minutes.

| Command | Time cost |
| --- | --- |
| `TurnParty`, `ReorderParty`, `OpenDoor`, `CloseDoor`, `WedgeDoor` | None |
| `ForceDoor` | None. Forcing takes a moment, and its cost is the noise. |
| `ListenAtDoor` | None. The limit of once per door is what holds it back. |
| Equipping and unequipping, `DropItems`, `ExtinguishSource` | None |
| `PurchaseEquipment` | None, because time in town is abstract. |
| Every referee command | None |
| `CastSpell` and `TurnUndead` outside battle | One round |
| `LightSource` | One round, and one round for each tinder attempt, RAW |
| `Search`, `InspectTreasure`, `Rest(turn)` | One turn |
| `RemoveTreasureTrap` | One turn, osrlib's choice of a symmetric cost, where RAW is silent |
| `PickLock` | One turn, osrlib's choice, where RAW is silent and the skill is delicate work |
| `TakeTreasure` | One turn, RAW's turn for packing bags |
| `MoveParty` | The odometer, for each cell |
| A passage | The odometer, the cost of a step onto its landing cell |
| `UseStairs` | The odometer, the cost of one unexplored cell (osrlib's choice) |
| `Rest(night)` | 48 turns |
| `Rest(day)` | 144 turns |
| `PrepareSpells` | 6 turns |
| Travel | The turns that the town's `travel_turns` lists for the dungeon |

The odometer counts movement in thirds of a foot. An unexplored cell costs 30 units and an explored cell 10, which is exactly the SRD's rule that a party moves three times as fast through familiar areas. An action that costs a turn moves the clock on to the next turn boundary, which takes in any partial move. Locked by `test_exploration.py::TestTimeCostCensus` and `TestOdometer`.

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

Locked by `test_exploration.py::TestRestAndFatigue` and `TestProvisions`, and by `test_rest_credit.py`.

### Doors: forcing, noise, swing-shut, locks

Doors follow these rules:

- Any attempt to force a door makes noise for the next wandering-monster check, because the banging is the same either way. Only a *failed* attempt denies the party surprise against the room beyond, exactly as RAW is written.
- A door the party opened swings shut once the party is in neither cell beside it, unless it's wedged. RAW's "likely to swing shut" becomes always, so a party that wants a door to stay open behind it has to wedge it.
- A stuck door that was forced is stuck again once it shuts.
- A failed attempt to pick a lock locks that thief out of that lock until the thief's next level.
- Each character may listen at a door once. Listening costs no time, and the engine rolls it whatever is behind the door.
- Undead make no noise, so silence at a door doesn't say whether the room is empty. That's the rule that a result never reveals hidden state, applied to exploration.

Locked by `test_exploration.py::TestDoors` and `TestListening`.

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

Light sources:

- Lighting a torch or a flask of oil uses up one, and putting it out forfeits what's left. osrlib doesn't track part-burned items, so relighting starts a fresh one.
- When a light effect expires, the session adds `exploration.light.expired` for the players, because the ledger's expiry event is visible only to the referee.
- When the party has no open flame, which is an active `brightness="flame"` effect, lighting needs the tinder box, which works on 2-in-6 each round, RAW.

Darkness:

- A darkness effect on any member puts out the whole party's light while it lasts, because the printed radii cover a marching party. `blocks_infravision` turns off infravision too.
- Following RAW, `Search`, `InspectTreasure`, `PickLock`, and reading need light. Searching and listening also work by the actor's infravision.
- The party can move in darkness with `MoveParty`. When the party is unlit and not every living member has infravision, it's surprised on 1-3 instead of 1-2. This is the blind-party adaptation.
- A cell is visible when it's explored or the party stands in it.

Location effects anchor to a cell reference, `cell:{dungeon}:{level}:{x},{y}`, and run their enter hooks in the order they were attached:

- The burning-oil pool. When the party drops oil with `DropItems` and lights it with `LightSource`, the engine puts the pool from [Splash damage is two applications](#splash-damage-is-two-applications) on the cell. Every living member who enters while it burns takes its 1d8.
- Stationary *silence* (see [Silence automates both forms](#silence-automates-both-forms-moving-on-a-failed-save-stationary-on-a-passed-one)).
- *Web*'s blocking cube. Entering it attaches the entangled escape countdown. A web cast on a location lasts the spell's duration on the cell, and the escape params are on the effect.

While a living member is entangled, the party can't move. The party doesn't leave a member behind on its own, and leaving someone is a decision for a referee command.

The web's flammability, *cloudkill*, and the walls stay manual, for the referee. The visible-cell rule is a simplification of the visible flag in the spec. Locked by `test_exploration.py::TestLight` and `TestLocationEffects`.

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
- Surprise gives the other side one free round, however the battle starts. Surprised monsters give the party a free battle round, in which the monsters hold while the party acts. A surprised party loses an encounter round, in which hostile or attacking monsters can open battle with a surprise round that the session runs.

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

When the party evades, the engine compares the two sides' speeds, and a pursuit runs round by round until it ends.

- The party's speed is its slowest member's running rate, in full feet per round. The pursuers' speed is their slowest base ground movement: the mode with no descriptor, or else the first one printed, so a flying mode doesn't count. Taking the slowest pursuer mirrors taking the slowest member, and the engine doesn't model a pack that strings out.
- The party can drop bait. `Evade(drop="treasure")` scatters every living member's coins, and `"food"` drops one ration per member (osrlib's reading). During the pursuit, `DropItems` drops new bait each round.
- Bait distracts the pursuers on 3-in-6 when it matches what they want. Monsters whose treasure type has letters want treasure, because osrlib uses the treasure type as a stand-in for intelligence, and an override can correct that for an encounter. Every other monster wants food.
- One distraction roll stops the whole side, because the pursuers act as a pack.
- The pursuit ends when bait distracts the pursuers, when they reach melee (a gap of 5' or less, which starts a battle at 5'), or after 30 rounds, when both sides are spent and the monsters give up. In a pursuit the party is no faster than the pursuers, so the gap never grows, and osrlib needs no distance at which the monsters lose sight of the party. So the 30-round limit is what ends every other pursuit.
- Running exhaustion gives −2 to attacks, damage, and AC until the party rests for three turns. The engine applies the AC penalty as +2 to attackers' rolls, through `attack_penalty_of_attackers`.

Locked by `test_encounter.py::TestEvasionAndPursuit`.

### The battle round

A battle runs in rounds. Each round, the party sends one command with one declaration for each living, able member, and each monster group acts through its policy. On this page, "the session" runs the battle, and the battle module is part of it.

#### Declarations and ranks

- The session rejects the whole round if any declaration is invalid, because accepting part of a round would complicate replay.
- Under `formation_width_limit`, the formation width is the frontage the space around the party offers: the widest square of unbroken floor that includes the party's cell, with five feet of frontage for each member. In a room 30' by 50', the widest square is 30' across, so six members fight side by side (see [`formation_width_limit`](#formation_width_limit-default-on) for more examples). The SRD says "2–3 characters in a 10' passage". osrlib takes the lower number, so each member needs 5', and applies that to whatever space the party stands in. Two members fit side by side in a 10'-wide passage, however long it runs, and in a room the rank is as wide as the room's shorter side.
- At the start of each round, the session works out the ranks again from the living marching order. Melee reaches only the front rank, in both directions. There's no penalty for firing into melee, because the SRD has none.
- A party melee attack hits the first living, visible monster of the group's reachable rank, with no draw. Monsters pick their targets with equal chance, on the monster-action stream (see [Monsters pass over statues](#monsters-pass-over-statues-and-ghouls-and-thouls-turn-from-the-members-they-paralyse)).
- A staff of striking is "Used as a magical melee weapon", so under `formation_width_limit` only a front-rank member may use one. The session judges that against the ranks the round's swaps produce, as it does a melee attack, and refuses a back-rank member's use with `battle.declaration.not_in_front_rank`.
- Under individual initiative, the session still resolves each side as a block, all of one side's actions and then all of the other's. It orders the blocks by each side's best individual total, because the SRD's phase sequence is per side.

#### Formation moves

The party moves as a formation when every declarer agrees. That's the convention of The Bard's Tale, where the party moves as one:

- When every declarer retreats, the formation moves off at the full encounter rate (Combat.md's "full encounter movement rate"). The running pursuit begins when the battle turns into a pursuit at the end of the round.
- When every declarer makes a fighting withdrawal, the formation backs off at half the encounter rate.
- The first `close` declaration advances the formation on the group it names, stopping at 5'. `close` needs no agreement, so a lone `close` advances the formation.
- A round whose only declarer declares a defensive move is a formation move, because everyone agreed.

The move set is the SRD's two defensive moves and `close`. Schema versions before 4 had a fourth move, `withdraw`. When osrlib loads an older command log, its lossless migration, which converts the log to the current schema without losing information, removes `withdraw` from the declaration and leaves the member holding, which is how that round played out.

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
- The session detects disruption by the RAW trigger: a declared caster is successfully attacked, or fails a save, before acting.
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

Battle reads these spells and conditions on each combatant:

- *Haste* makes the bearer's attack declaration resolve twice, and doubles the bearer's moves on the range track. The party's combined move doubles when every living member has the speed multiplier, which is osrlib's reading.
- An invisible combatant can't be targeted. Refusing that attack reveals nothing, since the player already knows the target is invisible. The session ends the invisibility when the bearer attacks or turns undead.
- *Mirror image* loses one image for each attack on the bearer, with no attack roll.
- Confusion replaces the bearer's declaration with the result of its 2d6 behaviour roll, on the combat stream, because it's a roll the rules require, not a choice a policy makes. Its re-saves draw on the magic stream, per its params. The session makes both rolls itself each round, not as ticks of the effect, so effects still draw at the times that [The `magic` RNG stream](#the-magic-rng-stream-and-the-attach-timetick-time-split) describes.
- `entangled` stops a member's declared moves and a monster's moves from its policy, while an entangled member in the front rank still fights.
- A `weakened` creature can't attack or cast. The kernel's validators refuse those actions, and the session reports the refusals.
- *Protection from evil*'s melee ban: a monster whose template has a warded category can't start a melee with a warded target of a different alignment. So the default policy's target pick skips that target, while missiles and breath still land. The ban breaks for a target who has engaged the barred creature in melee, and the ±1 modifiers stay, as RAW's clause says.

Locked by `test_battle.py::TestEffectConsumption`.

### The area footprint

An area effect aimed at a monster group affects as many of the group as its shape covers. osrlib works that out from the shape's size, because the combat space is an abstract range track, not a map. The SRD leaves this to the referee, so it's a documented adaptation.

- An area holds `ceil(span / 10) × width` creatures. The span is how far the shape reaches into the group, counted in 10' ranks.
- A radius shape spans its diameter, because a circle reaches twice its radius across. Lines, cubes, and clouds span their length. A cone spans its length minus the group's distance, because a cone starts at the caster.
- The width is the formation width, the same measure of the space that sets the party's rank width, because the monsters fight in the same space. With `formation_width_limit` off, the width has no limit.
- For example, a *fire ball* has a 20' radius, so it spans 40'. In a 10' corridor, where the width is 2, it catches ceil(40 / 10) × 2 = 8 monsters. A cone 60' long, aimed at a group 30' away, spans 30', so in the same corridor it catches 6.
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

The default policy, `ScriptedPolicy`, follows a monster's scripted pattern, a fixed way of fighting that its data records, when it has one. Otherwise the group closes, which means it moves toward the party on the range track, and attacks. It draws only from the monster-action stream.

Scripted patterns and movement:

- A breath weapon with `uses_per_day` opens with breath, then breath or melee with equal chance while daily uses remain, as the dragons do in RAW.
- For a breath weapon that works on a chance in six each round (`per_round_chance_in_six`), like the hellhound's, the policy rolls the chance each round.
- Otherwise, a group farther away than 5' closes, and at 5' it melees.
- The monster data has no ranges for monster missile attacks, so they work like melee: the monster closes, then attacks. So the spitting cobra spits from 5', which is osrlib's reading.
- Groups never cast, because casting tags are `manual` data.

Attack routines (see [Compiled monster data conventions](#compiled-monster-data-conventions) for the `use` labels):

- A monster that melees picks its target, then one of its attack routines. It picks with equal chance among the routines whose `use` is `primary` or `alternative`: the thoul claws or swings a weapon, the mule kicks or bites, and the spitting cobra spits or bites. A monster with one such routine draws nothing for the pick.
- The policy never picks a `situational` routine. The SRD ties such a routine to a situation osrlib doesn't model: a range, a charge or a trample chance, water, a ship, a form, or a loadout. So the default policy never rolls the 3-in-4 trample chance of the elephant and its kin, or the cave locust's 50% jump.
- A policy that your game supplies can name a situational routine in `MonsterAction.routine` when the game decides the situation applies. Battle then rolls that routine's attacks as melee attacks. A thrown missile in a later routine, like a giant's boulder, is a situational routine too, so it's rolled this way.
- A `special` routine is a breath, a gaze, or a spell. Battle rolls no attack for one, even when a policy names it. It also never rolls an attack entry named `gaze` or `breath` as an attack, because a breath and a gaze resolve another way.
- A confused monster that attacks uses its first routine, which is osrlib's reading.

Morale:

- At the start of the monsters' turn each round, battle checks each group's morale when a trigger applies, and records the checks for the rest of the battle. The triggers are the side's first death and half the side incapacitated.
- A monster's alternate morale score applies when the round meets its condition, like fear of fire when the round's damage included fire. A spell's morale modifier applies under [the cumulative-effects rule](#the-cumulative-effects-rule-largest-bonus-plus-largest-penalty-per-statistic).
- A group with a morale score of 2 routs when battle starts, or as it joins a battle already running.
- A group that fails a check is exposed that round as a retreating combatant is (see [A member who withdraws alone](#a-member-who-withdraws-alone)), then flees at full speed. The group leaves the battle as routed once it's past 120', the sight distance that pursuit uses, which applies the same way to both sides.
- Turned undead attack no one, as the cleric page says: "Flee from the cleric's presence if possible, and will not harm the cleric". A group whose living members are all turned or afraid retreats as a whole. When a turning affects only some of a group, the turned members break away as a new group, which runs, and the rest fight on. That's osrlib's reading.

A fled group stays in the player view's `groups`, in order, with `fled` set. The session refuses every declaration aimed at it with `battle.declaration.unknown_group`: an attack, a `close` move, a thrown item, a device, or a spell. The party can still declare against a group that's running but still inside 120', because the party can chase the group or shoot at it. A cast aimed at a group that flees earlier in the same round fizzles at the magic phase's re-check (see [The battle round](#the-battle-round)).

Chasing routed monsters is left to the game. osrlib would treat it as the evasion procedure with the party as the pursuer, and that isn't built yet.

No group in the compiled data has a morale score of 2, so tests force both the battle-start branch and the join branch. Locked by `test_battle.py::TestDefaultPolicy` and `TestMoraleAndEnds`, for the routine pick by `test_attack_routines.py`, for a group that joins by `test_spawn_into_encounter.py::TestAGroupThatWillNotFight`, and for a fled group by `test_battle_player_view.py::TestAFledGroup`.

### Monsters pass over statues, and ghouls and thouls turn from the members they paralyse

A monster that melees picks a target with equal chance among the members of the party's front rank it can reach. Two rules narrow that choice, and the policy applies them in this order:

1. A member turned to stone is a statue and out of the fight. While a target that isn't stone is in reach, no monster picks a statue. The SRD doesn't say this, so it's osrlib's judgement.
2. A ghoul or a thoul also passes over a paralysed member, while a target that is neither paralysed nor stone is in reach. When no one else is in reach, it attacks the paralysed member. The ghoul's entry says "After paralysing a target, ghouls will attack others", and the thoul's entry says the same of thouls.

So a ghoul whose reach holds only a paralysed member and a statue attacks the paralysed member, because rule 1 removes the statue first.

The second rule comes from data, not from the monster's id. The ghoul's and the thoul's `paralysis` abilities have the param `attacks_others`, and the policy passes over paralysed members for any monster whose `paralysis` ability has it. The carcass crawler's and the gelatinous cube's entries have no such clause, so they keep attacking the members they paralyse.

NPC adventurers use the built-in [`NpcPartyPolicy`][osrlib.crawl.battle.NpcPartyPolicy]. They pass over statues the same way. They still attack a paralysed member, because the second rule comes from the ghoul's and the thoul's monster entries, and an NPC adventurer isn't a ghoul. This is osrlib's reading.

When every member in reach is stone, the monster attacks a statue. That happens when the front rank is all stone and members behind it still stand:

- A statue keeps its place in the ranks, because it still counts among the party's living members. So the front rank can be all statues.
- The battle goes on, because the session ends the game only when nobody is left standing (see [A party turned wholly to stone has fallen](#a-party-turned-wholly-to-stone-has-fallen)).
- The monster can't reach past the statues to the ranks behind, because melee reaches only the front rank, in both directions.
- Holding would leave the monsters idle in front of the statues and stall the battle, so the monster attacks a statue and the fight goes on. A dead member leaves the ranks at once, so a front rank is never made of the dead.

A monster picks one target a round and makes all its attacks on that target. A ghoul has two claws and a bite, so a ghoul whose first claw paralyses its target still spends the rest of that round's attacks on the same target. When none of the members in reach is paralysed or stone, or when all of them are stone, these rules leave the choice as it is, so the monster's draw doesn't change. Locked by `test_monster_tactics.py`.

### Battle applies six kinds of monster attack effect and the petrifying gaze, and leaves the rest to the referee

When a monster's attack hits in battle, the session applies each effect the attack lists, if both of these are true:

- The effect is `paralysis`, `poison`, `disease`, `energy_drain`, `petrification`, or `blindness`.
- The monster's ability for that effect isn't `manual`, which would leave it to the referee.

The effects apply once for each hit, so a target hit by two paralysing claws saves twice. A dead or petrified target takes none of these effects.

B/X has five saving throw categories: death, which covers poison, wands, paralysis, which covers petrification, breath, and spells. The saves work like this:

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

The session applies only the six kinds above and the petrifying gaze. It emits no event for an effect it doesn't apply, so the referee applies the attack effects and gazes below from the monster's description, when the attack hits:

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

- The session allocator, the counter that hands out ids, assigns character ids as `character-NNNN`, in party order.
- Dead members stay in the party but count for nothing.
- The session records the clock round and the cause of each character's death. The cause is `poison` when the killing resolution was a poison save or the end of a poison's delay, and otherwise it's the kind of source.
- *Raise dead* always uses that record for its day count, and *neutralize poison* uses it for its round window only after a death by poison. A game that calls the kernel's spell functions, like `cast_spell`, without a session has to supply those values itself, like `CastContext.rounds_since_death` (see [Neutralize poison's revival window](#neutralize-poisons-revival-window-is-caller-attested-and-only-characters-revive)).

Visibility:

- Session flags are visible only to the referee, because how the game's content uses them is secret.
- Neither view includes the seed, which is only in the save.
- The player view contains only an enumerated list of fields, and a property test checks that nothing else leaks into it. It shows an undiscovered secret door as a wall.
- `SetDoorState` emits door events that only the referee sees.

Referee commands:

- `PlaceParty` triggers no hooks, which are the location effects that run when the party enters a cell, and no traps or keyed encounters, because it's a referee tool.
- A referee's `AdvanceTime` runs full bookkeeping but no wandering-monster checks, since the referee controls encounters.
- `SpawnMonsters` works only in a dungeon, because the combat space needs a party cell. It rolls its count dice on the encounter stream and opens a standard encounter, which rolls surprise for both sides, distance, and reaction, like a wandering encounter.
- While an encounter or battle is already running, `SpawnMonsters` adds one group to that encounter instead. That's how a referee brings in the allies an SRD monster summons. The group rolls no surprise or reaction, and it shares the encounter's stance and surprise. The spawn costs no time, so the group first acts in the next round.
- During a pursuit, the session refuses the spawn with `encounter.spawn.mid_pursuit`, because the chase runs at the pace of its slowest group.
- `SpawnNpcParty` sets the size of the band in one of three ways. `count_fixed` gives an exact size and draws nothing. `count_dice` rolls the size on the encounter stream. With neither set, the session rolls the B/X number for the band's kind. Setting both `count_fixed` and `count_dice` fails validation, as it does on `SpawnMonsters` ([#167](https://github.com/mmacy/osrlib-python/issues/167)).
- The session rejects `SpawnNpcParty` and `PlaceParty` while an encounter is running.

Locked by `test_session.py`, `test_crawl_properties.py`, `test_spawn_into_encounter.py`, and `test_rules_calls.py::TestSpawnNpcPartyTakesAFixedSize`.

### Persistence and replay

A save contains everything needed to resume play, including the adventure itself.

- A save always contains the log of accepted commands. The event log is optional.
- The event log keeps event types it doesn't know, without loss, because the log is a record and is never re-derived.
- Replaying under a different engine version raises `ReplayVersionError`. Loading a save from another engine version is still allowed.

For maintainers: a synthetic migration at schema version 1, inside the tests, exercises the migration framework. A standing test checks that `load(save)` equals `replay(seed, commands)` at every checkpoint of a recorded test game. Locked by `test_persistence.py` and `test_phase4_goldens.py`.

### The treasure compiler: entry grammar, magic clauses, and TreasureRef semantics

The compiler parses each treasure-type entry with a fixed grammar. An entry has an optional `NN%:` chance that the treasure is present at all, quantity dice with multipliers and coin suffixes attached, counts of gems and jewellery, and structured magic clauses. A magic clause has `any`, a named category, or a pool of categories. It also has a count or count dice, any exclusions, and extras like `+ 1 potion`.

- A pool picks one of its categories with equal chance before the sub-table roll.
- An exclusion re-rolls exactly the named master-table category, and the re-rolls consume draws, as the wandering-monster re-roll does.
- `TreasureRef` letters resolve by SRD section, and letters in parentheses are lair treasure.
- `extra_gp` joins the hoard, `multiplier` repeats the whole roll, and `special` and `see_below` generate nothing. The reduction for a small lair stays prose, for the referee.
- Each gem's value comes from the d20 value table, and each piece of jewellery is worth 3d6 × 100 gp. Both values are fixed when the treasure is generated.
- Appraisal is instant and exact, because B/X prices treasure for the XP economy. A haggling or appraisal minigame is left to the game.

Locked by `test_treasure_data.py` and `test_treasure_kernel.py`.

### Treasure generation order, tiers, and the treasure stream

The engine generates treasure in the order the entry is printed, and finishes each item before it starts the next:

- Each gem's value, and each piece of jewellery's value.
- Each magic item in full: its sub-table, the d8 armour type for generic armour, charges at creation, ammunition quantities, scroll contents, and sword sentience last. For sentience, the 1-in-20 special purpose comes first, then the printed procedure.

All of it is on the `"treasure"` stream, so the stream alone reproduces the draws.

- The tier picks the printed B/X sub-table column from the party's highest living level, with 4 or more counting as expert.
- A caller that generates treasure directly, outside a session, chooses the tier, and passes a stream and an allocator.

Locked by `test_treasure_kernel.py`, `test_magic_items.py`, and the phase 5 hoard golden.

### Hoards generate lazily, appear as engine-created caches, and are untrapped

The engine rolls treasure when it's first needed, not when the adventure loads, and never changes the adventure you wrote to do it.

- A keyed encounter's lair hoard, the treasure kept in the monsters' lair, is rolled once, when the group first spawns.
- The engine puts the hoard in a cache, a container of treasure the party can take, on the first cell in the area's list of cells. It records the cache in the dungeon state, the overlay where the session records what play has changed, so the authored specs stay frozen. Piles and features gain fields for valuables and magic items, which default to empty.
- When the party evades a keyed encounter and comes back, the monsters spawn again, but the hoard doesn't. The session records the area in `DungeonState.generated_lair_hoards` when it rolls the hoard, and doesn't roll it again. The party finds the cache from the first visit, or nothing if it already took that treasure.
- Carried treasure is rolled when the monsters spawn. `SpawnMonsters` rolls no carried treasure, because the referee grants carried loot explicitly.
- An area's treasure specs are rolled the first time the party enters the area. Unguarded treasure uses the bands on the SRD's *Designing a Dungeon* page.
- Generated hoards are untrapped, because a trap on treasure is authored content, not something generation produces.

For one keyed group, the order of draws on the treasure stream is the carried treasure for each monster line of the keyed encounter, in the order the author listed them, then the lair hoard. Locked by `test_exploration.py::TestTreasure`, `test_loot_and_traps.py::TestLairHoardOnce`, and the milestone golden.

### Loot drops: slain sides drop, routed sides keep

At the end of a battle, slain groups drop their carried treasure and group bundles into a pile on the party's cell. A routed group, one that fled, keeps everything and leaves with it. Defeated NPC adventurers drop their equipment and their share of the group's U + V treasure bundle the same way: one bundle of treasure types U and V for the whole group. Locked by `test_encounter.py` and the milestone golden.

### The end-of-adventure award is the departure-snapshot valuation delta

When the party returns to town, it gets XP for the treasure it brought back: the change in its valuation since it entered the dungeon.

- `EnterDungeon` records a snapshot of the party's valuation: coins plus carried valuables at their exact value, with magic and mundane gear at zero.
- `TravelToTown` awards the difference, rounded down once from copper and never below zero, plus the XP in the record of defeated monsters.
- The total divides evenly among the living members, rounded down, and the remainder is dropped. Each share goes through `apply_xp`, so class modifiers and the limit of one level at a time apply to each member.
- Dead members' carried treasure counts toward the difference, but they get no share. A party with no survivors gets no award.
- The award clears the record of defeated monsters and the snapshot.
- Selling in town after the award changes nothing.

For maintainers: there's no ledger of recovered treasure. Schema version 2 removed it, and the migration drops the field from version-1 saves. Locked by `test_award.py` and `test_persistence.py`.

### Treasure weight covers coins, valuables, and the five priced magic categories

`treasure_weight_coins` counts the weight of a character's treasure, in coins: the purse, valuables, and the magic items that the heading calls priced, which are the kinds the SRD's encumbrance table gives a weight.

- Those kinds are potion, scroll, rod, staff, and wand, and the table's rows are [`TreasureWeight`][osrlib.core.items.TreasureWeight].
- Rings and miscellaneous items weigh nothing unless their page gives a figure.
- The Bag of Holding's printed loaded weight of 600 coins counts while the bag contains anything.
- Enchanted weapons and armour count as equipment, alongside their mundane bases, not as treasure, so basic encumbrance's treasure tracking stays accurate. They weigh what the base weighs, except enchanted armour, which weighs half, as the SRD's armour page says. The base is the instance's `base_item_id`, or the template's when the instance has none.
- A spent stack, which is a magic item at quantity 0, weighs nothing, because the character no longer carries it.

A property test checks the weight against a direct formula. Locked by `test_items.py` and the closed-form property in `test_crawl_properties.py`.

### The magic item catalog: 164 templates, automated mechanics, and manual prose

The compiler builds every item on the printed sub-tables, with both the B and X probability columns. The sparse basic column compiles as printed.

- Items with automated mechanics get them from the hand-curated `_MAGIC_ITEM_MECHANICS`, as spells do from `_SPELL_MECHANICS`. Every other item compiles as prose tagged `manual`, for the referee.
- A "versus" clause compiles to structured references to categories and tags, never to matched prose. Lycanthropes and undead are category tags. Spell users and regenerating creatures are sets of templates worked out from their abilities.
- The girdle of giant strength is the one automated item whose mechanics depend on a `Ruleset` flag, `variable_weapon_damage`, and the tests name it as such.
- The ring of regeneration works only `while_alive`, and the displacer cloak penalizes only melee attackers.
- The energy-drain sword drains automatically on every hit, at the `level_minimum` XP policy. That's osrlib's default for a computer game, over RAW's "may command".
- Where the SRD's text is cut off, `overrides/magic_items.json` completes it, with a note of the source (`helm_of_telepathy`).

Locked by `test_srd_data.py` and the `test_magic_items.py` census.

### Magic items enter play unidentified, identify on first meaningful use, and curses stick

A magic item shows a generic name until it's identified: an enchanted weapon or armour is "a {base} with a faint aura", and a miscellaneous item is "a curious device". An item is identified by any of these:

- Its first meaningful use.
- The referee's `IdentifyItem`.
- A worn item's effect attaching.
- An attack on a character who wears enchanted defenses, because the bonus shows itself in battle.

A cursed item reveals itself at the same moment and sticks. The engine refuses `UnequipItem` for a cursed sword, weapon, armour, shield, or ring alike, with `items.curse.stuck`, until *remove curse*. A cursed ring reveals itself the moment it's equipped, because its onset is automatic.

Charges are rolled when the item is created, and only the referee ever sees them (RAW "undiscoverable"). The player view never shows a charge count, a potion's remaining duration, an item's internal state, sentience, or a hoard the party hasn't found. Locked by `test_magic_items.py`, `test_exploration.py`, and the leak property test.

### Item bonuses use a separate channel, outside the cumulative caps

Bonuses from equipped items, like enchanted weapons and armour, rings of protection, and the displacer cloak, come from what the character has equipped at the moment the engine reads them. They're never effects in the ledger, so unequipping takes effect at once, and nothing needs dispelling.

- Ledger effects that come from items, like potion durations, wards, and the onset of weakness, attach with `from_item=True`, and can't be dispelled.
- Those item effects sit outside the cumulative-effects cap, which applies only among spell effects. So `modifier_total` is the capped total of spell modifiers plus the sum of item modifiers.

For maintainers: `strength_set`, `ac_bonus`, and the damage-multiplier kinds are in `MODIFIER_KINDS`. Locked by `test_effects.py`, `test_combat.py`, and `test_magic_items.py`.

### Potions: hidden 1d6+6-turn durations, mixing, and the weekly inversion

- A potion lasts 1d6 + 6 turns, rolled on the effects stream and hidden from the player.
- Drinking a potion while another one runs cancels both, and stops potions from working for 3 turns. Instantaneous and permanent forms are exceptions.
- A character who uses a potion of invulnerability more than once in a week gets −2 instead of +2. The engine counts the week by the game clock's days, for each character.

Locked by `test_exploration.py::TestUseItem`.

### Scrolls: per-spell burn, minimum caster level, the thief fizzle, wards, and curses

Spell scrolls:

- A scroll loses each spell as it's cast from it. The page calls this burning.
- osrlib checks and resolves a scroll's spell as if the lowest class level able to cast it were casting, from the compiled progression. So the scroll's caster level decides how many targets a mode needs and how far it reaches. The engine reads the scroll through a stand-in for the character, `_ScrollReader`, so the character never changes.
- Reading a scroll doesn't require the arcane ability to read magical script, because the scroll itself is the medium (osrlib's reading).
- A 10th-level thief reads arcane scrolls with the printed 10% chance of error, resolved as a fizzle: the spell burns, and nothing resolves.

Cursed scrolls:

- A cursed scroll triggers when it's read, and picks one of the six compiled curses with equal chance. Energy drain and slow healing are automated, and the rest are manual.
- The energy drain curse resolves through `drain_levels` at the `halfway` XP policy, as the curse's wording says.
- Slow healing doubles the rest days that natural healing takes, and halves healing spells, rounded down. The curse halves healing, where *cause disease* blocks it, so the curse isn't a disease and attaches no condition.

Protection scrolls:

- A protection scroll attaches one ward to the reader that covers the whole party, with a count for each HD band in structured params (`affected_1_3`, `affected_4_5`, and `affected_6_plus`).
- The ward bars the listed creatures from melee, through the same rules as *protection from evil*. It breaks for the whole party when any member attacks a barred monster in melee.

Locked by `test_casting.py`, `test_exploration.py`, and `test_battle.py`.

### Devices spend a charge and a round, exhaust to inert, and never recharge

Using a wand, staff, or rod spends one charge and the user's action for the round.

- The use resolves in the magic phase, through the kernel's damage and save rules, with the item's printed area shape under [the area footprint](#the-area-footprint) rule.
- Device areas are destructive, like *lightning bolt*: a kill destroys the victim's equipment.
- An exhausted device goes inert, and the session refuses any further use with `items.device.inert`, rather than silently doing nothing.
- Devices never recharge and never show their charges.

Locked by `test_battle.py` and `test_exploration.py`.

### Rings cap at two, worn on hands

A character can wear at most two rings, `MAX_RINGS_WORN = 2`, one on each hand (RAW). The equip check refuses a third with `items.ring.hands_full`.

The ring of protection's 5'-radius variant also protects the members beside the wearer:

- In battle, it extends its AC bonus to the other members of the wearer's rank, against attacks. The battle space has nothing finer than the rank, and the printed example, "two characters fighting beside the wearer", is exactly melee defense.
- The bonus reaches allies as context the caller supplies (`AttackContext.defender_ally_ac_bonus`), and it never stacks across rings. The wearer's +1 comes from equipped items, as [Item bonuses](#item-bonuses-use-a-separate-channel-outside-the-cumulative-caps) describes.
- Saving throws, and anything outside battle, count the wearer alone, because a save resolves in the kernel for one character, and outside battle the party has no positions.

Locked by `test_items.py` and `test_magic_items.py`.

### The item streams

Items and treasure use these streams:

- `"treasure"`: every generation draw, which covers presence, quantity dice, values, sub-table rolls, charges, and sentience.
- `"npc_party"`: NPC composition, which covers alignment, class rows, level dice, scores, hit dice, and spell picks.
- `"magic"`: item use, beside spells. That covers device damage and saves, targeting for spells cast from scrolls, and the dice of potion effects.
- `"effects"`: durations rolled when an effect attaches, and the cursed scroll's pick of a curse, as [The combat kernel streams](#the-combat-kernel-streams) describes.
- `"advancement"`: the energy-drain sword's drains and the drain curse, beside `drain_levels`.

Locked by the phase 5 goldens and `test_treasure_kernel.py`.

### NPC adventuring parties: generation

The engine generates an NPC party with the SRD's procedure, on the `npc_party` stream:

1. One d6 alignment roll for the whole party.
2. For each member, the d8 row for class and level, then the level dice for the party's kind: 1d3 for a basic party, and the row's dice for an expert one. The compiler checks the demihuman level limits.
3. Ability scores of 3d6 in order. Class requirements are skipped, because the printed d8 row is authoritative, and re-rolling for a requirement would contradict it.
4. Hit points: the first-level hit die, then one `level_up` roll for each level above first.
5. Spells: picks with equal chance for each slot. An arcane caster's book holds exactly the spells it memorizes.

Equipment:

- Members wear a fixed set of equipment for their class, the SRD's "Basic equipment appropriate to their class" made concrete, with plate armour for expert clerics, dwarves, and fighters.
- The dwarf's battle axe is two-handed, so the dwarf carries the set's shield instead of using it.
- The set's generic Axe and Bow resolve to `battle_axe` and `long_bow`.

Treasure:

- Expert members roll Expert magic items at 5% per level for each suitable sub-table. Basic parties get none, per RAW's note that this is for Expert parties only. Rolls the member can't use are ignored, and better weapons and armour are equipped.
- The group shares one bundle of treasure types U and V, rolled on the treasure stream.

Locked by `test_npc.py` and the NPC-party golden.

### NPC parties in play: encounter sides like any other

An NPC party fights as an encounter side, like any monster group.

- `SpawnNpcParty` and wandering `npc_party` rows field a real generated party. Their count dice stay on the wandering stream.
- `EncounterGroup.monster_ids` contains the ids of combatants, monsters and NPCs alike.
- The roster event is visible only to the referee, so players learn what a party is made of by fighting it.
- An NPC group checks morale at the Veteran's morale score of 9, always counts as intelligent for distraction, and is worth XP as if each level were a Hit Die, recorded under `npc:<class_id>`.
- The default `NpcPartyPolicy` heals an ally below half hit points with the best cure it has, drinks a healing potion when no cure is left, casts automated attack spells highest level first, shoots at more than 5', and otherwise melees.
- NPC casters declare at the start of the round, and can be disrupted exactly like the party's casters.

Locked by `test_npc.py` and `test_battle.py`.

### Town services: full-value selling and the temple's six services

`SellTreasure` pays a valuable's exact rolled value to its carrier, because appraisal is exact: the engine knows each valuable's rolled value and doesn't estimate it (see [the treasure compiler](#the-treasure-compiler-entry-grammar-magic-clauses-and-treasureref-semantics)). Magic items have no fixed sale value, so the session refuses to sell one with `town.sell.no_fixed_value`. Pricing one is left to the game.

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
- Once the fee is paid, the purses after that pay nothing. The temple never takes value below a whole gold piece from any purse.

So what the party can spend is the whole gold pieces in each purse, not the purses' total worth. A party whose whole gold pieces fall short is refused with `items.purchase.insufficient_funds` and keeps every coin. For example, at 10 sp to 1 gp, two members who each have 12 gp and 5 sp are worth 25 gp between them, the fee for cure light wounds. Each purse can pay only 12 whole gold pieces, though, so together they pay 24 gp, and the party can't buy the service.

Raise dead is the reason the party pays. The patient is a corpse, no rule gives a corpse coin, and a party that splits its treasure never has 1,500 gp in one purse. So the documented revival flow (game over, `PlaceParty` to town, raise dead) works only if the party's purses can pay together. Locked by `test_award.py::TestTownServices`, `test_award.py::TestPooledHealingFees`, and `test_rules_calls.py::TestTempleRefusesAUselessService`.

### Listener-returned events are authored events

A listener returns only the events it creates itself.

- A listener that reacts by running referee commands, like the fetch-quest pattern, must not return those commands' events. The nested `execute` already logged them, and returning them would log them twice.
- So a replay of the command log runs without listeners, and matches the original event for event.
- The listener's stored state belongs to the game. Saves restore it, the game registers the listener again, and replay never re-derives it.

Locked by `test_session.py` and the phase 5 milestone golden.

## Documented adaptations

`Ruleset`-flagged deviations from rules-as-written. The flags `hp_reroll_at_first_level`, `encumbrance`, `variable_weapon_damage`, `individual_initiative`, `thac0_arithmetic`, `weapon_reload`, and `hd5_counts_as_magical` are SRD optional rules rather than adaptations, so this section doesn't list them.

### `deprivation_penalties` (default off)

With the flag on, going without food or water has penalties, which the SRD leaves "at the referee's discretion, for example". The engine tracks consumption whether the flag is on or off. The defaults come from the SRD's examples:

- After one full day without food or water, −1 to attack rolls, through a modifier effect the engine writes. Fatigue also comes after three turns without rest instead of six.
- After two days, movement also halves.
- From the third day on, the character loses 1d4 hit points each day, rolled on the effects stream.

Water and food deprivation don't stack, and the worse of the two applies. The numbers in the schedule are invented, since the SRD gives only an open list. Locked by `test_exploration.py::TestProvisions`.

### `aoe_friendly_fire` (default on)

With the flag on, an area that overlaps a melee catches the party too. An area that lands on a group at melee range (5' or less) adds the party's engaged front rank after the monsters, in marching order, and they use up whatever footprint capacity is left. With the flag off, an area aimed at a monster group never includes party members. Locked by `test_battle.py::TestAreaFootprint`.

### `formation_width_limit` (default on)

With the flag on, the space around the party limits how many combatants fight side by side. RAW prints one number and leaves the rest to judgement. The SRD says: "The referee should judge the number of opponents that can attack a single combatant, bearing in mind the combatant's size and the available space around them. **10' passage:** Enough space for at most 2–3 characters to fight side-by-side".

- osrlib uses the low end, two in ten feet, and measures the space instead of naming a number for each kind of cell.
- The rank width is the widest square of unbroken floor that includes the party's cell, with five feet of frontage for each combatant. That's two in a one-cell passage, however far it runs, and at a junction of two such passages. It's four in a room two cells square, and eight in a room four cells across.
- The measured space stops at walls, at doors, and where the space changes. A doorway is a threshold, not room to fight side by side, and a room's open mouth never counts the corridor beyond it.
- The same limit bounds how much of an area effect's footprint a formation takes.

With the flag off, there's no limit: every combatant may melee, and area footprints have no bound. Locked by `test_battle.py::TestFormationWidth`.

### `magic_item_death_save` (default on)

The SRD gives the referee an optional save for a dead character's magic items, and osrlib makes it the default. When a kill destroys equipment, as lightning and disintegration do, the engine rolls a save for each carried magic item:

- The engine rolls 1d20 for the item. The item saves when the roll reaches the owner's saving throw target in the category of what killed the owner: a breath weapon uses breath, a spell uses spells, a device uses wands, and anything else uses death.
- The item adds its best combat bonus to the roll, the highest of its attack, damage, and AC bonuses, so a better item is more likely to survive. A cursed item's bonuses are penalties, so its roll goes down, by 2 for a −2 item.

Items that survive go to the drop pile, a [`DropPile`][osrlib.crawl.dungeon.DropPile] of things lying on a cell that the party can pick up, instead of vanishing, and `EquipmentDestroyedEvent.saved_items` lists them. When the victim is a party member, the session moves the items to the drop pile on the party's cell in the command that killed the member, because no command can take anything from a dead member's inventory. With the flag off, items share their owner's fate. Locked by `test_combat.py::TestDestroyEquipment` and `test_loot_and_traps.py::TestBreathSavedItems`.

### `xp_award_timing` (default `on_return`)

`on_return` is RAW: monsters and treasure pay out when the adventure ends, as the change in the party's valuation since it left town, divided among the survivors.

`immediate` is the convention of computer role-playing games, offered as an adaptation:

- Monster XP is paid when the encounter ends, and treasure XP when the party gets the treasure, by taking it or as a quest grant.
- Each award divides among the members living at that moment.
- Dropping treasure never refunds its XP, so the return to town then awards nothing.

Neither timing pays twice. Locked by `test_award.py::TestImmediateTiming`.
