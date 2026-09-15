"""The referee view is typed: every group the save serializes is a field, built fresh from the session.

[`RefereeView`][osrlib.crawl.views.RefereeView] holds the same groups
[`session_state`][osrlib.persistence.session_state] writes, minus the master seed and the RNG stream
states, each as the session's own model rather than as a dict, so a front-end author reads
`view.monsters[0].current_hp` and `view.flags["key"]` with the types the reference documents. The view's
JSON dump is the save payload without the two withheld keys, which is what keeps the two layouts from
drifting apart. A view is a snapshot: the session moving on after the view was built changes nothing in
it.
"""

from crawl_fixtures import build_adventure, build_party
from osrlib.core.clock import TimeUnit
from osrlib.core.events import Visibility
from osrlib.crawl.commands import (
    AddJournalEntry,
    AdvanceTime,
    EnterDungeon,
    GrantItem,
    LightSource,
    SessionMode,
    SetFlag,
    SpawnMonsters,
)
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import RefereeView, build_referee_view
from osrlib.persistence import session_state

WITHHELD = ("master_seed", "rng_streams")
"""The two save keys a referee view never contains."""


def played_session() -> GameSession:
    """A session with something in every group a referee reads: a flag, a beat, a lit torch, a goblin."""
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=11)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    session.execute(EnterDungeon(dungeon_id="delve"))
    for _ in range(20):
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    session.execute(SetFlag(key="secret_wiring", value=True))
    session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
    spawned = session.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30))
    assert spawned.accepted, [rejection.code for rejection in spawned.rejections]
    assert session.mode is SessionMode.ENCOUNTER
    return session


def expected_payload(session: GameSession) -> dict:
    return {key: value for key, value in session_state(session).items() if key not in WITHHELD}


class TestTheRefereeViewIsTyped:
    def test_the_fields_are_the_save_groups_minus_the_withheld_two(self):
        session = played_session()
        assert set(RefereeView.model_fields) == set(expected_payload(session))
        assert "state" not in RefereeView.model_fields

    def test_the_view_dumps_as_the_save_does(self):
        session = played_session()
        view = build_referee_view(session)
        assert view.model_dump(mode="json") == expected_payload(session)

    def test_session_view_and_the_builder_agree(self):
        session = played_session()
        assert session.view(Visibility.REFEREE) == build_referee_view(session)

    def test_the_groups_are_the_sessions_own_models(self):
        session = played_session()
        view = build_referee_view(session)
        assert view.mode is SessionMode.ENCOUNTER
        assert view.flags == {"secret_wiring": True}
        assert view.journal[-1].text == "The lever grinds."
        assert isinstance(view.monsters[0].current_hp, int)
        assert view.monsters[0].template.id == "goblin"
        assert view.party.members[0].id == "character-0001"
        assert view.encounter is not None and tuple(view.encounter.groups[0].monster_ids) == (view.monsters[0].id,)
        assert view.battle is None
        assert view.clock_rounds == session.clock.rounds
        assert isinstance(view.exploration.odometer_thirds, int)
        assert view.exploration.model_dump(mode="json") == session_state(session)["exploration"]
        assert view.dungeon_state.explored == session.dungeon_state.explored
        assert view.command_log[-1].command_type == "spawn_monsters"
        assert [event.code for event in view.event_log] == [event.code for event in session.event_log]

    def test_the_view_is_a_snapshot(self):
        session = played_session()
        view = build_referee_view(session)
        rounds_before = view.clock_rounds
        session.execute(SetFlag(key="later", value=1))
        session.execute(AdvanceTime(n=1, unit=TimeUnit.TURN))
        session.party.members[0].current_hp -= 1
        session.monsters[view.monsters[0].id].current_hp = 0
        assert view.flags == {"secret_wiring": True}
        assert view.clock_rounds == rounds_before
        assert view.party.members[0].current_hp == session.party.members[0].current_hp + 1
        assert view.monsters[0].current_hp > 0

    def test_the_seed_and_the_streams_stay_out(self):
        session = played_session()
        blob = build_referee_view(session).model_dump_json()
        for key in WITHHELD:
            assert key not in blob
