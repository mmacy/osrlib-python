"""Acceptance tests for who can back-stab, and who can hide beside a planned round (issue #319).

A remote front end builds its battle menu from the player view, so it needs the view to say who may declare a
back-stab. The session accepts an `attack` with `BattleDeclaration.back_stab` when the member's class has the
`back_stab` ability and either the monsters are surprised, in the party's free round, or the member is hiding. The
OSE SRD's thief entry says "When attacking an unaware opponent from behind, a thief receives a +4 bonus to hit and
doubles any damage dealt", and the adaptations register entry "A back-stab needs surprised monsters or a hiding
thief" states osrlib's reading.

**The view.** `EncounterView.can_back_stab` lists, in marching order, the declarers whose back-stab the session would
not refuse with `battle.declaration.back_stab_no_ability` or `battle.declaration.back_stab_target_aware`. It reads
the class definition the character uses, so a custom class works, and it's built from who has hidden, never from the
secret hide in shadows roll. It doesn't read the declaration, so a back-stab by a member in it can still be refused
for the weapon, the group, the reach, or a check every attack takes.

**The preview.** `BattleRoundPreview.can_hide` and `BattleRoundPreview.can_back_stab` are the same lists judged
beside the planned declarations, as the session judges them. Each member is judged as if the `hide` or the back-stab
took the place of their own planned declaration, because a member declares one action in a round. So the lists
answer "may this member declare it in this round", and a member's own planned move never takes them out of
`can_hide`. With no planned move the preview's lists are the view's.

The battles are built by command from seeded sessions, in a 10' corridor two cells long, so the party's front rank
is two abreast and the third member in marching order stands in the second rank. Each helper takes the first seed
that gives the setup it needs, and each test asserts its own preconditions, so a test that stops exercising its case
fails loudly. Every member has 400 hit points, so nobody dies by chance.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.classes import ClassDefinition
from osrlib.core.clock import TimeUnit
from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, EffectDefinition, kill
from osrlib.core.events import Visibility
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.battle import BattleRoundPreview, preview_battle_round
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.events import DetectionRolledEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import EncounterView
from osrlib.data import load_classes

REPO = Path(__file__).resolve().parent.parent


SEEDS = range(0, 400)
"""Seeds the helpers scan for a setup they need, like a battle in which neither side was surprised."""

NO_ABILITY = "battle.declaration.back_stab_no_ability"
TARGET_AWARE = "battle.declaration.back_stab_target_aware"
MEMBER_CODES = {NO_ABILITY, TARGET_AWARE}
"""The two refusals that belong to the member, which `can_back_stab` answers for."""
NOT_A_THIEF = "battle.declaration.hide_not_a_thief"
IN_FRONT_RANK = "battle.declaration.hide_in_front_rank"
NOT_IN_FRONT_RANK = "battle.declaration.not_in_front_rank"

THIEF = load_classes().get("thief")
FIGHTER = load_classes().get("fighter")


def _custom(base: ClassDefinition, class_id: str, **changes) -> ClassDefinition:
    """A class of your own: a built-in class under a new id and name, with `changes` applied."""
    document = {**base.model_dump(), "id": class_id, "name": class_id.title(), **changes}
    return ClassDefinition.model_validate(document)


_BACK_STAB = next(ability for ability in THIEF.abilities if ability.tag == "back_stab")
CUTTHROAT = _custom(FIGHTER, "cutthroat", abilities=(*FIGHTER.abilities, _BACK_STAB))
"""A fighter with the thief's `back_stab` ability and no thief skills: it can back-stab and can't hide."""
SNEAK = _custom(THIEF, "sneak", abilities=tuple(ability for ability in THIEF.abilities if ability.tag != "back_stab"))
"""A thief without the `back_stab` ability: it can hide and can't back-stab."""

TRIO = (("Brakk", FIGHTER), ("Hild", FIGHTER), ("Sable", THIEF))
"""Brakk and Hild side by side in the front rank, and Sable the thief behind them in the second rank."""
SABLE_IN_FRONT = (("Brakk", FIGHTER), ("Sable", THIEF), ("Hild", FIGHTER))
"""Brakk and Sable side by side in the front rank, and Hild behind them."""
WITH_CUTTHROAT = (("Brakk", FIGHTER), ("Nim", CUTTHROAT), ("Sable", THIEF))
"""Brakk and Nim the cutthroat in the front rank, and Sable behind them."""
WITH_SNEAK = (("Brakk", FIGHTER), ("Hild", FIGHTER), ("Nim", SNEAK))
"""Brakk and Hild in the front rank, and Nim the sneak behind them."""


