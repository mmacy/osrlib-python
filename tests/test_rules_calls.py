"""Acceptance tests for seven rules calls from issues #166 and #167.

Each class pins one decision the owner made on behavior the code left ambiguous. A test
marked with the `rules-calls` chunk fails until that decision is built. The unmarked tests
pin the behavior next to it that is already right, so a fix can't overreach.
"""

import pytest
from pydantic import ValidationError

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, Character, create_character
from osrlib.core.clock import GameClock
from osrlib.core.combat import COMBAT_STREAM, AttackContext, SaveCategory, attack_roll, resolve_attack
from osrlib.core.effects import (
    EFFECTS_STREAM,
    Condition,
    EffectDefinition,
    EffectsLedger,
    grant_condition,
    has_condition,
)
from osrlib.core.items import Coins
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MAGIC_STREAM, MemorizedSpell, SaveSpec, cast_spell, caster_profile, memorize_spells
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    GrantCoins,
    MoveParty,
    PurchaseHealing,
    ResolveBattleRound,
    SpawnMonsters,
    SpawnNpcParty,
    parse_command,
)
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DungeonSpec,
    Edge,
    EdgeKind,
    LevelSpec,
    TransitionSpec,
    TrapEffect,
    TrapSpec,
)
from osrlib.crawl.events import NpcPartySpawnedEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import DeathRecord, GameSession
from osrlib.data import load_classes, load_equipment, load_monsters, load_spells

NO_EFFECT_CODE = "town.healing.no_effect"
"""The rejection a temple service gets when it would do nothing to the member."""


# ---------------------------------------------------------------------- helpers


def hero(name: str = "Hild", class_id: str = "fighter", seed: int = 7) -> Character:
    """Roll a first-level character."""
    stream = RngStreams(master_seed=seed).get(CHARACTER_CREATION_STREAM)
    return create_character(
        name=name, class_id=class_id, alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream
    ).character


def mage() -> Character:
    """A third-level magic-user with *phantasmal force* and two *magic missiles* memorized."""
    caster = Character(
        name="Mira",
        class_id="magic_user",
        race="human",
        level=3,
        xp=5000,
        scores={ability: 11 for ability in AbilityScore},
        alignment="lawful",
        max_hp=9,
        current_hp=9,
        spell_book=("phantasmal_force", "magic_missile"),
    )
    prepared = memorize_spells(
        caster,
        load_classes().get("magic_user"),
        load_spells(),
        [
            MemorizedSpell(spell_id="phantasmal_force"),
            MemorizedSpell(spell_id="magic_missile"),
            MemorizedSpell(spell_id="magic_missile"),
        ],
    )
    assert not prepared.rejections
    return caster


def monster(template_id: str, monster_id: str = "monster-0001", seed: int = 3):
    """Spawn one monster from the built-in catalog."""
    stream = RngStreams(master_seed=seed).get(MONSTER_SPAWN_STREAM)
    return spawn_monster(load_monsters().get(template_id), id=monster_id, stream=stream)


def corridor(areas=(), width: int = 2) -> LevelSpec:
    """A one-row level 1 whose cells are joined by open edges, entered at (0, 0)."""
    edges = {f"{x},0:west": Edge(kind=EdgeKind.OPEN) for x in range(1, width)}
    return LevelSpec(number=1, width=width, height=1, entrance=(0, 0), edges=edges, areas=tuple(areas))


def session(levels=None, members=None, seed: int = 7, enter: bool = True) -> GameSession:
    """Start a session on a one-dungeon adventure, standing at the entrance unless `enter` is False."""
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=tuple(levels or (corridor(),)))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    game = GameSession.new(Party(members=list(members or [hero()])), adventure, seed=seed)
    if enter:
        assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def attach(game: GameSession, definition: EffectDefinition, target_id: str) -> str:
    """Attach an effect through the session's ledger and return its id."""
    effect, _ = game.ledger.attach(
        definition, target_id, clock=game.clock, allocator=game.allocator, registry=game.registry()
    )
    assert effect is not None
    return effect.effect_id


