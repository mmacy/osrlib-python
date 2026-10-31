"""A turning with nothing left to turn, a staff of striking swung from the back rank, and two drain event fields.

Three findings from a playtest of a game built on osrlib, pinned here before the code.

**A turning reaches only the monsters still in the fight.** A battle `turn_undead` names no group. It reaches every
monster in the encounter, undead or not, and a monster that isn't undead comes out `unaffected`. That is why a
turning declared against a medusa is accepted (see `test_avert_eyes.py`). The turning resolves in the magic phase,
after the party's missiles, so the missiles can kill what it was meant for. A turning reaches only the living
monsters of the groups that haven't fled. A skeleton shot dead in the missile phase isn't read on the turning table,
because the SRD says "At least one undead monster will always be affected on a successful turning".
The magic phase's re-check refuses a turning with none left, with the code the other re-checks use for a missing
target, `battle.declaration.no_target`, and the turning fizzles at `magic.turning.fizzled`. It draws nothing and
costs what a `hold` costs. The check asks whether any monster is left, not whether any undead is left, so it never
tells the player for free what is undead. When a dead or fled undead leaves living monsters of other kinds behind,
the turning resolves against those, as a turning declared against them alone would.

**The staff of striking is a melee weapon.** The SRD's entry reads "Used as a magical melee weapon", and its
"Staves in Melee" note says magic-users "are able to use the staff of power, staff of striking, and staff of
wizardry in melee". So a staff of striking use needs the member in the front rank, as a melee attack does, and is
refused with `battle.declaration.not_in_front_rank` otherwise. At acceptance the rank is the one the round's swaps
will leave, so a replacement who steps up may strike, and in the magic phase's re-check it is the rank as it stands.
A strike that misses still spends its charge, because "Only one charge may be spent per strike".

**The drain event's `hp_lost` and `spawn_consequence`.** Maximum hit points stop at 1 while a level is left, so the
hit points rolled for a lost level can exceed what the maximum actually lost. `LevelDrainedEvent.hp_lost` is the
drop in maximum hit points. `spawn_consequence` describes what becomes of a character the undead kills ("A person
drained of all levels becomes a wight in 1d4 days"), so it is set only on a drain that kills.
"""

from collections.abc import Callable

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character
from osrlib.core.classes import drain_levels
from osrlib.core.combat import drain_monster_hd, incapacitated, resolve_energy_drain
from osrlib.core.effects import ActiveCondition, Condition, has_condition
from osrlib.core.events import (
    AttackRolledEvent,
    DamageDealtEvent,
    DeathEvent,
    DeclarationFizzledEvent,
    Event,
    LevelDrainedEvent,
    SpellDisruptedEvent,
    UndeadTurnedEvent,
    Visibility,
)
from osrlib.core.items import MagicItemInstance
from osrlib.core.monsters import spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MAGIC_STREAM
from osrlib.core.tables import ReactionResult
from osrlib.crawl import battle as battle_module
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.battle import MELEE_RANGE_FEET
from osrlib.crawl.commands import (
    BattleDeclaration,
    CommandResult,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    GrantItem,
    LightSource,
    ReorderParty,
    ResolveBattleRound,
    SessionMode,
    TurnUndead,
    UseItem,
)
from osrlib.crawl.events import MemberWithdrewEvent
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_monsters
from osrlib.messages import format_message
from test_phase2_goldens import run_wight_battle

FIGHTER = "character-0001"
THIEF = "character-0002"
CLERIC = "character-0003"
MAGIC_USER = "character-0004"

CLERIC_LEADS = (CLERIC, FIGHTER, THIEF, MAGIC_USER)
"""A marching order that puts the cleric in the front rank of the stock party."""

SCAN_SEEDS = range(16)
"""Seeds to scan for a case that depends on the dice. Each scanning test asserts it found what it needs."""

NO_TARGET = "battle.declaration.no_target"
NOT_IN_FRONT_RANK = "battle.declaration.not_in_front_rank"


# ---------------------------------------------------------------------- battle helpers


