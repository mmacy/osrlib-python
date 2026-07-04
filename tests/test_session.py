"""Session contract tests: rejection purity, modes, logs, listeners, records, views."""

from crawl_fixtures import build_adventure, build_party
from osrlib.core.clock import ROUNDS_PER_DAY, TimeUnit
from osrlib.core.events import Visibility
from osrlib.crawl.commands import (
    AdvanceTime,
    AwardXP,
    EnterDungeon,
    GrantCoins,
    GrantItem,
    MoveParty,
    PlaceParty,
    ReorderParty,
    ResolveBattleRound,
    SetDoorState,
    SetFlag,
    Wait,
)
from osrlib.crawl.dungeon import Coins, Direction, PartyLocation
from osrlib.crawl.events import FlagSetEvent
from osrlib.crawl.session import GameSession


def make_session(seed: int = 11) -> GameSession:
    return GameSession.new(build_party(), build_adventure(), seed=seed)


def stream_states(session) -> dict:
    return {key: state.model_dump() for key, state in session.streams.export_states().items()}


def outfit(session) -> None:
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))


class TestNewSession:
    def test_ids_assign_in_party_order_with_the_pinned_prefix(self):
        session = make_session()
        assert [member.id for member in session.party.members] == [
            "character-0001",
            "character-0002",
            "character-0003",
            "character-0004",
        ]

    def test_existing_ids_are_kept(self):
        party = build_party()
        party.members[0].id = "character-0007"
        GameSession.new(party, build_adventure(), seed=1)
        assert party.members[0].id == "character-0007"
        assert party.members[1].id == "character-0001"

    def test_metadata_handshake(self):
        from osrlib.versioning import SCHEMA_VERSION, engine_version

        session = make_session()
        assert session.metadata == {"schema_version": SCHEMA_VERSION, "engine_version": engine_version()}

    def test_starts_in_town_at_round_zero(self):
        session = make_session()
        assert session.mode.value == "town"
        assert session.clock.rounds == 0
        assert session.dungeon_state.location == PartyLocation(kind="town")


class TestRejectionPurity:
    def test_rejected_command_consumes_no_draws_no_time_and_stays_out_of_the_log(self):
        session = make_session()
        outfit(session)
        before_streams = stream_states(session)
        before_rounds = session.clock.rounds
        before_log = len(session.command_log)
        before_events = len(session.event_log)
        # Wrong mode: MoveParty in town.
        result = session.execute(MoveParty(direction=Direction.NORTH))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"
        assert result.events == ()
        assert stream_states(session) == before_streams
        assert session.clock.rounds == before_rounds
        assert len(session.command_log) == before_log
        assert len(session.event_log) == before_events

    def test_in_fiction_rejection_is_equally_pure(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        before_streams = stream_states(session)
        before_rounds = session.clock.rounds
        result = session.execute(MoveParty(direction=Direction.NORTH))  # boundary wall
        assert not result.accepted
        assert result.rejections[0].code == "exploration.move.blocked"
        assert stream_states(session) == before_streams
        assert session.clock.rounds == before_rounds

    def test_accepted_commands_are_logged(self):
        session = make_session()
        result = session.execute(SetFlag(key="lever", value=True))
        assert result.accepted
        assert session.command_log[-1] == SetFlag(key="lever", value=True)
        assert session.event_log[-1] == result.events[-1]


class TestModes:
    def test_battle_command_rejected_outside_battle(self):
        session = make_session()
        result = session.execute(ResolveBattleRound(declarations=()))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"

    def test_encounter_command_rejected_while_exploring(self):
        session = make_session()
        session.execute(EnterDungeon(dungeon_id="delve"))
        result = session.execute(Wait())
        assert not result.accepted
        assert result.rejections[0].code == "session.command.wrong_mode"


class TestRefereeCommands:
    def test_grant_item_and_coins(self):
        session = make_session()
        result = session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
        assert result.accepted
        member = session.member("character-0001")
        assert any(instance.template.id == "torch" and instance.quantity == 6 for instance in member.inventory.items)
        result = session.execute(GrantCoins(character_id="character-0001", coins=Coins(gp=50)))
        assert result.accepted
        assert member.inventory.purse.gp == 50

    def test_award_xp_levels_up(self):
        session = make_session()
        result = session.execute(AwardXP(character_id="character-0001", amount=2500))
        assert result.accepted
        (event,) = result.events
        assert event.code == "session.xp.awarded"
        assert session.member("character-0001").level == 2
        assert event.level_after == 2

    def test_award_xp_clamps_one_level_per_award(self):
        session = make_session()
        session.execute(AwardXP(character_id="character-0001", amount=100_000))
        member = session.member("character-0001")
        assert member.level == 2
        assert member.xp == 3999  # 1 shy of the fighter's level-3 threshold

    def test_set_flag_is_referee_visibility(self):
        session = make_session()
        result = session.execute(SetFlag(key="portcullis_open", value=True))
        (event,) = result.events
        assert isinstance(event, FlagSetEvent)
        assert event.visibility is Visibility.REFEREE
        assert session.flags["portcullis_open"] is True

    def test_set_door_state(self):
        session = make_session()
        result = session.execute(
            SetDoorState(dungeon_id="delve", level_number=1, x=2, y=0, direction=Direction.SOUTH, open=True)
        )
        assert result.accepted
        assert session.dungeon_state.doors["delve:1:2,1:north"].open

    def test_place_party(self):
        session = make_session()
        result = session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="delve", level_number=1, position=(0, 0), facing=Direction.EAST
                )
            )
        )
        assert result.accepted
        assert session.mode.value == "exploring"
        assert session.dungeon_state.is_explored("delve", 1, (0, 0))

    def test_advance_time(self):
        session = make_session()
        result = session.execute(AdvanceTime(n=2, unit=TimeUnit.TURN))
        assert result.accepted
        assert session.clock.rounds == 120
        assert result.events[-1].code == "session.time.advanced"

    def test_referee_time_consumes_provisions_at_day_boundaries(self):
        session = make_session()
        result = session.execute(AdvanceTime(n=1, unit=TimeUnit.DAY))
        assert session.clock.rounds == ROUNDS_PER_DAY
        codes = [event.code for event in result.events]
        # In town provisions consume but never run short.
        assert codes.count("exploration.provisions.consumed") == 8  # food + water for four members
        assert "exploration.provisions.short" not in codes