def open_battle(game: GameSession, template_id: str, *, count: int, distance_feet: int) -> None:
    """Spawn monsters and make sure a battle is open with them."""
    assert game.execute(SpawnMonsters(template_id=template_id, count_fixed=count, distance_feet=distance_feet)).accepted
    if game.battle is None:
        assert game.execute(EngageBattle()).accepted
    assert game.battle is not None


def maintain(game: GameSession, caster_id: str) -> str:
    """Give the caster a concentration spell to maintain: a real ledger effect listed under the caster."""
    effect_id = attach(game, EffectDefinition(kind="maintained_spell", permanent=True), caster_id)
    game.battle.concentration[caster_id] = [effect_id]
    return effect_id


def in_ledger(game: GameSession, effect_id: str) -> bool:
    return any(effect.effect_id == effect_id for effect in game.ledger.effects)


def concentration_of(game: GameSession, caster_id: str) -> list[str]:
    return list(game.battle.concentration.get(caster_id, [])) if game.battle is not None else []


# ---------------------------------------------------------------------- decision 1


class TestTurningEndsConcentration:
    """Turning the undead ends the cleric's concentration.

    The OSE SRD's concentration rule for devices says it "is broken if the wielder attacks,
    casts a spell, or is successfully attacked". A turning is the cleric's action for the
    round, so it ends a concentration spell the cleric maintains. The battle's own account is
    that concentration ends when the caster declares anything other than `cast` or `hold`.
    """

    def _cleric_in_battle(self) -> tuple[GameSession, Character, str]:
        game = session(members=[hero("Osric", "cleric")])
        open_battle(game, "skeleton", count=2, distance_feet=60)
        osric = game.party.members[0]
        return game, osric, maintain(game, osric.id)

    def test_a_turning_that_resolves_ends_concentration(self):
        game, osric, effect_id = self._cleric_in_battle()
        result = game.execute(
            ResolveBattleRound(declarations=(BattleDeclaration(character_id=osric.id, action="turn_undead"),))
        )
        assert result.accepted
        assert any(event.code.startswith("magic.turning.") for event in result.events)  # the turning ran
        assert not in_ledger(game, effect_id)
        assert effect_id not in concentration_of(game, osric.id)
        assert any(event.code == "effects.effect.released" and event.effect_id == effect_id for event in result.events)

    def test_holding_keeps_concentration(self):
        game, osric, effect_id = self._cleric_in_battle()
        result = game.execute(
            ResolveBattleRound(declarations=(BattleDeclaration(character_id=osric.id, action="hold"),))
        )
        assert result.accepted
        assert in_ledger(game, effect_id)
        assert concentration_of(game, osric.id) == [effect_id]

    def test_a_declared_turning_ends_concentration_even_when_confusion_stops_it(self):
        # A confused cleric's turning never resolves, so only the declaration can end
        # the concentration. Today `turn_undead` sits beside `cast` and `hold` among the
        # declarations that keep it.
        game, osric, effect_id = self._cleric_in_battle()
        attach(game, EffectDefinition(kind="confusion", permanent=True, condition=Condition.CONFUSED), osric.id)
        result = game.execute(
            ResolveBattleRound(declarations=(BattleDeclaration(character_id=osric.id, action="turn_undead"),))
        )
        assert result.accepted
        assert has_condition(osric, Condition.CONFUSED)  # still confused through the magic phase
        assert not any(event.code.startswith("magic.turning.") for event in result.events)  # no turning ran
        assert not in_ledger(game, effect_id)
        assert effect_id not in concentration_of(game, osric.id)


# ---------------------------------------------------------------------- decision 2