def _light_and_enter(session: GameSession) -> None:
    session.execute(GrantItem(character_id=FIGHTER, item_id="torch", quantity=6))
    session.execute(GrantItem(character_id=FIGHTER, item_id="tinder_box"))
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
    for _ in range(20):
        lit = session.execute(LightSource(character_id=FIGHTER, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            return
    pytest.fail("the torch never lit")


def battle(
    seed: int,
    groups: list[tuple[str, int]],
    *,
    distance: int,
    order: tuple[str, ...] | None = None,
    archers: tuple[str, ...] = (),
    ruleset: Ruleset | None = None,
) -> GameSession:
    """A battle of the stock party against `groups`, all at `distance`, in the one-cell entrance corridor.

    The corridor fits two abreast, so the stock party's front rank is the fighter and the thief unless `order`
    sets another marching order. Each of `archers` equips a short bow and carries arrows.
    """
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed, ruleset=ruleset)
    for member_id in archers:
        assert session.execute(GrantItem(character_id=member_id, item_id="short_bow")).accepted
        assert session.execute(GrantItem(character_id=member_id, item_id="arrows", quantity=20)).accepted
        assert session.execute(EquipItem(character_id=member_id, item_id="short_bow")).accepted
    _light_and_enter(session)
    if order is not None:
        assert session.execute(ReorderParty(order=order)).accepted
    spawned = [(template_id, session.spawn(template_id, count)) for template_id, count in groups]
    encounter_module.start_encounter(
        session,
        groups=spawned,
        kind="spawned",
        distance_feet=distance,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    session.execute(EngageBattle())
    assert session.mode is SessionMode.BATTLE
    return session


def declarations(session: GameSession, *extra: BattleDeclaration) -> tuple[BattleDeclaration, ...]:
    """The round's declarations: `extra` first, in the order given, then a `hold` for every other declarer.

    The party's magic phase resolves in declaration order, so a declaration listed before the turning resolves
    before it.
    """
    named = {declaration.character_id for declaration in extra}
    holds = tuple(
        BattleDeclaration(character_id=member.id, action="hold")
        for member in session.party.living_members()
        if not incapacitated(member) and member.id not in named
    )
    return (*extra, *holds)


def front_rank(session: GameSession) -> tuple[str, ...]:
    return session.view(Visibility.PLAYER).encounter.front_rank


def outcome(session: GameSession, result: CommandResult, *, drop: Callable[[Event], bool] = lambda _event: False):
    """Everything a round produced or changed that a player or a replay could tell apart."""
    return {
        "accepted": result.accepted,
        "rejections": [rejection.model_dump(mode="json") for rejection in result.rejections],
        "events": [event.model_dump(mode="json") for event in result.events if not drop(event)],
        "streams": {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()},
        "round": session.battle.round if session.battle is not None else None,
        "party": [member.model_dump(mode="json") for member in session.party.members],
        "ledger": session.ledger.model_dump(mode="json"),
    }


def stream_states(session: GameSession):
    return {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()}


def fizzles(events, actor_id: str, code: str) -> list[DeclarationFizzledEvent]:
    """The actor's `DeclarationFizzledEvent`s at `code`. A device or turning fizzle is never a spell event."""
    assert not any(isinstance(event, SpellDisruptedEvent) and event.caster_id == actor_id for event in events), (
        "a device use or turning reported as a lost spell"
    )
    return [
        event
        for event in events
        if isinstance(event, DeclarationFizzledEvent) and event.code == code and event.actor_id == actor_id
    ]


def is_fizzle(event: Event) -> bool:
    return isinstance(event, DeclarationFizzledEvent)


def party_acted_first(session: GameSession, result: CommandResult) -> bool:
    initiative = next(event for event in result.events if event.code == "combat.initiative.rolled")
    return initiative.order[0] in {member.id for member in session.party.members}


def death_index(events, monster_id: str) -> int | None:
    for index, event in enumerate(events):
        if isinstance(event, DeathEvent) and event.target_id == monster_id:
            return index
    return None


def turning_index(events) -> int | None:
    """Where the turning resolved or fizzled: the first event with a `magic.turning.` code."""
    for index, event in enumerate(events):
        if event.code.startswith("magic.turning."):
            return index
    return None


def turnings(events) -> list[UndeadTurnedEvent]:
    return [event for event in events if isinstance(event, UndeadTurnedEvent)]


def assert_turning_fizzled_for_want_of_a_target(events) -> None:
    found = fizzles(events, CLERIC, "magic.turning.fizzled")
    assert len(found) == 1, [event.code for event in events]
    fizzle = found[0]
    assert fizzle.event_type == "declaration_fizzled"
    assert fizzle.actor_id == CLERIC
    assert fizzle.item_id is None
    assert fizzle.reason == NO_TARGET
    assert fizzle.visibility is Visibility.PLAYER
    message = format_message(fizzle)
    assert message and message != fizzle.code
    assert not turnings(events), "a turning with nothing to turn still resolved"


TURN = BattleDeclaration(character_id=CLERIC, action="turn_undead")


# ---------------------------------------------------------------------- the turning: scenarios


def skeleton_under_fire(seed: int) -> tuple[GameSession, str]:
    """One skeleton at 40 feet, wounded to 1 hit point, and the fighter and the thief with short bows.

    Returns the session and the skeleton's id.
    """
    session = battle(seed, [("skeleton", 1)], distance=40, archers=(FIGHTER, THIEF))
    skeleton_id = session.encounter.groups[0].monster_ids[0]
    session.combatant(skeleton_id).current_hp = 1
    return session, skeleton_id


def shoot(session: GameSession, member_id: str, group_index: int = 0) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=member_id,
        action="attack",
        target_group_id=session.encounter.groups[group_index].id,
        weapon_id="short_bow",
    )


def give_wand(session: GameSession) -> MagicItemInstance:
    wand = MagicItemInstance(instance_id="magic-item-9001", template_id="wand_of_cold", charges_remaining=5)
    session.member(MAGIC_USER).inventory.items.append(wand)
    return wand


def wand_at(session: GameSession, wand: MagicItemInstance, group_index: int) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=MAGIC_USER,
        action="use_item",
        item_id=wand.instance_id,
        target_group_id=session.encounter.groups[group_index].id,
    )


