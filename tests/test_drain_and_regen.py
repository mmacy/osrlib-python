"""Two rules fixes: what a ring of regeneration can't heal, and how *sleep* judges a drained monster.

- The ring of regeneration. The OSE SRD's ring page says "Fire or acid damage: Cannot be regenerated." and "Death: The
  ring does not function if the wearer is reduced to 0 or less hit points." A troll already keeps its fire and acid
  damage in a ledger its regeneration never heals. A ring wearer gets the same outcome: the fire and acid damage stays,
  the ring heals the rest at 1 hit point a round, and a dead wearer heals nothing. The block survives a save and a
  load, and a save written before the fix still loads under the same `SCHEMA_VERSION`.
- *Sleep* on a drained monster. The spell takes "a single creature with 4+1 Hit Dice" or "a total of 2d8 Hit Dice of
  creatures of 4 HD or lower each". Energy drain takes Hit Dice away, so *sleep* judges a monster by the Hit Dice it
  has left. A 4+1 ogre drained to 3+1 is no longer mode 1's creature. In mode 2 it is eligible and costs 3 Hit Dice.
  A 5 HD grizzly drained to 4 is eligible for mode 2.
"""

import json

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import ADVANCEMENT_STREAM, Character
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.combat import DamageSource, apply_healing, deal_damage, drain_monster_hd
from osrlib.core.effects import Condition, EffectsLedger, has_condition
from osrlib.core.events import DamageDealtEvent
from osrlib.core.items import MagicItemInstance
from osrlib.core.monsters import IdAllocator, MonsterInstance, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MAGIC_STREAM, CastResult, MemorizedSpell, cast_spell, caster_profile
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import AdvanceTime, CastSpell, EnterDungeon, EquipItem
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_monsters, load_spells
from osrlib.persistence import load_game, save_game
from osrlib.versioning import SCHEMA_VERSION

CHUNK = "chunk: drain-and-regen"

# ---------------------------------------------------------------------- the ring of regeneration

WEARER_HP = 50
"""The wearer's hit points: enough to survive a 5d6 *fire ball* and the ordinary damage after it."""

PRE_FIX_CHARACTER_FIELDS = (
    "alignment",
    "carrying_treasure",
    "class_id",
    "conditions",
    "current_hp",
    "extra_languages",
    "id",
    "inventory",
    "level",
    "max_hp",
    "memorized_spells",
    "name",
    "race",
    "scores",
    "spell_book",
    "stat_modifiers",
    "xp",
)
"""The fields a saved `Character` had before this fix. A save written then carries no others."""


def adventurer(name: str, class_id: str, level: int, hp: int, **fields) -> Character:
    definition = load_classes().get(class_id)
    return Character(
        name=name,
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores={ability: 11 for ability in AbilityScore},
        alignment="lawful",
        max_hp=hp,
        current_hp=hp,
        **fields,
    )


def ring_session(seed: int = 7) -> tuple[GameSession, Character, Character]:
    """A session in a one-cell crypt: a fighter wearing a ring of regeneration and a mage who has *fire ball* ready.

    Returns:
        The session, the wearer, and the mage.
    """
    fighter = adventurer("Brakk", "fighter", 8, WEARER_HP)
    mage = adventurer(
        "Elara",
        "magic_user",
        5,
        14,
        spell_book=("fire_ball",),
        memorized_spells=(MemorizedSpell(spell_id="fire_ball"),),
    )
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    session = GameSession.new(Party(members=[fighter, mage]), adventure, seed=seed)
    wearer, caster = session.party.members
    ring = MagicItemInstance(instance_id="magic-item-9001", template_id="ring_of_regeneration")
    wearer.inventory.items.append(ring)
    assert session.execute(EnterDungeon(dungeon_id="crypt")).accepted
    assert session.execute(EquipItem(character_id=wearer.id, item_id=ring.instance_id)).accepted
    assert regenerating(session, wearer)
    return session, wearer, caster


def regenerating(session: GameSession, member: Character) -> bool:
    """Whether the ring's regeneration effect is running on the member."""
    return any(effect.definition.kind == "regeneration" for effect in session.ledger.active_on(member.id))


def advance(session: GameSession, rounds: int) -> list:
    result = session.execute(AdvanceTime(n=rounds, unit=TimeUnit.ROUND))
    assert result.accepted
    return list(result.events)


def healed(events, member: Character) -> int:
    """The hit points the events report the member regaining."""
    return sum(
        event.amount for event in events if event.code == "combat.healing.applied" and event.target_id == member.id
    )


def hurt(session: GameSession, member: Character, amount: int, element: str | None = None) -> DamageDealtEvent:
    """Deal damage through the kernel and return its damage event. `element=None` is ordinary physical damage."""
    events = deal_damage(member, amount, source=DamageSource(element=element), clock=session.clock)
    dealt = [event for event in events if isinstance(event, DamageDealtEvent)]
    assert len(dealt) == 1 and dealt[0].amount == amount  # nothing reduced it
    return dealt[0]


