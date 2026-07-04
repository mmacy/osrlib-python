# Adaptations and pinned interpretations

osrlib follows OSE rules-as-written. This file is the single register for the places
where that isn't enough: **pinned interpretations**, where the SRD text is ambiguous
and the library commits to one reading, and **documented adaptations**, where the
tabletop game assumes a human referee and the library provides a default behind a
`Ruleset` flag. Narrators and reviewers should look here instead of grepping
docstrings; every entry is also stated in the owning docstring and locked by a test.

## Pinned interpretations

### Multi-prime-requisite XP tiers carry no penalties

The SRD applies the standard XP-modifier table "to characters with a single prime
requisite", and the elf's and halfling's class descriptions note only bonuses (+5%,
+10%). Pinned: elf and halfling get exactly their stated bonus tiers and no penalty
tiers — an elf with INT 9 and STR 9 has a 0% modifier, never −10%. Locked by
`test_classes.py::TestXpModifier::test_multi_prime_requisite_classes_have_no_penalties`.

### XP tiers evaluate best-first, first-match-wins

The XP-modifier tiers are one uniform representation for all classes: ordered tiers of
`{modifier_pct, minimum scores}`, evaluated best-first, first tier whose minimums all
hold wins. The standard table's penalty rows only work under first-match-wins (a prime
requisite of 7 must fall past the +10/+5/none tiers and land on −10%), and the
halfling's "at least 13 in one prime requisite" +5% tier is expressed as two
single-minimum tiers under the same rule. Locked by
`test_classes.py::TestXpModifier`.

### XP percentage results floor

The SRD never says how to round a ±5/10/20% XP modifier. Pinned: the modified award is
floored (integer arithmetic, toward negative infinity). Locked by
`test_classes.py::TestApplyXp::test_modifier_floors`.

### First-level HP re-roll repeats until the die shows 3 or more

The optional rule says a roll of "1 or 2" may be re-rolled but not whether a re-rolled
1–2 is re-rolled again. Pinned: with `hp_reroll_at_first_level` on, the raw die
(before the CON modifier) is re-rolled *while* it shows 1–2, each re-roll consuming a
draw. Locked by `test_character.py::TestRollHitPoints`.

### Adjustment reductions are even per lowered score

"For every two points by which an ability score is reduced, one point may be added" —
the SRD doesn't say whether a single score may be lowered by an odd amount as long as
the total is even. Pinned: the two-for-one trade is per score, so each lowered score
drops by an even amount and nothing is stranded. The total raise must equal exactly
the sum of reductions divided by two. Locked by
`test_abilities.py::TestAdjustment`.

### Gear combat facets are exempt from class weapon policies

Torch, holy water, and burning oil appear on both the weapon table and the gear list;
they compile as gear with an embedded combat facet (one entry per physical item, no
item has two ids). Class weapon policies govern the weapons list only: a strict
quality-tag reading would forbid a cleric holy water or a torch, which is absurd. One
uniform rule: any class may buy, hold, and use all three. This deliberately
over-grants relative to a strict reading — a magic-user (RAW: dagger only) may also
throw oil or swing a torch — a consequence chosen, not overlooked. Locked by
`test_items.py::TestEquipLegality`.

### Basic encumbrance tracks treasure weight against the general 1,600-coin cap

The SRD's maximum load rule ("The maximum load any character can carry is 1,600 coins
of weight") sits in the general encumbrance text, and basic encumbrance says "The
weight of treasure carried is tracked to make sure that the character's maximum load
is not exceeded." Pinned: the cap is general, not a detailed-mode extra — under both
tracking modes, tracked weight above 1,600 coins means the character cannot move
(movement 0). Basic mode's "carrying a significant amount of treasure" stays a referee
judgment: a plain `carrying_treasure` boolean the game sets, no invented threshold.
Locked by `test_items.py::TestMovementRates` and the encumbrance property tests.

### Miscellaneous gear is a flat 80 coins under detailed encumbrance

The SRD gives adventuring gear no per-item weights and says gear "may be counted as 80
coins of weight". Pinned: any miscellaneous gear carried adds a flat 80 coins, once,
regardless of how much gear it is. Items with a listed weight of `-` (holy water,
burning oil) have no tracked weight of their own — they are gear, covered by the
flat 80. Locked by `test_items.py::TestWeights`.

### Ammunition weighs 0

The ammunition table has no weight column, and the SRD states "The listed weight of
missile weapons already includes the weight of the ammunition and its container."
Pinned: ammunition compiles with weight 0. Locked by
`test_srd_data.py::TestEquipmentData`.

### Sling stones cost 0, lot size 1

The sling stones cost cell reads `Free`. Pinned: cost 0, purchase lot 1. Locked by
`test_srd_data.py::TestEquipmentData`.

### INT-granted languages come from the Other Languages table and may not duplicate natives

The SRD says high-INT characters "may also choose additional languages from the list
of languages available in the setting" (at the referee's discretion). Pinned: choices
come from the twenty Other Languages, and a choice may not duplicate a class native —
a dwarf cannot spend an INT language on Dwarvish. Locked by
`test_character.py::TestExtraLanguages`.

### The attack matrix is clamp(THAC0 − AC, 2, 20) and extends beyond the printed columns

Every printed matrix cell equals `clamp(THAC0 − AC, 2, 20)` — locked as a property
over the shipped table — and AC values outside the printed −3..9 columns extend by
the same formula: the printed bounds are page layout, not a rules cliff. The clamping
is exactly what distinguishes matrix mode from the `thac0_arithmetic` flag once
modifiers push totals past the plateaus. Locked by
`test_monster_data.py::TestCombatTables` and
`test_combat.py::TestAttackRoll::test_matrix_versus_arithmetic_divergence_at_the_plateaus`.

### Initiative ties re-roll

RAW offers "re-roll or resolve simultaneously"; simultaneous resolution is a
different combat model the engine doesn't attempt. Pinned: tied sides — and tied
individuals among themselves — re-roll in stable input order until distinct, each
re-roll consuming draws. Locked by `test_combat.py::TestInitiative`.

### Slow actors act after all non-slow actors

Pinned reading of "always act last, as if they had lost initiative" for the general
case: slow-weapon actors act after every non-slow actor, ordered among themselves by
their side's initiative (their own results under individual initiative), then stable
order. Locked by `test_combat.py::TestInitiative::test_slow_actors_act_last_by_side_initiative`.

### Helpless defenders are hit automatically in melee, consuming no attack draw

