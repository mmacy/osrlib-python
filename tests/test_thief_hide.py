"""Acceptance tests for a thief who hides during a battle and back-stabs from hiding (issue #208).

The OSE SRD's thief entry says "When attacking an unaware opponent from behind, a thief receives a +4 bonus to hit
and doubles any damage dealt." Its skills say hide in shadows "Requires the thief to be motionless—attacking or
moving while hiding is not possible", that with move silently "A thief may attempt to sneak past enemies
unnoticed", and that "The referee should roll for hear noise, hide in shadows and move silently on the player’s
behalf, as the thief does not immediately know if the attempt was successful." osrlib lets a thief hide in a battle
and back-stab from hiding, which isn't the rules as written. The adaptations register states the reading.

**The declaration.** `BattleDeclaration.action="hide"` is open to a member whose class has thief skills and who
doesn't stand in the first row of the party's ranks toward any group still in the fight, judged where the round's
declared moves leave the members. It takes the member's action for the round. The refusals, after the back-stab
checks and the check of `move`, are `battle.declaration.hide_not_a_thief` and then
`battle.declaration.hide_in_front_rank`, each with the member's id as `character`.

**The rolls.** The session rolls hide in shadows when the `hide` resolves, in the party's movement step, and move
silently when a hiding member's `attack` with `back_stab` comes up, before its first attack roll. Both draw from the
combat stream, both are reported to the referee alone as `DetectionRolledEvent`s of kind `"hide_in_shadows"` and
`"move_silently"`, and the second is drawn whatever the first was and whichever way the ruleset is set. The players
see a `HideAttemptedEvent` (`battle.hide.attempted`) that doesn't say how the roll went.

**The attack.** A hiding member may declare an `attack` with `back_stab` in any round, and from outside the front
rank, at a group within 5' or with a `close` that brings one within 5'. The close doesn't end the attempt. The
attack is a back-stab when the rolls pass the test of `Ruleset.back_stab_needs_both_skills`: both rolls when it's
on, the default, and either roll when it's off. Otherwise it's an ordinary melee attack. A back-stab gets +4 to hit
and doubles the whole damage sum, through the kernel's existing arithmetic.

**The attempt.** A member is hiding from the `hide` until their next declaration other than `hold` or `hide` comes
up, they take damage, a monster attacks them, they can't act or are confused at the end of a round, or the battle
ends. A second `hide` replaces the attempt with a new roll. `EncounterView.can_hide` lists who may hide,
`EncounterView.hiding` who is hiding, and `BattleState.hiding` holds each hiding member's hide in shadows result for
the referee.

**The monsters.** Their choice of targets reads only the hide in shadows result. A member whose roll passed is
passed over while the monsters can pick another member, by the built-in policies, a confused monster, and a breath
that hits one member, and found when they're the only one. Statues are passed over first. A member whose roll failed
has been noticed and can be picked like anyone else.

The battles are built by command from seeded sessions, in a 10' corridor two cells long, so the party's front rank
is two abreast and the third member in marching order stands in the second rank. Each helper takes the first seed
that gives the setup it needs, and each test asserts its own preconditions, so a test that stops exercising its case
fails loudly. The members' scores are 10 unless a test says otherwise, so no ability modifier touches an attack.
"""

import hashlib
import json

import pytest
from pydantic import TypeAdapter

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character, party_to_document
from osrlib.core.clock import TimeUnit
from osrlib.core.combat import COMBAT_STREAM, incapacitated
from osrlib.core.effects import Condition, EffectDefinition
from osrlib.core.events import (
    AttackRolledEvent,
    DamageDealtEvent,
    DeclarationFizzledEvent,
    InitiativeRolledEvent,
    SavingThrowRolledEvent,
    Visibility,
)
from osrlib.core.rng import RngStream, RngStreams, RngStreamState
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.battle import BattleState, NpcPartyPolicy, preview_battle_round
from osrlib.crawl.commands import (
    AnyCommand,
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    GrantItem,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
    SpawnNpcParty,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.events import DetectionRolledEvent, HideAttemptedEvent, MemberMovedEvent, parse_any_event
from osrlib.crawl.party import Party
from osrlib.crawl.session import MONSTER_ACTION_STREAM, GameSession
from osrlib.crawl.views import EncounterView
from osrlib.data import load_classes
from osrlib.messages import format_message
from osrlib.persistence import load_game, replay_game, save_game, session_state

CHUNK = "chunk: thief-hide"

SEEDS = range(0, 400)
"""Seeds the helpers scan for a setup they need, like a battle in which neither side was surprised."""

THIEF = ("Sable", "thief")
FIGHTER = ("Brakk", "fighter")
SECOND_FIGHTER = ("Hild", "fighter")
TRIO = (FIGHTER, SECOND_FIGHTER, THIEF)
"""Brakk and Hild side by side in the front rank, and Sable the thief behind them in the second rank."""
DUO = (THIEF, FIGHTER)
"""Sable and Brakk side by side in the front rank."""

LEVEL = 7
"""Sable's level in most tests. The OSE SRD's thief table gives hide in shadows 45% and move silently 55% at level 7,
so each of the four combinations of the two rolls comes up within a few seeds."""
HS_AT_7 = 45
MS_AT_7 = 55

NOT_A_THIEF = "battle.declaration.hide_not_a_thief"
IN_FRONT_RANK = "battle.declaration.hide_in_front_rank"
NOT_ATTACK = "battle.declaration.back_stab_not_attack"
TARGET_AWARE = "battle.declaration.back_stab_target_aware"
MOVE_NOT_COMBINABLE = "battle.declaration.move_not_combinable"

HOUSE_RULE = Ruleset(back_stab_needs_both_skills=False)


# ---------------------------------------------------------------------- setup


def _character(name: str, class_id: str, *, level: int = LEVEL, hp: int = 400, strength: int = 10) -> Character:
    definition = load_classes().get(class_id)
    scores = {ability: 10 for ability in AbilityScore}
    scores[AbilityScore.STR] = strength
    return Character(
        name=name,
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores=scores,
        alignment="lawful",
        max_hp=hp,
        current_hp=hp,
    )


def _adventure() -> Adventure:
    """One level: a 10' corridor two cells long, so the party's front rank is two abreast."""
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    return Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))


def _session(
    seed: int,
    roster,
    *,
    ruleset: Ruleset,
    level: int,
    strength: int,
    weapons: tuple[str, ...] = ("sword",),
    items: tuple[str, ...] = (),
) -> GameSession:
    """The roster in the unlit corridor, in marching order, with Sable wielding `weapons`, all by command.

    Sable's level and STR are `level` and `strength`, and every other member is level 7 with STR 10. Sable also
    carries `items`. The party carries no light, so the monsters roll for surprise.
    """
    members = [
        _character(name, class_id, level=level, strength=strength) if name == "Sable" else _character(name, class_id)
        for name, class_id in roster
    ]
    game = GameSession.new(Party(members=members), _adventure(), seed=seed, ruleset=ruleset)
    sable = _named(game, "Sable")
    for item_id in (*weapons, *items):
        assert game.execute(GrantItem(character_id=sable, item_id=item_id)).accepted
    for item_id in weapons:
        assert game.execute(EquipItem(character_id=sable, item_id=item_id)).accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def _battle(
    *,
    first_seed: int = SEEDS.start,
    roster=TRIO,
    ruleset: Ruleset | None = None,
    level: int = LEVEL,
    strength: int = 10,
    template_id: str = "goblin",
    count: int = 2,
    distance_feet: int = 5,
    npc_party: bool = False,
    monsters_surprised: bool = False,
    weapons: tuple[str, ...] = ("sword",),
    items: tuple[str, ...] = (),
) -> GameSession:
    """A battle opened by command, from the first seed at or after `first_seed` that opens it as asked.

    The party is never surprised. The monsters are surprised when `monsters_surprised` is true, which gives the party
    a free round, and not otherwise. With `npc_party`, the other side is a basic party of `count` NPC adventurers
    instead of `count` monsters of `template_id`.
    """
    for seed in range(first_seed, SEEDS.stop):
        game = _session(
            seed,
            roster,
            ruleset=ruleset or Ruleset(),
            level=level,
            strength=strength,
            weapons=weapons,
            items=items,
        )
        spawn = (
            SpawnNpcParty(party_kind="basic", count_fixed=count, distance_feet=distance_feet)
            if npc_party
            else SpawnMonsters(template_id=template_id, count_fixed=count, distance_feet=distance_feet)
        )
        result = game.execute(spawn)
        assert result.accepted
        sides = {event.side: event.surprised for event in result.events if event.code == "encounter.surprise.rolled"}
        if sides.get("party") is not False or sides.get("monsters") is not monsters_surprised:
            continue
        if game.mode is SessionMode.ENCOUNTER:
            assert game.execute(EngageBattle()).accepted
        if game.mode is not SessionMode.BATTLE or game.battle.round != 0:
            continue
        assert game.battle.monsters_hold_rounds == (1 if monsters_surprised else 0), "precondition: the free round"
        assert game.encounter.groups[0].distance_feet == distance_feet
        return game
    raise AssertionError("precondition: no seed opened the battle")


