"""Acceptance tests for judging candidate declarations in one preview (issue #362).

A remote battle screen marks which targets each possible action reaches before the player declares one, with the
session's own rejection codes as the reason a target is disabled. `preview_battle_round` takes `candidates`, each a
`BattleCandidate` with a declaration complete except for its target, the field the target goes in, and the target
ids. `BattleRoundPreview.candidates` holds one `BattleCandidateJudgement` for each candidate, in the order passed.

**The agreement property.** Each candidate is judged as `can_hide` judges a `hide`: in place of the member's own
planned declaration, with every other member's planned move made. So for every plan, candidate, and target, the
judgement's `rejections[target_id]` equals the `rejections` entry for the member of the preview of the same plan with
the member's declaration replaced by `candidate.declaration_for(target_id)`. The tests state it over fixed scenarios
and plans, and over plans Hypothesis generates.

**Attack kinds.** `attack_kinds` says whether an attack candidate would be a melee or a missile attack at each target,
as the session decides it when it accepts the round. With the monsters held still, nothing changes the distance before
the attack comes up, so the round itself makes the same decision. A melee attack records the monster it fought in
`BattleState.melee_engagements`, and a missile attack doesn't.

The battles come from `positions_fixtures`: the walk-through party of two fighters, a thief, an elf, a magic-user, and
a cleric, with 400 hit points each, in a 10' corridor two cells long unless a test says otherwise.
"""

from functools import cache

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import positions_fixtures as pf
from osrlib.core.effects import Condition, grant_condition
from osrlib.core.items import MagicItemInstance
from osrlib.core.validation import Rejection
from osrlib.crawl import battle as battle_module
from osrlib.crawl.battle import (
    BattleCandidate,
    BattleCandidateJudgement,
    BattleRoundPreview,
    preview_battle_round,
)
from osrlib.crawl.commands import BattleDeclaration, EngageBattle, GrantItem, SessionMode, SpawnMonsters
from osrlib.crawl.session import GameSession

SPELLS = {"Tam": ("cure_light_wounds", "cure_light_wounds"), "Wren": ("sleep", "magic_missile")}
"""Spells memorized for the scenarios: a touch spell for the cleric, and a group spell and a missile spell."""

NOT_IN_FRONT_RANK = "battle.declaration.not_in_front_rank"
OUT_OF_REACH = "combat.attack.out_of_reach"
MEMBER_OUT_OF_REACH = "battle.declaration.member_out_of_reach"
WEAPON_NOT_WIELDED = "battle.declaration.weapon_not_wielded"
UNKNOWN_GROUP = "battle.declaration.unknown_group"


# ---------------------------------------------------------------------- setup


def _grant(game: GameSession, name: str, item_id: str, quantity: int = 1) -> str:
    """Give `name` an item and return the id a declaration names it by."""
    member = game.member(pf.named(game, name))
    assert game.execute(GrantItem(character_id=member.id, item_id=item_id, quantity=quantity)).accepted
    for entry in reversed(member.inventory.items):
        if isinstance(entry, MagicItemInstance) and entry.template_id == item_id:
            return entry.instance_id
    return item_id


def _equip(game: GameSession) -> None:
    """Give Tam a potion of healing, a staff of healing, and a torch, and Nim flasks of oil, for the candidates."""
    _grant(game, "Tam", "potion_of_healing")
    _grant(game, "Tam", "staff_of_healing")
    _grant(game, "Tam", "torch")
    if any(member.name == "Nim" for member in game.party.members):
        _grant(game, "Nim", "oil_flask", 2)


def _held(**battle_args) -> GameSession:
    """A battle whose monsters hold still, with the scenario spells memorized and Tam's items granted."""
    game = pf.battle(spells=SPELLS, **battle_args)
    pf.hold_monsters(game)
    _equip(game)
    return game


@cache
def _corridor() -> GameSession:
    """Two goblins 30' away in the corridor, with everyone on the line."""
    return _held(distance=30)


@cache
def _advanced() -> GameSession:
    """Two goblins 40' away, after a round in which Hild closed 20' and Aelin fell back 10'."""
    game = _held(distance=40)
    hild, aelin = pf.named(game, "Hild"), pf.named(game, "Aelin")
    pf.play(game, pf.close(game, hild), pf.declare(aelin, "move", move="fighting_withdrawal"))
    assert pf.advance(game, hild) == 20 and pf.advance(game, aelin) == -10
    return game


