"""An attack with no weapon named, and a device use or turning judged again in the magic phase.

Two owner decisions, pinned here before the code (issue #162, item 18, and issue #145).

**The default weapon.** `BattleDeclaration.weapon_id=None` used to mean bare hands, so a member holding a sword
attacked barehanded whenever a front end left the field out. Now `None` means the wielded weapon that suits
the attack. The SRD decides which attack a declaration makes from the distance alone. Its weapon qualities
read "**Melee:** Close quarters weapon (5' or less)" and "**Missile:** Thrown or fired weapon (greater than
5' distance)", and its combat page says missile attacks "Are possible when opponents are more than 5' from
one another" and melee attacks "Are possible when opponents are 5' or less from each other". The session
reads the group's distance when it accepts the round, then takes the first wielded item, in the order the
member equipped them, with the `melee` quality for a melee attack or the `missile` quality for a missile
attack. Among missile weapons it prefers the first whose long range reaches the group. The SRD's weapon table
lists the torch with the `Melee` quality, so a torch counts, but only when the member wields no real weapon that
suits the attack, because a member with a torch in one hand and a sword in the other swings the sword. Holy
water and burning oil are "Splash weapon"
entries that a `use_item` declaration throws, so they never count, and a wand has no weapon stats at all. A
member with nothing that suits the attack strikes unarmed, as before. The choice is made once, when the round
is accepted, so a round with `weapon_id=None` is exactly the round that names the chosen weapon. Most tests
here assert that equivalence: the same seed and setup, once with `None` and once with the chosen id, must give
the same events, rejections, stream states, and party.

**The device and turning re-check.** Since #139 the magic phase judges a `cast` and a scroll-read `use_item`
again just before they resolve, and one that now fails fizzles with `magic.cast.fizzled`. A device `use_item`
and a `turn_undead` now get the same re-check. One that fails it fizzles with a new event,
`DeclarationFizzledEvent`, at `magic.device.fizzled` or `magic.turning.fizzled`, with the re-check's first
rejection code as `reason`. `SpellDisruptedEvent` stays for spells and scrolls only, so its `spell_id` always
names a spell. A fizzled device use costs what a `hold` costs: the device keeps its charge, isn't identified,
emits no `items.device.activated`, and breaks no invisibility, because it was never used. Its event's
`item_id` is the device's instance id, which reveals nothing about an unidentified device. A fizzled turning
has no `item_id`, draws nothing, and costs nothing.

Tests of new behavior are marked expected-to-fail for the chunk. The unmarked classes pin behavior that has to
stay as it is, the explicit weapon, bare hands, and a device the re-check passes, and they pass today.
"""

from collections.abc import Callable
from typing import Any

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.character import party_to_document
from osrlib.core.combat import COMBAT_STREAM, incapacitated
from osrlib.core.effects import ActiveCondition, Condition, EffectDefinition, has_condition
from osrlib.core.events import (
    AttackRolledEvent,
    DeclarationFizzledEvent,
    Event,
    SpellDisruptedEvent,
    UndeadTurnedEvent,
    Visibility,
)
from osrlib.core.items import MagicItemInstance
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
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_magic_items
from osrlib.messages import format_message
from osrlib.persistence import replay_game

CHUNK = pytest.mark.xfail(reason="chunk: weapon-and-device", strict=True)

FIGHTER = "character-0001"
THIEF = "character-0002"
CLERIC = "character-0003"
MAGIC_USER = "character-0004"

WEAPON_SEED = 4
"""A seed under which the fighter lives to swing in every weapon case, the slow two-handed sword included."""

SCAN_SEEDS = range(16)
"""Seeds to scan for both initiative outcomes. Each scanning test asserts it found both."""


# ---------------------------------------------------------------------- helpers


def _held_id(held) -> str:
    """The id a declaration names a wielded item by: a magic item's instance id, a mundane item's catalog id."""
    return held.instance_id if isinstance(held, MagicItemInstance) else held.template.id


