"""A battle played from the player view alone: sleep aimed at a group, and a group that has fled.

Both classes come from issue #162, items 16 and 17. A front end built on
[`PlayerView`][osrlib.crawl.views.PlayerView] sees group ids and never monster ids, so every
declaration it makes has to work from a group id, and every group it offers has to be one the
session accepts a declaration against.

The party is the milestone party, built from the golden's seed: a 3rd-level fighter, elf, thief, and
cleric, and a 5th-level magic-user. The elf has *sleep* memorized and the magic-user has *fire ball*.
The tests give the fighter and the elf a sword each and the thief a flask of oil, and light a torch.
"""

import pytest

import positions_fixtures as pf
from crawl_fixtures import build_adventure, build_milestone_party
from osrlib.core.combat import TargetingMode, effective_hd, select_targets
from osrlib.core.effects import ActiveCondition, Condition, has_condition
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import ReactionResult
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.battle import FLEE_EXIT_FEET, MELEE_RANGE_FEET, group_front_rank, preview_battle_round
from osrlib.crawl.commands import (
    BattleDeclaration,
    CommandResult,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    GrantItem,
    LightSource,
    ReorderParty,
    ResolveBattleRound,
    SessionMode,
    SpawnNpcParty,
)
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import EncounterGroupView, EncounterView, PlayerView

PARTY_SEED = 20260715
"""The milestone golden's master seed, which `build_milestone_party` is known to build a party from."""

FIGHTER = "character-0001"
ELF = "character-0002"
THIEF = "character-0003"
CLERIC = "character-0004"
MAGIC_USER = "character-0005"

UNKNOWN_GROUP = "battle.declaration.unknown_group"

CASTER_BEHIND = (FIGHTER, THIEF, ELF, CLERIC, MAGIC_USER)
"""A marching order that keeps the elf out of the front rank, so no melee blow disrupts its *sleep*."""


# ---------------------------------------------------------------------- session helpers