def lone_skeleton_under_the_wand(seed: int) -> tuple[GameSession, str, MagicItemInstance]:
    """One skeleton at 40 feet with 1 hit point, and a wand of cold that the magic-user fires before the turning.

    Cold damage of 6d6, halved on a save, always kills it, so the wand kills it earlier in the same magic phase
    whatever the dice say.
    """
    session = battle(seed, [("skeleton", 1)], distance=40)
    skeleton_id = session.encounter.groups[0].monster_ids[0]
    session.combatant(skeleton_id).current_hp = 1
    return session, skeleton_id, give_wand(session)


# ---------------------------------------------------------------------- the turning: tests


class TestATurningWithNothingLeftToTurnFizzles:
    """A turning whose every monster died or left the battle earlier in the round fizzles in the magic phase."""

    def test_a_skeleton_shot_dead_in_the_missile_phase_leaves_nothing_to_turn(self):
        shot_dead, standing = [], []
        for seed in SCAN_SEEDS:
            session, skeleton_id = skeleton_under_fire(seed)
            result = session.execute(
                ResolveBattleRound(
                    declarations=declarations(session, shoot(session, FIGHTER), shoot(session, THIEF), TURN)
                )
            )
            assert result.accepted, [rejection.code for rejection in result.rejections]
            died_at = death_index(result.events, skeleton_id)
            turned_at = turning_index(result.events)
            assert turned_at is not None, "the turning neither resolved nor fizzled"
            if died_at is not None and died_at < turned_at:
                assert_turning_fizzled_for_want_of_a_target(result.events)
                shot_dead.append(seed)
            else:
                assert len(turnings(result.events)) == 1, "a turning with a living skeleton to face resolves"
                standing.append(seed)
        assert shot_dead, "no seed let the missiles kill the skeleton first"
        assert standing, "no seed left the skeleton standing for the turning"

    def test_a_skeleton_killed_earlier_in_the_magic_phase_leaves_nothing_to_turn(self):
        session, skeleton_id, wand = lone_skeleton_under_the_wand(seed=0)
        result = session.execute(
            ResolveBattleRound(declarations=declarations(session, wand_at(session, wand, 0), TURN))
        )
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert wand.charges_remaining == 4, "precondition: the wand went off"
        died_at = death_index(result.events, skeleton_id)
        assert died_at is not None, "precondition: the wand killed the skeleton"
        assert died_at < turning_index(result.events)
        assert_turning_fizzled_for_want_of_a_target(result.events)

    def test_a_turning_with_nothing_to_turn_costs_what_a_hold_costs(self):
        turning, skeleton_id, wand = lone_skeleton_under_the_wand(seed=0)
        turned = turning.execute(
            ResolveBattleRound(declarations=declarations(turning, wand_at(turning, wand, 0), TURN))
        )
        assert turned.accepted
        assert len(fizzles(turned.events, CLERIC, "magic.turning.fizzled")) == 1

        holding, _, held_wand = lone_skeleton_under_the_wand(seed=0)
        hold = BattleDeclaration(character_id=CLERIC, action="hold")
        # The cleric's hold sits where the turning did, because the initiative event lists the party in
        # declaration order.
        held = holding.execute(
            ResolveBattleRound(declarations=declarations(holding, wand_at(holding, held_wand, 0), hold))
        )
        assert held.accepted
        assert has_condition(holding.combatant(skeleton_id), Condition.DEAD), "precondition: the wand killed it"
        assert turning.streams.get(MAGIC_STREAM).export_state() == holding.streams.get(MAGIC_STREAM).export_state(), (
            "the fizzled turning drew from the magic stream"
        )
        assert outcome(turning, turned, drop=is_fizzle) == outcome(holding, held)

    def test_the_same_round_plays_out_the_same_twice(self):
        def play():
            session, _, wand = lone_skeleton_under_the_wand(seed=0)
            result = session.execute(
                ResolveBattleRound(declarations=declarations(session, wand_at(session, wand, 0), TURN))
            )
            return outcome(session, result)

        assert play() == play()


