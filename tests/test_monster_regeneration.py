"""A spawned monster with the `regeneration` ability regenerates in play (issue #167, item 13).

The troll page in the OSE SRD says: "Regeneration: 3 rounds after being damaged, start regaining 3hp per round",
"Return from death: If killed (0hp), will regenerate and fight again in 2d6 rounds", and "Fire and acid: Cannot
regenerate damage from these sources. The only way to permanently kill a troll." The thoul and vampire pages say a
damaged one gains hit points at the start of each round, "as long as it is alive".

The kernel already models all of this: [`regeneration_definition`][osrlib.core.effects.regeneration_definition]
builds the effect from the template's params, the ledger's tick heals and revives, and
[`deal_damage`][osrlib.core.combat.deal_damage] keeps fire and acid in the non-regenerable ledger. What was missing
is the session attaching the effect when it spawns such a monster. These tests drive the session through
`GameSession.execute`, `advance_rounds`, and battle rounds, and read the ledger, the monsters, and the events.

One kernel call sets up the preconditions that the command set has no direct way to stage: `deal_damage` with the
session's clock, which is the call the battle code makes, hurts or kills a monster by an exact amount or element.
Every test asserts the precondition it stages.

The engine pins one decision the SRD leaves open (`docs/adaptations.md`, the encounter entry): every effect on an
encounter's monsters releases when the encounter ends, so a troll slain as the last monster standing doesn't get
back up. A slain troll revives only while its encounter is still running.
"""

import pytest

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, Character, create_character, party_to_document
from osrlib.core.clock import TimeUnit
from osrlib.core.combat import DamageSource, deal_damage
from osrlib.core.effects import Condition, has_condition
from osrlib.core.events import (
    DamageDealtEvent,
    EffectAttachedEvent,
    EffectReleasedEvent,
    HealingAppliedEvent,
    HitPointsReportedEvent,
    MonsterRevivedEvent,
)
from osrlib.core.monsters import MonsterAbility, MonsterInstance
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import EncounterTable, EncounterTableRow, MonsterEncounterEntry
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    AdvanceTime,
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    MoveParty,
    ResolveBattleRound,
    Rest,
    SessionMode,
    SpawnMonsters,
)
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
from osrlib.crawl.events import EncounterEndedEvent, MonsterDefeatedEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_monsters
from osrlib.persistence import load_game, replay_game, save_game

XFAIL = pytest.mark.xfail(reason="chunk: monster-regeneration", strict=True)

SEED = 7
"""The master seed of every session here. Each test asserts the state it depends on, so a seed that stops
producing that state fails the test loudly rather than passing it."""

REGENERATING_IDS = tuple(
    sorted(template.id for template in load_monsters().monsters if template.ability("regeneration") is not None)
)
"""Every catalog template with a `regeneration` ability, read from the data rather than listed by hand."""


# ---------------------------------------------------------------------- helpers


def _hero() -> Character:
    """A first-level fighter with enough hit points to outlast any fight in this file."""
    stream = RngStreams(master_seed=SEED).get(CHARACTER_CREATION_STREAM)
    hero = create_character(
        name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
    ).character
    return hero.model_copy(update={"max_hp": 9999, "current_hp": 9999})


def _corridor(areas: tuple[AreaSpec, ...] = (), **fields) -> LevelSpec:
    """A one-row level 1 of two cells joined by an open edge, entered at (0, 0)."""
    return LevelSpec(
        number=1,
        width=2,
        height=1,
        entrance=(0, 0),
        edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
        areas=areas,
        **fields,
    )


def _adventure(level: LevelSpec | None = None, **fields) -> Adventure:
    crypt = DungeonSpec(
        id="crypt", name="Crypt", levels=(level or _corridor(wandering=WanderingSpec(chance_in_six=0)),)
    )
    town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
    return Adventure(name="A", town=town, dungeons=(crypt,), **fields)


def _party() -> Party:
    return Party(members=[_hero()])


def _session(adventure: Adventure | None = None, seed: int = SEED) -> GameSession:
    """A session standing at the dungeon entrance."""
    game = GameSession.new(_party(), adventure or _adventure(), seed=seed)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def _spawn(game: GameSession, template_id: str, count: int, distance_feet: int):
    result = game.execute(SpawnMonsters(template_id=template_id, count_fixed=count, distance_feet=distance_feet))
    assert result.accepted, result.rejections
    monsters = [monster for monster in game.monsters.values() if monster.template.id == template_id]
    assert len(monsters) == count
    return result, sorted(monsters, key=lambda monster: monster.id)


