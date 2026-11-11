"""One party member acts on another: a potion fed, an item handed over, a spell cast by touch.

These are issues #224 and #225, and item 16 of #166. Outside a battle the party stands together, so
any member can reach any other. In a battle each member has a place on the range track, and two
members reach each other when they stand within 5' after the round's declared moves.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.effects import Condition, grant_condition, kill
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import CastContext, caster_profile, validate_cast
from osrlib.core.tables import ReactionResult
from osrlib.crawl.commands import GiveItems, GrantItem, SessionMode, UseItem
from osrlib.crawl.encounter import start_encounter
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_spells

OUT_OF_REACH = "battle.declaration.member_out_of_reach"
CANNOT_DRINK = "items.use.target_cannot_drink"
HURT = 100
"""Hit points the wounded member is left with, out of 400."""


def fight(**battle_args) -> GameSession:
    """A battle with two goblins 60' away that never move, and every member on the party line."""
    game = pf.battle(distance=60, spells={"Tam": ("cure_light_wounds", "cure_light_wounds")}, **battle_args)
    pf.hold_monsters(game)
    return game


def exploring() -> GameSession:
    return pf.session(1, spells={"Tam": ("cure_light_wounds",)})


def grant(game: GameSession, name: str, item_id: str, quantity: int = 1) -> str:
    """Give `name` an item and return the id a command names it by."""
    member = game.member(pf.named(game, name))
    assert game.execute(GrantItem(character_id=member.id, item_id=item_id, quantity=quantity)).accepted
    for entry in member.inventory.items:
        if isinstance(entry, MagicItemInstance) and entry.template_id == item_id:
            return entry.instance_id
    return item_id


def wound(game: GameSession, name: str) -> None:
    game.member(pf.named(game, name)).current_hp = HURT


def hit_points(game: GameSession, name: str) -> int:
    return game.member(pf.named(game, name)).current_hp


def carries(game: GameSession, name: str, item_id: str) -> int:
    """How many of an item `name` carries, by catalog id or by instance id."""
    total = 0
    for entry in game.member(pf.named(game, name)).inventory.items:
        if isinstance(entry, MagicItemInstance):
            total += entry.quantity if entry.instance_id == item_id else 0
        elif entry.template.id == item_id:
            total += entry.quantity
    return total


def advance_hild(game: GameSession) -> None:
    """Hild closes 20' on the goblins, and everyone else stays on the line."""
    hild = pf.named(game, "Hild")
    pf.play(game, pf.close(game, hild))
    assert pf.advance(game, hild) == 20


def feed(game: GameSession, potion: str, target: str = "Hild"):
    return pf.declare(pf.named(game, "Tam"), "use_item", item_id=potion, targets=(pf.named(game, target),))


def hand(game: GameSession, item_id: str, target: str = "Hild", **fields):
    return pf.declare(pf.named(game, "Tam"), "give_item", item_id=item_id, targets=(pf.named(game, target),), **fields)


def cure(game: GameSession, target: str = "Hild"):
    return pf.declare(
        pf.named(game, "Tam"),
        "cast",
        spell_id="cure_light_wounds",
        spell_mode="heal",
        targets=(pf.named(game, target),),
    )


def codes(rejections) -> list[str]:
    return [code for code, _ in rejections]


# ---------------------------------------------------------------------- a potion for someone else (#224)


def test_a_member_feeds_a_potion_to_another_while_exploring():
    game = exploring()
    potion = grant(game, "Tam", "potion_of_healing")
    wound(game, "Hild")
    result = game.execute(UseItem(character_id=pf.named(game, "Tam"), item_id=potion, target_id=pf.named(game, "Hild")))
    assert result.accepted, [rejection.code for rejection in result.rejections]
    assert hit_points(game, "Hild") > HURT
    assert hit_points(game, "Tam") == 400
    assert carries(game, "Tam", potion) == 0


def test_the_carrier_still_drinks_when_no_target_is_named():
    game = exploring()
    potion = grant(game, "Tam", "potion_of_healing")
    wound(game, "Tam")
    assert game.execute(UseItem(character_id=pf.named(game, "Tam"), item_id=potion)).accepted
    assert hit_points(game, "Tam") > HURT


