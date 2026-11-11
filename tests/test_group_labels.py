"""What a monster group is called, and what the player view says about it (issues #228, #229, #230).

A group's label is its monsters' display name wherever the session makes the group, so the same
creature reads the same from a keyed area, a wandering monster check, and a
[`SpawnMonsters`][osrlib.crawl.commands.SpawnMonsters] (issue #162, item 8). The player view adds
the plural, a stable `family_id`, the group's `categories`, and a count for each visible condition.
"""

import pytest

from crawl_fixtures import build_party
from osrlib.core.effects import Condition, grant_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.tables import (
    EncounterTable,
    EncounterTableRow,
    MonsterEncounterEntry,
    NpcPartyEncounterEntry,
    ReactionResult,
)
from osrlib.crawl import exploration
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty, SpawnMonsters, SpawnNpcParty
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    WanderingSpec,
)
from osrlib.crawl.encounter import EncounterGroup, start_encounter
from osrlib.crawl.events import EncounterGroupJoinedEvent, EncounterStartedEvent
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game

FAR = 120
"""A distance no monster closes in one round, so a group is still there to read after it appears."""


# ---------------------------------------------------------------------- session helpers


def corridor(areas=(), wandering=None, width=8) -> LevelSpec:
    """A one-row level whose cells are joined by open edges, entered at the west end."""
    edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, width)}
    fields = {"wandering": wandering} if wandering is not None else {"wandering": WanderingSpec(chance_in_six=0)}
    return LevelSpec(number=1, width=width, height=1, entrance=(0, 0), edges=edges, areas=tuple(areas), **fields)


def session(level=None, monsters=()) -> GameSession:
    """The stock party, standing at the entrance of a one-level dungeon."""
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level or corridor(),))
    town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
    adventure = Adventure(name="A", town=town, dungeons=(crypt,), monsters=tuple(monsters))
    game = GameSession.new(build_party(), adventure, seed=7)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def lair(template_id: str, count: int) -> AreaSpec:
    """A keyed area one step east of the entrance, with friendly monsters in it."""
    keyed = KeyedEncounter(
        monsters=(KeyedMonster(template_id=template_id, count_fixed=count),), stance=ReactionResult.FRIENDLY
    )
    return AreaSpec(id="lair", cells=((1, 0),), encounter=keyed)


def table_of(row: EncounterTableRow) -> EncounterTable:
    """A wandering table whose every d20 result is `row`."""
    rows = tuple(row.model_copy(update={"roll": roll}) for roll in range(1, 21))
    return EncounterTable(id="always", label="Always", min_level=1, rows=rows)


def wandering_group(row: EncounterTableRow):
    """Run a wandering monster check that always hits on `row`, and return the session and the events."""
    game = session(corridor(wandering=WanderingSpec(chance_in_six=6, table=table_of(row))))
    events, encountered = exploration.wandering_check(game)
    assert encountered
    return game, events


def meet(template_id: str, label: str, count: int = 2) -> GameSession:
    """Open a friendly encounter with `count` of one monster, far away, under a label of the test's own."""
    game = session()
    instances = game.spawn(template_id, count)
    start_encounter(
        game,
        groups=[(label, instances)],
        kind="spawned",
        distance_feet=FAR,
        pinned_stance=ReactionResult.FRIENDLY,
    )
    return game


def only(events, event_type):
    found = [event for event in events if isinstance(event, event_type)]
    assert len(found) == 1, [event.code for event in events]
    return found[0]


def group_view(game: GameSession, index: int = 0):
    return game.view(Visibility.PLAYER).encounter.groups[index]


# ---------------------------------------------------------------------- labels, by where the group comes from


def test_a_keyed_group_is_labelled_with_the_display_name():
    game = session(corridor([lair("centipede_giant", 2)]))
    result = game.execute(MoveParty(direction=Direction.EAST))
    group = game.encounter.groups[0]
    assert (group.label, group.label_plural) == ("giant centipede", "giant centipedes")
    started = only(result.events, EncounterStartedEvent)
    assert (started.monster_name, started.monster_name_plural) == ("giant centipede", "giant centipedes")


