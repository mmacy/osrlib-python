"""Acceptance tests for five reports from issue #257 that a player or a front-end author sees.

- A potion given to another member, and a `give_item` hand-over, that fail when their step in the battle round
  comes report a `DeclarationFizzledEvent`, as a device use and a turning do.
- A battle declaration of a potion, a staff of healing, or a hand-over with the wrong number of ids in `targets`
  is refused with `battle.declaration.target_count`, which names the declaring member and the number given.
- `EncounterStartedEvent` names every group, and its default line is true for an encounter of two or more groups.
- The referee line for `SpawnMonsters` names the monsters by their display name.
- A staff of healing used on a creature it already healed today is refused, because the OSE SRD's Staff of Healing
  page says it "Is effective on any individual at most once per day."

The tests that pass on the branch's base are guards: they hold what the chunk must keep. Everything here is
additive within the current `schema_version`, and a session in which none of these situations arises draws
exactly as before.
"""

import pytest
from pydantic import ValidationError

import positions_fixtures as pf
from osrlib.core.clock import TimeUnit
from osrlib.core.effects import kill
from osrlib.core.events import DeclarationFizzledEvent, Visibility
from osrlib.core.spells import MAGIC_STREAM
from osrlib.core.tables import ReactionResult
from osrlib.crawl import battle as battle_module
from osrlib.crawl.commands import AdvanceTime, SpawnMonsters, UseItem
from osrlib.crawl.encounter import start_encounter
from osrlib.crawl.events import EncounterStartedEvent, MonstersSpawnedEvent
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message
from test_member_reach import HURT, carries, feed, fight, grant, hand, heal_with_staff, hit_points, wound

CHUNK = "chunk: session-reports"


def _fizzles(events, code: str) -> list[DeclarationFizzledEvent]:
    return [event for event in events if isinstance(event, DeclarationFizzledEvent) and event.code == code]


def _kill_before_the_magic_phase(monkeypatch, name: str) -> None:
    """Kill the member `name` after the round was accepted and before the party's magic phase resolves.

    The tests force the death because a battle against held monsters gives nothing that would kill a member
    between the pre-phase and the magic phase. The wrapper runs before the party block's magic phase, which
    judges a potion and a hand-over again just before each resolves. A round with monsters that win initiative
    and kill the member reaches the same state.
    """
    original = battle_module._party_magic

    def death_then_magic(session, *args, **kwargs):
        kill(session.member(pf.named(session, name)))
        return original(session, *args, **kwargs)

    monkeypatch.setattr(battle_module, "_party_magic", death_then_magic)


def _assert_readable(message: str, *parts: str) -> None:
    assert message.endswith(".")
    assert "—" not in message and "–" not in message
    for part in parts:
        assert part in message, (part, message)


# ---------------------------------------------------------------------- a potion or a hand-over that fails at its step


@pytest.mark.xfail(reason=CHUNK)
def test_a_potion_for_a_member_who_dies_before_it_resolves_fizzles(monkeypatch):
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    tam, hild = pf.named(game, "Tam"), pf.named(game, "Hild")
    _kill_before_the_magic_phase(monkeypatch, "Hild")

    events = pf.play(game, feed(game, potion))

    (fizzle,) = _fizzles(events, "magic.potion.fizzled")
    assert fizzle.event_type == "declaration_fizzled"
    assert fizzle.actor_id == tam
    assert fizzle.item_id == potion
    assert fizzle.target_id == hild
    assert fizzle.group_id is None
    # `_potion_drinker` refuses a drinker who is dead or turned to stone with this code.
    assert fizzle.reason == "items.use.target_cannot_drink"
    assert fizzle.visibility is Visibility.PLAYER
    assert "potion_of_healing" not in fizzle.model_dump_json(), "the fizzle names an unidentified potion"
    _assert_readable(format_message(fizzle), tam, potion, hild)
    assert "lost" not in format_message(fizzle).lower(), "a potion that fizzles loses nothing"
    assert carries(game, "Tam", potion) == 1
    assert game.member(tam).inventory.magic_item(potion).identified is False
    assert not [event for event in events if event.code == "items.potion.drunk"]