def _named(game: GameSession, name: str) -> str:
    return next(member.id for member in game.party.members if member.name == name)


def _sable(game: GameSession) -> str:
    return _named(game, "Sable")


def _group_id(game: GameSession) -> str:
    return game.encounter.groups[0].id


def _declare(member_id: str, action: str = "hold", **fields) -> BattleDeclaration:
    return BattleDeclaration(character_id=member_id, action=action, **fields)


def _hide(game: GameSession, member_id: str | None = None) -> BattleDeclaration:
    return _declare(member_id or _sable(game), "hide")


def _stab(game: GameSession, *, back_stab: bool = True) -> BattleDeclaration:
    """Sable's melee attack on the first group, a back-stab unless `back_stab` is false."""
    return _declare(_sable(game), "attack", target_group_id=_group_id(game), back_stab=back_stab)


def _withdraw(game: GameSession, name: str) -> BattleDeclaration:
    return _declare(_named(game, name), "move", move="fighting_withdrawal")


def _round(game: GameSession, *declared: BattleDeclaration):
    """Send one round: the members in `declared` as given, every other able member holding."""
    by_id = {declaration.character_id: declaration for declaration in declared}
    able = [member for member in game.party.living_members() if not incapacitated(member)]
    assert set(by_id) <= {member.id for member in able}, "precondition: every declared member can act"
    return game.execute(ResolveBattleRound(declarations=tuple(by_id.get(m.id, _declare(m.id)) for m in able)))


def _play(game: GameSession, *declared: BattleDeclaration) -> list:
    result = _round(game, *declared)
    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
    return list(result.events)


def _state_without_logs(game: GameSession) -> dict:
    state = session_state(game)
    state.pop("command_log")
    state.pop("event_log")
    return state


def _refused(game: GameSession, *declared: BattleDeclaration) -> list[tuple[str, dict]]:
    """Send a round that should be refused, check that it cost nothing, and return its rejections."""
    before = _state_without_logs(game)
    result = _round(game, *declared)
    assert not result.accepted, "the round was accepted"
    assert not result.events
    assert _state_without_logs(game) == before, "a refused round changed the session"
    return [(rejection.code, dict(rejection.params)) for rejection in result.rejections]


def _streams(game: GameSession) -> dict[str, dict]:
    return {key: state.model_dump(mode="json") for key, state in game.streams.export_states().items()}


def _encounter(game: GameSession) -> EncounterView:
    view = game.view(Visibility.PLAYER).encounter
    assert view is not None
    return view


def _rolls(events, kind: str, member_id: str) -> list[DetectionRolledEvent]:
    return [
        event
        for event in events
        if isinstance(event, DetectionRolledEvent) and event.kind == kind and event.character_id == member_id
    ]


def _attacks_by(events, attacker_id: str) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id == attacker_id]


def _attacks_on(events, defender_id: str) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.defender_id == defender_id]


def _index(events, event) -> int:
    return next(index for index, candidate in enumerate(events) if candidate is event)


def _player_visible(events) -> list[dict]:
    return [event.model_dump(mode="json") for event in events if event.visibility is Visibility.PLAYER]


def _hidden_seeds(*, passed: bool, **battle_args):
    """Each seed whose battle gives Sable a hide in shadows roll that passed, or failed, after one hide round.

    Yields each game after its hide round, with the round's events.
    """
    seed = SEEDS.start
    while seed < SEEDS.stop:
        game = _battle(first_seed=seed, **battle_args)
        events = _play(game, _hide(game))
        (roll,) = _rolls(events, "hide_in_shadows", _sable(game))
        if roll.passed is passed:
            yield game, events
        seed = game.master_seed + 1


def _hidden(*, passed: bool, **battle_args):
    return next(_hidden_seeds(passed=passed, **battle_args))


# ---------------------------------------------------------------------- the fields


class TestTheFields:
    """The new fields and the new event. These pass before and after the chunk, because the stub ships them."""

    def test_the_flag_defaults_to_both_skills(self):
        assert Ruleset().back_stab_needs_both_skills is True
        assert HOUSE_RULE.back_stab_needs_both_skills is False
        assert Ruleset.model_validate(HOUSE_RULE.model_dump(mode="json")) == HOUSE_RULE

    def test_a_hide_round_trips_through_its_json(self):
        command = ResolveBattleRound(declarations=(_declare("character-0001", "hide"),))
        restored = TypeAdapter(AnyCommand).validate_python(command.model_dump(mode="json"))
        assert restored == command
        assert restored.declarations[0].action == "hide"

    def test_the_state_and_the_view_start_with_nobody_hiding(self):
        assert BattleState(started_round=0).hiding == {}
        assert BattleState.model_validate({"started_round": 0}).hiding == {}
        view = EncounterView(groups=(), stance=None, in_battle=True)
        assert view.can_hide == ()
        assert view.hiding == ()

    def test_the_event_is_player_visible_and_round_trips(self):
        event = HideAttemptedEvent(character_id="character-0003")
        assert event.code == "battle.hide.attempted"
        assert event.visibility is Visibility.PLAYER
        assert parse_any_event(event.model_dump(mode="json")) == event
        assert format_message(event) == "character-0003 tries to hide in the shadows."


# ---------------------------------------------------------------------- the refusals


