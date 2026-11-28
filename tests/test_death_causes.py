"""Acceptance tests for the cause a death record gives (issue #375).

The session records a party member's death with its cause, and *neutralize poison* revives only a
character whose record says poison. The SRD page limits the revival to poison: "A **character** who
has died from poisoning can be revived, if *neutralize poison* is cast within ten rounds". The save
versus death covers death ray and poison alike, so the cause comes from what killed the character,
which [`DeathEvent.poison`][osrlib.core.events.DeathEvent.poison] carries, and never from the
save's category.

Saves are forced where a test needs a failure: a `save_bonus` modifier of -50 makes every save fail.
"""

import pytest
from pydantic import ValidationError

from crawl_fixtures import build_adventure, build_party
from osrlib.core.combat import SaveCategory, resolve_breath
from osrlib.core.effects import Condition as EffectCondition
from osrlib.core.effects import EffectDefinition, ModifierSpec, has_condition
from osrlib.core.events import DeathEvent, Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell, SaveSpec, memorize_spells
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import CastSpell, EnterDungeon, MoveParty, UseItem
from osrlib.crawl.dungeon import AreaSpec, Direction, DungeonSpec, Edge, EdgeKind, LevelSpec, TrapEffect, TrapSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_monsters, load_spells
from osrlib.messages import format_message
from test_monster_attack_effects import _character

ALWAYS_FAIL = -50
"""A save penalty no d20 can pass with."""


def _force_fail(session: GameSession, member_id: str) -> None:
    effect, _ = session.ledger.attach(
        EffectDefinition(kind="test_save_curse", modifiers=(ModifierSpec(kind="save_bonus", value=ALWAYS_FAIL),)),
        member_id,
        clock=session.clock,
        allocator=session.allocator,
        registry=session.registry(),
    )
    assert effect is not None


def _deaths(events, target_id: str) -> list[DeathEvent]:
    return [event for event in events if isinstance(event, DeathEvent) and event.target_id == target_id]


# ---------------------------------------------------------------------- traps


def _trap_session(effect: TrapEffect, seed: int) -> GameSession:
    level = LevelSpec(
        number=1,
        width=2,
        height=1,
        entrance=(0, 0),
        edges={"1,0:west": Edge(kind=EdgeKind.OPEN)},
        areas=(AreaSpec(id="hall", cells=((1, 0),), trap=TrapSpec(kind="room", trigger="enter", effect=effect)),),
    )
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    session = GameSession.new(
        Party(members=[_character(), _character("Wynn", "cleric", level=9)]), adventure, seed=seed
    )
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return session


def _spring(effect: TrapEffect):
    """Walk the party onto a trap that springs and kills the fighter, and return the session and the step's events."""
    for seed in range(1, 80):
        session = _trap_session(effect, seed)
        fighter = session.party.members[0]
        _force_fail(session, fighter.id)
        result = session.execute(MoveParty(direction=Direction.EAST))
        assert result.accepted
        if _deaths(result.events, fighter.id):
            return session, list(result.events)
    pytest.fail("precondition: the trap never sprang")


DEATH_SAVE = SaveSpec(category=SaveCategory.DEATH)


class TestTrapDeaths:
    def test_a_killing_trap_is_not_poison_unless_it_says_so(self):
        session, events = _spring(TrapEffect(kills=True, save=DEATH_SAVE))
        fighter = session.party.members[0]
        assert [death.poison for death in _deaths(events, fighter.id)] == [False]
        assert session.death_records[fighter.id].cause != "poison"

    def test_a_poison_trap_records_a_poison_death(self):
        session, events = _spring(TrapEffect(kills=True, poison=True, save=DEATH_SAVE))
        fighter = session.party.members[0]
        (death,) = _deaths(events, fighter.id)
        assert death.poison is True
        assert death.visibility is Visibility.PLAYER, "the players hear that the needle was poisoned"
        assert session.death_records[fighter.id].cause == "poison"
        assert format_message(death) == f"{fighter.id} is killed by poison."

    def _neutralize(self, session: GameSession):
        cleric = session.party.members[1]
        result = memorize_spells(
            cleric, load_classes().get("cleric"), load_spells(), [MemorizedSpell(spell_id="neutralize_poison")]
        )
        assert result.accepted
        fighter = session.party.members[0]
        return session.execute(
            CastSpell(character_id=cleric.id, spell_id="neutralize_poison", mode="neutralize", targets=(fighter.id,))
        )

    def test_neutralize_poison_does_not_revive_a_victim_of_a_trap_that_is_not_poison(self):
        session, _ = _spring(TrapEffect(kills=True, save=DEATH_SAVE))
        fighter = session.party.members[0]
        self._neutralize(session)
        assert has_condition(fighter, EffectCondition.DEAD)

    def test_neutralize_poison_revives_a_victim_of_a_poison_trap(self):
        session, _ = _spring(TrapEffect(kills=True, poison=True, save=DEATH_SAVE))
        fighter = session.party.members[0]
        result = self._neutralize(session)
        assert result.accepted, [rejection.code for rejection in result.rejections]
        assert not has_condition(fighter, EffectCondition.DEAD)
        assert fighter.current_hp == 1

    def test_poison_needs_a_kill(self):
        with pytest.raises(ValidationError, match="poison"):
            TrapEffect(poison=True, damage_dice="1d4")

    def test_a_document_written_before_the_field_loads_as_not_poison(self):
        effect = TrapEffect.model_validate({"kills": True, "save": {"category": "death"}})
        assert effect.poison is False


