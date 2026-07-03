"""Property tests for the combat kernel (hypothesis)."""

from hypothesis import given, settings
from hypothesis import strategies as st

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Alignment, Character
from osrlib.core.combat import (
    AttackContext,
    apply_healing,
    check_immunity,
    damage_source_for,
    deal_damage,
    validate_attack,
)
from osrlib.core.effects import Condition, grant_condition, has_condition
from osrlib.core.monsters import spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import to_hit_ac
from osrlib.data import load_equipment, load_monsters


def make_fighter():
    return Character(
        id="pc",
        name="Hero",
        class_id="fighter",
        race="human",
        level=3,
        xp=4000,
        scores={ability: 10 for ability in AbilityScore},
        alignment=Alignment.NEUTRAL,
        max_hp=18,
        current_hp=18,
    )


def make_troll(seed=1):
    streams = RngStreams(master_seed=seed)
    return spawn_monster(load_monsters().get("troll"), id="m-1", stream=streams.get("monster_spawn"))


@given(
    st.lists(
        st.tuples(st.sampled_from(["damage", "heal"]), st.integers(min_value=0, max_value=60)),
        max_size=30,
    )
)
def test_current_hp_stays_within_bounds_under_any_sequence(sequence):
    troll = make_troll()
    source = damage_source_for(make_fighter(), load_equipment().get("sword"), AttackContext())
    for action, amount in sequence:
        if action == "damage":
            deal_damage(troll, amount, source=source)
        else:
            apply_healing(troll, amount)
        assert 0 <= troll.current_hp <= troll.max_hp


@given(st.sampled_from([Condition.DEAD, Condition.PETRIFIED, Condition.PARALYSED, Condition.ASLEEP]))
def test_incapacitated_attackers_always_rejected(condition):
    fighter = make_fighter()
    grant_condition(fighter, condition, "effect-x")
    troll = make_troll()
    rejections = validate_attack(fighter, troll, load_equipment().get("sword"), AttackContext(), ruleset=Ruleset())
    assert rejections and rejections[0].code == "combat.attack.attacker_incapacitated"


@settings(max_examples=200)
@given(
    distance=st.one_of(st.none(), st.integers(min_value=0, max_value=2000)),
    situational=st.integers(min_value=-20, max_value=20),
    behind=st.booleans(),
    unaware=st.booleans(),
    retreating=st.booleans(),
    braced=st.booleans(),
    charging=st.booleans(),
    fired=st.booleans(),
    large=st.booleans(),
    lit=st.booleans(),
    item_id=st.sampled_from(["sword", "dagger", "short_bow", "crossbow", "holy_water", "oil_flask", "torch"]),
    reload_flag=st.booleans(),
)
def test_fuzzed_contexts_never_raise_from_the_validator(
    distance, situational, behind, unaware, retreating, braced, charging, fired, large, lit, item_id, reload_flag
):
    fighter = make_fighter()
    troll = make_troll()
    context = AttackContext(
        distance_feet=distance,
        situational_modifier=situational,
        behind_target=behind,
        target_unaware=unaware,
        defender_retreating=retreating,
        braced=braced,
        charging=charging,
        fired_last_round=fired,
        attacker_large=large,
        lit=lit,
    )
    ruleset = Ruleset(weapon_reload=reload_flag)
    attack = load_equipment().get(item_id)
    rejections = validate_attack(fighter, troll, attack, context, ruleset=ruleset)
    assert isinstance(rejections, list)  # reject, don't throw
    source = damage_source_for(fighter, attack, context)
    assert isinstance(check_immunity(troll, source, ruleset=ruleset, attacker=fighter), bool)


@given(st.integers(min_value=2, max_value=20), st.integers(min_value=-30, max_value=30))
def test_matrix_lookup_total_order(thac0, ac):
    # A lower (better) AC is never easier to hit.
    assert to_hit_ac(thac0, ac) >= to_hit_ac(thac0, ac + 1)
    assert 2 <= to_hit_ac(thac0, ac) <= 20


@given(st.lists(st.integers(min_value=1, max_value=40), min_size=1, max_size=10))
def test_dead_creatures_stay_dead_and_at_zero(damages):
    troll = make_troll()
    source = damage_source_for(make_fighter(), load_equipment().get("sword"), AttackContext())
    for amount in damages:
        deal_damage(troll, amount, source=source)
    if troll.current_hp == 0:
        assert has_condition(troll, Condition.DEAD)
        apply_healing(troll, 100)
        assert troll.current_hp == 0  # the dead cannot be healed
