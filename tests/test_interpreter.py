"""The interpreter: matching, firing, selectors, drops, and the cascade bound.

`TestMatching` pins each pattern against events that fit and events that don't,
straight through the private matcher. `TestFiring` covers what decides whether a
match becomes a firing — fired-state, conditions, document and batch order — and
`TestSelectors`, `TestDrops`, and `TestCascadeDepth` cover what a firing issues and
what happens when part of it cannot land. `TestReplayEquivalence` is the standing
guarantee: a session with the interpreter registered and a replay of its log with no
listeners at all reach the same world.
"""

import json

import pytest

from crawl_fixtures import LEVER_KEY, PORTCULLIS_FIRED, PORTCULLIS_JOURNAL, build_adventure, build_party
from crawl_fixtures import build_portcullis_adventure as build_keep
from osrlib.core.effects import kill
from osrlib.core.events import Visibility
from osrlib.core.items import Coins, MagicItemInstance
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    AwardXP,
    EnterDungeon,
    GrantCoins,
    GrantItem,
    LightSource,
    MoveParty,
    OpenDoor,
    PlaceParty,
    SetFlag,
    SpawnMonsters,
    UseStairs,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    LevelSpec,
    PartyLocation,
    TransitionSpec,
    WanderingSpec,
)
from osrlib.crawl.events import (
    FlagSetEvent,
    ItemAcquiredEvent,
    LocationEnteredEvent,
    MonsterDefeatedEvent,
    NoteRecordedEvent,
)
from osrlib.crawl.gates import FlagEqualsCondition, HasItemCondition
from osrlib.crawl.interpreter import Interpreter, _matches
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.session import GameSession
from osrlib.crawl.triggers import (
    FIRST_LIVING_SELECTOR,
    PARTY_SELECTOR,
    AreaEnteredPattern,
    DungeonEnteredPattern,
    FlagSetPattern,
    ItemAcquiredPattern,
    LevelEnteredPattern,
    MonsterDefeatedPattern,
    TownEnteredPattern,
    TriggerSpec,
)
from osrlib.persistence import load_game, save_game, session_state


def with_triggers(*triggers: TriggerSpec, adventure: Adventure | None = None) -> Adventure:
    """The shared delve (or another adventure) with authored triggers bolted on."""
    base = adventure if adventure is not None else build_adventure(wandering_chance=0)
    return base.model_copy(update={"triggers": triggers})