class TestTheRefusals:
    """Each hide refusal and the back-stab's new third check, each refusing the round before it costs anything."""

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_fighter_cannot_hide(self):
        game = _battle(roster=(FIGHTER, THIEF, SECOND_FIGHTER))
        hild = _named(game, "Hild")
        assert hild not in _encounter(game).front_rank, "precondition: Hild stands in the second rank"

        assert _refused(game, _hide(game, hild)) == [(NOT_A_THIEF, {"character": hild})]

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_thief_in_the_front_rank_cannot_hide(self):
        game = _battle(roster=DUO)
        sable = _sable(game)
        assert sable in _encounter(game).front_rank, "precondition: Sable stands in the front rank"

        assert _refused(game, _hide(game)) == [(IN_FRONT_RANK, {"character": sable})]

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_fighter_in_the_front_rank_gets_not_a_thief(self):
        game = _battle(roster=DUO)
        brakk = _named(game, "Brakk")

        assert _refused(game, _hide(game, brakk)) == [(NOT_A_THIEF, {"character": brakk})]

    @pytest.mark.xfail(reason=CHUNK)
    def test_the_front_rank_is_judged_after_the_rounds_moves(self):
        """Brakk falls back alone this round, which leaves Sable beside Hild in the first row."""
        game = _battle()
        sable = _sable(game)
        assert sable in _encounter(game).can_hide, "precondition: Sable can hide where the party stands now"

        assert _refused(game, _withdraw(game, "Brakk"), _hide(game)) == [(IN_FRONT_RANK, {"character": sable})]

    def test_a_hide_with_a_move_is_refused_as_not_combinable(self):
        """A guard: `hide` takes no `move`, like `hold`, and the check of `move` comes before the hide checks."""
        game = _battle()
        sable = _sable(game)

        rejections = _refused(game, _declare(sable, "hide", move="close", target_group_id=_group_id(game)))

        assert rejections == [(MOVE_NOT_COMBINABLE, {"character": sable})]

    def test_a_back_stab_on_a_hide_is_refused_as_not_an_attack(self):
        """A guard: the back-stab checks come first, on `hide` as on every action but `attack`."""
        game = _battle()
        sable = _sable(game)

        assert _refused(game, _declare(sable, "hide", back_stab=True)) == [(NOT_ATTACK, {"character": sable})]

    def test_a_back_stab_by_a_thief_who_hasnt_hidden_is_refused_as_aware(self):
        """A guard: from the second rank, `back_stab_target_aware` comes before the melee checks."""
        game = _battle()

        assert _refused(game, _stab(game)) == [(TARGET_AWARE, {"character": _sable(game)})]

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_back_stab_after_the_attempt_ended_is_refused_as_aware(self):
        game = _battle()
        sable = _sable(game)
        _play(game, _hide(game))
        assert _encounter(game).hiding == (sable,), "precondition: Sable is hiding"
        _play(game, _stab(game))
        assert game.mode is SessionMode.BATTLE, "precondition: the battle runs on"

        assert _refused(game, _stab(game)) == [(TARGET_AWARE, {"character": sable})]

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_hiding_thiefs_attack_without_back_stab_needs_the_front_rank(self):
        game = _battle()
        sable = _sable(game)
        _play(game, _hide(game))
        assert _encounter(game).hiding == (sable,), "precondition: Sable is hiding"

        assert _refused(game, _stab(game, back_stab=False)) == [
            ("battle.declaration.not_in_front_rank", {"character": sable})
        ]


# ---------------------------------------------------------------------- the views and the events


class TestWhatThePlayersSee:
    """The player view says who can hide and who is hiding, and nothing the players see tells how a hide went."""

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_thief_behind_the_front_rank_can_hide(self):
        game = _battle()

        assert _encounter(game).can_hide == (_sable(game),)
        assert _encounter(game).hiding == ()

    def test_nobody_in_the_front_rank_can_hide(self):
        """A guard: a thief beside a fighter in the front rank can't hide, and a fighter never can."""
        game = _battle(roster=DUO)

        assert _encounter(game).can_hide == ()

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_hide_shows_the_member_hiding_and_reports_the_roll_to_the_referee(self):
        game = _battle()
        sable = _sable(game)

        events = _play(game, _hide(game))

        attempts = [event for event in events if isinstance(event, HideAttemptedEvent)]
        assert [attempt.character_id for attempt in attempts] == [sable]
        (roll,) = _rolls(events, "hide_in_shadows", sable)
        assert roll.visibility is Visibility.REFEREE
        assert roll.chance == HS_AT_7
        assert roll.roll is not None and roll.passed is (roll.roll <= HS_AT_7)
        assert not _rolls(events, "move_silently", sable), "the hide rolls hide in shadows alone"
        assert _encounter(game).hiding == (sable,)
        assert game.battle.hiding == {sable: roll.passed}

    @pytest.mark.xfail(reason=CHUNK)
    def test_the_players_see_the_same_whether_the_hide_passed_or_failed(self):
        """The same battle with Sable at level 1 and at level 14, where a hide passes 10% and 99% of the time.

        The same d% roll from the same draw passes at level 1 on 1 to 10 and at level 14 on 1 to 99.

        The two battles differ from the start only in what Sable's level sets on the sheet. After the hide round,
        and in the attack round up to Sable's attack, every player-visible event is the same, and the player view
        differs only where it differed at the start.
        """
        seed = SEEDS.start
        while seed < SEEDS.stop:
            low, high = (_battle(first_seed=seed, level=level) for level in (1, 14))
            assert low.master_seed == high.master_seed, "precondition: Sable's level doesn't change the opening"
            seed = low.master_seed + 1
            differing = _differing_paths(_player_view(low), _player_view(high))
            assert differing, "precondition: the sheets show Sable's level"
            low_hide, high_hide = (_play(game, _hide(game)) for game in (low, high))
            (low_roll,) = _rolls(low_hide, "hide_in_shadows", _sable(low))
            (high_roll,) = _rolls(high_hide, "hide_in_shadows", _sable(high))
            assert low_roll.roll == high_roll.roll, "precondition: the same d% from the same draw"
            if low_roll.passed or not high_roll.passed:
                continue  # Look for a roll from 11 to 99, which fails at level 1 and passes at level 14.

            assert _player_visible(low_hide) == _player_visible(high_hide)
            assert _differing_paths(_player_view(low), _player_view(high)) == differing
            assert _encounter(low).hiding == _encounter(high).hiding == (_sable(low),)

            low_stab, high_stab = (_play(game, _stab(game)) for game in (low, high))
            low_first = _attacks_by(low_stab, _sable(low))[0]
            high_first = _attacks_by(high_stab, _sable(high))[0]
            assert _player_visible(low_stab[: _index(low_stab, low_first)]) == _player_visible(
                high_stab[: _index(high_stab, high_first)]
            )
            return
        raise AssertionError("precondition: no seed rolled a hide that fails at level 1 and passes at level 14")


def _player_view(game: GameSession) -> dict:
    return json.loads(game.view(Visibility.PLAYER).model_dump_json())


def _differing_paths(first, second, path: str = "") -> set[str]:
    """The JSON paths at which two parsed documents differ."""
    if isinstance(first, dict) and isinstance(second, dict):
        keys = sorted(set(first) | set(second))
        return set().union(*(_differing_paths(first.get(key), second.get(key), f"{path}/{key}") for key in keys))
    if isinstance(first, list) and isinstance(second, list) and len(first) == len(second):
        pairs = enumerate(zip(first, second, strict=True))
        return set().union(*(_differing_paths(one, other, f"{path}/{index}") for index, (one, other) in pairs))
    return set() if first == second else {path}


# ---------------------------------------------------------------------- the rolls and the two settings


def _d100s_between(start: RngStreamState, end: RngStreamState, *, limit: int = 2000) -> set[int]:
    """Every d100 a stream at `start` would give from each raw position before it reaches `end`."""
    stepper = RngStream.restore(start)
    positions = 0
    while stepper.export_state() != end:
        stepper.next_uint64()
        positions += 1
        assert positions < limit, "the end state isn't reachable from the start state"
    rolls = set()
    for skip in range(positions):
        stream = RngStream.restore(start)
        for _ in range(skip):
            stream.next_uint64()
        rolls.add(stream.randbelow(100) + 1)
    return rolls


def _hide_then_stab(seed: int, ruleset: Ruleset, **battle_args):
    """Play a hide round and then a back-stab round from the first battle at or after `seed`.

    Returns the game, the attack round's events, and Sable's two rolls as `(hide in shadows, move silently)`.
    """
    game = _battle(first_seed=seed, ruleset=ruleset, **battle_args)
    sable = _sable(game)
    hide_events = _play(game, _hide(game))
    (hide_roll,) = _rolls(hide_events, "hide_in_shadows", sable)
    events = _play(game, _stab(game))
    (move_roll,) = _rolls(events, "move_silently", sable)
    return game, events, (hide_roll, move_roll)


def _combinations(ruleset: Ruleset, **battle_args) -> dict[tuple[bool, bool], tuple[int, list, tuple]]:
    """For each combination of the two rolls, the first seed that gives it, with its attack round and its rolls."""
    found: dict[tuple[bool, bool], tuple[int, list, tuple]] = {}
    seed = SEEDS.start
    while len(found) < 4 and seed < SEEDS.stop:
        game, events, rolls = _hide_then_stab(seed, ruleset, **battle_args)
        found.setdefault((rolls[0].passed, rolls[1].passed), (game.master_seed, events, rolls))
        seed = game.master_seed + 1
    assert len(found) == 4, f"precondition: every combination of the two rolls, found {sorted(found)}"
    return found