# ---------------------------------------------------------------------- setup


def _character(name: str, definition: ClassDefinition) -> Character:
    built_in = definition.id in {"fighter", "thief"}
    return Character(
        name=name,
        class_id=definition.id,
        custom_class=None if built_in else definition,
        race=definition.race,
        level=7,
        xp=definition.row(7).xp,
        scores={ability: 10 for ability in AbilityScore},
        alignment="lawful",
        max_hp=400,
        current_hp=400,
    )


def _adventure() -> Adventure:
    """One level: a 10' corridor two cells long, so the party's front rank is two abreast."""
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    return Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))


def _spawned(roster, *, first_seed: int, distance_feet: int, monsters_surprised: bool) -> GameSession:
    """The first session at or after `first_seed` whose goblins, spawned in the corridor, surprise as asked.

    The party is never surprised, and the goblins are surprised exactly when `monsters_surprised` is true. The
    session is left in `encounter` mode, or in `battle` mode when the goblins attacked at once.
    """
    for seed in range(first_seed, SEEDS.stop):
        members = [_character(name, definition) for name, definition in roster]
        game = GameSession.new(Party(members=members), _adventure(), seed=seed)
        assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
        result = game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=distance_feet))
        assert result.accepted
        sides = {event.side: event.surprised for event in result.events if event.code == "encounter.surprise.rolled"}
        if sides.get("party") is not False or sides.get("monsters") is not monsters_surprised:
            continue
        return game
    raise AssertionError("precondition: no seed spawned the encounter")


def _battle(
    roster=TRIO, *, first_seed: int = SEEDS.start, distance_feet: int = 5, monsters_surprised: bool = False
) -> GameSession:
    """A battle opened by command in round zero, from the first seed at or after `first_seed` that opens it as asked.

    The monsters are surprised when `monsters_surprised` is true, which gives the party a free round.
    """
    seed = first_seed
    while seed < SEEDS.stop:
        game = _spawned(roster, first_seed=seed, distance_feet=distance_feet, monsters_surprised=monsters_surprised)
        seed = game.master_seed + 1
        if game.mode is SessionMode.ENCOUNTER:
            assert game.execute(EngageBattle()).accepted
        if game.mode is not SessionMode.BATTLE or game.battle.round != 0:
            continue
        assert game.battle.monsters_hold_rounds == (1 if monsters_surprised else 0), "precondition: the free round"
        return game
    raise AssertionError("precondition: no seed opened the battle")


def _named(game: GameSession, name: str) -> str:
    return next(member.id for member in game.party.members if member.name == name)


def _group_id(game: GameSession) -> str:
    return game.encounter.groups[0].id


def _declare(member_id: str, action: str = "hold", **fields) -> BattleDeclaration:
    return BattleDeclaration(character_id=member_id, action=action, **fields)


def _hide(member_id: str) -> BattleDeclaration:
    return _declare(member_id, "hide")


def _stab(game: GameSession, member_id: str) -> BattleDeclaration:
    """A well-formed melee back-stab: an `attack` on the first group, bare-handed, with `back_stab` set."""
    return _declare(member_id, "attack", target_group_id=_group_id(game), back_stab=True)


def _close(game: GameSession, member_id: str) -> BattleDeclaration:
    return _declare(member_id, "move", move="close", target_group_id=_group_id(game))


def _withdraw(member_id: str) -> BattleDeclaration:
    return _declare(member_id, "move", move="fighting_withdrawal")


def _able(game: GameSession) -> list[str]:
    return [member.id for member in game.party.living_members() if not incapacitated(member)]


def _play(game: GameSession, *declared: BattleDeclaration) -> list:
    """Play one round: the members in `declared` as given, every other able member holding."""
    by_id = {declaration.character_id: declaration for declaration in declared}
    able = _able(game)
    assert set(by_id) <= set(able), "precondition: every declared member can act"
    result = game.execute(ResolveBattleRound(declarations=tuple(by_id.get(m, _declare(m)) for m in able)))
    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
    return list(result.events)


def _encounter(game: GameSession) -> EncounterView:
    view = game.view(Visibility.PLAYER).encounter
    assert view is not None
    return view


