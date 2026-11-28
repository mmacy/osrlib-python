"""The player-visible item events name the items they list (issue #325).

A client that replays an event log, or that didn't keep the view from before a command, has only the events. Each
player-visible item event has `item_names` and `item_names_plural`, keyed by the ids it lists, and
`EquipmentDestroyedEvent` has `saved_item_names` and `saved_item_names_plural` for `saved_items`. The names come from
the function that names items for `PlayerView.item_names`, so an event and a view never name one id two ways, and an
unidentified magic item has its masked name.

Each name is the one the party knows when the event happens. Every test here plays a command and checks each item
event in its result against the views from before and after the command: an id that an `ItemIdentifiedEvent` of the
same command names has that event's `display_name`, an id the view after the command shows has that name, and any
other id has the name the view before it gave. So no event name tells the players more than the view after the
command and the command's own identification.
"""

from collections.abc import Iterable

import pytest

import positions_fixtures as pf
from crawl_fixtures import build_party
from osrlib.core.effects import kill
from osrlib.core.events import DeclarationFizzledEvent, EquipmentDestroyedEvent, Event, Visibility
from osrlib.core.ruleset import EncumbranceMode, Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    BattleDeclaration,
    Command,
    DropItems,
    EndEncounter,
    EnterDungeon,
    EquipItem,
    Evade,
    ForceDoor,
    GiveItems,
    GrantItem,
    MoveParty,
    ResolveBattleRound,
    SellTreasure,
    SpawnMonsters,
    SpawnNpcParty,
    TakeTreasure,
    TravelToTown,
    UseItem,
    WedgeDoor,
)
from osrlib.crawl.dungeon import AreaSpec, Direction, DungeonSpec, FeatureSpec, LevelSpec, ValuableSpec
from osrlib.crawl.events import (
    CurseRevealedEvent,
    ItemAcquiredEvent,
    ItemConsumedEvent,
    ItemIdentifiedEvent,
    ItemsDroppedEvent,
    ItemsGivenEvent,
    ItemsLeftBehindEvent,
    ItemUsedEvent,
    LootDroppedEvent,
    TreasureSoldEvent,
)
from osrlib.crawl.session import GameSession, SessionMode
from osrlib.crawl.views import PlayerView
from osrlib.messages import format_message
from test_exploration import entered, quiet_session
from test_loot_and_traps import breath_kill
from test_member_reach import feed, fight, grant, hand
from test_session_reports import _kill_before_the_magic_phase
from test_view_item_names import TEMPLE_IDOL

FIGHTER = "character-0001"
SECOND = "character-0002"


def _ids(event: Event) -> tuple[str, ...]:
    """The ids an item event lists, in the field each event class lists them in."""
    if isinstance(event, EquipmentDestroyedEvent):
        return event.saved_items
    if isinstance(event, ItemConsumedEvent):
        return (event.item_id,)
    if isinstance(event, ItemUsedEvent | CurseRevealedEvent):
        return (event.instance_id,)
    if isinstance(event, TreasureSoldEvent):
        return event.instance_ids
    if isinstance(event, DeclarationFizzledEvent):
        return () if event.item_id is None else (event.item_id,)
    return event.item_ids


def _names(event: Event) -> tuple[dict[str, str], dict[str, str]]:
    if isinstance(event, EquipmentDestroyedEvent):
        return dict(event.saved_item_names), dict(event.saved_item_names_plural)
    return dict(event.item_names), dict(event.item_names_plural)


NAMED = (
    ItemAcquiredEvent,
    ItemConsumedEvent,
    ItemsDroppedEvent,
    ItemsLeftBehindEvent,
    ItemsGivenEvent,
    LootDroppedEvent,
    ItemUsedEvent,
    DeclarationFizzledEvent,
    TreasureSoldEvent,
    CurseRevealedEvent,
    EquipmentDestroyedEvent,
)


