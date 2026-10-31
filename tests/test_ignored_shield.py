"""Acceptance tests for the shield an attack ignores (a crash found in playtesting on osrlib 2.2.0).

The OSE SRD's Other Combat Issues page says of attacking from behind, "AC bonuses from shields are negated." Its
Combat page says a retreating combatant's opponent "ignores any AC bonus due to the combatant's shield". A game's bot
crashed on this rule with seed 100: a monster attacked a retreating member who carried a magic shield, and the kernel
raised `AttributeError` because a `MagicItemInstance` has no `template`.

The contract these tests check:

- When an attack ignores the shield, because the context sets `defender_retreating` or `behind_target`, the attack is
  made against the armour class the defender would have without the shield. That is the shield's whole part of
  `armour_class`. For a mundane shield it's the +1. For a magic shield it's the +1 and the enchantment.
- The rule takes away a shield's bonus and not its penalty. A cursed shield that makes the defender's armour class
  worse still counts.
- A defender with no shield, and an attack that doesn't ignore shields, keep the armour class they have today.
"""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import Character
from osrlib.core.combat import AttackContext, attack_roll
from osrlib.core.events import AttackRolledEvent
from osrlib.core.items import ItemInstance, MagicItemInstance
from osrlib.core.monsters import spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import BattleDeclaration, EquipItem, GrantItem, ResolveBattleRound, SpawnMonsters
from osrlib.crawl.events import ItemAcquiredEvent
from osrlib.data import load_classes, load_equipment, load_monsters
from positions_fixtures import HILD, SEEDS, _opened, named, session

RETREATING = AttackContext(defender_retreating=True)
BEHIND = AttackContext(behind_target=True)
IGNORING = [pytest.param(RETREATING, id="retreating"), pytest.param(BEHIND, id="behind")]


class FixedStream:
    """A stream stub that yields scripted `randbelow` results."""

    def __init__(self, values):
        self.values = list(values)

    def randbelow(self, n):
        return self.values.pop(0) % n


def defender(shield: str | None) -> Character:
    """A fighter in chain mail with scores of 10, carrying `shield`: a catalog id, or None for no shield.

    `"shield"` is the mundane shield. Any other id is a magic shield from the magic item catalog.
    """
    scores = {ability: 10 for ability in AbilityScore}
    definition = load_classes().get("fighter")
    character = Character(
        id="character-0001",
        name="Hild",
        class_id="fighter",
        race=definition.race,
        level=2,
        xp=definition.row(2).xp,
        scores=scores,
        alignment=Alignment.LAWFUL,
        max_hp=12,
        current_hp=12,
    )
    equipment = load_equipment()
    character.inventory.worn_armour = ItemInstance(template=equipment.get("chainmail"))
    if shield == "shield":
        character.inventory.shield = ItemInstance(template=equipment.get("shield"))
    elif shield is not None:
        character.inventory.shield = MagicItemInstance(
            instance_id="magic-item-0001", template_id=shield, base_item_id="shield", identified=True
        )
    return character


def goblin():
    stream = RngStreams(master_seed=3).get("monster_spawn")
    return spawn_monster(load_monsters().get("goblin"), id="monster-0001", stream=stream)


def roll(target, context: AttackContext) -> AttackRolledEvent:
    """One goblin attack on `target` in `context`, with a fixed die, and its event."""
    attack = load_monsters().get("goblin").attacks[0].attacks[0]
    result = attack_roll(goblin(), target, attack, context=context, ruleset=Ruleset(), stream=FixedStream([10]))
    (event,) = result.events
    assert isinstance(event, AttackRolledEvent)
    return event


# ---------------------------------------------------------------------- the shield an attack ignores

# Chain mail is AC 5, and DEX 10 has no modifier. A shield gives +1 and a magic shield adds its enchantment.
SHIELDED_AC = {"shield": 4, "shield_plus_1": 3, "shield_plus_2": 2}
UNSHIELDED_AC = 5


class TestAnIgnoredShieldCountsForNothing:
    """An attack that ignores the shield is made against the armour class the defender would have without it."""

    @pytest.mark.parametrize("context", IGNORING)
    @pytest.mark.parametrize(
        "shield",
        [
            pytest.param("shield", id="mundane"),
            pytest.param("shield_plus_1", id="plus_1"),
            pytest.param("shield_plus_2", id="plus_2"),
        ],
    )
    def test_the_defender_loses_the_shields_whole_part_of_armour_class(self, shield, context):
        target = defender(shield)
        assert target.armour_class == SHIELDED_AC[shield], "precondition: armour_class counts the shield"
        assert defender(None).armour_class == UNSHIELDED_AC, "precondition: chain mail alone"

        ignored = roll(target, context)
        bare = roll(defender(None), context)

        assert ignored.defender_ac == UNSHIELDED_AC
        assert ignored.required == bare.required
        assert ignored.modifier == bare.modifier