class TestReorder:
    def test_reorder_party_is_the_only_marching_order_mutation(self):
        session = make_session()
        result = session.execute(
            ReorderParty(order=("character-0004", "character-0003", "character-0002", "character-0001"))
        )
        assert result.accepted
        assert [member.id for member in session.party.members] == [
            "character-0004",
            "character-0003",
            "character-0002",
            "character-0001",
        ]

    def test_bad_order_rejects(self):
        session = make_session()
        result = session.execute(ReorderParty(order=("character-0001",)))
        assert not result.accepted


class TestListeners:
    def test_listener_events_append_to_result_and_log_with_state_snapshotted(self):
        class QuestTracker:
            key = "quest"

            def handle(self, events, state):
                count = state.get("flags_seen", 0)
                emitted = []
                for event in events:
                    if isinstance(event, FlagSetEvent):
                        count += 1
                        emitted.append(FlagSetEvent(key="quest_progress", value=count))
                return emitted, {"flags_seen": count}

        session = make_session()
        session.register_listener(QuestTracker())
        result = session.execute(SetFlag(key="lever", value=True))
        assert [event.key for event in result.events] == ["lever", "quest_progress"]
        assert session.listener_state["quest"] == {"flags_seen": 1}
        assert session.event_log[-1].key == "quest_progress"

    def test_listeners_run_in_registration_order_and_see_earlier_events(self):
        order = []

        class First:
            key = "first"

            def handle(self, events, state):
                order.append(("first", len(events)))
                return [FlagSetEvent(key="from_first", value=1)], state

        class Second:
            key = "second"

            def handle(self, events, state):
                order.append(("second", len(events)))
                return [], state

        session = make_session()
        session.register_listener(First())
        session.register_listener(Second())
        session.execute(SetFlag(key="lever", value=True))
        assert order == [("first", 1), ("second", 2)]


