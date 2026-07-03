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
and set XP once. Draining a monster loses Hit Dice symmetrically: the instance
re-derives THAC0 and saves from the reduced HD and loses a rolled d8; below 1 HD it
dies. The spawn consequence (rises as a wight in 1d4 days) is a structured-but-manual
field on the drain event — the kernel kills, the game narrates. Locked by
`test_drain.py`.

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

The three-per-day limit is the *dragons'* rule (and the dragon turtle's), pinned
per-monster; the hellhound has none — its 2-in-6 per-round chance ships as data for
Phase 4's action policy, and the kernel resolves the breath whenever invoked. Dragon
breath is a destructive death: the victim's mundane equipment is destroyed. Dragons'
energy immunity encodes as immune-to-nonmagical plus auto-save-versus-magical for the
variant's breath element(s). Locked by `test_combat.py::TestBreathAndGaze`.

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
3 magic. Locked by `test_effects.py::TestMummyRot`.

### Asleep grants auto-hit and dies-to-a-blade; dozing is a caller modifier

The asleep condition's combat hook ships now (the *sleep* spell that inflicts it is
Phase 3); the dragons' "may be attacked for one round with a +2 bonus" dozing is the
caller-supplied situational modifier, not a condition. Locked by
`test_combat.py::TestAttackRoll`.

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
`test_monster_data.py` and `test_effects.py`.

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

## Documented adaptations

None yet. `Ruleset`-flagged deviations from rules-as-written join this register in
the phases that implement them; the Phase 1 and Phase 2 flags
(`hp_reroll_at_first_level`, `encumbrance`, `variable_weapon_damage`,
`individual_initiative`, `thac0_arithmetic`, `weapon_reload`,
`hd5_counts_as_magical`) are SRD optional rules, not adaptations.
