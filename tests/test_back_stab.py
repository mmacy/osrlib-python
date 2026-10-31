"""Acceptance tests for a thief's back-stab in a session's battle.

The OSE SRD's thief entry says "When attacking an unaware opponent from behind, a thief receives a +4 bonus to hit
and doubles any damage dealt." The kernel already applies that to an `AttackContext` that sets both `behind_target`
and `target_unaware` for an attacker whose class has the `back_stab` ability. These tests check the contract that
lets a session's battle ask for it, which the adaptations register states under "A back-stab needs monsters the
party surprised".

**The reading.** osrlib has no facing in battle, so "an unaware opponent from behind" is a monster the party
surprised, in the round it can't act. A member sets `BattleDeclaration.back_stab` on an `attack`, and the session
accepts it only while `BattleState.monsters_hold_rounds` is above zero as it accepts the round. Every attack roll the
declaration makes that round is a back-stab, even when the monsters win initiative and their held turn passes first.

**The attack.** An accepted back-stab hits the monster an ordinary melee attack would. It resolves through
`AttackContext(behind_target=True, target_unaware=True)`: +4 to hit and double damage from the class's `back_stab`
params, and the defender's shield doesn't count. Its `AttackRolledEvent` has `back_stab` set, which the kernel sets
for any back-stab, an automatic hit included.

**The refusals.** Checked before any draw, so a refused round costs nothing, and a declaration gets only the first
that applies, with the member's id as the `character` param:

1. `battle.declaration.back_stab_not_attack`, on any action but `attack`, before the action's own checks.
2. `battle.declaration.back_stab_no_ability`, for a class with no `back_stab` ability.
3. `battle.declaration.back_stab_target_aware`, in any round the monsters aren't held by surprise.
4. The attack's group and weapon checks: `unknown_group`, `no_target`, and `weapon_not_wielded`.
5. `battle.declaration.back_stab_not_melee`, when the attack the session would resolve is a missile attack.
6. The melee checks, like `not_in_front_rank` and `combat.attack.out_of_reach`.

A declaration that leaves `back_stab` at `False` resolves exactly as before. The goldens check that for every
existing battle, and a guard here checks it in the free round.

The battles are built by command from seeded sessions. Each helper takes the first seed that gives the setup it
needs, so two calls with the same arguments give the same session, and a test can play one with a back-stab and one
without. Each test asserts its own preconditions, so a test that stops exercising its case fails loudly.
"""

import pytest
from pydantic import TypeAdapter

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character, party_to_document
from osrlib.core.combat import COMBAT_STREAM, AttackContext, attack_roll, incapacitated
from osrlib.core.effects import EffectDefinition
from osrlib.core.events import AttackRolledEvent, DamageDealtEvent, InitiativeRolledEvent, Visibility
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
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
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_equipment, load_monsters
from osrlib.messages import format_message
from osrlib.persistence import replay_game, session_state

SEEDS = range(0, 160)
"""Seeds the helpers scan for a setup they need, like a battle in which the party surprised the monsters."""

THIEF = ("Sable", "thief")
FIGHTER = ("Brakk", "fighter")
SECOND_FIGHTER = ("Hild", "fighter")
DUO = (THIEF, FIGHTER)
"""Sable the thief and Brakk the fighter, side by side in the corridor's front rank."""

NOT_ATTACK = "battle.declaration.back_stab_not_attack"
NO_ABILITY = "battle.declaration.back_stab_no_ability"
TARGET_AWARE = "battle.declaration.back_stab_target_aware"
NOT_MELEE = "battle.declaration.back_stab_not_melee"


# ---------------------------------------------------------------------- setup


def _character(name: str, class_id: str, *, level: int = 3, hp: int = 400) -> Character:
    """A character with scores of 10, so no STR modifier touches an attack or its damage."""
    definition = load_classes().get(class_id)
    return Character(
        name=name,
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores={ability: 10 for ability in AbilityScore},
        alignment="lawful",
        max_hp=hp,
        current_hp=hp,
    )


def _adventure() -> Adventure:
    """One level: a 10' corridor two cells long, so the party's front rank is two abreast."""
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    return Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))