def test_a_spawned_group_is_labelled_with_the_display_name_not_the_template_id():
    game = session()
    result = game.execute(SpawnMonsters(template_id="veteran_2", count_fixed=3, distance_feet=FAR))
    group = game.encounter.groups[0]
    assert (group.label, group.label_plural) == ("veteran", "veterans")
    started = only(result.events, EncounterStartedEvent)
    assert (started.monster_name, started.monster_name_plural) == ("veteran", "veterans")


def test_the_same_monster_reads_the_same_from_every_source():
    keyed = session(corridor([lair("fire_beetle", 2)]))
    keyed.execute(MoveParty(direction=Direction.EAST))
    spawned = session()
    spawned.execute(SpawnMonsters(template_id="fire_beetle", count_fixed=2, distance_feet=FAR))
    row = EncounterTableRow(
        roll=1, name="Beetle, Fire", entry=MonsterEncounterEntry(monster_ids=("fire_beetle",)), count_fixed=2
    )
    wandering, _ = wandering_group(row)
    for game in (keyed, spawned, wandering):
        view = group_view(game)
        assert (view.label, view.label_plural, view.family_id) == ("fire beetle", "fire beetles", "fire_beetle")


def test_a_group_that_joins_an_open_encounter():
    game = session()
    game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=FAR))
    result = game.execute(SpawnMonsters(template_id="normal_wolf", count_fixed=2, distance_feet=FAR))
    assert result.accepted
    joined = only(result.events, EncounterGroupJoinedEvent)
    assert (joined.label, joined.label_plural) == ("wolf", "wolves")
    group = game.encounter.groups[1]
    assert (group.label, group.label_plural) == ("wolf", "wolves")


def test_a_wandering_row_of_one_family_takes_the_display_name():
    """The row's printed name can state Hit Dice, as "Hydra 1d4+4HD" does, so it's never the label."""
    row = EncounterTableRow(
        roll=1,
        name="Veteran",
        entry=MonsterEncounterEntry(monster_ids=("veteran_1", "veteran_2", "veteran_3")),
        count_fixed=4,
    )
    game, events = wandering_group(row)
    group = game.encounter.groups[0]
    assert (group.label, group.label_plural) == ("veteran", "veterans")
    assert only(events, EncounterStartedEvent).monster_name == "veteran"
    assert group_view(game).family_id == "veteran"

    hydras = EncounterTableRow(
        roll=1,
        name="Hydra 1d4+4HD",
        entry=MonsterEncounterEntry(monster_ids=("hydra_5", "hydra_6", "hydra_7", "hydra_8"), variant_dice="1d4"),
        count_fixed=1,
    )
    game, _ = wandering_group(hydras)
    assert (game.encounter.groups[0].label, game.encounter.groups[0].label_plural) == ("hydra", "hydras")


def test_a_wandering_row_of_several_families_keeps_the_rows_name():
    row = EncounterTableRow(
        roll=1, name="Tomb guardians", entry=MonsterEncounterEntry(monster_ids=("skeleton", "zombie")), count_fixed=12
    )
    game, _ = wandering_group(row)
    drawn = {game.combatant(monster_id).template.id for monster_id in game.encounter.groups[0].monster_ids}
    assert drawn == {"skeleton", "zombie"}, "twelve draws from two ids should give both"
    group = game.encounter.groups[0]
    assert (group.label, group.label_plural) == ("Tomb guardians", "Tomb guardians")
    view = group_view(game)
    assert view.family_id is None
    assert view.categories == ("undead",)


@pytest.mark.parametrize("party_kind", ["basic", "expert"])
def test_a_party_of_npc_adventurers(party_kind):
    game = session()
    result = game.execute(SpawnNpcParty(party_kind=party_kind, distance_feet=FAR))
    group = game.encounter.groups[0]
    assert (group.label, group.label_plural) == ("adventurer", "adventurers")
    started = only(result.events, EncounterStartedEvent)
    assert (started.monster_name, started.monster_name_plural) == ("adventurer", "adventurers")
    view = group_view(game)
    assert view.family_id is None
    assert view.categories == ("person",)


