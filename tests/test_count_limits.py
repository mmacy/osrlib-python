"""Upper limits on the referee commands' counts (issue #272).

A referee command whose count has no upper limit lets one command run for minutes or grow
the save document by hundreds of megabytes, and every later save and load pays for it. The
command models refuse a count larger than any game needs when they are constructed, the
way [`parse`][osrlib.core.dice.parse] refuses more than 999 dice, so the session never sees
the command. The limits:

- `SpawnMonsters` and `SpawnNpcParty` create at most 100 monsters or adventurers, whether
  the count is `count_fixed` or the largest total `count_dice` can roll. 100 is the largest
  number appearing in the B/X monster data, for normal bats and for wild horses in a lair.
- `GrantItem` grants at most 1,000 items.
- `AdvanceTime` advances at most 28 days in one command: 28 days, 4,032 turns, or 241,920
  rounds. 28 days is the longest time limit on raising the dead, for a 14th-level cleric, and
  a referee who wants more time to pass sends the command again.
"""

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_adventure, build_party
from osrlib.crawl.commands import (
    AdvanceTime,
    EnterDungeon,
    GrantItem,
    SpawnMonsters,
    SpawnNpcParty,
    TimeUnit,
    parse_command,
)
from osrlib.crawl.session import GameSession

CHUNK = pytest.mark.xfail(reason="chunk: count-limits")

LEAD = "character-0001"
SPAWN_LIMIT = 100
GRANT_LIMIT = 1_000
DAYS_LIMIT = 28
TURNS_LIMIT = DAYS_LIMIT * 144
ROUNDS_LIMIT = TURNS_LIMIT * 60


def new_session() -> GameSession:
    return GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=31)


# SpawnMonsters


def test_spawn_monsters_accepts_the_limit() -> None:
    command = SpawnMonsters(template_id="goblin", count_fixed=SPAWN_LIMIT, distance_feet=30)
    assert command.count_fixed == SPAWN_LIMIT


@CHUNK
def test_spawn_monsters_refuses_a_fixed_count_over_the_limit() -> None:
    with pytest.raises(ValidationError):
        SpawnMonsters(template_id="goblin", count_fixed=SPAWN_LIMIT + 1, distance_feet=30)


@pytest.mark.parametrize("dice", ["1d100", "1d10x10", "6d10", "2d6+88"])
def test_spawn_monsters_accepts_dice_that_reach_the_limit(dice: str) -> None:
    assert SpawnMonsters(template_id="goblin", count_dice=dice, distance_feet=30).count_dice == dice


@CHUNK
@pytest.mark.parametrize("dice", ["999d100", "1d6x999999", "2d6+89", "1d100+1", "11d10", "1d4x26"])
def test_spawn_monsters_refuses_dice_that_can_roll_past_the_limit(dice: str) -> None:
    with pytest.raises(ValidationError):
        SpawnMonsters(template_id="goblin", count_dice=dice, distance_feet=30)


@CHUNK
def test_spawn_monsters_document_over_the_limit_fails_to_parse() -> None:
    document = {"command_type": "spawn_monsters", "template_id": "goblin", "count_fixed": 10_000, "distance_feet": 30}
    with pytest.raises(ValidationError):
        parse_command(document)


@CHUNK
def test_spawn_monsters_schema_states_the_limit() -> None:
    schema = SpawnMonsters.model_json_schema()
    count_fixed = schema["properties"]["count_fixed"]
    maxima = [branch.get("maximum") for branch in count_fixed.get("anyOf", [count_fixed])]
    assert SPAWN_LIMIT in maxima


def test_a_session_spawns_the_limit() -> None:
    session = new_session()
    session.execute(EnterDungeon(dungeon_id="delve"))
    result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=SPAWN_LIMIT, distance_feet=30))
    assert result.accepted
    assert session.encounter is not None
    assert sum(len(group.monster_ids) for group in session.encounter.groups) == SPAWN_LIMIT


# SpawnNpcParty