class TestATurningReachesOnlyMonstersStillInTheFight:
    """A dead monster, or one whose group fled the battle, is never a candidate for a turning, even when living
    monsters remain for it to reach."""

    def test_a_kind_whose_last_monster_died_this_round_is_not_read_on_the_table(self):
        for seed in range(4):
            session = battle(seed, [("zombie", 1), ("skeleton", 2)], distance=40)
            zombies, skeletons = session.encounter.groups
            zombie_id = zombies.monster_ids[0]
            session.combatant(zombie_id).current_hp = 1
            wand = give_wand(session)
            result = session.execute(
                ResolveBattleRound(declarations=declarations(session, wand_at(session, wand, 0), TURN))
            )
            assert result.accepted, [rejection.code for rejection in result.rejections]
            died_at = death_index(result.events, zombie_id)
            assert died_at is not None and died_at < turning_index(result.events), "precondition: the zombie died first"
            assert all(not has_condition(session.combatant(mid), Condition.DEAD) for mid in skeletons.monster_ids), (
                "precondition: the skeletons were still standing"
            )
            (turned,) = turnings(result.events)
            assert [kind.template_id for kind in turned.types] == ["skeleton"], f"seed {seed}"
            assert zombie_id not in turned.affected_ids
            assert set(turned.affected_ids) <= set(skeletons.monster_ids)

    def test_a_dead_monster_beside_living_ones_of_its_kind_is_never_affected(self):
        for seed in range(4):
            session = battle(seed, [("skeleton", 1), ("skeleton", 2)], distance=40)
            fallen, standing = session.encounter.groups
            fallen_id = fallen.monster_ids[0]
            session.combatant(fallen_id).current_hp = 1
            wand = give_wand(session)
            result = session.execute(
                ResolveBattleRound(declarations=declarations(session, wand_at(session, wand, 0), TURN))
            )
            assert result.accepted
            assert death_index(result.events, fallen_id) is not None, "precondition: the wand killed one skeleton"
            (turned,) = turnings(result.events)
            assert [kind.template_id for kind in turned.types] == ["skeleton"]
            assert fallen_id not in turned.affected_ids
            assert set(turned.affected_ids) <= set(standing.monster_ids)

    def test_a_group_that_fled_before_the_magic_phase_is_not_turned(self):
        fled_first, still_here = [], []
        for seed in SCAN_SEEDS:
            session = battle(seed, [("skeleton", 2), ("goblin", 2)], distance=40)
            skeletons = session.encounter.groups[0]
            skeletons.distance_feet = 100
            skeletons.fleeing = True
            result = session.execute(ResolveBattleRound(declarations=declarations(session, TURN)))
            assert result.accepted, [rejection.code for rejection in result.rejections]
            assert session.mode is SessionMode.BATTLE, "precondition: the goblins keep the battle going"
            (turned,) = turnings(result.events)
            kinds = [kind.template_id for kind in turned.types]
            if not party_acted_first(session, result):
                assert skeletons.fled, "precondition: the skeletons left the battle before the party acted"
                assert kinds == ["goblin"], f"seed {seed}"
                assert turned.code == "magic.turning.failed"
                assert turned.affected_ids == ()
                assert not any(has_condition(session.combatant(mid), Condition.TURNED) for mid in skeletons.monster_ids)
                fled_first.append(seed)
            else:
                assert kinds == ["skeleton", "goblin"], f"seed {seed}"
                still_here.append(seed)
        assert fled_first, "no seed let the skeletons flee before the party acted"
        assert still_here, "no seed let the party act before the skeletons fled"

    def test_a_turning_in_a_fight_with_no_undead_is_accepted_and_resolves(self):
        session = battle(0, [("goblin", 2)], distance=40)
        result = session.execute(ResolveBattleRound(declarations=declarations(session, TURN)))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert not fizzles(result.events, CLERIC, "magic.turning.fizzled")
        (turned,) = turnings(result.events)
        assert turned.code == "magic.turning.failed"
        assert [(kind.template_id, kind.outcome) for kind in turned.types] == [("goblin", "unaffected")]