def _session(seed: int, roster, *, wield: tuple[str, ...]) -> GameSession:
    """The roster in the unlit corridor, in marching order, with Sable wielding `wield`, all by command.

    The party carries no light, so the monsters roll for surprise.
    """
    members = [_character(name, class_id) for name, class_id in roster]
    game = GameSession.new(Party(members=members), _adventure(), seed=seed)
    sable = _sable(game)
    for item_id in (*wield, "arrows"):
        assert game.execute(GrantItem(character_id=sable, item_id=item_id)).accepted
    for item_id in wield:
        assert game.execute(EquipItem(character_id=sable, item_id=item_id)).accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def _opened(game: GameSession, result, *, surprised: bool) -> bool:
    """Whether the encounter `result` opened was the one wanted, and if so, open its battle.

    The party is never surprised. The monsters are surprised when `surprised` is true.
    """
    assert result.accepted
    sides = {event.side: event.surprised for event in result.events if event.code == "encounter.surprise.rolled"}
    if sides.get("party") is not False or sides.get("monsters") is not surprised:
        return False
    if game.mode is SessionMode.ENCOUNTER:
        assert game.execute(EngageBattle()).accepted
    if game.mode is not SessionMode.BATTLE or game.battle.round != 0:
        return False
    assert game.battle.monsters_hold_rounds == (1 if surprised else 0), "precondition: the free round as wanted"
    return True


def _battle(
    *,
    surprised: bool = True,
    roster=DUO,
    wield: tuple[str, ...] = ("dagger",),
    distance_feet: int = 5,
    template_id: str = "goblin",
    first_seed: int = SEEDS.start,
) -> GameSession:
    """A battle against two monsters, opened by command, with the party unsurprised.

    With `surprised`, the party surprised the monsters, so they hold in the first round. Without it, neither side
    was surprised.
    """
    for seed in range(first_seed, SEEDS.stop):
        game = _session(seed, roster, wield=wield)
        result = game.execute(SpawnMonsters(template_id=template_id, count_fixed=2, distance_feet=distance_feet))
        if not _opened(game, result, surprised=surprised):
            continue
        assert game.encounter.groups[0].distance_feet == distance_feet
        return game
    raise AssertionError(f"precondition: no seed opened the battle (surprised={surprised})")


def _seeds(**battle_args):
    """Each seed from which `_battle` builds the battle `battle_args` describe, in order."""
    seed = SEEDS.start
    while True:
        seed = _battle(first_seed=seed, **battle_args).master_seed
        yield seed
        seed += 1


def _group_id(game: GameSession) -> str:
    return game.encounter.groups[0].id


def _named(game: GameSession, name: str) -> str:
    return next(member.id for member in game.party.members if member.name == name)


def _sable(game: GameSession) -> str:
    return _named(game, "Sable")


def _brakk(game: GameSession) -> str:
    return _named(game, "Brakk")


def _declare(member_id: str, action: str = "hold", **fields) -> BattleDeclaration:
    return BattleDeclaration(character_id=member_id, action=action, **fields)


def _attack(game: GameSession, *, back_stab: bool, member_id: str | None = None, **fields) -> BattleDeclaration:
    """An attack on the first group, by Sable unless `member_id` names another member."""
    fields.setdefault("target_group_id", _group_id(game))
    return _declare(member_id or _sable(game), "attack", back_stab=back_stab, **fields)


def _round(game: GameSession, *declared: BattleDeclaration):
    """Send one round: the members in `declared` as given, every other able member holding."""
    by_id = {declaration.character_id: declaration for declaration in declared}
    able = [member for member in game.party.living_members() if not incapacitated(member)]
    assert set(by_id) <= {member.id for member in able}, "precondition: every declared member can act"
    return game.execute(ResolveBattleRound(declarations=tuple(by_id.get(m.id, _declare(m.id)) for m in able)))


def _play(game: GameSession, *declared: BattleDeclaration) -> list:
    result = _round(game, *declared)
    assert result.accepted, [rejection.code for rejection in result.rejections]
    return list(result.events)


def _rolls_by(events, attacker_id: str) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id == attacker_id]