def _delve(seed: int, order: tuple[str, ...] | None = None) -> GameSession:
    """The milestone party in the delve, torch lit, fighter and elf armed, the thief carrying oil."""
    session = GameSession.new(
        Party(members=build_milestone_party(PARTY_SEED)), build_adventure(wandering_chance=0), seed=seed
    )
    session.execute(GrantItem(character_id=FIGHTER, item_id="torch", quantity=6))
    session.execute(GrantItem(character_id=FIGHTER, item_id="tinder_box"))
    for member_id in (FIGHTER, ELF):
        assert session.execute(GrantItem(character_id=member_id, item_id="sword")).accepted
        assert session.execute(EquipItem(character_id=member_id, item_id="sword")).accepted
    assert session.execute(GrantItem(character_id=THIEF, item_id="oil_flask")).accepted
    if order is not None:
        assert session.execute(ReorderParty(order=order)).accepted
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
    for _ in range(20):
        lit = session.execute(LightSource(character_id=FIGHTER, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    lit_now, _ = session.party_light()
    assert lit_now, "precondition: the party carries a lit torch"
    assert [spell.spell_id for spell in session.member(ELF).memorized_spells] == ["sleep"]
    assert "fire_ball" in [spell.spell_id for spell in session.member(MAGIC_USER).memorized_spells]
    return session


def _battle(
    seed: int, groups: tuple[tuple[str, int], ...], distance: int, order: tuple[str, ...] | None = None
) -> GameSession:
    """A hostile encounter with the listed groups at one distance, engaged into a battle."""
    session = _delve(seed, order)
    pairs = [(template_id, session.spawn(template_id, count)) for template_id, count in groups]
    encounter_module.start_encounter(
        session,
        groups=pairs,
        kind="spawned",
        distance_feet=distance,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    if session.mode is SessionMode.ENCOUNTER:
        assert session.execute(EngageBattle()).accepted
    assert session.mode is SessionMode.BATTLE, "precondition: the encounter became a battle"
    assert [len(group.monster_ids) for group in session.encounter.groups] == [count for _, count in groups]
    return session


def _encounter(session: GameSession) -> EncounterView:
    view = session.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    assert view.encounter is not None
    return view.encounter


def _with_holds(session: GameSession, *declarations: BattleDeclaration) -> tuple[BattleDeclaration, ...]:
    """The given declarations, and a `hold` for every other declarer the player view lists."""
    covered = {declaration.character_id for declaration in declarations}
    holds = [
        BattleDeclaration(character_id=member_id, action="hold")
        for member_id in _encounter(session).declarers
        if member_id not in covered
    ]
    return (*declarations, *holds)


def _round(session: GameSession, *declarations: BattleDeclaration) -> CommandResult:
    for declaration in declarations:
        assert declaration.character_id in _encounter(session).declarers, "precondition: the member can declare"
    return session.execute(ResolveBattleRound(declarations=_with_holds(session, *declarations)))


def _sleep(*, group_id: str | None = None, targets: tuple[str, ...] = ()) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=ELF,
        action="cast",
        spell_id="sleep",
        spell_mode="hd_budget",
        target_group_id=group_id,
        targets=targets,
    )


def _slept(result: CommandResult) -> list[str]:
    return [
        event.target_id
        for event in result.events
        if event.code == "effects.condition.gained" and event.condition == Condition.ASLEEP
    ]


def _selected(result: CommandResult) -> tuple[str, ...]:
    selections = [
        event for event in result.events if event.code == "combat.targeting.selected" and event.mode == "hd_budget"
    ]
    assert len(selections) == 1, "the cast selected its targets once, by Hit Dice budget"
    return selections[0].target_ids


def _living_ids(session: GameSession, group) -> tuple[str, ...]:
    return tuple(
        monster_id
        for monster_id in group.monster_ids
        if not has_condition(session.combatant(monster_id), Condition.DEAD)
    )


def _sleep_copies(session: GameSession) -> int:
    return [spell.spell_id for spell in session.member(ELF).memorized_spells].count("sleep")


def _snapshot(session: GameSession) -> dict:
    """Everything a refused round must leave as it was: draws, time, distances, memory, and the log."""
    return {
        "streams": session.streams.export_states(),
        "rounds": session.clock.rounds,
        "battle_round": session.battle.round,
        "distances": [(group.id, group.distance_feet) for group in session.encounter.groups],
        "memorized": {member.id: list(member.memorized_spells) for member in session.party.members},
        "event_log": len(session.event_log),
    }


def _assert_refused_whole(session: GameSession, result: CommandResult, before: dict, rejections: list) -> None:
    assert not result.accepted
    assert result.events == ()
    assert [(rejection.code, dict(rejection.params)) for rejection in result.rejections] == rejections
    assert _snapshot(session) == before, "a refused round costs no draw, no time, and changes nothing"


def _fingerprint(session: GameSession, result: CommandResult) -> dict:
    """What a resolved round did and left behind, for comparing two runs."""
    return {
        "accepted": result.accepted,
        "events": [event.model_dump(mode="json") for event in result.events],
        "streams": session.streams.export_states(),
        "view": session.view(Visibility.PLAYER).model_dump(mode="json"),
    }


# ---------------------------------------------------------------------- item 16: an hd_budget spell at a group


def _goblins_one_slain() -> GameSession:
    """Goblins at melee range, attacked until one is dead and the rest are still in the fight."""
    for seed in range(1, 41):
        session = _battle(seed, (("goblin", 4),), distance=MELEE_RANGE_FEET, order=CASTER_BEHIND)
        assert ELF not in _encounter(session).front_rank
        for _ in range(6):
            group = session.encounter.groups[0]
            if group.distance_feet > MELEE_RANGE_FEET:
                break
            # The elf keeps its sleep for the test: only the fighter swings.
            swing = BattleDeclaration(
                character_id=FIGHTER, action="attack", target_group_id=group.id, weapon_id="sword"
            )
            result = _round(session, swing)
            assert result.accepted, [rejection.code for rejection in result.rejections]
            if session.mode is not SessionMode.BATTLE:
                break
            living = _living_ids(session, group)
            if 0 < len(living) < len(group.monster_ids) and not group.fled and not group.fleeing:
                return session
    pytest.fail("no seed in range left a goblin slain with the rest still fighting")


def _battle_three_goblins(seed: int = 5) -> GameSession:
    return _battle(seed, (("goblin", 3),), distance=40, order=CASTER_BEHIND)


def _battle_twelve_goblins(seed: int = 7) -> GameSession:
    return _battle(seed, (("goblin", 12),), distance=40, order=CASTER_BEHIND)


GROUP_SHAPES = {
    "three goblins": _battle_three_goblins,
    "twelve goblins, more than the budget can always pay for": _battle_twelve_goblins,
    "goblins with one slain": _goblins_one_slain,
}


class TestAnHdBudgetSpellAimedAtAGroup:
    """Issue #162, item 16: *sleep* in battle, declared from the player view.

    The OSE SRD's *sleep* page reads: "The spell may target either: ... A total of 2d8 Hit Dice of
    creatures of 4 HD or lower each", with "**Weakest first:** Targets with the least HD are affected
    first." A player-side front end knows a group's id and nothing about its members, so a `cast` of an
    `hd_budget` mode declared with `target_group_id` and no `targets` spreads the budget over that
    group's living members, in the group's spawn order, through the kernel's own target selection.
    The result is exactly the cast that names every living member of the group explicitly.

    A declaration that names explicit `targets` keeps today's meaning, with or without a
    `target_group_id` beside them, so a saved command log replays as it did.
    """

    def test_a_sleep_aimed_at_a_group_needs_only_the_player_view(self):
        session = _battle_three_goblins()
        view = session.view(Visibility.PLAYER)
        goblin_ids = tuple(session.encounter.groups[0].monster_ids)
        assert not any(monster_id in view.model_dump_json() for monster_id in goblin_ids), (
            "precondition: the player view shows no monster ids"
        )
        group = view.encounter.groups[0]
        assert group.count == 3 and group.visible_conditions == ()

        result = _round(session, _sleep(group_id=group.id))

        assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
        selected = _selected(result)
        assert selected, "a 2d8 budget always pays for at least one 1 HD goblin"
        assert set(selected) <= set(goblin_ids)
        assert _slept(result) == list(selected)
        assert _sleep_copies(session) == 0, "the cast spends the memorized copy"
        after = _encounter(session).groups[0]
        assert after.id == group.id
        assert "asleep" in after.visible_conditions

    @pytest.mark.parametrize("shape", list(GROUP_SHAPES))
    def test_aiming_at_a_group_is_naming_every_living_member(self, shape):
        build = GROUP_SHAPES[shape]
        by_group = build()
        by_name = build()
        group = by_group.encounter.groups[0]
        living = _living_ids(by_name, by_name.encounter.groups[0])
        assert living, "precondition: the group has living members"
        assert _snapshot(by_group) == _snapshot(by_name), "precondition: the two runs start alike"

        aimed = _round(by_group, _sleep(group_id=group.id))
        named = _round(by_name, _sleep(targets=living))

        assert named.accepted, "precondition: naming every living member is accepted today"
        assert _selected(named), "precondition: the named cast put someone to sleep"
        assert _fingerprint(by_group, aimed) == _fingerprint(by_name, named)

    def test_the_budget_goes_weakest_first_through_a_mixed_group(self):
        session = _delve(seed=2, order=CASTER_BEHIND)
        assert session.execute(SpawnNpcParty(party_kind="basic", distance_feet=60)).accepted
        if session.mode is SessionMode.ENCOUNTER:
            assert session.execute(EngageBattle()).accepted
        assert session.mode is SessionMode.BATTLE, "precondition: the adventurers fight"
        group = session.encounter.groups[0]
        members = [session.combatant(member_id) for member_id in _living_ids(session, group)]
        levels = [effective_hd(member) for member in members]
        assert len(set(levels)) > 1, "precondition: the group mixes levels"
        assert sorted(levels) != levels, "precondition: spawn order is not weakest first"
        assert max(levels) <= 4, "precondition: every member is within sleep's 4 HD cap"

        result = _round(session, _sleep(group_id=_encounter(session).groups[0].id))

        assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
        assert _living_ids(session, group) == tuple(member.id for member in members), (
            "precondition: nobody in the group died this round"
        )
        selected = _selected(result)
        stream = RngStreams(master_seed=0).get("unused")
        kernel_choices = {
            tuple(
                target.id for target in select_targets(TargetingMode.HD_BUDGET, members, stream=stream, hd_budget=b)[0]
            )
            for b in range(2, 17)
        }
        assert selected in kernel_choices, "the kernel's weakest-first choice over the group, for a 2d8 budget"
        assert _slept(result) == list(selected)

    def test_another_group_stays_awake(self):
        session = _battle(5, (("goblin", 2), ("kobold", 4)), distance=40, order=CASTER_BEHIND)
        goblins, kobolds = session.encounter.groups
        groups = _encounter(session).groups
        assert [group.id for group in groups] == [goblins.id, kobolds.id]

        result = _round(session, _sleep(group_id=groups[1].id))

        assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
        slept = _slept(result)
        assert slept
        assert set(slept) <= set(kobolds.monster_ids)
        assert not any(has_condition(session.combatant(goblin), Condition.ASLEEP) for goblin in goblins.monster_ids)

    def test_an_undead_group_spends_the_copy_and_sleeps_nobody(self):
        session = _battle(5, (("skeleton", 3),), distance=40, order=CASTER_BEHIND)
        group = _encounter(session).groups[0]

        result = _round(session, _sleep(group_id=group.id))

        assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
        assert _slept(result) == []
        assert "magic.cast.no_effect" in [event.code for event in result.events]
        assert _sleep_copies(session) == 0

    def test_a_group_not_in_the_encounter_is_refused_whole(self):
        session = _battle_three_goblins()
        before = _snapshot(session)

        result = _round(session, _sleep(group_id="group-9999"))

        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == [UNKNOWN_GROUP]
        assert result.events == ()
        assert _snapshot(session) == before

    @pytest.mark.parametrize(
        "aim",
        ["group", "every living member"],
    )
    def test_the_same_seed_gives_the_same_round(self, aim):
        runs = []
        for _ in range(2):
            session = _battle_twelve_goblins()
            group = session.encounter.groups[0]
            if aim == "group":
                declaration = _sleep(group_id=group.id)
            else:
                declaration = _sleep(targets=_living_ids(session, group))
            result = _round(session, declaration)
            assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
            runs.append(_fingerprint(session, result))
        assert runs[0] == runs[1]

    def test_naming_one_monster_sleeps_only_that_monster(self):
        session = _battle_three_goblins()
        first = session.encounter.groups[0].monster_ids[0]

        result = _round(session, _sleep(targets=(first,)))

        assert result.accepted
        assert _selected(result) == (first,)
        assert _slept(result) == [first]

    def test_named_targets_win_over_a_group_id_beside_them(self):
        session = _battle_three_goblins()
        group = session.encounter.groups[0]
        first = group.monster_ids[0]

        result = _round(session, _sleep(group_id=group.id, targets=(first,)))

        assert result.accepted
        assert _selected(result) == (first,)
        assert _slept(result) == [first]

    def test_naming_neither_targets_nor_a_group_is_refused_whole(self):
        session = _battle_three_goblins()
        before = _snapshot(session)

        result = _round(session, _sleep())

        _assert_refused_whole(
            session,
            result,
            before,
            [("magic.cast.target_count", {"mode": "hd_budget", "expected": 1, "supplied": 0})],
        )


# ---------------------------------------------------------------------- item 17: a fled group


def _goblins_breaking(stage: str) -> GameSession:
    """Two goblins and six hobgoblins at melee range. The front rank cuts at the goblins until they break.

    `stage` is `"fleeing"` for the first state in which the goblins are running but still on the range
    track, or `"fled"` for the first in which they have run past `FLEE_EXIT_FEET` and left the fight. In
    both, the battle goes on against the hobgoblins, which have not broken.
    """
    for seed in range(1, 41):
        session = _battle(seed, (("goblin", 2), ("hobgoblin", 6)), distance=MELEE_RANGE_FEET)
        for _ in range(12):
            goblins = session.encounter.groups[0]
            attacks = [
                BattleDeclaration(
                    character_id=member_id, action="attack", target_group_id=goblins.id, weapon_id="sword"
                )
                for member_id in (FIGHTER, ELF)
                if goblins.distance_feet <= MELEE_RANGE_FEET and not goblins.fled
            ]
            result = _round(session, *attacks)
            assert result.accepted, [rejection.code for rejection in result.rejections]
            if session.mode is not SessionMode.BATTLE:
                break
            goblins, hobgoblins = session.encounter.groups
            if hobgoblins.fled or hobgoblins.fleeing or not _living_ids(session, hobgoblins):
                break
            if not _living_ids(session, goblins):
                break
            if stage == "fleeing" and goblins.fleeing and not goblins.fled:
                assert MELEE_RANGE_FEET < goblins.distance_feet <= FLEE_EXIT_FEET
                return session
            if stage == "fled" and goblins.fled:
                assert goblins.distance_feet > FLEE_EXIT_FEET
                return session
    pytest.fail(f"no seed in range reached the goblins' {stage} stage with the battle still on")


def _fled_flag(group: EncounterGroupView) -> bool:
    assert "fled" in EncounterGroupView.model_fields, "the player view has a field that marks a fled group"
    return group.fled


def _arm_for_every_kind(session: GameSession) -> None:
    """Give the casters the magic items whose declarations name a group."""
    session.member(MAGIC_USER).inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9001", template_id="wand_of_cold", identified=True, charges_remaining=5
        )
    )
    session.member(MAGIC_USER).inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9002",
            template_id="spell_scroll_1",
            state={"spell_list": "magic_user", "spells": ("fire_ball",)},
        )
    )
    # The staff is a melee weapon, so it goes to the elf, who stands in the front rank.
    assert ELF in _encounter(session).front_rank
    session.member(ELF).inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9003", template_id="staff_of_striking", identified=True, charges_remaining=5
        )
    )