@pytest.mark.xfail(reason=CHUNK)
def test_a_hand_over_to_a_member_who_dies_before_it_resolves_fizzles(monkeypatch):
    game = fight()
    grant(game, "Tam", "oil_flask")
    tam, hild = pf.named(game, "Tam"), pf.named(game, "Hild")
    _kill_before_the_magic_phase(monkeypatch, "Hild")

    events = pf.play(game, hand(game, "oil_flask"))

    (fizzle,) = _fizzles(events, "battle.give.fizzled")
    assert fizzle.actor_id == tam
    assert fizzle.item_id == "oil_flask"
    assert fizzle.target_id == hild
    assert fizzle.group_id is None
    # `_member_able` refuses a member who is dead, paralysed, turned to stone, or asleep with this code.
    assert fizzle.reason == "session.command.member_incapacitated"
    assert fizzle.visibility is Visibility.PLAYER
    _assert_readable(format_message(fizzle), tam, "oil_flask", hild)
    assert (carries(game, "Tam", "oil_flask"), carries(game, "Hild", "oil_flask")) == (1, 0)
    assert not [event for event in events if event.code == "exploration.item.given"]


@pytest.mark.parametrize(
    "declare",
    [
        lambda game: feed(game, grant(game, "Tam", "potion_of_healing")),
        lambda game: (grant(game, "Tam", "oil_flask"), hand(game, "oil_flask"))[1],
    ],
    ids=["a potion", "a hand-over"],
)
def test_a_declaration_that_fails_at_its_step_costs_what_a_hold_costs(monkeypatch, declare):
    """Guard: the fizzle report adds an event and nothing else. The round draws and changes what a hold does."""
    _kill_before_the_magic_phase(monkeypatch, "Hild")
    used, held = fight(), fight()
    used_declaration = declare(used)
    declare(held)  # the same items granted, so the two sessions differ only in Tam's declaration

    used_events = pf.play(used, used_declaration)
    held_events = pf.play(held)

    assert pf.state_without_logs(used) == pf.state_without_logs(held)
    drop = {"magic.potion.fizzled", "battle.give.fizzled"}
    assert [event for event in used_events if event.code not in drop] == held_events


def test_a_staff_of_healing_on_a_member_who_dies_still_fizzles_as_a_device(monkeypatch):
    """Guard: the device case the two new codes follow."""
    game = fight()
    staff = grant(game, "Tam", "staff_of_healing")
    _kill_before_the_magic_phase(monkeypatch, "Hild")

    events = pf.play(game, heal_with_staff(game, staff, pf.named(game, "Hild")))

    (fizzle,) = _fizzles(events, "magic.device.fizzled")
    assert (fizzle.item_id, fizzle.reason) == (staff, "items.use.target_cannot_be_healed")
    assert fizzle.target_id is None


def test_a_fizzle_event_with_a_target_round_trips():
    """Guard: the new field serializes and reads back."""
    event = DeclarationFizzledEvent(
        code="magic.potion.fizzled",
        actor_id="character-0006",
        item_id="magic-item-0001",
        target_id="character-0001",
        reason="items.use.target_cannot_drink",
    )
    assert DeclarationFizzledEvent.model_validate_json(event.model_dump_json()) == event
    legacy = DeclarationFizzledEvent(code="magic.device.fizzled", actor_id="a", item_id="w", reason="r")
    assert legacy.target_id is None


# ---------------------------------------------------------------------- the number of targets


TARGET_COUNT = "battle.declaration.target_count"


@pytest.mark.xfail(reason=CHUNK)
def test_a_potion_for_two_members_is_refused_with_the_count():
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    tam = pf.named(game, "Tam")
    declaration = pf.declare(tam, "use_item", item_id=potion, targets=(pf.named(game, "Hild"), pf.named(game, "Osric")))

    assert pf.refused(game, declaration) == [(TARGET_COUNT, {"character": tam, "expected": 1, "supplied": 2})]


@pytest.mark.xfail(reason=CHUNK)
def test_a_staff_of_healing_aimed_at_three_creatures_is_refused_with_the_count():
    game = fight()
    staff = grant(game, "Tam", "staff_of_healing")
    tam = pf.named(game, "Tam")
    three = (pf.named(game, "Hild"), pf.named(game, "Osric"), pf.named(game, "Nim"))

    assert pf.refused(game, heal_with_staff(game, staff, *three)) == [
        (TARGET_COUNT, {"character": tam, "expected": 1, "supplied": 3})
    ]


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("names", [("Hild", "Osric"), ()], ids=["two recipients", "no recipient"])
def test_a_hand_over_with_other_than_one_recipient_is_refused_with_the_count(names):
    """A hand-over has no default recipient, so an empty `targets` is a count of zero."""
    game = fight()
    grant(game, "Tam", "oil_flask")
    tam = pf.named(game, "Tam")
    declaration = pf.declare(
        tam, "give_item", item_id="oil_flask", targets=tuple(pf.named(game, name) for name in names)
    )

    assert pf.refused(game, declaration) == [(TARGET_COUNT, {"character": tam, "expected": 1, "supplied": len(names)})]