BOTH_NEEDED = {(True, True): True, (True, False): False, (False, True): False, (False, False): False}
"""Under the default, the attack is a back-stab only when both rolls pass."""
EITHER_ENOUGH = {(True, True): True, (True, False): True, (False, True): True, (False, False): False}
"""Under the house rule, the attack is a back-stab when either roll passes."""


class TestTheRolls:
    """Each roll's time, stream, and visibility, and how each setting combines them."""

    @pytest.mark.xfail(reason=CHUNK)
    @pytest.mark.parametrize("first_seed", [0, 7, 10, 22, 31])
    def test_the_hide_draws_from_the_combat_stream_alone(self, first_seed):
        """The same battle with Sable hiding and with Sable holding: no stream but the combat stream differs.

        Sable stands in the second rank, so the goblins' choice of targets, on the monster action stream, is the
        same whether Sable hid or not. The combat stream's final state can match the holding game's, because a
        bounded draw discards a raw number now and then and the hide can land on one the holding game discards.
        So the test finds the roll in the combat stream instead: some raw position between the stream's state
        before the round and its state after it gives the roll as a d100. The seeds include the ones on which the
        two final states match.
        """
        hiding, holding = _battle(first_seed=first_seed), _battle(first_seed=first_seed)
        assert _streams(hiding) == _streams(holding), "precondition: identical setups"
        before = hiding.streams.get(COMBAT_STREAM).export_state()

        (roll,) = _rolls(_play(hiding, _hide(hiding)), "hide_in_shadows", _sable(hiding))
        _play(holding)

        hid, held = _streams(hiding), _streams(holding)
        assert {key: value for key, value in hid.items() if key != COMBAT_STREAM} == {
            key: value for key, value in held.items() if key != COMBAT_STREAM
        }
        assert roll.roll in _d100s_between(before, hiding.streams.get(COMBAT_STREAM).export_state())

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_back_stab_from_hiding_rolls_move_silently_before_its_attack(self):
        game, events, (_, move_roll) = _hide_then_stab(SEEDS.start, Ruleset())
        sable = _sable(game)

        assert move_roll.visibility is Visibility.REFEREE
        assert move_roll.chance == MS_AT_7
        assert move_roll.roll is not None and move_roll.passed is (move_roll.roll <= MS_AT_7)
        (attack,) = _attacks_by(events, sable)
        assert _index(events, move_roll) < _index(events, attack)
        assert not _rolls(events, "hide_in_shadows", sable), "the attack rolls move silently alone"

    @pytest.mark.xfail(reason=CHUNK)
    @pytest.mark.parametrize(
        ("ruleset", "outcomes"), [(Ruleset(), BOTH_NEEDED), (HOUSE_RULE, EITHER_ENOUGH)], ids=["default", "house"]
    )
    def test_each_combination_of_the_rolls_under_each_setting(self, ruleset, outcomes):
        for combination, (_, events, (hide_roll, _)) in _combinations(ruleset).items():
            sable = hide_roll.character_id
            attacks = _attacks_by(events, sable)
            assert len(attacks) == 1
            assert attacks[0].back_stab is outcomes[combination], combination

    @pytest.mark.xfail(reason=CHUNK)
    def test_both_settings_draw_the_same(self):
        """The same declarations from the same seed under both settings draw the same numbers from every stream.

        The rolls and the attack's d20 come from the same draws. When the two settings agree on the attack, the
        whole session matches but for the ruleset. When they differ, the house rule's attack is the back-stab, with
        4 more on the same d20.
        """
        for combination, (seed, _, _) in _combinations(Ruleset()).items():
            both, either = (_hide_then_stab(seed, ruleset) for ruleset in (Ruleset(), HOUSE_RULE))
            (both_game, both_events, both_rolls), (either_game, either_events, either_rolls) = both, either
            assert both_game.master_seed == either_game.master_seed == seed
            assert [roll.model_dump() for roll in both_rolls] == [roll.model_dump() for roll in either_rolls]
            (both_attack,) = _attacks_by(both_events, _sable(both_game))
            (either_attack,) = _attacks_by(either_events, _sable(either_game))
            assert both_attack.roll == either_attack.roll, combination
            if BOTH_NEEDED[combination] == EITHER_ENOUGH[combination]:
                assert _streams(both_game) == _streams(either_game), combination
                both_state, either_state = _state_without_logs(both_game), _state_without_logs(either_game)
                both_state.pop("ruleset")
                either_state.pop("ruleset")
                assert both_state == either_state, combination
            else:
                assert (both_attack.back_stab, either_attack.back_stab) == (False, True)
                assert either_attack.modifier == both_attack.modifier + 4

    def test_a_battle_with_no_hide_draws_as_it_did_before_the_chunk(self):
        """A guard, before and after the chunk: a battle in which nobody hides draws exactly as `main` did.

        The digest is of the event log and every stream's final state after four rounds in which Brakk and Hild
        attack while the goblins stand and fight, and Sable holds, from the seed `_battle` takes. It was taken on
        `main` at 6a508af, before hiding existed, by the same steps.
        """
        game = _battle()
        group = game.encounter.groups[0]
        fighters = [_named(game, name) for name in ("Brakk", "Hild")]
        for _ in range(4):
            if game.mode is not SessionMode.BATTLE:
                break
            if group.fleeing:
                _play(game)
            else:
                _play(game, *(_declare(member, "attack", target_group_id=group.id) for member in fighters))

        assert (game.master_seed, _digest(game)) == NO_HIDE_DIGEST

    @pytest.mark.parametrize(
        ("battle_args", "rounds", "expected"),
        [
            ({"npc_party": True, "count": 3, "distance_feet": 30}, 4, "NO_HIDE_NPC_DIGEST"),
            ({"template_id": "red_dragon", "count": 1, "distance_feet": 20}, 3, "NO_HIDE_BREATH_DIGEST"),
        ],
        ids=["npc-party", "red-dragon"],
    )
    def test_other_battles_with_no_hide_draw_as_they_did_before_the_chunk(self, battle_args, rounds, expected):
        """Guards like the goblin fight's: a basic NPC party 30' away, and a red dragon that breathes from 20' away.

        Every member holds each round, from the seed `_battle` takes, and the digests were taken on `main` at
        6a508af by the same steps. The NPC adventurers shoot, cast, and close by `NpcPartyPolicy`, and the dragon
        breathes on the party's ranks, so both pass through the target choices that hiding changes.
        """
        game = _battle(**battle_args)
        for _ in range(rounds):
            if game.mode is not SessionMode.BATTLE:
                break
            _play(game)

        assert (game.master_seed, _digest(game)) == globals()[expected]


def _digest(game: GameSession) -> str:
    """The SHA-256 of a session's event log and every stream's state, as the no-hide guards pin it."""
    record = {"events": [event.model_dump(mode="json") for event in game.event_log], "streams": _streams(game)}
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


NO_HIDE_DIGEST = (0, "b4320e255c2e7ad618638ebc322949863b223ccf102d641f477b27d424a4fc59")
"""The seed `_battle` takes and the digest of the no-hide goblin battle, taken on `main` at 6a508af."""
NO_HIDE_NPC_DIGEST = (0, "707bfd5abdeba613386d6dcd4ea70e0b24b87c4acf8cdb15eb2e89934bd2a610")
"""The seed and the digest of the no-hide battle against a basic NPC party, taken on `main` at 6a508af."""
NO_HIDE_BREATH_DIGEST = (0, "240574ae1c4b66e009adf18d41bb4510f7a52b1db0298cde9799cc831892b439")
"""The seed and the digest of the no-hide battle against a red dragon, taken on `main` at 6a508af."""


# ---------------------------------------------------------------------- the arithmetic