DECLARATION_KINDS = {
    "attack": lambda group_id: BattleDeclaration(
        character_id=FIGHTER, action="attack", target_group_id=group_id, weapon_id="sword"
    ),
    "move close": lambda group_id: BattleDeclaration(
        character_id=FIGHTER, action="move", move="close", target_group_id=group_id
    ),
    "thrown item": lambda group_id: BattleDeclaration(
        character_id=THIEF, action="use_item", item_id="oil_flask", target_group_id=group_id
    ),
    "wand that covers an area": lambda group_id: BattleDeclaration(
        character_id=MAGIC_USER, action="use_item", item_id="magic-item-9001", target_group_id=group_id
    ),
    "staff that strikes": lambda group_id: BattleDeclaration(
        character_id=ELF, action="use_item", item_id="magic-item-9003", target_group_id=group_id
    ),
    "area spell": lambda group_id: BattleDeclaration(
        character_id=MAGIC_USER, action="cast", spell_id="fire_ball", spell_mode="damage", target_group_id=group_id
    ),
    "area spell read off a scroll": lambda group_id: BattleDeclaration(
        character_id=MAGIC_USER,
        action="use_item",
        item_id="magic-item-9002",
        spell_id="fire_ball",
        target_group_id=group_id,
    ),
    "hd_budget spell": lambda group_id: _sleep(group_id=group_id),
}


