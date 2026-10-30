"""Acceptance tests for the item and rules fixes reported as items 28 to 34 of issue #166.

Each class covers one item. A test marked `xfail(reason="chunk: item-rules")` fails on `main` because of the bug
it names, and a test without the marker pins nearby behavior that is already right and must stay right.

Two of the bugs show only with a magic item template the compiled catalog doesn't ship: a weapon whose attack and
damage bonuses differ, and enchanted body armour whose template names its base. The `custom_magic_items` fixture
adds those templates through the one lookup every core path shares, `osrlib.data.load_magic_items`, the way
`test_advanced_groundwork.py` patches its class catalog.
"""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.alignment import Alignment
from osrlib.core.character import ADVANCEMENT_STREAM, Character
from osrlib.core.clock import GameClock
from osrlib.core.combat import (
    COMBAT_STREAM,
    AttackContext,
    TargetingMode,
    attack_roll,
    damage_roll,
    drain_monster_hd,
    effective_hd,
    melee_modifier_for,
    select_targets,
)
from osrlib.core.effects import ActiveModifier, EffectsLedger
from osrlib.core.items import (
    MAX_LOAD_COINS,
    CoinPurse,
    Coins,
    Inventory,
    MagicItemCatalog,
    MagicItemCategory,
    MagicItemInstance,
    MagicItemTemplate,
    VersusBonus,
    equip,
    equipment_weight_coins,
    magic_item_template,
    movement_rate_feet,
    tracked_weight_coins,
    treasure_weight_coins,
)
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, MonsterInstance, spawn_monster
from osrlib.core.rng import RngStream, RngStreams
from osrlib.core.ruleset import EncumbranceMode, Ruleset
from osrlib.core.spells import MAGIC_STREAM, MemorizedSpell, cast_spell, caster_profile
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, TakeTreasure
from osrlib.crawl.dungeon import DropPile, DungeonSpec, LevelSpec, cell_ref
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_equipment, load_magic_items, load_monsters, load_spells

CHUNK = pytest.mark.xfail(reason="chunk: item-rules", strict=True)

NONE = Ruleset(encumbrance=EncumbranceMode.NONE)
BASIC = Ruleset(encumbrance=EncumbranceMode.BASIC)
DETAILED = Ruleset(encumbrance=EncumbranceMode.DETAILED)

GAUNTLETS_BONUS = 1000
"""The OSE SRD's Gauntlets of Ogre Power page: "The character's ability to carry weight is increased by 1,000
coins. (i.e. the character can carry 1,400 coins without becoming encumbered.)" """


def character(class_id: str = "fighter", *, strength: int = 10, char_id: str = "pc-1") -> Character:
    """A first-level character with every score at 10 except Strength, and no equipment."""
    definition = load_classes().get(class_id)
    scores = dict.fromkeys(AbilityScore, 10)
    scores[AbilityScore.STR] = strength
    return Character(
        id=char_id,
        name=f"Test {class_id}",
        class_id=class_id,
        race=definition.race,
        level=1,
        xp=0,
        scores=scores,
        alignment=Alignment.LAWFUL,
        max_hp=8,
        current_hp=8,
    )


def monster(template_id: str, monster_id: str = "monster-0001", seed: int = 3) -> MonsterInstance:
    """Spawn one monster from the built-in catalog."""
    stream = RngStreams(master_seed=seed).get(MONSTER_SPAWN_STREAM)
    return spawn_monster(load_monsters().get(template_id), id=monster_id, stream=stream)


def combat_stream(seed: int) -> RngStream:
    return RngStreams(master_seed=seed).get(COMBAT_STREAM)


def magic(template_id: str, n: int = 1, **fields) -> MagicItemInstance:
    return MagicItemInstance(instance_id=f"magic-item-{n:04d}", template_id=template_id, **fields)


def worn_gauntlets(inventory: Inventory) -> MagicItemInstance:
    """Put on a pair of gauntlets of ogre power, and check they're worn and carry the printed bonus."""
    gauntlets = magic("gauntlets_of_ogre_power", 99)
    inventory.items.append(gauntlets)
    equip(inventory, load_classes().get("fighter"), gauntlets)
    assert any(item is gauntlets for item in inventory.wielded)
    assert magic_item_template(gauntlets).params["max_load_bonus_coins"] == GAUNTLETS_BONUS
    return gauntlets