# ---------------------------------------------------------------------- the staff of striking


def staff_battle(seed: int, *, order: tuple[str, ...] | None = None, ruleset: Ruleset | None = None):
    """Two goblins at melee range, and a cleric carrying an unidentified staff of striking with 5 charges."""
    session = battle(seed, [("goblin", 2)], distance=MELEE_RANGE_FEET, order=order, ruleset=ruleset)
    staff = MagicItemInstance(instance_id="magic-item-9003", template_id="staff_of_striking", charges_remaining=5)
    session.member(CLERIC).inventory.items.append(staff)
    return session, session.encounter.groups[0], staff


def strike(member_id: str, group, staff: MagicItemInstance) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=member_id, action="use_item", item_id=staff.instance_id, target_group_id=group.id
    )


def activations(events, instance_id: str) -> list[Event]:
    return [
        event
        for event in events
        if event.code == "items.device.activated" and getattr(event, "instance_id", None) == instance_id
    ]


def strikes_by(events, member_id: str) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id == member_id]


def paralyse_the_fighter_before_the_move(monkeypatch, playing: list[GameSession]) -> None:
    """Paralyse the fighter as initiative is rolled, after the round was accepted and before either side acts.

    A paralysed member can't move, so a withdrawal the fighter declared doesn't happen, and neither does the trade
    with the member it named, who stays in the back rank. A ghoul's touch does this when the monsters act first. The
    test forces it through a wrapper around the battle module's `roll_initiative`, which the round calls once, so the
    round doesn't depend on the ghoul's dice. The wrapper paralyses the fighter of the last session in `playing`.
    """
    original = battle_module.roll_initiative

    def paralysis_then_initiative(*args, **kwargs):
        fighter = playing[-1].member(FIGHTER)
        if not has_condition(fighter, Condition.PARALYSED):
            fighter.conditions = (*fighter.conditions, ActiveCondition(condition=Condition.PARALYSED, effect_id=None))
        return original(*args, **kwargs)

    monkeypatch.setattr(battle_module, "roll_initiative", paralysis_then_initiative)