def _light_and_enter(session: GameSession) -> None:
    session.execute(GrantItem(character_id=FIGHTER, item_id="torch", quantity=6))
    session.execute(GrantItem(character_id=FIGHTER, item_id="tinder_box"))
    session.execute(EnterDungeon(dungeon_id="delve"))
    for _ in range(20):
        lit = session.execute(LightSource(character_id=FIGHTER, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            return
    pytest.fail("the torch never lit")


def armed_battle(
    wielded: tuple[str, ...], *, distance: int, seed: int = WEAPON_SEED, carried: tuple[str, ...] = ()
) -> GameSession:
    """A goblin battle built from commands alone, so `replay_game` can rebuild it from the command log.

    The fighter equips `wielded` in that order and carries `carried` unequipped. Two goblins appear at
    `distance` and the battle is engaged.
    """
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    for item_id in wielded:
        assert session.execute(GrantItem(character_id=FIGHTER, item_id=item_id)).accepted
        equipped = session.execute(EquipItem(character_id=FIGHTER, item_id=item_id))
        assert equipped.accepted, [rejection.code for rejection in equipped.rejections]
    for item_id in carried:
        assert session.execute(GrantItem(character_id=FIGHTER, item_id=item_id)).accepted
    _light_and_enter(session)
    session.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=distance))
    if session.mode is SessionMode.ENCOUNTER:
        session.execute(EngageBattle())
    assert session.mode is SessionMode.BATTLE
    assert session.encounter.groups[0].distance_feet == distance
    assert [_held_id(held) for held in session.member(FIGHTER).inventory.wielded] == list(wielded)
    return session


def declarations(session: GameSession, *extra: BattleDeclaration) -> tuple[BattleDeclaration, ...]:
    """The round's declarations in marching order: each member's from `extra`, and a `hold` for the rest.

    The order is fixed whatever `extra` holds, because the initiative event lists the party in declaration
    order, and a test that compares two rounds needs the same order in both.
    """
    chosen = {declaration.character_id: declaration for declaration in extra}
    return tuple(
        chosen.get(member.id, BattleDeclaration(character_id=member.id, action="hold"))
        for member in session.party.living_members()
        if not incapacitated(member)
    )


def attack(session: GameSession, weapon_id: str | None, member_id: str = FIGHTER) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=member_id, action="attack", target_group_id=session.encounter.groups[0].id, weapon_id=weapon_id
    )


def attack_names(events, member_id: str = FIGHTER) -> list[str]:
    return [
        event.attack_name for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id == member_id
    ]


def outcome(session: GameSession, result: CommandResult, *, drop: Callable[[Event], bool] = lambda _event: False):
    """Everything a round produced or changed that a player or a replay could tell apart.

    `drop` removes events from the comparison, for a test that expects exactly those events to differ.
    """
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


def monsters_first(result: CommandResult, group_id: str) -> bool:
    """Whether the monster side acted before the party this round, read off the initiative event."""
    initiative = next(event for event in result.events if event.code == "combat.initiative.rolled")
    return initiative.order[0] == group_id


def is_fizzle(event: Event) -> bool:
    return isinstance(event, DeclarationFizzledEvent)


# ---------------------------------------------------------------------- the default weapon