def _damage_from(events, attacker_id: str) -> list[DamageDealtEvent]:
    return [event for event in events if isinstance(event, DamageDealtEvent) and event.attacker_id == attacker_id]


def _monsters_first(game: GameSession, events) -> bool:
    initiative = next(event for event in events if isinstance(event, InitiativeRolledEvent))
    return initiative.order[0] not in {member.id for member in game.party.members}


def _state_without_logs(game: GameSession) -> dict:
    """Every state block, the clock and the random number streams among them, without the two logs."""
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


# ---------------------------------------------------------------------- the fields


class TestTheFields:
    """The new fields. These pass before and after the chunk, because the stub ships them."""

    def test_the_declaration_defaults_to_no_back_stab(self):
        assert BattleDeclaration(character_id="character-0001", action="attack").back_stab is False

    def test_a_round_with_a_back_stab_round_trips_through_its_json(self):
        declaration = _declare("character-0001", "attack", target_group_id="group-0001", back_stab=True)
        command = ResolveBattleRound(declarations=(declaration,))
        restored = TypeAdapter(AnyCommand).validate_python(command.model_dump(mode="json"))
        assert restored == command
        assert restored.declarations[0].back_stab is True

    def test_the_event_defaults_to_no_back_stab_and_round_trips(self):
        plain = AttackRolledEvent(code="combat.attack.hit", attacker_id="a", defender_id="b", attack_name="Dagger")
        assert plain.back_stab is False
        marked = plain.model_copy(update={"back_stab": True})
        assert AttackRolledEvent.model_validate(marked.model_dump(mode="json")) == marked


# ---------------------------------------------------------------------- the kernel marks the event


def _kernel_roll(class_id: str, context: AttackContext, *, template_id: str = "goblin") -> AttackRolledEvent:
    """One dagger attack by a level 3 character of `class_id` on a fresh monster, from a fixed seed."""
    streams = RngStreams(master_seed=11)
    defender = spawn_monster(load_monsters().get(template_id), id="monster-1", stream=streams.get(MONSTER_SPAWN_STREAM))
    attacker = _character("Sable", class_id).model_copy(update={"id": "character-0001"})
    result = attack_roll(
        attacker,
        defender,
        load_equipment().get("dagger"),
        context=context,
        ruleset=Ruleset(),
        stream=streams.get(COMBAT_STREAM),
    )
    (event,) = result.events
    return event


BACK_STAB_CONTEXT = AttackContext(behind_target=True, target_unaware=True)


class TestTheKernelMarksTheEvent:
    """`attack_roll` sets `AttackRolledEvent.back_stab` whenever it applies the back-stab."""

    def test_a_thiefs_back_stab_is_marked_and_adds_four(self):
        plain = _kernel_roll("thief", AttackContext())
        stab = _kernel_roll("thief", BACK_STAB_CONTEXT)

        assert stab.roll == plain.roll, "precondition: the same d20"
        assert stab.modifier == plain.modifier + 4
        assert stab.back_stab is True
        assert plain.back_stab is False

    def test_an_automatic_hit_is_marked_too(self):
        stab = _kernel_roll("thief", BACK_STAB_CONTEXT, template_id="green_slime")

        assert stab.code == "combat.attack.auto_hit", "precondition: green slime has no armour class"
        assert stab.back_stab is True

    @pytest.mark.parametrize(
        ("class_id", "context"),
        [
            ("fighter", BACK_STAB_CONTEXT),
            ("thief", AttackContext(behind_target=True)),
            ("thief", AttackContext(target_unaware=True)),
        ],
        ids=["a-fighter", "behind-only", "unaware-only"],
    )
    def test_an_attack_that_isnt_a_back_stab_isnt_marked(self, class_id, context):
        """A guard: no `back_stab` ability, or only one of the two context fields, is no back-stab."""
        assert _kernel_roll(class_id, context).back_stab is False
        assert _kernel_roll(class_id, context).modifier == _kernel_roll(class_id, AttackContext()).modifier


# ---------------------------------------------------------------------- an accepted back-stab


def _paired(seed: int, **battle_args) -> tuple[GameSession, GameSession]:
    """Two identical battles from `seed`, one to play with a back-stab and one without."""
    sessions = (_battle(first_seed=seed, **battle_args), _battle(first_seed=seed, **battle_args))
    assert _state_without_logs(sessions[0]) == _state_without_logs(sessions[1]), "precondition: identical setups"
    return sessions