def build_tower() -> Adventure:
    """A two-cell tower whose stair lands the party inside a keyed area.

    One command — `UseStairs` — therefore reports two crossings in one batch: the
    level arrival, then the area the party landed in.
    """
    upper = LevelSpec(
        number=1,
        width=1,
        height=1,
        entrance=(0, 0),
        transitions=(
            TransitionSpec(
                kind="stairs_down",
                position=(0, 0),
                to_dungeon_id="tower",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.EAST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=0),
    )
    lower = LevelSpec(
        number=2,
        width=1,
        height=1,
        areas=(AreaSpec(id="cellar", name="Cellar", cells=((0, 0),)),),
        wandering=WanderingSpec(chance_in_six=0),
    )
    return Adventure(
        name="The Tower",
        town=TownSpec(name="Threshold", travel_turns={"tower": 1}),
        dungeons=(DungeonSpec(id="tower", name="The Tower", levels=(upper, lower)),),
    )


def played(adventure: Adventure, seed: int = 31) -> GameSession:
    """A session running `adventure` with the interpreter registered."""
    session = GameSession.new(build_party(), adventure, seed=seed)
    session.register_listener(Interpreter(session))
    return session


def replayed(session: GameSession) -> GameSession:
    """The same seed and the same accepted commands, with no listeners at all."""
    fresh = GameSession.new(build_party(), session.adventure, seed=session.master_seed)
    for command in session.command_log:
        result = fresh.execute(command)
        assert result.accepted, f"replay diverged on {command.command_type}"
    return fresh


def notes(session: GameSession) -> list[str]:
    """Every referee note the run recorded, in order."""
    return [event.text for event in session.event_log if isinstance(event, NoteRecordedEvent)]


def torchlit(session: GameSession) -> None:
    """Put a lit torch in the lead member's hand, in town, before the delve."""
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    for _ in range(20):  # tinder is 2-in-6 per round; retry until it takes
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            return
    raise AssertionError("torch never lit in twenty tinder attempts")


class TestMatching:
    def match(self, pattern, event, session=None) -> bool:
        return _matches(pattern, event, session if session is not None else played(build_adventure()))

    def test_an_area_pattern_wants_the_whole_triple(self):
        pattern = AreaEnteredPattern(dungeon_id="delve", level_number=2, area_id="crypt")
        event = LocationEnteredEvent(location_kind="area", location_id="crypt", level_number=2, dungeon_id="delve")
        assert self.match(pattern, event)
        assert not self.match(pattern, event.model_copy(update={"dungeon_id": "elsewhere"}))
        assert not self.match(pattern, event.model_copy(update={"level_number": 1}))
        assert not self.match(pattern, event.model_copy(update={"location_id": "room_a"}))

    def test_a_level_pattern_matches_a_dungeon_crossing_too(self):
        pattern = LevelEnteredPattern(dungeon_id="delve", level_number=1)
        by_stair = LocationEnteredEvent(location_kind="level", location_id="delve", level_number=1)
        from_town = LocationEnteredEvent(location_kind="dungeon", location_id="delve", level_number=1)
        assert self.match(pattern, by_stair)
        assert self.match(pattern, from_town), "arriving on a level is arriving on it, however the party got there"
        assert not self.match(pattern, from_town.model_copy(update={"level_number": 2}))

    def test_a_dungeon_pattern_ignores_the_levels_beneath_it(self):
        pattern = DungeonEnteredPattern(dungeon_id="delve")
        assert self.match(pattern, LocationEnteredEvent(location_kind="dungeon", location_id="delve", level_number=1))
        assert not self.match(pattern, LocationEnteredEvent(location_kind="level", location_id="delve", level_number=2))

    def test_a_town_pattern_matches_the_homecoming_however_it_happened(self):
        assert self.match(TownEnteredPattern(), LocationEnteredEvent(location_kind="town", location_id="town"))
        assert not self.match(
            TownEnteredPattern(), LocationEnteredEvent(location_kind="dungeon", location_id="delve", level_number=1)
        )

    def test_a_flag_pattern_reads_the_written_value_strictly(self):
        assert self.match(FlagSetPattern(key="lever"), FlagSetEvent(key="lever", value="pulled"))
        assert self.match(FlagSetPattern(key="lever"), FlagSetEvent(key="lever", value=False)), "any value at all"
        assert not self.match(FlagSetPattern(key="lever"), FlagSetEvent(key="other", value="pulled"))
        assert self.match(FlagSetPattern(key="lever", value=1), FlagSetEvent(key="lever", value=1))
        assert not self.match(FlagSetPattern(key="lever", value=1), FlagSetEvent(key="lever", value=True))
        assert not self.match(FlagSetPattern(key="lever", value=True), FlagSetEvent(key="lever", value=1))

    def test_a_flag_pattern_watches_the_edge_not_the_store(self):
        session = played(build_adventure(wandering_chance=0))
        session.flags["lever"] = "shut"
        # The store says shut; the write said pulled, and the write is the edge.
        assert self.match(
            FlagSetPattern(key="lever", value="pulled"), FlagSetEvent(key="lever", value="pulled"), session
        )

    def test_a_monster_pattern_takes_every_outcome(self):
        pattern = MonsterDefeatedPattern(template_id="goblin")
        for outcome in ("slain", "routed", "surrendered"):
            event = MonsterDefeatedEvent(monster_id="monster-0001", template_id="goblin", outcome=outcome, xp=5)
            assert self.match(pattern, event), outcome
        skeleton = MonsterDefeatedEvent(monster_id="monster-0002", template_id="skeleton", outcome="slain", xp=10)
        assert not self.match(pattern, skeleton)

    def test_an_item_pattern_takes_a_mundane_catalog_id_directly(self):
        session = played(build_adventure(wandering_chance=0))
        event = ItemAcquiredEvent(character_id="character-0001", item_ids=("torch", "holy_water"))
        assert self.match(ItemAcquiredPattern(item_id="holy_water"), event, session)
        assert not self.match(ItemAcquiredPattern(item_id="rope"), event, session)

    def test_an_item_pattern_resolves_a_magic_instance_through_the_acquirer(self):
        session = played(build_adventure(wandering_chance=0))
        member = session.member("character-0002")
        instance = MagicItemInstance(instance_id="magic-item-0001", template_id="potion_of_healing")
        member.inventory.items.append(instance)
        event = ItemAcquiredEvent(character_id="character-0002", item_ids=("magic-item-0001",))
        assert self.match(ItemAcquiredPattern(item_id="potion_of_healing"), event, session)
        # Somebody else's pack is not the acquirer's.
        elsewhere = event.model_copy(update={"character_id": "character-0003"})
        assert not self.match(ItemAcquiredPattern(item_id="potion_of_healing"), elsewhere, session)

    def test_a_pattern_never_asks_the_session_where_the_party_is(self):
        session = played(build_adventure(wandering_chance=0))
        session.execute(EnterDungeon(dungeon_id="delve"))
        # The party stands on level 1; the event says level 2, and the event wins.
        pattern = LevelEnteredPattern(dungeon_id="delve", level_number=2)
        assert self.match(pattern, LocationEnteredEvent(location_kind="level", location_id="delve", level_number=2))


class TestFiring:
    def test_a_firing_marks_first_then_runs_its_consequences_then_journals(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        issued = [
            command.command_type for command in session.command_log if command.source == "trigger:portcullis-rises"
        ]
        assert issued == ["mark_trigger_fired", "set_door_state", "add_journal_entry"]
        assert session.fired_triggers == ["portcullis-rises"]
        assert [entry.text for entry in session.journal] == [PORTCULLIS_JOURNAL]

    def test_the_fired_beat_rides_the_referee_event_and_the_journal_speaks_to_the_table(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        result = session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        fired = next(event for event in result.events if event.code == "session.trigger.fired")
        assert fired.narrative == PORTCULLIS_FIRED
        assert fired.visibility.value == "referee"
        beat = next(event for event in result.events if event.code == "session.journal.entry_added")
        assert beat.text == PORTCULLIS_JOURNAL
        assert beat.visibility.value == "player"

    def test_a_failing_condition_blocks_the_firing_and_leaves_no_mark(self):
        trigger = TriggerSpec(
            id="warded",
            when=FlagSetPattern(key="lever"),
            conditions=(HasItemCondition(item_id="holy_water"),),
            consequences=(SetFlag(key="opened", value=True),),
        )
        session = played(with_triggers(trigger))
        session.execute(SetFlag(key="lever", value=True))
        assert session.fired_triggers == []
        assert "opened" not in session.flags
        session.execute(GrantItem(character_id="character-0001", item_id="holy_water"))
        session.execute(SetFlag(key="lever", value=True))
        assert session.fired_triggers == ["warded"]
        assert session.flags["opened"] is True

    def test_once_only_fires_once_and_repeatable_fires_every_time(self):
        once = TriggerSpec(
            id="once", when=FlagSetPattern(key="bell"), consequences=(SetFlag(key="once-rang", value=1),)
        )
        again = TriggerSpec(
            id="again", when=FlagSetPattern(key="bell"), repeatable=True, consequences=(SetFlag(key="rung", value=1),)
        )
        session = played(with_triggers(once, again))
        for _ in range(3):
            session.execute(SetFlag(key="bell", value=True))
        marks = [command for command in session.command_log if command.command_type == "mark_trigger_fired"]
        assert [command.trigger_id for command in marks] == ["once", "again", "again", "again"]
        assert session.fired_triggers == ["once", "again"], "state records that a trigger has fired, not how often"

    def test_two_triggers_on_one_event_fire_in_document_order(self):
        first = TriggerSpec(id="first", when=FlagSetPattern(key="bell"))
        second = TriggerSpec(id="second", when=FlagSetPattern(key="bell"))
        session = played(with_triggers(second, first))
        session.execute(SetFlag(key="bell", value=True))
        assert session.fired_triggers == ["second", "first"], "document order, not alphabetical, not registration"

    def test_two_events_in_one_command_fire_in_the_order_they_happened(self):
        # `UseStairs` reports the level crossing and then the area the party landed
        # in. The area trigger is written first in the document and still fires
        # second, because the batch's order outranks the document's.
        area = TriggerSpec(
            id="area-written-first", when=AreaEnteredPattern(dungeon_id="tower", level_number=2, area_id="cellar")
        )
        level = TriggerSpec(id="level-written-second", when=LevelEnteredPattern(dungeon_id="tower", level_number=2))
        session = played(with_triggers(area, level, adventure=build_tower()))
        session.execute(EnterDungeon(dungeon_id="tower"))
        session.execute(UseStairs())
        assert session.fired_triggers == ["level-written-second", "area-written-first"]

    def test_evaluate_as_you_go_lets_an_earlier_firing_satisfy_a_later_condition(self):
        opener = TriggerSpec(
            id="opener", when=FlagSetPattern(key="bell"), consequences=(SetFlag(key="power", value="on"),)
        )
        follower = TriggerSpec(
            id="follower",
            when=FlagSetPattern(key="bell"),
            conditions=(FlagEqualsCondition(key="power", value="on"),),
        )
        session = played(with_triggers(opener, follower))
        session.execute(SetFlag(key="bell", value=True))
        assert session.fired_triggers == ["opener", "follower"]
        # The other way round, the follower's condition is still false when it looks.
        reversed_session = played(with_triggers(follower, opener))
        reversed_session.execute(SetFlag(key="bell", value=True))
        assert reversed_session.fired_triggers == ["opener"]

    def test_the_mark_lands_before_a_consequence_that_would_re_match_the_trigger(self):
        loop = TriggerSpec(
            id="loop", when=FlagSetPattern(key="loop"), consequences=(SetFlag(key="loop", value="again"),)
        )
        session = played(with_triggers(loop))
        session.execute(SetFlag(key="loop", value="first"))
        marks = [command for command in session.command_log if command.command_type == "mark_trigger_fired"]
        assert len(marks) == 1, "the mark is in place before the consequence that would match again"
        assert session.flags["loop"] == "again"
        assert notes(session) == []


class TestSelectors:
    def test_party_expands_to_the_living_in_marching_order(self):
        trigger = TriggerSpec(
            id="boon",
            when=FlagSetPattern(key="bell"),
            consequences=(AwardXP(character_id=PARTY_SELECTOR, amount=50),),
        )
        session = played(with_triggers(trigger))
        kill(session.member("character-0002"))
        session.execute(SetFlag(key="bell", value=True))
        awards = [command for command in session.command_log if command.command_type == "award_xp"]
        assert [command.character_id for command in awards] == ["character-0001", "character-0003", "character-0004"]

    def test_first_addresses_the_lead_survivor(self):
        trigger = TriggerSpec(
            id="purse",
            when=FlagSetPattern(key="bell"),
            consequences=(GrantCoins(character_id=FIRST_LIVING_SELECTOR, coins=Coins(gp=25)),),
        )
        session = played(with_triggers(trigger))
        kill(session.member("character-0001"))
        session.execute(SetFlag(key="bell", value=True))
        grants = [command for command in session.command_log if command.command_type == "grant_coins"]
        assert [command.character_id for command in grants] == ["character-0002"]

    def test_party_with_nobody_standing_expands_to_nothing_and_says_nothing(self):
        trigger = TriggerSpec(
            id="boon",
            when=FlagSetPattern(key="bell"),
            consequences=(AwardXP(character_id=PARTY_SELECTOR, amount=50),),
        )
        session = played(with_triggers(trigger))
        for member in session.party.members:
            kill(member)
        session.execute(SetFlag(key="bell", value=True))
        assert [command for command in session.command_log if command.command_type == "award_xp"] == []
        assert notes(session) == [], "a reward for the dead is nothing, not a problem"

    def test_first_with_nobody_standing_drops_and_says_so(self):
        trigger = TriggerSpec(
            id="purse",
            when=FlagSetPattern(key="bell"),
            consequences=(GrantCoins(character_id=FIRST_LIVING_SELECTOR, coins=Coins(gp=25)),),
        )
        session = played(with_triggers(trigger))
        for member in session.party.members:
            kill(member)
        session.execute(SetFlag(key="bell", value=True))
        assert [command for command in session.command_log if command.command_type == "grant_coins"] == []
        assert notes(session) == ["trigger purse: consequence 0 (grant_coins) dropped (no living member for @first)"]

    def test_every_issued_command_carries_the_triggers_stamp(self):
        trigger = TriggerSpec(
            id="boon",
            when=FlagSetPattern(key="bell"),
            consequences=(AwardXP(character_id=PARTY_SELECTOR, amount=50),),
            narrative=NarrativeBlock(fired="A warmth in the chest.", journal="Something was given."),
        )
        session = played(with_triggers(trigger))
        session.execute(SetFlag(key="bell", value=True))
        issued = session.command_log[1:]
        assert issued, "the firing issued something"
        assert {command.source for command in issued} == {"trigger:boon"}

    def test_the_authored_consequence_is_never_mutated_by_the_stamp(self):
        trigger = TriggerSpec(
            id="boon",
            when=FlagSetPattern(key="bell"),
            consequences=(AwardXP(character_id=PARTY_SELECTOR, amount=50),),
        )
        session = played(with_triggers(trigger))
        session.execute(SetFlag(key="bell", value=True))
        authored = session.adventure.triggers[0].consequences[0]
        assert authored.source is None
        assert authored.character_id == PARTY_SELECTOR


class TestDrops:
    def test_a_spawn_that_meets_an_open_encounter_drops_and_records_why(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        for _ in range(4):
            session.execute(MoveParty(direction=Direction.EAST))
        assert session.mode.value == "encounter", "the guardroom's own goblins opened it"
        assert "guard-ambush" in session.fired_triggers, "the trigger fired; its consequence is what dropped"
        assert notes(session) == [
            "trigger guard-ambush: consequence 0 (spawn_monsters) dropped (session.command.encounter_in_progress)"
        ]
        assert [command for command in session.command_log if command.command_type == "spawn_monsters"] == []

    def test_a_dropped_consequence_does_not_stop_the_ones_after_it(self):
        trigger = TriggerSpec(
            id="mixed",
            when=FlagSetPattern(key="bell"),
            consequences=(
                SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30),
                SetFlag(key="after", value=True),
            ),
            narrative=NarrativeBlock(journal="The wiring ran to the end."),
        )
        session = played(with_triggers(trigger))
        session.execute(SetFlag(key="bell", value=True))  # in town: a spawn needs a dungeon cell
        assert notes(session) == [
            "trigger mixed: consequence 0 (spawn_monsters) dropped (session.command.not_in_dungeon)"
        ]
        assert session.flags["after"] is True
        assert [entry.text for entry in session.journal] == ["The wiring ran to the end."]

    def test_a_literal_character_id_lands_as_an_ordinary_rejection(self):
        # Hand-built content that never went through validation: the id flows
        # through untouched and the machinery degrades into a drop-and-note.
        trigger = TriggerSpec(
            id="misaddressed",
            when=FlagSetPattern(key="bell"),
            consequences=(AwardXP(character_id="character-9999", amount=10),),
        )
        # The spec parses; it is `validate_adventure` that rejects the literal id, and
        # this trigger reaches the session behind its back.
        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=31)
        session.adventure = session.adventure.model_copy(update={"triggers": (trigger,)})
        session.register_listener(Interpreter(session))
        session.execute(SetFlag(key="bell", value=True))
        assert notes(session) == [
            "trigger misaddressed: consequence 0 (award_xp) dropped (session.command.unknown_member)"
        ]


class TestCascadeDepth:
    """A flag chain deep enough to hit the bound, and what the bound does."""

    def chain(self) -> tuple[TriggerSpec, ...]:
        links = [
            TriggerSpec(
                id=f"chain-{step}",
                when=FlagSetPattern(key=f"step{step}"),
                repeatable=True,
                consequences=(SetFlag(key=f"step{step + 1}", value=True),),
            )
            for step in range(1, 6)
        ]
        # The sixth link is once-only, so its suppression is provably not a mark.
        links.append(
            TriggerSpec(
                id="chain-6",
                when=FlagSetPattern(key="step6"),
                consequences=(SetFlag(key="step7", value=True),),
            )
        )
        return tuple(links)

    def test_the_cascade_runs_to_the_bound_and_records_the_truncation(self):
        session = played(with_triggers(*self.chain()))
        session.execute(SetFlag(key="step1", value=True))
        assert session.fired_triggers == [f"chain-{step}" for step in range(1, 6)]
        assert sorted(session.flags) == [f"step{step}" for step in range(1, 7)]
        assert notes(session) == ["trigger chain-6: not fired, the cascade reached depth 5 past the limit of 4"]

    def test_a_truncated_once_only_trigger_is_still_fireable_afterwards(self):
        session = played(with_triggers(*self.chain()))
        session.execute(SetFlag(key="step1", value=True))
        assert "chain-6" not in session.fired_triggers
        session.execute(SetFlag(key="step6", value=True))
        assert "chain-6" in session.fired_triggers, "a suppressed firing leaves the trigger unfired, not spent"
        assert session.flags["step7"] is True

    def test_the_truncation_note_is_stamped_like_everything_else(self):
        session = played(with_triggers(*self.chain()))
        session.execute(SetFlag(key="step1", value=True))
        note = next(command for command in session.command_log if command.command_type == "record_note")
        assert note.source == "trigger:chain-6"


class TestTheResultEnvelope:
    def test_a_player_commands_result_carries_the_whole_cascade_in_log_order(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        before = len(session.event_log)
        result = session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        assert [event.code for event in result.events] == [
            "session.flag.set",
            "session.trigger.fired",
            "exploration.door.opened",
            "session.journal.entry_added",
        ]
        logged = [event.code for event in session.event_log[before:]]
        assert logged == [event.code for event in result.events], "each event once, in the log's own order"

    def test_the_gate_refuses_until_the_trigger_opens_the_grille(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        session.execute(MoveParty(direction=Direction.EAST))
        session.execute(MoveParty(direction=Direction.EAST))
        refused = session.execute(OpenDoor(direction=Direction.EAST))
        assert refused.rejections[0].code == "exploration.door.gate_refused"
        assert refused.rejections[0].params["refusal"].startswith("The portcullis is a grille")
        session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        moved = session.execute(MoveParty(direction=Direction.EAST))
        assert moved.accepted, "a door standing open admits passage without a gate check"


class TestReplayEquivalence:
    def test_the_interpreter_holds_nothing_and_emits_nothing(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        assert session.listener_state == {"osrlib.interpreter": {}}

    def test_a_triggered_run_replays_with_no_listeners_at_all(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        for _ in range(2):
            session.execute(MoveParty(direction=Direction.EAST))
        fresh = replayed(session)
        live, again = session_state(session), session_state(fresh)
        assert live.pop("listener_state") == {"osrlib.interpreter": {}}
        assert again.pop("listener_state") == {}
        assert live == again

    def test_a_teleporting_consequence_replays_the_same_map_memory(self):
        trigger = TriggerSpec(
            id="pitfall",
            when=DungeonEnteredPattern(dungeon_id="delve"),
            consequences=(
                PlaceParty(
                    location=PartyLocation(
                        kind="dungeon", dungeon_id="delve", level_number=2, position=(1, 0), facing=Direction.EAST
                    )
                ),
            ),
        )
        adventure = with_triggers(trigger)
        session = played(adventure)
        torchlit(session)
        session.execute(EnterDungeon(dungeon_id="delve"))
        assert session.dungeon_state.location.level_number == 2, "the trigger moved the party on arrival"
        fresh = replayed(session)
        assert fresh.dungeon_state.seen == session.dungeon_state.seen
        assert set(session.dungeon_state.seen) == {"delve:1", "delve:2"}, "both folds landed, in order"

    def test_load_of_a_save_equals_the_replay(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        restored = load_game(json.loads(json.dumps(save_game(session))))
        fresh = replayed(session)
        assert restored.journal == session.journal == fresh.journal
        assert restored.fired_triggers == session.fired_triggers == fresh.fired_triggers
        state = session_state(restored)
        assert state.pop("listener_state") == {"osrlib.interpreter": {}}
        again = session_state(fresh)
        assert again.pop("listener_state") == {}
        assert state == again

    def test_the_player_view_never_shows_the_wiring(self):
        session = played(build_keep())
        session.execute(EnterDungeon(dungeon_id="keep"))
        session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
        payload = json.dumps(session.view(Visibility.PLAYER).model_dump(mode="json"))
        for forbidden in ("pattern_type", "consequences", "fired_triggers", "portcullis-rises", PORTCULLIS_FIRED):
            assert forbidden not in payload
        assert PORTCULLIS_JOURNAL in payload, "the journal is the players' side of the beat"


@pytest.mark.parametrize("adventure_builder", [build_keep], ids=["keep"])
def test_an_unregistered_interpreter_changes_nothing(adventure_builder):
    """Triggers are inert content until a game registers the listener that plays them."""
    session = GameSession.new(build_party(), adventure_builder(), seed=31)
    session.execute(EnterDungeon(dungeon_id="keep"))
    session.execute(SetFlag(key=LEVER_KEY, value="pulled"))
    assert session.fired_triggers == []
    assert session.journal == []
    assert [command.command_type for command in session.command_log] == ["enter_dungeon", "set_flag"]