class TestAFledGroup:
    """Issue #162, item 17: the player view marks a fled group, and every declaration at one is refused.

    The engine already treats a fled group as gone: `FLEE_EXIT_FEET` says the session rejects an
    attack at one with `battle.declaration.unknown_group`, a fled group takes no further action, the
    party's movement skips it, and the battle ends once every group is fled or dead. So the outcome
    pinned here extends that refusal to every declaration that names a group: an attack, a `close`
    move, a thrown item, a wand, staff, or rod, an area spell cast or read off a scroll, and an
    `hd_budget` spell aimed at the group. Each is refused whole with `battle.declaration.unknown_group`
    and the declarer in the params, as the attack refusal is today, and costs nothing.

    The player view keeps a fled group in `EncounterView.groups`, in the encounter's order, and adds an
    `EncounterGroupView.fled` field, `False` by default, so a front end can tell it apart and stop
    offering it. A group that is running but still on the range track is not fled, because the party
    can still chase it or shoot at it.
    """

    @pytest.mark.parametrize("kind", list(DECLARATION_KINDS))
    def test_every_declaration_at_a_fled_group_is_refused_alike(self, kind):
        session = _goblins_breaking("fled")
        _arm_for_every_kind(session)
        fled_id = session.encounter.groups[0].id
        declaration = DECLARATION_KINDS[kind](fled_id)
        before = _snapshot(session)

        result = _round(session, declaration)

        _assert_refused_whole(session, result, before, [(UNKNOWN_GROUP, {"character": declaration.character_id})])

    @pytest.mark.parametrize(
        "kind",
        list(DECLARATION_KINDS),
    )
    def test_the_same_declaration_at_the_group_still_fighting_is_accepted(self, kind):
        session = _goblins_breaking("fled")
        _arm_for_every_kind(session)
        hobgoblins = session.encounter.groups[1]
        assert not hobgoblins.fled and hobgoblins.distance_feet == MELEE_RANGE_FEET

        result = _round(session, DECLARATION_KINDS[kind](hobgoblins.id))

        assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]

    def test_the_player_view_still_lists_a_fled_group_in_order(self):
        session = _goblins_breaking("fled")
        goblins, hobgoblins = session.encounter.groups

        groups = _encounter(session).groups

        assert [group.id for group in groups] == [goblins.id, hobgoblins.id]
        assert groups[0].distance_feet == goblins.distance_feet > FLEE_EXIT_FEET

    def test_the_player_view_marks_a_fled_group(self):
        session = _goblins_breaking("fled")
        goblins, hobgoblins = session.encounter.groups
        assert goblins.fled and not hobgoblins.fled

        groups = {group.id: group for group in _encounter(session).groups}

        assert _fled_flag(groups[goblins.id]) is True
        assert _fled_flag(groups[hobgoblins.id]) is False

    def test_a_group_still_running_is_not_marked_fled(self):
        session = _goblins_breaking("fleeing")
        goblins = session.encounter.groups[0]
        assert goblins.fleeing and not goblins.fled

        assert _fled_flag(_encounter(session).groups[0]) is False

    @pytest.mark.parametrize("kind", ["move close", "area spell", "hd_budget spell"])
    def test_a_group_still_running_can_still_be_named(self, kind):
        session = _goblins_breaking("fleeing")
        goblins = session.encounter.groups[0]
        assert goblins.fleeing and not goblins.fled

        result = _round(session, DECLARATION_KINDS[kind](goblins.id))

        assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]

    def test_the_player_view_alone_offers_only_close_moves_the_session_accepts(self):
        offered = _encounter(_goblins_breaking("fled")).groups
        assert len(offered) == 2
        for position, group in enumerate(offered):
            session = _goblins_breaking("fled")
            assert _encounter(session).groups[position].id == group.id

            result = _round(session, DECLARATION_KINDS["move close"](group.id))

            assert result.accepted is not _fled_flag(group), (group.id, result.rejections)

    def test_the_fled_field_is_additive(self):
        field = EncounterGroupView.model_fields.get("fled")
        assert field is not None
        assert field.default is False
        older = EncounterGroupView.model_validate(
            {"id": "group-0001", "label": "goblin", "count": 2, "distance_feet": 30, "visible_conditions": []}
        )
        assert _fled_flag(older) is False
        view = _goblins_breaking("fled").view(Visibility.PLAYER)
        assert PlayerView.model_validate_json(view.model_dump_json()) == view
        assert _fled_flag(view.encounter.groups[0]) is True