class TestAnAcceptedBackStab:
    """In the free round, a thief's back-stab gets +4 to hit, double damage, and the event's flag."""

    def test_it_adds_four_to_hit_against_the_same_target_and_draws_the_same_dice(self):
        for seed in _seeds():
            plain, stabbed = _paired(seed)
            sable = _sable(plain)
            plain_events = _play(plain, _attack(plain, back_stab=False))
            (ordinary,) = _rolls_by(plain_events, sable)
            if ordinary.code != "combat.attack.hit":
                continue  # Look for a seed where both attacks hit, so both roll damage.
            stab_events = _play(stabbed, _attack(stabbed, back_stab=True))
            (stab,) = _rolls_by(stab_events, sable)

            assert stab.back_stab is True
            assert ordinary.back_stab is False
            assert stab.defender_id == ordinary.defender_id, "a back-stab picks the target an attack would"
            assert stab.roll == ordinary.roll
            assert stab.modifier == ordinary.modifier + 4
            assert stab.defender_ac == ordinary.defender_ac, "a goblin carries no shield"
            assert stab.code == "combat.attack.hit"
            (ordinary_damage,) = _damage_from(plain_events, sable)
            (stab_damage,) = _damage_from(stab_events, sable)
            assert stab_damage.rolls == ordinary_damage.rolls, "a back-stab takes no draw of its own"
            assert stab_damage.amount == 2 * ordinary_damage.amount
            return

    def test_it_doubles_the_damage(self):
        for seed in _seeds():
            game = _battle(first_seed=seed)
            sable = _sable(game)
            events = _play(game, _attack(game, back_stab=True))
            (stab,) = _rolls_by(events, sable)
            assert stab.back_stab is True
            if stab.code != "combat.attack.hit":
                continue
            (damage,) = _damage_from(events, sable)
            assert damage.amount == 2 * sum(damage.rolls), "a dagger with no STR modifier, doubled"
            return

    def test_it_holds_when_the_monsters_win_initiative_in_the_free_round(self):
        for seed in _seeds():
            game = _battle(first_seed=seed)
            events = _play(game, _attack(game, back_stab=True))
            if not _monsters_first(game, events):
                continue
            assert game.battle is None or game.battle.monsters_hold_rounds == 0, "precondition: the held turn passed"
            (stab,) = _rolls_by(events, _sable(game))
            assert stab.back_stab is True, "the round was accepted while the monsters were held"
            return

    def test_every_swing_under_haste_is_a_back_stab(self):
        game = _battle()
        sable = _sable(game)
        haste = EffectDefinition(kind="haste", params={"attacks_multiplier": 2, "movement_multiplier": 2})
        effect, _ = game.ledger.attach(
            haste, sable, clock=game.clock, allocator=game.allocator, registry=game.registry()
        )
        assert effect is not None

        swings = _rolls_by(_play(game, _attack(game, back_stab=True)), sable)

        assert len(swings) == 2, "precondition: haste gave Sable two swings"
        assert all(swing.back_stab for swing in swings)

    def test_the_defenders_shield_doesnt_count(self):
        """An NPC adventurer with a shield is one AC worse against a back-stab, as against any attack from behind."""
        for seed in SEEDS:
            pair = [_session(seed, DUO, wield=("dagger",)) for _ in range(2)]
            results = [game.execute(SpawnNpcParty(party_kind="basic", count_fixed=1, distance_feet=5)) for game in pair]
            if not all(_opened(game, result, surprised=True) for game, result in zip(pair, results, strict=True)):
                continue
            plain, stabbed = pair
            sable = _sable(plain)
            (ordinary,) = _rolls_by(_play(plain, _attack(plain, back_stab=False)), sable)
            shield = plain.npcs[ordinary.defender_id].inventory.shield
            if ordinary.defender_ac is None or shield is None:
                continue
            (stab,) = _rolls_by(_play(stabbed, _attack(stabbed, back_stab=True)), _sable(stabbed))

            assert stab.defender_id == ordinary.defender_id
            assert stab.back_stab is True
            assert stab.defender_ac == ordinary.defender_ac + shield.template.ac_bonus
            return
        raise AssertionError("precondition: no seed surprised an NPC party whose first member carries a shield")