class TestTheStaffOfStrikingIsAMeleeWeapon:
    """The SRD's staff of striking is "Used as a magical melee weapon", so only the front rank strikes with it."""

    @pytest.mark.parametrize("member_id", [CLERIC, MAGIC_USER])
    def test_a_member_behind_the_front_rank_is_refused(self, member_id):
        session, group, staff = staff_battle(0)
        if member_id != CLERIC:
            session.member(CLERIC).inventory.items.remove(staff)
            session.member(member_id).inventory.items.append(staff)
        assert member_id not in front_rank(session), "precondition: the member stands behind the front rank"
        assert group.distance_feet == MELEE_RANGE_FEET, "precondition: the group is in melee reach"
        streams = stream_states(session)

        result = session.execute(
            ResolveBattleRound(declarations=declarations(session, strike(member_id, group, staff)))
        )

        assert not result.accepted
        assert [(rejection.code, dict(rejection.params)) for rejection in result.rejections] == [
            (NOT_IN_FRONT_RANK, {"character": member_id})
        ]
        assert result.events == ()
        assert staff.charges_remaining == 5
        assert staff.identified is False
        assert stream_states(session) == streams
        assert session.battle.round == 0

    def test_a_named_member_whose_trade_did_not_happen_fizzles_in_the_magic_phase(self, monkeypatch):
        playing: list[GameSession] = []
        paralyse_the_fighter_before_the_move(monkeypatch, playing)
        checked = []
        for seed in SCAN_SEEDS:
            session, group, staff = staff_battle(seed)
            playing.append(session)
            assert CLERIC not in front_rank(session), "precondition: the cleric starts in the back rank"
            withdraw = BattleDeclaration(
                character_id=FIGHTER, action="move", move="fighting_withdrawal", replacement_id=CLERIC
            )
            result = session.execute(
                ResolveBattleRound(declarations=declarations(session, withdraw, strike(CLERIC, group, staff)))
            )
            assert result.accepted, [rejection.code for rejection in result.rejections]
            assert not any(isinstance(event, MemberWithdrewEvent) for event in result.events), "the trade happened"
            party_ids = {member.id for member in session.party.members}
            if any(isinstance(event, DeathEvent) and event.target_id in party_ids for event in result.events):
                continue  # a member's death moves the ranks up, so this round can't show the rank the staff met
            assert CLERIC not in front_rank(session), "precondition: the cleric stayed in the back rank"
            found = fizzles(result.events, CLERIC, "magic.device.fizzled")
            assert len(found) == 1, [event.code for event in result.events]
            assert found[0].reason == NOT_IN_FRONT_RANK
            assert found[0].item_id == staff.instance_id
            assert not activations(result.events, staff.instance_id)
            assert not strikes_by(result.events, CLERIC)
            assert staff.charges_remaining == 5
            assert staff.identified is False
            checked.append(seed)
        assert checked, "every seed killed a member, so no round kept the cleric behind"

    def test_a_front_rank_member_strikes(self):
        session, group, staff = staff_battle(0, order=CLERIC_LEADS)
        assert CLERIC in front_rank(session)
        result = session.execute(ResolveBattleRound(declarations=declarations(session, strike(CLERIC, group, staff))))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert activations(result.events, staff.instance_id)
        assert strikes_by(result.events, CLERIC)
        assert staff.charges_remaining == 4

    def test_without_the_width_limit_every_member_strikes(self):
        session, group, staff = staff_battle(0, ruleset=Ruleset(formation_width_limit=False))
        assert session.party.members[-2].id == CLERIC, "precondition: the cleric marches third"
        assert CLERIC in front_rank(session)
        result = session.execute(ResolveBattleRound(declarations=declarations(session, strike(CLERIC, group, staff))))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert activations(result.events, staff.instance_id)
        assert staff.charges_remaining == 4

    def test_a_replacement_who_steps_up_strikes(self):
        session, group, staff = staff_battle(0)
        assert CLERIC not in front_rank(session), "precondition: the cleric starts in the back rank"
        withdraw = BattleDeclaration(
            character_id=FIGHTER, action="move", move="fighting_withdrawal", replacement_id=CLERIC
        )
        result = session.execute(
            ResolveBattleRound(declarations=declarations(session, withdraw, strike(CLERIC, group, staff)))
        )
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert any(isinstance(event, MemberWithdrewEvent) for event in result.events), "precondition: the swap"
        assert activations(result.events, staff.instance_id)
        assert strikes_by(result.events, CLERIC)
        assert staff.charges_remaining == 4

    def test_a_strike_that_misses_still_spends_its_charge(self):
        for seed in SCAN_SEEDS:
            session, group, staff = staff_battle(seed, order=CLERIC_LEADS)
            result = session.execute(
                ResolveBattleRound(declarations=declarations(session, strike(CLERIC, group, staff)))
            )
            assert result.accepted
            rolled = strikes_by(result.events, CLERIC)
            if len(rolled) == 1 and rolled[0].code == "combat.attack.missed":
                assert activations(result.events, staff.instance_id)
                assert not [
                    event
                    for event in result.events
                    if isinstance(event, DamageDealtEvent) and event.attacker_id == CLERIC
                ]
                assert staff.charges_remaining == 4
                return
        pytest.fail("no seed made the cleric miss")


# ---------------------------------------------------------------------- energy drain


class FixedStream:
    """A stand-in stream that returns the given values, so a test picks the hit point die exactly."""

    def __init__(self, values):
        self.values = list(values)

    def randbelow(self, n):
        return self.values.pop(0) % n