@cache
def _three_groups() -> GameSession:
    """Three goblins 30' away, two orcs 50' away, and two orcs 70' away, in a hall four wide."""
    for seed in pf.SEEDS:
        game = pf.session(seed, adventure=pf.hall(), spells=SPELLS)
        opened = True
        for template_id, count, distance in (("goblin", 3, 30), ("orc", 2, 50), ("orc", 2, 70)):
            result = game.execute(SpawnMonsters(template_id=template_id, count_fixed=count, distance_feet=distance))
            if not result.accepted or game.mode is not SessionMode.ENCOUNTER:
                opened = False
                break
        if not opened or not game.execute(EngageBattle()).accepted or game.mode is not SessionMode.BATTLE:
            continue
        if game.battle.round == 0 and [group.distance_feet for group in game.encounter.groups] == [30, 50, 70]:
            pf.hold_monsters(game)
            _equip(game)
            return game
    raise AssertionError("precondition: no seed opened the three-group battle")


@cache
def _wren_invisible() -> GameSession:
    """Two goblins 30' away, with Wren invisible, so the line moves back when every other member falls back."""
    game = _held(distance=30)
    grant_condition(game.member(pf.named(game, "Wren")), Condition.INVISIBLE, "effect-9999")
    return game


SCENARIOS = {
    "corridor": _corridor,
    "after a round of moves": _advanced,
    "three groups": _three_groups,
    "an invisible magic-user": _wren_invisible,
}


def _ids(game: GameSession) -> dict[str, str]:
    return {member.name: member.id for member in game.party.members}


def _groups(game: GameSession) -> tuple[str, ...]:
    return tuple(group.id for group in game.encounter.groups)


def _withdraw(member_id: str, move: str = "fighting_withdrawal", replacement_id: str | None = None):
    return pf.declare(member_id, "move", move=move, replacement_id=replacement_id)


def _plans(game: GameSession) -> dict[str, list[BattleDeclaration]]:
    """The plans of the agreement property, each over the declarers of `game`."""
    ids = _ids(game)
    declarers = pf.able(game)
    first_group = _groups(game)[0]
    mixed = [
        _withdraw(member_id, "retreat" if index == 0 else "fighting_withdrawal")
        for index, member_id in enumerate(member_id for member_id in declarers if member_id != ids["Wren"])
    ]
    return {
        "no plan": [],
        "everyone holds": [pf.declare(member_id) for member_id in declarers],
        "Hild closes and attacks": [pf.strike(game, ids["Hild"], move="close", group=first_group)],
        "Hild falls back alone and trades places with Aelin": [_withdraw(ids["Hild"], replacement_id=ids["Aelin"])],
        "everyone withdraws as a formation": [_withdraw(member_id) for member_id in declarers],
        "everyone but Wren falls back alone": mixed,
    }


