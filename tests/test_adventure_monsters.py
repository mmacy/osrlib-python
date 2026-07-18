"""Adventure-bundled monsters (phase 9): the field, validation, engine paths, persistence."""

import json

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.alignment import Alignment
from osrlib.core.monsters import MonsterTemplate
from osrlib.core.tables import EncounterTable, EncounterTableRow, MonsterEncounterEntry, ReactionResult
from osrlib.crawl import exploration
from osrlib.crawl.adventure import Adventure, validate_adventure
from osrlib.crawl.commands import (
    BattleDeclaration,
    EnterDungeon,
    EquipItem,
    GrantItem,
    LightSource,
    ListenAtDoor,
    MoveParty,
    PlaceParty,
    ResolveBattleRound,
    SpawnMonsters,
    TakeTreasure,
    TravelToTown,
)
from osrlib.crawl.dungeon import Direction, KeyedEncounter, KeyedMonster, PartyLocation, WanderingSpec
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.persistence import load_game, replay_game, save_game, session_state

CUSTOM_ID = "cave_wretch"


def wretch(monster_id: str = CUSTOM_ID, **overrides) -> MonsterTemplate:
    """A deliberately fragile custom monster: 1 fixed hp, AC 9 [10], a bespoke 7 XP."""
    data = {
        "id": monster_id,
        "name": "Cave Wretch",
        "page": "Custom",
        "ac": 9,
        "ac_ascending": 10,
        "hit_dice": {"count": 0, "fixed_hp": 1},
        "attacks": ({"attacks": ({"name": "claw", "damage": "1d2"},)},),
        "thac0": 19,
        "attack_bonus": 0,
        "movement": ({"rate_feet": 90, "encounter_rate_feet": 30},),
        "saves": {
            "values": {"death": 14, "wands": 15, "paralysis": 16, "breath": 17, "spells": 18},
            "save_as": "NH",
        },
        "morale": 12,
        "alignment": {"options": ("chaotic",)},
        "xp": 7,
        "number_appearing": {"dungeon": {"dice": "1d4"}, "lair": {"dice": "1d6"}},
        "treasure": {"letters": ("R",)},
        "categories": ("humanoid",),
    }
    data.update(overrides)
    return MonsterTemplate.model_validate(data)


def bundled_adventure(
    *,
    monsters: tuple[MonsterTemplate, ...] | None = None,
    keyed: KeyedEncounter | None = None,
    wandering_chance: int = 0,
    table: EncounterTable | None = None,
) -> Adventure:
    """The fixture delve with room_a's keyed encounter, the bundle, or level 1's wandering table swapped."""
    adventure = build_adventure(wandering_chance=wandering_chance)
    dungeon = adventure.dungeons[0]
    level_1 = dungeon.levels[0]
    if keyed is not None:
        areas = tuple(
            area.model_copy(update={"encounter": keyed}) if area.id == "room_a" else area for area in level_1.areas
        )
        level_1 = level_1.model_copy(update={"areas": areas})
    if table is not None:
        level_1 = level_1.model_copy(
            update={"wandering": WanderingSpec(chance_in_six=wandering_chance, interval_turns=2, table=table)}
        )
    dungeon = dungeon.model_copy(update={"levels": (level_1, dungeon.levels[1])})
    update: dict = {"dungeons": (dungeon,)}
    if monsters is not None:
        update["monsters"] = monsters
    return adventure.model_copy(update=update)


def wretch_table() -> EncounterTable:
    row_template = {
        "name": "Cave Wretch",
        "entry": MonsterEncounterEntry(monster_ids=(CUSTOM_ID,)),
        "count_fixed": 1,
    }
    return EncounterTable(
        id="wretches_only",
        label="Wretches only",
        min_level=1,
        max_level=None,
        rows=tuple(EncounterTableRow(roll=roll, **row_template) for roll in range(1, 21)),
    )