class TestOneConcentrationSpellAtATime:
    """A new concentration spell ends the one the caster already maintains.

    A caster maintains one concentration spell at a time. Every concentration spell in the
    built-in catalog is a manual mode that attaches no effect, so the maintained spell here
    is a ledger effect listed under the caster in `BattleState.concentration`.
    """

    def _mage_in_battle(self) -> tuple[GameSession, Character, str]:
        game = session(members=[mage()])
        open_battle(game, "goblin", count=2, distance_feet=120)
        mira = game.party.members[0]
        return game, mira, maintain(game, mira.id)

    @staticmethod
    def _cast_resolved(result, caster_id: str, spell_id: str) -> bool:
        resolved = any(
            event.code == "magic.cast.cast"
            and getattr(event, "caster_id", None) == caster_id
            and event.spell_id == spell_id
            for event in result.events
        )
        lost = any(event.code in ("magic.cast.disrupted", "magic.cast.fizzled") for event in result.events)
        return resolved and not lost

    def test_casting_a_second_concentration_spell_ends_the_first(self):
        assert load_spells().get("phantasmal_force").duration_spec.kind == "concentration"
        game, mira, effect_id = self._mage_in_battle()
        result = game.execute(
            ResolveBattleRound(
                declarations=(
                    BattleDeclaration(
                        character_id=mira.id, action="cast", spell_id="phantasmal_force", spell_mode="cast"
                    ),
                )
            )
        )
        assert result.accepted
        assert self._cast_resolved(result, mira.id, "phantasmal_force")
        assert not in_ledger(game, effect_id)
        assert effect_id not in concentration_of(game, mira.id)
        assert any(event.code == "effects.effect.released" and event.effect_id == effect_id for event in result.events)

    def test_casting_a_spell_that_needs_no_concentration_keeps_it(self):
        missile = load_spells().get("magic_missile")
        assert missile.duration_spec.kind != "concentration"
        game, mira, effect_id = self._mage_in_battle()
        goblin_id = game.encounter.groups[0].monster_ids[0]
        result = game.execute(
            ResolveBattleRound(
                declarations=(
                    BattleDeclaration(
                        character_id=mira.id,
                        action="cast",
                        spell_id="magic_missile",
                        spell_mode=missile.modes[0].key,
                        targets=(goblin_id,),
                    ),
                )
            )
        )
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert self._cast_resolved(result, mira.id, "magic_missile")
        assert in_ledger(game, effect_id)
        assert concentration_of(game, mira.id) == [effect_id]


# ---------------------------------------------------------------------- decision 3