# ---------------------------------------------------------------------- spells


class TestSpellDeaths:
    @pytest.mark.parametrize(("spell_id", "reversed_form"), [("disintegrate", False), ("raise_dead", True)])
    def test_a_killing_spell_is_not_poison(self, spell_id, reversed_form):
        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=5)
        caster_class = "cleric" if reversed_form else "magic_user"
        caster = next(member for member in session.party.members if member.class_id == caster_class)
        victim = session.party.members[0]
        caster.level = 12
        if not reversed_form:
            caster.spell_book = (spell_id,)
        result = memorize_spells(
            caster, load_classes().get(caster_class), load_spells(), [MemorizedSpell(spell_id=spell_id)]
        )
        assert result.accepted, [rejection.code for rejection in result.rejections]
        _force_fail(session, victim.id)
        cast = session.execute(
            CastSpell(
                character_id=caster.id, spell_id=spell_id, mode="kill", reversed=reversed_form, targets=(victim.id,)
            )
        )
        assert cast.accepted, [rejection.code for rejection in cast.rejections]
        assert [death.poison for death in _deaths(cast.events, victim.id)] == [False]
        assert session.death_records[victim.id].cause != "poison"


# ---------------------------------------------------------------------- monsters and items


class TestOtherDeaths:
    def test_a_monster_poison_records_a_poison_death(self):
        from test_monster_attack_effects import ALWAYS_FAIL as FAIL
        from test_monster_attack_effects import _battle, _effect_attacks, _force_saves, _until_hit

        game, monster = _battle(4, "pit_viper")
        fighter = game.party.members[0]
        _force_saves(game, fighter, FAIL)
        events = _until_hit(game, fighter, _effect_attacks(monster, "poison"))
        assert [death.poison for death in _deaths(events, fighter.id)] == [True]
        assert game.death_records[fighter.id].cause == "poison"

    def test_a_sea_dragons_poison_spittle_is_poison(self):
        # "Breath weapon: Poison spittle, 100' range, 20' diameter. Save versus breath or die."
        streams = RngStreams(master_seed=3)
        dragon = spawn_monster(
            load_monsters().get("sea_dragon"), id="dragon-1", stream=streams.get(MONSTER_SPAWN_STREAM)
        )
        victim = _character()
        victim.id = "character-0001"
        victim.current_hp = 1
        events = []
        for _ in range(40):
            events = resolve_breath(dragon, [victim], ruleset=Ruleset(), stream=streams.get("combat"))
            dragon.breath_uses_today = 0
            if _deaths(events, victim.id):
                break
        assert [death.poison for death in _deaths(events, victim.id)] == [True]

    def test_a_potion_of_poison_records_a_poison_death(self):
        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=5)
        member = session.party.members[0]
        instance = MagicItemInstance(instance_id="magic-item-9001", template_id="potion_of_poison")
        member.inventory.items.append(instance)
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        _force_fail(session, member.id)
        result = session.execute(UseItem(character_id=member.id, item_id=instance.instance_id))
        assert result.accepted
        assert [death.poison for death in _deaths(result.events, member.id)] == [True]
        assert session.death_records[member.id].cause == "poison"

    @pytest.mark.parametrize("flag", ["true", "false", 1])
    def test_only_a_boolean_poison_param_makes_a_potion_kill_by_poison(self, monkeypatch, flag):
        # `params` allows strings, so a content author's `"poison": "false"` must not read as poison.
        from osrlib.core import items as items_module
        from osrlib.crawl import _magic_items

        real = items_module.magic_item_template(MagicItemInstance(instance_id="x", template_id="potion_of_poison"))
        effect = real.effect.model_copy(update={"params": {**real.effect.params, "poison": flag}})
        altered = real.model_copy(update={"effect": effect})
        monkeypatch.setattr(
            _magic_items,
            "magic_item_template",
            lambda instance: altered if instance.template_id == "potion_of_poison" else real,
        )
        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=5)
        member = session.party.members[0]
        instance = MagicItemInstance(instance_id="magic-item-9001", template_id="potion_of_poison")
        member.inventory.items.append(instance)
        assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
        _force_fail(session, member.id)
        result = session.execute(UseItem(character_id=member.id, item_id=instance.instance_id))
        assert result.accepted
        assert [death.poison for death in _deaths(result.events, member.id)] == [False]
        assert session.death_records[member.id].cause != "poison"