EQUIVALENCE_CASES = [
    pytest.param(5, ("sword",), "sword", "Sword", id="a sword at melee range"),
    pytest.param(5, ("two_handed_sword",), "two_handed_sword", "Two-handed sword", id="a slow two-handed sword"),
    pytest.param(40, ("crossbow",), "crossbow", "Crossbow", id="a crossbow beyond 5 feet"),
    pytest.param(5, ("short_bow", "sword"), "sword", "Sword", id="the sword after a bow at melee range"),
    pytest.param(40, ("sword", "short_bow"), "short_bow", "Short bow", id="the bow after a sword beyond 5 feet"),
    pytest.param(5, ("mace", "sword"), "mace", "Mace", id="the first of two melee weapons"),
    pytest.param(40, ("spear", "long_bow"), "spear", "Spear", id="the first missile weapon that reaches"),
    pytest.param(100, ("spear", "long_bow"), "long_bow", "Long bow", id="a later missile weapon that reaches"),
    pytest.param(5, ("torch",), "torch", "improvised", id="a torch is a melee weapon"),
    pytest.param(5, ("torch", "sword"), "sword", "Sword", id="a sword over a torch equipped first"),
    pytest.param(5, ("torch", "dagger"), "dagger", "Dagger", id="a dagger over a torch equipped first"),
    pytest.param(40, ("oil_flask", "short_bow"), "short_bow", "Short bow", id="a splash weapon is passed over"),
    pytest.param(200, ("short_bow",), "short_bow", None, id="a bow that cannot reach is still the bow"),
]


@CHUNK
class TestAnAttackWithNoWeaponUsesTheWieldedWeapon:
    """`weapon_id=None` attacks with the wielded weapon that suits the attack, chosen when the round is
    accepted, and the round is the one that names that weapon."""

    def test_a_member_holding_a_sword_swings_it(self):
        session = armed_battle(("sword",), distance=5)
        result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, None))))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        names = attack_names(result.events)
        assert names, "the fighter attacked"
        assert set(names) == {load_equipment().get("sword").name}

    @pytest.mark.parametrize(("distance", "wielded", "chosen", "attack_name"), EQUIVALENCE_CASES)
    def test_leaving_the_weapon_out_is_the_round_that_names_the_suitable_weapon(
        self, distance, wielded, chosen, attack_name
    ):
        named = armed_battle(wielded, distance=distance)
        named_result = named.execute(ResolveBattleRound(declarations=declarations(named, attack(named, chosen))))
        if attack_name is None:
            assert not named_result.accepted
            assert [rejection.code for rejection in named_result.rejections] == ["combat.attack.out_of_range"]
        else:
            assert named_result.accepted, [rejection.code for rejection in named_result.rejections]
            names = attack_names(named_result.events)
            assert names and set(names) == {attack_name}, names

        default = armed_battle(wielded, distance=distance)
        default_result = default.execute(ResolveBattleRound(declarations=declarations(default, attack(default, None))))
        assert outcome(default, default_result) == outcome(named, named_result)

    def test_the_weapon_is_chosen_when_the_round_is_accepted(self):
        """At 20 feet the bow suits the attack. The thief's `close` brings the party to 5 feet before the missile
        phase, where the sword would suit, but the bow chosen at acceptance is the one that fires."""

        def play(weapon_id):
            session = armed_battle(("short_bow", "sword"), distance=20)
            close = BattleDeclaration(
                character_id=THIEF, action="move", move="close", target_group_id=session.encounter.groups[0].id
            )
            result = session.execute(
                ResolveBattleRound(declarations=declarations(session, attack(session, weapon_id), close))
            )
            return session, result

        named, named_result = play("short_bow")
        assert named_result.accepted, [rejection.code for rejection in named_result.rejections]
        moves = [event for event in named_result.events if event.code == "battle.group.moved"]
        first_shot = next(
            index
            for index, event in enumerate(named_result.events)
            if isinstance(event, AttackRolledEvent) and event.attacker_id == FIGHTER
        )
        assert moves and named_result.events.index(moves[0]) < first_shot
        assert moves[0].distance_feet == MELEE_RANGE_FEET, "the party closed to melee range before the shot"
        assert set(attack_names(named_result.events)) == {"Short bow"}

        default, default_result = play(None)
        assert outcome(default, default_result) == outcome(named, named_result)

    def test_a_wand_held_before_the_sword_is_passed_over(self):
        def play(weapon_id):
            session = armed_battle(("sword",), distance=5)
            wand = MagicItemInstance(
                instance_id="magic-item-9001", template_id="wand_of_cold", identified=True, charges_remaining=5
            )
            session.member(FIGHTER).inventory.wielded.insert(0, wand)
            result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, weapon_id))))
            return session, result

        named, named_result = play("sword")
        assert named_result.accepted
        assert set(attack_names(named_result.events)) == {"Sword"}
        default, default_result = play(None)
        assert outcome(default, default_result) == outcome(named, named_result)

    def test_a_magic_weapon_is_chosen_by_its_instance(self):
        def play(weapon_id):
            session = armed_battle((), distance=5)
            blade = MagicItemInstance(instance_id="magic-item-9002", template_id="sword_plus_1", base_item_id="sword")
            session.member(FIGHTER).inventory.wielded.append(blade)
            result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, weapon_id))))
            return session, result

        named, named_result = play("magic-item-9002")
        assert named_result.accepted
        names = attack_names(named_result.events)
        assert names and set(names) == {load_magic_items().get("sword_plus_1").name}
        default, default_result = play(None)
        assert outcome(default, default_result) == outcome(named, named_result)

    def test_a_refused_default_costs_nothing(self):
        session = armed_battle(("short_bow",), distance=200)
        streams = stream_states(session)
        party = [member.model_dump(mode="json") for member in session.party.members]
        logged = len(session.command_log)
        round_before = session.battle.round
        result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, None))))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["combat.attack.out_of_range"]
        assert result.events == ()
        assert stream_states(session) == streams
        assert [member.model_dump(mode="json") for member in session.party.members] == party
        assert session.battle.round == round_before
        assert len(session.command_log) == logged

    def test_a_default_battle_replays_to_the_same_game(self):
        session = armed_battle(("sword",), distance=5)
        swung = []
        for _ in range(3):
            if session.mode is not SessionMode.BATTLE or incapacitated(session.member(FIGHTER)):
                break
            result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, None))))
            assert result.accepted, [rejection.code for rejection in result.rejections]
            swung.extend(attack_names(result.events))
        assert swung and set(swung) == {"Sword"}

        logged = [command for command in session.command_log if isinstance(command, ResolveBattleRound)]
        assert logged
        fighter_declarations = [
            declaration
            for command in logged
            for declaration in command.declarations
            if declaration.character_id == FIGHTER
        ]
        assert fighter_declarations and all(declaration.weapon_id is None for declaration in fighter_declarations)

        replayed = replay_game(
            WEAPON_SEED, party_to_document(build_party().members), session.adventure, Ruleset(), session.command_log
        )
        assert [event.model_dump(mode="json") for event in replayed.event_log] == [
            event.model_dump(mode="json") for event in session.event_log
        ]
        assert stream_states(replayed) == stream_states(session)


