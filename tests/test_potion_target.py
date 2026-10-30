"""`UseItem` and a potion's `target_id` (issue #174).

A potion is drunk by whoever carries it, so a `target_id` that names someone else can't be honoured.
The session refuses it with `items.use.target_not_allowed` instead of accepting the command and letting
the carrier drink. A refused command follows the command contract: no draw, no clock time, no state change,
no log entry. A `target_id` that is the user, or `None`, still works as before, and a device that takes a
target (the staff of healing) still takes another member.

`UseItem` is legal only while exploring and during an encounter. In town and in battle the session refuses
it for its mode, whatever `target_id` says. In battle a potion is drunk through a `use_item`
`BattleDeclaration`, which has no member-target field, so nothing there can name another drinker.
"""

import copy

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.items import MagicItemInstance
from osrlib.core.tables import ReactionResult
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    GrantItem,
    LightSource,
    ResolveBattleRound,
    SessionMode,
    UseItem,
)
from osrlib.crawl.session import GameSession
from osrlib.persistence import session_state

POTION_ID = "magic-item-9001"
CARRIER_ID = "character-0001"
OTHER_ID = "character-0002"
CLERIC_ID = "character-0003"

XFAIL = pytest.mark.xfail(reason="chunk: potion-target", strict=True)

NOT_ALLOWED = "items.use.target_not_allowed"