def _view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def assert_named_as_the_views(before: PlayerView, after: PlayerView, events: Iterable[Event]) -> list[Event]:
    """Check every item event of one command against the views around it, and return the item events."""
    events = list(events)
    identified = {
        event.instance_id: (event.display_name, event.display_name_plural)
        for event in events
        if isinstance(event, ItemIdentifiedEvent)
    }
    checked = []
    for event in events:
        if not isinstance(event, NAMED):
            continue
        assert event.visibility is Visibility.PLAYER
        names, plurals = _names(event)
        ids = set(_ids(event))
        assert set(names) == ids and set(plurals) == ids, (event, names)
        for item_id in ids:
            if item_id in identified:
                expected = identified[item_id]
            elif item_id in after.item_names:
                expected = (after.item_names[item_id], after.item_names_plural[item_id])
            else:
                expected = (before.item_names[item_id], before.item_names_plural[item_id])
            assert (names[item_id], plurals[item_id]) == expected, (event, item_id)
        checked.append(event)
    return checked


def play(game: GameSession, command: Command) -> list[Event]:
    """Execute an accepted command and check its item events against the views. Returns the item events."""
    before = _view(game)
    result = game.execute(command)
    assert result.accepted, [rejection.code for rejection in result.rejections]
    return assert_named_as_the_views(before, _view(game), result.events)


def _only[T](events: Iterable[Event], event_class: type[T]) -> T:
    (event,) = [event for event in events if isinstance(event, event_class)]
    return event


# ---------------------------------------------------------------------- the shrine


def _shrine(*, heavy: bool = False) -> Adventure:
    """One cell in an area whose reliquary holds holy water, a bundled idol, a potion, a ring, and two valuables.

    With `heavy`, the reliquary also holds more plate mail than the party can carry.
    """
    reliquary = FeatureSpec(
        id="reliquary",
        kind="treasure_cache",
        description="A lead-lined reliquary.",
        cell=(0, 0),
        item_ids=("holy_water", "holy_water", "temple_idol", *(("plate_mail",) * 40 if heavy else ())),
        magic_item_ids=("potion_of_healing", "ring_of_weakness"),
        valuables=(
            ValuableSpec(kind="jewellery", name="The abbot's seal ring", value_gp=900),
            ValuableSpec(kind="gem", value_gp=50),
        ),
    )
    level = LevelSpec(
        number=1,
        width=1,
        height=1,
        entrance=(0, 0),
        areas=(AreaSpec(id="vault", name="Vault", cells=((0, 0),), features=(reliquary,)),),
    )
    return Adventure(
        name="The Drowned Saint",
        town=TownSpec(name="Threshold", travel_turns={"shrine": 1}),
        dungeons=(DungeonSpec(id="shrine", name="The Shrine", levels=(level,)),),
        items=(TEMPLE_IDOL,),
    )


def _looted(*, heavy: bool = False) -> tuple[GameSession, list[Event]]:
    # Detailed encumbrance weighs the armour, so the party can't carry 40 suits of plate mail.
    ruleset = Ruleset(encumbrance=EncumbranceMode.DETAILED) if heavy else None
    game = GameSession.new(build_party(), _shrine(heavy=heavy), seed=5, ruleset=ruleset)
    assert game.execute(EnterDungeon(dungeon_id="shrine")).accepted
    (feature,) = _view(game).explored[0].features
    return game, play(game, TakeTreasure(feature_id=feature.id))


def _carried(game: GameSession, template_id: str) -> tuple[str, str]:
    """The member who carries the magic item made from `template_id`, and its instance id."""
    for member in game.party.members:
        for instance in member.inventory.all_instances():
            if getattr(instance, "template_id", None) == template_id:
                return member.id, instance.instance_id
    raise AssertionError(f"nobody carries {template_id}")


def test_a_take_names_each_item_the_way_the_view_does_and_masks_the_magic_items():
    game, events = _looted()
    _, potion = _carried(game, "potion_of_healing")
    _, ring = _carried(game, "ring_of_weakness")

    names: dict[str, str] = {}
    for event in events:
        assert isinstance(event, ItemAcquiredEvent)
        names.update(event.item_names)
    assert names["holy_water"] == "vial of holy water"
    assert names["temple_idol"] == "idol of the drowned saint", "a bundled item is named from the session's catalog"
    assert names[potion] == "potion"
    assert names[ring] == "ring", "a cursed ring is named as any unidentified ring is"
    assert sorted(name for item_id, name in names.items() if item_id.startswith("valuable-")) == [
        "gem",
        "piece of jewellery",
    ]
    dumped = " ".join(event.model_dump_json() for event in events)
    assert "healing" not in dumped and "weakness" not in dumped, "no event names an unidentified item's template"