Paralysed and sleeping defenders are hit automatically in melee — no roll is
consumed, damage only, per RAW. A `No hit roll required` defender (green slime,
yellow mould) is likewise hit without a roll. Locked by
`test_combat.py::TestAttackRoll::test_helpless_defender_auto_hit_consumes_no_draw`.

### Morale "incapacitated" means dead, paralysed, petrified, or asleep

The RAW trigger text says "slain, paralysed, etc". Pinned for the half-the-side
trigger and the sleeping dies-to-a-blade hook. Situational morale adjustments clamp
to ±2 per RAW and never apply to ML 2 (never fights) or ML 12 (never checks); two
passed checks mean no further checks. Locked by `test_combat.py::TestMorale`.

### Energy drain reverses level_up

Per level drained, mirroring `level_up` exactly in reverse: above name level subtract
the flat-bonus delta (no roll, no CON); otherwise roll the class hit die plus the CON
modifier (minimum 1 per die) and subtract it — rolling the lost die is the
RAW-faithful reading of "loses one Hit Die of hit points" that keeps the model
stateless, with no per-level HP history field. Drain never reduces max or current HP
below 1 while the character retains a level; death by drain happens only by losing
the last level. XP policy is per-monster data: the wight sets XP to the floored
midpoint of the former and new levels' thresholds; wraith, spectre, and vampire set
it to the new level's threshold exactly. Two-level drains apply the procedure twice
and set XP once. On the terminal drain the killing level counts in `levels_lost` (a
level-1 victim loses 1 level; a spectre draining a level-2 fighter reports 2).
Draining a monster loses Hit Dice symmetrically: the instance re-derives THAC0 and
saves from the reduced HD and loses a rolled d8; below 1 HD it dies. The spawn
consequence (rises as a wight in 1d4 days) is a structured-but-manual field on the
drain event — the kernel kills, the game narrates. Locked by `test_drain.py`.

### The damage pipeline order

Gate → roll (+ STR for melee) → doublings (brace, charge, back-stab) → minimum 1 →
reductions (floored, never below 1). If the defender's `harmed_only_by` or energy
defenses exclude the source, no damage is rolled and the event says so. Save-for-half
halving also floors. Back-stab adds +4 to hit and doubles damage, both halves from
the thief tag's params, when the caller asserts an unaware target attacked from
behind. Locked by `test_combat.py::TestDamagePipeline`.

### The WIS save modifier applies to magical non-breath saves only

Pinned reading of "does not normally include saves against breath attacks"; referee
discretion beyond that arrives as a caller modifier. Locked by
`test_combat.py::TestSavingThrows`.

### `hd5_counts_as_magical` implements the whole invulnerabilities optional rule

The spec's flag summary names the 5+ HD half as shorthand; the flag governs both
bullets of the SRD optional rule it cites — a monster of 5+ HD *and* another
invulnerable monster bypass the gate — since implementing half an optional rule would
be an undocumented deviation. Boundary pinned to the rule's own wording: the flag
touches only weapon-material gates whose keys are a subset of {silver, magic}, and
"another invulnerable monster" means a monster bearing such a gate — element-keyed
and mixed gates (the mummy's fire-or-magic, the black pudding's fire-only) are
unaffected. Locked by `test_combat.py::TestDamagePipeline`.

### The `holy` damage key is admitted through any gate on undead targets — and only there

"Holy water inflicts damage on undead monsters": the specific rule overrides the
general immunity, otherwise the wight's silver-or-magic gate would absorb the one
weapon made for it. Against anything living, the throw resolves normally and the
damage pipeline reports no effect — deliberately *not* a validator rejection:
rejections are free (no roll, no time, no log entry), and a rejection would be a
zero-cost undead detector. Locked by `test_combat.py::TestSplashWeapons`.

### Variable weapon damage off means 1d6 for weapons and gear facets

RAW: "PC attacks inflict 1d6 damage" — weapons *and* gear combat facets. Unarmed
attacks stay 1d2 (the specific unarmed rule, not a weapon) and monster damage is
unaffected (monsters "deal the damage indicated in the description"). Locked by
`test_combat.py::TestDamagePipeline::test_variable_damage_flag_off`.

### Splash damage is two applications