# ---------------------------------------------------------------------- the refusals


class TestTheRefusals:
    """Each back-stab code, in its place in the order, refusing the round before it costs anything."""

    @pytest.mark.parametrize("action", ["hold", "cast", "turn_undead", "use_item", "move"])
    def test_a_back_stab_on_any_other_action_is_refused_before_the_actions_own_checks(self, action):
        game = _battle()
        sable = _sable(game)
        fields = {
            "hold": {},
            "cast": {"spell_id": "sleep", "spell_mode": "hd_budget", "target_group_id": _group_id(game)},
            "turn_undead": {},
            "use_item": {"item_id": "flask_of_oil", "target_group_id": _group_id(game)},
            "move": {"move": "close", "target_group_id": _group_id(game)},
        }[action]

        rejections = _refused(game, _declare(sable, action, back_stab=True, **fields))

        assert rejections == [(NOT_ATTACK, {"character": sable})]

    def test_a_fighters_back_stab_is_refused(self):
        game = _battle()
        brakk = _brakk(game)

        rejections = _refused(game, _attack(game, back_stab=True, member_id=brakk))

        assert rejections == [(NO_ABILITY, {"character": brakk})]

    def test_a_back_stab_in_a_battle_without_surprise_is_refused(self):
        game = _battle(surprised=False)
        sable = _sable(game)

        rejections = _refused(game, _attack(game, back_stab=True))

        assert rejections == [(TARGET_AWARE, {"character": sable})]

    def test_a_back_stab_after_the_free_round_is_refused(self):
        game = _battle()
        sable = _sable(game)
        _play(game)
        assert game.mode is SessionMode.BATTLE, "precondition: the battle runs on"
        assert game.battle.monsters_hold_rounds == 0, "precondition: the free round is over"

        rejections = _refused(game, _attack(game, back_stab=True))

        assert rejections == [(TARGET_AWARE, {"character": sable})]

    def test_a_back_stab_with_a_bow_is_refused(self):
        game = _battle(wield=("short_bow",), distance_feet=30)
        sable = _sable(game)

        rejections = _refused(game, _attack(game, back_stab=True, weapon_id="short_bow"))

        assert rejections == [(NOT_MELEE, {"character": sable})]

    def test_a_back_stab_that_would_throw_a_dagger_is_refused(self):
        """Beyond 5' the session throws the dagger, so the attack it would resolve is a missile attack."""
        game = _battle(distance_feet=10)
        sable = _sable(game)

        rejections = _refused(game, _attack(game, back_stab=True))

        assert rejections == [(NOT_MELEE, {"character": sable})]

    def test_not_attack_comes_first_and_no_ability_before_target_aware(self):
        """Without surprise, a fighter's back-stab on a hold isn't an attack, and on an attack has no ability."""
        game = _battle(surprised=False)
        brakk = _brakk(game)

        assert _refused(game, _declare(brakk, "hold", back_stab=True)) == [(NOT_ATTACK, {"character": brakk})]
        assert _refused(game, _attack(game, back_stab=True, member_id=brakk)) == [(NO_ABILITY, {"character": brakk})]

    def test_an_aware_target_comes_before_the_group_checks(self):
        game = _battle(surprised=False)
        sable = _sable(game)

        rejections = _refused(game, _attack(game, back_stab=True, target_group_id="no-such-group"))

        assert rejections == [(TARGET_AWARE, {"character": sable})]

    def test_a_missile_back_stab_from_the_back_rank_is_refused_as_a_missile(self):
        """A missile attack takes no rank check, so a back-rank thief with a bow gets `back_stab_not_melee`."""
        game = _battle(roster=(FIGHTER, SECOND_FIGHTER, THIEF), wield=("short_bow",), distance_feet=30)
        sable = _sable(game)
        assert sable not in game.view(Visibility.PLAYER).encounter.front_rank, (
            "precondition: Sable stands in the back rank"
        )

        rejections = _refused(game, _attack(game, back_stab=True, weapon_id="short_bow"))

        assert rejections == [(NOT_MELEE, {"character": sable})]

    def test_the_group_and_weapon_checks_come_before_not_melee(self):
        """A guard: the free round's back-stab at a missing group, or with a weapon not wielded, gets the old code."""
        game = _battle(wield=("short_bow",), distance_feet=30)
        sable = _sable(game)

        missing = _refused(game, _attack(game, back_stab=True, target_group_id="no-such-group"))
        unwielded = _refused(game, _attack(game, back_stab=True, weapon_id="sword"))

        assert missing == [("battle.declaration.unknown_group", {"character": sable})]
        assert unwielded == [("battle.declaration.weapon_not_wielded", {"item": "sword"})]

    def test_the_melee_checks_come_after_the_back_stab_checks(self):
        """A guard: a melee back-stab out of reach, or from the back rank, gets the ordinary melee code."""
        far = _battle(wield=("sword",), distance_feet=10)
        assert _refused(far, _attack(far, back_stab=True)) == [("combat.attack.out_of_reach", {"distance_feet": 10})]

        three = _battle(roster=(FIGHTER, SECOND_FIGHTER, THIEF), wield=("sword",))
        sable = _sable(three)
        assert sable not in three.view(Visibility.PLAYER).encounter.front_rank, (
            "precondition: Sable stands in the back rank"
        )
        rejections = _refused(three, _attack(three, back_stab=True))
        assert rejections == [("battle.declaration.not_in_front_rank", {"character": sable})]