class TestACursedShieldKeepsItsPenalty:
    """The SRD negates a shield's AC bonus. A cursed shield that worsens armour class has no bonus to negate."""

    @pytest.mark.parametrize("context", IGNORING)
    @pytest.mark.parametrize(
        ("shield", "cursed_ac"),
        [pytest.param("cursed_shield_minus_2", 6, id="minus_2"), pytest.param("cursed_shield_ac_9", 9, id="ac_9")],
    )
    def test_an_ignored_cursed_shield_still_worsens_armour_class(self, shield, cursed_ac, context):
        target = defender(shield)
        assert target.armour_class == cursed_ac, "precondition: the curse makes armour class worse than chain mail"
        assert cursed_ac > UNSHIELDED_AC

        ignored = roll(target, context)

        assert ignored.defender_ac == cursed_ac


class TestWhatTheRuleLeavesAlone:
    """Guards: nothing changes for a defender with no shield, or for an attack that doesn't ignore shields."""

    @pytest.mark.parametrize("context", IGNORING)
    def test_a_defender_with_no_shield_keeps_their_armour_class(self, context):
        target = defender(None)

        assert roll(target, context).defender_ac == target.armour_class == UNSHIELDED_AC

    @pytest.mark.parametrize("context", IGNORING)
    def test_a_monster_keeps_its_armour_class(self, context):
        target = goblin()

        assert roll(target, context).defender_ac == target.armour_class

    @pytest.mark.parametrize(
        "context",
        [
            pytest.param(AttackContext(), id="plain"),
            pytest.param(AttackContext(distance_feet=5), id="in_melee"),
            pytest.param(AttackContext(target_unaware=True), id="unaware_only"),
        ],
    )
    @pytest.mark.parametrize("shield", ["shield", "shield_plus_1", "shield_plus_2"])
    def test_an_attack_that_doesnt_ignore_the_shield_counts_it(self, shield, context):
        target = defender(shield)

        assert roll(target, context).defender_ac == target.armour_class == SHIELDED_AC[shield]


# ---------------------------------------------------------------------- the playtest's battle


def _shielded_battle(first_seed: int):
    """Hild alone, in plate with a sword and a shield +1 equipped by command, facing two goblins 5' away.

    Returns the first session from `first_seed` on in which neither side was surprised.
    """
    for seed in range(first_seed, SEEDS.stop):
        game = session(seed, (HILD,))
        hild = named(game, "Hild")
        granted = game.execute(GrantItem(character_id=hild, item_id="shield_plus_1"))
        assert granted.accepted
        (shield_id,) = next(event.item_ids for event in granted.events if isinstance(event, ItemAcquiredEvent))
        assert game.execute(EquipItem(character_id=hild, item_id=shield_id)).accepted
        assert isinstance(game.member(hild).inventory.shield, MagicItemInstance), "precondition: a magic shield"
        spawn = SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)
        if _opened(game, game.execute(spawn), surprised=False, engage=True):
            return game
    raise AssertionError("precondition: no seed opened the battle")


def test_a_monster_attacks_a_retreating_member_with_a_magic_shield():
    """The playtest's crash, end to end: Hild retreats, a goblin strikes, and the round completes."""
    seed = SEEDS.start
    while seed < SEEDS.stop:
        game = _shielded_battle(seed)
        seed = game.master_seed + 1
        hild = named(game, "Hild")
        member = game.member(hild)
        shielded_ac = member.armour_class
        assert shielded_ac == 1, "precondition: plate is AC 3, and the shield +1 gives 2 more"

        result = game.execute(
            ResolveBattleRound(declarations=(BattleDeclaration(character_id=hild, action="move", move="retreat"),))
        )

        assert result.accepted, [rejection.code for rejection in result.rejections]
        swings = [
            event
            for event in result.events
            if isinstance(event, AttackRolledEvent) and event.defender_id == hild and event.defender_ac is not None
        ]
        if not swings:
            continue
        assert all(swing.defender_ac == shielded_ac + 2 for swing in swings), "the shield and its +1 both went"
        assert member.armour_class == shielded_ac, "the shield still counts outside the retreat"
        return
    raise AssertionError("precondition: no seed had a goblin attack the retreating member")
