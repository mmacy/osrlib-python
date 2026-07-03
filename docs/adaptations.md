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

## Documented adaptations

None yet. `Ruleset`-flagged deviations from rules-as-written join this register in the
phases that implement them; Phase 1's two flags (`hp_reroll_at_first_level`,
`encumbrance`) are SRD optional rules, not adaptations.