class TestCureLightWoundsLiftsParalysis:
    """*Cure light wounds* removes paralysis however it was caused.

    The OSE SRD's *cure light wounds* page: "Curing paralysis: Paralysing effects are
    negated." The spell removes the condition itself, not only a ledger effect behind it.
    """

    @staticmethod
    def _cast(brand: Character, ledger: EffectsLedger | None = None, registry: dict | None = None):
        aldis = hero("Aldis", "cleric")
        aldis.id, aldis.level = "pc-1", 2
        memorize_spells(
            aldis, load_classes().get("cleric"), load_spells(), [MemorizedSpell(spell_id="cure_light_wounds")]
        )
        streams = RngStreams(master_seed=7)
        return cast_spell(
            aldis,
            load_spells().get("cure_light_wounds"),
            "cure_paralysis",
            profile=caster_profile(load_classes().get("cleric")),
            targets=[brand],
            ledger=ledger or EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={"pc-1": aldis, "pc-2": brand, **(registry or {})},
            ruleset=Ruleset(),
            stream=streams.get(MAGIC_STREAM),
            effects_stream=streams.get(EFFECTS_STREAM),
        )

    @staticmethod
    def _brand() -> Character:
        brand = hero("Brand", seed=8)
        brand.id = "pc-2"
        return brand

    @staticmethod
    def _condition_removed(result, condition: Condition) -> bool:
        return any(
            event.code == "effects.condition.removed"
            and event.target_id == "pc-2"
            and event.condition == condition.value
            for event in result.events
        )

    def test_lifts_paralysis_granted_without_an_effect(self):
        brand = self._brand()
        grant_condition(brand, Condition.PARALYSED, None)
        assert has_condition(brand, Condition.PARALYSED)
        result = self._cast(brand)
        assert not has_condition(brand, Condition.PARALYSED)
        assert not result.no_effect
        assert self._condition_removed(result, Condition.PARALYSED)

    def test_lifts_paralysis_whose_effect_the_ledger_does_not_hold(self):
        brand = self._brand()
        grant_condition(brand, Condition.PARALYSED, "effect-0099")  # an owner the ledger never saw
        result = self._cast(brand)
        assert not has_condition(brand, Condition.PARALYSED)
        assert not result.no_effect
        assert self._condition_removed(result, Condition.PARALYSED)

    def test_lifts_only_paralysis(self):
        brand = self._brand()
        grant_condition(brand, Condition.PARALYSED, None)
        grant_condition(brand, Condition.BLIND, None)
        self._cast(brand)
        assert not has_condition(brand, Condition.PARALYSED)
        assert has_condition(brand, Condition.BLIND)

    def test_lifts_paralysis_from_a_ledger_effect(self):
        brand = self._brand()
        ledger = EffectsLedger()
        effect, _ = ledger.attach(
            EffectDefinition(kind="ghoul_paralysis", permanent=True, condition=Condition.PARALYSED),
            brand.id,
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={"pc-2": brand},
        )
        assert has_condition(brand, Condition.PARALYSED)
        result = self._cast(brand, ledger)
        assert not has_condition(brand, Condition.PARALYSED)
        assert not result.no_effect
        assert not ledger.active_on(brand.id)
        assert any(
            event.code == "effects.effect.released" and event.effect_id == effect.effect_id for event in result.events
        )

    def test_a_target_who_is_not_paralysed_gets_no_effect(self):
        brand = self._brand()
        grant_condition(brand, Condition.BLIND, None)
        result = self._cast(brand)
        assert result.no_effect
        assert has_condition(brand, Condition.BLIND)


# ---------------------------------------------------------------------- decision 4


def temple_session() -> GameSession:
    """A session in town with two members and 5,000 gp more in the first member's purse."""
    game = session(members=[hero("Hild"), hero("Brand", seed=8)], enter=False)
    assert game.execute(GrantCoins(character_id=game.party.members[0].id, coins=Coins(gp=5000))).accepted
    return game


def party_gp(game: GameSession) -> int:
    return sum(member.inventory.purse.gp for member in game.party.members)


def fingerprint(game: GameSession) -> dict:
    """Everything a refused purchase must leave alone: purses, members, effects, streams, clock, and mode."""
    return {
        "members": [member.model_dump(mode="json") for member in game.party.members],
        "ledger": [effect.model_dump(mode="json") for effect in game.ledger.effects],
        "streams": {key: state.model_dump(mode="json") for key, state in game.streams.export_states().items()},
        "rounds": game.clock.rounds,
        "mode": game.mode,
        "death_records": dict(game.death_records),
    }