def _to_battle(game: GameSession) -> None:
    if game.mode is SessionMode.ENCOUNTER:
        assert game.execute(EngageBattle()).accepted
    assert game.mode is SessionMode.BATTLE


def _round(game: GameSession, action: str = "hold"):
    """Resolve one battle round in which the party's only member holds or attacks the first group."""
    hero = game.party.members[0]
    declaration = BattleDeclaration(character_id=hero.id, action="hold")
    if action == "attack":
        declaration = BattleDeclaration(
            character_id=hero.id, action="attack", target_group_id=game.encounter.groups[0].id
        )
    result = game.execute(ResolveBattleRound(declarations=(declaration,)))
    assert result.accepted, result.rejections
    return result


def _advance(game: GameSession, rounds: int = 1):
    result = game.execute(AdvanceTime(n=rounds, unit=TimeUnit.ROUND))
    assert result.accepted
    return result


def _regeneration(game: GameSession, monster: MonsterInstance):
    return game.ledger.active_on(monster.id, "regeneration")


def _heals(events, monster: MonsterInstance) -> list[int]:
    """The hit points each regeneration tick in `events` gave `monster`, in order."""
    healed = [event for event in events if isinstance(event, HealingAppliedEvent) and event.target_id == monster.id]
    assert all(event.source == "regeneration" for event in healed)
    return [event.amount for event in healed]


def _revivals(events, monster: MonsterInstance) -> list[MonsterRevivedEvent]:
    return [event for event in events if isinstance(event, MonsterRevivedEvent) and event.target_id == monster.id]


def _params(monster: MonsterInstance) -> dict:
    ability = monster.template.ability("regeneration")
    assert ability is not None
    return dict(ability.params)


def _wound(game: GameSession, monster: MonsterInstance, amount: int, element: str | None = None) -> list:
    """Hurt a monster the way the battle code does, through `deal_damage` with the session's clock."""
    source = DamageSource(kind="weapon") if element is None else DamageSource(kind="spell", element=element)
    before = monster.current_hp
    events = deal_damage(monster, amount, source=source, clock=game.clock)
    assert monster.current_hp == max(0, before - amount)
    assert monster.last_damaged_round == game.clock.rounds
    return events


def _assert_attached(game: GameSession, events, monsters) -> None:
    """Each monster carries one regeneration effect built from its template, and `events` reports the attach."""
    attached = [event for event in events if isinstance(event, EffectAttachedEvent) and event.kind == "regeneration"]
    assert sorted(event.target_ref for event in attached) == sorted(monster.id for monster in monsters)
    for monster in monsters:
        effects = _regeneration(game, monster)
        assert len(effects) == 1
        effect = effects[0]
        assert effect.definition.tick == "regeneration"
        assert dict(effect.definition.params) == _params(monster)
        assert [event.effect_id for event in attached if event.target_ref == monster.id] == [effect.effect_id]


# ---------------------------------------------------------------------- attaching at spawn


def test_the_catalog_has_regenerating_monsters():
    """Guards the parametrization below against an empty or shrunken catalog query."""
    assert {"troll", "thoul"} <= set(REGENERATING_IDS)
    troll = load_monsters().get("troll").ability("regeneration")
    assert dict(troll.params) == {"blocked_by": ("fire", "acid"), "delay_rounds": 3, "per_round": 3, "revive": "2d6"}


@XFAIL
@pytest.mark.parametrize("template_id", REGENERATING_IDS)
def test_spawn_monsters_attaches_each_regenerating_template(template_id):
    game = _session()
    result, monsters = _spawn(game, template_id, count=2, distance_feet=60)
    _assert_attached(game, result.events, monsters)


@XFAIL
def test_a_keyed_encounter_attaches_regeneration_to_its_troll_only():
    den = AreaSpec(
        id="den",
        name="Den",
        cells=((1, 0),),
        encounter=KeyedEncounter(
            monsters=(KeyedMonster(template_id="troll", count_fixed=1), KeyedMonster(template_id="ogre", count_fixed=1))
        ),
    )
    game = _session(_adventure(_corridor(areas=(den,), wandering=WanderingSpec(chance_in_six=0))))
    assert not game.monsters
    result = game.execute(MoveParty(direction=Direction.EAST))
    assert result.accepted
    assert game.encounter is not None
    by_template = {monster.template.id: monster for monster in game.monsters.values()}
    assert set(by_template) == {"troll", "ogre"}
    _assert_attached(game, result.events, [by_template["troll"]])
    assert game.ledger.active_on(by_template["ogre"].id, "regeneration") == []


