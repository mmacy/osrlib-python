"""A kernel function checks its arguments before its first draw (issues #162, #166, and #167).

The rule is in the agent guide: a kernel function raises `ValueError` from the call for a bad
argument, so a refused call costs no draws, no time, and no state, and never returns a
plausible-looking result. Each test here passes one bad argument, and checks the error, the random
stream's position, and the state the call could have changed.
"""

import signal
from contextlib import contextmanager

import pytest

from osrlib.core.abilities import AbilityAdjustment, AbilityScore, validate_adjustment
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, choose_starting_spells, create_character
from osrlib.core.classes import detection_chance, level_title, thief_skill_check
from osrlib.core.clock import GameClock
from osrlib.core.combat import (
    COMBAT_STREAM,
    AttackContext,
    DamageSource,
    Participant,
    TargetingMode,
    deal_damage,
    drain_monster_hd,
    resolve_attack,
    resolve_splash_attack,
    roll_initiative,
    select_targets,
)
from osrlib.core.effects import EffectsLedger
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, MonsterCatalog, spawn_monster
from osrlib.core.npc import generate_npc_party, npc_defeat_xp
from osrlib.core.rng import RngStream, RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import select_encounter_individuals
from osrlib.core.treasure import TREASURE_STREAM, MagicItemType, generate_magic_item
from osrlib.crawl.stocking import stock_area
from osrlib.data import load_classes, load_encounter_tables, load_equipment, load_monsters, load_spells

pytestmark = pytest.mark.xfail(reason="chunk: kernel-arguments")


def stream_for(key: str, seed: int = 1) -> RngStream:
    return RngStreams(master_seed=seed).get(key)


def hero(class_id: str = "fighter", seed: int = 7, **choices):
    """Roll a first-level character. `choices` go to `create_character`."""
    return create_character(
        name="Hild",
        class_id=class_id,
        alignment=Alignment.LAWFUL,
        ruleset=Ruleset(),
        stream=stream_for(CHARACTER_CREATION_STREAM, seed),
        **choices,
    ).character


def monster(template_id: str, monster_id: str = "monster-0001"):
    return spawn_monster(load_monsters().get(template_id), id=monster_id, stream=stream_for(MONSTER_SPAWN_STREAM, 3))


@contextmanager
def refused(stream: RngStream | None = None, match: str | None = None):
    """Expect `ValueError`, and check that `stream` is where it was."""
    before = stream.export_state() if stream is not None else None
    with pytest.raises(ValueError, match=match):
        yield
    if stream is not None:
        assert stream.export_state() == before, "the refused call drew from the stream"