def test_spawn_npc_party_accepts_the_limit() -> None:
    command = SpawnNpcParty(party_kind="basic", count_fixed=SPAWN_LIMIT, distance_feet=30)
    assert command.count_fixed == SPAWN_LIMIT


@CHUNK
def test_spawn_npc_party_refuses_a_fixed_count_over_the_limit() -> None:
    with pytest.raises(ValidationError):
        SpawnNpcParty(party_kind="basic", count_fixed=SPAWN_LIMIT + 1, distance_feet=30)


@CHUNK
@pytest.mark.parametrize("dice", ["999d100", "1d6x999999", "1d100+1"])
def test_spawn_npc_party_refuses_dice_that_can_roll_past_the_limit(dice: str) -> None:
    with pytest.raises(ValidationError):
        SpawnNpcParty(party_kind="expert", count_dice=dice, distance_feet=30)


def test_spawn_npc_party_accepts_dice_that_reach_the_limit() -> None:
    assert SpawnNpcParty(party_kind="expert", count_dice="1d100", distance_feet=30).count_dice == "1d100"


def test_spawn_npc_party_default_size_is_still_legal() -> None:
    command = SpawnNpcParty(party_kind="basic", distance_feet=30)
    assert command.count_fixed is None and command.count_dice is None


# GrantItem


def test_grant_item_accepts_the_limit() -> None:
    assert GrantItem(character_id=LEAD, item_id="torch", quantity=GRANT_LIMIT).quantity == GRANT_LIMIT


@CHUNK
def test_grant_item_refuses_a_quantity_over_the_limit() -> None:
    with pytest.raises(ValidationError):
        GrantItem(character_id=LEAD, item_id="torch", quantity=GRANT_LIMIT + 1)


@CHUNK
def test_grant_item_document_over_the_limit_fails_to_parse() -> None:
    document = {"command_type": "grant_item", "character_id": LEAD, "item_id": "torch", "quantity": 1_000_000}
    with pytest.raises(ValidationError):
        parse_command(document)


@CHUNK
def test_grant_item_schema_states_the_limit() -> None:
    assert GrantItem.model_json_schema()["properties"]["quantity"]["maximum"] == GRANT_LIMIT


def test_a_session_grants_the_limit() -> None:
    session = new_session()
    result = session.execute(GrantItem(character_id=LEAD, item_id="torch", quantity=GRANT_LIMIT))
    assert result.accepted


# AdvanceTime


@pytest.mark.parametrize(
    ("n", "unit"),
    [(DAYS_LIMIT, TimeUnit.DAY), (TURNS_LIMIT, TimeUnit.TURN), (ROUNDS_LIMIT, TimeUnit.ROUND), (0, TimeUnit.DAY)],
)
def test_advance_time_accepts_up_to_the_limit(n: int, unit: TimeUnit) -> None:
    assert AdvanceTime(n=n, unit=unit).n == n


@CHUNK
@pytest.mark.parametrize(
    ("n", "unit"),
    [
        (DAYS_LIMIT + 1, TimeUnit.DAY),
        (TURNS_LIMIT + 1, TimeUnit.TURN),
        (ROUNDS_LIMIT + 1, TimeUnit.ROUND),
        (10**30, TimeUnit.TURN),
    ],
)
def test_advance_time_refuses_more_than_the_limit(n: int, unit: TimeUnit) -> None:
    with pytest.raises(ValidationError):
        AdvanceTime(n=n, unit=unit)


@CHUNK
def test_advance_time_document_over_the_limit_fails_to_parse() -> None:
    with pytest.raises(ValidationError):
        parse_command({"command_type": "advance_time", "n": 100_000, "unit": "turn"})


def test_a_session_advances_the_limit() -> None:
    session = new_session()
    before = session.clock.rounds
    result = session.execute(AdvanceTime(n=DAYS_LIMIT, unit=TimeUnit.DAY))
    assert result.accepted
    assert session.clock.rounds - before == ROUNDS_LIMIT