# ---------------------------------------------------------------------- issue #209: positions in the player view

VIEW = pytest.mark.xfail(reason="chunk: view-and-codes", strict=True)

MISSILE_SPELLS = {"Wren": ("magic_missile", "magic_missile")}


def _ids(game: GameSession, *names: str) -> tuple[str, ...]:
    return tuple(pf.named(game, name) for name in names)


def _held(**battle_args) -> GameSession:
    game = pf.battle(**battle_args)
    pf.hold_monsters(game)
    return game


def _living_goblin(game: GameSession) -> str:
    return next(
        monster_id
        for monster_id in game.encounter.groups[0].monster_ids
        if not has_condition(game.combatant(monster_id), Condition.DEAD)
    )


def _missile(game: GameSession, caster_id: str) -> BattleDeclaration:
    return pf.declare(
        caster_id, "cast", spell_id="magic_missile", spell_mode="missiles", targets=(_living_goblin(game),)
    )


@VIEW
class TestThePositionsInThePlayerView:
    """A battle screen draws the fight and offers each member's choices from the player view alone."""

    def test_each_group_lists_every_living_members_distance(self):
        game = _held(distance=30)
        nim = pf.named(game, "Nim")
        pf.play(game, pf.close(game, nim))

        group = _encounter(game).groups[0]

        assert group.member_distance_feet == {
            member.id: 30 - pf.advance(game, member.id) for member in game.party.members
        }
        assert group.member_distance_feet[nim] == 5

    def test_each_group_lists_who_can_fight_it_hand_to_hand(self):
        game = _held(distance=30)
        nim = pf.named(game, "Nim")
        assert _encounter(game).groups[0].front_rank == (), "nobody within reach yet"

        pf.play(game, pf.close(game, nim))

        assert _encounter(game).groups[0].front_rank == (nim,) == group_front_rank(game, pf.group_id(game))

    def test_the_encounter_front_rank_is_the_first_row_toward_the_nearest_group(self):
        game = _held(distance=30)
        hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")
        assert _encounter(game).front_rank == (hild, osric), "before anyone moves, the marching order's first rank"

        pf.play(game, pf.close(game, nim))

        assert _encounter(game).front_rank == (nim,)

    def test_advance_feet_lists_every_living_member(self):
        game = _held(distance=30)
        nim = pf.named(game, "Nim")
        assert _encounter(game).advance_feet == {member.id: 0 for member in game.party.members}

        pf.play(game, pf.close(game, nim))

        assert _encounter(game).advance_feet == {
            member.id: (25 if member.id == nim else 0) for member in game.party.members
        }

    @pytest.mark.parametrize("name", ["Hild", "Osric", "Nim", "Wren", "Tam"])
    def test_the_group_front_rank_is_exactly_who_may_strike_it(self, name):
        """Nim got there first, then Hild and Osric together: the corridor's two places go to Nim and Hild."""
        game = _held(distance=30)
        hild, osric, nim = _ids(game, "Hild", "Osric", "Nim")
        pf.play(game, pf.close(game, nim))
        pf.play(game, pf.close(game, hild), pf.close(game, osric))
        pf.play(game, pf.close(game, hild), pf.close(game, osric))
        member_id = pf.named(game, name)
        front_rank = _encounter(game).groups[0].front_rank
        assert front_rank == (hild, nim), "precondition: the view shows the front rank"
        offered = member_id in front_rank

        result = pf.round_(game, pf.strike(game, member_id))

        assert result.accepted is offered, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
        assert offered is (name in ("Hild", "Nim"))

    def test_formation_width_is_the_space_the_party_stands_in(self):
        assert _encounter(pf.battle()).formation_width == 2
        assert _encounter(pf.battle(engage=False)).formation_width == 2
        assert _encounter(pf.battle(adventure=pf.hall())).formation_width == 4
        assert _encounter(pf.battle(ruleset=Ruleset(formation_width_limit=False))).formation_width is None

    def test_the_free_round_flag_marks_the_round_a_back_stab_is_legal(self):
        surprised = pf.battle(surprised=True)
        assert _encounter(surprised).party_free_round is True
        pf.play(surprised)
        assert _encounter(surprised).party_free_round is False
        assert _encounter(pf.battle(surprised=False)).party_free_round is False

    def test_each_member_reports_how_far_they_can_move_in_a_round(self):
        game = pf.battle()
        nim = pf.named(game, "Nim")
        assert {member.name: member.encounter_rate_feet for member in game.view(Visibility.PLAYER).party} == pf.RATE

        pf.attach(game, pf.HASTE, nim)

        rates = {member.id: member.encounter_rate_feet for member in game.view(Visibility.PLAYER).party}
        assert rates[nim] == 60