class TestTempleRefusesAUselessService:
    """The temple refuses a service that would do nothing, before it takes payment.

    *Raise dead* on a living member, *cure disease* on a member with no disease, and
    *neutralize poison* on a member who isn't poisoned are refused with
    `town.healing.no_effect`. A refusal takes no payment, no draws, no time, and changes
    no state, like every other rejected command.
    """

    @pytest.mark.parametrize("service", ["raise_dead", "cure_disease", "neutralize_poison"])
    def test_a_service_that_would_do_nothing_is_refused_before_payment(self, service):
        game = temple_session()
        patient = game.party.members[0]
        assert not has_condition(patient, Condition.DEAD)
        assert not has_condition(patient, Condition.DISEASED)
        assert not has_condition(patient, Condition.POISONED)
        before = fingerprint(game)
        result = game.execute(PurchaseHealing(character_id=patient.id, service=service))
        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == [NO_EFFECT_CODE]
        assert not result.events
        assert fingerprint(game) == before

    def test_raise_dead_on_a_living_member_whose_friend_is_dead_is_still_refused(self):
        # Paying for the wrong member is the case the refusal guards: the dead friend
        # doesn't make the living patient's service do anything.
        game = temple_session()
        patient, friend = game.party.members
        friend.current_hp = 0
        grant_condition(friend, Condition.DEAD, None)
        game.death_records[friend.id] = DeathRecord(round=game.clock.rounds, cause="damage")
        before = fingerprint(game)
        result = game.execute(PurchaseHealing(character_id=patient.id, service="raise_dead"))
        assert [rejection.code for rejection in result.rejections] == [NO_EFFECT_CODE]
        assert fingerprint(game) == before

    def test_cure_disease_on_a_diseased_member_is_sold(self):
        game = temple_session()
        patient = game.party.members[0]
        attach(game, EffectDefinition(kind="mummy_rot", permanent=True, condition=Condition.DISEASED), patient.id)
        assert has_condition(patient, Condition.DISEASED)
        gp = party_gp(game)
        result = game.execute(PurchaseHealing(character_id=patient.id, service="cure_disease"))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert not has_condition(patient, Condition.DISEASED)
        assert party_gp(game) == gp - 150

    def test_neutralize_poison_on_a_poisoned_member_is_sold(self):
        game = temple_session()
        patient = game.party.members[0]
        attach(game, EffectDefinition(kind="poison", permanent=True, condition=Condition.POISONED), patient.id)
        assert has_condition(patient, Condition.POISONED)
        gp = party_gp(game)
        result = game.execute(PurchaseHealing(character_id=patient.id, service="neutralize_poison"))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert not has_condition(patient, Condition.POISONED)
        assert party_gp(game) == gp - 150

    def test_neutralize_poison_on_a_member_poison_just_killed_is_sold(self):
        # The spell revives a character dead of poison for up to 10 rounds, so this
        # member is not one the service would do nothing for.
        game = temple_session()
        patient = game.party.members[1]
        patient.current_hp = 0
        grant_condition(patient, Condition.DEAD, None)
        game.death_records[patient.id] = DeathRecord(round=game.clock.rounds, cause="poison")
        gp = party_gp(game)
        result = game.execute(PurchaseHealing(character_id=patient.id, service="neutralize_poison"))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert not has_condition(patient, Condition.DEAD)
        assert party_gp(game) == gp - 150

    def test_raise_dead_on_a_dead_member_is_sold(self):
        game = temple_session()
        patient = game.party.members[1]
        patient.current_hp = 0
        grant_condition(patient, Condition.DEAD, None)
        game.death_records[patient.id] = DeathRecord(round=game.clock.rounds, cause="damage")
        gp = party_gp(game)
        result = game.execute(PurchaseHealing(character_id=patient.id, service="raise_dead"))
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert not has_condition(patient, Condition.DEAD)
        assert party_gp(game) == gp - 1500


# ---------------------------------------------------------------------- decision 5


CHUTE = TransitionSpec(
    kind="chute",
    position=(1, 0),
    to_dungeon_id="crypt",
    to_level_number=2,
    to_position=(0, 0),
    to_facing=Direction.NORTH,
)
"""A chute from level 1 (1, 0) down to level 2 (0, 0)."""

PARALYSIS_SAVE = SaveSpec(category=SaveCategory.PARALYSIS)


def trap_levels(effect: TrapEffect, *, affects: str) -> list[LevelSpec]:
    """Level 1 with a trapped area at (1, 0) that springs on entry, and a one-cell level 2."""
    pit = AreaSpec(
        id="pit", cells=((1, 0),), trap=TrapSpec(kind="room", trigger="enter", affects=affects, effect=effect)
    )
    return [corridor([pit]), LevelSpec(number=2, width=1, height=1, entrance=(0, 0))]