def _candidates(game: GameSession) -> list[BattleCandidate]:
    """A generous menu for every declarer: each weapon, close and attack, spells, items, and hand-overs."""
    ids = _ids(game)
    groups = (*_groups(game), "group-9999")
    members = (*ids.values(), "character-9999")
    monsters = tuple(game.encounter.groups[0].monster_ids)
    tam = game.member(ids["Tam"])
    potion = next(e.instance_id for e in tam.inventory.items if getattr(e, "template_id", None) == "potion_of_healing")
    staff = next(e.instance_id for e in tam.inventory.items if getattr(e, "template_id", None) == "staff_of_healing")
    weapons = {"Hild": "sword", "Osric": "sword", "Nim": "dagger", "Aelin": "short_bow", "Wren": None, "Tam": "mace"}
    found = []
    for name, weapon_id in weapons.items():
        member_id = ids[name]
        for move in (None, "close"):
            attack = pf.declare(member_id, "attack", weapon_id=weapon_id, move=move)
            found.append(BattleCandidate(declaration=attack, target_field="target_group_id", target_ids=groups))
        found.append(
            BattleCandidate(
                declaration=pf.declare(member_id, "attack", weapon_id="dagger"),
                target_field="target_group_id",
                target_ids=groups,
            )
        )
        found.append(
            BattleCandidate(
                declaration=pf.declare(member_id, "move", move="close"),
                target_field="target_group_id",
                target_ids=groups,
            )
        )
    nim = ids["Nim"]
    found.append(_on_groups_of(pf.declare(nim, "use_item", item_id="oil_flask"), groups))
    found.append(
        BattleCandidate(declaration=pf.declare(nim, "attack"), target_field="target_group_id", target_ids=groups)
    )
    found.append(
        BattleCandidate(
            declaration=pf.declare(nim, "attack", move="close"), target_field="target_group_id", target_ids=groups
        )
    )
    tam_id, wren_id = ids["Tam"], ids["Wren"]
    cure = pf.declare(tam_id, "cast", spell_id="cure_light_wounds", spell_mode="heal")
    harm = pf.declare(tam_id, "cast", spell_id="cure_light_wounds", spell_mode="harm", reversed=True)
    found += [
        BattleCandidate(declaration=cure, target_field="targets", target_ids=members),
        BattleCandidate(declaration=harm, target_field="targets", target_ids=monsters),
        BattleCandidate(
            declaration=pf.declare(wren_id, "cast", spell_id="sleep", spell_mode="default"),
            target_field="target_group_id",
            target_ids=groups,
        ),
        BattleCandidate(
            declaration=pf.declare(wren_id, "cast", spell_id="magic_missile", spell_mode="missiles"),
            target_field="targets",
            target_ids=monsters,
        ),
        BattleCandidate(
            declaration=pf.declare(tam_id, "use_item", item_id=potion), target_field="targets", target_ids=members
        ),
        BattleCandidate(
            declaration=pf.declare(tam_id, "use_item", item_id=staff), target_field="targets", target_ids=members
        ),
        BattleCandidate(
            declaration=pf.declare(tam_id, "give_item", item_id="torch"), target_field="targets", target_ids=members
        ),
        *(
            BattleCandidate(declaration=pf.declare(member_id, "hide"), target_field="targets", target_ids=(member_id,))
            for member_id in ids.values()
        ),
    ]
    declarers = set(pf.able(game))
    return [candidate for candidate in found if candidate.declaration.character_id in declarers]


def _on_groups_of(declaration: BattleDeclaration, groups: tuple[str, ...]) -> BattleCandidate:
    return BattleCandidate(declaration=declaration, target_field="target_group_id", target_ids=groups)


def _replaced(plan, declaration: BattleDeclaration) -> list[BattleDeclaration]:
    """`plan` with the member's own declaration, if any, replaced by `declaration`."""
    return [*(planned for planned in plan if planned.character_id != declaration.character_id), declaration]


def _assert_agrees(game: GameSession, plan, candidates) -> BattleRoundPreview:
    """Check the agreement property for every candidate and target, and that the rest of the preview is unchanged."""
    preview = preview_battle_round(game, plan, candidates)
    assert preview.model_copy(update={"candidates": ()}) == preview_battle_round(game, plan)
    assert [judgement.candidate for judgement in preview.candidates] == list(candidates)
    for judgement in preview.candidates:
        candidate = judgement.candidate
        assert list(judgement.rejections) == list(candidate.target_ids)
        assert set(judgement.attack_kinds) <= set(candidate.target_ids)
        assert list(judgement.attack_kinds) == [t for t in candidate.target_ids if t in judgement.attack_kinds]
        for target_id in candidate.target_ids:
            declaration = candidate.declaration_for(target_id)
            alone = preview_battle_round(game, _replaced(plan, declaration))
            assert judgement.rejections[target_id] == alone.rejections[declaration.character_id], (
                candidate,
                target_id,
            )
            if declaration.action == "attack" and not judgement.rejections[target_id]:
                assert target_id in judgement.attack_kinds, (candidate, target_id)
        if candidate.declaration.action != "attack":
            assert judgement.attack_kinds == {}
    return preview


# ---------------------------------------------------------------------- the agreement property


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_each_entry_is_the_preview_of_the_plan_with_the_declaration_replaced(scenario):
    game = SCENARIOS[scenario]()
    before = pf.state_without_logs(game)
    candidates = _candidates(game)

    for label, plan in _plans(game).items():
        preview = _assert_agrees(game, plan, candidates)
        assert len(preview.candidates) == len(candidates), label
        for member_id in pf.able(game):
            hide = pf.declare(member_id, "hide")
            accepted = not preview_battle_round(game, _replaced(plan, hide)).rejections[member_id]
            assert (member_id in preview.can_hide) is accepted, (label, member_id)

    assert pf.state_without_logs(game) == before