class TestExplicitAndUnarmedAttacksStayAsTheyAre:
    """An explicit `weapon_id` behaves as it always has, and a member with nothing that suits the attack strikes
    unarmed. These pass before and after the chunk."""

    def test_an_explicit_weapon_is_never_swapped_for_a_better_one(self):
        session = armed_battle(("sword", "short_bow"), distance=40)
        result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, "sword"))))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["combat.attack.out_of_reach"]

    def test_an_explicit_weapon_that_is_only_carried_is_refused(self):
        session = armed_battle(("sword",), distance=5, carried=("short_bow",))
        result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, "short_bow"))))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["battle.declaration.weapon_not_wielded"]
        assert result.rejections[0].params == {"item": "short_bow"}

    def test_a_member_wielding_nothing_strikes_unarmed(self):
        session = armed_battle((), distance=5, carried=("sword",))
        result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, None))))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        names = attack_names(result.events)
        assert names and set(names) == {"unarmed"}

    def test_a_member_wielding_only_a_wand_strikes_unarmed(self):
        session = armed_battle((), distance=5)
        session.member(FIGHTER).inventory.wielded.append(
            MagicItemInstance(
                instance_id="magic-item-9001", template_id="wand_of_cold", identified=True, charges_remaining=5
            )
        )
        result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, None))))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        names = attack_names(result.events)
        assert names and set(names) == {"unarmed"}

    def test_a_member_wielding_only_a_splash_weapon_is_refused_beyond_reach(self):
        session = armed_battle(("oil_flask",), distance=40)
        streams = stream_states(session)
        result = session.execute(ResolveBattleRound(declarations=declarations(session, attack(session, None))))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["combat.attack.out_of_reach"]
        assert stream_states(session) == streams