@XFAIL
def test_a_wandering_encounter_attaches_regeneration():
    row = {"name": "Troll", "entry": MonsterEncounterEntry(monster_ids=("troll",)), "count_fixed": 1}
    table = EncounterTable(
        id="trolls_only",
        label="Trolls only",
        min_level=1,
        max_level=None,
        rows=tuple(EncounterTableRow(roll=roll, **row) for roll in range(1, 21)),
    )
    game = _session(_adventure(_corridor(wandering=WanderingSpec(chance_in_six=6, interval_turns=1, table=table))))
    result = None
    for _ in range(20):
        result = game.execute(Rest(kind="turn"))
        assert result.accepted
        if game.encounter is not None:
            break
    assert game.encounter is not None, "no wandering encounter in twenty turns of rest"
    trolls = list(game.monsters.values())
    assert [troll.template.id for troll in trolls] == ["troll"]
    _assert_attached(game, result.events, trolls)


@XFAIL
def test_an_adventure_bundled_template_with_the_ability_regenerates():
    """The session reads the ability off whatever template it spawns, not a list of catalog ids."""
    ogre = load_monsters().get("ogre")
    ability = MonsterAbility(
        tag="regeneration",
        name="Regeneration",
        prose="Gains 2hp each round, one round after being hurt, while alive.",
        params={"delay_rounds": 1, "per_round": 2, "while_alive": True},
    )
    mire_ogre = ogre.model_copy(
        update={"id": "mire_ogre", "name": "Mire Ogre", "abilities": (*ogre.abilities, ability)}
    )
    game = _session(_adventure(monsters=(mire_ogre,)))
    result, (monster,) = _spawn(game, "mire_ogre", count=1, distance_feet=60)
    _assert_attached(game, result.events, [monster])
    _wound(game, monster, 4)
    damaged_at = monster.last_damaged_round
    assert _heals(_advance(game).events, monster) == [2]
    assert game.clock.rounds == damaged_at + 1
    assert _heals(_advance(game).events, monster) == [2]
    assert monster.current_hp == monster.max_hp


def test_a_monster_without_the_ability_never_regenerates():
    game = _session()
    _, (ogre,) = _spawn(game, "ogre", count=1, distance_feet=60)
    assert ogre.template.ability("regeneration") is None
    _wound(game, ogre, 5)
    hurt = ogre.current_hp
    assert 0 < hurt < ogre.max_hp
    events = [*_advance(game, 30).events, *game.advance_rounds(30)]
    assert _heals(events, ogre) == []
    assert ogre.current_hp == hurt
    assert _regeneration(game, ogre) == []


# ---------------------------------------------------------------------- healing as time passes


def _expected_gain(monster: MonsterInstance, now: int, damaged_at: int, hp: int, ceiling: int) -> int:
    """What the regeneration tick at clock round `now` should give back: nothing inside the delay, then
    `per_round`, never past `ceiling`."""
    params = _params(monster)
    if now < damaged_at + int(params["delay_rounds"]):
        return 0
    return min(int(params["per_round"]), ceiling - hp)


@XFAIL
def test_a_troll_heals_per_round_after_the_delay_as_time_advances_outside_battle():
    game = _session()
    _, (troll,) = _spawn(game, "troll", count=1, distance_feet=60)
    assert game.mode is SessionMode.ENCOUNTER
    _wound(game, troll, 10)
    assert troll.current_hp == troll.max_hp - 10 > 0
    assert not has_condition(troll, Condition.DEAD)
    damaged_at = troll.last_damaged_round
    hp = troll.current_hp
    gains = []
    for _ in range(12):
        result = _advance(game)
        gain = _expected_gain(troll, game.clock.rounds, damaged_at, hp, troll.max_hp)
        assert _heals(result.events, troll) == ([gain] if gain else [])
        hp += gain
        gains.append(gain)
        assert troll.current_hp == hp <= troll.max_hp
    assert gains[:2] == [0, 0]  # the two rounds inside the three-round delay
    assert gains[2] == 3
    assert troll.current_hp == troll.max_hp
    assert gains[-1] == 0  # already whole, so no tick heals it past its maximum