def _declaration_choices(game: GameSession, member_id: str, declarers: list[str]):
    """The declarations Hypothesis picks from for one member: every kind of move, and a few actions."""
    groups = _groups(game)
    others = [other for other in declarers if other != member_id]
    return st.one_of(
        st.just(None),
        st.just(pf.declare(member_id)),
        st.just(pf.declare(member_id, "hide")),
        st.sampled_from([pf.close(game, member_id, group=group) for group in groups]),
        st.sampled_from([pf.strike(game, member_id, group=group, move="close") for group in groups]),
        st.sampled_from([_withdraw(member_id, move) for move in ("fighting_withdrawal", "retreat")]),
        st.sampled_from([_withdraw(member_id, replacement_id=other) for other in others]),
    )


@st.composite
def _scenario_and_plan(draw):
    name = draw(st.sampled_from(list(SCENARIOS)))
    game = SCENARIOS[name]()
    declarers = pf.able(game)
    plan = [draw(_declaration_choices(game, member_id, declarers)) for member_id in declarers]
    return name, [declaration for declaration in plan if declaration is not None]


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_scenario_and_plan())
def test_the_agreement_holds_over_generated_plans(scenario_and_plan):
    name, plan = scenario_and_plan
    game = SCENARIOS[name]()
    candidates = [
        candidate for candidate in _candidates(game) if candidate.declaration.action in ("attack", "move", "cast")
    ]
    _assert_agrees(game, plan, candidates[::3])


# ---------------------------------------------------------------------- positions and reach


def _one(game: GameSession, plan, candidate: BattleCandidate) -> BattleCandidateJudgement:
    preview = _assert_agrees(game, plan, [candidate])
    return preview.candidates[0]


def _codes(judgement: BattleCandidateJudgement) -> dict[str, list[str]]:
    return {target_id: [rejection.code for rejection in found] for target_id, found in judgement.rejections.items()}


def _on_groups(declaration: BattleDeclaration, *group_ids: str) -> BattleCandidate:
    return BattleCandidate(declaration=declaration, target_field="target_group_id", target_ids=group_ids)


def _on_targets(declaration: BattleDeclaration, *target_ids: str) -> BattleCandidate:
    return BattleCandidate(declaration=declaration, target_field="targets", target_ids=target_ids)


def test_a_close_takes_the_last_front_rank_place_and_one_more_is_refused_for_a_full_rank():
    """Goblins 25' away: Hild, Osric, and Nim each close to 5', and the corridor's front rank holds two."""
    game = _held(distance=25)
    ids = _ids(game)
    group = _groups(game)[0]
    hild_closes = pf.strike(game, ids["Hild"], move="close", weapon_id="sword")

    osric = _one(
        game, [hild_closes], _on_groups(pf.declare(ids["Osric"], "attack", move="close", weapon_id="sword"), group)
    )
    assert _codes(osric) == {group: []}

    both = [hild_closes, pf.strike(game, ids["Osric"], move="close", weapon_id="sword")]
    nim = _one(game, both, _on_groups(pf.declare(ids["Nim"], "attack", move="close", weapon_id="dagger"), group))
    assert _codes(nim) == {group: [NOT_IN_FRONT_RANK]}
    assert nim.rejections[group][0].params["character"] == ids["Nim"]


def test_a_candidate_close_earlier_in_marching_order_reports_only_its_own_rejections():
    """Osric and Nim plan to close and attack; Hild's candidate close takes a place, which pushes Nim out."""
    game = _held(distance=25)
    ids = _ids(game)
    group = _groups(game)[0]
    plan = [
        pf.strike(game, ids["Osric"], move="close", weapon_id="sword"),
        pf.strike(game, ids["Nim"], move="close", weapon_id="dagger"),
    ]
    assert not any(preview_battle_round(game, plan).rejections.values()), "precondition: the plan is accepted"
    candidate = _on_groups(pf.declare(ids["Hild"], "attack", move="close", weapon_id="sword"), group)

    judgement = _one(game, plan, candidate)

    assert _codes(judgement) == {group: []}
    picked = preview_battle_round(game, [*plan, candidate.declaration_for(group)])
    assert [rejection.code for rejection in picked.rejections[ids["Nim"]]] == [NOT_IN_FRONT_RANK]