class TestTheArithmetic:
    """A back-stab from hiding uses the kernel's arithmetic: +4 to hit, and the whole damage sum doubled."""

    @pytest.mark.xfail(reason=CHUNK)
    def test_the_house_rules_back_stab_adds_four_and_doubles_the_whole_sum(self):
        """Sable has STR 16, which adds 2 to melee attack and damage rolls, as `STR_16` gives.

        A seed where hide in shadows passed and move silently failed makes the house rule's attack a back-stab and
        the default's an ordinary attack, from the same draws. Where both hit, the back-stab's modifier is 4 more on
        the same d20, and its damage is `(1d8 + STR) × 2` on the same die, not `1d8 × 2 + STR`.
        """
        seed = SEEDS.start
        while seed < SEEDS.stop:
            plain_game, plain_events, (hide_roll, move_roll) = _hide_then_stab(seed, Ruleset(), strength=16)
            seed = plain_game.master_seed + 1
            if not hide_roll.passed or move_roll.passed:
                continue
            (plain,) = _attacks_by(plain_events, _sable(plain_game))
            if plain.code != "combat.attack.hit":
                continue
            stab_game, stab_events, _ = _hide_then_stab(plain_game.master_seed, HOUSE_RULE, strength=16)
            (stab,) = _attacks_by(stab_events, _sable(stab_game))
            if stab.code != "combat.attack.hit":
                continue

            assert (plain.back_stab, stab.back_stab) == (False, True)
            assert stab.roll == plain.roll
            assert stab.modifier == plain.modifier + 4
            assert plain.modifier == STR_16
            assert stab.defender_id == plain.defender_id
            (plain_damage,) = [
                e for e in plain_events if isinstance(e, DamageDealtEvent) and e.attacker_id == plain.attacker_id
            ]
            (stab_damage,) = [
                e for e in stab_events if isinstance(e, DamageDealtEvent) and e.attacker_id == stab.attacker_id
            ]
            assert stab_damage.rolls == plain_damage.rolls, "a back-stab takes no draw of its own"
            assert plain_damage.amount == sum(plain_damage.rolls) + STR_16
            assert stab_damage.amount == 2 * (sum(stab_damage.rolls) + STR_16)
            return
        raise AssertionError("precondition: no seed gave a passed hide, a failed move, and two hits")


STR_16 = 2
"""The STR modifier for a score of 16. The OSE SRD's table gives +2 for scores of 16 and 17, and the melee modifier
"Is applied to attack and damage rolls with melee weapons"."""


# ---------------------------------------------------------------------- the monsters' targets


class TestTheMonstersTargets:
    """The goblins pass over a hidden thief while they can pick another member, and find the thief when they can't.

    Sable hides in the second rank. Then Brakk falls back alone, which leaves Sable beside Hild in the first row,
    within the goblins' reach. With both fighters fallen back, Sable stands alone in it.
    """

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_hidden_thief_is_passed_over_while_another_member_is_in_reach(self):
        game, _ = _hidden(passed=True)
        sable, hild = _sable(game), _named(game, "Hild")
        events = _play(game, _withdraw(game, "Brakk"))
        group_front = _encounter(game).groups[0].front_rank
        assert set(group_front) == {sable, hild}, "precondition: Sable stands in the goblins' reach beside Hild"
        for _ in range(3):
            events += _play(game)

        assert not _attacks_on(events, sable)
        assert _attacks_on(events, hild), "precondition: the goblins attacked"
        assert _encounter(game).hiding == (sable,)
        assert game.battle.hiding == {sable: True}

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_noticed_thief_can_be_attacked_and_the_attack_ends_the_attempt(self):
        for game, _ in _hidden_seeds(passed=False):
            sable = _sable(game)
            events = _play(game, _withdraw(game, "Brakk"))
            for _ in range(4):
                if _attacks_on(events, sable):
                    assert _encounter(game).hiding == ()
                    assert game.battle.hiding == {}
                    return
                assert _encounter(game).hiding == (sable,), "nobody attacked Sable, so Sable is still hiding"
                events = _play(game)
        raise AssertionError("precondition: no seed's goblins attacked the noticed thief")

    @pytest.mark.xfail(reason=CHUNK)
    def test_the_monsters_find_a_hidden_thief_who_is_the_only_member_in_reach(self):
        game, _ = _hidden(passed=True)
        sable = _sable(game)
        events = _play(game, _withdraw(game, "Brakk"), _withdraw(game, "Hild"))
        assert _encounter(game).groups[0].front_rank == (sable,), "precondition: Sable alone in the goblins' reach"
        events += _play(game)

        assert _attacks_on(events, sable)
        assert _encounter(game).hiding == ()
        assert game.battle.hiding == {}


# ---------------------------------------------------------------------- what ends the attempt


class TestWhatEndsTheAttempt:
    """Each way an attempt ends, and the ways it carries on."""

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_hold_keeps_the_attempt_and_a_later_back_stab_takes_it(self):
        game = _battle()
        sable = _sable(game)
        _play(game, _hide(game))
        _play(game)
        _play(game)
        assert _encounter(game).hiding == (sable,)

        events = _play(game, _stab(game))

        assert len(_rolls(events, "move_silently", sable)) == 1
        assert len(_attacks_by(events, sable)) == 1
        assert game.mode is not SessionMode.BATTLE or _encounter(game).hiding == ()

    @pytest.mark.xfail(reason=CHUNK)
    def test_another_declaration_ends_the_attempt(self):
        game = _battle()
        _play(game, _hide(game))
        assert _encounter(game).hiding == (_sable(game),)

        _play(game, _withdraw(game, "Sable"))

        assert _encounter(game).hiding == ()
        assert game.battle.hiding == {}

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_second_hide_replaces_the_attempt_with_a_new_roll(self):
        game = _battle()
        sable = _sable(game)
        _play(game, _hide(game))

        events = _play(game, _hide(game))

        (roll,) = _rolls(events, "hide_in_shadows", sable)
        assert _encounter(game).hiding == (sable,)
        assert game.battle.hiding == {sable: roll.passed}

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_thief_can_hide_again_after_attacking(self):
        game = _battle()
        sable = _sable(game)
        _play(game, _hide(game))
        _play(game, _stab(game))
        assert game.mode is SessionMode.BATTLE, "precondition: the battle runs on"
        assert sable in _encounter(game).can_hide

        events = _play(game, _hide(game))

        assert len(_rolls(events, "hide_in_shadows", sable)) == 1
        assert _encounter(game).hiding == (sable,)

    @pytest.mark.xfail(reason=CHUNK)
    def test_damage_ends_the_attempt(self):
        """A red dragon's breath covers the party's ranks from the front, Sable's second rank among them.

        The test takes a seed in which the party acts first, so Sable hides before the dragon breathes, and in which
        the hide in shadows roll passed, so the area covers a thief the monsters can't pick.
        """
        seed = SEEDS.start
        while seed < SEEDS.stop:
            game = _battle(first_seed=seed, template_id="red_dragon", count=1, distance_feet=20)
            seed = game.master_seed + 1
            sable = _sable(game)
            events = _play(game, _hide(game))
            initiative = next(event for event in events if isinstance(event, InitiativeRolledEvent))
            if initiative.order[0] not in {member.id for member in game.party.members}:
                continue
            if not _rolls(events, "hide_in_shadows", sable)[0].passed:
                continue
            burned = [event for event in events if isinstance(event, DamageDealtEvent) and event.target_id == sable]
            if not burned:
                continue  # The dragon didn't breathe this round.
            assert _index(events, _rolls(events, "hide_in_shadows", sable)[0]) < _index(events, burned[0])

            assert _encounter(game).hiding == ()
            assert game.battle.hiding == {}
            return
        raise AssertionError("precondition: no seed's dragon breathed on Sable after Sable hid")

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_thief_who_cant_act_at_the_end_of_a_round_stops_hiding(self):
        game = _battle()
        sable = _sable(game)
        _play(game, _hide(game))
        asleep = EffectDefinition(
            kind="sleep", duration_unit=TimeUnit.TURN, duration_amount=4, condition=Condition.ASLEEP
        )
        effect, _ = game.ledger.attach(
            asleep, sable, clock=game.clock, allocator=game.allocator, registry=game.registry()
        )
        assert effect is not None and incapacitated(game.member(sable)), "precondition: Sable is asleep"

        _play(game)

        assert _encounter(game).hiding == ()
        assert game.battle.hiding == {}

    @pytest.mark.xfail(reason=CHUNK)
    def test_the_attempt_ends_with_the_battle(self):
        """The fighters kill the goblins while Sable holds in hiding, and the next battle starts with nobody hiding."""
        game = _battle(count=1)
        sable = _sable(game)
        _play(game, _hide(game))
        assert _encounter(game).hiding == (sable,), "precondition: Sable is hiding"
        fighters = [_named(game, name) for name in ("Brakk", "Hild")]
        for _ in range(40):
            if game.mode is not SessionMode.BATTLE:
                break
            _play(game, *(_declare(member, "attack", target_group_id=_group_id(game)) for member in fighters))
        assert game.battle is None, "precondition: the battle is over"

        result = game.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=5))
        assert result.accepted
        if game.mode is SessionMode.ENCOUNTER:
            assert game.execute(EngageBattle()).accepted
        assert game.mode is SessionMode.BATTLE, "precondition: a new battle"

        assert _encounter(game).hiding == ()
        assert game.battle.hiding == {}