def make_character(class_id: str, level: int, *, max_hp: int) -> Character:
    """A character with every ability score 10, so no CON modifier touches the hit point die."""
    definition = load_classes().get(class_id)
    return Character(
        id="pc",
        name="Hero",
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores={ability: 10 for ability in AbilityScore},
        alignment=Alignment.NEUTRAL,
        max_hp=max_hp,
        current_hp=max_hp,
    )


def spawn(template_id: str, monster_id: str):
    return spawn_monster(
        load_monsters().get(template_id), id=monster_id, stream=RngStreams(master_seed=9).get("monster_spawn")
    )


def drain_event(events) -> LevelDrainedEvent:
    (event,) = [event for event in events if isinstance(event, LevelDrainedEvent)]
    return event


class TestHpLostIsTheDropInMaximumHitPoints:
    """`LevelDrainedEvent.hp_lost` is how far the victim's maximum hit points fell."""

    def test_the_dwarf_from_the_playtest_reports_the_two_points_it_lost(self):
        # The playtest's dwarf went from level 2 to 1 and from 3 maximum hit points to 1, and the event said 7.
        wight = spawn("wight", "m-w")
        dwarf = make_character("dwarf", 2, max_hp=3)
        events = resolve_energy_drain(wight, dwarf, stream=FixedStream([6]))
        assert dwarf.level == 1 and dwarf.max_hp == 1, "precondition: the floor of 1 stopped the loss"
        assert drain_event(events).hp_lost == 2

    def test_a_victim_already_at_one_maximum_hit_point_reports_none_lost(self):
        fighter = make_character("fighter", 2, max_hp=1)
        result = drain_levels(fighter, load_classes().get("fighter"), xp_policy="halfway", stream=FixedStream([4]))
        assert fighter.level == 1 and fighter.max_hp == 1
        assert drain_event(result.events).hp_lost == 0

    def test_a_two_level_drain_reports_the_whole_drop(self):
        fighter = make_character("fighter", 3, max_hp=4)
        result = drain_levels(
            fighter, load_classes().get("fighter"), levels=2, xp_policy="level_minimum", stream=FixedStream([5, 5])
        )
        assert fighter.level == 1 and fighter.max_hp == 1
        assert result.hp_rolls == (6, 6), "precondition: both levels rolled more than the maximum had to give"
        assert drain_event(result.events).hp_lost == 3

    def test_a_drain_that_kills_reports_the_drop_before_the_killing_level(self):
        spectre = spawn("spectre", "m-s")
        fighter = make_character("fighter", 2, max_hp=3)
        events = resolve_energy_drain(spectre, fighter, stream=FixedStream([6]))
        drained = drain_event(events)
        assert drained.code == "combat.drain.slain", "precondition: the second level killed the fighter"
        assert fighter.max_hp == 1
        assert drained.hp_lost == 2

    def test_a_monster_drain_stopped_at_one_reports_the_drop(self):
        ogre = spawn("ogre", "m-o")
        ogre.max_hp = ogre.current_hp = 3
        events = drain_monster_hd(ogre, stream=FixedStream([5]))
        assert ogre.max_hp == 1, "precondition: the floor of 1 stopped the loss"
        assert drain_event(events).hp_lost == 2

    def test_a_drain_with_room_to_spare_reports_the_whole_roll(self):
        fighter = make_character("fighter", 4, max_hp=24)
        result = drain_levels(fighter, load_classes().get("fighter"), xp_policy="halfway", stream=FixedStream([5]))
        assert fighter.max_hp == 18
        assert drain_event(result.events).hp_lost == 6 == result.hp_lost

    def test_a_monster_drain_with_room_to_spare_reports_the_whole_roll(self):
        ogre = spawn("ogre", "m-o")
        before = ogre.max_hp
        assert before > 8, "precondition: room for any d8"
        events = drain_monster_hd(ogre, stream=FixedStream([4]))
        assert drain_event(events).hp_lost == 5 == before - ogre.max_hp