def test_a_close_toward_a_far_group_stops_short_of_a_nearer_one():
    """Nim closes 30' toward the orcs 70' away, and stops 5' from the goblins 30' away, so the throw is too long."""
    game = _three_groups()
    nim = _ids(game)["Nim"]
    goblins, _, far = _groups(game)
    judgement = _one(game, [], _on_groups(pf.declare(nim, "attack", move="close", weapon_id="dagger"), goblins, far))

    assert _codes(judgement)[goblins] == []
    assert judgement.attack_kinds == {goblins: "melee", far: "missile"}
    assert _codes(judgement)[far] == ["combat.attack.out_of_range"]
    preview = preview_battle_round(game, [pf.close(game, nim, group=far)])
    assert preview.advance_feet[nim] == 25


def test_a_touch_spell_cant_reach_a_member_whose_planned_close_takes_them_away():
    game = _held(distance=60)
    ids = _ids(game)
    cure = pf.declare(ids["Tam"], "cast", spell_id="cure_light_wounds", spell_mode="heal")
    plan = [pf.close(game, ids["Hild"])]

    judgement = _one(game, plan, _on_targets(cure, ids["Hild"], ids["Osric"], ids["Tam"]))

    assert _codes(judgement) == {ids["Hild"]: ["magic.cast.out_of_range"], ids["Osric"]: [], ids["Tam"]: []}
    assert judgement.attack_kinds == {}


def test_a_touch_spell_cant_reach_a_monster_outside_its_groups_front_rank():
    """Tam stands in the front rank beside Hild, 5' from three goblins, and only two goblins stand in theirs."""
    game = _held(roster=(pf.TAM, pf.HILD), distance=5, count=3)
    tam = _ids(game)["Tam"]
    first, second, third = game.encounter.groups[0].monster_ids
    harm = pf.declare(tam, "cast", spell_id="cure_light_wounds", spell_mode="harm", reversed=True)

    judgement = _one(game, [], _on_targets(harm, first, second, third))

    assert _codes(judgement) == {first: [], second: [], third: [OUT_OF_REACH]}
    assert judgement.rejections[third][0].params["target"] == third


def test_a_potion_and_a_staff_of_healing_cant_reach_a_member_out_of_reach():
    game = _held(distance=60)
    ids = _ids(game)
    tam = game.member(ids["Tam"])
    plan = [pf.close(game, ids["Hild"])]
    for template_id in ("potion_of_healing", "staff_of_healing"):
        item_id = next(e.instance_id for e in tam.inventory.items if getattr(e, "template_id", None) == template_id)
        use = pf.declare(ids["Tam"], "use_item", item_id=item_id)

        judgement = _one(game, plan, _on_targets(use, ids["Hild"], ids["Osric"]))

        assert _codes(judgement) == {ids["Hild"]: [MEMBER_OUT_OF_REACH], ids["Osric"]: []}, template_id
        assert judgement.rejections[ids["Hild"]][0].params == {"character": ids["Tam"], "target": ids["Hild"]}


def test_a_hand_over_to_oneself_and_to_an_incapacitated_member():
    game = _held(distance=30)
    ids = _ids(game)
    grant_condition(game.member(ids["Osric"]), Condition.PARALYSED, "effect-9999")
    assert ids["Osric"] not in pf.able(game), "precondition: Osric can't act"
    give = pf.declare(ids["Tam"], "give_item", item_id="torch")

    judgement = _one(game, [], _on_targets(give, ids["Tam"], ids["Osric"], ids["Hild"]))

    codes = _codes(judgement)
    assert codes[ids["Tam"]] != []
    assert codes[ids["Hild"]] == []
    # Osric can't act but can still be handed an item, as the session judges a hand-over.
    assert codes[ids["Osric"]] == [
        rejection.code
        for rejection in preview_battle_round(game, [give.model_copy(update={"targets": (ids["Osric"],)})]).rejections[
            ids["Tam"]
        ]
    ]


def test_a_weapon_that_isnt_wielded_is_refused_under_every_target():
    game = _three_groups()
    hild = _ids(game)["Hild"]
    groups = _groups(game)

    judgement = _one(game, [], _on_groups(pf.declare(hild, "attack", weapon_id="dagger"), *groups))

    expected = (Rejection(code=WEAPON_NOT_WIELDED, params={"item": "dagger", "character": hild}),)
    assert judgement.rejections == {group: expected for group in groups}
    assert judgement.attack_kinds == {}


def test_an_unknown_target_gets_the_sessions_own_rejection():
    game = _corridor()
    hild = _ids(game)["Hild"]

    judgement = _one(game, [], _on_groups(pf.declare(hild, "attack", weapon_id="sword"), "group-9999"))

    assert judgement.rejections == {
        "group-9999": (Rejection(code=UNKNOWN_GROUP, params={"character": hild}),),
    }
    assert judgement.attack_kinds == {}