def test_a_wandering_party_of_npc_adventurers():
    row = EncounterTableRow(
        roll=1, name="Basic Adventurers", entry=NpcPartyEncounterEntry(party_kind="basic"), count_dice="1d4+4"
    )
    game, _ = wandering_group(row)
    group = game.encounter.groups[0]
    assert (group.label, group.label_plural) == ("adventurer", "adventurers")


def test_a_template_of_your_own_names_its_group():
    """An adventure shows a creature under another name by giving it a template, which is its own family."""
    wight = session().spawn("wight", 1)[0].template
    document = wight.model_dump()
    document.update(
        id="hooded_figure",
        name="Hooded Figure",
        display_name="hooded figure",
        display_name_plural="hooded figures",
        family_id="hooded_figure",
    )
    game = session(monsters=(type(wight).model_validate(document),))
    game.execute(SpawnMonsters(template_id="hooded_figure", count_fixed=2, distance_feet=FAR))
    view = group_view(game)
    assert (view.label, view.label_plural, view.family_id) == ("hooded figure", "hooded figures", "hooded_figure")


# ---------------------------------------------------------------------- start_encounter with a label of your own


def test_start_encounter_takes_the_templates_plural_for_their_display_name():
    game = meet("normal_wolf", label="wolf")
    group = game.encounter.groups[0]
    assert (group.label, group.label_plural) == ("wolf", "wolves")


def test_start_encounter_gives_any_other_label_the_regular_ending():
    game = meet("wight", label="hooded figure")
    group = game.encounter.groups[0]
    assert (group.label, group.label_plural) == ("hooded figure", "hooded figures")
    game = meet("normal_wolf", label="grey shape in the brush")
    assert game.encounter.groups[0].label_plural == "grey shape in the brushes"


def test_the_view_reports_the_family_whatever_the_label_says():
    view = group_view(meet("wight", label="hooded figure"))
    assert view.label == "hooded figure"
    assert view.family_id == "wight"
    assert view.categories == ("undead",)


# ---------------------------------------------------------------------- the messages


@pytest.mark.parametrize(
    ("count", "line"), [(1, "Encounter: 1 giant centipede at "), (3, "Encounter: 3 giant centipedes at ")]
)
def test_the_encounter_line_uses_the_plural(count, line):
    game = session(corridor([lair("centipede_giant", count)]))
    result = game.execute(MoveParty(direction=Direction.EAST))
    assert format_message(only(result.events, EncounterStartedEvent)).startswith(line)


def test_the_joined_line_uses_the_plural():
    game = session()
    game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=FAR))
    result = game.execute(SpawnMonsters(template_id="normal_wolf", count_fixed=2, distance_feet=30))
    assert format_message(only(result.events, EncounterGroupJoinedEvent)) == (
        "More monsters join the encounter: 2 wolves at 30'."
    )
    result = game.execute(SpawnMonsters(template_id="ogre", count_fixed=1, distance_feet=30))
    assert format_message(only(result.events, EncounterGroupJoinedEvent)) == (
        "More monsters join the encounter: 1 ogre at 30'."
    )


# ---------------------------------------------------------------------- saves written without a plural


def test_a_group_without_a_plural_gets_the_regular_ending():
    group = EncounterGroup.model_validate(
        {"id": "group-0001", "label": "Goblin", "monster_ids": ["monster-0001"], "distance_feet": 30}
    )
    assert group.label_plural == "Goblins"


def test_events_without_a_plural_get_the_regular_ending():
    started = EncounterStartedEvent.model_validate(
        {"event_type": "encounter_started", "monster_name": "Goblin", "count": 3, "distance_feet": 30}
    )
    assert started.monster_name_plural == "Goblins"
    assert format_message(started) == "Encounter: 3 Goblins at 30'."
    joined = EncounterGroupJoinedEvent.model_validate(
        {
            "event_type": "encounter_group_joined",
            "group_id": "group-0002",
            "label": "fox",
            "count": 2,
            "distance_feet": 30,
        }
    )
    assert joined.label_plural == "foxes"