class TestTheSpawnConsequenceIsForADrainThatKills:
    """`LevelDrainedEvent.spawn_consequence` says what becomes of a character the undead kills, so a drain the
    character survives doesn't carry it."""

    def test_a_drain_the_character_survives_carries_none(self):
        fighter = make_character("fighter", 4, max_hp=24)
        prose = "A person drained of all levels becomes a wight in 1d4 days."
        result = drain_levels(
            fighter,
            load_classes().get("fighter"),
            xp_policy="halfway",
            stream=FixedStream([5]),
            spawn_consequence=prose,
        )
        drained = drain_event(result.events)
        assert drained.code == "combat.drain.drained"
        assert drained.spawn_consequence is None

    @pytest.mark.parametrize("template_id", ["wight", "wraith", "spectre"])
    def test_an_undead_touch_the_character_survives_carries_none(self, template_id):
        fighter = make_character("fighter", 6, max_hp=36)
        events = resolve_energy_drain(spawn(template_id, "m-u"), fighter, stream=FixedStream([5, 5]))
        drained = drain_event(events)
        assert drained.code == "combat.drain.drained"
        assert drained.spawn_consequence is None

    def test_the_drain_in_the_wight_golden_battle_carries_none(self):
        document = run_wight_battle()
        drained = next(event for event in document["events"] if event["code"] == "combat.drain.drained")
        assert document["fighter_level"] == 1, "precondition: the fighter survived the drain"
        assert drained["spawn_consequence"] is None

    @pytest.mark.parametrize("template_id", ["wight", "wraith", "spectre"])
    def test_a_touch_that_kills_carries_the_monsters_text(self, template_id):
        undead = spawn(template_id, "m-u")
        fighter = make_character("fighter", 1, max_hp=6)
        events = resolve_energy_drain(undead, fighter, stream=FixedStream([]))
        drained = drain_event(events)
        assert drained.code == "combat.drain.slain"
        assert drained.spawn_consequence == undead.template.ability("energy_drain").prose
        assert f"becomes a {template_id}" in drained.spawn_consequence

    def test_a_spectre_that_kills_a_level_two_character_carries_it(self):
        spectre = spawn("spectre", "m-s")
        fighter = make_character("fighter", 2, max_hp=12)
        events = resolve_energy_drain(spectre, fighter, stream=FixedStream([3]))
        drained = drain_event(events)
        assert drained.code == "combat.drain.slain" and drained.levels_lost == 2
        assert drained.spawn_consequence == spectre.template.ability("energy_drain").prose

    @pytest.mark.parametrize("victim", ["troll", "goblin"])
    def test_a_monster_victim_carries_none(self, victim):
        events = resolve_energy_drain(spawn("wight", "m-w"), spawn(victim, "m-v"), stream=FixedStream([4]))
        assert drain_event(events).spawn_consequence is None


class TestATurningOutsideBattleSkipsTheDead:
    """`TurnUndead` outside a battle reads only the living undead on the table, as a battle-round turning does."""

    @pytest.mark.parametrize("seed", range(6))
    def test_a_zombie_killed_before_the_turning_is_not_among_its_types(self, seed):
        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
        session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
        session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
        session.execute(EnterDungeon(dungeon_id="delve"))
        for _ in range(20):
            lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
            if any(event.code == "exploration.light.lit" for event in lit.events):
                break
        zombies = session.spawn("zombie", 1)
        skeletons = session.spawn("skeleton", 2)
        encounter_module.start_encounter(
            session,
            groups=[("zombie", zombies), ("skeleton", skeletons)],
            kind="spawned",
            distance_feet=40,
            pinned_stance=ReactionResult.INDIFFERENT,
            party_aware=True,
            monsters_roll_surprise=False,
        )
        zombie_id = session.encounter.groups[0].monster_ids[0]
        session.combatant(zombie_id).current_hp = 1
        wand = MagicItemInstance(instance_id="magic-item-9001", template_id="wand_of_cold", charges_remaining=5)
        session.member("character-0004").inventory.items.append(wand)
        used = session.execute(
            UseItem(character_id="character-0004", item_id=wand.instance_id, target_id=session.encounter.groups[0].id)
        )
        assert used.accepted, "precondition: the wand use is accepted"
        assert has_condition(session.combatant(zombie_id), Condition.DEAD), "precondition: the wand killed the zombie"
        assert session.encounter is not None and session.battle is None, "precondition: the encounter goes on"

        result = session.execute(TurnUndead(character_id="character-0003"))

        assert result.accepted
        turnings = [event for event in result.events if event.code.startswith("magic.turning.")]
        assert turnings, "the turning reports an outcome"
        for event in turnings:
            assert [kind.template_id for kind in event.types] == ["skeleton"]