class TestDeathRecords:
    def test_poison_death_records_the_cause(self):
        from osrlib.core.events import DeathEvent, SavingThrowRolledEvent

        session = make_session()
        member = session.member("character-0001")
        events = [
            SavingThrowRolledEvent(
                code="combat.save.failed", target_id=member.id, category="death", roll=2, required=12
            ),
            DeathEvent(code="combat.death.died", target_id=member.id),
        ]
        session._record_deaths(events)
        record = session.death_records[member.id]
        assert record.cause == "poison"
        assert record.round == session.clock.rounds

    def test_damage_death_records_a_non_poison_cause(self):
        from osrlib.core.events import DamageDealtEvent, DeathEvent

        session = make_session()
        member = session.member("character-0001")
        events = [
            DamageDealtEvent(target_id=member.id, amount=9),
            DeathEvent(code="combat.death.died", target_id=member.id),
        ]
        session._record_deaths(events)
        assert session.death_records[member.id].cause == "damage"


class TestViews:
    def test_player_view_carries_the_whitelist(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        view = session.view(Visibility.PLAYER)
        assert view.party[0].current_hp == 6
        assert view.mode == "exploring"
        assert view.location.position == (0, 0)
        level_view = view.explored[0]
        assert (0, 0) in level_view.cells

    def test_player_view_never_leaks_the_basics(self):
        session = make_session(seed=99)
        outfit(session)
        session.execute(SetFlag(key="secret_wiring", value=True))
        session.execute(EnterDungeon(dungeon_id="delve"))
        blob = session.view(Visibility.PLAYER).model_dump_json()
        assert "secret_wiring" not in blob
        assert str(session.master_seed) not in blob
        assert "trap" not in blob.lower() or "trap_ref" not in blob  # no trap specs
        # Unexplored geometry: room_a's cells are unexplored.
        assert "room_a" not in blob
        assert "chest" not in blob

    def test_undiscovered_secret_door_renders_as_wall(self):
        session = make_session()
        session.execute(
            PlaceParty(
                location=PartyLocation(
                    kind="dungeon", dungeon_id="delve", level_number=1, position=(3, 1), facing=Direction.EAST
                )
            )
        )
        view = session.view(Visibility.PLAYER)
        level_view = next(entry for entry in view.explored if entry.level_number == 1)
        assert level_view.edges["4,1:west"].kind == "wall"

    def test_referee_view_has_everything_but_rng(self):
        session = make_session()
        session.execute(SetFlag(key="secret_wiring", value=True))
        view = session.view(Visibility.REFEREE)
        assert view.state["flags"] == {"secret_wiring": True}
        assert "rng_streams" not in view.state
        assert "master_seed" not in view.state

    def test_monster_hp_never_in_player_view(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        from osrlib.crawl.commands import SpawnMonsters

        session.execute(SpawnMonsters(template_id="goblin", count_fixed=3, distance_feet=60))
        view = session.view(Visibility.PLAYER)
        assert view.encounter is not None
        assert view.encounter.groups[0].count == 3
        blob = view.model_dump_json()
        assert "current_hp" in blob  # the party's own
        goblins = [session.monsters[mid] for mid in session.encounter.groups[0].monster_ids]
        for goblin in goblins:
            assert f'"{goblin.id}"' not in blob  # not even ids, only counts and labels


class TestSpawnMonstersCommand:
    def test_spawn_opens_an_encounter(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        from osrlib.crawl.commands import SpawnMonsters

        result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=40))
        assert result.accepted
        codes = [event.code for event in result.events]
        assert "session.monsters.spawned" in codes
        assert "encounter.started" in codes
        assert session.mode.value in ("encounter", "battle")
        assert len(session.monsters) == 2

    def test_unknown_template_rejects(self):
        session = make_session()
        from osrlib.crawl.commands import SpawnMonsters

        result = session.execute(SpawnMonsters(template_id="gazebo", count_fixed=1, distance_feet=30))
        assert not result.accepted


class TestTownGuards:
    def test_spawn_monsters_rejects_in_town(self):
        # The fuzz contract caught this: an encounter needs a dungeon cell for
        # the combat space, so a town spawn rejects instead of crashing.
        session = make_session()
        from osrlib.crawl.commands import SpawnMonsters

        result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.not_in_dungeon"

    def test_place_party_rejects_mid_encounter(self):
        session = make_session()
        outfit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        from osrlib.crawl.commands import SpawnMonsters

        session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=30))
        assert session.encounter is not None
        result = session.execute(PlaceParty(location=PartyLocation(kind="town")))
        assert not result.accepted
        assert result.rejections[0].code == "session.command.encounter_in_progress"