@pytest.fixture
def custom_magic_items(monkeypatch) -> MagicItemCatalog:
    """The compiled magic item catalog plus templates no built-in item has.

    - `blade_of_wounding`: a sword +0 to hit and +3 to damage.
    - `blade_of_accuracy`: a sword +3 to hit and +0 to damage.
    - `blade_of_the_crypt`: a sword +1 to hit and +2 to damage, with a +4 alternate against undead.
    - `plate_of_warding`: enchanted plate mail whose template names its base, `plate_mail`.
    """
    base = load_magic_items()
    sword_like = base.get("sword_plus_1")
    extras = (
        MagicItemTemplate(
            id="blade_of_wounding",
            name="Blade of Wounding",
            category=MagicItemCategory.SWORD,
            base_item_id="sword",
            attack_bonus=0,
            damage_bonus=3,
            usable_by=sword_like.usable_by,
        ),
        MagicItemTemplate(
            id="blade_of_accuracy",
            name="Blade of Accuracy",
            category=MagicItemCategory.SWORD,
            base_item_id="sword",
            attack_bonus=3,
            damage_bonus=0,
            usable_by=sword_like.usable_by,
        ),
        MagicItemTemplate(
            id="blade_of_the_crypt",
            name="Blade of the Crypt",
            category=MagicItemCategory.SWORD,
            base_item_id="sword",
            attack_bonus=1,
            damage_bonus=2,
            versus=(VersusBonus(label="+4 vs Undead", bonus=4, categories=("undead",)),),
            usable_by=sword_like.usable_by,
        ),
        MagicItemTemplate(
            id="plate_of_warding",
            name="Plate of Warding",
            category=MagicItemCategory.ARMOUR,
            base_item_id="plate_mail",
            ac_bonus=1,
            usable_by=base.get("armour_plus_1").usable_by,
        ),
    )
    catalog = MagicItemCatalog(**{**dict(base), "items": (*base.items, *extras)})
    monkeypatch.setattr("osrlib.data.load_magic_items", lambda: catalog)
    # The seam reaches the shared lookup, so every core path sees the custom templates.
    assert magic_item_template(magic("blade_of_wounding")).damage_bonus == 3
    return catalog