def test_a_candidate_for_a_member_not_in_the_plan_and_several_for_one_member():
    game = _corridor()
    ids = _ids(game)
    group = _groups(game)[0]
    plan = [pf.declare(ids["Hild"])]
    candidates = [
        _on_groups(pf.declare(ids["Aelin"], "attack", weapon_id="short_bow"), group),
        _on_groups(pf.declare(ids["Aelin"], "attack", move="close", weapon_id="short_bow"), group),
        _on_groups(pf.declare(ids["Hild"], "attack", move="close", weapon_id="sword"), group),
    ]

    preview = _assert_agrees(game, plan, candidates)

    assert [judgement.candidate for judgement in preview.candidates] == candidates
    assert set(preview.rejections) == {ids["Hild"]}
    assert preview.candidates[0].attack_kinds == {group: "missile"}


def test_an_empty_target_list_gives_an_empty_judgement():
    game = _corridor()
    candidate = _on_groups(pf.declare(_ids(game)["Hild"], "attack", weapon_id="sword"))

    judgement = preview_battle_round(game, [], [candidate]).candidates[0]

    assert judgement.rejections == {}
    assert judgement.attack_kinds == {}


def test_a_declaration_with_the_other_target_field_set_is_judged_with_both():
    """A `cast` at `targets` whose declaration also names a group keeps the group, as the plan would."""
    game = _corridor()
    wren = _ids(game)["Wren"]
    group = _groups(game)[0]
    monster = game.encounter.groups[0].monster_ids[0]
    cast = pf.declare(wren, "cast", spell_id="magic_missile", spell_mode="missiles", target_group_id=group)
    candidate = _on_targets(cast, monster)

    assert candidate.declaration_for(monster).target_group_id == group
    _one(game, [], candidate)


# ---------------------------------------------------------------------- attack kinds


def _melee_at_resolution(game: GameSession, declaration: BattleDeclaration) -> bool:
    """Play a round with `declaration` and everyone else holding, and say whether the member fought in melee."""
    pf.play(game, declaration)
    return bool(game.battle.melee_engagements.get(declaration.character_id))


@pytest.mark.parametrize(
    ("label", "distance", "name", "fields", "kind"),
    [
        ("a dagger from reach", 5, "Nim", {"weapon_id": "dagger"}, "melee"),
        (
            "a dagger close and attack that stops short and throws",
            40,
            "Nim",
            {"weapon_id": "dagger", "move": "close"},
            "missile",
        ),
        ("a bow", 30, "Aelin", {"weapon_id": "short_bow"}, "missile"),
        ("a sword close and attack", 25, "Hild", {"weapon_id": "sword", "move": "close"}, "melee"),
        ("no weapon_id, closing into reach", 25, "Hild", {"move": "close"}, "melee"),
        ("no weapon_id, stopping short", 40, "Nim", {"move": "close"}, "missile"),
    ],
)
def test_the_attack_kind_is_the_one_the_round_resolves(label, distance, name, fields, kind):
    roster = (pf.NIM, pf.HILD) if distance == 5 else pf.WALK
    game = pf.battle(roster=roster, distance=distance)
    pf.hold_monsters(game)
    member_id = _ids(game)[name]
    group = _groups(game)[0]
    candidate = _on_groups(pf.declare(member_id, "attack", **fields), group)

    judgement = _one(game, [], candidate)

    assert judgement.rejections[group] == (), label
    assert judgement.attack_kinds == {group: kind}, label
    assert _melee_at_resolution(game, candidate.declaration_for(group)) is (kind == "melee"), label


def test_a_refused_attack_has_a_kind_when_the_check_gets_that_far():
    """Hild's sword can't reach goblins 30' away, which is a melee refusal, so the label is still melee."""
    game = _corridor()
    hild = _ids(game)["Hild"]
    group = _groups(game)[0]

    judgement = _one(game, [], _on_groups(pf.declare(hild, "attack", weapon_id="sword"), group))

    assert _codes(judgement) == {group: [OUT_OF_REACH]}
    assert judgement.attack_kinds == {group: "melee"}