def fire_ball(session: GameSession, caster: Character, wearer: Character) -> tuple[list, int]:
    """Cast the mage's *fire ball* at the wearer through the session.

    Returns:
        The command's events and the fire damage the wearer took.
    """
    result = session.execute(
        CastSpell(character_id=caster.id, spell_id="fire_ball", mode="damage", targets=(wearer.id,))
    )
    assert result.accepted
    dealt = [event for event in result.events if event.code == "combat.damage.dealt" and event.target_id == wearer.id]
    assert len(dealt) == 1 and "fire" in dealt[0].keys
    assert 1 <= dealt[0].amount < WEARER_HP - 10  # the wearer survives with room for more damage
    return list(result.events), dealt[0].amount


class TestRegenerationRing:
    def test_ordinary_damage_regenerates_one_point_a_round(self):
        session, wearer, _ = ring_session()
        hurt(session, wearer, 3)
        assert wearer.current_hp == WEARER_HP - 3
        events = advance(session, 1)
        assert healed(events, wearer) == 1 and wearer.current_hp == WEARER_HP - 2
        events = advance(session, 5)
        assert healed(events, wearer) == 2 and wearer.current_hp == WEARER_HP  # it stops at the maximum

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_fire_ball_damage_never_regenerates(self):
        session, wearer, caster = ring_session()
        cast_events, fire = fire_ball(session, caster, wearer)
        assert healed(cast_events, wearer) == 0  # the casting round heals none of it
        assert wearer.current_hp == WEARER_HP - fire
        assert healed(advance(session, 10), wearer) == 0
        assert wearer.current_hp == WEARER_HP - fire
        assert regenerating(session, wearer)  # the ring still runs, it has nothing it may heal

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_fire_ball_then_ordinary_damage_regenerates_only_the_ordinary_part(self):
        session, wearer, caster = ring_session()
        _, fire = fire_ball(session, caster, wearer)
        hurt(session, wearer, 5)
        assert wearer.current_hp == WEARER_HP - fire - 5
        events = advance(session, 10)
        assert healed(events, wearer) == 5
        assert wearer.current_hp == WEARER_HP - fire

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    @pytest.mark.parametrize("element", ["fire", "acid"])
    def test_element_damage_never_regenerates(self, element):
        session, wearer, _ = ring_session()
        hurt(session, wearer, 4, element)
        assert wearer.current_hp == WEARER_HP - 4
        assert healed(advance(session, 10), wearer) == 0
        assert wearer.current_hp == WEARER_HP - 4

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    @pytest.mark.parametrize("element", ["fire", "acid"])
    @pytest.mark.parametrize("element_first", [True, False], ids=["element-first", "ordinary-first"])
    def test_ordinary_damage_regenerates_around_element_damage(self, element, element_first):
        session, wearer, _ = ring_session()
        if element_first:
            hurt(session, wearer, 4, element)
            hurt(session, wearer, 3)
        else:
            hurt(session, wearer, 3)
            hurt(session, wearer, 4, element)
        assert wearer.current_hp == WEARER_HP - 7
        events = advance(session, 10)
        assert healed(events, wearer) == 3
        assert wearer.current_hp == WEARER_HP - 4

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    @pytest.mark.parametrize("element", ["fire", "acid"])
    def test_the_damage_event_marks_element_damage_non_regenerable(self, element):
        session, wearer, _ = ring_session()
        assert hurt(session, wearer, 4, element).non_regenerable is True

    def test_the_damage_event_leaves_ordinary_damage_regenerable(self):
        session, wearer, _ = ring_session()
        assert hurt(session, wearer, 4).non_regenerable is False

    def test_the_ring_stops_when_the_wearer_reaches_0_hit_points(self):
        session, wearer, _ = ring_session()
        hurt(session, wearer, WEARER_HP + 5)
        assert wearer.current_hp == 0 and has_condition(wearer, Condition.DEAD)
        assert regenerating(session, wearer)  # the effect is still attached, and does nothing
        assert healed(advance(session, 10), wearer) == 0
        assert wearer.current_hp == 0 and has_condition(wearer, Condition.DEAD)

    def test_fire_damage_healed_another_way_leaves_later_damage_regenerable(self):
        session, wearer, _ = ring_session()
        hurt(session, wearer, 4, "fire")
        apply_healing(wearer, 4)
        assert wearer.current_hp == WEARER_HP
        hurt(session, wearer, 3)
        assert healed(advance(session, 10), wearer) == 3
        assert wearer.current_hp == WEARER_HP

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_the_block_survives_a_save_and_a_load(self):
        session, wearer, _ = ring_session()
        hurt(session, wearer, 4, "fire")
        document = save_game(session)
        assert document["schema_version"] == SCHEMA_VERSION == 4  # the fix adds fields with defaults only
        loaded = load_game(json.loads(json.dumps(document)))
        restored = loaded.member(wearer.id)
        assert restored.current_hp == WEARER_HP - 4 and regenerating(loaded, restored)
        hurt(loaded, restored, 3)
        assert healed(advance(loaded, 10), restored) == 3
        assert restored.current_hp == WEARER_HP - 4

    def test_a_save_from_before_the_fix_loads_and_regenerates(self):
        session, wearer, _ = ring_session()
        hurt(session, wearer, 3)
        document = json.loads(json.dumps(save_game(session)))
        assert document["schema_version"] == SCHEMA_VERSION == 4
        for member in document["payload"]["party"]["members"]:
            for key in [key for key in member if key not in PRE_FIX_CHARACTER_FIELDS]:
                del member[key]
        loaded = load_game(document)
        restored = loaded.member(wearer.id)
        assert restored.current_hp == WEARER_HP - 3 and regenerating(loaded, restored)
        assert healed(advance(loaded, 10), restored) == 3
        assert restored.current_hp == WEARER_HP

    def test_the_same_seed_gives_the_same_game_and_the_ticks_draw_nothing(self):
        def play() -> tuple[dict, dict, dict]:
            session, wearer, caster = ring_session(seed=11)
            fire_ball(session, caster, wearer)
            hurt(session, wearer, 3, "acid")
            hurt(session, wearer, 5)
            before = save_game(session)
            advance(session, 10)
            return before, save_game(session), save_game(session)["payload"]["rng_streams"]

        first_before, first, first_streams = play()
        second_before, second, _ = play()
        assert first == second
        assert first_before == second_before
        assert first_streams == first_before["payload"]["rng_streams"]  # regeneration rolls no dice