# ---------------------------------------------------------------------- the device and turning re-check


def staff_battle(seed: int, *, identified: bool = False, invisible: bool = False):
    """Goblins at melee range that are already fleeing, and a cleric with a staff of striking aimed at them.

    The declaration passes at the top of the round. When the monsters win initiative, the group runs 60 feet
    before the party's magic phase, out of the staff's melee reach. When the party wins, the staff resolves at
    5 feet. The group is still inside 120 feet either way, so it hasn't fled and the battle goes on.
    """
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    _light_and_enter(session)
    instances = session.spawn("goblin", 2)
    encounter_module.start_encounter(
        session,
        groups=[("goblin", instances)],
        kind="spawned",
        distance_feet=MELEE_RANGE_FEET,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    session.execute(EngageBattle())
    assert session.mode is SessionMode.BATTLE
    group = session.encounter.groups[0]
    group.fleeing = True
    staff = MagicItemInstance(
        instance_id="magic-item-9003", template_id="staff_of_striking", identified=identified, charges_remaining=5
    )
    session.member(CLERIC).inventory.items.append(staff)
    if invisible:
        session.ledger.attach(
            EffectDefinition(kind="invisibility", condition=Condition.INVISIBLE),
            CLERIC,
            clock=session.clock,
            allocator=session.allocator,
            registry=session.registry(),
        )
    return session, group, staff


def use_staff(session, group, staff) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=CLERIC, action="use_item", item_id=staff.instance_id, target_group_id=group.id
    )