def test_the_names_of_an_event_count_each_unit_once_per_id():
    """A take lists holy water once for each vial, and the names have one key for it, with a plural to count by."""
    _, events = _looted()
    (event,) = [event for event in events if "holy_water" in event.item_ids]
    assert isinstance(event, ItemAcquiredEvent)
    assert event.item_ids.count("holy_water") == 2
    assert (event.item_names["holy_water"], event.item_names_plural["holy_water"]) == (
        "vial of holy water",
        "vials of holy water",
    )


def test_what_the_party_cannot_carry_is_named_as_it_is_left_behind():
    game, events = _looted(heavy=True)
    left = _only(events, ItemsLeftBehindEvent)
    assert "plate_mail" in left.item_ids
    assert left.item_names["plate_mail"] == "suit of plate mail"


def test_a_drop_and_a_give_name_what_moved():
    game, _ = _looted()
    holder, potion = _carried(game, "potion_of_healing")
    recipient = next(member.id for member in game.party.members if member.id != holder)

    given = _only(
        play(game, GiveItems(character_id=holder, recipient_id=recipient, item_ids=(potion,))), ItemsGivenEvent
    )
    assert dict(given.item_names) == {potion: "potion"}

    dropped = _only(play(game, DropItems(character_id=recipient, item_ids=(potion,))), ItemsDroppedEvent)
    assert (dict(dropped.item_names), dict(dropped.item_names_plural)) == ({potion: "potion"}, {potion: "potions"})


def test_a_potion_drunk_unidentified_is_named_by_the_name_its_identification_gives():
    """The drink identifies the potion just before the use. The use names it as the identification does.

    No view names the potion by its true name: the view before showed it masked and the view after doesn't have it.
    The `ItemIdentifiedEvent` of the same command already names it, so the use's name tells the players nothing more.
    """
    game, _ = _looted()
    holder, potion = _carried(game, "potion_of_healing")
    before = _view(game)

    events = play(game, UseItem(character_id=holder, item_id=potion))

    used = _only(events, ItemUsedEvent)
    assert dict(used.item_names) == {potion: "potion of healing"}
    assert dict(used.item_names_plural) == {potion: "potions of healing"}
    assert before.item_names[potion] == "potion"
    assert potion not in _view(game).item_names


def test_a_curse_revealed_on_wearing_names_the_ring_as_identified():
    """Putting on the ring of weakness identifies it and reveals the curse in one command."""
    game, _ = _looted()
    holder, ring = _carried(game, "ring_of_weakness")

    events = play(game, EquipItem(character_id=holder, item_id=ring))

    revealed = _only(events, CurseRevealedEvent)
    assert dict(revealed.item_names) == {ring: "ring of weakness"}
    assert _view(game).item_names[ring] == "ring of weakness"


def test_treasure_sold_in_town_is_named_though_it_leaves_the_party():
    game, _ = _looted()
    assert game.execute(TravelToTown()).accepted
    seller = next(member for member in game.party.members if member.inventory.valuables)
    valuables = tuple(valuable.instance_id for valuable in seller.inventory.valuables)

    sold = _only(play(game, SellTreasure(item_ids=valuables)), TreasureSoldEvent)

    assert set(sold.item_names) == set(valuables)
    assert set(sold.item_names.values()) <= {"gem", "piece of jewellery"}


# ---------------------------------------------------------------------- gates, doors, and loot


def test_a_wedged_door_names_the_spike_it_takes():
    game = quiet_session(seed=3)
    entered(game)
    game.execute(MoveParty(direction=Direction.EAST))
    game.execute(MoveParty(direction=Direction.EAST))
    while game.execute(ForceDoor(direction=Direction.SOUTH, character_id=FIGHTER)).events[0].code != (
        "exploration.door.forced"
    ):
        pass

    consumed = _only(play(game, WedgeDoor(direction=Direction.SOUTH)), ItemConsumedEvent)

    assert (dict(consumed.item_names), dict(consumed.item_names_plural)) == (
        {"iron_spikes": "iron spike"},
        {"iron_spikes": "iron spikes"},
    )


def test_the_loot_of_slain_adventurers_is_named_as_the_pile_names_it():
    from test_event_gaps import session

    game = session()
    assert game.execute(SpawnNpcParty(party_kind="basic", distance_feet=120)).accepted
    for group in game.encounter.groups:
        for monster_id in group.monster_ids:
            kill(game.combatant(monster_id))

    loot = _only(play(game, EndEncounter(outcome="departed")), LootDroppedEvent)

    assert loot.item_names, "the adventurers carried gear"
    assert set(loot.item_names) == set(loot.item_ids)