class TestGauntletsOfOgrePower:
    """Item 28: worn gauntlets raise the carrying capacity by the template's `max_load_bonus_coins`."""

    @CHUNK
    def test_worn_gauntlets_raise_every_detailed_threshold(self):
        # 1,400 coins without becoming encumbered, then each later band 1,000 coins further on.
        for weight, expected in (
            (1400, 120),
            (1401, 90),
            (1600, 90),
            (1601, 60),
            (1800, 60),
            (1801, 30),
            (MAX_LOAD_COINS + GAUNTLETS_BONUS, 30),
        ):
            inventory = Inventory(purse=CoinPurse(gp=weight))
            worn_gauntlets(inventory)
            assert tracked_weight_coins(inventory, EncumbranceMode.DETAILED) == weight  # gauntlets weigh nothing
            assert movement_rate_feet(inventory, DETAILED) == expected, weight

    @CHUNK
    def test_the_issue_reproduction_moves_at_full_rate(self):
        inventory = Inventory(purse=CoinPurse(gp=1300))
        assert movement_rate_feet(inventory, DETAILED) == 30  # bare-handed: past 800, inside 1,600
        worn_gauntlets(inventory)
        assert movement_rate_feet(inventory, DETAILED) == 120

    @CHUNK
    def test_worn_gauntlets_raise_the_maximum_load(self):
        hauler = character(strength=10)
        hauler.inventory.purse.gp = MAX_LOAD_COINS + 1
        assert hauler.movement_rate(DETAILED) == 0  # bare-handed: past the maximum load
        worn_gauntlets(hauler.inventory)
        assert hauler.movement_rate(DETAILED) == 60  # inside the raised 1,600 to 1,800 band
        hauler.inventory.purse.gp = MAX_LOAD_COINS + GAUNTLETS_BONUS + 1
        assert hauler.movement_rate(DETAILED) == 0  # the raised maximum still binds

    @CHUNK
    def test_a_wearer_takes_a_haul_up_to_the_raised_maximum_load(self):
        game = gauntlet_session(worn=True)
        wearer = game.party.members[0]
        before = tracked_weight_coins(wearer.inventory, EncumbranceMode.DETAILED)
        assert before + 2000 <= MAX_LOAD_COINS + GAUNTLETS_BONUS  # room for the whole haul, but only with the bonus
        assert before + 2000 > MAX_LOAD_COINS
        game.dungeon_state.piles[cell_ref("crypt", 1, (0, 0))] = DropPile(coins=Coins(cp=2000))
        assert game.execute(TakeTreasure(feature_id="pile")).accepted
        assert wearer.inventory.purse.cp == 2000
        assert cell_ref("crypt", 1, (0, 0)) not in game.dungeon_state.piles  # nothing left behind
        assert wearer.movement_rate(game.ruleset) > 0

    def test_gauntlets_carried_but_not_worn_add_nothing(self):
        game = gauntlet_session(worn=False)
        carrier = game.party.members[0]
        assert any(item.instance_id == "magic-item-0099" for item in carrier.inventory.items)
        before = tracked_weight_coins(carrier.inventory, EncumbranceMode.DETAILED)
        game.dungeon_state.piles[cell_ref("crypt", 1, (0, 0))] = DropPile(coins=Coins(cp=2000))
        assert game.execute(TakeTreasure(feature_id="pile")).accepted
        assert carrier.inventory.purse.cp == MAX_LOAD_COINS - before
        inventory = Inventory(purse=CoinPurse(gp=1300), items=[magic("gauntlets_of_ogre_power")])
        assert movement_rate_feet(inventory, DETAILED) == 30

    def test_gauntlets_change_nothing_when_encumbrance_is_off(self):
        inventory = Inventory(purse=CoinPurse(gp=5000))
        worn_gauntlets(inventory)
        assert movement_rate_feet(inventory, NONE) == 120


def gauntlet_session(*, worn: bool) -> GameSession:
    """A one-fighter party under detailed encumbrance, in the dungeon at the entrance, with gauntlets of ogre power."""
    member = character(char_id="character-0001")
    gauntlets = magic("gauntlets_of_ogre_power", 99)
    member.inventory.items.append(gauntlets)
    if worn:
        equip(member.inventory, load_classes().get("fighter"), gauntlets)
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    adventure = Adventure(
        name="A",
        town=TownSpec(name="Threshold", travel_turns={"crypt": 1}),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )
    game = GameSession.new(Party(members=[member]), adventure, seed=7, ruleset=DETAILED)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