def wand_battle(seed: int):
    """Two goblin groups at 40 feet. The first is moved to 100 feet and is already fleeing, and the magic-user
    aims a wand of cold at it.

    When the monsters win initiative, the first group runs past 120 feet and leaves the battle before the magic
    phase. The second group keeps the battle going. When the party wins, the wand goes off at 100 feet.
    """
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    _light_and_enter(session)
    first = session.spawn("goblin", 2)
    second = session.spawn("goblin", 2)
    encounter_module.start_encounter(
        session,
        groups=[("goblin", first), ("goblin", second)],
        kind="spawned",
        distance_feet=40,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    session.execute(EngageBattle())
    assert session.mode is SessionMode.BATTLE
    fleeing = session.encounter.groups[0]
    fleeing.distance_feet = 100
    fleeing.fleeing = True
    wand = MagicItemInstance(instance_id="magic-item-9001", template_id="wand_of_cold", charges_remaining=5)
    session.member(MAGIC_USER).inventory.items.append(wand)
    declaration = BattleDeclaration(
        character_id=MAGIC_USER, action="use_item", item_id=wand.instance_id, target_group_id=fleeing.id
    )
    return session, fleeing, wand, declaration


def first_seed_where_monsters_act_first(play: Callable[[int], tuple[CommandResult, Any]]) -> int:
    """The first seed in `SCAN_SEEDS` whose round, as `play` runs it, gave the monster side initiative.

    `play` takes a seed and returns the round's result and the group whose initiative to read.
    """
    for seed in SCAN_SEEDS:
        result, group = play(seed)
        if monsters_first(result, group.id):
            return seed
    pytest.fail("no seed let the monsters act first")


def activations(events, instance_id: str) -> list[Event]:
    return [
        event
        for event in events
        if event.code == "items.device.activated" and getattr(event, "instance_id", None) == instance_id
    ]


def assert_device_fizzle(
    fizzle: DeclarationFizzledEvent, *, actor_id: str, instance_id: str, template_id: str, reason: str
):
    assert fizzle.event_type == "declaration_fizzled"
    assert fizzle.code == "magic.device.fizzled"
    assert fizzle.actor_id == actor_id
    assert fizzle.item_id == instance_id
    assert fizzle.reason == reason
    assert fizzle.visibility is Visibility.PLAYER
    assert template_id not in fizzle.model_dump_json(), "the fizzle names an unidentified device"
    message = format_message(fizzle)
    assert message and message != fizzle.code
    assert instance_id in message
    assert "lost" not in message.lower(), "a device that fizzles loses nothing"


@CHUNK
class TestADeviceUseIsJudgedAgainInTheMagicPhase:
    """A wand, staff, or rod declaration is judged again just before it resolves. One that now fails fizzles
    with `magic.device.fizzled`, keeps its charge, and costs what a `hold` costs."""

    def test_a_staff_of_striking_whose_group_ran_out_of_reach_fizzles(self):
        fizzled_seeds, resolved_seeds = [], []
        for seed in SCAN_SEEDS:
            session, group, staff = staff_battle(seed)
            result = session.execute(
                ResolveBattleRound(declarations=declarations(session, use_staff(session, group, staff)))
            )
            assert result.accepted, [rejection.code for rejection in result.rejections]
            if monsters_first(result, group.id):
                assert group.distance_feet > MELEE_RANGE_FEET and not group.fled, "the group ran but stayed"
                found = fizzles(result.events, CLERIC, "magic.device.fizzled")
                assert len(found) == 1, [event.code for event in result.events]
                assert_device_fizzle(
                    found[0],
                    actor_id=CLERIC,
                    instance_id=staff.instance_id,
                    template_id="staff_of_striking",
                    reason="combat.attack.out_of_reach",
                )
                assert not activations(result.events, staff.instance_id)
                assert not attack_names(result.events, CLERIC)
                assert staff.charges_remaining == 5
                assert staff.identified is False
                fizzled_seeds.append(seed)
            else:
                assert not fizzles(result.events, CLERIC, "magic.device.fizzled")
                assert activations(result.events, staff.instance_id)
                assert attack_names(result.events, CLERIC)
                assert staff.charges_remaining == 4
                resolved_seeds.append(seed)
        assert fizzled_seeds, "no seed let the monsters act first"
        assert resolved_seeds, "no seed let the party act first"

    def test_a_fizzled_device_use_costs_what_a_hold_costs(self):
        seed = first_seed_where_monsters_act_first(lambda seed: self._staff_round(seed, use=True)[1:3])
        used_session, used_result, _, staff = self._staff_round(seed, use=True)
        held_session, held_result, _, held_staff = self._staff_round(seed, use=False)
        assert len(fizzles(used_result.events, CLERIC, "magic.device.fizzled")) == 1
        assert outcome(used_session, used_result, drop=is_fizzle) == outcome(held_session, held_result)
        assert staff.model_dump(mode="json") == held_staff.model_dump(mode="json")
        assert has_condition(used_session.member(CLERIC), Condition.INVISIBLE), "the unused staff broke invisibility"

    @staticmethod
    def _staff_round(seed: int, *, use: bool):
        session, group, staff = staff_battle(seed, invisible=True)
        assert has_condition(session.member(CLERIC), Condition.INVISIBLE)
        extra = (use_staff(session, group, staff),) if use else ()
        result = session.execute(ResolveBattleRound(declarations=declarations(session, *extra)))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        return session, result, group, staff

    def test_an_area_wand_aimed_at_a_group_that_left_the_battle_fizzles(self):
        fizzled_seeds, resolved_seeds = [], []
        for seed in SCAN_SEEDS:
            session, fleeing, wand, declaration = wand_battle(seed)
            result = session.execute(ResolveBattleRound(declarations=declarations(session, declaration)))
            assert result.accepted, [rejection.code for rejection in result.rejections]
            assert session.mode is SessionMode.BATTLE, "the second group keeps the battle going"
            if monsters_first(result, fleeing.id):
                assert fleeing.fled, "the group left the battle before the magic phase"
                found = fizzles(result.events, MAGIC_USER, "magic.device.fizzled")
                assert len(found) == 1, [event.code for event in result.events]
                assert_device_fizzle(
                    found[0],
                    actor_id=MAGIC_USER,
                    instance_id=wand.instance_id,
                    template_id="wand_of_cold",
                    reason="battle.declaration.unknown_group",
                )
                assert not activations(result.events, wand.instance_id)
                assert wand.charges_remaining == 5
                assert wand.identified is False
                fizzled_seeds.append(seed)
            else:
                assert not fizzles(result.events, MAGIC_USER, "magic.device.fizzled")
                assert activations(result.events, wand.instance_id)
                assert wand.charges_remaining == 4
                resolved_seeds.append(seed)
        assert fizzled_seeds, "no seed let the monsters act first"
        assert resolved_seeds, "no seed let the party act first"

    def test_a_fizzled_device_round_replays_identically(self):
        def play(seed):
            session, group, staff = staff_battle(seed)
            result = session.execute(
                ResolveBattleRound(declarations=declarations(session, use_staff(session, group, staff)))
            )
            return session, result, group

        seed = first_seed_where_monsters_act_first(lambda seed: play(seed)[1:])
        first_session, first_result, _ = play(seed)
        second_session, second_result, _ = play(seed)
        assert len(fizzles(first_result.events, CLERIC, "magic.device.fizzled")) == 1
        assert outcome(first_session, first_result) == outcome(second_session, second_result)


def weaken_the_cleric_after_party_movement(monkeypatch) -> None:
    """Weaken the cleric partway through the round, after the round was accepted and before the magic phase.

    Nothing in the rules weakens a cleric in the middle of a battle round today, so the test forces it. It wraps
    the party block's movement step, which runs before the party's missile and magic phases. Weakened is one of
    the conditions under which `validate_turn_undead` refuses a turning, and it isn't one of the conditions that
    make the magic phase skip a member outright.
    """
    original = battle_module._party_movement

    def movement_then_weakness(session, by_member):
        events = original(session, by_member)
        cleric = session.member(CLERIC)
        if not has_condition(cleric, Condition.WEAKENED):
            cleric.conditions = (*cleric.conditions, ActiveCondition(condition=Condition.WEAKENED, effect_id=None))
        return events

    monkeypatch.setattr(battle_module, "_party_movement", movement_then_weakness)


def skeleton_battle(seed: int) -> GameSession:
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    _light_and_enter(session)
    instances = session.spawn("skeleton", 2)
    encounter_module.start_encounter(
        session,
        groups=[("skeleton", instances)],
        kind="spawned",
        distance_feet=40,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    session.execute(EngageBattle())
    assert session.mode is SessionMode.BATTLE
    return session


@CHUNK
class TestATurningIsJudgedAgainInTheMagicPhase:
    """A `turn_undead` declaration is judged again just before it resolves. One that now fails fizzles with
    `magic.turning.fizzled` and no `item_id`, instead of raising out of the round."""

    def test_a_turning_the_cleric_can_no_longer_attempt_fizzles(self, monkeypatch):
        weaken_the_cleric_after_party_movement(monkeypatch)
        turning = skeleton_battle(seed=3)
        assert not has_condition(turning.member(CLERIC), Condition.WEAKENED), "accepted while able to turn"
        turn = BattleDeclaration(character_id=CLERIC, action="turn_undead")
        result = turning.execute(ResolveBattleRound(declarations=declarations(turning, turn)))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert has_condition(turning.member(CLERIC), Condition.WEAKENED), "the cleric was weakened mid-round"

        found = fizzles(result.events, CLERIC, "magic.turning.fizzled")
        assert len(found) == 1, [event.code for event in result.events]
        fizzle = found[0]
        assert fizzle.event_type == "declaration_fizzled"
        assert fizzle.actor_id == CLERIC
        assert fizzle.item_id is None
        assert fizzle.reason == "magic.turning.caster_incapacitated"
        assert fizzle.visibility is Visibility.PLAYER
        message = format_message(fizzle)
        assert message and message != fizzle.code
        assert "lost" not in message.lower(), "a turning that fizzles loses nothing"
        assert "turn" in message.lower(), "the message names the turning"
        assert not any(isinstance(event, UndeadTurnedEvent) for event in result.events)

        holding = skeleton_battle(seed=3)
        held = holding.execute(ResolveBattleRound(declarations=declarations(holding)))
        assert held.accepted
        assert outcome(turning, result, drop=is_fizzle) == outcome(holding, held)

    def test_a_fizzled_turning_draws_nothing_from_the_magic_stream(self, monkeypatch):
        weaken_the_cleric_after_party_movement(monkeypatch)
        session = skeleton_battle(seed=3)
        before = session.streams.get(MAGIC_STREAM).export_state()
        turn = BattleDeclaration(character_id=CLERIC, action="turn_undead")
        result = session.execute(ResolveBattleRound(declarations=declarations(session, turn)))
        assert result.accepted
        assert len(fizzles(result.events, CLERIC, "magic.turning.fizzled")) == 1
        assert session.streams.get(MAGIC_STREAM).export_state() == before


# ---------------------------------------------------------------------- what stays as it is


STAFF_SEED_0_CODES = [
    "battle.round.started",
    "combat.initiative.rolled",
    "items.item.identified",
    "items.device.activated",
    "combat.attack.hit",
    "combat.damage.dealt",
    "combat.state.hit_points",
    "effects.condition.gained",
    "combat.death.died",
    "combat.state.hit_points",
    "battle.group.moved",
]
"""The staff round under seed 0, captured before the chunk: the party acts first and the staff strikes at 5 feet."""

STAFF_SEED_0_ROLL = (16, 16, 13)
"""The cleric's attack roll under seed 0 as `(roll, total, required)`, captured before the chunk."""

STAFF_SEED_0_DAMAGE = (5, (1, 4))
"""The staff's 2d6 under seed 0 as `(amount, rolls)`, captured before the chunk."""


class TestADeviceTheReCheckPassesResolvesAsItDid:
    """The re-check takes no draw, so a device use that passes it resolves with the same events and the same draws
    as before the chunk. The literal values below were captured from the engine before the chunk. They pass before
    and after it."""

    def test_a_staff_of_striking_in_reach_strikes_and_spends_a_charge(self):
        session, group, staff = staff_battle(0)
        combat_before = session.streams.get(COMBAT_STREAM).export_state()
        result = session.execute(
            ResolveBattleRound(declarations=declarations(session, use_staff(session, group, staff)))
        )
        assert result.accepted
        assert not monsters_first(result, group.id), "the party acts first under this seed"
        assert [event.code for event in result.events] == STAFF_SEED_0_CODES
        strike = next(
            event for event in result.events if isinstance(event, AttackRolledEvent) and event.attacker_id == CLERIC
        )
        assert (strike.roll, strike.total, strike.required) == STAFF_SEED_0_ROLL
        damage = next(event for event in result.events if event.code == "combat.damage.dealt")
        assert (damage.amount, tuple(damage.rolls)) == STAFF_SEED_0_DAMAGE
        assert staff.charges_remaining == 4
        assert staff.identified is True
        assert session.streams.get(COMBAT_STREAM).export_state() != combat_before

    def test_a_staff_declared_out_of_reach_is_refused_and_costs_nothing(self):
        session, group, staff = staff_battle(0)
        group.fleeing = False
        group.distance_feet = 40
        streams = stream_states(session)
        result = session.execute(
            ResolveBattleRound(declarations=declarations(session, use_staff(session, group, staff)))
        )
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["combat.attack.out_of_reach"]
        assert result.events == ()
        assert staff.charges_remaining == 5
        assert staff.identified is False
        assert stream_states(session) == streams