def lit_session(adventure: Adventure, seed: int = 5, *, armed: bool = False) -> GameSession:
    session = GameSession.new(build_party(), adventure, seed=seed)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    if armed:
        session.execute(GrantItem(character_id="character-0001", item_id="sword"))
        session.execute(EquipItem(character_id="character-0001", item_id="sword"))
    session.execute(EnterDungeon(dungeon_id="delve"))
    for _ in range(20):
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            return session
    raise AssertionError("torch never lit in twenty tinder attempts")


def into_room_a(session: GameSession) -> None:
    """Walk into room_a through arrival processing (the door state opened directly)."""
    session.execute(
        PlaceParty(
            location=PartyLocation(
                kind="dungeon", dungeon_id="delve", level_number=1, position=(2, 0), facing=Direction.SOUTH
            )
        )
    )
    session.dungeon_state.door("delve:1:2,1:north").open = True
    session.execute(MoveParty(direction=Direction.SOUTH))


class TestTheField:
    def test_the_default_is_the_empty_tuple(self):
        adventure = build_adventure()
        assert adventure.monsters == ()
        assert adventure.model_dump(mode="json")["monsters"] == []

    def test_a_bundled_template_round_trips_the_document(self):
        adventure = bundled_adventure(monsters=(wretch(),))
        reloaded = Adventure.model_validate(json.loads(json.dumps(adventure.model_dump(mode="json"))))
        assert reloaded == adventure
        assert reloaded.monsters[0].id == CUSTOM_ID

    def test_a_bundled_template_survives_save_and_load(self):
        session = GameSession.new(build_party(), bundled_adventure(monsters=(wretch(),)), seed=5)
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)
        assert restored.adventure.monsters == (wretch(),)

    def test_a_version_2_save_without_the_key_loads_with_the_default(self):
        session = GameSession.new(build_party(), build_adventure(), seed=5)
        document = json.loads(json.dumps(save_game(session)))
        del document["payload"]["adventure"]["monsters"]
        restored = load_game(document)
        assert restored.adventure.monsters == ()


class TestValidation:
    def test_duplicate_bundled_ids_fail_the_standard_gate(self):
        adventure = bundled_adventure(monsters=(wretch(), wretch()))
        with pytest.raises(ContentValidationError, match="adventure validation failed") as excinfo:
            validate_adventure(adventure, load_monsters(), load_equipment())
        assert f"bundled monster id {CUSTOM_ID!r} collides with the catalog" in str(excinfo.value)

    def test_a_base_catalog_collision_names_every_colliding_id(self):
        adventure = bundled_adventure(monsters=(wretch("goblin"), wretch("skeleton")))
        with pytest.raises(ContentValidationError) as excinfo:
            validate_adventure(adventure, load_monsters(), load_equipment())
        message = str(excinfo.value)
        assert "bundled monster id 'goblin' collides with the catalog" in message
        assert "bundled monster id 'skeleton' collides with the catalog" in message

    def test_a_collision_reports_no_spurious_unknown_monster_echo(self):
        # room_a keys 'goblin'; the colliding bundle must produce exactly the
        # collision line — keyed references resolve the dedup union's first
        # occurrence and stay quiet.
        adventure = bundled_adventure(monsters=(wretch("goblin"),))
        with pytest.raises(ContentValidationError) as excinfo:
            validate_adventure(adventure, load_monsters(), load_equipment())
        message = str(excinfo.value)
        assert "bundled monster id 'goblin' collides with the catalog" in message
        assert "unknown monster" not in message

    def test_a_keyed_reference_to_a_bundled_id_validates(self):
        adventure = bundled_adventure(
            monsters=(wretch(),),
            keyed=KeyedEncounter(monsters=(KeyedMonster(template_id=CUSTOM_ID, count_fixed=2),)),
        )
        validate_adventure(adventure, load_monsters(), load_equipment())

    def test_a_dangling_bundled_looking_id_still_fails(self):
        adventure = bundled_adventure(
            keyed=KeyedEncounter(monsters=(KeyedMonster(template_id=CUSTOM_ID, count_fixed=2),)),
        )
        with pytest.raises(ContentValidationError, match="unknown monster"):
            validate_adventure(adventure, load_monsters(), load_equipment())

    def test_alignment_pins_resolve_against_the_bundled_template(self):
        keyed = KeyedEncounter(
            monsters=(KeyedMonster(template_id=CUSTOM_ID, count_fixed=1),), alignment=Alignment.CHAOTIC
        )
        validate_adventure(bundled_adventure(monsters=(wretch(),), keyed=keyed), load_monsters(), load_equipment())
        pinned_outside = keyed.model_copy(update={"alignment": Alignment.LAWFUL})
        with pytest.raises(ContentValidationError, match="pins alignment"):
            validate_adventure(
                bundled_adventure(monsters=(wretch(),), keyed=pinned_outside), load_monsters(), load_equipment()
            )

    def test_inline_wandering_rows_resolve_against_the_union(self):
        validate_adventure(
            bundled_adventure(monsters=(wretch(),), table=wretch_table()), load_monsters(), load_equipment()
        )
        with pytest.raises(ContentValidationError, match="wandering row"):
            validate_adventure(bundled_adventure(table=wretch_table()), load_monsters(), load_equipment())

    def test_npc_party_wandering_rows_pass_untouched(self):
        row_template = {"name": "Basic Adventurers", "entry": {"kind": "npc_party", "party_kind": "basic"}}
        table = EncounterTable(
            id="npcs_only",
            label="NPCs only",
            min_level=1,
            max_level=None,
            rows=tuple(EncounterTableRow(roll=roll, count_dice="1d4", **row_template) for roll in range(1, 21)),
        )
        validate_adventure(bundled_adventure(table=table), load_monsters(), load_equipment())