@VIEW
class TestLightAndSightInThePlayerView:
    """Hild carries the torch, 30' of light. Aelin the elf sees 60' by infravision. Nobody else sees in the dark."""

    def test_a_group_in_the_torchlight_is_lit_and_seen_by_everyone(self):
        game = _held(distance=20, light=True)

        group = _encounter(game).groups[0]

        assert group.lit is True
        assert group.seen_by == tuple(member.id for member in game.party.members)

    def test_a_group_beyond_the_torch_is_seen_only_by_infravision(self):
        game = _held(distance=50, light=True)

        group = _encounter(game).groups[0]

        assert group.lit is False
        assert group.seen_by == (pf.named(game, "Aelin"),)

    def test_the_torch_reaches_as_far_as_its_bearer_stands_forward(self):
        game = _held(distance=50, light=True)
        hild = pf.named(game, "Hild")
        pf.play(game, pf.close(game, hild))

        group = _encounter(game).groups[0]

        assert group.member_distance_feet[hild] == 30
        assert group.lit is True
        assert group.seen_by == tuple(member.id for member in game.party.members)

    def test_a_group_beyond_infravision_is_seen_by_nobody(self):
        game = _held(distance=70, light=True)

        group = _encounter(game).groups[0]

        assert (group.lit, group.seen_by) == (False, ())

    def test_a_blind_member_sees_nothing(self):
        game = _held(distance=50, light=True)
        aelin = pf.named(game, "Aelin")
        game.member(aelin).conditions = (ActiveCondition(condition=Condition.BLIND, effect_id="effect-9999"),)

        group = _encounter(game).groups[0]

        assert (group.lit, group.seen_by) == (False, ())