def party_of(size: int) -> list[Character]:
    return [hero("Hild"), hero("Brand", seed=8), hero("Cade", seed=9)][:size]


def step_into_trap(effect: TrapEffect, *, size: int, seed: int, affects: str = "party"):
    game = session(trap_levels(effect, affects=affects), members=party_of(size), seed=seed)
    result = game.execute(MoveParty(direction=Direction.EAST))
    assert result.accepted
    return game, result


def save_codes(result) -> list[str]:
    return [event.code for event in result.events if event.code.startswith("combat.save.")]


def find_seed(effect: TrapEffect, *, size: int, wanted, affects: str = "party") -> int:
    """The first seed whose spring rolls exactly the save results `wanted`."""
    for seed in range(1, 400):
        _, result = step_into_trap(effect, size=size, seed=seed, affects=affects)
        if save_codes(result) == list(wanted):
            return seed
    raise AssertionError(f"no seed in range rolls {wanted}")


def location(game: GameSession) -> tuple[int | None, tuple[int, int] | None]:
    where = game.dungeon_state.location
    return where.level_number, tuple(where.position) if where.position is not None else None


PASSED, FAILED = "combat.save.passed", "combat.save.failed"


class TestChuteNeedsAFailedSave:
    """A chute trap drops the party only if at least one member fails the save.

    The party has one location, so the chute moves everyone or no one. When every save
    passes, nobody moves, and the party stays on the trapped cell.
    """

    def test_one_member_who_saves_stays_put(self):
        effect = TrapEffect(transition=CHUTE, save=PARALYSIS_SAVE)
        seed = find_seed(effect, size=1, wanted=[PASSED])
        game, result = step_into_trap(effect, size=1, seed=seed)
        assert any(event.code == "exploration.trap.sprung" for event in result.events)
        assert location(game) == (1, (1, 0))

    def test_a_party_that_all_save_stays_put(self):
        effect = TrapEffect(transition=CHUTE, save=PARALYSIS_SAVE)
        seed = find_seed(effect, size=3, wanted=[PASSED, PASSED, PASSED])
        game, result = step_into_trap(effect, size=3, seed=seed)
        assert location(game) == (1, (1, 0))

    def test_a_triggerer_who_saves_spares_the_party(self):
        effect = TrapEffect(transition=CHUTE, save=PARALYSIS_SAVE)
        seed = find_seed(effect, size=2, wanted=[PASSED], affects="triggerer")
        game, _ = step_into_trap(effect, size=2, seed=seed, affects="triggerer")
        assert location(game) == (1, (1, 0))

    def test_a_chute_everyone_saves_against_is_a_trap_with_no_chute(self):
        # Determinism: the saves are rolled as before, and passing them costs no further
        # draw and emits nothing more than the same trap would without a chute.
        effect = TrapEffect(transition=CHUTE, save=PARALYSIS_SAVE)
        twin = TrapEffect(save=PARALYSIS_SAVE)
        seed = find_seed(effect, size=2, wanted=[PASSED, PASSED])
        assert find_seed(twin, size=2, wanted=[PASSED, PASSED]) == seed  # the chute changes no draw before it
        chute_game, chute_result = step_into_trap(effect, size=2, seed=seed)
        twin_game, twin_result = step_into_trap(twin, size=2, seed=seed)
        assert [event.model_dump(mode="json") for event in chute_result.events] == [
            event.model_dump(mode="json") for event in twin_result.events
        ]
        assert chute_game.streams.export_states() == twin_game.streams.export_states()
        assert location(chute_game) == location(twin_game) == (1, (1, 0))

    def test_one_failed_save_drops_the_whole_party(self):
        effect = TrapEffect(transition=CHUTE, save=PARALYSIS_SAVE)
        seed = find_seed(effect, size=2, wanted=[PASSED, FAILED])
        game, _ = step_into_trap(effect, size=2, seed=seed)
        assert location(game) == (2, (0, 0))

    def test_a_party_that_all_fail_drops(self):
        effect = TrapEffect(transition=CHUTE, save=PARALYSIS_SAVE)
        seed = find_seed(effect, size=2, wanted=[FAILED, FAILED])
        game, _ = step_into_trap(effect, size=2, seed=seed)
        assert location(game) == (2, (0, 0))

    def test_a_chute_with_no_save_always_drops(self):
        effect = TrapEffect(transition=CHUTE)
        for seed in range(1, 60):
            game, result = step_into_trap(effect, size=2, seed=seed)
            if any(event.code == "exploration.trap.sprung" for event in result.events):
                assert save_codes(result) == []
                assert location(game) == (2, (0, 0))
                return
        raise AssertionError("no seed in range springs the trap")

    def test_the_same_seed_springs_the_same_way_twice(self):
        effect = TrapEffect(transition=CHUTE, save=PARALYSIS_SAVE)
        seed = find_seed(effect, size=3, wanted=[PASSED, PASSED, PASSED])
        first_game, first = step_into_trap(effect, size=3, seed=seed)
        second_game, second = step_into_trap(effect, size=3, seed=seed)
        assert [event.model_dump(mode="json") for event in first.events] == [
            event.model_dump(mode="json") for event in second.events
        ]
        assert first_game.streams.export_states() == second_game.streams.export_states()
        assert location(first_game) == location(second_game)