def test_a_potion_with_no_target_is_still_drunk_by_its_carrier():
    """Guard: an empty `targets` on a potion or a staff of healing means the carrier, and isn't a count error."""
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    wound(game, "Tam")

    pf.play(game, pf.declare(pf.named(game, "Tam"), "use_item", item_id=potion))

    assert hit_points(game, "Tam") > HURT


# ---------------------------------------------------------------------- the encounter's groups


def _open(groups: list[tuple[str, str, int]]) -> EncounterStartedEvent:
    """Open an encounter with one group for each `(label, template_id, count)` and return its started event."""
    game = pf.session(1)
    events = start_encounter(
        game,
        groups=[(label, game.spawn(template_id, count)) for label, template_id, count in groups],
        kind="spawned",
        distance_feet=40,
        pinned_stance=ReactionResult.FRIENDLY,
    )
    (started,) = [event for event in events if isinstance(event, EncounterStartedEvent)]
    return started


@pytest.mark.xfail(reason=CHUNK)
def test_an_encounter_of_two_groups_names_both():
    started = _open([("goblin", "goblin", 2), ("orc", "orc", 3)])

    assert (started.group_labels, started.group_labels_plural, started.group_counts) == (
        ("goblin", "orc"),
        ("goblins", "orcs"),
        (2, 3),
    )
    # The existing fields keep their meaning: the first group's names, and the creatures of every group.
    assert (started.monster_name, started.monster_name_plural, started.count) == ("goblin", "goblins", 5)
    assert format_message(started).startswith("Encounter: 2 goblins and 3 orcs at 40'")


@pytest.mark.xfail(reason=CHUNK)
def test_an_encounter_of_three_groups_lists_them_with_and_before_the_last():
    """A group of one takes the singular label, and an irregular plural comes from the group, not an ending."""
    started = _open([("merman", "merman", 2), ("goblin", "goblin", 1), ("orc", "orc", 3)])

    assert started.group_labels_plural == ("mermen", "goblins", "orcs")
    assert started.group_counts == (2, 1, 3)
    assert format_message(started).startswith("Encounter: 2 mermen, 1 goblin, and 3 orcs at 40'")


@pytest.mark.xfail(reason=CHUNK)
def test_an_encounter_of_one_group_fills_the_group_fields():
    game = pf.session(1)
    result = game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=40))

    (started,) = [event for event in result.events if isinstance(event, EncounterStartedEvent)]
    assert (started.group_labels, started.group_labels_plural, started.group_counts) == (
        ("goblin",),
        ("goblins",),
        (2,),
    )


def test_the_line_for_one_group_is_unchanged():
    """Guard: the line the goldens record for a one-group encounter, and for an event from an older save."""
    started = _open([("goblin", "goblin", 2)])
    assert format_message(started).startswith("Encounter: 2 goblins at 40'")
    legacy = EncounterStartedEvent(monster_name="goblin", count=2, distance_feet=40)
    assert format_message(legacy) == "Encounter: 2 goblins at 40'."


# ---------------------------------------------------------------------- the spawn line


def _spawned(template_id: str, count: int) -> tuple[GameSession, MonstersSpawnedEvent]:
    game = pf.session(1)
    result = game.execute(SpawnMonsters(template_id=template_id, count_fixed=count, distance_feet=40))
    assert result.accepted, [rejection.code for rejection in result.rejections]
    (spawned,) = [event for event in result.events if isinstance(event, MonstersSpawnedEvent)]
    return game, spawned


@pytest.mark.xfail(reason=CHUNK)
def test_the_spawn_line_names_the_monsters_by_their_display_name():
    _, spawned = _spawned("centipede_giant", 3)

    assert (spawned.display_name, spawned.display_name_plural) == ("giant centipede", "giant centipedes")
    message = format_message(spawned)
    first, second, third = spawned.monster_ids
    _assert_readable(message, "3 giant centipedes", f"{first}, {second}, and {third}")
    assert "×" not in message
    assert "centipede_giant" not in message


