# Adaptations and pinned interpretations

osrlib follows OSE rules-as-written (RAW). Every place where that isn't enough is recorded here, as one of two kinds of entry: **pinned interpretations**, where the SRD text is ambiguous and the library commits to one reading, and **documented adaptations**, where the tabletop game assumes a human referee and the library provides a default behind a `Ruleset` flag. Look here instead of grepping docstrings. Every entry is also stated in the docstring of the member it applies to and locked by a test.

Each entry also cites the source files and test names that lock it. Those citations point into [the osrlib repository](https://github.com/mmacy/osrlib-python), not to pages on this site.

## Pinned interpretations

### Multi-prime-requisite XP tiers have no penalties

The SRD applies the standard XP-modifier table "to characters with a single prime requisite", and the elf's and halfling's class descriptions note only bonuses (+5%, +10%). Pinned: elf and halfling get exactly their stated bonus tiers and no penalty tiers. An elf with INT 9 and STR 9 has a 0% modifier, never −10%. Locked by `test_classes.py::TestXpModifier::test_multi_prime_requisite_classes_have_no_penalties`.

### XP tiers evaluate best-first, first-match-wins

The XP-modifier tiers are one uniform representation for all classes: ordered tiers of `{modifier_pct, minimum scores}`, evaluated best-first, and the first tier whose minimums all hold wins. The standard table's penalty rows only work under first-match-wins (a prime requisite of 7 must fall past the +10/+5/none tiers and reach −10%), and the halfling's "at least 13 in one prime requisite" +5% tier is expressed as two single-minimum tiers under the same rule. Locked by `test_classes.py::TestXpModifier`.

### XP percentage results floor

The SRD never says how to round a ±5/10/20% XP modifier. Pinned: the modified award is floored (integer arithmetic, toward negative infinity). Locked by `test_classes.py::TestApplyXp::test_modifier_floors`.

### First-level HP re-roll repeats until the die shows 3 or more

The optional rule says a roll of "1 or 2" may be re-rolled but not whether a re-rolled 1-2 is re-rolled again. Pinned: with `hp_reroll_at_first_level` on, the raw die (before the CON modifier) is re-rolled *while* it shows 1-2, each re-roll consuming a draw. Locked by `test_character.py::TestRollHitPoints`.

### Adjustment reductions are even per lowered score

The SRD says "For every two points by which an ability score is reduced, one point may be added", and doesn't say whether a single score may be lowered by an odd amount as long as the total is even. Pinned: the two-for-one trade is per score, so each lowered score drops by an even amount and no point of a reduction goes unused. The total raise must equal exactly the sum of reductions divided by two. Locked by `test_abilities.py::TestAdjustment`.

### Gear combat facets are exempt from class weapon policies

Torch, holy water, and burning oil appear on both the weapon table and the gear list. They compile as gear with an embedded combat facet (one entry per physical item, no item has two ids). Class weapon policies govern the weapons list only: a strict quality-tag reading would forbid a cleric holy water or a torch. One uniform rule applies instead: any class may buy, hold, and use all three. That grants more than a strict reading would, and on purpose: a magic-user (RAW: dagger only) may also throw oil or swing a torch. Locked by `test_items.py::TestEquipLegality`.

### Basic encumbrance tracks treasure weight against the general 1,600-coin cap

The SRD's maximum load rule ("The maximum load any character can carry is 1,600 coins of weight") sits in the general encumbrance text, and basic encumbrance says "The weight of treasure carried is tracked to make sure that the character's maximum load is not exceeded." Pinned: the cap is general, not a detailed-mode extra. Under basic and detailed tracking alike, tracked weight above 1,600 coins means the character cannot move (movement 0). The one exception is worn gauntlets of ogre power under detailed tracking, covered in the next entry. Basic mode's "carrying a significant amount of treasure" stays a referee judgment: a plain `carrying_treasure` boolean the game sets, no invented threshold. Locked by `test_items.py::TestMovementRates` and the encumbrance property tests.

### Gauntlets of ogre power raise every detailed encumbrance band

The gauntlets' page says "The character's ability to carry weight is increased by 1,000 coins. (i.e. the character can carry 1,400 coins without becoming encumbered.)" The 1,400 is the first detailed encumbrance threshold of 400 coins plus the bonus, and the page's encumbrance bullet refers to the detailed rules. While the gauntlets are worn under detailed encumbrance, osrlib adds the template's `max_load_bonus_coins` to every movement band and to the maximum load. The wearer moves at 120' up to 1,400 coins, 90' up to 1,600, 60' up to 1,800, and 30' up to 2,600, and the engine loads the wearer up to 2,600 coins when it spreads a haul. Basic encumbrance is unchanged: its rates follow armour, not weight, and its cap stays at 1,600 coins. Gauntlets carried but not worn add nothing. Locked by `test_item_rules.py::TestGauntletsOfOgrePower`.

### Miscellaneous gear is a flat 80 coins under detailed encumbrance

The SRD gives adventuring gear no per-item weights and says gear "may be counted as 80 coins of weight". Pinned: any miscellaneous gear carried adds a flat 80 coins, once, regardless of how much gear it is. Items with a listed weight of `-` (holy water, burning oil) have no separate tracked weight. They are gear, covered by the flat 80. Locked by `test_items.py::TestWeights`.

### Ammunition weighs 0

The ammunition table has no weight column, and the SRD states "The listed weight of missile weapons already includes the weight of the ammunition and its container." Pinned: ammunition compiles with weight 0. Locked by `test_srd_data.py::TestEquipmentData`.

### Sling stones cost 0, lot size 1

The sling stones cost cell reads `Free`. Pinned: cost 0, purchase lot 1. Locked by `test_srd_data.py::TestEquipmentData`.

### INT-granted languages come from the Other Languages table and may not duplicate natives

The SRD says high-INT characters "may also choose additional languages from the list of languages available in the setting" (at the referee's discretion). Pinned: choices come from the twenty Other Languages, and a choice may not duplicate a class native, so a dwarf cannot spend an INT language on Dwarvish. Locked by `test_character.py::TestExtraLanguages`.

### The attack matrix is clamp(THAC0 − AC, 2, 20) and extends beyond the printed columns

Every printed matrix cell equals `clamp(THAC0 − AC, 2, 20)`, locked by a property test over the compiled table, and AC values outside the printed −3..9 columns extend by the same formula: the printed bounds are a limit of the page layout, not of the rules. The clamping is what distinguishes matrix mode from the `thac0_arithmetic` flag once modifiers push totals past the plateaus. Locked by `test_monster_data.py::TestCombatTables` and `test_combat.py::TestAttackRoll::test_matrix_versus_arithmetic_divergence_at_the_plateaus`.

### Initiative ties re-roll

RAW offers "re-roll or resolve simultaneously". Simultaneous resolution is a different combat model the engine doesn't attempt. Pinned: tied sides, and tied individuals among themselves, re-roll in stable input order until distinct, each re-roll consuming draws. Locked by `test_combat.py::TestInitiative`.

### Slow actors act after all non-slow actors

Pinned reading of "always act last, as if they had lost initiative" for the general case: slow-weapon actors act after every non-slow actor, ordered among themselves by their side's initiative (each actor's result under individual initiative), then stable order. Locked by `test_combat.py::TestInitiative::test_slow_actors_act_last_by_side_initiative`.

### Helpless defenders are hit automatically in melee, consuming no attack draw

Paralysed, sleeping, and petrified defenders are hit automatically in melee: the engine consumes no attack roll and rolls only damage, per RAW ("Helpless opponents, such as those magically paralysed or frozen, can be automatically hit in melee"). A petrified creature is frozen in stone, so it counts as helpless (#166). A `No hit roll required` defender (green slime, yellow mould) is likewise hit without a roll. Locked by `test_combat.py::TestAttackRoll::test_helpless_defender_auto_hit_consumes_no_draw` and `test_rules_calls.py::TestMeleeHitsAPetrifiedCreature`.

### Morale "incapacitated" means dead, paralysed, petrified, or asleep

The RAW trigger text says "slain, paralysed, etc". Pinned for the morale trigger that fires when half the side is incapacitated, and for the sleeping dies-to-a-blade hook. Situational morale adjustments clamp to ±2 per RAW and never apply to ML 2 or less (never fights) or ML 12 or more (never checks). Two passed checks mean no further checks. Locked by `test_combat.py::TestMorale`.

### Energy drain reverses level_up

Per level drained, mirroring `level_up` exactly in reverse: above name level subtract the flat-bonus delta (no roll, no CON), otherwise roll the class hit die plus the CON modifier (minimum 1 per die) and subtract it. Rolling the lost die is the RAW-faithful reading of "loses one Hit Die of hit points" that keeps the model stateless, with no per-level HP history field. Drain never reduces max or current HP below 1 while the character retains a level. Death by drain happens only by losing the last level. XP policy is per-monster data: the wight sets XP to the floored midpoint of the former and new levels' thresholds, while wraith, spectre, and vampire set it to the new level's threshold exactly. Two-level drains apply the procedure twice and set XP once. On the terminal drain the killing level counts in `levels_lost` (a level-1 victim loses 1 level, and a spectre draining a level-2 fighter reports 2). A drained monster loses Hit Dice symmetrically: the instance re-derives THAC0 and saves from the reduced HD and loses a rolled d8, and below 1 HD it dies. It also counts its remaining Hit Dice where a spell like *sleep* spends a Hit Dice budget or decides whether the monster is a 4+1 creature. The spawn consequence (rises as a wight in 1d4 days) is a field on the drain event that the engine doesn't act on: the kernel kills the victim, and the game narrates what it becomes. Locked by `test_drain.py`.

### The damage pipeline order

The gate resolves first, then the damage roll (plus STR for melee), then the doublings (brace, charge, back-stab), then the minimum of 1, then the reductions (floored, never below 1). If the defender's `harmed_only_by` or energy defenses exclude the source, no damage is rolled and the event reports it. Save-for-half halving also floors. Back-stab adds +4 to hit and doubles damage, both halves from the thief tag's params, when the caller asserts an unaware target attacked from behind. Locked by `test_combat.py::TestDamagePipeline`.

### The WIS save modifier applies to magical non-breath saves only

Pinned reading of "does not normally include saves against breath attacks". The caller supplies any further referee discretion as a modifier. Locked by `test_combat.py::TestSavingThrows`.

### `hd5_counts_as_magical` implements the whole invulnerabilities optional rule

The flag's name covers only the 5+ HD half of the rule, but the flag governs both bullets of the SRD optional rule it cites. A monster of 5+ HD *and* another invulnerable monster therefore bypass the gate, because implementing half an optional rule would be an undocumented deviation. The boundary follows the rule's wording: the flag touches only weapon-material gates whose keys are a subset of {silver, magic}, and "another invulnerable monster" means a monster bearing such a gate. Element-keyed and mixed gates (the mummy's fire-or-magic, the black pudding's fire-only) are unaffected. Locked by `test_combat.py::TestDamagePipeline`.

### The `holy` damage key is admitted through any gate on undead targets, and only there

"Holy water inflicts damage on undead monsters": the specific rule overrides the general immunity. Otherwise the wight's silver-or-magic gate would absorb the one weapon made for it. Against anything living, the throw resolves normally and the damage pipeline reports no effect. That is deliberately *not* a validator rejection: rejections are free (no roll, no time, no log entry), and a rejection would be a zero-cost undead detector. Locked by `test_combat.py::TestSplashWeapons`.

### Variable weapon damage off means 1d6 for weapons and gear facets

RAW: "PC attacks inflict 1d6 damage", meaning weapons *and* gear combat facets. Unarmed attacks stay 1d2 (the specific unarmed rule, not a weapon) and monster damage is unaffected (monsters "deal the damage indicated in the description"). Locked by `test_combat.py::TestDamagePipeline::test_variable_damage_flag_off`.

### Splash damage is two applications

"Inflicted for two rounds" is pinned as two applications: the hit's damage now, and the douse effect's expiry applies the listed damage once more at the next round boundary. Unlit oil deals no damage. The caller may compile it into a location-attached pool (3-foot pool, burns 1 turn once lit, 1d8 to creatures passing through), and the caller asserts who passes through. In a session, the crawl layer anchors the pool to a cell, as [Light, darkness, and location-bound effects](#light-darkness-and-location-bound-effects) describes. `uses_fire` monsters ignore burning oil outright. Locked by `test_combat.py::TestSplashWeapons`.

### Torch and burning oil deal fire damage, and holy water has the holy key

The damage sources of the three dual-listed gear items are pinned. A torch or oil hit is fire damage, so it goes into the troll's non-regenerable ledger. Locked by `test_combat.py::TestSplashWeapons::test_douse_applies_twice_then_expires`.

### Breath-weapon daily limits are per-monster data

The three-per-day limit is per-monster data. The SRD prints it for the dragons, the dragon turtle, and the chimera. The hellhound has no daily limit: its 2-in-6 per-round chance is data that the default action policy reads, and the kernel resolves the breath whenever it's invoked. Every breath weapon is a destructive death (pinned): the SRD's destruction-of-items examples ("a lightning bolt spell or a dragon's breath") illustrate energy deaths generally, so the hellhound's fire kill destroys the victim's mundane equipment too. A dragon's defense against its variant's breath element(s) is immunity to nonmagical sources plus an automatic save against magical ones. Locked by `test_combat.py::TestBreathAndGaze`.

### The HD-budget targeting mode consumes weakest-first

Candidates sort weakest-first by effective HD, with sub-1 HD rounding up to 1, fixed hit-point bonuses dropped, and characters counting their level, ties broken by stable input order. The budget spends whole creatures, and a target whose HD exceed the remainder is skipped while selection continues. For the *sleep* spell's arithmetic, see [Sleep's mode arithmetic](#sleeps-mode-arithmetic). Locked by `test_combat.py::TestTargeting`.

### Monster XP validation: above-21-HD inflation and the negative-modifier band

Printed XP is authoritative and cross-validated at compile time: base by HD row plus asterisks × the bonus column, where above 21 HD *both* amounts first gain 250 per HD above 21. The dragon turtle (HD 30*, XP 9,000) matches this reading. Negative hit-point modifiers map to the *lower* band: the goblin's 1-1 validates against "Less than 1" (XP 5) while still using the unmodified "Up to 1" attack-matrix row (THAC0 19 [0]). The attack-as-1-HD-higher rule is for bonus modifiers only. Table mismatches fail the build unless recorded as overrides. Locked by `test_monster_data.py::TestCombatTables`.

### Poison failure is death, with optional onset; riders leave the damage unaffected

Save versus death or poison, and failure is death, either immediate or an onset-delay effect for forms like the giant rattler's "death in 1d6 turns". Poison riders on damaging attacks leave the damage unaffected by the save, per RAW. Locked by `test_effects.py::TestLedger::test_delayed_death_poison`.

### Petrification suspends the target's other effects

While a target is petrified its other attached effects suspend, with no ticks and durations frozen, so an adventurer who was poisoned before turning to stone is still poisoned after *stone to flesh*. Petrification is permanent but recoverable, because a petrified creature isn't dead. Locked by `test_effects.py::TestLedger::test_petrification_suspends_other_effects`.

### The canonical tick order

At each round boundary, expirations resolve before ticks, and simultaneous effects resolve in attachment order, tie-broken by effect id. Only the effects engine (and the kernel's death routine, for `dead`) writes creature conditions. Locked by `test_effects.py::TestLedger`.

### Troll damage splits into regenerable and non-regenerable ledgers

Fire and acid accrue in a separate non-regenerable ledger on the instance. Regeneration never heals that ledger, and the troll is permanently dead only when non-regenerable damage alone reaches max HP. Otherwise death at 0 hp schedules a 2d6-round revival (rolled on the effects stream), counted from the round of the killing damage, with the revived troll rising at 1 hp. Locked by `test_effects.py::TestRegeneration` and the troll battle golden.

A character who wears a ring of regeneration has the same ledger, in `Character.nonregen_damage`, because the ring's page says "Fire or acid damage: Cannot be regenerated." The ring heals 1 hp a round up to max HP less the ledger, and heals nothing once the wearer is at 0 hp. Other healing, like a potion or a cure spell, heals the ledger's damage as well as the rest. `deal_damage` doesn't change the ledger when the wearer heals. Instead, the next time the wearer takes damage, it first trims the ledger to the damage the wearer had before that hit. It trims a troll's ledger the same way, though a troll's regeneration never lets the ledger exceed its damage. The field defaults to 0, so a save from before it existed loads under the same `schema_version`. Locked by `test_drain_and_regen.py::TestRegenerationRing`.

### Mummy rot heals naturally once per ten full rest days and blocks magical healing

"Magical healing is ineffective; natural healing is ten times slower" is pinned as one 1d3 recovery per ten consecutive full rest days, tracked on the effect. *Cure disease* removes it. `apply_healing` defaults its source to `magical` (osrlib treats instantaneous healing as magical healing), so a caller that forgets to name the source still respects the block. Locked by `test_effects.py::TestMummyRot`.

### Asleep grants auto-hit and dies-to-a-blade; dozing is a caller modifier

The asleep condition, which the *sleep* spell inflicts, has two combat hooks: a sleeping defender is hit automatically in melee, and "a single attack with a bladed weapon can kill", so the melee hit kills outright with no damage roll. Pinned: "bladed" means a weapon (not a gear facet, a monster's natural attack, or an unarmed strike) with the melee quality and without the blunt quality, since the SRD's blunt list exists to separate crushing weapons from edged ones. The hook is melee-use only, and the immunity gate still applies first (a sword absorbed by the black pudding's fire-only gate kills nothing). The dragons' "may be attacked for one round with a +2 bonus" dozing is the caller-supplied situational modifier, not a condition. Locked by `test_combat.py::TestDamagePipeline::test_sleeping_defender_dies_to_a_blade` and its neighbors.

### Falling damage floors per full 10 feet

The engine rolls 1d6 per full 10 feet fallen, and nothing at all for a fall under 10 feet. Locked by `test_combat.py::TestHealingAndFalling`.

### Reload is enforced from caller context

A reload-quality weapon may not fire two rounds running: under the `weapon_reload` flag the attack validator rejects the attack when the caller-supplied context shows that the weapon fired last round. In a session, the battle machine tracks which members fired last round and supplies that context. A direct caller of the kernel supplies it instead, and the kernel enforces the rule only as far as that context is accurate. Locked by `test_combat.py::TestValidateAttack`.

### Two-handed weapons and shields conflict at equip time

`validate_equip` rejects wielding a two-handed weapon with a shield equipped, or equipping the second of the pair, with `items.equip.two_handed_with_shield`. The conflict is pinned at equip time, rather than resolved by silently ignoring the shield at resolution. Locked by `test_items.py::TestTwoHandedShieldConflict`.

### Monster spawn HP is minimum 1, ½ HD is 1d4, fixed forms are exact

Spawned HP is the sum of the HD count in d8s (d4 for ½ HD) plus the signed modifier, minimum 1. The engine rolls nothing for `1hp` and for the hydra's 8-hp-per-HD forms. Locked by `test_monster_data.py::TestSpawnHitPoints`.

### Compiled monster data conventions

`No hit roll required` AC means attacks auto-hit, and `Varies` morale compiles to none. Packed-variant pages expand to concrete entries under enumerated ids, because a frozen template must be spawnable. `undead` auto-tags from the standard verbatim bullet with the shadow excluded ("incorporeal (but not undead)"), `person` comes from the `General.md` §Persons list (the SRD's default for a rule otherwise left to the referee's judgment), and `enchanted` is hand-curated via overrides with a reason per entry. Embedded XP values are authoritative, compound alignments compile to an options model, and leader or chieftain XP compiles to structured notes with stats left as prose. Locked by `test_monster_data.py`.

Every attack routine carries a `use` label. The first is `primary`. A later routine whose attacks are a breath, a gaze, or a spell is `special` by name, and every other later routine is labelled by hand from the monster's text: `alternative` where the SRD's "or" is a free choice each round, and `situational` where the text ties the routine to a range, a chance, water, a ship, a form, or a loadout. The compiler labels the routines after the overrides apply, so the bone golem's override-split routines are labelled too, and it fails the build on a later routine with no label, naming the monster. The stat blocks printed on spell pages go through the same labelling. Locked by `test_attack_routines.py::TestTheCatalog` and `TestTheCompilerRefusesAnUnlabelledRoutine`.

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

Consumption takes the lowest tuple index, and disruption removes a copy identically ("as if it had been cast"). *Cause wounds* touch attacks take a melee attack roll only in combat ("In combat, a melee attack roll is required", with the pinned inverse: outside combat, the touch needs no roll), and the copy is spent whether or not the touch lands, because RAW has no rule for holding the charge. Locked by `test_casting.py::TestCuresAndRestoration`.

### The holy symbol is not a mechanical gate on casting or turning

`Cleric.md` states "A cleric must carry a holy symbol" as a class edict alongside deity faithfulness, not as a precondition on any procedure. The holy symbol is in the gear data, and games wanting the stricter reading check inventory themselves. As a result, a cleric without a holy symbol can still turn undead and cast spells. Locked by `test_turning.py::TestValidation::test_holy_symbol_is_not_a_gate`.

### Starting books are filled to capacity, book capacity is per level, and books never auto-shrink

Arcane casters begin play with exactly as many spells as they can memorize, one first-level spell at level 1. Clerics start with nothing, because their level-1 row has no slots. `add_spell_to_book` caps the book, per spell level, at the current slot count for that level (the RAW "exactly the number of spells that the character is capable of memorizing", read per level). The book is a physical object and never auto-shrinks: a drained character may have a book over capacity and cannot add more. Drain forgets excess *memorized* copies newest-first per level (RAW is silent, and newest-first is deterministic without new state). Locked by `test_memorization.py`.

### Category and immunity gates resolve as no-effect, never as rejections

The holy-water reasoning applies here too: rejections are free and would leak hidden state, so casting *charm person* at a disguised doppelgänger or *sleep* at a wight must not be a zero-cost detector. Casting at an ineligible target consumes the copy and reports `magic.cast.no_effect`. Immune and excluded creatures consume no HD budget, because exclusion happens inside resolution rather than as a validator refusal. Locked by `test_casting.py::TestCastingPipeline::test_no_effect_still_consumes_the_copy` and `TestSleep`.

### Sleep's mode arithmetic

Mode 1's "a single creature with 4+1 Hit Dice" is pinned as a monster with HD count 4 and a positive fixed modifier. Mode 2 ("2d8 HD of creatures of 4 HD or less") excludes exactly those, costs each creature its HD count with fixed bonuses dropped (the spell page's "3+2 counts as 3") and sub-1 HD rounded up to 1, spends weakest-first with stable-order ties through the HD-budget targeting mode, and grants no saving throw. Both modes judge a drained monster by the Hit Dice it has left, with its template's modifier. An ogre drained from 4+1 to 3+1 is no longer mode 1's creature, and mode 2 takes it for 3 HD. A 5 HD grizzly drained to 4 HD becomes eligible for mode 2. Locked by `test_casting.py::TestSleep` and `test_drain_and_regen.py::TestSleepOnDrainedMonsters`.

### Magic missile is 1 + 2×⌊(level−1)/5⌋ missiles, resolved instantly

Pinned from "three missiles at 6th–10th level, five missiles at 11th–15th level". Each missile takes one supplied target (repeats stack), hits unerringly with no attack roll and no save, and resolves at cast, so the printed 1-turn duration has no effect in the engine. Locked by `test_casting.py::TestMagicMissile`.

### Lightning bolt kills destroy equipment; disintegrate kills permanently and destroys carried gear

*Lightning bolt* is a destructive death source. *Disintegrate* destroys "the material form ... instantly and permanently": pinned as permanent death plus equipment destruction, because the material form includes what the creature carries. Locked by `test_casting.py::TestFireBallAndLightningBolt` and `TestSaveOrDie`.

### Spell damage presents the `magic` key

A damage-dealing spell passes weapon-material gates whose keys include `magic`: a wight's silver-or-magic gate admits *magic missile*, a gargoyle's magic-only gate admits *fire ball*. Without the key, those gates would block spell damage. Locked by the Phase 3 spell-battle golden (three missiles damaging the wight).

### The hold/charm person gate admits any character and only `person`-category monsters

Undead are never affected (their exclusion resolves as no effect). *Hold monster* and *charm monster* affect anything but undead. *Charm monster*'s single mode affects one creature of more than 3 HD, and its group mode affects 3d6 creatures of 3 HD or less. Locked by `test_casting.py::TestHoldAndCharm`.

### Charm re-saves are monthly/weekly/daily by INT, with month = 30 days

INT 3-8 saves monthly, 9-12 weekly, and 13-18 daily, with a month pinned to 30 days and a week to 7. Monsters have no INT score and default to the middle weekly band (override-correctable per monster). The re-save is a tick-time draw on the `effects` stream, and a passed save releases the charm. Locked by `test_casting.py::TestHoldAndCharm`.

### The cumulative-effects rule: largest bonus plus largest penalty per statistic

`Spells.md`: "Multiple spells affecting the same game statistic do not combine." Pinned: at consultation time only the single largest bonus and single largest penalty apply, so a *bless* and a *blight* offset and two *blesses* don't stack. Spell modifiers combine freely with non-spell sources (the RAW carve-out for magic items). Spell morale modifiers use `check_morale`'s existing modifier argument inside the morale rule's ±2 clamp and ML 2/12 exemptions: one uniform adjustment rule rather than a second channel. Locked by `test_casting.py::TestBuffsAndWards` and `test_spell_properties.py`.

### Shield's AC values apply as better-of

"AC 2 [17] against missiles, AC 4 [15] against other attacks": the target's effective AC is the better of the target's AC and the set value, never worse, so a target in plate armour keeps its plate AC. Locked by `test_casting.py::TestBuffsAndWards`.

### Striking applies to weapon attacks of any kind, never unarmed, and attaches to the wielder

RAW names no weapon type, so missile weapons qualify, and unarmed strikes gain nothing. The enchantment attaches to the *wielder* because item instances have no ids. In the corner case where the wielder hands the weapon away mid-duration, the enchantment stays with the wielder. The wielder counts as magical for weapon-material gates while the enchantment lasts. Locked by `test_casting.py::TestBuffsAndWards`.

### Protection from evil: the melee ban is data, and monster alignment resolves at spawn

The enchanted/constructed/summoned melee ban is structured params that the battle machine reads (the kernel can't see who initiates melee). The ward's ±1 applies against creatures of another alignment, which needs a resolved alignment where `MonsterTemplate.alignment` is multi-option: `MonsterInstance.alignment` resolves at spawn (caller's choice, else the template's `usual`, else its sole option), and an unresolved alignment counts as differing, so the ±1 applies to a creature whose alignment is unresolved too. Locked by `test_casting.py::TestBuffsAndWards`.

### Small nonmagical missiles are character weapon missiles and thrown splash items

*Protection from normal missiles*' boundary, pinned from the spell page's examples ("no protection is granted against hurled boulders or enchanted missiles"): missile weapon uses and thrown splash items set the `DamageSource.missile` flag. The engine never sets the flag on a monster attack by itself. The caller sets `AttackContext.monster_missile` when the attack is a small missile in the fiction (a hobgoblin's arrow). Locked by `test_casting.py::TestBuffsAndWards::test_protection_from_normal_missiles_boundary`.

### All spell-attached effects are dispellable, monster effects are not, and survival is 5% per level of deficit

RAW says *dispel magic* "ends spell effects" without qualification: `permanent=True` means "no duration expiry", not "undispellable" (*continual light* and *flesh to stone* can be dispelled). Effects attached by `cast_spell` record the caster's level, and per effect, when the recorded level exceeds the dispelling caster's, the effect survives on a d100 at or under 5% per level of difference. Monster-inflicted effects (poison, rot, regeneration) are non-dispellable by construction, and magic items are exempt. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### Raise dead: 0 days at level 7, a 14-elapsed-day weakness, and permanent undead destruction

The time limit is 4 days per caster level above 7th, which is 0 days at level 7, RAW-faithful. Revival sets 1 hp and attaches the weakness, pinned to 14 elapsed days as the simplification of "two full weeks of bed rest" (rest tracking belongs to the crawl layer, and games wanting strict bed-rest semantics extend or release via the ledger). While it runs, the subject cannot attack, cast, or turn undead (RAW bans "other class abilities" too), moves at half rate (a marker param the crawl layer reads), and healing from *every* source is blocked, because RAW says the subject "has 1 hit point" until the recovery completes and no magical healing shortens it. The hit point returns to normal recovery when the weakness ends. Only characters are raisable (all four Classic races are human or demihuman, and monsters are not). The destroy-undead usage kills permanently on a failed save, matching turning's `D`. Locked by `test_casting.py::TestCuresAndRestoration::test_raise_dead_level_seven_allows_zero_days`.

### Neutralize poison's revival window is caller-attested, and only characters revive

"A **character** who has died from poisoning can be revived, if *neutralize poison* is cast within ten rounds": the kernel has no cause-of-death model, so supplying `CastContext.rounds_since_death` *is* the caller's attestation that the target died of poison within that many rounds. Omit it for any other death. Only a `Character` is revivable (the page's usage is titled "Characters"), and revival stands the subject up at 1 hp (pinned, since RAW names no hit point total). Locked by `test_casting.py::TestCuresAndRestoration`.

### Cure light wounds removes paralysis from any source

"Curing paralysis: Paralysing effects are negated." The `cure_paralysis` mode releases each ledger effect that paralyses the target, then removes any `paralysed` condition still left on it, like one a game granted with `grant_condition` and no effect behind it. It removes no other condition. The other cures, like *cure disease* and *neutralize poison*, still lift a condition only by releasing the ledger effect that granted it (#166). Locked by `test_rules_calls.py::TestCureLightWoundsLiftsParalysis`.

### Silence automates both forms: moving on a failed save, stationary on a passed one

A failed save attaches `silenced` (the casting validator consumes it) and the silence moves with the creature. On a passed save, RAW leaves a *stationary* area the creature can step out of. Location-bound effects anchor to cells, so the save-passed outcome attaches a stationary silence effect to the target's cell, pinned as the party's current cell (the encounter's abstract location). Creatures in that cell cannot cast while there, battles included. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`, `test_exploration.py::TestStationarySilence`, and `test_battle.py::TestSilencedCellInBattle`.

### Web escape tiers depend on STR, with the augmented and giant tiers caller-asserted

Normal strength escapes in 2d4 turns, rolled at attach on the effects stream. Magically augmented STR above 18 escapes in 4 rounds and giant strength in 2 rounds, asserted via `CastContext.strength_tiers` until such effects exist. The blocking cube is a location effect (see [Light, darkness, and location-bound effects](#light-darkness-and-location-bound-effects)), and the flammability rule stays manual. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### Feeblemind targets only `arcane_magic` casters

The spell page's target is "an arcane spell caster (e.g. a magic-user or elf)": the gate is the target bearing the `arcane_magic` class tag, and anything else resolves as no effect. Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### The turning column is the HD count, `2*` only at count 2, counts above 9 unturnable

A monster's column is its `hit_dice.count` as a string, except count 2 with a special ability (asterisked HD) maps to `2*` (the table's footnote), counts 7-9 share `7-9`, and counts above 9 have no column, so turning fails. The printed table is the rule, and the "referee may expand" footnote is left to game data. Modifiers never shift columns (the mummy's 5+1 turns on column 5) and the asterisk matters only at count 2 (the wight's `3*` is column 3). Locked by `test_turning.py::TestColumnMapping` and the verbatim table fidelity test.

### The turning pool spends lowest-first, stops at the first unaffordable monster, and always affects one

The engine compares one 2d6 turn roll per candidate *type* against its cell, and on any success one 2d6 HD pool follows. Eligible monsters are affected lowest-HD-first (stable input order on ties), each costing its HD count (minimum 1 and fixed bonuses dropped, the *sleep* convention). The pool stops at the first unaffordable monster (RAW: excess Hit Dice "are wasted", not reallocated), and at least one undead is always affected on a success, pinned to the cheapest eligible monster. `D` results are permanent destruction, and the rest gain an indefinite, non-dispellable `turned` condition the encounter releases. Only `undead`-category monsters are candidates, and non-undead resolve as unaffected. Locked by `test_turning.py` and `test_spell_properties.py`.

### The `magic` RNG stream, and the attach-time/tick-time split

Every draw inside spell resolution (targeting dice, damage dice, touch-attack rolls, cast-time forced saves, dispel survival rolls, and both turning rolls) comes from the `"magic"` stream, so a combat-rules change never shifts spell goldens and vice versa. Effect-internal draws (rolled durations at attach, *web* escape dice, tick-time saves like the charm re-save) stay on the `"effects"` stream per [The combat kernel streams](#the-combat-kernel-streams), which is why `cast_spell` takes both streams. Spell resolution has no `Ruleset` flags: every choice is rules-as-written or a pinned interpretation, never a referee-optional variant. Locked by the Phase 3 milestone goldens.

### Encounter tables: hand-mapped ids, table counts override, variant pools

The encounter-table cell names hand-map to compiled monster ids with compile-time resolution (the printed links are unresolvable), and the printed count dice override the monster description's number appearing, per the SRD's note. Packed-variant cells (`Veteran`, `Vampire`, `Hellhound`) compile with the full id tuple and each spawned individual picks uniformly on the wandering stream. The two hydra cells have `variant_dice`, so the engine rolls the printed HD dice once on the wandering stream and selects the template. NPC-adventurer rows compile faithfully as structured `npc_party` entries, and the session fields a generated party for them (see [NPC parties in play](#npc-parties-in-play-encounter-sides-like-any-other)). The "Basic Adventures" typo and the singular "Expert Adventurer" labels normalize via `overrides/encounter_tables.json` with provenance. Locked by `test_encounter_data.py` and `test_exploration.py::TestWandering`.

### Encounter-table and reaction models live in core/tables.py

Pinned for layering: `osrlib/data/` loaders import the modules that define their models, and `core` modules import the loaders, so defining these models under `crawl/` would give `load_encounter_tables()` a transitive import from core through data into crawl. The reaction table clamps totals into its outer bands ("2 or less" / "12 or more") and reaction rolls are referee-visible (players learn reactions from behavior, as they learn morale results). A dungeon level's encounter table is its number clamped into the printed bands (1, 2, 3, 4-5, 6-7, 8+). Locked by `test_encounter_data.py` and `test_kernel_checks.py::TestRollReaction`.

### The crawl streams

The crawl layer's stream keys are `"wandering"` (the check die, the d20, count dice, variant picks, NPC re-rolls), `"encounter"` (surprise, distance, reaction, distraction), `"exploration"` (forcing, listening, searching, trap springs and trap resolution, tinder, thief skills), and `"monster_action"` (every policy draw, so a substituted policy never shifts attack or damage draws). Trap resolution draws (saves, damage, volley counts) run on the exploration stream, because they are part of the trap procedure, while attach-time condition durations stay on the effects stream per [The combat kernel streams](#the-combat-kernel-streams). Locked by the Phase 4 delve golden and `test_battle.py::TestDefaultPolicy`.

### Detection checks: precedence, zero-chance, and pick-pockets arithmetic

The detection-chance precedence is thief row, then class tag, then the universal 1-in-6 baseline. Construction tricks are the exception, dwarf-only with a **zero** baseline, because the SRD grants that perception to dwarves alone. A zero chance consumes no draw. Pick pockets caps at 99 ("always at least a 1% chance of failure"), takes the victim's over-5th-level penalty as a caller modifier, and a roll of more than twice the effective chance is noticed. Locked by `test_kernel_checks.py`.

### The time-cost census and the thirds odometer

Zero time: `TurnParty`, `ReorderParty`, `OpenDoor`, `CloseDoor`, `WedgeDoor`, `ForceDoor` (forcing takes a moment, and its cost is the noise), `ListenAtDoor` (the once-per-door cap is the limiter), equip/unequip, `DropItems`, `ExtinguishSource`, `PurchaseEquipment` (town time is abstract), and all referee commands. One round: `CastSpell` and `TurnUndead` outside battle, `LightSource` (per tinder attempt too, RAW). One turn: `Search`, `InspectTreasure`, `RemoveTreasureTrap` (pinned symmetric, where RAW is silent), `PickLock` (pinned, where RAW is silent and the skill is delicate work), `TakeTreasure` (RAW's packing-bags turn), `Rest(turn)`. On the odometer: `MoveParty` per cell, a passage at the cost of a step onto its landing cell, and `UseStairs` at one unexplored-cell cost (pinned). Stated durations: `Rest(night)` 48 turns, `Rest(day)` 144, `PrepareSpells` 6, and travel the turns that the town's `travel_turns` lists for the dungeon. Movement accrues in thirds-of-feet, an unexplored cell 30 units and an explored cell 10, implementing the SRD's three-times-through-familiar-areas rule exactly, and turn-costing actions snap the clock to the next boundary, absorbing the partial move. Locked by `test_exploration.py::TestTimeCostCensus` and `TestOdometer`.

### Rest, fatigue, sleep, and provisions

A living entangled member blocks party movement (the party doesn't leave a member behind implicitly, and leaving someone is a referee-command decision). Fatigue (−1 attack, −1 damage, engine-written) attaches after six consecutive unrested turns, the dungeon rule, since town and travel time don't accrue. A full turn of rest clears it, even when a wandering monster interrupts the rest on a later turn. An interrupted rest's full turns are the whole turns of time before the check that turned up monsters, because that check is the first step of the next turn. A rest broken during its first turn therefore clears nothing. Each full turn also counts toward the three that clear running exhaustion, and the next rest adds to that count. A night is 48 turns, since the SRD has no time-of-day model and a night is therefore a duration. Preparation requires an uninterrupted night's sleep, so an interrupted night isn't sleep, even when its full turns clear fatigue. Preparation happens at most once per sleep and costs six turns for divine and arcane casters alike. A wandering encounter mid-preparation doesn't void the already-written memorization (pinned). An interrupted rest day heals nothing (RAW). At day boundaries, each member eats a standard ration before an iron ration (fresh food spoils first, where RAW is silent), and a carried waterskin satisfies the day (the engine doesn't track water by the pint). In town, carried rations are still eaten, but nobody goes short. Locked by `test_exploration.py::TestRestAndFatigue` and `TestProvisions`, and by `test_rest_credit.py`.

### Doors: forcing, noise, swing-shut, locks

Any door-forcing attempt sets the noise flag for the next wandering check (the banging is identical either way) while only a *failed* attempt denies the party surprise against the room beyond (RAW, exactly as written). Doors the party opened swing shut when the party is no longer in either adjoining cell unless wedged: RAW's "likely to swing shut" becomes always, so a party that wants a door to stay open behind it has to wedge it. A forced stuck door is stuck again once shut. A failed pick-lock locks that thief out of that lock until the next level. Listening is once per character per door, free, and rolled regardless of occupants. Undead make no noise, so silence is ambiguous (the rule that a result never reveals hidden state, applied to exploration). Locked by `test_exploration.py::TestDoors` and `TestListening`.

### Searching and traps

Searching covers the party's current cell (the 10' × 10' area is the cell), costs a turn, is once per character per cell per kind, is rolled regardless of contents, and reveals every matching hidden feature on the cell. Treasure-trap find and removal are once per trap per character each, and a failed removal springs the trap, which is the classic reading. RAW says only "attempted once per trap". Found traps stop springing on movement (the party walks around the known pit), and the engine emits the player-facing `exploration.trap.safe` only for a *known* trap whose trigger resolved without springing. An unknown trap's spring die stays referee-only.

What finding a trap does depends on its kind (pinned): a found *room* trap never springs at all, at its area's edge or at one of its doors, while the engine still rolls a found *treasure* trap's 2-in-6 on every `TakeTreasure` until a thief removes it, because in B/X anyone can find and avoid a room trap and only a thief can take a treasure trap out. `exploration.trap.safe` is therefore a cache outcome only: past a found room trap, the engine rolls no die for a door or an area edge, so there is nothing to report.

A trap transition (slides) relocates the whole party, because the party model has one location (pinned simplification). It does so only when at least one victim fails the trap's save, so a party that passes every save stays on the trapped cell, and a transition with no save always moves the party (#167). The darts volley resolves as one damage application whose rolls are every dart's die. `TakeTreasure`'s treasure trap springs on the character who reaches in: the command's `recipient_id` when one is named, otherwise the first living member in marching order (pinned).

A room trap's `trigger` is the action that springs it: `enter` rolls on entering a cell of the area, `open` rolls on opening a door of the area, through `OpenDoor` or a successful `ForceDoor`, with the same 2-in-6 on every opening until the one spring. A door belongs to the area from either adjoining cell (pinned: the blade over the lintel drops on the way out too). If one door joins two trapped areas, the far side rolls first. Each spring hits the opener (the forcing character, or else the first living member in marching order), passing to the next member standing if an earlier spring killed the opener. A spring needs a living victim, so the engine rolls no spring die for a party with no one left, and a spring whose effect carries the party away from the door (a chute) cancels the springs behind it, because the party is no longer performing the opening (all pinned).

A `room_traps` search covers the searched cell's door edges, so the `open`-trigger trap beyond a known door is findable from the threatened side before the door is opened. An undiscovered secret door hides its trap along with itself (a result never reveals hidden state), and a found door trap never springs, like the walked-around pit. Because those two rules compose into an unfindable trap, discovering a secret door clears the `room_traps` attempts of both cells the door's edge joins, whichever side the discovery was named from and whether it came from a `secret_doors` search or from the referee's `SetDoorState` with `discovered=True` (pinned: the discovery is new information about both cells' 10' squares, so a re-search is earned on either side). `secret_doors` and `construction` attempts on those two cells, and every attempt on every other cell, are untouched.

A treasure trap's only springing action is opening its cache, so the model rejects `trigger="enter"` on `kind="treasure"`. Schema version 3 added the rejection, and its lossless migration rewrites the unused value to `"open"` on load. Locked by `test_exploration.py::TestSearching`, `TestTreasureTraps`, `TestDoorTraps`, `TestTwoTrappedAreas`, `TestTrapResolutionCensus`, `TestDiscoveringASecretDoorRefundsTheCellsTrapSearch`, and `TestDiscoveringASecretDoorRefundsBothCells`.

### A recovered haul spreads across the party: usable items, even wealth, everyone mobile

RAW never says who physically carries the loot. It says the opposite, that the split is the players' business: "Awarded XP is always divided evenly, irrespective of how the players decide to divide the treasure" (*Awarding XP*). What RAW *does* state are the consequences: "The maximum load any character can carry is 1,600 coins of weight. Characters carrying more than this cannot move", and "The movement rate of the party as a whole is determined by the speed of the slowest member" (*Time, Weight, Movement*). Together those make a single-carrier default a bug that can stop the whole party: one pickup on one character can freeze everyone. There is no tabletop referee here to say "spread it out", so the engine does, as a documented adaptation:

- **Items go where they are useful.** The engine prefers a carrier whose class may use the item, judged by the same class policies `validate_equip` enforces plus a magic item's `usable_by`: the fighter takes the plate mail, the magic-user the arcane wand, the cleric the divine staff. Equipped state is *not* consulted: the question is where the item belongs, not whether the carrier's hands are full at that moment. When nobody's class can use it, it still goes in somebody's pack.
- **Gems and jewellery divide by worth, not by count.** Three 50 gp gems against one 300 gp ring is even in objects and lopsided in wealth, and wealth is what a share means. Value is also cheap to move, since a 1,000 gp gem weighs one coin, so balancing it costs the party nothing in mobility. The engine deals pieces richest-first, each to whoever currently holds the least by value.
- **Coins divide evenly, denomination by denomination.** Every coin weighs 1 whatever its metal, so an even split of a denomination is even in worth *and* even in weight, the one place where a fair share and a mobile party come to the same thing. Coins are not used to compensate for a big gem: matching a 1,000 gp gem in coin means 1,000 coins of weight, which would immobilise the carrier.
- **Nothing is ever loaded past the maximum load.** A carrier who would cross their maximum load, 1,600 coins unless worn gauntlets of ogre power raise it, does not take the parcel, and the search moves on. A take therefore cannot immobilise anyone that some other arrangement would have kept moving.
- **What will not fit stays where it lies**, in the drop pile on the party's cell, with an `exploration.item.left_behind` event. Treasure is never destroyed, so the party can lighten its load and come back for it. Goods pack best-first, items then valuables then coins richest denomination first, so a party out of capacity keeps the platinum and leaves the copper.

The order is items, then valuables, then coins: the engine places items, which only some classes can use, while every carrier still has room for them, and valuables and coins follow to even out the shares. The whole pass is deterministic and spends no RNG draw: ties break on marching order, never on a die. It is automatic bookkeeping, not a ruling: `GiveItems` and `DropItems` rearrange it freely, and the XP award is untouched either way, since `party_valuation_cp` sums the whole party. `TakeTreasure` also accepts an explicit `recipient_id`, which narrows the carriers to that one member (still capped at that member's maximum load). Locked by `test_exploration.py::TestTreasureDistribution`.

### Light, darkness, and location-bound effects

Lighting consumes the consumable (one torch, one oil flask) and extinguishing forfeits the remainder, with no partial-burn bookkeeping, and relighting starts a fresh consumable. When a light-kind effect expires the session appends `exploration.light.expired` for players (the ledger's expiry event is referee visibility). The tinder box gates lighting when the party has no open flame (an active `brightness="flame"` effect), 2-in-6 per round, RAW. A darkness-family effect on any member suppresses the whole party's light while it runs (the printed radii cover a marching party) and `blocks_infravision` disables infravision too. Following RAW, `Search`, `InspectTreasure`, `PickLock`, and reading require light, while searching and listening alone also work on the actor's infravision. `MoveParty` in darkness is legal, but the party is surprised on 1-3 instead of 1-2 when unlit and not every living member has infravision (the blind-party adaptation). A cell is visible when it's explored or is the party's current cell, a simplification of the spec's visible flag. Location effects anchor to `cell:{dungeon}:{level}:{x},{y}` references with enter hooks in attachment order: the burning-oil pool (when the party drops oil with `DropItems` and then lights it with `LightSource`, the engine compiles the pool from [Splash damage is two applications](#splash-damage-is-two-applications) onto the cell, and every living member entering while it burns takes its 1d8), stationary *silence* (see [Silence automates both forms](#silence-automates-both-forms-moving-on-a-failed-save-stationary-on-a-passed-one)), and *web*'s blocking cube (entering attaches the entangled escape countdown, a web cast on a location lasts the spell's duration on the cell, and the escape params are on the effect). Web flammability, *cloudkill*, and the walls stay manual. Locked by `test_exploration.py::TestLight` and `TestLocationEffects`.

### Wandering monsters

The chance gets +1 for noise since the last check, +1 for daylight-brightness light (*continual light*'s data, since the torch or lantern flame is the baseline the printed 1-in-6 already assumes), and −1 while the party hides. It clamps to [0, 6], and a chance of zero skips the roll. The modifier numbers are pinned for RAW's "referee may increase/decrease". The SRD ties the decrease to hiding, not to resting: "If the party rests quietly in an out-of-the-way location, the referee may decrease the chance of wandering monsters being encountered." A plain `Rest` therefore checks at the level's chance, with noise and light added, so wandering monsters can interrupt a rest in a dungeon. A `Rest` with `hidden` set takes the −1 on every check during the rest. The game or the referee decides whether a place is out of the way, and the session accepts the flag as given. On a level with the printed 1-in-6 chance, the session rolls no check during a hidden rest. With a higher chance, or with noise or bright light, it still rolls. `hidden` is accepted in town and changes nothing there, because no checks run in town. The cadence suspends during encounters and battles and resumes after. Wandering monsters are never surprised (they come "moving in the direction of the party") but the party may be. Locked by `test_exploration.py::TestWandering` and `test_rest_hiding.py`.

### The encounter procedure

Every encounter opens with surprise, a 2d6 × 10' distance roll, and reaction, keyed encounters included (rooms are abstracted onto the same track). The distance roll is bounded by the space it happens in: RAW rolls 2d6 × 10' only "if there is uncertainty", because "the situation in which the encounter occurs often determines how far away the monster is", and the walls around the party's cell are that situation. Pinned: a rolled distance caps at the longest unobstructed straight sight line from the party's cell, taken across the four grid directions through the edges sight crosses (open floor, open non-secret doors). On a rectangular room that is exactly the room's extent from the cell the party stands on, and down a straight corridor it is the whole passage, so the printed 20'-120' band stays fully available wherever the space affords it. The floor is one cell, 10', so a sealed cell never yields 0'. Darkness never shortens the line, since light governs surprise and not distance (a monster the party cannot see is still exactly as far away as it is), and a caller-supplied distance, which is every referee spawn, is never capped. The engine caps the rolled result without drawing from the stream, so the cap consumes no randomness.

Keyed awareness comes from the area's flag, a failed door forcing alerts the room, the lit-party rule skips the monsters' surprise roll entirely, and a successful listen marks party awareness. Parley re-rolls are uncapped, each a fresh roll with the speaker's CHA modifier. The stance map (RAW's bands assume a referee): 2- attacks now, 3-5 attacks at the end of the next encounter round absent evasion or improvement, 6-8 holds and re-rolls at +0, 9-11 passes freely, and 12+ is friendly. Offensive acts in encounter mode go through `EngageBattle`, except turn undead, which has a separate pre-battle, encounter-only command and whose use against non-undead onlookers provokes battle (pinned). Only monsters in the attacking and hostile stances pursue an evading party.

One side's surprise is one free round, however the battle starts: surprised monsters give the party a free battle round (they hold while the party acts), and a surprised party loses an encounter round, in which hostile or attacking monsters can open battle with their machine-run surprise round. An encounter consumes at least one full turn, `max(next turn boundary, start + one turn)`, which may itself end mid-turn when the encounter started mid-turn (pinned). An encounter releases `turned` effects when it ends, and every effect on the encounter's monsters releases at its end, because the fiction moves on (a dead troll's pending revival is post-battle narration). When an encounter opens, `start_encounter` attaches the regeneration effect to each monster whose template has the `regeneration` ability, so a troll heals and revives only while its encounter runs. An evaded keyed encounter re-triggers fresh on the next entry. Locked by `test_encounter.py`, the free rounds by `test_encounter.py::TestSurpriseFreeRounds`, the distance bound by `test_encounter.py::TestEncounterDistance`, and the regeneration effect by `test_monster_regeneration.py`.

### Evasion and pursuit

Pursuit compares the party's slowest running rate (full feet per round) against the pursuers' slowest base ground mode (the mode with no descriptor, else the first printed, so a flying mode doesn't count). Slowest-of-pursuers mirrors slowest-of-party, and the engine doesn't model a pack that strings out. `Evade(drop="treasure")` scatters every living member's coins and `"food"` one ration per member (pinned), and mid-pursuit `DropItems` re-baits round by round. The distraction is 3-in-6 when the bait matches: treasure for monsters whose treasure ref has letters (the intelligence proxy, override-correctable per encounter) and food for everything else. One roll stops the whole side (pack behavior). Pursuit ends on distraction success, at melee contact (gap ≤ 5', battle at 5'), or at 30 rounds, when both sides are spent and the monsters give up (the limit that ends every pursuit: with the party no faster the gap never grows, so no sight-loss distance is needed). Running exhaustion (−2 attack, damage, and AC, the AC penalty applied through `attack_penalty_of_attackers` +2) lasts until three rested turns. Locked by `test_encounter.py::TestEvasionAndPursuit`.

### The battle round

Battle declarations arrive as one command per round with one declaration per living, able member, rejected whole on any invalid declaration (partial acceptance would complicate replay). Formation width is the frontage the space around the party offers under `formation_width_limit`: the widest square of unbroken floor that includes the party's cell, at five feet of frontage each. RAW's "2–3 characters in a 10' passage" is pinned to its conservative end and then applied to whatever space the party stands in, so two fit in a ten-foot passage however long it runs, and in a room the rank is as wide as the room's shorter side. Ranks recompute from the living marching order at round start and melee reaches the front rank only, both directions, with no firing-into-melee penalty (none exists in the SRD).

The party moves as a formation and individual members cannot leave it (the Bard's Tale convention): all-retreat moves off at the full encounter rate (Combat.md's "full encounter movement rate", with the running pursuit beginning when the battle converts at round end), all-fighting-withdrawal backs off at half encounter rate, and the first `close` declaration advances the formation on its named group, stopping at 5'. A `fighting_withdrawal` or a `retreat` that not every declarer chose is therefore not a legal declaration for that member: the round is refused whole with `battle.declaration.formation_split`, one rejection per defensive move the round split on, naming the declarers who chose it and the declarers who did not. `close` is not a defensive move and needs no agreement: a lone `close` advances the formation, and a `close` declared beside a `fighting_withdrawal` or a `retreat` counts among the others that split the round, so that round is refused and nothing advances. A round whose only declarer declares a defensive move is legal, because everyone agreed. A fighting withdrawal here is a pure move and pinned as one: RAW gives the withdrawing combatant the backward move and takes nothing away, so the attack stays with them, but a member declares one thing per round and the whole formation moves together, so a round the party withdraws in is a round it does not attack in. The SRD's attack-while-withdrawing half is not represented. The move set is the SRD's two defensive moves and `close`. Schema version 4 removed a fourth value, `withdraw`, and its lossless migration clears `withdraw` off a logged declaration and leaves a member who declared it holding, as that round played out.

A party melee attack hits the first living, visible monster of the group's reachable rank (deterministic, no draw), and monsters pick uniformly on the monster-action stream. Under individual initiative the machine still resolves side blocks, ordered by the best individual total (the SRD's phase sequence is per side). Turn undead resolves in the magic phase, needs no declaration posting, and cannot be disrupted, because it is a class ability rather than a spell. The machine detects disruption per the RAW trigger (a declared caster successfully attacked or failing a save before acting) and releases concentration on any other declared action. A turning is one of those actions, so a cleric who declares one stops concentrating, even when confusion stops the turning before it resolves. A caster maintains one concentration spell at a time: casting a second ends the first, and a spell that needs no concentration leaves it alone (#167). Locked by `test_rules_calls.py::TestTurningEndsConcentration` and `test_rules_calls.py::TestOneConcentrationSpellAtATime`.

An `attack` declaration that names no weapon attacks with the wielded weapon that suits the attack (#162). The SRD decides melee or missile by distance alone ("Melee: Close quarters weapon (5' or less)" and "Missile: Thrown or fired weapon (greater than 5' distance)"), so the group's distance when the round is accepted decides it: a melee attack at 5' or less, a missile attack beyond. The session takes the first wielded item, in the order the member equipped them, with the `melee` or `missile` quality. For a missile attack it prefers the first whose long range reaches the group, and when none reaches it takes the first missile weapon, which the session then refuses with `combat.attack.out_of_range`. A torch counts, because the weapon table gives it the melee quality. A splash weapon never counts, because a `use_item` declaration throws it, and a magic item with no base weapon, like a wand, has no weapon stats. A member with nothing that suits the attack strikes unarmed. The choice is made once, when the round is accepted, and the round then plays out exactly as if the player had named that weapon, even when the distance changes before the attack resolves. The command log keeps the declaration as it was sent. Locked by `test_weapon_default_and_device_recheck.py`.

A declaration is judged twice: once when the round is accepted, and again in the magic phase immediately before it resolves, with every check it passed the first time, because the phases between the two can change what those checks read. Failing any one of them fizzles the declaration, whatever the round changed to cause the failure. Two cases that occur are an ally's *silence 15' radius* anchoring on the party's cell earlier in the same magic phase, which refuses a cast with `magic.cast.silenced_area`, and the party's last torch going out as its bearer dies in the missile phase, which refuses a scroll read with `exploration.action.requires_light`. A cast or scroll read the re-check refuses is not cast and never raises out of the round: the caster loses the memorized copy, or the scroll its spell, exactly as a disruption loses it, and the round reports it with `magic.cast.fizzled` on `SpellDisruptedEvent`, whose `reason` field contains the first rejection code the re-check produced. A caster the round already disrupted reports `magic.cast.disrupted` instead, because that check runs first. A wand, staff, or rod use and a turning get the same re-check (#145). A staff of striking aimed at a group that ran out of melee reach fizzles with `combat.attack.out_of_reach`, and a wand aimed at a group that fled the battle fizzles with `battle.declaration.unknown_group`. One that fizzles is reported by `DeclarationFizzledEvent`, at `magic.device.fizzled` or `magic.turning.fizzled`, with the same `reason` field. `SpellDisruptedEvent` stays for spells and scrolls, so its `spell_id` always names a spell. A fizzled device use costs what a `hold` costs: the device was never used, so it keeps its charge, isn't identified, and breaks no invisibility. A fizzled turning draws nothing and costs nothing. Each battle round advances the clock one round through the ledger. Locked by `test_battle.py`, the split refusal by `test_battle.py::TestAFormationMoveNeedsEveryDeclarer`, and the re-check by `test_battle.py::TestTheMagicPhaseRechecksADeclaration` and, for devices and turnings, `test_weapon_default_and_device_recheck.py`.

### Spell effect consumption in battle

*Haste* resolves the bearer's attack declaration twice and doubles track moves (the party's consolidated move doubles when every living member bears the multiplier, pinned). Invisible combatants are untargetable, and a rejection here leaks nothing, since the player already knows the target is invisible, and the machine releases the effect when the bearer attacks or turns undead. Mirror images pop one per incoming attack with no attack roll. Confusion overrides declarations with its 2d6 behavior roll on the combat stream (a forced rules roll, not a policy choice) and re-saves on the magic stream per its params, both machine-run round rolls rather than ledger ticks, so the tick-time convention in [The `magic` RNG stream](#the-magic-rng-stream-and-the-attach-timetick-time-split) is unchanged. `entangled` blocks declared and policy moves while an entangled front-ranker still fights, and a `weakened` creature cannot attack or cast (the kernel validators reject those actions, and the machine reports the rejections). The *protection from evil* melee ban: a monster whose template bears a warded category may not initiate melee against a warded target of differing alignment, so the default policy's target pick skips them while missiles and breath land, and the ban breaks for a target who has engaged the barred creature in melee, the ±1 modifiers persisting (RAW's clause). Locked by `test_battle.py::TestEffectConsumption`.

### The area footprint

An area effect targets one group, and its capacity in creatures is `ceil(span / 10) × width`, where span is the diameter (radius shapes), length (lines, cubes, clouds), or reach-limited length (cones: length minus the gap), and width is the formation width (unbounded with the flag off). Candidates fill in stable spawn order up to capacity. Under `aoe_friendly_fire`, an area landing on a group at melee range appends the engaged party front rank after the monsters, in marching order, consuming remaining capacity. Breath weapons against the party cover `ceil(reach-limited span / 10)` ranks from the front, and single-target breath (the hellhound) picks uniformly from the front rank on the monster-action stream. Locked by `test_battle.py::TestAreaFootprint`.

An HD-budget spell needs no footprint to aim at a group. A `cast` in an `hd_budget` mode with a `target_group_id` and no `targets` targets every living member of that group, in spawn order, and the targeting mode spends the budget on them weakest first. That's the same cast as naming each of those members in `targets`. The player view shows group ids and no monster ids, so a front end built on it casts *sleep* in battle this way. The SRD's *sleep* page gives the spell a range and a Hit Dice budget but no area, so the session doesn't cap the candidates with a footprint. *Death spell* is also `hd_budget`, and the session doesn't apply its "60' cube" when the cast aims at a group either (pinned). When both are set, the session uses the named `targets`, so a recorded command log replays as it did. The session refuses a declaration that sets neither with `magic.cast.target_count`. Locked by `test_battle_player_view.py::TestAnHdBudgetSpellAimedAtAGroup`.

### The default action policy and morale

The policy draws only from the monster-action stream. Monsters with a scripted pattern in their data follow it: an `uses_per_day` breath weapon opens with breath, then breath or melee with equal chance while daily uses remain (the dragons, RAW), and the engine rolls a `per_round_chance_in_six` gate each round (the hellhound). Otherwise a group beyond 5' closes and at 5' melees. A monster that melees picks its target, then one of its attack routines, with equal chance among the routines whose `use` is `primary` or `alternative`: the thoul claws or swings a weapon, the mule kicks or bites, and the spitting cobra spits or bites. A monster with one such routine draws nothing for the pick. The policy never picks a `situational` routine, which the SRD ties to a situation osrlib doesn't model: a range, a charge or a trample chance, water, a ship, a form, or a loadout. A game's own policy names a situational routine in `MonsterAction.routine` when the game decides the situation applies, and battle rolls it as melee. So the default policy never rolls the 3-in-4 trample chance of the elephant and its kin or the cave locust's 50% jump (pinned). A `special` routine, which is a breath, a gaze, or a spell, rolls nothing even when a policy names it, and battle never rolls an entry named `gaze` or `breath` as an attack. A confused monster that attacks uses its first routine (pinned). Monster missile attacks lack structured range data and stay close-then-melee, so the spitting cobra spits from 5' (pinned). A thrown missile in a later routine, like a giant's boulder, is a situational routine. Groups never cast (casting tags are `manual` data). Morale is auto-invoked at the start of each monster block through a per-battle tracker. The triggers are first death and half incapacitated. A monster's alternate morale score applies when the round meets its condition (fear of fire when the round's damage included fire), and the spell morale modifier applies per the cumulative-effects rule. ML 2 groups rout when battle starts, or as they join a battle already running (none exist in the compiled data, so both the battle-start branch and the join branch are test-forced). A failed check means retreat exposure that round, then flight at full speed, leaving the battle past 120' (the pursuit sight figure, symmetric) as routed. A fled group stays in the player view's `groups`, in order, with `fled` set. The session refuses every declaration aimed at it with `battle.declaration.unknown_group`: an attack, a `close` move, a thrown item, a device, or a spell. The party can still declare against a group that is running but still inside 120', because it can chase the group or shoot at it. A cast aimed at a group that flees earlier in the same round fizzles at the magic phase's re-check (see [The battle round](#the-battle-round)). Chasing routed monsters is left to the game (an evasion procedure with roles swapped, deferred). A group whose living members are all turned or afraid retreats as a whole, and individually turned members of a mixed group don't act (pinned). Locked by `test_battle.py::TestDefaultPolicy` and `TestMoraleAndEnds`, for the routine pick by `test_attack_routines.py`, for a group that joins by `test_spawn_into_encounter.py::TestAGroupThatWillNotFight`, and for a fled group by `test_battle_player_view.py::TestAFledGroup`.

### Battle applies five kinds of monster attack effect and the petrifying gaze, and leaves the rest to the referee

When a monster's attack hits in battle, the session applies each effect the attack lists if the effect is `paralysis`, `poison`, `disease`, `energy_drain`, or `petrification` and the monster's ability for it isn't `manual`. The effects apply once for each hit, so a target hit by two paralysing claws saves twice. The target saves versus paralysis against paralysis and petrification, and versus death, with the ability's modifier, against poison. A failed save against poison kills at once, or when the poison's onset runs out. Mummy rot and energy drain allow no save. An elf, whose class has `ghoul_paralysis_immunity`, rolls no save against the ghoul's or the thoul's paralysis and isn't paralysed. osrlib has no model of size, so the same paralysis affects a creature larger than an ogre, which the SRD exempts. A dead or petrified target takes none of these effects.

The session runs the basilisk's and the medusa's petrifying gaze through `resolve_gaze` once a round while the gazer's group is at 5', before the group acts. The gaze reaches the party's front rank and takes no attack roll. The session never rolls the basilisk's `gaze` attack entry as an attack. `resolve_gaze` spares a creature with the `averted_eyes` condition, but no command gives a member that condition, so in play every member in the front rank meets the gaze. The SRD's penalties for fighting with averted eyes (−4 to the member's attacks and +2 to the basilisk's) aren't applied. A gazer that surprises the party from beyond 5' doesn't gaze in its free round, though the SRD says characters a basilisk surprises meet its gaze. Mirrors and the medusa's disguise are left to the referee.

An effect in a later attack routine applies when battle resolves that routine. The spitting cobra's poison bite is an alternative routine, so under the default policy the cobra bites in about half the rounds it fights. The vampire's charming gaze is a special routine, which battle never rolls, so the referee applies it.

The session doesn't apply these attack effects and gazes, so the referee applies them from the monster's description:

- `1d10_heat`: bronze golem.
- `1d10_hull_damage`: lesser sea serpent, giant squid.
- `2_hull_damage`: giant squid.
- `6d6_hull_damage`: sperm whale.
- `blindness`: spitting cobra.
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

Character ids assign as `character-NNNN` from the session allocator in party order. Dead members stay in the party but count for nothing. The session records each character death's clock round and cause (`poison` when the killing resolution was a poison save or poison-delay expiry, else the source kind), feeding *raise dead*'s day count always and *neutralize poison*'s round window only for poison deaths, so a session supplies the values that a direct kernel caller has to attest. Session flags are referee-only (how the game's content uses them is secret) and neither view includes the seed, which lives only in the save. The player view is the enumerated whitelist locked by the leak property test, with undiscovered secret doors rendering as walls. `SetDoorState` emits referee-visibility door events, and `PlaceParty` triggers no hooks, traps, or keyed encounters, because it is a referee tool. Referee `AdvanceTime` runs full bookkeeping but no wandering cadence, since the referee controls encounters. `SpawnMonsters` rolls its count dice on the encounter stream and opens a standard encounter with both sides rolling surprise, in a dungeon only (the combat space needs a party cell). While an encounter or battle is already running, `SpawnMonsters` adds one group to that encounter instead, which is how a referee brings in the allies an SRD monster summons. The group rolls no surprise or reaction and shares the encounter's stance and surprise. The spawn costs no time, so the group first acts in the next round. During a pursuit the session refuses the spawn with `encounter.spawn.mid_pursuit`, because the chase runs at the pace of its slowest group. `SpawnNpcParty` takes an exact band size in `count_fixed`, which draws nothing, or rolls the size from `count_dice` on the encounter stream, or rolls the B/X number for its kind when neither is set. Setting both fails validation, as on `SpawnMonsters` (#167). The session rejects `SpawnNpcParty` and `PlaceParty` mid-encounter. Locked by `test_session.py`, `test_crawl_properties.py`, `test_spawn_into_encounter.py`, and `test_rules_calls.py::TestSpawnNpcPartyTakesAFixedSize`.

### Persistence and replay

Saves are self-contained (adventure content embedded) with the accepted-command log always and the event log optional, and the event log preserves unknown event types losslessly (the log is a record and is never re-derived). Replay under a different engine version raises `ReplayVersionError`, while loading a save across engine versions remains legal. A synthetic in-test migration at schema version 1 exercises the migration framework. The standing test: `load(save)` equals `replay(seed, commands)` for the delve golden at every checkpoint. Locked by `test_persistence.py` and `test_phase4_goldens.py`.

### The treasure compiler: entry grammar, magic clauses, and TreasureRef semantics

Treasure-type entries parse on a fixed grammar: an optional `NN%:` presence gate, quantity dice with multipliers and glued coin suffixes, gem and jewellery counts, and structured magic clauses. A clause's parts are `any`, a named category, or a pool, a count or count dice, exclusions, and `+ 1 potion`-style extras. Pools pick uniformly among their categories before the sub-table roll, and an exclusion re-rolls exactly the named master-table category with draws consumed (the wandering-re-roll precedent). `TreasureRef` letters resolve by SRD section, and parenthetical letters are lair treasure. `extra_gp` joins the hoard, `multiplier` repeats the whole roll, `special` and `see_below` generate nothing, and the small-lair reduction stays prose for the referee. The engine rolls the d20 value table per gem and 3d6 × 100 gp per piece of jewellery, and both values fix at generation, with appraisal instantaneous and exact (B/X prices treasure for the XP economy, and a haggling or appraisal minigame is left to the game). Locked by `test_treasure_data.py` and `test_treasure_kernel.py`.

### Treasure generation order, tiers, and the treasure stream

Generation follows printed-entry order, depth-first per item: each gem's value, each jewellery's value, and each magic item fully resolved (sub-table, armour-type d8 for generic armour, charges at creation, ammunition quantities, scroll contents, and sword sentience last, with the special-purpose 1-in-20 first and then the printed procedure) before the next entry, all on the `"treasure"` stream, so draws are reproducible from the stream alone. Tier selects the printed B/X sub-table columns from the party's highest living level, with 4+ as expert. À la carte callers choose explicitly and pass a stream and an allocator. Locked by `test_treasure_kernel.py`, `test_magic_items.py`, and the phase 5 hoard golden.

### Hoards generate lazily, appear as engine-created caches, and are untrapped

A keyed encounter's lair hoard generates once, when the group first spawns, and appears as an engine-created cache on the area's first listed cell in the dungeon-state overlay, so authored specs stay frozen, while piles and features gain valuable and magic-item fields that default to empty. Carried treasure generates at spawn (one keyed group's draw order: carried treasure per printed line, then the lair hoard), and `SpawnMonsters` generates none, because the referee grants carried loot explicitly. Area treasure specs generate on first entry, and unguarded treasure uses the Designing-a-Dungeon bands. Generated hoards are untrapped, because trapping treasure is authored content rather than a generation outcome. When the party evades a keyed encounter and comes back, the monsters spawn again, but the session records the area in `DungeonState.generated_lair_hoards` when it rolls the hoard and doesn't roll it again. The party finds the first cache, or nothing if it took that cache. Locked by `test_exploration.py::TestTreasure`, `test_loot_and_traps.py::TestLairHoardOnce`, and the milestone golden.

### Loot drops: slain sides drop, routed sides keep

At battle's end, slain groups drop their carried treasure and group bundles into a pile on the party's cell, while a routed (fled) group keeps everything and leaves with it. Defeated NPC adventurers dump their kit and share of the group's U + V bundle the same way. Locked by `test_encounter.py` and the milestone golden.

### The end-of-adventure award is the departure-snapshot valuation delta

`EnterDungeon` snapshots the party's valuation (coins plus carried valuables at exact value, magic and mundane gear at zero) and `TravelToTown` awards the delta: floored once from copper, clamped at zero, added to the defeated-monster ledger's XP, divided evenly among living members with floor division and the remainder dropped, applied through `apply_xp` (class modifiers and the one-level clamp apply per member). Dead members' carried treasure counts toward the delta but draws no share, and a party with no survivors gets no award. The award clears the defeated-monsters ledger and the snapshot. There's no recovered-treasure ledger: schema version 2 removed it, and the migration drops the field from version-1 saves. Selling in town after the award changes nothing. Locked by `test_award.py` and `test_persistence.py`.

### Treasure weight covers coins, valuables, and the five priced magic categories

`treasure_weight_coins` counts the purse, valuables, and the magic items the `TreasureWeight` rows price (potion, scroll, rod, staff, wand). Rings and miscellaneous items weigh zero absent a page figure, and the Bag of Holding's printed 600-coin loaded weight counts while it contains anything. Enchanted weapons and armour weigh as equipment beside their mundane bases, at base weight with armour halved per RAW, rather than as treasure, so basic encumbrance's treasure tracking stays accurate. The base is the instance's `base_item_id`, or the template's when the instance has none. A spent stack, a magic item at quantity 0, weighs nothing, because the character no longer carries it. Locked by `test_items.py` and the closed-form property in `test_crawl_properties.py`.

### The magic item catalog: 164 templates, automated mechanics, and manual prose

The compiler builds every item on the eight printed sub-tables (both B and X probability columns, with the sparse basic column compiled as printed), with hand-curated mechanics in `_MAGIC_ITEM_MECHANICS` (as spells have in `_SPELL_MECHANICS`), and every other item compiles as prose tagged `manual`. Versus clauses compile as structured category and tag references (lycanthropes and undead as category tags, spell users and regenerating creatures as ability-derived template sets), never string-matched prose. The girdle of giant strength is the one automated item whose mechanics branch on a `Ruleset` flag (`variable_weapon_damage`), named as such in the tests. The regeneration ring gates `while_alive`, the displacer cloak penalizes melee attackers only, and the energy-drain sword drains automatically on every hit at the `level_minimum` XP policy (pinned CRPG default over RAW's "may command"). Truncated SRD text completes via `overrides/magic_items.json` with provenance (`helm_of_telepathy`). Locked by `test_srd_data.py` and the `test_magic_items.py` census.

### Magic items enter play unidentified, identify on first meaningful use, and curses stick

Instances mask behind category display names (an enchanted arm is "a {base} with a faint aura", a miscellaneous item "a curious device") until identified: first meaningful use, the referee's `IdentifyItem`, a worn item's effect attaching, or being attacked while wearing enchanted defenses (the bonus shows itself in battle). A cursed item reveals at the same trigger and sticks: the engine rejects `UnequipItem` for a cursed sword, weapon, armour, shield, or ring alike (`items.curse.stuck`) until *remove curse*. A cursed ring reveals the moment it is equipped, because its onset is automatic. Charges are rolled at creation and referee-only forever after (RAW "undiscoverable"): the player view never shows a charge count, a potion's remaining duration, per-item state, sentience, or a hoard not yet found. Locked by `test_magic_items.py`, `test_exploration.py`, and the leak property test.

### Item bonuses use a separate channel, outside the cumulative caps

Equipped-item bonuses (enchanted arms, rings of protection, the displacer cloak) compute from equipped inventory at query time, never as ledger effects, so unequipping is instant and nothing needs dispelling. Item-sourced ledger effects (potion durations, wards, the weakness onset) attach with `from_item=True`, are undispellable, and sit outside the cumulative largest-bonus-plus-largest-penalty cap, which applies among spell-kind effects only: `modifier_total` is the capped spell contribution plus the summed item contribution. `strength_set`, `ac_bonus`, and the damage-multiplier kinds join `MODIFIER_KINDS`. Locked by `test_effects.py`, `test_combat.py`, and `test_magic_items.py`.

### Potions: hidden 1d6+6-turn durations, mixing, and the weekly inversion

A drunk potion runs 1d6 + 6 turns, rolled on the effects stream and hidden from the player. Drinking while another potion runs cancels both and disables potions for 3 turns, except for instantaneous or permanent forms. Invulnerability inverts on the clock's day: used more than once in a week, it applies −2 instead of +2, tracked per character. Locked by `test_exploration.py::TestUseItem`.

### Scrolls: per-spell burn, minimum caster level, the thief fizzle, wards, and curses

Spell scrolls burn per spell cast. They are validated and resolved alike at the minimum class level able to cast the spell per the compiled progression (a `_ScrollReader` proxy, since the character is never mutated), so the scroll's caster level settles how many targets a mode demands and how far it reaches. They also waive the arcane magical-script reading gate (pinned: the scroll itself is the medium). A 10th-level thief reads arcane scrolls with the printed 10% error chance, resolved as a fizzle: the spell burns and nothing resolves. A cursed scroll triggers on reading and rolls uniformly among the six compiled curses (energy drain and slow healing automated, the rest manual): energy drain resolves through `drain_levels` at the `halfway` XP policy (the curse's wording), and slow healing doubles the natural rest-day cadence while halving healing spells, floored. The curse halves healing where *cause disease* blocks it, so the curse is no disease and attaches no condition. Protection scrolls attach one party-wide ward effect to the reader with per-HD-band counts as structured params (`affected_1_3`, `affected_4_5`, and `affected_6_plus`), and the ward bars the listed creatures from melee (the *protection from evil* machinery) and breaks for the whole party when any member melees a barred monster. Locked by `test_casting.py`, `test_exploration.py`, and `test_battle.py`.

### Devices spend a charge and a round, exhaust to inert, and never recharge

Wand, staff, and rod activations spend one charge and the user's action, resolve in the magic phase through the kernel damage/save path with the item's printed area shape under [the area footprint](#the-area-footprint) rule, and an exhausted device goes inert, so further activation is a rejection (`items.device.inert`), never a silent no-op. Devices never recharge and never show charges. Device areas are destructive, like *lightning bolt*: a kill destroys the victim's equipment. Locked by `test_battle.py` and `test_exploration.py`.

### Rings cap at two, worn on hands

`MAX_RINGS_WORN = 2` (RAW: one on each hand), the third rejected at equip validation (`items.ring.hands_full`). The ring of protection's 5'-radius variant extends its AC bonus to the wearer's rank-mates against attacks in battle, because the battle space has no finer adjacency than the rank and the printed example ("two characters fighting beside the wearer") is exactly melee defense. The aura arrives as caller-asserted context (`AttackContext.defender_ally_ac_bonus`), never stacks across rings, and the wearer's +1 goes through the equipped-item channel as usual. Saving throws and out-of-battle resolution honor the wearer alone, pinned: saves resolve inside the character-scoped kernel, and outside battle the party has no spatial model. Locked by `test_items.py` and `test_magic_items.py`.

### The item streams

The item and treasure stream keys are `"treasure"` (every generation draw: presence gates, quantity dice, values, sub-table rolls, charges, sentience) and `"npc_party"` (NPC composition: alignment, class rows, level dice, scores, hit dice, spell picks). Use-time item resolution (device damage and saves, scroll-cast targeting, the potion-effect dice) draws from the `"magic"` stream beside spells. Attach-time rolled durations and the cursed-scroll curse pick stay on `"effects"` per [The combat kernel streams](#the-combat-kernel-streams), and energy-drain sword drains and the drain curse resolve on `"advancement"` beside `drain_levels`. Locked by the phase 5 goldens and `test_treasure_kernel.py`.

### NPC adventuring parties: generation

One d6 alignment roll for the whole party, then per member on the npc-party stream: the d8 class-and-level row, the level dice by kind (basic 1d3, expert per row, demi-human caps asserted at compile), 3d6-in-order scores with class ability requirements skipped (the printed d8 row is authoritative, and a requirement re-roll would contradict it), the first-level hit die and one `level_up` roll per level above first, and uniform spell-slot picks (books match memorization for arcane casters). Members wear pinned class kits, the SRD's "Basic equipment appropriate to their class" made concrete, with plate upgrades for expert clerics, dwarves, and fighters. The dwarf's battle axe is two-handed, so the kit shield is carried rather than wielded. The kit's generic Axe and Bow resolve to `battle_axe` and `long_bow`. Expert members roll Expert magic items at 5% per level per suitable sub-table (basic parties take none, per RAW's Expert-only note), unusable rolls ignored and better arms equipped, and the group shares one U + V treasure bundle on the treasure stream. Locked by `test_npc.py` and the NPC-party golden.

### NPC parties in play: encounter sides like any other

`SpawnNpcParty` and wandering `npc_party` rows field a real generated party as an encounter side (count dice stay on the wandering stream), and `EncounterGroup.monster_ids` contains combatant ids spanning monsters and NPCs. The roster event is referee-visibility, so players learn compositions by fighting. NPC groups check morale at the Veteran's ML 9, are always intelligent for distraction, and are worth level-as-HD XP recorded under `npc:<class_id>`. The default `NpcPartyPolicy` heals a below-half ally with the highest cure available, drinks a healing potion when no cure remains, casts automated attack spells highest-level-first, shoots beyond 5', and melees otherwise. NPC casts declare at the round's top and disrupt exactly like the party's. Locked by `test_npc.py` and `test_battle.py`.

### Town services: full-value selling and the temple's six services

`SellTreasure` pays a valuable's exact rolled value to its carrier, because appraisal is exact (see [the treasure compiler](#the-treasure-compiler-entry-grammar-magic-clauses-and-treasureref-semantics)). Magic items have no fixed sale value and refuse (`town.sell.no_fixed_value`), because pricing one is left to the game. `PurchaseHealing` offers six services at invented, registered prices (cure light wounds 25 gp, cure serious wounds 100, cure disease 150, neutralize poison 150, remove curse 200, raise dead 1,500), each resolved by casting the real spell through the kernel at the minimum-able cleric level, honoring the death-record windows (raise dead's day limit, the 14-day weakness). Remove curse also unsticks cursed equipment. The temple charges the party rather than the patient: the fee is drawn from the treated member's purse first and then from the other members' purses in marching order, dead members included. Each purse pays in whole gold pieces: as much of what is still owed as its total value covers, rounded down to a whole gold piece. A purse that can't cover the rest pays that amount and keeps any value below a gold piece, and the last purse charged pays the outstanding remainder alone. What the party can spend is therefore the whole gold pieces in its purses rather than their total worth, and a party whose whole gold pieces fall short is refused with `items.purchase.insufficient_funds` and keeps every coin: two members holding 12 gp and 5 sp each are worth the 25 gp fee between them and still cannot buy the service. Raise dead is why the party pays. The patient is a corpse, no rule gives a corpse coin, and a party that splits its treasure never holds 1,500 gp in one purse, so the documented revival flow (game over, `PlaceParty` to town, raise dead) works only if the party's purses can pay together. Every service works in any town, whatever its `services` list says (town size and cleric rosters are left to the game). The temple refuses a service that would do nothing, with `town.healing.no_effect` (#167): raise dead on a living member, cure disease on a member who isn't diseased, and neutralize poison on a member who is neither poisoned nor dead of poison within the spell's 10-round window. The funds check runs first, so a party that can't pay gets `items.purchase.insufficient_funds`, not `town.healing.no_effect`. Like every refusal, this one takes no coin, no draw, and no time. Locked by `test_award.py::TestTownServices`, `test_award.py::TestPooledHealingFees`, and `test_rules_calls.py::TestTempleRefusesAUselessService`.

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

The SRD's referee-optional save for a dead character's magic items, given a default: when a kill destroys equipment (lightning, disintegration), the engine rolls each carried magic item against the owner's save value for the source's category (breath as breath, spells as spells, devices as wands, anything else as death), plus the item's best combat bonus, the highest of its attack, damage, and AC bonuses, while a cursed item saves at its penalty. Items that survive go to the drop pile instead of vanishing, and `EquipmentDestroyedEvent.saved_items` lists them. When the victim is a party member, the session moves the items to the drop pile on the party's cell in the command that killed the member, because no command can take anything from a dead member's inventory. Off means items share their owner's fate. Locked by `test_combat.py::TestDestroyEquipment` and `test_loot_and_traps.py::TestBreathSavedItems`.

### `xp_award_timing` (default `on_return`)

`on_return` is RAW: monsters and treasure pay out at the adventure's end, the departure-snapshot valuation delta divided among the survivors. `immediate` is the CRPG convention offered as an adaptation: monster XP at encounter end and treasure XP at acquisition (take, quest grant), each divided among the living at that moment, and drops never refund, so the return then awards nothing. Neither timing double-pays. Locked by `test_award.py::TestImmediateTiming`.