def test_a_potion_cant_be_fed_to_the_dead():
    game = exploring()
    potion = grant(game, "Tam", "potion_of_healing")
    kill(game.member(pf.named(game, "Hild")))
    clock = game.clock.rounds
    result = game.execute(UseItem(character_id=pf.named(game, "Tam"), item_id=potion, target_id=pf.named(game, "Hild")))
    assert [rejection.code for rejection in result.rejections] == [CANNOT_DRINK]
    assert carries(game, "Tam", potion) == 1
    assert game.clock.rounds == clock


def test_a_potion_for_nobody_in_the_party_is_refused():
    game = exploring()
    potion = grant(game, "Tam", "potion_of_healing")
    result = game.execute(UseItem(character_id=pf.named(game, "Tam"), item_id=potion, target_id="character-9999"))
    assert not result.accepted
    assert carries(game, "Tam", potion) == 1


def test_a_member_feeds_a_potion_to_another_in_battle():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    wound(game, "Hild")
    pf.play(game, feed(game, potion))
    assert hit_points(game, "Hild") > HURT
    assert hit_points(game, "Tam") == 400
    assert carries(game, "Tam", potion) == 0


def test_a_sleeping_member_can_be_fed():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    wound(game, "Hild")
    grant_condition(game.member(pf.named(game, "Hild")), Condition.ASLEEP, None)
    pf.play(game, feed(game, potion))
    assert hit_points(game, "Hild") > HURT


def test_a_potion_cant_be_fed_to_a_member_out_of_reach():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    wound(game, "Hild")
    advance_hild(game)
    assert codes(pf.refused(game, feed(game, potion))) == [OUT_OF_REACH]
    assert hit_points(game, "Hild") == HURT


def test_reach_is_judged_where_the_members_stand_after_the_rounds_moves():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    wound(game, "Hild")
    # Hild declares a move away in the same round, so she's 20' from Tam when he reaches for her.
    rejections = pf.refused(game, pf.close(game, pf.named(game, "Hild")), feed(game, potion))
    assert codes(rejections) == [OUT_OF_REACH]


def test_two_members_who_advance_together_stay_in_reach():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    wound(game, "Hild")
    pf.play(game, pf.close(game, pf.named(game, "Hild")), pf.close(game, pf.named(game, "Tam")))
    assert pf.advance(game, pf.named(game, "Hild")) == pf.advance(game, pf.named(game, "Tam")) == 20
    pf.play(game, feed(game, potion))
    assert hit_points(game, "Hild") > HURT


def test_a_potion_cant_be_fed_to_the_dead_in_battle():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    kill(game.member(pf.named(game, "Hild")))
    assert codes(pf.refused(game, feed(game, potion))) == [CANNOT_DRINK]


# ---------------------------------------------------------------------- handing an item over (#225)


def test_give_items_works_in_an_encounter_and_takes_a_round():
    game = exploring()
    potion = grant(game, "Tam", "potion_of_healing")
    start_encounter(
        game,
        groups=[("goblin", game.spawn("goblin", 2))],
        kind="spawned",
        distance_feet=120,
        pinned_stance=ReactionResult.FRIENDLY,
    )
    assert game.mode is SessionMode.ENCOUNTER
    clock = game.clock.rounds
    result = game.execute(
        GiveItems(character_id=pf.named(game, "Tam"), recipient_id=pf.named(game, "Hild"), item_ids=(potion,))
    )
    assert result.accepted, [rejection.code for rejection in result.rejections]
    assert (carries(game, "Tam", potion), carries(game, "Hild", potion)) == (0, 1)
    assert game.clock.rounds == clock + 1


def test_the_give_items_command_is_still_refused_in_a_battle():
    """In a battle a hand-over is a member's declaration for the round, not a command of its own."""
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    result = game.execute(
        GiveItems(character_id=pf.named(game, "Tam"), recipient_id=pf.named(game, "Hild"), item_ids=(potion,))
    )
    assert [rejection.code for rejection in result.rejections] == ["session.command.wrong_mode"]


def test_a_member_hands_an_item_over_as_their_declaration():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    events = pf.play(game, hand(game, potion))
    assert (carries(game, "Tam", potion), carries(game, "Hild", potion)) == (0, 1)
    given = [event for event in events if event.code == "exploration.item.given"]
    assert len(given) == 1
    assert given[0].character_id == pf.named(game, "Tam")