def _codes(preview: BattleRoundPreview, member_id: str) -> list[str]:
    return [rejection.code for rejection in preview.rejections[member_id]]


def _replaced(plan: list[BattleDeclaration], declaration: BattleDeclaration) -> list[BattleDeclaration]:
    """The plan with `declaration` in place of whatever its member had planned."""
    return [*(planned for planned in plan if planned.character_id != declaration.character_id), declaration]


def _hidden(roster=TRIO, *, name: str = "Sable", passed: bool | None = None, first_seed: int = SEEDS.start):
    """A battle after one round in which `name` hid and everyone else held, with that round's events.

    With `passed` set, it's the first seed whose hide in shadows roll passed, or failed, as asked.
    """
    seed = first_seed
    while seed < SEEDS.stop:
        game = _battle(roster, first_seed=seed)
        seed = game.master_seed + 1
        hider = _named(game, name)
        events = _play(game, _hide(hider))
        (roll,) = [
            event
            for event in events
            if isinstance(event, DetectionRolledEvent)
            and event.kind == "hide_in_shadows"
            and event.character_id == hider
        ]
        if passed is not None and roll.passed is not passed:
            continue
        assert _encounter(game).hiding == (hider,), "precondition: the hider is hiding"
        return game, events
    raise AssertionError("precondition: no seed's hide went as asked")


# ---------------------------------------------------------------------- the scenarios


def _ordinary_round() -> GameSession:
    return _battle()


def _free_round() -> GameSession:
    return _battle(monsters_surprised=True)


def _free_round_with_a_cutthroat() -> GameSession:
    return _battle(WITH_CUTTHROAT, monsters_surprised=True)


def _hiding_thief() -> GameSession:
    return _hidden()[0]


def _attempt_ended() -> GameSession:
    """Sable hid, then back-stabbed from hiding, and the attack coming up ended the attempt."""
    game = _hiding_thief()
    _play(game, _stab(game, _named(game, "Sable")))
    assert _encounter(game).hiding == (), "precondition: the attempt has ended"
    return game


def _asleep_while_hiding() -> GameSession:
    """Sable hid, and has fallen asleep since, before the round's end could end the attempt."""
    game = _hiding_thief()
    sable = _named(game, "Sable")
    asleep = EffectDefinition(kind="sleep", duration_unit=TimeUnit.TURN, duration_amount=4, condition=Condition.ASLEEP)
    effect, _ = game.ledger.attach(asleep, sable, clock=game.clock, allocator=game.allocator, registry=game.registry())
    assert effect is not None and incapacitated(game.member(sable)), "precondition: Sable is asleep"
    assert _encounter(game).hiding == (sable,), "precondition: Sable is still hiding"
    return game


def _dead_in_the_free_round() -> GameSession:
    game = _free_round()
    sable = game.member(_named(game, "Sable"))
    kill(sable)
    assert sable not in game.party.living_members(), "precondition: Sable is dead"
    return game


def _cutthroat_in_an_ordinary_round() -> GameSession:
    return _battle(WITH_CUTTHROAT)


def _sneak_hiding() -> GameSession:
    return _hidden(WITH_SNEAK, name="Nim")[0]


Scenario = Callable[[], GameSession]

SCENARIOS: dict[str, tuple[Scenario, tuple[str, ...], tuple[str, ...]]] = {
    "an ordinary round with nobody hiding": (_ordinary_round, ("Sable",), ()),
    "the party's free round": (_free_round, ("Sable",), ("Sable",)),
    "the free round with a custom back-stabber": (_free_round_with_a_cutthroat, ("Sable",), ("Nim", "Sable")),
    "a hiding thief": (_hiding_thief, ("Sable",), ("Sable",)),
    "a thief whose attempt ended": (_attempt_ended, ("Sable",), ()),
    "a hiding thief who can't act": (_asleep_while_hiding, (), ()),
    "a dead thief in the free round": (_dead_in_the_free_round, (), ()),
    "a custom back-stabber in an ordinary round": (_cutthroat_in_an_ordinary_round, ("Sable",), ()),
    "a custom thief without the ability, hiding": (_sneak_hiding, ("Nim",), ()),
}
"""Each scenario's builder, and the names in its `can_hide` and its `can_back_stab`, in marching order.

These are the cases a client that copies the rule from the class catalog, `party_free_round`, and `hiding` gets
wrong: a thief who isn't hiding in an ordinary round, a thief whose attempt ended, the free round, a member who
can't act or is dead, and the custom classes that the catalog doesn't have."""