# ---------------------------------------------------------------------- saves and replays


class TestSavesAndReplays:
    """An attempt survives a save and load, and a battle with a hide replays exactly."""

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_save_in_the_middle_of_an_attempt_keeps_it(self):
        game, _ = _hidden(passed=True)
        sable = _sable(game)

        restored = load_game(json.loads(json.dumps(save_game(game))))

        assert restored.battle.hiding == {sable: True}
        assert _encounter(restored).hiding == (sable,)
        played, reloaded = _play(game, _stab(game)), _play(restored, _stab(restored))
        assert _rolls(reloaded, "move_silently", sable), "precondition: the back-stab came from hiding"
        assert [event.model_dump(mode="json") for event in played] == [
            event.model_dump(mode="json") for event in reloaded
        ]

    def test_a_save_with_no_hiding_key_loads_with_nobody_hiding(self):
        """A guard: a battle saved before `BattleState.hiding` existed loads with the field empty."""
        game = _battle()
        _play(game)
        document = json.loads(json.dumps(save_game(game)))
        battle = document["payload"]["battle"]
        assert "hiding" in battle, "precondition: the save carries the field to remove"
        del battle["hiding"]

        restored = load_game(document)

        assert restored.battle.hiding == {}
        assert _encounter(restored).hiding == ()

    @pytest.mark.xfail(reason=CHUNK)
    @pytest.mark.parametrize("ruleset", [Ruleset(), HOUSE_RULE], ids=["default", "house"])
    def test_a_battle_with_a_hide_replays_from_its_command_log(self, ruleset):
        game = _battle(ruleset=ruleset)
        roster = [_character(name, class_id) for name, class_id in TRIO]
        _play(game, _hide(game))
        _play(game)
        _play(game, _stab(game))

        replayed = replay_game(game.master_seed, party_to_document(roster), game.adventure, ruleset, game.command_log)

        assert [event.model_dump(mode="json") for event in replayed.event_log] == [
            event.model_dump(mode="json") for event in game.event_log
        ]
        assert session_state(replayed) == session_state(game)


# ---------------------------------------------------------------------- the approach and the timing of the attack


def _initiative_party_first(game: GameSession, events) -> bool:
    initiative = next(event for event in events if isinstance(event, InitiativeRolledEvent))
    return initiative.order[0] in {member.id for member in game.party.members}


def _fizzles_by(events, member_id: str) -> list[DeclarationFizzledEvent]:
    return [event for event in events if isinstance(event, DeclarationFizzledEvent) and event.actor_id == member_id]


class TestTheApproach:
    """A back-stab from hiding needs a group within reach, and a `close` on it is part of the approach."""

    def _hidden_far(self):
        """Sable hidden after one round against goblins that started 40' away, with the goblins still out of reach."""
        game = _battle(distance_feet=40)
        sable = _sable(game)
        _play(game, _hide(game))
        assert _encounter(game).hiding == (sable,), "precondition: Sable is hiding"
        distance = _encounter(game).groups[0].member_distance_feet[sable]
        rate = next(member for member in game.view(Visibility.PLAYER).party if member.id == sable).encounter_rate_feet
        assert 5 < distance <= rate + 5, "precondition: out of reach, and within a close of Sable's rate"
        return game, sable, distance

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_back_stab_from_hiding_at_a_group_out_of_reach_is_refused(self):
        game, sable, distance = self._hidden_far()

        assert _refused(game, _stab(game)) == [
            ("combat.attack.out_of_reach", {"distance_feet": distance, "character": sable})
        ]

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_close_with_a_back_stab_from_hiding_keeps_the_attempt_to_the_attack(self):
        game, sable, _ = self._hidden_far()
        stab = _declare(sable, "attack", target_group_id=_group_id(game), back_stab=True, move="close")

        events = _play(game, stab)

        (move_roll,) = _rolls(events, "move_silently", sable)
        (attack,) = _attacks_by(events, sable)
        assert _index(events, move_roll) < _index(events, attack)
        assert not _fizzles_by(events, sable)
        moves = [event for event in events if isinstance(event, MemberMovedEvent) and event.character_id == sable]
        assert all(_index(events, move) < _index(events, move_roll) for move in moves)
        if game.mode is SessionMode.BATTLE:
            assert _encounter(game).hiding == ()
            if moves:
                assert _encounter(game).advance_feet[sable] == moves[-1].advance_feet, "Sable stays where she closed"

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_slow_back_stab_from_hiding_rolls_after_the_monsters_and_is_passed_over_until_then(self):
        """Sable swings a battle axe, which is slow, from beside Hild in the goblins' reach.

        The monsters' block comes before a slow blow whichever side wins initiative, so every goblin attack of the
        round comes before the move silently roll, and none of them is on Sable, whose hide in shadows passed.
        """
        game, _ = _hidden(passed=True, weapons=("battle_axe",))
        sable, hild = _sable(game), _named(game, "Hild")
        _play(game, _withdraw(game, "Brakk"))
        assert set(_encounter(game).groups[0].front_rank) == {sable, hild}, "precondition: Sable beside Hild"
        assert _encounter(game).hiding == (sable,), "precondition: Sable is still hiding"

        events = _play(game, _stab(game))

        (move_roll,) = _rolls(events, "move_silently", sable)
        (attack,) = _attacks_by(events, sable)
        goblin_attacks = [event for event in events if isinstance(event, AttackRolledEvent) and event is not attack]
        assert goblin_attacks, "precondition: the goblins attacked this round"
        assert all(_index(events, event) < _index(events, move_roll) for event in goblin_attacks)
        assert _index(events, move_roll) < _index(events, attack)
        assert not _attacks_on(events, sable)

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_hasted_back_stab_from_hiding_rolls_once_and_both_swings_agree(self):
        """Under *haste*, as in the free round, every swing of a back-stab declaration is a back-stab or none is."""
        for combination, (seed, _, _) in _combinations(Ruleset()).items():
            game = _battle(first_seed=seed)
            sable = _sable(game)
            (hide_roll,) = _rolls(_play(game, _hide(game)), "hide_in_shadows", sable)
            effect, _ = game.ledger.attach(
                HASTE, sable, clock=game.clock, allocator=game.allocator, registry=game.registry()
            )
            assert effect is not None

            events = _play(game, _stab(game))

            (move_roll,) = _rolls(events, "move_silently", sable)
            swings = _attacks_by(events, sable)
            assert len(swings) == 2, "precondition: haste gave Sable two swings"
            assert _index(events, move_roll) < _index(events, swings[0])
            expected = BOTH_NEEDED[(hide_roll.passed, move_roll.passed)]
            assert [swing.back_stab for swing in swings] == [expected] * 2, combination