class TestTheBattleFieldsOutsideABattle:
    """Guards: the new fields default to empty outside a battle, and an older client's view still parses."""

    def test_an_encounter_before_the_battle_has_no_positions(self):
        game = pf.battle(engage=False)
        assert game.mode is SessionMode.ENCOUNTER, "precondition: no battle yet"

        view = _encounter(game)
        group = view.groups[0]

        assert (view.advance_feet, view.party_free_round) == ({}, False)
        assert (group.member_distance_feet, group.front_rank, group.lit, group.seen_by) == ({}, (), None, ())

    def test_the_new_fields_are_additive(self):
        older_group = EncounterGroupView.model_validate(
            {"id": "group-0001", "label": "goblin", "count": 2, "distance_feet": 30, "visible_conditions": []}
        )
        assert (older_group.member_distance_feet, older_group.front_rank) == ({}, ())
        assert (older_group.lit, older_group.seen_by) == (None, ())
        older_view = EncounterView.model_validate({"groups": [], "stance": None, "in_battle": False})
        assert (older_view.advance_feet, older_view.formation_width, older_view.party_free_round) == ({}, None, False)
        view = pf.battle().view(Visibility.PLAYER)
        assert PlayerView.model_validate_json(view.model_dump_json()) == view


# ---------------------------------------------------------------------- the walk-through