def _ids(game: GameSession, names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_named(game, name) for name in names)


# ---------------------------------------------------------------------- the fields


class TestTheFields:
    """The new fields default to empty, so a view or a preview from 2.7.0 still parses."""

    def test_the_view_starts_with_nobody_able_to_back_stab(self):
        assert EncounterView(groups=(), stance=None, in_battle=True).can_back_stab == ()
        assert EncounterView.model_validate({"groups": [], "stance": None, "in_battle": True}).can_back_stab == ()

    def test_a_preview_without_the_lists_parses_with_both_empty(self):
        document = {"rejections": {}, "advance_feet": {}, "front_rank": {}}
        preview = BattleRoundPreview.model_validate(document)
        assert preview.can_hide == ()
        assert preview.can_back_stab == ()

    def test_the_lists_round_trip_through_json(self):
        preview = BattleRoundPreview(
            rejections={}, advance_feet={}, front_rank={}, can_hide=("character-0003",), can_back_stab=("c-1", "c-3")
        )
        assert BattleRoundPreview.model_validate(json.loads(preview.model_dump_json())) == preview
        view = EncounterView(groups=(), stance=None, in_battle=True, can_back_stab=("character-0002",))
        assert EncounterView.model_validate(json.loads(view.model_dump_json())) == view


# ---------------------------------------------------------------------- the view


class TestTheView:
    """`EncounterView.can_back_stab` in each scenario, and its agreement with what the session refuses."""

    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_the_lists_in_each_scenario(self, name):
        build, can_hide, can_back_stab = SCENARIOS[name]
        game = build()
        view = _encounter(game)

        assert view.can_hide == _ids(game, can_hide)
        assert view.can_back_stab == _ids(game, can_back_stab)

    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_the_list_is_who_the_session_wouldnt_refuse_for_the_member(self, name):
        """For every declarer, a back-stab gets neither member code exactly when the declarer is in the list.

        When the declarer can reach the group, because they stand in its front rank, or they're hiding within 5' of
        it, the whole back-stab is accepted exactly when they're in the list. A member who isn't a declarer is never
        in it.
        """
        game = SCENARIOS[name][0]()
        view = _encounter(game)
        group = view.groups[0]

        for member in game.party.members:
            if member.id not in view.declarers:
                assert member.id not in view.can_back_stab
                continue
            preview = preview_battle_round(game, [_stab(game, member.id)])
            codes = _codes(preview, member.id)
            assert (member.id in view.can_back_stab) is not bool(MEMBER_CODES & set(codes)), (member.name, codes)
            hiding_in_reach = member.id in view.hiding and abs(group.member_distance_feet[member.id]) <= 5
            if member.id in group.front_rank or hiding_in_reach:
                assert (member.id in view.can_back_stab) is (codes == []), (member.name, codes)

    def test_the_free_round_lists_a_thief_who_cant_reach_and_the_session_says_why(self):
        """Where the line falls: in the free round, a back-stab from the second rank is refused for the declaration."""
        game = _free_round()
        sable = _named(game, "Sable")
        view = _encounter(game)
        assert sable not in view.groups[0].front_rank, "precondition: Sable stands in the second rank"

        assert view.can_back_stab == (sable,)
        assert _codes(preview_battle_round(game, [_stab(game, sable)]), sable) == [NOT_IN_FRONT_RANK]

    def test_a_blind_thief_in_hiding_is_listed_and_refused_by_the_attack_check(self):
        """Where the line falls: blindness stops any attack, and the list answers for the back-stab's own checks."""
        game = _hiding_thief()
        sable = _named(game, "Sable")
        blind = EffectDefinition(
            kind="blindness", duration_unit=TimeUnit.TURN, duration_amount=4, condition=Condition.BLIND
        )
        effect, _ = game.ledger.attach(
            blind, sable, clock=game.clock, allocator=game.allocator, registry=game.registry()
        )
        assert effect is not None, "precondition: Sable is blind"

        assert _encounter(game).can_back_stab == (sable,)
        assert _codes(preview_battle_round(game, [_stab(game, sable)]), sable) == ["combat.attack.attacker_blind"]

    def test_the_list_never_tells_how_the_hide_went(self):
        passed, _ = _hidden(passed=True)
        failed, _ = _hidden(passed=False)

        for game in (passed, failed):
            assert _encounter(game).can_back_stab == (_named(game, "Sable"),)

    def test_custom_classes_follow_the_abilities_the_session_reads(self):
        """A cutthroat can back-stab in the free round and can't hide. A sneak can hide and can't back-stab."""
        free = _free_round_with_a_cutthroat()
        nim = _named(free, "Nim")
        assert nim in _encounter(free).can_back_stab
        assert nim not in _encounter(free).can_hide
        assert _codes(preview_battle_round(free, [_hide(nim)]), nim) == [NOT_A_THIEF]

        hiding = _sneak_hiding()
        nim = _named(hiding, "Nim")
        assert nim in _encounter(hiding).hiding, "precondition: Nim is hiding"
        assert nim in _encounter(hiding).can_hide
        assert nim not in _encounter(hiding).can_back_stab
        assert _codes(preview_battle_round(hiding, [_stab(hiding, nim)]), nim) == [NO_ABILITY]

    def test_outside_a_battle_both_lists_are_empty(self):
        """A guard: an encounter that isn't a battle yet lists nobody, even when the party surprised the monsters.

        Hiding and back-stabbing are battle declarations, so neither list has anyone until the battle starts.
        Outside an encounter the player view has no encounter at all.
        """
        for monsters_surprised in (False, True):
            seed = SEEDS.start
            while True:
                game = _spawned(TRIO, first_seed=seed, distance_feet=30, monsters_surprised=monsters_surprised)
                seed = game.master_seed + 1
                if game.mode is SessionMode.ENCOUNTER:
                    break
            view = _encounter(game)
            assert not view.in_battle, "precondition: the encounter isn't a battle"

            assert view.can_hide == ()
            assert view.can_back_stab == ()

        exploring = GameSession.new(Party(members=[_character("Sable", THIEF)]), _adventure(), seed=1)
        assert exploring.execute(EnterDungeon(dungeon_id="crypt")).accepted
        assert exploring.view(Visibility.PLAYER).encounter is None