def test_food_dropped_while_fleeing_is_named_though_it_is_gone():
    """The rations an `Evade` drops are left on the trail, so no view built after the command names them."""
    from test_event_gaps import session

    for seed in range(80):
        game = session(seed=seed)
        for member in game.party.members:
            game.execute(GrantItem(character_id=member.id, item_id="rations_standard"))
        game.execute(SpawnMonsters(template_id="normal_wolf", count_fixed=2, distance_feet=60))
        if game.mode is not SessionMode.ENCOUNTER:
            continue
        dropped = [event for event in play(game, Evade(drop="food")) if isinstance(event, ItemsDroppedEvent)]
        if not dropped:
            continue
        for event in dropped:
            assert dict(event.item_names) == {"rations_standard": "standard ration"}
        return
    pytest.fail("no seed dropped food")


# ---------------------------------------------------------------------- battle


def test_a_potion_that_fizzles_keeps_its_mask(monkeypatch):
    game = fight()
    potion = grant(game, "Tam", "potion_of_healing")
    _kill_before_the_magic_phase(monkeypatch, "Hild")

    events = play(game, ResolveBattleRound(declarations=pf.plan(game, feed(game, potion))))

    fizzle = _only(events, DeclarationFizzledEvent)
    assert fizzle.code == "magic.potion.fizzled"
    assert dict(fizzle.item_names) == {potion: "potion"}


def test_a_hand_over_that_fizzles_names_the_item(monkeypatch):
    game = fight()
    grant(game, "Tam", "oil_flask")
    _kill_before_the_magic_phase(monkeypatch, "Hild")

    events = play(game, ResolveBattleRound(declarations=pf.plan(game, hand(game, "oil_flask"))))

    fizzle = _only(events, DeclarationFizzledEvent)
    assert fizzle.code == "battle.give.fizzled"
    assert dict(fizzle.item_names) == {"oil_flask": "flask of oil"}


def test_a_battle_round_with_no_item_declaration_names_nothing():
    game = fight()
    declarers = _view(game).encounter.declarers
    hold = ResolveBattleRound(
        declarations=tuple(BattleDeclaration(character_id=member, action="hold") for member in declarers)
    )
    assert play(game, hold) == []


def test_the_magic_items_that_survive_a_breath_are_named_masked():
    """The swords that save are unidentified, so they're named "sword with a faint aura". The `item_names` of the
    destroyed items keeps its own form, with the article."""
    _, _, event = breath_kill(need_saved=True)

    assert event.saved_items
    assert dict(event.saved_item_names) == dict.fromkeys(event.saved_items, "sword with a faint aura")
    assert dict(event.saved_item_names_plural) == dict.fromkeys(event.saved_items, "swords with a faint aura")
    assert all(name == "a sword with a faint aura" for name in event.item_names)


# ---------------------------------------------------------------------- the default lines


def test_the_identification_line_names_the_item():
    game, _ = _looted()
    holder, potion = _carried(game, "potion_of_healing")

    result = game.execute(UseItem(character_id=holder, item_id=potion))

    identified = _only(result.events, ItemIdentifiedEvent)
    assert format_message(identified) == f"{potion} is identified: potion of healing."


def test_an_identification_from_before_the_names_falls_back_to_the_template_id():
    event = ItemIdentifiedEvent(instance_id="magic-item-0001", template_id="ring_of_weakness")
    assert format_message(event) == "magic-item-0001 is identified: ring_of_weakness."


def test_an_event_from_before_the_names_reads_with_them_empty():
    event = ItemAcquiredEvent.model_validate({"character_id": FIGHTER, "item_ids": ["torch"]})
    assert (dict(event.item_names), dict(event.item_names_plural)) == ({}, {})
    fizzle = DeclarationFizzledEvent(code="magic.device.fizzled", actor_id="a", item_id="w", reason="r")
    assert dict(fizzle.item_names) == {}


def test_the_names_are_read_only():
    _, events = _looted()
    event = events[0]
    assert isinstance(event, ItemAcquiredEvent)
    with pytest.raises(TypeError):
        event.item_names["torch"] = "torch"  # type: ignore[index]
