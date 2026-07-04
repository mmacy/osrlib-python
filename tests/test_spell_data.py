"""Tests for the spell data pipeline: census, dual pages, spot checks, provenance."""

import pytest

from osrlib.core.monsters import MonsterTemplate
from osrlib.core.spells import SpellTemplate
from osrlib.data import load_monsters, load_spells

# The nine concepts printed as separate (C) and (MU) pages, pinned to _c/_mu ids.
DUAL_CONCEPTS = (
    "continual_light",
    "detect_evil",
    "detect_magic",
    "hold_person",
    "light",
    "locate_object",
    "protection_from_evil",
    "protection_from_evil_10_radius",
    "remove_curse",
)


class TestCensus:
    def test_106_entries(self):
        assert len(load_spells().spells) == 106

    def test_list_split(self):
        catalog = load_spells()
        assert len(catalog.by_list("cleric")) == 34
        assert len(catalog.by_list("magic_user")) == 72

    def test_per_level_counts(self):
        catalog = load_spells()
        assert [len(catalog.by_list("cleric", level)) for level in range(1, 6)] == [8, 8, 6, 6, 6]
        assert [len(catalog.by_list("magic_user", level)) for level in range(1, 7)] == [12] * 6

    def test_16_reversible_entries(self):
        reversible = [spell for spell in load_spells().spells if spell.reversed_form is not None]
        assert len(reversible) == 16

    def test_nine_dual_pairs_under_pinned_ids(self):
        catalog = load_spells()
        for base in DUAL_CONCEPTS:
            cleric = catalog.get(f"{base}_c")
            magic_user = catalog.get(f"{base}_mu")
            assert cleric.spell_list == "cleric"
            assert magic_user.spell_list == "magic_user"
        # No dual concept leaks an unsuffixed id.
        ids = {spell.id for spell in catalog.spells}
        assert not any(base in ids for base in DUAL_CONCEPTS)

    def test_compiled_spells_carry_exactly_the_two_legacy_lists(self):
        # `spell_list` is an open validated string; the shipped Classic catalog
        # carries exactly the two legacy wire values.
        assert {spell.spell_list for spell in load_spells().spells} == {"cleric", "magic_user"}

    def test_invisible_stalker_is_not_a_dual_page(self):
        # The (MU) marker distinguishes the monster page, not a (C) twin.
        spell = load_spells().get("invisible_stalker")
        assert spell.level == 6 and spell.spell_list == "magic_user"
        assert load_monsters().get("invisible_stalker_monster")

    def test_double_encoded_filenames_resolved(self):
        # The four typographic-foot-mark filenames must have compiled.
        catalog = load_spells()
        assert catalog.get("silence_15_radius").level == 2
        assert catalog.get("invisibility_10_radius").level == 3
        assert catalog.get("protection_from_evil_10_radius_c").level == 4
        assert catalog.get("protection_from_evil_10_radius_mu").level == 3

    def test_every_mode_key_unique_per_form(self):
        for spell in load_spells().spells:
            keys = [mode.key for mode in spell.modes]
            assert len(set(keys)) == len(keys), spell.id
            if spell.reversed_form is not None:
                keys = [mode.key for mode in spell.reversed_form.modes]
                assert len(set(keys)) == len(keys), spell.id

    def test_manual_modes_carry_prose(self):
        for spell in load_spells().spells:
            for mode in spell.modes:
                if mode.manual:
                    assert mode.prose, f"{spell.id}.{mode.key}"