# ---------------------------------------------------------------------- the preview


def _plans(game: GameSession) -> dict[str, list[BattleDeclaration]]:
    """Plans for the equivalence test: none, holds, one member's move, and a formation move of every declarer."""
    able = _able(game)
    plans = {
        "no plan": [],
        "every declarer holds": [_declare(member_id) for member_id in able],
        "the first declarer withdraws": [_withdraw(able[0])],
        "the last declarer closes": [_close(game, able[-1])],
        "every declarer withdraws": [_withdraw(member_id) for member_id in able],
    }
    return plans


class TestThePreview:
    """`BattleRoundPreview.can_hide` and `can_back_stab`, judged beside the planned declarations."""

    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_with_no_planned_move_the_lists_are_the_views(self, name):
        game = SCENARIOS[name][0]()
        view = _encounter(game)

        for plan in ([], [_declare(member_id) for member_id in view.declarers]):
            preview = preview_battle_round(game, plan)
            assert preview.can_hide == view.can_hide
            assert preview.can_back_stab == view.can_back_stab

    @pytest.mark.parametrize("name", list(SCENARIOS))
    def test_each_list_is_what_the_member_could_declare_in_place_of_their_plan(self, name):
        """For every plan and every declarer, membership matches the preview with that member's declaration replaced.

        A declarer is in `can_hide` exactly when a `hide` in place of their planned declaration has no rejection,
        and in `can_back_stab` exactly when a back-stab in its place gets neither member code. The formation move
        of every declarer is in the plans, because replacing one declaration breaks it.
        """
        game = SCENARIOS[name][0]()
        declarers = _encounter(game).declarers

        for label, plan in _plans(game).items():
            preview = preview_battle_round(game, plan)
            for member_id in declarers:
                hide_codes = _codes(preview_battle_round(game, _replaced(plan, _hide(member_id))), member_id)
                stab_codes = _codes(preview_battle_round(game, _replaced(plan, _stab(game, member_id))), member_id)
                assert (member_id in preview.can_hide) is (hide_codes == []), (label, member_id, hide_codes)
                assert (member_id in preview.can_back_stab) is not bool(MEMBER_CODES & set(stab_codes)), (
                    label,
                    member_id,
                )
            assert set(preview.can_hide) <= set(declarers)
            assert preview.can_hide == tuple(member_id for member_id in declarers if member_id in preview.can_hide)
            assert preview.can_back_stab == tuple(m for m in declarers if m in preview.can_back_stab)
            assert preview.can_back_stab == _encounter(game).can_back_stab, label

    def test_another_members_withdrawal_makes_a_hide_illegal(self):
        """Brakk falls back alone, which leaves Sable beside Hild in the first row, so Sable can't hide."""
        game = _ordinary_round()
        sable, brakk = _named(game, "Sable"), _named(game, "Brakk")
        assert _encounter(game).can_hide == (sable,), "precondition: Sable can hide where the party stands now"

        assert preview_battle_round(game, [_declare(brakk)]).can_hide == (sable,)
        withdrawn = preview_battle_round(game, [_withdraw(brakk)])
        assert withdrawn.can_hide == ()
        assert _codes(preview_battle_round(game, [_withdraw(brakk), _hide(sable)]), sable) == [IN_FRONT_RANK]

    def test_another_members_close_makes_a_hide_legal(self):
        """Hild closes on the goblins 30' away and takes Sable's place in the first row, so Sable can hide."""
        game = _battle(SABLE_IN_FRONT, distance_feet=30)
        sable, hild = _named(game, "Sable"), _named(game, "Hild")
        assert _encounter(game).can_hide == (), "precondition: Sable stands in the front rank"

        assert preview_battle_round(game, [_close(game, hild)]).can_hide == (sable,)
        result = game.execute(
            ResolveBattleRound(
                declarations=(_declare(_named(game, "Brakk")), _hide(sable), _close(game, hild)),
            )
        )
        assert result.accepted, [rejection.code for rejection in result.rejections]

    def test_a_members_own_close_doesnt_take_them_out_of_can_hide(self):
        """Sable plans to close, which would put Sable in the first row, and Sable could still hide instead."""
        game = _battle(distance_feet=30)
        sable = _named(game, "Sable")
        plan = [_close(game, sable)]
        preview = preview_battle_round(game, plan)
        assert sable in preview.front_rank[_group_id(game)], "precondition: the close puts Sable in the front rank"

        assert preview.can_hide == (sable,)
        assert _codes(preview_battle_round(game, [_hide(sable)]), sable) == []

    def test_a_member_who_plans_a_hide_hasnt_hidden_yet(self):
        """Sable's planned hide is accepted, so Sable is in `can_hide`, and not yet in `can_back_stab`."""
        game = _ordinary_round()
        sable = _named(game, "Sable")
        preview = preview_battle_round(game, [_hide(sable)])
        assert _codes(preview, sable) == [], "precondition: the hide is accepted"

        assert preview.can_hide == (sable,)
        assert preview.can_back_stab == ()

    def test_a_hiding_member_who_plans_a_move_can_still_back_stab_this_round(self):
        """Sable's withdrawal will end the attempt when it comes up, but the round is judged before it resolves."""
        game = _hiding_thief()
        sable = _named(game, "Sable")

        for plan in ([_withdraw(sable)], [_hide(sable)], [_close(game, _named(game, "Brakk"))]):
            assert preview_battle_round(game, plan).can_back_stab == (sable,), plan

    def test_a_planned_move_doesnt_change_who_can_back_stab_in_the_free_round(self):
        game = _free_round_with_a_cutthroat()
        expected = (_named(game, "Nim"), _named(game, "Sable"))

        for plan in _plans(game).values():
            assert preview_battle_round(game, plan).can_back_stab == expected


# ---------------------------------------------------------------------- the guides


def _released_in_2_8_0() -> str:
    changelog = (REPO / "CHANGELOG.md").read_text()
    match = re.search(r"^## \[2\.8\.0\][^\n]*\n(.*?)^## \[", changelog, flags=re.S | re.M)
    assert match is not None
    return match.group(1)


def test_the_changelog_records_the_new_lists():
    released = _released_in_2_8_0()
    bullet = next((line for line in released.splitlines() if "(#319)" in line), "")
    assert "EncounterView.can_back_stab" in bullet
    assert "BattleRoundPreview.can_hide" in bullet
    assert "BattleRoundPreview.can_back_stab" in bullet


def test_the_view_guide_points_the_battle_menu_at_the_lists():
    guide = (REPO / "docs" / "guides" / "views-and-visibility.md").read_text()
    assert "[`can_back_stab`][osrlib.crawl.views.EncounterView.can_back_stab]" in guide
    assert "osrlib.crawl.battle.BattleRoundPreview.can_hide" in guide
    assert "osrlib.crawl.battle.BattleRoundPreview.can_back_stab" in guide