class TestTheEngine:
    def test_a_keyed_encounter_spawns_a_bundled_template_on_arrival(self):
        adventure = bundled_adventure(
            monsters=(wretch(),),
            keyed=KeyedEncounter(monsters=(KeyedMonster(template_id=CUSTOM_ID, count_fixed=2),), aware=True),
        )
        session = lit_session(adventure)
        into_room_a(session)
        assert session.encounter is not None and session.encounter.kind == "keyed"
        assert {instance.template.id for instance in session.monsters.values()} == {CUSTOM_ID}

    def test_spawn_monsters_spawns_a_bundled_id(self):
        session = lit_session(bundled_adventure(monsters=(wretch(),)))
        result = session.execute(SpawnMonsters(template_id=CUSTOM_ID, count_fixed=1, distance_feet=30))
        assert result.accepted
        spawned = next(event for event in result.events if event.code == "session.monsters.spawned")
        assert spawned.template_id == CUSTOM_ID

    def test_a_listen_check_reads_a_bundled_template_categories(self):
        # An undead bundled monster is never noisy: whatever the detection rolls,
        # no listener ever hears the room.
        adventure = bundled_adventure(
            monsters=(wretch("grave_wretch", categories=("undead",)),),
            keyed=KeyedEncounter(monsters=(KeyedMonster(template_id="grave_wretch", count_fixed=3),)),
        )
        session = lit_session(adventure)
        session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="delve", level_number=1, position=(2, 0), facing=Direction.SOUTH
                )
            )
        )
        for character_id in ("character-0001", "character-0002", "character-0003", "character-0004"):
            result = session.execute(ListenAtDoor(direction=Direction.SOUTH, character_id=character_id))
            listened = next(event for event in result.events if event.event_type == "listened")
            assert listened.code == "exploration.listen.silent"
        assert session.heard_areas == []

    def test_a_wandering_row_spawns_a_bundled_id_through_session_spawn(self):
        adventure = bundled_adventure(monsters=(wretch(),), wandering_chance=6, table=wretch_table())
        session = lit_session(adventure)
        events, encountered = exploration.wandering_check(session)
        assert encountered
        assert {instance.template.id for instance in session.monsters.values()} == {CUSTOM_ID}

    def test_a_bundled_monster_fights_to_xp_and_treasure(self):
        # The downstream-unchanged pin: keyed spawn, battle to victory, loot the
        # drop pile, return to town — the award reads the embedded template's
        # bespoke 7 XP and the type-R carried gold (2d6 gp, never empty).
        adventure = bundled_adventure(
            monsters=(wretch(),),
            keyed=KeyedEncounter(
                monsters=(KeyedMonster(template_id=CUSTOM_ID, count_fixed=1),),
                aware=True,
                stance=ReactionResult.ATTACKS,
            ),
        )
        session = lit_session(adventure, seed=9, armed=True)
        into_room_a(session)
        assert session.mode.value == "battle"
        group_id = session.encounter.groups[0].id
        for _ in range(40):
            if session.mode.value != "battle":
                break
            attack = BattleDeclaration(
                character_id="character-0001", action="attack", target_group_id=group_id, weapon_id="sword"
            )
            holds = tuple(
                BattleDeclaration(character_id=member.id, action="hold")
                for member in session.party.living_members()
                if member.id != "character-0001"
            )
            result = session.execute(ResolveBattleRound(declarations=(attack, *holds)))
            if not result.accepted:  # out of melee reach: the whole party closes instead
                close = tuple(
                    BattleDeclaration(character_id=member.id, action="move", move="close", target_group_id=group_id)
                    for member in session.party.living_members()
                )
                assert session.execute(ResolveBattleRound(declarations=close)).accepted
        assert session.mode.value == "exploring"
        assert [record.xp for record in session.defeated_monsters] == [7]
        assert session.defeated_monsters[0].template_id == CUSTOM_ID
        assert session.execute(TakeTreasure(feature_id="pile")).accepted
        session.execute(MoveParty(direction=Direction.NORTH))
        session.execute(MoveParty(direction=Direction.WEST))
        session.execute(MoveParty(direction=Direction.WEST))
        result = session.execute(TravelToTown())
        award = next(event for event in result.events if event.code == "session.xp.adventure_award")
        assert award.monster_xp == 7
        assert award.treasure_xp >= 2  # type R: 2d6 gp carried, taken from the pile