@pytest.mark.xfail(reason=CHUNK)
def test_the_spawn_line_for_one_monster_is_singular():
    _, spawned = _spawned("centipede_giant", 1)

    message = format_message(spawned)
    _assert_readable(message, "1 giant centipede", spawned.monster_ids[0])
    assert "centipedes" not in message
    assert "×" not in message


@pytest.mark.xfail(reason=CHUNK)
def test_the_spawn_line_takes_an_irregular_plural():
    _, spawned = _spawned("merman", 2)

    first, second = spawned.monster_ids
    _assert_readable(format_message(spawned), "2 mermen", f"{first} and {second}")


@pytest.mark.xfail(reason=CHUNK)
def test_the_spawn_line_of_an_event_from_an_older_save_reads_as_a_sentence():
    """An event with no display name names the monsters by `template_id`, still without a multiplication sign."""
    legacy = MonstersSpawnedEvent(template_id="goblin", monster_ids=("monster-0001", "monster-0002"))

    message = format_message(legacy)
    _assert_readable(message, "monster-0001 and monster-0002")
    assert "×" not in message


# ---------------------------------------------------------------------- a staff of healing once a day for each creature


HEALED_TODAY = "items.use.target_healed_today"


def _explore_with_staff() -> tuple[GameSession, str]:
    game = pf.session(1)
    return game, grant(game, "Tam", "staff_of_healing")


def _staff_use(game: GameSession, staff: str, target: str | None) -> UseItem:
    return UseItem(
        character_id=pf.named(game, "Tam"),
        item_id=staff,
        target_id=pf.named(game, target) if target is not None else None,
    )


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("target", ["Hild", None], ids=["another member", "its user"])
def test_a_second_use_on_a_creature_healed_today_is_refused_and_costs_nothing(target):
    game, staff = _explore_with_staff()
    patient = target or "Tam"
    wound(game, patient)
    assert game.execute(_staff_use(game, staff, target)).accepted
    wound(game, patient)
    clock, draws = game.clock.rounds, game.streams.get(MAGIC_STREAM).export_state()

    result = game.execute(_staff_use(game, staff, target))

    assert [(rejection.code, dict(rejection.params)) for rejection in result.rejections] == [
        (HEALED_TODAY, {"item": staff, "target": pf.named(game, patient)})
    ]
    assert result.events == ()
    assert hit_points(game, patient) == HURT
    assert game.clock.rounds == clock
    assert game.streams.get(MAGIC_STREAM).export_state() == draws


def test_the_staff_heals_another_creature_the_same_day():
    """Guard: the limit is for each creature."""
    game, staff = _explore_with_staff()
    assert game.execute(_staff_use(game, staff, "Hild")).accepted
    wound(game, "Osric")

    assert game.execute(_staff_use(game, staff, "Osric")).accepted
    assert hit_points(game, "Osric") > HURT


def test_the_staff_heals_the_same_creature_the_next_day():
    """Guard: the limit is a day."""
    game, staff = _explore_with_staff()
    assert game.execute(_staff_use(game, staff, "Hild")).accepted
    assert game.execute(AdvanceTime(n=1, unit=TimeUnit.DAY)).accepted
    wound(game, "Hild")

    assert game.execute(_staff_use(game, staff, "Hild")).accepted
    assert hit_points(game, "Hild") > HURT


def test_a_dead_creature_healed_today_gets_the_dead_code_first():
    """Guard: the session checks that the creature stands before it checks the day's use."""
    game, staff = _explore_with_staff()
    assert game.execute(_staff_use(game, staff, "Hild")).accepted
    kill(game.member(pf.named(game, "Hild")))

    result = game.execute(_staff_use(game, staff, "Hild"))

    assert [rejection.code for rejection in result.rejections] == ["items.use.target_cannot_be_healed"]


@pytest.mark.xfail(reason=CHUNK)
def test_a_battle_declaration_on_a_creature_healed_today_is_refused():
    game = fight()
    staff = grant(game, "Tam", "staff_of_healing")
    tam, hild = pf.named(game, "Tam"), pf.named(game, "Hild")
    wound(game, "Hild")
    pf.play(game, heal_with_staff(game, staff, hild))
    assert hit_points(game, "Hild") > HURT
    wound(game, "Hild")

    assert pf.refused(game, heal_with_staff(game, staff, hild)) == [
        (HEALED_TODAY, {"item": staff, "target": hild, "character": tam})
    ]