HASTE = EffectDefinition(kind="haste", params={"attacks_multiplier": 2, "movement_multiplier": 2})


class TestAnAttemptThatEndsBeforeTheAttack:
    """A back-stab accepted from hiding whose attempt ends before it comes up is an ordinary attack, unrefused."""

    @pytest.mark.xfail(reason=CHUNK)
    def test_damage_before_the_attack_leaves_an_ordinary_attack_from_the_second_rank(self):
        """A red dragon 5' away breathes before Sable hides, and again before her back-stab comes up.

        The dragon opens with its breath, so a round-one hide that comes after the dragon's turn leaves Sable hiding.
        In round two the dragon acts first again and breathes on the ranks, which damages Sable in the second rank.
        """
        seed = SEEDS.start
        while seed < SEEDS.stop:
            game = _battle(first_seed=seed, template_id="red_dragon", count=1, distance_feet=5)
            seed = game.master_seed + 1
            sable = _sable(game)
            first = _play(game, _hide(game))
            if _initiative_party_first(game, first) or _encounter(game).hiding != (sable,):
                continue
            assert sable not in _encounter(game).groups[0].front_rank, "precondition: Sable in the second rank"
            events = _play(game, _stab(game))
            burned = [event for event in events if isinstance(event, DamageDealtEvent) and event.target_id == sable]
            attacks = _attacks_by(events, sable)
            if _initiative_party_first(game, events) or not burned or not attacks:
                continue

            assert _index(events, burned[0]) < _index(events, attacks[0])
            assert not _rolls(events, "move_silently", sable)
            assert [attack.back_stab for attack in attacks] == [False] * len(attacks)
            assert not _fizzles_by(events, sable)
            return
        raise AssertionError("precondition: no seed's dragon breathed on Sable before her back-stab came up")

    @pytest.mark.xfail(reason=CHUNK)
    def test_an_attack_on_the_thief_before_the_back_stab_leaves_an_ordinary_attack(self):
        """Both fighters fall back while the goblins act first, and in the next round the goblins find Sable first."""
        for game, _ in _hidden_seeds(passed=True):
            sable = _sable(game)
            withdrawal = _play(game, _withdraw(game, "Brakk"), _withdraw(game, "Hild"))
            if _initiative_party_first(game, withdrawal) or _encounter(game).hiding != (sable,):
                continue
            assert _encounter(game).groups[0].front_rank == (sable,), "precondition: Sable alone in reach"
            events = _play(game, _stab(game))
            if _initiative_party_first(game, events):
                continue
            found = _attacks_on(events, sable)
            attacks = _attacks_by(events, sable)
            assert found and attacks, "precondition: the goblins attacked Sable, and Sable attacked"

            assert _index(events, found[0]) < _index(events, attacks[0])
            assert not _rolls(events, "move_silently", sable)
            assert [attack.back_stab for attack in attacks] == [False] * len(attacks)
            return
        raise AssertionError("precondition: no seed let the goblins act first in both rounds")


# ---------------------------------------------------------------------- the other declarations that end an attempt


class TestTheOtherDeclarationsThatEndAnAttempt:
    """Each declaration other than `hold` or `hide` that a thief can make ends the attempt when it comes up.

    A thief has no spells and can't turn undead, and no built-in class with thief skills can, so `cast` and
    `turn_undead` aren't reachable here.
    """

    @pytest.mark.xfail(reason=CHUNK)
    def test_drinking_a_potion_ends_the_attempt(self):
        game = _battle(items=("potion_of_healing",))
        sable = _sable(game)
        potion = next(item.instance_id for item in game.member(sable).inventory.items if hasattr(item, "instance_id"))
        _play(game, _hide(game))
        assert _encounter(game).hiding == (sable,), "precondition: Sable is hiding"

        _play(game, _declare(sable, "use_item", item_id=potion))

        assert _encounter(game).hiding == ()

    @pytest.mark.xfail(reason=CHUNK)
    def test_handing_over_an_item_ends_the_attempt(self):
        game = _battle(items=("oil_flask",))
        sable, hild = _sable(game), _named(game, "Hild")
        _play(game, _hide(game))
        assert _encounter(game).hiding == (sable,), "precondition: Sable is hiding"

        _play(game, _declare(sable, "give_item", item_id="oil_flask", targets=(hild,)))

        assert _encounter(game).hiding == ()

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_missile_attack_without_back_stab_ends_the_attempt(self):
        """A missile attack takes no rank check, so it's legal from the second rank."""
        game = _battle(weapons=("short_bow",), items=("arrows",))
        sable = _sable(game)
        _play(game, _hide(game))
        assert _encounter(game).hiding == (sable,), "precondition: Sable is hiding"

        events = _play(game, _declare(sable, "attack", target_group_id=_group_id(game), weapon_id="short_bow"))

        assert _attacks_by(events, sable), "precondition: Sable shot"
        assert not _rolls(events, "move_silently", sable)
        assert _encounter(game).hiding == ()

    @pytest.mark.xfail(reason=CHUNK)
    def test_an_npc_adventurers_attack_on_a_noticed_thief_ends_the_attempt(self):
        """Sable's hide in shadows failed, and she stands beside Hild in the NPC party's reach or line of fire."""
        for game, _ in _hidden_seeds(passed=False, npc_party=True, count=4, distance_feet=30):
            sable = _sable(game)
            npcs = set(game.encounter.groups[0].monster_ids)
            events = _play(game, _withdraw(game, "Brakk"))
            for _ in range(4):
                aimed = [event for event in _attacks_on(events, sable) if event.attacker_id in npcs] + [
                    event
                    for event in events
                    if isinstance(event, DamageDealtEvent) and event.target_id == sable and event.attacker_id in npcs
                ]
                if aimed:
                    assert _encounter(game).hiding == ()
                    return
                if game.mode is not SessionMode.BATTLE or _encounter(game).hiding != (sable,):
                    break
                events = _play(game)
        raise AssertionError("precondition: no seed's NPC adventurers attacked the noticed thief")


# ---------------------------------------------------------------------- passing over a hidden thief


def _beside_hild(*, passed: bool, **battle_args) -> tuple[GameSession, str, str]:
    """Sable hidden, then Brakk fallen back alone, which leaves Sable beside Hild in the first row."""
    game, _ = _hidden(passed=passed, **battle_args)
    sable, hild = _sable(game), _named(game, "Hild")
    _play(game, _withdraw(game, "Brakk"))
    assert set(_encounter(game).front_rank) == {sable, hild}, "precondition: Sable in the first row beside Hild"
    return game, sable, hild


CONFUSION = EffectDefinition(
    kind="confusion",
    condition=Condition.CONFUSED,
    params={"behaviour_dice": "2d6", "behaviour_table": ["2-12:attack_caster_group"], "resave_hd_min_count": 99},
)
"""A confusion whose behaviour roll always sends the monster at the party, and which the monster never re-saves."""


def _attach(game: GameSession, definition: EffectDefinition, target_id: str) -> None:
    effect, _ = game.ledger.attach(
        definition, target_id, clock=game.clock, allocator=game.allocator, registry=game.registry()
    )
    assert effect is not None, "precondition: the effect attached"