# ---------------------------------------------------------------------- decision 6


def helpless_goblin(condition: Condition):
    goblin = monster("goblin")
    EffectsLedger().attach(
        EffectDefinition(kind=condition.value, permanent=True, condition=condition),
        goblin.id,
        clock=GameClock(),
        allocator=IdAllocator(),
        registry={goblin.id: goblin},
    )
    assert has_condition(goblin, condition)
    return goblin


def strike(defender, weapon_id: str = "sword", *, distance_feet: int | None = None, seed: int = 1):
    """One attack by a first-level fighter, returning the result and the combat stream after it."""
    stream = RngStreams(master_seed=seed).get(COMBAT_STREAM)
    result = resolve_attack(
        hero(),
        defender,
        load_equipment().get(weapon_id),
        context=AttackContext(distance_feet=distance_feet),
        ruleset=Ruleset(),
        stream=stream,
    )
    return result, stream.export_state()


class TestMeleeHitsAPetrifiedCreature:
    """Melee against a petrified creature hits automatically, as against an asleep or paralysed one.

    The OSE SRD's other combat issues: "Helpless opponents, such as those magically
    paralysed or frozen, can be automatically hit in melee. Only a roll for damage is
    required."
    """

    def test_melee_against_a_petrified_creature_hits_automatically(self):
        result, _ = strike(helpless_goblin(Condition.PETRIFIED))
        assert result.events[0].code == "combat.attack.auto_hit"
        assert result.events[0].roll is None

    def test_the_attack_roll_against_a_petrified_creature_draws_nothing(self):
        goblin = helpless_goblin(Condition.PETRIFIED)
        stream = RngStreams(master_seed=1).get(COMBAT_STREAM)
        before = stream.export_state()
        rolled = attack_roll(
            hero(), goblin, load_equipment().get("sword"), context=AttackContext(), ruleset=Ruleset(), stream=stream
        )
        assert rolled.hit and rolled.auto and rolled.roll is None
        assert stream.export_state() == before

    def test_a_petrified_creature_costs_the_draws_a_paralysed_one_does(self):
        # Determinism: an automatic hit skips the d20 and rolls only damage, so the stream
        # ends where it ends after the same attack on a paralysed creature. (An asleep
        # creature is no yardstick: a blow slays it without a damage roll.)
        petrified, petrified_stream = strike(helpless_goblin(Condition.PETRIFIED))
        paralysed, paralysed_stream = strike(helpless_goblin(Condition.PARALYSED))
        assert paralysed.events[0].code == "combat.attack.auto_hit"
        assert any(event.code == "combat.damage.dealt" for event in paralysed.events)
        assert petrified_stream == paralysed_stream
        assert [event.model_dump(mode="json") for event in petrified.events] == [
            event.model_dump(mode="json") for event in paralysed.events
        ]

    @pytest.mark.parametrize("condition", [Condition.ASLEEP, Condition.PARALYSED])
    def test_melee_against_an_asleep_or_paralysed_creature_hits_automatically(self, condition):
        result, _ = strike(helpless_goblin(condition))
        assert result.events[0].code == "combat.attack.auto_hit"

    @pytest.mark.parametrize("condition", [Condition.PETRIFIED, Condition.ASLEEP, Condition.PARALYSED])
    def test_a_missile_is_still_rolled(self, condition):
        result, _ = strike(helpless_goblin(condition), "short_bow", distance_feet=30)
        assert result.events[0].code in ("combat.attack.hit", "combat.attack.missed")
        assert result.events[0].roll is not None