def exploring_session(template_id: str = "potion_of_healing", *, carrier_id: str = CARRIER_ID) -> GameSession:
    """A lit party in the dungeon, with one magic item carried by `carrier_id`."""
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=51)
    session.execute(GrantItem(character_id=CARRIER_ID, item_id="torch", quantity=6))
    session.execute(GrantItem(character_id=CARRIER_ID, item_id="tinder_box"))
    session.member(carrier_id).inventory.items.append(MagicItemInstance(instance_id=POTION_ID, template_id=template_id))
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
    for _ in range(20):
        lit = session.execute(LightSource(character_id=CARRIER_ID, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    assert session.mode is SessionMode.EXPLORING
    return session


def encounter_session(template_id: str = "potion_of_healing", *, carrier_id: str = CARRIER_ID) -> GameSession:
    """The same party with a hostile goblin encounter open at 60 feet, before any battle."""
    session = exploring_session(template_id, carrier_id=carrier_id)
    instances = session.spawn("goblin", 1)
    encounter_module.start_encounter(
        session,
        groups=[("goblin", instances)],
        kind="spawned",
        distance_feet=60,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    assert session.mode is SessionMode.ENCOUNTER
    return session


def battle_session(template_id: str = "potion_of_healing", *, carrier_id: str = CARRIER_ID) -> GameSession:
    """The encounter engaged, so the session is in battle."""
    session = encounter_session(template_id, carrier_id=carrier_id)
    assert session.execute(EngageBattle()).accepted
    assert session.mode is SessionMode.BATTLE
    return session


def town_session(template_id: str = "potion_of_healing") -> GameSession:
    """A new session, still in town, with the item carried."""
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=51)
    session.member(CARRIER_ID).inventory.items.append(MagicItemInstance(instance_id=POTION_ID, template_id=template_id))
    assert session.mode is SessionMode.TOWN
    return session


def drunk_by(result) -> list[str]:
    """The ids of the members who drank a potion in `result`, in event order."""
    return [event.character_id for event in result.events if event.code == "items.potion.drunk"]


def carries(session: GameSession, member_id: str) -> bool:
    return session.member(member_id).inventory.magic_item(POTION_ID) is not None


MODE_BUILDERS = [
    pytest.param(exploring_session, id="exploring"),
    pytest.param(encounter_session, id="encounter"),
]


class TestAPotionRefusesAnotherTarget:
    """A potion used on someone other than the user is refused, and the refusal costs nothing."""

    @XFAIL
    @pytest.mark.parametrize("build", MODE_BUILDERS)
    def test_another_member_is_refused(self, build):
        session = build()
        result = session.execute(UseItem(character_id=CARRIER_ID, item_id=POTION_ID, target_id=OTHER_ID))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == [NOT_ALLOWED]
        assert result.events == ()
        assert drunk_by(result) == []

    @XFAIL
    @pytest.mark.parametrize("build", MODE_BUILDERS)
    def test_an_id_that_matches_nobody_is_refused_the_same_way(self, build):
        session = build()
        result = session.execute(UseItem(character_id=CARRIER_ID, item_id=POTION_ID, target_id="bogus"))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == [NOT_ALLOWED]

    @XFAIL
    def test_the_comparison_is_to_the_user_not_to_the_first_member(self):
        # The carrier here is not the first member, so a fix that compares `target_id` to the first
        # member, or to the party leader, instead of to the user, still fails.
        session = exploring_session(carrier_id=CLERIC_ID)
        result = session.execute(UseItem(character_id=CLERIC_ID, item_id=POTION_ID, target_id=CARRIER_ID))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == [NOT_ALLOWED]

    @XFAIL
    @pytest.mark.parametrize("build", MODE_BUILDERS)
    def test_a_refused_use_costs_no_draw_no_time_and_no_state(self, build):
        session = build()
        before = copy.deepcopy(session_state(session))
        rounds = session.clock.rounds
        logged = len(session.command_log)
        result = session.execute(UseItem(character_id=CARRIER_ID, item_id=POTION_ID, target_id=OTHER_ID))
        assert not result.accepted
        assert result.rejections[0].code == NOT_ALLOWED
        # The whole saved state is unchanged: RNG streams, clock, ledger, inventories, and both logs.
        assert session_state(session) == before
        assert session.clock.rounds == rounds
        assert len(session.command_log) == logged
        # The potion is still carried and still unidentified.
        instance = session.member(CARRIER_ID).inventory.magic_item(POTION_ID)
        assert instance is not None and instance.identified is False

    @XFAIL
    def test_a_refused_use_leaves_the_potion_for_the_right_use_afterwards(self):
        session = exploring_session()
        carrier = session.member(CARRIER_ID)
        carrier.current_hp = 1
        refused = session.execute(UseItem(character_id=CARRIER_ID, item_id=POTION_ID, target_id=OTHER_ID))
        assert not refused.accepted
        accepted = session.execute(UseItem(character_id=CARRIER_ID, item_id=POTION_ID))
        assert accepted.accepted
        assert drunk_by(accepted) == [CARRIER_ID]
        assert carrier.current_hp > 1


class TestAPotionStillWorksForTheUser:
    """`target_id` equal to the user, or `None`, drinks the potion as it always has."""

    @pytest.mark.parametrize("build", MODE_BUILDERS)
    @pytest.mark.parametrize("target_id", [None, CARRIER_ID], ids=["no_target", "self_target"])
    def test_the_carrier_drinks_it(self, build, target_id):
        session = build()
        carrier = session.member(CARRIER_ID)
        other = session.member(OTHER_ID)
        carrier.current_hp = 1
        other.current_hp = 1
        result = session.execute(UseItem(character_id=CARRIER_ID, item_id=POTION_ID, target_id=target_id))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert drunk_by(result) == [CARRIER_ID]
        assert not carries(session, CARRIER_ID)
        assert carrier.current_hp > 1

    def test_a_self_target_is_judged_by_the_user_not_by_the_first_member(self):
        session = exploring_session(carrier_id=CLERIC_ID)
        cleric = session.member(CLERIC_ID)
        cleric.current_hp = 1
        result = session.execute(UseItem(character_id=CLERIC_ID, item_id=POTION_ID, target_id=CLERIC_ID))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert drunk_by(result) == [CLERIC_ID]
        assert cleric.current_hp > 1


class TestADeviceStillTakesAnotherMember:
    """The staff of healing touches the member `target_id` names, so the potion rule doesn't reach it."""

    @pytest.mark.parametrize("build", MODE_BUILDERS)
    def test_the_staff_heals_the_named_member(self, build):
        session = build("staff_of_healing", carrier_id=CLERIC_ID)
        cleric = session.member(CLERIC_ID)
        patient = session.member(OTHER_ID)
        cleric.current_hp = 1
        patient.current_hp = 1
        result = session.execute(UseItem(character_id=CLERIC_ID, item_id=POTION_ID, target_id=OTHER_ID))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert patient.current_hp > 1
        assert cleric.current_hp == 1


class TestTheModesWhereUseItemIsNotLegal:
    """Town and battle refuse `UseItem` for its mode before they look at the target."""

    @pytest.mark.parametrize(
        ("build", "mode"),
        [
            pytest.param(town_session, "town", id="town"),
            pytest.param(battle_session, "battle", id="battle"),
        ],
    )
    @pytest.mark.parametrize("target_id", [None, CARRIER_ID, OTHER_ID], ids=["no_target", "self", "other"])
    def test_a_potion_is_refused_for_the_mode_and_nothing_moves(self, build, mode, target_id):
        session = build()
        before = copy.deepcopy(session_state(session))
        result = session.execute(UseItem(character_id=CARRIER_ID, item_id=POTION_ID, target_id=target_id))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]
        assert result.rejections[0].params["mode"] == mode
        assert session_state(session) == before
        assert carries(session, CARRIER_ID)


class TestADeclaredPotionInBattle:
    """In battle a potion is drunk through a `use_item` declaration, and the declarer drinks it."""

    def test_the_declarer_drinks_it(self):
        session = battle_session()
        carrier = session.member(CARRIER_ID)
        carrier.current_hp = 1
        declarations = tuple(
            BattleDeclaration(character_id=member.id, action="hold")
            for member in session.party.living_members()
            if member.id != CARRIER_ID
        ) + (BattleDeclaration(character_id=CARRIER_ID, action="use_item", item_id=POTION_ID),)
        result = session.execute(ResolveBattleRound(declarations=declarations))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert drunk_by(result) == [CARRIER_ID]
        assert not carries(session, CARRIER_ID)