def test_the_plural_survives_a_save():
    game = meet("normal_wolf", label="wolf")
    loaded = load_game(save_game(game))
    assert loaded.encounter.groups[0].label_plural == "wolves"
    assert group_view(loaded).label_plural == "wolves"


# ---------------------------------------------------------------------- the player view: family and categories


@pytest.mark.parametrize(
    ("template_id", "family_id", "categories"),
    [
        ("goblin", "goblin", ("person",)),
        ("fire_beetle", "fire_beetle", ()),
        ("hellhound_5", "hellhound", ()),
        ("vampire_9", "vampire", ("undead",)),
        ("skeleton", "skeleton", ("undead",)),
        ("veteran_1", "veteran", ("person",)),
        ("centipede_giant", "centipede_giant", ()),
    ],
)
def test_the_view_says_what_kind_of_monster_a_group_is(template_id, family_id, categories):
    game = session()
    game.execute(SpawnMonsters(template_id=template_id, count_fixed=2, distance_feet=FAR))
    view = group_view(game)
    assert view.family_id == family_id
    assert view.categories == categories
    template = game.combatant(game.encounter.groups[0].monster_ids[0]).template
    assert (view.label, view.label_plural) == (template.display_name, template.display_name_plural)


def test_the_view_never_names_the_form():
    """A form's id states what the players can't see, like a hellhound's Hit Dice."""
    game = session()
    game.execute(SpawnMonsters(template_id="hellhound_5", count_fixed=2, distance_feet=FAR))
    assert "hellhound_5" not in game.view(Visibility.PLAYER).model_dump_json()


def test_the_family_stays_after_members_die():
    game = meet("skeleton", count=3, label="skeleton")
    kill(game.combatant(game.encounter.groups[0].monster_ids[0]))
    view = group_view(game)
    assert (view.count, view.family_id, view.categories) == (2, "skeleton", ("undead",))


# ---------------------------------------------------------------------- the player view: condition counts


def test_condition_counts_say_how_many_members_have_each_condition():
    game = meet("orc", count=11, label="orc")
    monsters = [game.combatant(monster_id) for monster_id in game.encounter.groups[0].monster_ids]
    for orc in monsters[:4]:
        grant_condition(orc, Condition.ASLEEP, None)
    view = group_view(game)
    assert view.count == 11
    assert view.visible_conditions == ("asleep",)
    assert view.condition_counts == {"asleep": 4}


def test_condition_counts_leave_out_the_dead():
    game = meet("orc", count=5, label="orc")
    monsters = [game.combatant(monster_id) for monster_id in game.encounter.groups[0].monster_ids]
    grant_condition(monsters[0], Condition.ASLEEP, None)
    grant_condition(monsters[1], Condition.ASLEEP, None)
    kill(monsters[0])
    view = group_view(game)
    assert view.count == 4
    assert view.condition_counts == {"asleep": 1}


def test_condition_counts_have_the_keys_of_visible_conditions_in_order():
    game = meet("orc", count=6, label="orc")
    monsters = [game.combatant(monster_id) for monster_id in game.encounter.groups[0].monster_ids]
    for orc in monsters[:3]:
        grant_condition(orc, Condition.PARALYSED, None)
    for orc in monsters[2:4]:
        grant_condition(orc, Condition.ASLEEP, None)
    view = group_view(game)
    assert view.visible_conditions == ("asleep", "paralysed")
    assert list(view.condition_counts) == ["asleep", "paralysed"]
    assert view.condition_counts == {"asleep": 2, "paralysed": 3}


def test_condition_counts_are_empty_when_nothing_shows():
    view = group_view(meet("orc", count=3, label="orc"))
    assert view.visible_conditions == ()
    assert view.condition_counts == {}