@VIEW
def test_the_walk_through_battle_from_the_player_view():
    """The design's section 8, played as a game's battle screen plays it, from the player view and the dry run.

    Four goblins appear 30' down a 10' corridor, and the party surprises them. In the free round Nim the thief closes
    and back-stabs, Aelin the elf shoots from where she stands, Wren the magic-user casts *magic missile* from the back,
    and Hild and Osric, the fighters in plate, close in without reaching. In round 2 Hild closes and attacks beside Nim,
    the dry run turns Osric's close and attack into a close, and Tam closes up. From then on the front rank fights,
    Aelin keeps shooting, and no goblin ever reaches Aelin, Wren, Osric, or Tam. When the battle ends, the party is back
    on its cell as one body.
    """
    battle_args = {"distance": 30, "count": 4, "surprised": True, "spells": MISSILE_SPELLS}
    for seed in pf.seeds(**battle_args):
        game = pf.battle(first_seed=seed, **battle_args)
        hild, osric, nim, aelin, wren, tam = _ids(game, "Hild", "Osric", "Nim", "Aelin", "Wren", "Tam")
        view = game.view(Visibility.PLAYER)
        group = view.encounter.groups[0]
        rates = {member.id: member.encounter_rate_feet for member in view.party}

        # Round 1, the free round: the screen offers "close in and attack" to whoever can reach.
        assert view.encounter.party_free_round is True
        assert view.encounter.formation_width == 2
        reach = {
            member_id
            for member_id in view.encounter.declarers
            if group.member_distance_feet[member_id] <= rates[member_id] + 5
        }
        assert nim in reach and hild not in reach and osric not in reach
        hild_swings = pf.strike(game, hild, move="close", weapon_id="sword")
        assert [
            (rejection.code, dict(rejection.params))
            for rejection in preview_battle_round(game, [hild_swings]).rejections[hild]
        ] == [("combat.attack.out_of_reach", {"distance_feet": 10})]
        first = (
            pf.strike(game, nim, move="close", back_stab=True),
            pf.close(game, hild),
            pf.close(game, osric),
            pf.strike(game, aelin, weapon_id="short_bow"),
            _missile(game, wren),
            pf.declare(tam),
        )
        assert all(found == () for found in preview_battle_round(game, first).rejections.values())

        events = pf.play(game, *first)

        assert pf.advances(game) == {hild: 20, osric: 20, nim: 25, aelin: 0, wren: 0, tam: 0}
        assert [(event.character_id, event.distance_feet) for event in pf.moved(events)] == [
            (hild, 10),
            (osric, 10),
            (nim, 5),
        ]
        (shot,) = pf.attacks_by(events, aelin)
        (stab,) = pf.attacks_by(events, nim)
        cast = next(
            event
            for event in events
            if getattr(event, "caster_id", None) == wren and event.code.startswith("magic.cast.")
        )
        assert stab.back_stab is True
        assert pf.position(events, shot) < pf.position(events, cast) < pf.position(events, stab)
        assert pf.monster_attacks(game, events) == []
        assert _encounter(game).party_free_round is False

        # Round 2: Hild takes the last place in the front rank, and the dry run turns Osric's swing into a close.
        group = _encounter(game).groups[0]
        assert group.front_rank == (nim,)
        planned = [pf.strike(game, nim), pf.strike(game, hild, move="close", weapon_id="sword")]
        osric_swings = pf.strike(game, osric, move="close", weapon_id="sword")
        assert [
            rejection.code for rejection in preview_battle_round(game, [*planned, osric_swings]).rejections[osric]
        ] == ["battle.declaration.not_in_front_rank"]
        planned += [
            pf.close(game, osric),
            pf.strike(game, aelin, weapon_id="short_bow"),
            _missile(game, wren),
            pf.close(game, tam),
        ]

        events = pf.play(game, *planned)
        if game.battle is None or game.encounter.groups[0].fleeing:
            continue  # The walk-through supposes the goblins stand and fight.

        assert pf.advances(game) == {hild: 25, osric: 25, nim: 25, aelin: 0, wren: 0, tam: 20}
        assert pf.group_moves(events) == [], "within reach of Nim, the goblins hold their ground"
        struck = {attack.defender_id for attack in pf.monster_attacks(game, events)}
        assert struck <= ({nim} if pf.monsters_first(game, events) else {nim, hild})
        assert _encounter(game).groups[0].front_rank == (hild, nim)

        # Round 3 on: the front rank fights, Aelin shoots while the goblins are in range, and the rest hold.
        for _ in range(40):
            if game.mode is not SessionMode.BATTLE:
                break
            group = _encounter(game).groups[0]
            declared = [pf.strike(game, member_id) for member_id in group.front_rank]
            if not group.fled and group.member_distance_feet[aelin] <= 150:
                declared.append(pf.strike(game, aelin, weapon_id="short_bow"))
            fleeing = game.encounter.groups[0].fleeing

            events = pf.play(game, *declared)

            assert {attack.defender_id for attack in pf.monster_attacks(game, events)} <= {nim, hild}
            if game.battle is not None:
                assert (pf.advance(game, aelin), pf.advance(game, wren)) == (0, 0)
                if not fleeing and not game.encounter.groups[0].fleeing:
                    assert game.encounter.groups[0].distance_feet == 30

        # The end: the battle is over, and the positions went with it.
        assert game.mode is SessionMode.EXPLORING
        assert game.battle is None
        assert game.view(Visibility.PLAYER).encounter is None
        return