@contextmanager
def within(seconds: int = 5):
    """Fail the test instead of hanging when the call under test never returns."""

    def give_up(*_):
        raise TimeoutError(f"still running after {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, give_up)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


# ---------------------------------------------------------------------- create_character (#166, item 1)


@pytest.mark.parametrize(
    ("class_id", "choices"),
    [
        ("fighter", {"extra_languages": ["klingon"]}),
        ("magic_user", {"starting_spell_ids": ["wish"]}),
        ("magic_user", {"starting_spell_ids": ["sleep", "magic_missile"]}),
        ("fighter", {"starting_spell_ids": ["sleep"]}),
        ("fighter", {"purchases": [("vorpal_blade", 1)]}),
        ("fighter", {"equip_ids": ["sword"]}),
    ],
    ids=[
        "an unknown language",
        "an unknown spell",
        "more spells than the book starts with",
        "a spell for a class with no book",
        "an unknown item",
        "an item to equip that isn't bought",
    ],
)
def test_create_character_refuses_a_bad_choice_before_it_rolls(class_id, choices):
    stream = stream_for(CHARACTER_CREATION_STREAM, 2)
    with refused(stream):
        create_character(
            name="Rurik", class_id=class_id, alignment=Alignment.LAWFUL, ruleset=Ruleset(), stream=stream, **choices
        )


def test_create_character_still_accepts_good_choices():
    mage = hero("magic_user", starting_spell_ids=["sleep"], purchases=[("dagger", 1)], equip_ids=["dagger"])
    assert mage.spell_book == ("sleep",)


# ---------------------------------------------------------------------- attacks (#166, items 2 to 4)


def test_resolve_attack_refuses_a_damage_option_the_attack_lacks():
    swarm, goblin = monster("insect_swarm_2"), monster("goblin", "monster-0002")
    attack = swarm.template.attacks[0].attacks[0]
    assert len(attack.fixed_damage_options) == 2
    stream = stream_for(COMBAT_STREAM)
    for option in (2, 5, -1):
        with refused(stream):
            resolve_attack(
                swarm,
                goblin,
                attack,
                context=AttackContext(fixed_damage_option=option),
                ruleset=Ruleset(),
                stream=stream,
            )


def test_resolve_attack_refuses_an_item_that_cant_deal_damage():
    stream = stream_for(COMBAT_STREAM)
    with refused(stream):
        resolve_attack(
            monster("goblin"),
            monster("orc", "monster-0002"),
            load_equipment().get("lantern"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=stream,
        )


def test_resolve_splash_attack_refuses_an_item_that_doesnt_splash():
    goblin, ogre = monster("goblin"), monster("ogre", "monster-0002")
    stream = stream_for(COMBAT_STREAM)
    ledger = EffectsLedger()
    for item_id in ("torch", "sword"):
        with refused(stream):
            resolve_splash_attack(
                goblin,
                ogre,
                load_equipment().get(item_id),
                context=AttackContext(),
                ruleset=Ruleset(),
                stream=stream,
                ledger=ledger,
                clock=GameClock(),
                allocator=IdAllocator(),
                registry={goblin.id: goblin, ogre.id: ogre},
            )
    assert ledger.effects == []
    assert ogre.current_hp == ogre.max_hp


@pytest.mark.parametrize("item_id", ["oil_flask", "holy_water"])
def test_resolve_splash_attack_still_takes_oil_and_holy_water(item_id):
    goblin, ogre = monster("goblin"), monster("ogre", "monster-0002")
    result = resolve_splash_attack(
        goblin,
        ogre,
        load_equipment().get(item_id),
        context=AttackContext(),
        ruleset=Ruleset(),
        stream=stream_for(COMBAT_STREAM),
        ledger=EffectsLedger(),
        clock=GameClock(),
        allocator=IdAllocator(),
        registry={goblin.id: goblin, ogre.id: ogre},
    )
    assert result.attack_roll is not None


# ---------------------------------------------------------------------- calls that never returned (#166, items 5 and 6)


def test_roll_initiative_refuses_two_participants_with_one_key():
    stream = stream_for(COMBAT_STREAM)
    for ruleset in (Ruleset(individual_initiative=True), Ruleset()):
        with within(), refused(stream):
            roll_initiative(
                [Participant(key="a", side="party"), Participant(key="a", side="monsters")],
                ruleset=ruleset,
                stream=stream,
            )


def test_generate_magic_item_refuses_to_exclude_every_kind():
    stream = stream_for(TREASURE_STREAM, 9)
    with within(), refused(stream):
        generate_magic_item(None, tier="basic", stream=stream, allocator=IdAllocator(), exclude=tuple(MagicItemType))


def test_generate_magic_item_still_excludes_some_kinds():
    keep = MagicItemType.POTION
    exclude = tuple(kind for kind in MagicItemType if kind is not keep)
    with within():
        generated = generate_magic_item(
            None, tier="basic", stream=stream_for(TREASURE_STREAM, 9), allocator=IdAllocator(), exclude=exclude
        )
    assert generated is not None


# ---------------------------------------------------------------------- RngStream.randbelow (#166, item 7)


def test_randbelow_names_its_limit():
    stream = stream_for("combat")
    assert 0 <= stream.randbelow(2**64) < 2**64
    with refused(stream, match="64"):
        stream.randbelow(2**64 + 1)
    for bound in (0, -1):
        with refused(stream):
            stream.randbelow(bound)


# ---------------------------------------------------------------------- counts (#162 items 1 and 2, #166 items 8 to 10)


@pytest.mark.parametrize("count", [0, -1])
def test_select_targets_refuses_a_count_below_one(count):
    goblins = [monster("goblin", f"monster-000{number}") for number in (1, 2, 3)]
    stream = stream_for(COMBAT_STREAM)
    with refused(stream):
        select_targets(TargetingMode.UP_TO_N, goblins, stream=stream, count=count)


def test_select_targets_refuses_up_to_n_with_no_count():
    goblins = [monster("goblin", f"monster-000{number}") for number in (1, 2, 3)]
    stream = stream_for(COMBAT_STREAM)
    with refused(stream, match="count"):
        select_targets(TargetingMode.UP_TO_N, goblins, stream=stream)


def test_select_encounter_individuals_refuses_a_negative_count():
    entry = load_encounter_tables().for_level(1).rows[0].entry
    stream = stream_for("wandering", 5)
    with refused(stream):
        select_encounter_individuals(entry, -2, stream)
    assert select_encounter_individuals(entry, 0, stream) == []


@pytest.mark.parametrize("levels", [0, -1])
def test_drain_monster_hd_refuses_levels_below_one(levels):
    ogre = monster("ogre")
    before = (ogre.hit_dice_count, ogre.current_hp, ogre.max_hp)
    stream = stream_for("advancement")
    with refused(stream):
        drain_monster_hd(ogre, levels=levels, stream=stream)
    assert (ogre.hit_dice_count, ogre.current_hp, ogre.max_hp) == before


@pytest.mark.parametrize("count", [0, -3])
def test_generate_npc_party_refuses_a_count_below_one(count):
    streams = RngStreams(master_seed=1)
    npc, treasure = streams.get("npc"), streams.get("treasure")
    treasure_before = treasure.export_state()
    with refused(npc):
        generate_npc_party("basic", count=count, npc_stream=npc, treasure_stream=treasure, allocator=IdAllocator())
    assert treasure.export_state() == treasure_before


def test_generate_npc_party_refuses_an_unknown_kind():
    streams = RngStreams(master_seed=1)
    npc, treasure = streams.get("npc"), streams.get("treasure")
    allocator = IdAllocator()
    with refused(npc, match="wizards"):
        generate_npc_party("wizards", count=3, npc_stream=npc, treasure_stream=treasure, allocator=allocator)
    assert allocator.allocate("monster") == IdAllocator().allocate("monster"), "the refused call used up an id"


# ---------------------------------------------------------------------- amounts and levels (#166, items 11 to 13)


def test_deal_damage_refuses_a_negative_amount():
    goblin = monster("goblin")
    before = goblin.current_hp
    with refused():
        deal_damage(goblin, -10, source=DamageSource())
    assert goblin.current_hp == before


@pytest.mark.parametrize("level", [0, -4])
def test_npc_defeat_xp_refuses_a_level_below_one(level):
    with refused(match="level"):
        npc_defeat_xp(level)


@pytest.mark.parametrize("level", [0, -1])
def test_level_title_refuses_a_level_below_one(level):
    with refused():
        level_title(load_classes().get("fighter"), level)


def test_level_title_past_the_last_title_is_still_none():
    fighter = load_classes().get("fighter")
    assert level_title(fighter, 1) == "Veteran"
    assert level_title(fighter, 14) is None


# ---------------------------------------------------------------------- another class's definition (#166, item 14)


def test_choose_starting_spells_refuses_another_classs_definition():
    fighter = hero()
    with refused():
        choose_starting_spells(fighter, load_classes().get("magic_user"), load_spells(), ["sleep"])
    assert fighter.spell_book == ()


def test_thief_skill_check_refuses_another_classs_definition():
    fighter = hero()
    stream = stream_for("exploration")
    with refused(stream):
        thief_skill_check(fighter, load_classes().get("thief"), "open_locks", stream=stream)


def test_detection_chance_refuses_another_classs_definition():
    with refused():
        detection_chance(hero(), load_classes().get("elf"), "secret_doors")


# ---------------------------------------------------------------------- validate_adjustment (#166, item 15)


def test_validate_adjustment_refuses_a_missing_score():
    trade = AbilityAdjustment(lowered={AbilityScore.INT: 2}, raised={AbilityScore.STR: 1})
    with refused(match="int"):
        validate_adjustment({AbilityScore.STR: 12}, trade, (AbilityScore.STR,))


@pytest.mark.parametrize("score", [0, 2, 19, 30])
def test_validate_adjustment_refuses_scores_no_character_can_roll(score):
    with refused():
        validate_adjustment({ability: score for ability in AbilityScore}, AbilityAdjustment(), (AbilityScore.STR,))


def test_validate_adjustment_still_judges_a_legal_trade():
    scores = {ability: 12 for ability in AbilityScore}
    trade = AbilityAdjustment(lowered={AbilityScore.INT: 2}, raised={AbilityScore.STR: 1})
    assert validate_adjustment(scores, trade, (AbilityScore.STR,)) == []


# ---------------------------------------------------------------------- stock_area (#167, item 12)


@pytest.mark.parametrize("level", [0, -3])
@pytest.mark.parametrize("seed", [0, 7])
def test_stock_area_refuses_a_level_below_one(level, seed):
    stream = RngStream.from_seed_material(seed, "stocking")
    with refused(stream):
        stock_area(level, catalog=load_monsters(), stream=stream)


@pytest.mark.parametrize("seed", [0, 7])
def test_stock_area_refuses_a_catalog_without_the_tables_monsters(seed):
    stream = RngStream.from_seed_material(seed, "stocking")
    with refused(stream):
        stock_area(1, catalog=MonsterCatalog(monsters=()), stream=stream)