class TestPassingOverEverywhere:
    """Every place a monster side picks one member passes over a hidden thief while another member can be picked."""

    def _npc_targets(self, game: GameSession) -> list[str]:
        """The members `NpcPartyPolicy` aims at over many choices, each from its own monster action stream."""
        group = game.encounter.groups[0]
        members = {member.id for member in game.party.members}
        targets = []
        for seed in range(40):
            for action in NpcPartyPolicy().choose(game, group, RngStreams(master_seed=seed).get(MONSTER_ACTION_STREAM)):
                if action.target_id in members:
                    targets.append(action.target_id)
        return targets

    @pytest.mark.xfail(reason=CHUNK)
    def test_npc_adventurers_pass_over_a_hidden_thief(self):
        game, sable, _ = _beside_hild(passed=True, npc_party=True, count=4, distance_feet=30)

        targets = self._npc_targets(game)

        assert targets, "precondition: the NPC adventurers aim at someone"
        assert sable not in targets

    @pytest.mark.xfail(reason=CHUNK)
    def test_npc_adventurers_can_pick_a_noticed_thief(self):
        game, sable, _ = _beside_hild(passed=False, npc_party=True, count=4, distance_feet=30)

        assert sable in self._npc_targets(game)

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_confused_monster_passes_over_a_hidden_thief(self):
        game, sable, hild = _beside_hild(passed=True)
        for monster_id in game.encounter.groups[0].monster_ids:
            _attach(game, CONFUSION, monster_id)

        events = [event for _ in range(4) for event in _play(game)]

        assert _attacks_on(events, hild), "precondition: the confused goblins attacked"
        assert not _attacks_on(events, sable)
        assert _encounter(game).hiding == (sable,)

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_confused_monster_can_pick_a_noticed_thief(self):
        game, sable, _ = _beside_hild(passed=False)
        for monster_id in game.encounter.groups[0].monster_ids:
            _attach(game, CONFUSION, monster_id)

        events = [event for _ in range(4) for event in _play(game)]

        assert _attacks_on(events, sable)

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_breath_that_hits_one_member_passes_over_a_hidden_thief(self):
        """A hellhound's breath hits one member of the first row, which allows a save, and its bite picks one too."""
        game, sable, hild = _beside_hild(passed=True, template_id="hellhound_3", count=1)
        events = []
        for _ in range(12):
            if game.mode is not SessionMode.BATTLE:
                break
            events += _play(game)
        breaths = [event for event in events if isinstance(event, SavingThrowRolledEvent)]

        assert breaths and {event.target_id for event in breaths} == {hild}, "precondition: the hellhound breathed"
        assert not [event for event in events if getattr(event, "target_id", None) == sable]
        assert not _attacks_on(events, sable)

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_statue_is_passed_over_before_a_hidden_thief(self):
        """With Hild turned to stone beside her, Sable is the only member the goblins could pick, so they find her."""
        game, sable, hild = _beside_hild(passed=True)
        _attach(game, STONE, hild)

        events = _play(game)

        assert _attacks_on(events, sable)
        assert not _attacks_on(events, hild)
        assert _encounter(game).hiding == ()


STONE = EffectDefinition(kind="petrification", permanent=True, condition=Condition.PETRIFIED)


# ---------------------------------------------------------------------- previews, order, and the free round


class TestPreviewsOrderAndTheFreeRound:
    """The preview judges a hide as the round does, thieves hide in marching order, and a hide works in any round."""

    @pytest.mark.xfail(reason=CHUNK)
    def test_the_preview_refuses_a_hide_from_the_front_rank_as_the_round_does(self):
        game = _battle(roster=DUO)
        sable = _sable(game)
        before = _state_without_logs(game)

        preview = preview_battle_round(game, [_hide(game)])

        assert _state_without_logs(game) == before
        assert [(r.code, dict(r.params)) for r in preview.rejections.get(sable, ())] == [
            (IN_FRONT_RANK, {"character": sable})
        ]
        assert _refused(game, _hide(game)) == [(IN_FRONT_RANK, {"character": sable})]

    @pytest.mark.xfail(reason=CHUNK)
    def test_the_preview_accepts_a_hide_and_a_back_stab_from_hiding_and_changes_nothing(self):
        game = _battle()
        before = _state_without_logs(game)
        assert not any(preview_battle_round(game, [_hide(game)]).rejections.values())
        assert _state_without_logs(game) == before

        _play(game, _hide(game))
        before = _state_without_logs(game)

        assert not any(preview_battle_round(game, [_stab(game)]).rejections.values())
        assert _state_without_logs(game) == before

    @pytest.mark.xfail(reason=CHUNK)
    def test_two_thieves_hide_in_marching_order_whatever_order_they_declare_in(self):
        game = _battle(roster=(*TRIO, ("Wren", "thief")))
        sable, wren = _sable(game), _named(game, "Wren")
        assert _encounter(game).can_hide == (sable, wren), "precondition: both thieves stand in the second rank"

        events = _play(game, _hide(game, wren), _hide(game, sable))

        rolls = [event.character_id for event in events if isinstance(event, DetectionRolledEvent)]
        attempts = [event.character_id for event in events if isinstance(event, HideAttemptedEvent)]
        assert rolls == attempts == [sable, wren]
        assert _encounter(game).hiding == (sable, wren)

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_thief_can_hide_in_the_free_round_and_back_stab_from_hiding_after_it(self):
        game = _battle(monsters_surprised=True)
        sable = _sable(game)

        hide_events = _play(game, _hide(game))
        assert game.battle.monsters_hold_rounds == 0, "precondition: the free round is over"
        events = _play(game, _stab(game))

        assert len(_rolls(hide_events, "hide_in_shadows", sable)) == 1
        assert len(_rolls(events, "move_silently", sable)) == 1
        assert len(_attacks_by(events, sable)) == 1

    @pytest.mark.xfail(reason=CHUNK)
    def test_a_battle_that_ends_in_the_round_of_the_hide_leaves_nobody_hiding(self):
        """The fighters kill the lone goblin in the round Sable hides, and the next battle starts with nobody hiding."""
        seed = SEEDS.start
        while seed < SEEDS.stop:
            game = _battle(first_seed=seed, count=1)
            seed = game.master_seed + 1
            fighters = [_named(game, name) for name in ("Brakk", "Hild")]
            events = _play(
                game, _hide(game), *(_declare(member, "attack", target_group_id=_group_id(game)) for member in fighters)
            )
            if game.battle is not None:
                continue
            assert [event.character_id for event in events if isinstance(event, HideAttemptedEvent)] == [_sable(game)]

            assert game.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=5)).accepted
            if game.mode is SessionMode.ENCOUNTER:
                assert game.execute(EngageBattle()).accepted
            assert game.mode is SessionMode.BATTLE, "precondition: a new battle"
            assert game.battle.hiding == {}
            assert _encounter(game).hiding == ()
            return
        raise AssertionError("precondition: no seed's fighters ended the battle in the round of the hide")


# ---------------------------------------------------------------------- confusion ends the attempt


class TestConfusionEndsTheAttempt:
    @pytest.mark.xfail(reason=CHUNK)
    def test_a_hiding_thief_who_is_confused_at_the_end_of_a_round_stops_hiding(self):
        game = _battle()
        sable = _sable(game)
        _play(game, _hide(game))
        assert _encounter(game).hiding == (sable,), "precondition: Sable is hiding"
        _attach(game, CONFUSION, sable)

        _play(game)

        assert _encounter(game).hiding == ()
        assert game.battle.hiding == {}


# ---------------------------------------------------------------------- the formatter


class TestTheDefaultFormatter:
    """A percentile thief roll reads as a percentage, not as a chance in six."""

    @pytest.mark.xfail(reason=CHUNK)
    @pytest.mark.parametrize("kind", ["hide_in_shadows", "move_silently", "open_locks", "treasure_traps"])
    def test_a_percentile_detection_roll_reads_as_a_percentage(self, kind):
        event = DetectionRolledEvent(character_id="character-0003", kind=kind, chance=45, roll=38, passed=True)

        assert format_message(event) == f"Detection roll ({kind}, character-0003): 38 vs 45%. Success."

    def test_a_chance_in_six_still_reads_as_one(self):
        """A guard: the goldens' transcripts carry the d6 lines."""
        event = DetectionRolledEvent(character_id="character-0001", kind="listening", chance=2, roll=1, passed=True)

        assert format_message(event) == "Detection roll (listening, character-0001): 1 vs 2-in-6. Success."