class TestSpotChecks:
    def test_fire_ball(self):
        spell = load_spells().get("fire_ball")
        assert (spell.level, spell.spell_list) == (3, "magic_user")
        assert spell.duration_spec.kind == "instant"
        assert spell.range_spec.kind == "feet" and spell.range_spec.feet == 240
        mode = spell.mode("damage")
        assert mode.targeting.shape == "sphere" and mode.targeting.dimensions == {"radius_feet": 20}
        assert mode.save.category == "spells" and mode.save.on_save == "half"
        assert mode.effect.params["dice_per_level"] == "1d6"
        assert mode.effect.params["element"] == "fire"

    def test_sleep(self):
        spell = load_spells().get("sleep")
        assert spell.duration_spec.kind == "fixed"
        assert spell.duration_spec.dice == "4d4" and spell.duration_spec.unit == "turn"
        assert [mode.key for mode in spell.modes] == ["single_4_plus", "hd_budget"]
        budget = spell.mode("hd_budget")
        assert budget.targeting.hd_budget_dice == "2d8" and budget.targeting.hd_cap == 4
        assert budget.save is None  # no saving throw
        single = spell.mode("single_4_plus")
        assert single.effect.params["hd_bonus_required"] is True

    def test_hold_person_pair_diverges_exactly_in_level_duration_range(self):
        catalog = load_spells()
        cleric, magic_user = catalog.get("hold_person_c"), catalog.get("hold_person_mu")
        assert (cleric.level, magic_user.level) == (2, 3)
        assert cleric.duration == "9 turns" and cleric.duration_spec.amount == 9
        assert magic_user.duration == "1 turn per level"
        assert magic_user.duration_spec.amount == 0 and magic_user.duration_spec.per_level == 1
        assert (cleric.range, magic_user.range) == ("180’", "120’")
        # Same modes, same saves, same effect on both entries.
        for key in ("individual", "group"):
            assert cleric.mode(key).save == magic_user.mode(key).save
            assert cleric.mode(key).effect == magic_user.mode(key).effect
        assert cleric.mode("individual").save.modifier == -2
        assert cleric.mode("group").targeting.count_dice == "1d4"

    def test_magic_missile(self):
        mode = load_spells().get("magic_missile").mode("missiles")
        assert mode.effect.params["auto_hit"] is True
        assert mode.effect.params["dice"] == "1d6+1"
        assert (
            mode.effect.params["missiles_base"],
            mode.effect.params["missiles_step"],
            mode.effect.params["missiles_per_levels"],
        ) == (1, 2, 5)

    def test_lightning_bolt_is_destructive(self):
        mode = load_spells().get("lightning_bolt").mode("damage")
        assert mode.effect.params["destructive"] is True
        assert mode.effect.params["element"] == "lightning"

    def test_conjured_snake_is_a_valid_monster_template(self):
        spell = load_spells().get("sticks_to_snakes")
        assert len(spell.conjured_monsters) == 1
        snake = spell.conjured_monsters[0]
        assert isinstance(snake, MonsterTemplate)
        assert snake.id == "conjured_snake"
        assert snake.xp == 10
        assert (snake.ac, snake.hit_dice.count, snake.morale) == (6, 1, 7)
        assert snake.saves.save_as == "1"

    def test_conjure_elemental_references_resolve(self):
        spell = load_spells().get("conjure_elemental")
        monsters = load_monsters()
        assert len(spell.conjured_monster_ids) == 4
        for monster_id in spell.conjured_monster_ids:
            assert monsters.get(monster_id).hit_dice.count == 16

    def test_dual_form_duration_splits(self):
        spell = load_spells().get("remove_curse_c")
        assert spell.duration_spec.kind == "instant"
        assert spell.reversed_form.duration_spec.kind == "permanent"

    def test_charm_person_takes_first_duration_label(self):
        # The page carries a second **Duration:** label deeper in its prose.
        spell = load_spells().get("charm_person")
        assert spell.duration == "One or more days (see below)"
        assert spell.duration_spec.kind == "special"

    def test_reversed_forms_are_entry_data(self):
        spell = load_spells().get("cure_light_wounds")
        assert spell.reversed_form.name == "Cause Light Wounds"
        harm = spell.mode("harm", reversed=True)
        assert harm.effect.params["touch_attack"] is True
        with pytest.raises(ValueError):
            load_spells().get("cause_light_wounds")

    def test_range_kinds(self):
        catalog = load_spells()
        assert catalog.get("shield").range_spec.kind == "caster"
        assert catalog.get("cure_light_wounds").range_spec.kind == "touch"
        # 240 yards around the caster converts to feet.
        yards = [spell for spell in catalog.spells if spell.range_spec.kind == "yards"]
        assert len(yards) == 1 and yards[0].range_spec.feet == 720

    def test_concentration_caps_parse(self):
        specs = [spell.duration_spec for spell in load_spells().spells if spell.duration_spec.kind == "concentration"]
        assert len(specs) >= 4
        capped = [spec for spec in specs if spec.concentration_cap_amount is not None]
        assert {(spec.concentration_cap_amount, spec.concentration_cap_unit) for spec in capped} == {
            (1, "day"),
            (6, "round"),
        }

    def test_override_provenance_default_empty(self):
        for spell in load_spells().spells:
            assert spell.overrides_applied == ()


class TestModelGuards:
    def test_automated_mode_requires_targeting_and_effect(self):
        with pytest.raises(ValueError, match="lacks targeting or an effect"):
            SpellTemplate(
                id="x",
                name="X",
                spell_list="cleric",
                level=1,
                duration="Instant",
                duration_spec={"kind": "instant"},
                range="30’",
                range_spec={"kind": "feet", "feet": 30},
                modes=[{"key": "broken", "manual": False}],
            )

    def test_unknown_effect_kind_rejected(self):
        with pytest.raises(ValueError, match="effect kind"):
            SpellTemplate(
                id="x",
                name="X",
                spell_list="cleric",
                level=1,
                duration="Instant",
                duration_spec={"kind": "instant"},
                range="30’",
                range_spec={"kind": "feet", "feet": 30},
                modes=[
                    {
                        "key": "bad",
                        "targeting": {"mode": "single"},
                        "effect": {"kind": "transmogrify"},
                    }
                ],
            )