def test_a_hand_over_moves_one_of_a_stack():
    game = fight()
    grant(game, "Tam", "oil_flask", quantity=3)
    pf.play(game, hand(game, "oil_flask"))
    assert (carries(game, "Tam", "oil_flask"), carries(game, "Hild", "oil_flask")) == (2, 1)


def test_a_hand_over_needs_the_two_members_in_reach():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    advance_hild(game)
    assert codes(pf.refused(game, hand(game, potion))) == [OUT_OF_REACH]


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (lambda game: hand(game, "potion_of_speed"), "exploration.item.not_carried"),
        (lambda game: hand(game, "oil_flask", target="Tam"), "exploration.give.same_member"),
        (
            lambda game: hand(game, "oil_flask", move="close", target_group_id=pf.group_id(game)),
            "battle.declaration.move_not_combinable",
        ),
    ],
    ids=["an item the giver doesn't carry", "the giver as the recipient", "a hand-over with a move"],
)
def test_a_hand_over_that_cant_happen_is_refused(setup, code):
    game = fight()
    grant(game, "Tam", "oil_flask")
    assert codes(pf.refused(game, setup(game))) == [code]


def test_a_hand_over_to_a_member_who_cant_take_it_is_refused():
    game = fight()
    grant(game, "Tam", "oil_flask")
    grant_condition(game.member(pf.named(game, "Hild")), Condition.ASLEEP, None)
    assert codes(pf.refused(game, hand(game, "oil_flask"))) == ["session.command.member_incapacitated"]


# ---------------------------------------------------------------------- touch (#166, item 16)


def touch_cast(distance_feet: int | None):
    catalog, cleric = load_spells(), load_classes().get("cleric")
    tam = pf.session(1, spells={"Tam": ("cure_light_wounds",)}).party.members[5]
    assert tam.class_id == "cleric"
    spell = catalog.get("cure_light_wounds")
    assert spell.range_spec.kind == "touch"
    return validate_cast(
        tam,
        spell,
        "heal",
        profile=caster_profile(cleric),
        targets=[tam],
        context=CastContext(distance_feet=distance_feet),
    )


def test_validate_cast_refuses_a_touch_spell_beyond_reach():
    assert [rejection.code for rejection in touch_cast(500)] == ["magic.cast.out_of_range"]
    assert [rejection.code for rejection in touch_cast(10)] == ["magic.cast.out_of_range"]
    assert touch_cast(5) == []
    assert touch_cast(0) == []
    assert touch_cast(None) == []


def test_a_touch_spell_reaches_a_member_beside_the_caster():
    game = fight()
    wound(game, "Hild")
    pf.play(game, cure(game))
    assert hit_points(game, "Hild") > HURT


def test_a_touch_spell_cant_reach_a_member_who_has_moved_away():
    game = fight()
    wound(game, "Hild")
    advance_hild(game)
    assert codes(pf.refused(game, cure(game))) == ["magic.cast.out_of_range"]
    assert hit_points(game, "Hild") == HURT


def test_a_touch_spell_follows_the_rounds_moves():
    game = fight()
    wound(game, "Hild")
    rejections = pf.refused(game, pf.close(game, pf.named(game, "Hild")), cure(game))
    assert codes(rejections) == ["magic.cast.out_of_range"]


# ---------------------------------------------------------------------- a staff of healing heals by touch


def heal_with_staff(game: GameSession, staff: str, *targets: str):
    return pf.declare(pf.named(game, "Tam"), "use_item", item_id=staff, targets=targets)


def test_a_staff_of_healing_heals_a_member_beside_its_user():
    game = fight()
    staff = grant(game, "Tam", "staff_of_healing")
    wound(game, "Hild")
    pf.play(game, heal_with_staff(game, staff, pf.named(game, "Hild")))
    assert hit_points(game, "Hild") > HURT


def test_a_staff_of_healing_cant_reach_a_member_who_has_moved_away():
    game = fight()
    staff = grant(game, "Tam", "staff_of_healing")
    wound(game, "Hild")
    advance_hild(game)
    assert codes(pf.refused(game, heal_with_staff(game, staff, pf.named(game, "Hild")))) == [OUT_OF_REACH]
    assert hit_points(game, "Hild") == HURT


def test_a_staff_of_healing_aimed_at_nobody_is_refused():
    game = fight()
    staff = grant(game, "Tam", "staff_of_healing")
    assert codes(pf.refused(game, heal_with_staff(game, staff, "character-9999"))) == ["items.use.unknown_target"]