class TestPersistenceAndReplay:
    def test_spawned_bundled_monsters_save_load_and_replay_to_equal_state(self):
        adventure = bundled_adventure(monsters=(wretch(),))
        session = GameSession.new(build_party(), adventure, seed=11)
        commands = [
            GrantItem(character_id="character-0001", item_id="torch", quantity=6),
            GrantItem(character_id="character-0001", item_id="tinder_box"),
            EnterDungeon(dungeon_id="delve"),
            SpawnMonsters(template_id=CUSTOM_ID, count_fixed=2, distance_feet=30),
        ]
        accepted = [command for command in commands if session.execute(command).accepted]
        assert any(instance.template.id == CUSTOM_ID for instance in session.monsters.values())
        restored = load_game(json.loads(json.dumps(save_game(session))))
        assert session_state(restored) == session_state(session)
        from osrlib.core.character import party_to_document
        from osrlib.core.ruleset import Ruleset

        replayed = replay_game(11, party_to_document(build_party().members), adventure, Ruleset(), accepted)
        assert session_state(replayed) == session_state(session)

    def test_a_doctored_save_with_a_colliding_bundled_id_fails_typed(self):
        session = GameSession.new(build_party(), bundled_adventure(monsters=(wretch(),)), seed=5)
        document = json.loads(json.dumps(save_game(session)))
        goblin = load_monsters().get("goblin")
        document["payload"]["adventure"]["monsters"].append(goblin.model_dump(mode="json"))
        with pytest.raises(ContentValidationError, match="colliding monster ids"):
            load_game(document)


class TestEffectiveCatalog:
    def test_an_empty_bundle_is_the_cached_base_catalog_object(self):
        session = GameSession.new(build_party(), build_adventure(), seed=5)
        assert session.effective_monsters is load_monsters()

    def test_a_bundle_resolves_base_and_bundled_ids(self):
        session = GameSession.new(build_party(), bundled_adventure(monsters=(wretch(),)), seed=5)
        assert session.effective_monsters.get("goblin").id == "goblin"
        assert session.effective_monsters.get(CUSTOM_ID).xp == 7

    def test_the_property_is_stable_across_calls(self):
        session = GameSession.new(build_party(), bundled_adventure(monsters=(wretch(),)), seed=5)
        assert session.effective_monsters is session.effective_monsters