"Inflicted for two rounds" is pinned as two applications: the hit's damage now, and
the douse effect's expiry applies the listed damage once more at the next round
boundary. Unlit oil deals no damage (the caller may compile it into a
location-attached pool: 3-foot pool, burns 1 turn once lit, 1d8 to creatures passing
through — who passes through is the caller's assertion until Phase 4 owns space);
`uses_fire` monsters ignore burning oil outright. Locked by
`test_combat.py::TestSplashWeapons`.

### Torch and burning oil deal fire damage; holy water carries the holy key

The three dual-listed gear items carry pinned damage-source semantics — this is what
routes a torch or oil hit into the troll's non-regenerable ledger. Locked by
`test_combat.py::TestSplashWeapons::test_douse_applies_twice_then_expires`.

### Breath-weapon daily limits are per-monster data

The three-per-day limit is per-monster data (the dragons, the dragon turtle, and the
chimera print it; the hellhound has none — its 2-in-6 per-round chance ships as data
for Phase 4's action policy, and the kernel resolves the breath whenever invoked).
Every breath weapon is a destructive death (pinned): the SRD's destruction-of-items
examples ("a lightning bolt spell or a dragon's breath") illustrate energy deaths
generally, so the hellhound's fire kill destroys the victim's mundane equipment too.
Dragons' energy immunity encodes as immune-to-nonmagical plus
auto-save-versus-magical for the variant's breath element(s). Locked by
`test_combat.py::TestBreathAndGaze`.

### The HD-budget targeting mode consumes weakest-first

Candidates sort weakest-first by effective HD — sub-1 HD rounds up to 1, fixed
hit-point bonuses are dropped, characters count their level — ties broken by stable
input order; the budget spends whole creatures, and a target whose HD exceed the
remainder is skipped while selection continues. *Sleep*'s exact arithmetic lands with
the spell in Phase 3. Locked by `test_combat.py::TestTargeting`.

### Monster XP validation: above-21-HD inflation and the negative-modifier band

Printed XP is authoritative and cross-validated at compile time: base by HD row plus
asterisks × the bonus column, where above 21 HD *both* amounts first gain 250 per HD
above 21 — the dragon turtle (HD 30*, XP 9,000) proves the reading. Negative
hit-point modifiers map to the *lower* band — the goblin's 1-1 validates against
"Less than 1" (XP 5) while keeping the unmodified "Up to 1" attack-matrix row (THAC0
19 [0]); the attack-as-1-HD-higher rule is for bonus modifiers only. Table mismatches
fail the build unless recorded as overrides. Locked by
`test_monster_data.py::TestCombatTables`.

### Poison failure is death, with optional onset; riders leave the damage unaffected

Save versus death/poison; failure is death — immediate, or an onset-delay effect for
forms like the giant rattler's "death in 1d6 turns". Poison riders on damaging
attacks leave the damage unaffected by the save, per RAW. Locked by
`test_effects.py::TestLedger::test_delayed_death_poison`.

### Petrification suspends the target's other effects

While a target is petrified its other attached effects suspend — no ticks, durations
frozen: a poisoned, petrified adventurer is a problem for after *stone to flesh*.
Petrification is permanent but recoverable — stone is not dead. Locked by
`test_effects.py::TestLedger::test_petrification_suspends_other_effects`.

### The canonical tick order

At each round boundary, expirations resolve before ticks; simultaneous effects
resolve in attachment order, tie-broken by effect id. Only the effects engine (and
the kernel's death routine, for `dead`) writes creature conditions. Locked by
`test_effects.py::TestLedger`.

### Troll damage splits into regenerable and non-regenerable ledgers

Fire and acid accrue in a separate non-regenerable ledger on the instance;
regeneration never heals that ledger, and the troll is permanently dead only when
non-regenerable damage alone reaches max HP — otherwise death at 0 hp schedules a
2d6-round revival (the effects stream), anchored to the round the killing damage
landed, with the revived troll rising at 1 hp. Locked by
`test_effects.py::TestRegeneration` and the troll battle golden.

### Mummy rot heals naturally once per ten full rest days and blocks magical healing

"Magical healing is ineffective; natural healing is ten times slower" — one 1d3
recovery per ten consecutive full rest days, tracked on the effect; removal is Phase
3 magic. `apply_healing` defaults its source to `magical` (instantaneous healing *is*
magical healing per the spec), so a caller that forgets to name the source still
respects the block. Locked by `test_effects.py::TestMummyRot`.

### Asleep grants auto-hit and dies-to-a-blade; dozing is a caller modifier

The asleep condition's combat hooks ship now (the *sleep* spell that inflicts it is
Phase 3): a sleeping defender is hit automatically in melee, and "a single attack
with a bladed weapon can kill" — the melee hit kills outright with no damage roll.
Pinned: "bladed" means a weapon (not a gear facet, a monster's natural attack, or an
unarmed strike) with the melee quality and without the blunt quality — the SRD's
blunt list exists precisely to separate crushing weapons from edged ones; the hook is
melee-use only, and the immunity gate still applies first (a sword absorbed by the
black pudding's fire-only gate kills nothing). The dragons' "may be attacked for one
round with a +2 bonus" dozing is the caller-supplied situational modifier, not a
condition. Locked by `test_combat.py::TestDamagePipeline::test_sleeping_defender_dies_to_a_blade`
and its neighbors.

### Falling damage floors per full 10 feet

1d6 per full 10 feet fallen; a 9-foot fall rolls nothing. Locked by
`test_combat.py::TestHealingAndFalling`.

### Reload is enforced from caller context

A reload-quality weapon may not fire two rounds running: under the `weapon_reload`
flag the attack validator rejects when the caller-supplied context says the weapon
fired last round. Round bookkeeping is the Phase 4 battle machine's job — the kernel
enforces the rule given honest context. Locked by `test_combat.py::TestValidateAttack`.

### Two-handed weapons and shields conflict at equip time

Wielding a two-handed weapon with a shield equipped — or equipping the second of the
pair — rejects with `items.equip.two_handed_with_shield`, pinned at equip time rather
than silently ignoring the shield at resolution. Locked by
`test_items.py::TestTwoHandedShieldConflict`.

### Monster spawn HP is minimum 1, ½ HD is 1d4, fixed forms are exact

Spawned HP is the sum of the HD count in d8s (d4 for ½ HD) plus the signed modifier,
minimum 1; `1hp` and the hydra's 8-hp-per-HD forms roll nothing. Locked by
`test_monster_data.py::TestSpawnHitPoints`.

### Compiled monster data conventions

`No hit roll required` AC means attacks auto-hit; `Varies` morale compiles to none;
packed-variant pages expand to concrete entries under enumerated ids (a frozen
template must be spawnable); `undead` auto-tags from the standard verbatim bullet
with the shadow excluded ("incorporeal (but not undead)"); `person` comes from the
`General.md` §Persons list (the SRD's own pinned default for a referee-judgment
rule); `enchanted` is hand-curated via overrides with a reason per entry; embedded XP
values are authoritative; compound alignments compile to an options model;
leader/chieftain XP compiles to structured notes with stats left as prose. Locked by
`test_monster_data.py`.

### The event discriminator is `event_type`, with outcome-bearing codes

Every kernel event class declares a single-valued `event_type` Literal (snake_case,
schema-stable, additive-only) that the `KernelEvent` union discriminates on; message
`code` stays free to be outcome-bearing from a per-class closed set
(`combat.attack.hit` / `combat.attack.missed`), so formatters key off codes while
consumers discriminate on `event_type` and skip unknown values mechanically. The
default English formatter is total: unknown codes format to the code string itself.
Locked by `test_events_kernel.py`.

### New RNG stream conventions

Phase 2's stream keys are `"combat"` (battle resolution: attacks, damage, saves,
morale, initiative), `"effects"` (effect-internal randomness: durations, onsets,
revival delays, natural-healing rolls), and `"monster_spawn"` (spawned hit points) —
scoped so a combat-rules change never shifts spawned HP or effect draws in a golden.
Locked by the Phase 2 milestone goldens.

### Spell ids are page-derived slugs, dual pages take `_c`/`_mu`, reversed forms are entry data

A spell's id is the slugified primary name with the reversed parenthetical dropped
(`cure_light_wounds`); the nine concepts printed as separate `(C)` and `(MU)` pages
compile as two entries with `_c`/`_mu` suffixes because the pairs differ mechanically
(*hold person* is 2nd-level cleric at 9 turns/180' but 3rd-level magic-user at 1 turn
per level/120'), and a reversible spell's reverse is a `ReversedForm` on its entry,
never a separate catalog entry. The *invisible stalker* page's `(MU)` marker
distinguishes the monster page, not a dual pair — its id stays plain. Locked by
`test_spell_data.py::TestCensus`.

### `Spells.md` is the single magic-rules source, and spell pages are identified by the level line

`Rules_of_Magic.md` carries identical content and is never read. Spell pages are
identified by the italic level line (`*3rd Level [Magic-User Spell](...)*`) — never by
the `**Duration:**`/`**Range:**` labels, which also appear on potion, hazard, and
water-vessel pages. Locked by the compiler census (106 pages, 34 + 72 list entries).

### Memorization is a full-replacement preparation with the timing gates deferred

`memorize_spells` models the daily preparation as a whole-list replacement. The
once-a-day/after-uninterrupted-sleep/one-hour gates are exploration procedure owned by
the Phase 4 crawl layer; standalone users call it freely. Duplicate selections are
legal per RAW. Locked by `test_memorization.py::TestMemorizeSpells`.

### Arcane casters fix normal/reversed at memorization; divine casters choose at cast

Per `Spells.md`: the arcane form "must be selected when the spell is memorized", while
divine casters reverse "when it is cast" — so divine copies never carry a reversed
flag (`magic.memorize.divine_reverses_at_cast`) and a divine reversed cast consumes
any copy of the spell. Locked by `test_memorization.py` and
`test_casting.py::TestCastingPipeline`.

### Casting consumes the first matching memorized copy, and touch attacks consume it hit or miss

Consumption takes the lowest tuple index; disruption removes a copy identically ("as
if it had been cast"). *Cause wounds* touch attacks roll a melee attack only in combat
("In combat, a melee attack roll is required" — the pinned inverse: no roll outside
combat), and the copy is spent whether or not the touch lands — nothing in RAW holds
the charge. Locked by `test_casting.py::TestCuresAndRestoration`.

### The holy symbol is not a mechanical gate on casting or turning

`Cleric.md` states "A cleric must carry a holy symbol" as a class edict alongside
deity faithfulness, not as a precondition on any procedure. The item ships in the
Phase 1 gear data; games wanting the stricter reading check inventory themselves.
Consequence chosen: a symbol-less cleric turns and casts unimpeded. Locked by
`test_turning.py::TestValidation::test_holy_symbol_is_not_a_gate`.

### Starting books hold exactly capacity; book capacity is per level; books never auto-shrink

Arcane casters begin play with as many spells as they can memorize (exactly — one
first-level spell at level 1); clerics start with nothing (their level-1 row has no
slots). `add_spell_to_book` caps the book, per spell level, at the current slot count
for that level (the RAW "exactly the number of spells that the character is capable of
memorizing", read per level). The book is a physical object and never auto-shrinks:
a drained character may hold a book over capacity and simply cannot add more. Drain
forgets excess *memorized* copies newest-first per level (RAW is silent; newest-first
is deterministic without new state). Locked by `test_memorization.py`.

### Category and immunity gates resolve as no-effect, never as rejections

Extending the Phase 2 holy-water doctrine: rejections are free and would leak hidden
state — casting *charm person* at a disguised doppelgänger or *sleep* at a wight must
not be a zero-cost detector. Casting at an ineligible target consumes the copy and
reports `magic.cast.no_effect`; immune and excluded creatures consume no HD budget
(exclusion happens inside resolution, not as a validator refusal). Locked by
`test_casting.py::TestCastingPipeline::test_no_effect_still_consumes_the_copy` and
`TestSleep`.

### Sleep's mode arithmetic

Mode 1's "a single creature with 4+1 Hit Dice" is pinned as a monster with HD count 4
and a positive fixed modifier; mode 2 ("2d8 HD of creatures of 4 HD or less") excludes
exactly those, costs each creature its HD count with fixed bonuses dropped (the page's
own "3+2 counts as 3") and sub-1 HD rounded up to 1, spends weakest-first with
stable-order ties through the Phase 2 resolver, and grants no saving throw. Locked by
`test_casting.py::TestSleep`.

### Magic missile is 1 + 2×⌊(level−1)/5⌋ missiles, resolved instantly

Pinned from "three missiles at 6th–10th level, five missiles at 11th–15th level". Each
missile takes one supplied target (repeats stack), hits unerringly with no attack roll
and no save, and resolves at cast — the printed 1-turn duration is holding prose.
Locked by `test_casting.py::TestMagicMissile`.

### Lightning bolt kills destroy equipment; disintegrate kills permanently and destroys carried gear

*Lightning bolt* is a destructive death source (closing the Phase 2 forward
reference). *Disintegrate* destroys "the material form ... instantly and permanently":
pinned as permanent death plus equipment destruction — the material form includes what
it carries. Locked by `test_casting.py::TestFireBallAndLightningBolt` and
`TestSaveOrDie`.

### Spell damage presents the `magic` key

A damage-dealing spell passes weapon-material gates whose keys include `magic`: a
wight's silver-or-magic gate admits *magic missile*, a gargoyle's magic-only gate
admits *fire ball*. Discovered in implementation (the plan was silent); without it the
one attack form made for gated undead would bounce off them. Locked by the Phase 3
spell-battle golden (three missiles damaging the wight).

### The hold/charm person gate admits any character and only `person`-category monsters

Undead are never affected (their exclusion resolves as no effect). *Hold monster* and
*charm monster* affect anything but undead; *charm monster*'s single mode takes more
than 3 HD and its group mode 3d6 creatures of 3 HD or less. Locked by
`test_casting.py::TestHoldAndCharm`.

### Charm re-saves are monthly/weekly/daily by INT, with month = 30 days

INT 3–8 saves monthly, 9–12 weekly, 13–18 daily; a month is pinned to 30 days and a
week to 7. Monsters have no INT score and default to the middle weekly band
(override-correctable per monster). The re-save is a tick-time draw on the `effects`
stream; a passed save releases the charm. Locked by
`test_casting.py::TestHoldAndCharm`.

### The cumulative-effects rule: largest bonus plus largest penalty per statistic

`Spells.md`: "Multiple spells affecting the same game statistic do not combine."
Pinned: at consultation time only the single largest bonus and single largest penalty
apply — a *bless* and a *blight* offset; two *blesses* don't stack. Spell modifiers
combine freely with non-spell sources (the RAW carve-out for magic items, Phase 5).
Spell morale modifiers ride `check_morale`'s existing modifier argument inside the
Phase 2 ±2 clamp and ML 2/12 exemptions — one uniform adjustment rule, not a second
channel. Locked by `test_casting.py::TestBuffsAndWards` and
`test_spell_properties.py`.

### Shield's AC values apply as better-of

"AC 2 [17] against missiles, AC 4 [15] against other attacks": the target's effective
AC is the better of their own and the set value, never worse — a plate-armoured target
keeps plate. Locked by `test_casting.py::TestBuffsAndWards`.

### Striking applies to weapon attacks of any kind, never unarmed, and attaches to the wielder

RAW names no weapon type, so missile weapons qualify; unarmed strikes gain nothing.
The enchantment attaches to the *wielder* because item instances carry no ids — the
corner case of handing the enchanted weapon away mid-duration is accepted. The wielder
counts as magical for weapon-material gates while it runs. Locked by
`test_casting.py::TestBuffsAndWards`.

### Protection from evil: the melee ban ships as data, and monster alignment resolves at spawn

The enchanted/constructed/summoned melee ban is structured params consumed by Phase
4's battle machine (the kernel can't see who initiates melee). The ward's ±1 applies
against creatures of another alignment, which needs a resolved alignment where
`MonsterTemplate.alignment` is multi-option: `MonsterInstance.alignment` resolves at
spawn (caller's choice, else the template's `usual`, else its sole option), and an
unresolved alignment counts as differing — the ward errs protective. Locked by
`test_casting.py::TestBuffsAndWards`.

### Small nonmagical missiles are character weapon missiles and thrown splash items

*Protection from normal missiles*' boundary, pinned from the page's own examples
("no protection is granted against hurled boulders or enchanted missiles"): missile
weapon uses and thrown splash items set the new `DamageSource.missile` flag; monster
attacks are never auto-marked, with `AttackContext.monster_missile` as the caller's
opt-in when the fiction says small missile (a hobgoblin's arrow). Locked by
`test_casting.py::TestBuffsAndWards::test_protection_from_normal_missiles_boundary`.

### All spell-attached effects are dispellable, monster effects are not, and survival is 5% per level of deficit

RAW says *dispel magic* "ends spell effects", full stop: `permanent=True` means "no
duration expiry", not "undispellable" (*continual light* and *flesh to stone* can be
dispelled). Effects attached by `cast_spell` record the caster's level; per effect,
when the recorded level exceeds the dispelling caster's, it survives on a d100 at or
under 5% per level of difference. Monster-inflicted effects (poison, rot,
regeneration) are non-dispellable by construction; magic items are exempt (Phase 5).
Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### Raise dead: 0 days at level 7, a 14-elapsed-day weakness, and permanent undead destruction

The time limit is 4 days per caster level above 7th — 0 days at level 7, RAW-faithful.
Revival sets 1 hp and attaches the weakness, pinned to 14 elapsed days as the
simplification of "two full weeks of bed rest" (rest tracking is Phase 4 procedure;
games wanting strict bed-rest semantics extend or release via the ledger). While it
runs, the subject cannot attack, cast, or turn undead (RAW bans "other class
abilities" too), moves at half rate (a Phase 4 marker param), and healing from
*every* source is blocked — RAW says the subject "has 1 hit point" until the
recovery completes and no magical healing shortens it; the hit point returns to
normal recovery when the weakness ends. Only characters are raisable (all four
Classic races are human or demihuman; monsters are not). The destroy-undead usage
kills permanently on a failed save, matching turning's `D`. Locked by
`test_casting.py::TestCuresAndRestoration::test_raise_dead_level_seven_allows_zero_days`.

### Neutralize poison's revival window is caller-attested, and only characters revive

"A **character** who has died from poisoning can be revived, if *neutralize poison*
is cast within ten rounds": the kernel has no cause-of-death model, so supplying
`CastContext.rounds_since_death` *is* the caller's attestation that the target died
of poison within that many rounds — omit it for any other death. Only a `Character`
is revivable (the page's usage is titled "Characters"); revival stands the subject up
at 1 hp (pinned — RAW names no hit point total). Locked by
`test_casting.py::TestCuresAndRestoration`.

### Silence automates both forms: moving on a failed save, stationary on a passed one

A failed save attaches `silenced` (the casting validator consumes it) and the silence
moves with the creature. On a passed save RAW leaves a *stationary* area the creature
can step out of: since Phase 4 gave location-bound effects real cells, the
save-passed outcome attaches a stationary silence effect to the target's cell —
pinned as the party's current cell, the encounter's abstract location — and
creatures in that cell cannot cast while there, battles included (the Phase 3
registered gap, closed). Locked by
`test_casting.py::TestSilenceWebDispelFeeblemind`,
`test_exploration.py::TestStationarySilence`, and
`test_battle.py::TestSilencedCellInBattle`.

### Web escape tiers key off STR, with the augmented and giant tiers caller-asserted

Normal strength escapes in 2d4 turns (rolled at attach on the effects stream);
magically augmented STR above 18 in 4 rounds and giant strength in 2 rounds, asserted
via `CastContext.strength_tiers` until such effects exist. The flammable-cube and
blocking rules stay location prose for Phase 4. Locked by
`test_casting.py::TestSilenceWebDispelFeeblemind`.

### Feeblemind targets only `arcane_magic` casters

The page names "an arcane spell caster (e.g. a magic-user or elf)": the gate is the
target bearing the `arcane_magic` class tag; anything else resolves as no effect.
Locked by `test_casting.py::TestSilenceWebDispelFeeblemind`.

### The turning column is the HD count, `2*` only at count 2, counts above 9 unturnable

A monster's column is its `hit_dice.count` as a string, except count 2 with a special
ability (asterisked HD) maps to `2*` (the table's own footnote), counts 7–9 share
`7-9`, and counts above 9 have no column — turning fails (the printed table is the
rule; the "referee may expand" footnote is game data territory). Modifiers never shift
columns (the mummy's 5+1 turns on column 5) and the asterisk matters only at count 2
(the wight's `3*` is column 3). Locked by `test_turning.py::TestColumnMapping` and the
verbatim table fidelity test.

### The turning pool spends lowest-first, stops at the first unaffordable monster, and always affects one

One 2d6 turn roll compares per candidate *type* against its cell; on any success one
2d6 HD pool follows. Eligible monsters are affected lowest-HD-first (stable input
order on ties), each costing its HD count (minimum 1, fixed bonuses dropped — the
*sleep* convention); the pool stops at the first unaffordable monster (RAW: excess Hit
Dice "are wasted", not reallocated); at least one undead is always affected on a
success, pinned to the cheapest eligible monster. `D` results are permanent
destruction; the rest gain an indefinite, non-dispellable `turned` condition the
encounter releases. Only `undead`-category monsters are candidates; non-undead resolve
as unaffected. Locked by `test_turning.py` and `test_spell_properties.py`.

### The `magic` RNG stream, and the attach-time/tick-time split

Every draw inside spell resolution — targeting dice, damage dice, touch-attack rolls,
cast-time forced saves, dispel survival rolls, and both turning rolls — comes from the
new `"magic"` stream, so a combat-rules change never shifts spell goldens and vice
versa. Effect-internal draws (rolled durations at attach, *web* escape dice, tick-time
saves such as the charm re-save) stay on the `"effects"` stream per the Phase 2
convention — which is why `cast_spell` takes both streams. Phase 3 adds no `Ruleset`
flags: every choice is rules-as-written or a pinned interpretation, never a
referee-optional variant. Locked by the Phase 3 milestone goldens.

### Encounter tables: hand-mapped ids, table counts override, variant pools

The encounter-table cell names hand-map to compiled monster ids with compile-time
resolution (the printed links are unresolvable), and the printed count dice override
the monster description's number appearing, per the SRD's own note. Packed-variant
cells (`Veteran`, `Vampire`, `Hellhound`) compile with the full id tuple and each
spawned individual picks uniformly on the wandering stream; the two hydra cells
carry `variant_dice` — the printed HD dice roll once on the wandering stream and
select the template. NPC-adventurer rows compile faithfully as structured
`npc_party` entries and re-roll at runtime until Phase 5 builds the parties, draws
consumed. The "Basic Adventures" typo and the singular "Expert Adventurer" labels
normalize via `overrides/encounter_tables.json` with provenance. Locked by
`test_encounter_data.py` and `test_exploration.py::TestWandering`.

### Encounter-table and reaction models live in core/tables.py

Pinned for layering: `osrlib/data/` loaders import their model homes and `core`
modules import the loaders, so a `crawl/` model home would give
`load_encounter_tables()` a core → data → crawl transitive import. The reaction
table clamps totals into its outer bands ("2 or less" / "12 or more") and reaction
rolls are referee-visible (players learn reactions from behavior, the morale
precedent); a dungeon level's encounter table is its number clamped into the
printed bands (1, 2, 3, 4–5, 6–7, 8+). Locked by `test_encounter_data.py` and
`test_kernel_checks.py::TestRollReaction`.

### The crawl streams

Phase 4's stream keys are `"wandering"` (the check die, the d20, count dice,
variant picks, NPC re-rolls), `"encounter"` (surprise, distance, reaction,
distraction), `"exploration"` (forcing, listening, searching, trap springs and
trap resolution, tinder, thief skills), and `"monster_action"` (every policy draw —
a substituted policy never shifts attack or damage draws). Trap resolution draws
(saves, damage, volley counts) run on the exploration stream — the procedure owns
its dice — while attach-time condition durations stay on the effects stream per the
Phase 2 convention. Locked by the Phase 4 delve golden and
`test_battle.py::TestDefaultPolicy`.

### Detection checks: precedence, zero-chance, and pick-pockets arithmetic

The detection-chance precedence is thief row, then class tag, then the universal
1-in-6 baseline — except construction tricks, dwarf-only with a **zero** baseline
(the SRD grants that perception to dwarves alone). A zero chance consumes no draw.
Pick pockets caps at 99 ("always at least a 1% chance of failure"), takes the
victim's over-5th-level penalty as a caller modifier, and a roll of more than twice
the effective chance is noticed. Locked by `test_kernel_checks.py`.

### The time-cost census and the thirds odometer

Zero time: `TurnParty`, `ReorderParty`, `OpenDoor`, `CloseDoor`, `WedgeDoor`,
`ForceDoor` (a moment of violence; the noise is the cost), `ListenAtDoor` (the
once-per-door cap is the limiter), equip/unequip, `DropItems`,
`ExtinguishSource`, `PurchaseEquipment` (town time is abstract), and all referee
commands. One round: `CastSpell` and `TurnUndead` outside battle, `LightSource`
(per tinder attempt too, RAW). One turn: `Search`, `InspectTreasure`,
`RemoveTreasureTrap` (pinned symmetric; RAW is silent), `PickLock` (pinned; RAW is
silent and the skill is delicate work), `TakeTreasure` (RAW's packing-bags turn),
`Rest(turn)`. On the odometer: `MoveParty` per cell and `UseStairs` at one
unexplored-cell cost (pinned). Stated durations: `Rest(night)` 48 turns,
`Rest(day)` 144, `PrepareSpells` 6, travel per content. Movement accrues in
thirds-of-feet — an unexplored cell 30 units, an explored cell 10, implementing the
SRD's three-times-through-familiar-areas rule exactly — and turn-costing actions
snap the clock to the next boundary, absorbing the partial move. Locked by
`test_exploration.py::TestTimeCostCensus` and `TestOdometer`.

### Rest, fatigue, sleep, and provisions

A living entangled member blocks party movement (the party doesn't abandon its own
implicitly; leaving someone is a referee-command decision). Fatigue (−1 attack, −1
damage, engine-written) attaches after six consecutive unrested turns — the dungeon
rule; town and travel time don't accrue — and a completed rest turn clears it. A
night is 48 turns (the SRD has no time-of-day model, so a night is a duration);
preparation requires an uninterrupted night's sleep, happens at most once per
sleep, and costs six turns for divine and arcane casters alike; a wandering
encounter mid-preparation doesn't void the already-written memorization (pinned).
An interrupted rest day heals nothing (RAW). Provisions consume standard rations
before iron at day boundaries (fresh food spoils first; RAW is silent) and a
carried waterskin satisfies the day (per-pint bookkeeping is below the simulation
floor). In town, provisions consume but never run short. Locked by
`test_exploration.py::TestRestAndFatigue` and `TestProvisions`.

### Doors: forcing, noise, swing-shut, locks

Any door-forcing attempt sets the noise flag for the next wandering check (the
banging is identical either way) while only a *failed* attempt denies the party
surprise against the room beyond (RAW, exactly as written). Doors the party opened
swing shut when the party is no longer in either adjoining cell unless wedged —
RAW's "likely to swing shut" becomes always, making spikes matter; a forced stuck
door is stuck again once shut. A failed pick-lock locks that thief out of that lock
until the next level; listening is once per character per door, free, and rolled
regardless of occupants — undead make no noise, so silence is genuinely ambiguous
(the no-leak doctrine applied to exploration). Locked by
`test_exploration.py::TestDoors` and `TestListening`.

### Searching and traps

Searching covers the party's current cell (the 10' × 10' area is the cell), costs a
turn, is once per character per cell per kind, is rolled regardless of contents,
and reveals every matching hidden feature on the cell. Treasure-trap find and
removal are once per trap per character each, and a failed removal springs the trap
(the classic reading; RAW says only "attempted once per trap"). Found traps stop
springing on movement (the party walks around the known pit); the player-facing
`exploration.trap.safe` emits only for a *known* trap whose trigger resolved
without springing — an unknown trap's spring die stays referee-only. A trap
transition (slides) relocates the whole party — the party model has one location
(pinned simplification). The darts volley resolves as one damage application whose
rolls are every dart's die. `TakeTreasure` delivers contents to the first living
member in marching order, who is also the treasure trap's triggerer (pinned; the
command carries no character). Locked by `test_exploration.py::TestSearching`,
`TestTreasureTraps`, and `TestTrapResolutionCensus`.

### Light, darkness, and location-bound effects

Lighting consumes the consumable (one torch, one oil flask) and extinguishing
forfeits the remainder — no partial-burn bookkeeping; relighting starts a fresh
consumable. A light-kind expiry surfaces to players as `exploration.light.expired`
appended by the session (the ledger's expiry event is referee visibility). The
tinder box gates lighting when the party has no open flame (an active
`brightness="flame"` effect), 2-in-6 per round, RAW. A darkness-family effect on
any member suppresses the whole party's light while it runs (the printed radii
swallow a marching party) and `blocks_infravision` disables infravision too.
Darkness gates, the RAW set: `Search`, `InspectTreasure`, `PickLock`, and reading
require light — searching and listening alone also work on the actor's own
infravision; `MoveParty` in darkness is legal but the party is surprised on 1–3
instead of 1–2 when unlit and not every living member has infravision (the
blind-party adaptation). Visible equals explored-plus-current-cell — the named
simplification of the spec's visible flag. Location effects anchor to
`cell:{dungeon}:{level}:{x},{y}` references with enter hooks in attachment order:
the burning-oil pool (`DropItems` oil + `LightSource` compiles the Phase 2 pool
onto the cell; every living member entering while it burns takes its 1d8),
stationary *silence* (see the amended Phase 3 entry), and *web*'s blocking cube
(entering attaches the entangled escape countdown; a location-cast web keeps the
spell's own duration on the cell and the escape params ride the effect). Web
flammability, *cloudkill*, and the walls stay manual, as registered in Phase 3.
Locked by `test_exploration.py::TestLight` and `TestLocationEffects`.

### Wandering monsters

The chance takes +1 for noise since the last check, +1 for daylight-brightness
light (*continual light*'s data — the torch/lantern flame is the baseline the
printed 1-in-6 already assumes), −1 while resting, clamps to [0, 6] with zero
skipping the roll — the modifier numbers are pinned for RAW's "referee may
increase/decrease". The cadence suspends during encounters and battles and resumes
after. Wandering monsters are never surprised (they come "moving in the direction
of the party") but the party may be. Locked by
`test_exploration.py::TestWandering`.

### The encounter procedure

Every encounter opens with surprise, a 2d6 × 10' distance roll, and reaction —
keyed encounters included (rooms are abstracted onto the same track). Keyed
awareness comes from the area's flag, a failed door forcing alerts the room, the
lit-party rule skips the monsters' surprise roll entirely, and a successful listen
marks party awareness. Parley re-rolls are uncapped, each a fresh roll with the
speaker's CHA modifier. The stance map (RAW's bands assume a referee): 2- attacks
now; 3–5 attacks at the end of the next encounter round absent evasion or
improvement; 6–8 holds and re-rolls at +0; 9–11 passes freely; 12+ friendly.
Offensive acts in encounter mode go through `EngageBattle` — except turn undead,
which keeps its own pre-battle, encounter-only command, and whose use against
non-undead onlookers provokes battle (pinned). Only attacks/hostile stances pursue
an evading party. One side's surprise is one free round, whichever door battle
enters through: surprised monsters become the party's free battle round (they
hold while the party acts), and a surprised party's lost encounter beat lets
hostile or attacking monsters open battle with their machine-run surprise round.
An encounter consumes at least one full turn — `max(next turn boundary, start +
one turn)`, which may itself land mid-turn when the encounter started mid-turn
(pinned) — and releases
`turned` effects when it ends; every effect on the encounter's monsters releases at
its end — the fiction moves on (a dead troll's pending revival is post-battle
narration); an evaded keyed encounter re-triggers fresh on the next entry. Locked
by `test_encounter.py`, the free rounds by
`test_encounter.py::TestSurpriseFreeRounds`.

### Evasion and pursuit

Pursuit compares the party's slowest running rate (full feet per round) against
the pursuers' slowest base ground mode (the mode with no descriptor, else the
first printed — flying reads dungeon ceilings); slowest-of-pursuers mirrors
slowest-of-party, and a pack that strings out is fiction. `Evade(drop="treasure")`
scatters every living member's coins and `"food"` one ration per member (pinned);
mid-pursuit `DropItems` re-baits round by round. The distraction is 3-in-6 when
the bait matches: treasure for monsters whose treasure ref carries letters — the
intelligence proxy, override-correctable per encounter — food otherwise; one roll
stops the whole side (pack behavior). Pursuit ends on distraction success, at
melee contact (gap ≤ 5', battle at 5'), or at 30 rounds — both sides spent, the
monsters give up (the terminal escape valve; with the party no faster the gap
never grows, so no sight-loss distance is needed) — with running exhaustion (−2
attack, damage, and AC — the AC penalty rides `attack_penalty_of_attackers` +2)
until three rested turns. Locked by `test_encounter.py::TestEvasionAndPursuit`.

### The battle round

Battle declarations arrive as one command per round with one declaration per
living, able member, rejected whole on any invalid declaration (partial acceptance
would tangle the replay contract). Formation width is 3 in a keyed area and 2 in
corridor under `formation_width_limit` (RAW's "2–3 characters in a 10' passage"
pinned to 2, rooms at 3); ranks recompute from the living marching order at round
start and melee reaches the front rank only, both directions, with no
firing-into-melee penalty (none exists in the SRD). The party moves as a
formation — individual members cannot leave it (the Bard's Tale convention):
all-retreat moves off at the full encounter rate (Combat.md's "full encounter
movement rate"; the running pursuit begins when the battle converts at round
end), all-fighting-withdrawal backs off at half encounter rate, and the first
`close` declaration advances the formation on its named group, stopping at 5'. A party melee attack lands on the
first living, visible monster of the group's reachable rank (deterministic, no
draw); monsters pick uniformly on the monster-action stream. Under individual
initiative the machine still resolves side blocks, ordered by the best individual
total (the SRD's phase sequence is per side). Turn undead resolves in the magic
phase, needs no declaration posting, and cannot be disrupted — a class ability,
not a spell. The machine detects disruption per the RAW trigger (a declared caster
successfully attacked or failing a save before acting) and releases concentration
on any other declared action. Each battle round advances the clock one round
through the ledger. Locked by `test_battle.py`.

### Phase 3 effect consumption in battle

*Haste* resolves the bearer's attack declaration twice and doubles track moves
(the party's consolidated move doubles when every living member bears the
multiplier, pinned); invisible combatants are untargetable — a rejection here
leaks nothing, you know what you can't see — and the machine releases the effect
when the bearer attacks or turns undead; mirror images pop one per incoming attack
with no attack roll; confusion overrides declarations with its 2d6 behavior roll
on the combat stream (a forced rules roll, not a policy choice) and re-saves on
the magic stream per its params — machine-run round rolls, not ledger ticks, so
the Phase 3 tick-time convention is untouched; `entangled` blocks declared and
policy moves while an entangled front-ranker still fights; `weakened` cannot
attack or cast (the kernel validators reject, the machine surfaces them). The
*protection from evil* melee ban: a monster whose template bears a warded category
may not initiate melee against a warded target of differing alignment — the
default policy's target pick skips them, missiles and breath land — and the ban
breaks for a target who has engaged the barred creature in melee, the ±1 modifiers
persisting (RAW's own clause). Locked by `test_battle.py::TestEffectConsumption`.

### The area footprint

An area effect targets one group; its capacity in creatures is
`ceil(span / 10) × width`, where span is the diameter (radius shapes), length
(lines, cubes, clouds), or reach-limited length (cones: length minus the gap), and
width is the formation width (unbounded with the flag off). Candidates fill in
stable spawn order up to capacity; under `aoe_friendly_fire`, an area landing on a
group at melee range appends the engaged party front rank after the monsters, in
marching order, consuming remaining capacity. Breath weapons against the party
cover `ceil(reach-limited span / 10)` ranks from the front; single-target breath
(the hellhound) picks uniformly from the front rank on the monster-action stream.
Locked by `test_battle.py::TestAreaFootprint`.

### The default action policy and morale

The policy draws only from the monster-action stream. Monsters with a scripted
pattern in their data follow it: an `uses_per_day` breath weapon opens with breath,
then breath or melee with equal chance while daily uses remain (the dragons, RAW);
a `per_round_chance_in_six` gate rolls each round (the hellhound). Otherwise a
group beyond 5' closes and at 5' melees; monster missile routines lack structured
range data and stay close-then-melee (pinned); groups never cast (casting tags are
`manual` data). Morale is auto-invoked at the start of each monster block through a
per-battle tracker: first death and half incapacitated, conditional alternates by
round context (fear-of-fire when the round's damage included fire), the spell
morale modifier folded per the Phase 3 rule; ML 2 groups rout when battle starts
(none exist in the compiled data — the branch is test-forced); a failed check
means retreat exposure that round, then flight at full speed, leaving the battle
past 120' (the pursuit sight figure, symmetric) as routed. Chasing routed monsters
is game territory (an evasion procedure with roles swapped, deferred). A group
whose living members are all turned or afraid retreats as a whole; individually
turned members of a mixed group simply don't act (pinned). Locked by
`test_battle.py::TestDefaultPolicy` and `TestMoraleAndEnds`.

### The session's honest context

Character ids assign as `character-NNNN` from the session allocator in party order
(closing the Phase 1 seam). Dead members stay in the party but count for nothing.
The session records each character death's clock round and cause — `poison` when
the killing resolution was a poison save or poison-delay expiry, else the source
kind — feeding *raise dead*'s day count always and *neutralize poison*'s round
window only for poison deaths, replacing the Phase 3 caller attestations. Session
flags are referee-only (content wiring is the game's secret) and neither view
carries the seed (it lives only in the save); the player view is the enumerated
whitelist locked by the leak property test, with undiscovered secret doors
rendering as walls. `SetDoorState` emits referee-visibility door events;
`PlaceParty` triggers no hooks, traps, or keyed encounters (a referee tool);
referee `AdvanceTime` runs full bookkeeping but no wandering cadence (the referee
controls encounters); `SpawnMonsters` rolls its count dice on the encounter stream
and opens a standard encounter with both sides rolling surprise — in a dungeon
only (the combat space needs a party cell), and never while an encounter is
already running; `PlaceParty` likewise rejects mid-encounter. Locked by
`test_session.py` and `test_crawl_properties.py`.

### Persistence and replay

Saves are self-contained (adventure content embedded) with the accepted-command
log always and the event log optional; the event log preserves unknown event types
losslessly (the log is a record, never re-derived, never lossy). Replay under a
different engine version raises `ReplayVersionError`; loading a save across engine
versions remains legal. The migration framework ships exercised by a synthetic
in-test migration at schema version 1. The standing test: `load(save)` equals
`replay(seed, commands)` for the delve golden at every checkpoint. Locked by
`test_persistence.py` and `test_phase4_goldens.py`.

## Documented adaptations

`Ruleset`-flagged deviations from rules-as-written. The Phase 1–2 flags
(`hp_reroll_at_first_level`, `encumbrance`, `variable_weapon_damage`,
`individual_initiative`, `thac0_arithmetic`, `weapon_reload`,
`hd5_counts_as_magical`) are SRD optional rules, not adaptations; Phase 4 adds the
register's first true documented adaptations.

### `deprivation_penalties` (default off)

Consumption is tracked regardless; with the flag on, the SRD's "at the referee's
discretion, for example" starvation list gets pinned defaults drawn from its own
examples: after one full day without food or water, −1 to attack rolls (an
engine-written modifier effect) and the rest cadence doubles (fatigue after three
unrested turns instead of six); after two days, movement also halves; from the
third day on, a daily 1d4 hit-point loss ticks on the effects stream. Water and
food deprivation don't stack — the worse track applies. The schedule's numbers are
invented over the SRD's open list. Locked by
`test_exploration.py::TestProvisions`.

### `aoe_friendly_fire` (default on)

Areas overlapping a melee catch friends: an area landing on a group at melee range
(≤ 5') appends the engaged party front rank after the monsters, in marching order,
consuming remaining footprint capacity. Off means areas never include party
members among a monster group's candidates. Locked by
`test_battle.py::TestAreaFootprint`.

### `formation_width_limit` (default on)

Corridor width caps combatants fighting abreast: rank width 3 inside a keyed area
and 2 in corridor cells (RAW's "2–3 characters in a 10' passage"). Off lifts the
cap — every combatant may melee and area footprints are unbounded. Locked by
`test_battle.py::TestFormationWidth`.