class TestEnchantedWeaponDamageBonus:
    """Item 29: damage adds the weapon's `damage_bonus`, and the attack roll adds its `attack_bonus`."""

    @staticmethod
    def damage_pair(attacker, template_id: str, defender, seed: int = 3):
        """Roll the same dice with a mundane sword and with the enchanted one, and check the dice match."""
        mundane = damage_roll(
            attacker,
            load_equipment().get("sword"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=combat_stream(seed),
            defender=defender,
        )
        enchanted = damage_roll(
            attacker,
            magic(template_id),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=combat_stream(seed),
            defender=defender,
        )
        assert enchanted.rolls == mundane.rolls and len(mundane.rolls) == 1  # the same d8
        return mundane.total, enchanted.total

    @CHUNK
    def test_damage_adds_the_damage_bonus(self, custom_magic_items):
        assert magic_item_template(magic("blade_of_wounding")).attack_bonus == 0
        mundane, enchanted = self.damage_pair(character(strength=13), "blade_of_wounding", monster("goblin"))
        assert enchanted == mundane + 3

    @CHUNK
    def test_damage_ignores_the_attack_bonus(self, custom_magic_items):
        assert magic_item_template(magic("blade_of_accuracy")).damage_bonus == 0
        mundane, enchanted = self.damage_pair(character(strength=13), "blade_of_accuracy", monster("goblin"))
        assert enchanted == mundane

    @CHUNK
    def test_without_a_versus_match_damage_adds_the_damage_bonus(self, custom_magic_items):
        goblin = monster("goblin")
        assert "undead" not in goblin.template.categories
        mundane, enchanted = self.damage_pair(character(strength=13), "blade_of_the_crypt", goblin)
        assert enchanted == mundane + 2

    def test_a_versus_match_swaps_in_the_alternate_for_damage(self, custom_magic_items):
        skeleton = monster("skeleton")
        assert "undead" in skeleton.template.categories
        mundane, enchanted = self.damage_pair(character(strength=13), "blade_of_the_crypt", skeleton)
        assert enchanted == mundane + 4

    def test_the_attack_roll_adds_the_attack_bonus(self, custom_magic_items):
        attacker, goblin = character(strength=13), monster("goblin")
        rolls = {}
        for template_id in ("sword", "blade_of_accuracy", "blade_of_wounding"):
            attack = load_equipment().get("sword") if template_id == "sword" else magic(template_id)
            rolls[template_id] = attack_roll(
                attacker, goblin, attack, context=AttackContext(), ruleset=Ruleset(), stream=combat_stream(5)
            )
        assert rolls["sword"].natural == rolls["blade_of_accuracy"].natural == rolls["blade_of_wounding"].natural
        assert rolls["blade_of_accuracy"].modifier == rolls["sword"].modifier + 3
        assert rolls["blade_of_wounding"].modifier == rolls["sword"].modifier

    def test_built_in_weapons_with_equal_bonuses_add_their_bonus(self):
        mundane, enchanted = self.damage_pair(character(strength=13), "sword_plus_2", monster("goblin"))
        assert enchanted == mundane + 2


class TestEffectiveHitDiceAfterDrain:
    """Item 31: a drained monster counts its remaining Hit Dice, not its template's."""

    @staticmethod
    def drained(template_id: str, levels: int, monster_id: str = "monster-0001") -> MonsterInstance:
        creature = monster(template_id, monster_id)
        before = creature.hit_dice_count
        drain_monster_hd(creature, levels=levels, stream=RngStreams(master_seed=1).get(ADVANCEMENT_STREAM))
        assert creature.hit_dice_count == before - levels
        assert creature.current_hp > 0
        return creature

    def test_an_undrained_monster_counts_its_template_hit_dice(self):
        assert effective_hd(monster("ogre")) == 4  # 4+1 counts as 4
        assert effective_hd(monster("mountain_lion")) == 3  # 3+2 counts as 3
        assert effective_hd(monster("kobold")) == 1

    @CHUNK
    def test_a_drained_monster_counts_its_remaining_hit_dice(self):
        assert effective_hd(self.drained("ogre", 1)) == 3
        assert effective_hd(self.drained("mountain_lion", 2)) == 1

    @CHUNK
    def test_a_drained_monster_costs_its_remaining_hit_dice_from_a_budget(self):
        ogre = self.drained("ogre", 1)
        goblin = monster("goblin", "monster-0002")
        selected, _ = select_targets(TargetingMode.HD_BUDGET, [ogre, goblin], stream=combat_stream(1), hd_budget=4)
        assert selected == [goblin, ogre]  # 1 + 3 fits a budget of 4

    @CHUNK
    def test_sleep_takes_a_drained_monster_first_as_the_weakest(self):
        lion = self.drained("mountain_lion", 2)  # 3+2 drained to 1+2, which counts as 1
        gnoll = monster("gnoll", "monster-0002")  # 2 HD
        assert effective_hd(gnoll) == 2
        caster = character("magic_user", char_id="pc-mage")
        caster.spell_book = ("sleep",)
        caster.memorized_spells = (MemorizedSpell(spell_id="sleep"),)
        streams = RngStreams(master_seed=11)
        result = cast_spell(
            caster,
            load_spells().get("sleep"),
            "hd_budget",
            profile=caster_profile(load_classes().get("magic_user")),
            targets=[gnoll, lion],
            ledger=EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={caster.id: caster, gnoll.id: gnoll, lion.id: lion},
            ruleset=Ruleset(),
            stream=streams.get(MAGIC_STREAM),
            effects_stream=streams.get("effects"),
        )
        selection = next(event for event in result.events if event.code == "combat.targeting.selected")
        assert selection.target_ids[0] == lion.id  # weakest first: 1 HD before 2


class TestSpentStackWeight:
    """Item 32: a stack at quantity 0 isn't carried, so it weighs nothing."""

    def test_a_live_stack_weighs_each_unit(self):
        assert treasure_weight_coins(Inventory(items=[magic("potion_of_healing")])) == 10
        assert treasure_weight_coins(Inventory(items=[magic("potion_of_healing", quantity=3)])) == 30

    @CHUNK
    def test_a_spent_stack_weighs_nothing(self):
        inventory = Inventory(items=[magic("potion_of_healing", quantity=0)])
        assert inventory.carried_item("potion_of_healing") is None
        assert treasure_weight_coins(inventory) == 0
        assert tracked_weight_coins(inventory, EncumbranceMode.BASIC) == 0
        assert tracked_weight_coins(inventory, EncumbranceMode.DETAILED) == 0

    @CHUNK
    def test_a_spent_stack_does_not_slow_the_carrier(self):
        inventory = Inventory(purse=CoinPurse(gp=400), items=[magic("staff_of_healing", quantity=0)])
        assert inventory.carried_item("staff_of_healing") is None
        assert movement_rate_feet(inventory, DETAILED) == 120  # 400 coins is the top of the 120' band


class TestEnchantedItemBase:
    """Item 33: an enchanted item's base is the instance's `base_item_id`, else the template's, on every path."""

    def test_the_template_names_the_base_the_hand_built_instance_lacks(self):
        hand_built = magic("shield_plus_1")
        assert hand_built.base_item_id is None
        assert magic_item_template(hand_built).base_item_id == "shield"

    def test_a_generated_instance_weighs_its_recorded_base(self):
        assert equipment_weight_coins(Inventory(items=[magic("shield_plus_1", base_item_id="shield")])) == 50
        assert equipment_weight_coins(Inventory(items=[magic("sword_plus_1", base_item_id="sword")])) == 60

    @CHUNK
    def test_a_hand_built_shield_weighs_like_a_generated_one(self):
        hand_built = Inventory(items=[magic("shield_plus_1")])
        generated = Inventory(items=[magic("shield_plus_1", base_item_id="shield")])
        assert equipment_weight_coins(hand_built) == equipment_weight_coins(generated) == 50  # a shield, halved

    @CHUNK
    def test_a_hand_built_sword_weighs_a_sword(self):
        assert equipment_weight_coins(Inventory(items=[magic("sword_plus_1")])) == 60

    @CHUNK
    def test_a_hand_built_sword_slows_a_detailed_carrier(self):
        inventory = Inventory(purse=CoinPurse(gp=360), items=[magic("sword_plus_1")])
        assert movement_rate_feet(inventory, DETAILED) == 90  # 360 + 60 = 420, past the 120' band

    def test_a_hand_built_shield_equips_into_the_shield_slot(self):
        inventory = Inventory(items=[magic("shield_plus_1")])
        shield = inventory.items[0]
        equip(inventory, load_classes().get("fighter"), shield)
        assert inventory.shield is shield

    def test_rolled_armour_moves_at_its_recorded_base_rate(self):
        inventory = Inventory(items=[magic("armour_plus_1", base_item_id="leather")])
        equip(inventory, load_classes().get("fighter"), inventory.items[0])
        assert inventory.worn_armour is not None
        assert movement_rate_feet(inventory, BASIC) == 90  # light armour
        assert equipment_weight_coins(inventory) == 100  # leather 200, halved

    @CHUNK
    def test_worn_armour_whose_template_names_its_base_moves_at_that_rate(self, custom_magic_items):
        inventory = Inventory(items=[magic("plate_of_warding")])
        armour = inventory.items[0]
        assert armour.base_item_id is None
        equip(inventory, load_classes().get("fighter"), armour)
        assert inventory.worn_armour is armour
        assert movement_rate_feet(inventory, BASIC) == 60  # heavy armour
        assert equipment_weight_coins(inventory) == 250  # plate mail 500, halved


class TestDamageRollTotal:
    """Item 34: a damage roll's `modifier` holds every flat amount, so the `RollResult` total rule holds.

    The rule is `total == (sum(rolls) + modifier) * multiplier`. `damage_roll` also raises a total below 1 to 1, so
    a clamped result satisfies `total == max(1, ...)` instead, and these tests state it that way where a roll could
    clamp.
    """

    @staticmethod
    def rule(result) -> int:
        return (sum(result.rolls) + result.modifier) * result.multiplier

    def test_a_plain_monster_weapon_roll_already_obeys_the_rule(self):
        result = damage_roll(
            monster("goblin"),
            load_equipment().get("sword"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=combat_stream(3),
        )
        assert result.rolls and result.modifier == 0 and result.multiplier == 1
        assert result.total == self.rule(result)

    def test_an_effect_only_attack_totals_zero(self):
        wight = monster("wight")
        touch = next(
            attack for routine in wight.template.attacks for attack in routine.attacks if attack.damage is None
        )
        result = damage_roll(wight, touch, context=AttackContext(), ruleset=Ruleset(), stream=combat_stream(3))
        assert result.total == 0 == self.rule(result)

    @CHUNK
    def test_the_strength_modifier_is_in_the_modifier(self):
        fighter = character(strength=16)
        assert melee_modifier_for(fighter) == 2
        result = damage_roll(
            fighter, load_equipment().get("sword"), context=AttackContext(), ruleset=Ruleset(), stream=combat_stream(3)
        )
        assert result.modifier == 2 and result.multiplier == 1
        assert result.total == self.rule(result) == result.rolls[0] + 2

    @CHUNK
    def test_a_magic_weapon_and_a_blessing_join_the_modifier(self):
        fighter = character(strength=16)
        fighter.stat_modifiers = (ActiveModifier(kind="damage_bonus", value=1, effect_id="effect-0001"),)
        result = damage_roll(
            fighter,
            magic("sword_plus_2", base_item_id="sword"),
            context=AttackContext(),
            ruleset=Ruleset(),
            stream=combat_stream(3),
            defender=monster("goblin"),
        )
        assert result.modifier == 2 + 2 + 1  # Strength, the sword's +2, and the blessing
        assert result.total == self.rule(result)

    @CHUNK
    def test_a_back_stab_doubling_is_in_the_multiplier(self):
        thief = character("thief", strength=16)
        result = damage_roll(
            thief,
            load_equipment().get("sword"),
            context=AttackContext(behind_target=True, target_unaware=True),
            ruleset=Ruleset(),
            stream=combat_stream(3),
        )
        assert result.multiplier == 2 and result.modifier == 2
        assert result.total == self.rule(result) == (result.rolls[0] + 2) * 2

    @CHUNK
    def test_a_braced_spear_against_a_charge_doubles_through_the_multiplier(self):
        fighter = character(strength=16)
        result = damage_roll(
            fighter,
            load_equipment().get("spear"),
            context=AttackContext(braced=True),
            ruleset=Ruleset(),
            stream=combat_stream(3),
        )
        assert result.multiplier == 2
        assert result.total == self.rule(result)

    @CHUNK
    def test_a_fixed_damage_attack_obeys_the_rule(self):
        camel = monster("camel")
        bite = camel.template.attacks[0].attacks[0]
        assert bite.fixed_damage == 1 and bite.damage is None
        result = damage_roll(camel, bite, context=AttackContext(), ruleset=Ruleset(), stream=combat_stream(3))
        assert result.rolls == () and result.total == 1
        assert result.total == self.rule(result)

    @CHUNK
    @pytest.mark.parametrize("strength", [4, 6, 13, 16, 18])
    def test_the_rule_holds_across_strength_and_dice(self, strength):
        fighter = character(strength=strength)
        assert melee_modifier_for(fighter) != 0
        totals = []
        for seed in range(40):
            result = damage_roll(
                fighter,
                load_equipment().get("dagger"),
                context=AttackContext(),
                ruleset=Ruleset(),
                stream=combat_stream(seed),
            )
            assert result.modifier == melee_modifier_for(fighter)
            assert result.total == max(1, self.rule(result))
            totals.append(result.total)
        assert len(set(totals)) > 1  # the dice varied