def test_a_candidate_that_isnt_an_attack_has_no_kinds():
    game = _corridor()
    ids = _ids(game)
    group = _groups(game)[0]
    candidates = [
        _on_groups(pf.declare(ids["Hild"], "move", move="close"), group),
        _on_groups(pf.declare(ids["Wren"], "cast", spell_id="sleep", spell_mode="default"), group),
    ]

    for judgement in _assert_agrees(game, [], candidates).candidates:
        assert judgement.attack_kinds == {}


# ---------------------------------------------------------------------- bad calls and the session


def _bad_candidates(game: GameSession) -> dict[str, BattleCandidate]:
    ids = _ids(game)
    group = _groups(game)[0]
    return {
        "isn't one of this round's declarers": _on_groups(pf.declare("character-9999", "attack"), group),
        "already sets target_group_id": _on_groups(pf.strike(game, ids["Hild"]), group),
        "already sets targets": _on_targets(
            pf.declare(ids["Tam"], "give_item", item_id="torch", targets=(ids["Hild"],)), ids["Osric"]
        ),
        "repeats the target id": _on_groups(pf.declare(ids["Hild"], "attack"), group, group),
        "makes a fighting_withdrawal": _on_groups(_withdraw(ids["Hild"]), group),
        "makes a retreat": _on_groups(_withdraw(ids["Hild"], "retreat"), group),
    }


@pytest.mark.parametrize("reason", list(_bad_candidates(_corridor())))
def test_a_bad_candidate_raises_with_its_position_and_changes_nothing(reason):
    game = _corridor()
    good = _on_groups(pf.declare(_ids(game)["Osric"], "attack", weapon_id="sword"), _groups(game)[0])
    bad = _bad_candidates(game)[reason]
    before = pf.state_without_logs(game)

    with pytest.raises(ValueError) as raised:
        preview_battle_round(game, [], [good, good, bad])

    message = str(raised.value)
    assert type(raised.value) is ValueError
    assert message.startswith("candidate 2 for ")
    assert repr(bad.declaration.character_id) in message
    assert reason in message
    assert pf.state_without_logs(game) == before


def test_a_call_with_candidates_takes_no_draw_and_changes_nothing():
    game = _held(distance=30)
    before = pf.state_without_logs(game)
    log_size = len(game.command_log)
    events = len(game.event_log)

    for plan in _plans(game).values():
        preview_battle_round(game, plan, _candidates(game))

    assert pf.state_without_logs(game) == before
    assert len(game.command_log) == log_size
    assert len(game.event_log) == events


# ---------------------------------------------------------------------- the models


def test_declaration_for_sets_the_named_field_only():
    declaration = pf.declare("character-0001", "cast", spell_id="sleep", spell_mode="default", reversed=False)
    by_group = BattleCandidate(declaration=declaration, target_field="target_group_id", target_ids=("group-0001",))
    by_target = BattleCandidate(declaration=declaration, target_field="targets", target_ids=("monster-0001",))

    assert by_group.declaration_for("group-0002") == declaration.model_copy(update={"target_group_id": "group-0002"})
    assert by_target.declaration_for("monster-0002") == declaration.model_copy(update={"targets": ("monster-0002",)})
    assert by_target.declaration_for("monster-0002").targets == ("monster-0002",)
    assert by_group.declaration == declaration
    assert (
        BattleDeclaration.model_validate(by_group.declaration_for("group-0002").model_dump()).target_group_id
        == "group-0002"
    )


def test_a_preview_without_candidates_parses_with_them_empty():
    document = preview_battle_round(_corridor(), []).model_dump(mode="json")
    document.pop("candidates")

    assert BattleRoundPreview.model_validate(document).candidates == ()


def test_a_judgement_without_attack_kinds_parses_with_them_empty():
    game = _corridor()
    candidate = _on_groups(pf.declare(_ids(game)["Hild"], "attack", weapon_id="sword"), _groups(game)[0])
    document = preview_battle_round(game, [], [candidate]).candidates[0].model_dump(mode="json")
    document.pop("attack_kinds")

    assert BattleCandidateJudgement.model_validate(document).attack_kinds == {}


def test_a_preview_with_candidates_round_trips_through_json():
    game = _corridor()
    preview = preview_battle_round(game, [], _candidates(game))

    assert BattleRoundPreview.model_validate_json(preview.model_dump_json()) == preview


def test_the_models_are_frozen_and_public():
    for model in (BattleCandidate, BattleCandidateJudgement):
        assert model.model_config.get("frozen") is True
        assert model.__name__ in battle_module.__all__