@pytest.mark.xfail(reason=CHUNK)
def test_a_staff_use_that_becomes_useless_by_its_step_fizzles(monkeypatch):
    """The staff's record of whom it healed today changes after the round was accepted.

    No declaration in a round can heal with the same staff before its holder's step, so the test writes the record
    the staff keeps in `MagicItemInstance.state`, under the key the session writes when it heals a creature. A save
    stores that state, so the key is part of the save's format.
    """
    game = fight()
    staff_id = grant(game, "Tam", "staff_of_healing")
    tam, hild = pf.named(game, "Tam"), pf.named(game, "Hild")
    staff = game.member(tam).inventory.magic_item(staff_id)
    wound(game, "Hild")
    original = battle_module._party_magic

    def healed_then_magic(session, *args, **kwargs):
        staff.state = {**staff.state, f"healed:{hild}": session.clock.days}
        return original(session, *args, **kwargs)

    monkeypatch.setattr(battle_module, "_party_magic", healed_then_magic)
    draws = game.streams.get(MAGIC_STREAM).export_state()

    events = pf.play(game, heal_with_staff(game, staff_id, hild))

    (fizzle,) = _fizzles(events, "magic.device.fizzled")
    assert (fizzle.actor_id, fizzle.item_id, fizzle.reason) == (tam, staff_id, HEALED_TODAY)
    assert not [event for event in events if event.code == "items.device.activated"]
    assert hit_points(game, "Hild") == HURT
    assert staff.identified is False
    assert game.streams.get(MAGIC_STREAM).export_state() == draws


# ------------------------------------------------------------------------------- findings from the chunk's review


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize(
    "fields",
    [
        {"group_labels": ("goblin",), "group_labels_plural": (), "group_counts": ()},
        {"group_labels": ("goblin", "orc"), "group_labels_plural": ("goblins", "orcs"), "group_counts": (2,)},
        {"group_labels": (), "group_labels_plural": ("goblins",), "group_counts": (2,)},
    ],
    ids=["a label alone", "a count missing", "no label"],
)
def test_an_encounter_event_refuses_group_fields_of_different_lengths(fields):
    # The three fields have one entry for each group, so an event whose tuples differ in length describes no
    # encounter. It's refused when it's built or loaded, and `format_message` never sees it.
    with pytest.raises(ValidationError):
        EncounterStartedEvent(monster_name="goblin", monster_name_plural="goblins", count=5, distance_feet=40, **fields)


def test_an_encounter_event_with_matching_or_empty_group_fields_is_accepted():
    old = EncounterStartedEvent(monster_name="goblin", monster_name_plural="goblins", count=5, distance_feet=40)
    assert old.group_labels == old.group_labels_plural == old.group_counts == ()
    new = EncounterStartedEvent(
        monster_name="goblin",
        monster_name_plural="goblins",
        count=5,
        distance_feet=40,
        group_labels=("goblin", "orc"),
        group_labels_plural=("goblins", "orcs"),
        group_counts=(2, 3),
    )
    assert EncounterStartedEvent.model_validate(new.model_dump(mode="json")) == new


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("item_id", ["magic-item-9999", "oil_flask"], ids=["an unknown id", "gear not carried"])
def test_a_use_of_an_item_the_member_doesnt_carry_names_the_item_before_the_group(item_id):
    # With a group named, the session already refuses this with `battle.declaration.item_unusable` and the item.
    # With no group it answered `battle.declaration.unknown_group`, which sends a player looking for a group
    # when the item is what's wrong. The item is judged first.
    game = fight()
    tam = pf.named(game, "Tam")

    rejections = pf.refused(game, pf.declare(tam, "use_item", item_id=item_id))

    assert rejections == [("battle.declaration.item_unusable", {"character": tam, "item": item_id})]


def test_a_thrown_flask_with_no_group_is_still_refused_for_the_group():
    # A flask the member does carry is usable, so the missing group is what's wrong.
    game = fight()
    tam = pf.named(game, "Tam")
    grant(game, "Tam", "oil_flask")

    rejections = pf.refused(game, pf.declare(tam, "use_item", item_id="oil_flask"))

    assert [code for code, _ in rejections] == ["battle.declaration.unknown_group"]