@XFAIL
def test_advance_rounds_heals_a_troll_and_reports_it():
    game = _session()
    _, (troll,) = _spawn(game, "troll", count=1, distance_feet=60)
    _wound(game, troll, 10)
    hurt = troll.current_hp
    assert 0 < hurt
    assert _heals(game.advance_rounds(2), troll) == []
    assert troll.current_hp == hurt
    events = game.advance_rounds(1)
    assert _heals(events, troll) == [3]
    reported = [event for event in events if isinstance(event, HitPointsReportedEvent) and event.target_id == troll.id]
    assert reported[-1].current_hp == hurt + 3 == troll.current_hp
    events = game.advance_rounds(20)
    assert sum(_heals(events, troll)) == 7
    assert troll.current_hp == troll.max_hp


@XFAIL
def test_fire_and_acid_damage_never_regenerates():
    game = _session()
    _, (troll,) = _spawn(game, "troll", count=1, distance_feet=60)
    assert set(_params(troll)["blocked_by"]) == {"fire", "acid"}
    events = _wound(game, troll, 4) + _wound(game, troll, 3, "fire") + _wound(game, troll, 2, "acid")
    dealt = [event for event in events if isinstance(event, DamageDealtEvent)]
    assert [event.non_regenerable for event in dealt] == [False, True, True]
    assert troll.nonregen_damage == 5
    assert troll.current_hp == troll.max_hp - 9 > 0
    healed = _heals(_advance(game, 40).events, troll)
    assert sum(healed) == 4  # only the ordinary damage comes back
    assert troll.current_hp == troll.max_hp - 5


@XFAIL
def test_a_thoul_heals_from_the_first_round_while_alive_and_stays_dead():
    game = _session()
    _, (hurt, slain) = _spawn(game, "thoul", count=2, distance_feet=60)
    params = _params(hurt)
    assert params["delay_rounds"] == 0 and params["per_round"] == 1 and params["while_alive"] is True
    _wound(game, hurt, 2)
    assert hurt.current_hp == hurt.max_hp - 2 > 0
    _wound(game, slain, slain.max_hp)
    assert has_condition(slain, Condition.DEAD)
    assert _heals(_advance(game).events, hurt) == [1]
    events = _advance(game, 20).events
    assert _heals(events, hurt) == [1]
    assert hurt.current_hp == hurt.max_hp
    assert _heals(events, slain) == [] and _revivals(events, slain) == []
    assert slain.current_hp == 0
    assert has_condition(slain, Condition.DEAD)


# ---------------------------------------------------------------------- healing in battle


@XFAIL
def test_a_troll_hurt_in_battle_heals_after_the_delay_while_the_party_holds():
    game = _session()
    _, (troll,) = _spawn(game, "troll", count=1, distance_feet=5)
    _to_battle(game)
    for _ in range(20):
        _round(game, "attack")
        if troll.current_hp < troll.max_hp:
            break
    assert 0 < troll.current_hp < troll.max_hp, "the party's attacks never hurt the troll"
    damaged_at = troll.last_damaged_round
    assert damaged_at is not None
    hp = troll.current_hp
    gains = []
    for _ in range(25):
        result = _round(game)
        assert game.mode is SessionMode.BATTLE
        assert troll.last_damaged_round == damaged_at  # nothing else hurt it
        gain = _expected_gain(troll, game.clock.rounds, damaged_at, hp, troll.max_hp)
        assert _heals(result.events, troll) == ([gain] if gain else [])
        hp += gain
        gains.append(gain)
        assert troll.current_hp == hp <= troll.max_hp
    assert 3 in gains
    assert troll.current_hp == troll.max_hp
    assert gains[-1] == 0


# ---------------------------------------------------------------------- death and revival


def _three_trolls(seed: int = SEED):
    """Three trolls in battle: one burned to death, one cut down, one standing to keep the battle going."""
    game = _session(seed=seed)
    _, (burned, slain, standing) = _spawn(game, "troll", count=3, distance_feet=5)
    _to_battle(game)
    fire = _wound(game, burned, burned.max_hp, "fire")
    assert "combat.death.permanent" in [event.code for event in fire]
    blow = _wound(game, slain, slain.max_hp)
    assert "combat.death.died" in [event.code for event in blow]
    assert has_condition(burned, Condition.DEAD) and has_condition(slain, Condition.DEAD)
    assert not has_condition(standing, Condition.DEAD)
    return game, burned, slain, standing