# ---------------------------------------------------------------------- sleep on a drained monster

BUDGET_3_SEED = 20_261_064
"""A master seed whose *sleep* rolls exactly 3 on its 2d8 Hit Dice budget."""

BUDGET_4_SEED = 20_261_047
"""A master seed whose *sleep* rolls exactly 4 on its 2d8 Hit Dice budget."""


class SleepScene:
    """One casting of *sleep* by a first-level magic-user, through the kernel."""

    def __init__(self, seed: int):
        self.streams = RngStreams(master_seed=seed)
        self.clock = GameClock()
        self.ledger = EffectsLedger()
        self.allocator = IdAllocator()
        definition = load_classes().get("magic_user")
        self.mage = Character(
            id="pc-mage",
            name="Elara",
            class_id="magic_user",
            race=definition.race,
            level=1,
            xp=0,
            scores={ability: 10 for ability in AbilityScore},
            alignment="lawful",
            max_hp=4,
            current_hp=4,
            spell_book=("sleep",),
            memorized_spells=(MemorizedSpell(spell_id="sleep"),),
        )
        self.registry: dict = {self.mage.id: self.mage}

    def monster(self, template_id: str) -> MonsterInstance:
        instance = spawn_monster(
            load_monsters().get(template_id),
            id=self.allocator.allocate("monster"),
            stream=self.streams.get("monster_spawn"),
        )
        self.registry[instance.id] = instance
        return instance

    def drained(self, template_id: str, levels: int = 1) -> MonsterInstance:
        """Spawn a monster and drain Hit Dice from it, as a wight's touch does."""
        instance = self.monster(template_id)
        before = instance.hit_dice_count
        events = drain_monster_hd(instance, levels=levels, stream=self.streams.get(ADVANCEMENT_STREAM))
        assert events[0].code == "combat.drain.drained"
        assert instance.hit_dice_count == before - levels and instance.drained_hd == levels
        assert not has_condition(instance, Condition.DEAD)
        return instance

    def cast(self, mode: str, targets) -> CastResult:
        result = cast_spell(
            self.mage,
            load_spells().get("sleep"),
            mode,
            profile=caster_profile(load_classes().get("magic_user")),
            targets=targets,
            ledger=self.ledger,
            clock=self.clock,
            allocator=self.allocator,
            registry=self.registry,
            ruleset=Ruleset(),
            stream=self.streams.get(MAGIC_STREAM),
            effects_stream=self.streams.get("effects"),
        )
        assert self.mage.memorized_spells == ()  # the copy is spent whatever happens
        return result


def sleep_budget(seed: int) -> int:
    """The 2d8 budget *sleep* rolls at this seed, read off a cast at sixteen 1 HD goblins."""
    scene = SleepScene(seed)
    goblins = [scene.monster("goblin") for _ in range(16)]
    return len(scene.cast("hd_budget", goblins).affected_ids)


def asleep(monster: MonsterInstance) -> bool:
    return has_condition(monster, Condition.ASLEEP)