# ---------------------------------------------------------------------- decision 7


def npc_band(command: SpawnNpcParty, seed: int) -> tuple[GameSession, NpcPartySpawnedEvent]:
    game = session(seed=seed)
    result = game.execute(command)
    assert result.accepted, [rejection.code for rejection in result.rejections]
    roster = next(event for event in result.events if isinstance(event, NpcPartySpawnedEvent))
    return game, roster


class TestSpawnNpcPartyTakesAFixedSize:
    """`SpawnNpcParty` takes a fixed band size in `count_fixed`, beside `count_dice`.

    The field is additive: a command with neither field still rolls the B/X number for its
    kind. Setting both fails validation, as on `SpawnMonsters`.
    """

    @pytest.mark.parametrize("size", [1, 4, 7])
    def test_a_fixed_size_spawns_exactly_that_many(self, size):
        for seed in (3, 11, 29):
            command = SpawnNpcParty(party_kind="basic", count_fixed=size, distance_feet=30)
            _, roster = npc_band(command, seed)
            assert len(roster.npc_ids) == size

    def test_the_field_is_a_positive_integer_or_none(self):
        assert SpawnNpcParty(party_kind="expert", distance_feet=30).count_fixed is None
        with pytest.raises(ValidationError) as caught:
            SpawnNpcParty(party_kind="expert", count_fixed=0, distance_feet=30)
        assert any(error["loc"] == ("count_fixed",) for error in caught.value.errors())

    def test_setting_both_fails_validation(self):
        with pytest.raises(ValidationError):
            SpawnNpcParty(party_kind="basic", count_dice="1d3", count_fixed=2, distance_feet=30)

    def test_a_serialized_fixed_size_round_trips(self):
        command = SpawnNpcParty(party_kind="basic", count_fixed=5, distance_feet=30)
        parsed = parse_command(command.model_dump(mode="json"))
        assert parsed == command
        assert parsed.count_fixed == 5

    def test_neither_field_rolls_the_number_for_the_kind(self):
        command = SpawnNpcParty(party_kind="basic", distance_feet=30)
        sizes = {len(npc_band(command, seed)[1].npc_ids) for seed in range(1, 25)}
        assert sizes <= set(range(5, 9))  # 1d4+4
        assert len(sizes) > 1

    def test_count_dice_still_rolls(self):
        command = SpawnNpcParty(party_kind="basic", count_dice="1d3", distance_feet=30)
        sizes = {len(npc_band(command, seed)[1].npc_ids) for seed in range(1, 25)}
        assert sizes <= {1, 2, 3}
        assert len(sizes) > 1

    def test_a_serialized_command_without_the_field_still_parses(self):
        parsed = parse_command(
            {"command_type": "spawn_npc_party", "party_kind": "expert", "count_dice": "1d2", "distance_feet": 20}
        )
        assert isinstance(parsed, SpawnNpcParty)
        assert parsed.count_dice == "1d2"