@XFAIL
def test_a_slain_troll_revives_in_2d6_rounds_and_fights_again_unless_burned():
    game, burned, slain, standing = _three_trolls()
    killed_at = slain.last_damaged_round
    revived_at = None
    fought_again = False
    for _ in range(40):
        result = _round(game)
        assert game.mode is SessionMode.BATTLE, "the battle ended before the troll could revive"
        assert _revivals(result.events, burned) == []
        assert burned.current_hp == 0 and has_condition(burned, Condition.DEAD)
        revivals = _revivals(result.events, slain)
        if revivals:
            assert revived_at is None, "the troll revived twice"
            revived_at = game.clock.rounds
            assert len(revivals) == 1
            assert slain.current_hp == 1
            assert not has_condition(slain, Condition.DEAD)
        elif revived_at is not None:
            fought_again = fought_again or any(
                getattr(event, "attacker_id", None) == slain.id and event.code.startswith("combat.attack.")
                for event in result.events
            )
        if revived_at is not None and fought_again and slain.current_hp == slain.max_hp:
            break
    assert revived_at is not None, "the slain troll never revived"
    assert killed_at + 2 <= revived_at <= killed_at + 12
    assert fought_again
    assert slain.current_hp == slain.max_hp
    assert not has_condition(standing, Condition.DEAD)


@XFAIL
def test_a_troll_slain_last_ends_the_battle_and_its_revival_is_released():
    game = _session()
    _, (troll,) = _spawn(game, "troll", count=1, distance_feet=5)
    _to_battle(game)
    effects = _regeneration(game, troll)
    assert len(effects) == 1, "the troll carries no regeneration effect"
    effect = effects[0]
    _wound(game, troll, troll.max_hp)
    assert has_condition(troll, Condition.DEAD)
    result = _round(game)
    ended = [event for event in result.events if isinstance(event, EncounterEndedEvent)]
    assert [event.outcome for event in ended] == ["victory"]
    defeated = [event for event in result.events if isinstance(event, MonsterDefeatedEvent)]
    assert [(event.monster_id, event.outcome) for event in defeated] == [(troll.id, "slain")]
    released = [event for event in result.events if isinstance(event, EffectReleasedEvent)]
    assert effect.effect_id in [event.effect_id for event in released]
    assert game.mode is SessionMode.EXPLORING
    assert _regeneration(game, troll) == []
    events = result.events + _advance(game, 30).events
    assert _revivals(events, troll) == []
    assert troll.current_hp == 0 and has_condition(troll, Condition.DEAD)


# ---------------------------------------------------------------------- determinism


def _dump(events) -> list:
    return [event if isinstance(event, dict) else event.model_dump(mode="json") for event in events]


def _revival_battle(seed: int) -> list:
    game, burned, slain, _ = _three_trolls(seed)
    for _ in range(20):
        _round(game)
    _advance(game, 5)
    return _dump(game.event_log)


@XFAIL
def test_the_same_seed_and_commands_give_the_same_regeneration_and_revival_events():
    first = _revival_battle(SEED)
    assert any(event["code"] == "effects.regeneration.revived" for event in first)
    assert any(event["code"] == "combat.healing.applied" and event["source"] == "regeneration" for event in first)
    assert _revival_battle(SEED) == first


def _commanded_battle() -> GameSession:
    """A troll fight run only by commands: attack until the troll is hurt, then hold while it heals."""
    game = _session()
    _, (troll,) = _spawn(game, "troll", count=1, distance_feet=5)
    _to_battle(game)
    for _ in range(20):
        _round(game, "attack")
        if troll.current_hp < troll.max_hp:
            break
    assert troll.current_hp < troll.max_hp
    for _ in range(12):
        _round(game)
    return game


@XFAIL
def test_executing_and_replaying_the_command_log_give_the_same_events():
    game = _commanded_battle()
    events = _dump(game.event_log)
    assert any(event["code"] == "combat.healing.applied" and event["source"] == "regeneration" for event in events)
    again = GameSession.new(_party(), _adventure(), seed=SEED)
    for command in game.command_log:
        assert again.execute(command).accepted
    assert _dump(again.event_log) == events
    replayed = replay_game(SEED, party_to_document(_party().members), _adventure(), Ruleset(), game.command_log)
    assert _dump(replayed.event_log) == events


@XFAIL
def test_a_saved_and_loaded_session_keeps_regenerating_the_same_way():
    game = _session()
    _, (troll,) = _spawn(game, "troll", count=1, distance_feet=60)
    _wound(game, troll, 10)
    loaded = load_game(save_game(game))
    loaded_troll = loaded.combatant(troll.id)
    assert loaded_troll.current_hp == troll.current_hp
    assert len(loaded.ledger.active_on(troll.id, "regeneration")) == 1
    original = _dump(_advance(game, 10).events)
    restored = _dump(_advance(loaded, 10).events)
    assert any(event["code"] == "combat.healing.applied" for event in original)
    assert restored == original
    assert loaded_troll.current_hp == troll.current_hp == troll.max_hp