# ---------------------------------------------------------------------- no back-stab is unchanged


class TestNoBackStabIsUnchanged:
    """A guard: an attack without `back_stab` in the free round is an ordinary attack."""

    def test_an_ordinary_attack_in_the_free_round_isnt_marked(self):
        game = _battle()
        sable = _sable(game)

        (event,) = _rolls_by(_play(game, _attack(game, back_stab=False)), sable)

        assert event.back_stab is False
        assert event.modifier == 0, "a dagger in hands with no STR modifier"


# ---------------------------------------------------------------------- the formatter


class TestTheDefaultFormatter:
    """`format_message` names a back-stab, so the default transcript says what happened."""

    @pytest.mark.parametrize("code", ["combat.attack.hit", "combat.attack.missed", "combat.attack.auto_hit"])
    def test_a_back_stab_line_says_so(self, code):
        numbers = {} if code == "combat.attack.auto_hit" else {"roll": 12, "modifier": 4, "total": 16, "required": 13}
        event = AttackRolledEvent(
            code=code, attacker_id="sable", defender_id="goblin-1", attack_name="Dagger", back_stab=True, **numbers
        )

        assert "back-stab" in format_message(event)

    @pytest.mark.parametrize("code", ["combat.attack.hit", "combat.attack.missed", "combat.attack.auto_hit"])
    def test_an_ordinary_line_doesnt(self, code):
        """A guard: the goldens' transcripts check the exact wording of every ordinary line."""
        numbers = {} if code == "combat.attack.auto_hit" else {"roll": 12, "modifier": 0, "total": 12, "required": 13}
        event = AttackRolledEvent(
            code=code, attacker_id="sable", defender_id="goblin-1", attack_name="Dagger", **numbers
        )

        assert "back-stab" not in format_message(event)


# ---------------------------------------------------------------------- determinism


class TestDeterminism:
    """A battle with a back-stab replays exactly from its command log."""

    def test_a_battle_with_a_back_stab_replays_from_its_command_log(self):
        game = _battle()
        members = [_character(name, class_id) for name, class_id in DUO]
        party_document = party_to_document(members)

        events = _play(game, _attack(game, back_stab=True))
        assert any(event.back_stab for event in _rolls_by(events, _sable(game))), "precondition: a back-stab"
        for _ in range(3):
            if game.mode is not SessionMode.BATTLE:
                break
            _play(game)

        replayed = replay_game(game.master_seed, party_document, game.adventure, Ruleset(), game.command_log)

        assert [event.model_dump(mode="json") for event in replayed.event_log] == [
            event.model_dump(mode="json") for event in game.event_log
        ]
        assert session_state(replayed) == session_state(game)