class TestSleepOnDrainedMonsters:
    def test_mode_one_takes_an_undrained_ogre(self):
        scene = SleepScene(BUDGET_3_SEED)
        ogre = scene.monster("ogre")
        assert (ogre.template.hit_dice.count, ogre.template.hit_dice.modifier, ogre.drained_hd) == (4, 1, 0)
        result = scene.cast("single_4_plus", [ogre])
        assert result.affected_ids == (ogre.id,) and asleep(ogre)

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_mode_one_has_no_effect_on_an_ogre_drained_to_3_plus_1(self):
        scene = SleepScene(BUDGET_3_SEED)
        ogre = scene.drained("ogre")
        assert ogre.hit_dice_count == 3 and ogre.template.hit_dice.modifier == 1  # now 3+1
        result = scene.cast("single_4_plus", [ogre])
        assert result.no_effect is True
        assert result.affected_ids == ()
        assert not asleep(ogre)

    def test_mode_two_excludes_an_undrained_ogre(self):
        scene = SleepScene(BUDGET_4_SEED)
        assert sleep_budget(BUDGET_4_SEED) == 4
        ogre = scene.monster("ogre")
        result = scene.cast("hd_budget", [ogre])
        assert ogre.id not in result.affected_ids and not asleep(ogre)

    def test_mode_two_takes_an_undrained_3_plus_1_creature_on_a_budget_of_3(self):
        # The calibration for the drained ogre below: a 3+1 bugbear costs 3, and a budget of 3 affords it.
        assert sleep_budget(BUDGET_3_SEED) == 3
        scene = SleepScene(BUDGET_3_SEED)
        bugbear = scene.monster("bugbear")
        assert (bugbear.template.hit_dice.count, bugbear.template.hit_dice.modifier) == (3, 1)
        result = scene.cast("hd_budget", [bugbear])
        assert result.affected_ids == (bugbear.id,) and asleep(bugbear)

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_mode_two_takes_an_ogre_drained_to_3_plus_1_for_3_hit_dice(self):
        assert sleep_budget(BUDGET_3_SEED) == 3  # a 4 HD cost would not fit
        scene = SleepScene(BUDGET_3_SEED)
        ogre = scene.drained("ogre")
        result = scene.cast("hd_budget", [ogre])
        selection = next(event for event in result.events if event.code == "combat.targeting.selected")
        assert selection.target_ids == (ogre.id,)
        assert result.affected_ids == (ogre.id,) and asleep(ogre)

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_mode_two_takes_the_drained_ogre_and_still_excludes_an_undrained_one(self):
        assert sleep_budget(BUDGET_4_SEED) == 4
        scene = SleepScene(BUDGET_4_SEED)
        whole = scene.monster("ogre")
        drained = scene.drained("ogre")
        result = scene.cast("hd_budget", [whole, drained])
        assert result.affected_ids == (drained.id,)
        assert asleep(drained) and not asleep(whole)

    def test_mode_two_excludes_an_undrained_5_hd_grizzly(self):
        assert sleep_budget(BUDGET_4_SEED) == 4
        scene = SleepScene(BUDGET_4_SEED)
        grizzly = scene.monster("grizzly_bear")
        assert (grizzly.template.hit_dice.count, grizzly.template.hit_dice.modifier) == (5, 0)
        result = scene.cast("hd_budget", [grizzly])
        assert result.affected_ids == () and not asleep(grizzly)

    @pytest.mark.xfail(reason=CHUNK, strict=True)
    def test_mode_two_takes_a_grizzly_drained_to_4_hit_dice(self):
        assert sleep_budget(BUDGET_4_SEED) == 4
        scene = SleepScene(BUDGET_4_SEED)
        grizzly = scene.drained("grizzly_bear")
        assert grizzly.hit_dice_count == 4 and grizzly.template.hit_dice.modifier == 0  # now a flat 4
        result = scene.cast("hd_budget", [grizzly])
        assert result.affected_ids == (grizzly.id,) and asleep(grizzly)

    def test_the_same_seed_gives_the_same_cast_at_a_drained_monster(self):
        def play() -> tuple[list[dict], tuple[str, ...], int]:
            scene = SleepScene(BUDGET_4_SEED)
            whole = scene.monster("ogre")
            drained = scene.drained("ogre")
            goblin = scene.monster("goblin")
            result = scene.cast("hd_budget", [whole, drained, goblin])
            assert goblin.id in result.affected_ids  # the cast resolved something
            # The next draw on the magic stream shows both casts drew the same number of times.
            return (
                [event.model_dump(mode="json") for event in result.events],
                tuple(sorted(effect.effect_id for effect in scene.ledger.effects)),
                scene.streams.get(MAGIC_STREAM).randbelow(1_000_000),
            )

        assert play() == play()
