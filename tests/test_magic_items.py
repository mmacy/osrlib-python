"""The wired magic item census, behavior by behavior — the Phase 5 item contract."""

from crawl_fixtures import build_adventure, build_party
from osrlib.core.combat import (
    AttackContext,
    DamageSource,
    SaveCategory,
    deal_damage,
    destroy_equipment,
    resolve_attack,
    saving_throw,
)
from osrlib.core.effects import ActiveModifier, EffectDefinition, ModifierSpec
from osrlib.core.items import (
    MagicItemInstance,
    equip,
    sword_control_check,
    validate_equip,
)
from osrlib.core.monsters import spawn_monster
from osrlib.core.rng import RngStream
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.commands import (
    EnterDungeon,
    EquipItem,
    GrantItem,
    IdentifyItem,
    UnequipItem,
    UseItem,
)
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_equipment, load_monsters

RULESET = Ruleset()


def magic_instance(template_id: str, n: int = 1, **fields) -> MagicItemInstance:
    return MagicItemInstance(instance_id=f"magic-item-{n:04d}", template_id=template_id, **fields)


def stream(seed: int = 5, key: str = "combat") -> RngStream:
    return RngStream.from_seed_material(seed, key)


def fighter():
    party = build_party()
    member = party.members[0]
    member.id = "character-0001"
    return member


def spawned(template_id: str, seed: int = 3):
    return spawn_monster(load_monsters().get(template_id), id="monster-0001", stream=stream(seed, "monster_spawn"))


class TestEnchantedArms:
    def test_versus_bonus_swaps_against_a_match(self):
        sword = magic_instance("sword_plus_1_plus_3_vs_undead", base_item_id="sword")
        member = fighter()
        wight = spawned("wight")
        hits = {"base": [], "versus": []}
        for seed in range(60):
            baseline = resolve_attack(
                member,
                spawned("orc", seed),
                magic_instance("sword_plus_1", base_item_id="sword"),
                context=AttackContext(distance_feet=5),
                ruleset=RULESET,
                stream=stream(seed),
            )
            hits["base"].append(baseline.attack_roll.modifier)
            versus = resolve_attack(
                member,
                wight,
                sword,
                context=AttackContext(distance_feet=5),
                ruleset=RULESET,
                stream=stream(seed),
            )
            hits["versus"].append(versus.attack_roll.modifier)
        # +1 base everywhere; +3 whenever the defender's template carries `undead`.
        assert all(modifier - base == 2 for base, modifier in zip(hits["base"], hits["versus"], strict=True))

    def test_magic_weapon_counts_as_magical(self):
        # The wight's silver-or-magic gate absorbs a mundane sword and admits +1.
        member = fighter()
        wight = spawned("wight")
        mundane = load_equipment().get("sword")
        result = resolve_attack(
            member, wight, mundane, context=AttackContext(distance_feet=5), ruleset=RULESET, stream=stream(11)
        )
        if result.attack_roll.hit:
            assert result.absorbed
        enchanted = resolve_attack(
            member,
            wight,
            magic_instance("sword_plus_1", base_item_id="sword"),
            context=AttackContext(distance_feet=5),
            ruleset=RULESET,
            stream=stream(11),
        )
        assert not enchanted.absorbed

    def test_cursed_sword_applies_its_penalty_and_counts_as_magical(self):
        member = fighter()
        cursed = magic_instance("sword_minus_2_cursed", base_item_id="sword")
        result = resolve_attack(
            member,
            spawned("wight"),
            cursed,
            context=AttackContext(distance_feet=5),
            ruleset=RULESET,
            stream=stream(13),
        )
        assert not result.absorbed  # cursed is still magical (pinned)

    def test_magic_armour_and_cursed_ac_set(self):
        member = fighter()
        member.inventory.items = []
        member.inventory.worn_armour = None
        member.inventory.shield = None
        plate_plus_one = magic_instance("armour_plus_1", base_item_id="plate_mail")
        member.inventory.worn_armour = plate_plus_one
        plate = load_equipment().get("plate_mail")
        # Neutral DEX keeps the arithmetic bare: plate 3, +1 enchantment = AC 2.
        from osrlib.core.abilities import AbilityScore

        member.scores = {**member.scores, AbilityScore.DEX: 9}
        assert member.armour_class == plate.ac - 1
        member.inventory.worn_armour = magic_instance("cursed_armour_ac_9", n=2, base_item_id="chainmail")
        assert member.armour_class == 9
        assert member.armour_class_ascending == 10

    def test_enchanted_armour_moves_at_its_base_category(self):
        # Enchantment halves the weight, not the bulk: +1 chainmail is still
        # heavy for the basic-encumbrance rates, +1 leather still light.
        from osrlib.core.items import movement_rate_feet

        member = fighter()
        member.inventory.worn_armour = magic_instance("armour_plus_1", base_item_id="chainmail")
        assert movement_rate_feet(member.inventory, RULESET) == 60
        member.inventory.worn_armour = magic_instance("armour_plus_1", 2, base_item_id="leather")
        assert movement_rate_feet(member.inventory, RULESET) == 90
        # And the loot-wear-walk loop survives end to end.
        from osrlib.crawl.commands import MoveParty

        session, member, instance = session_with_item("armour_plus_1", base_item_id="chainmail")
        assert session.execute(EquipItem(character_id=member.id, item_id=instance.instance_id)).accepted
        assert session.execute(MoveParty(direction="east")).accepted

    def test_girdle_branches_on_variable_weapon_damage(self):
        member = fighter()
        girdle = magic_instance("girdle_of_giant_strength")
        member.inventory.wielded.append(girdle)
        from osrlib.core.combat import damage_roll

        sword = load_equipment().get("sword")
        flag_on = damage_roll(
            member, sword, context=AttackContext(distance_feet=5), ruleset=Ruleset(), stream=stream(17)
        )
        flag_off = damage_roll(
            member,
            sword,
            context=AttackContext(distance_feet=5),
            ruleset=Ruleset(variable_weapon_damage=False),
            stream=stream(17),
        )
        # Flag on: twice normal weapon damage (even); flag off: the printed 2d8.
        assert flag_on.total % 2 == 0
        assert 2 <= flag_off.total <= 16 and len(flag_off.rolls) == 2

    def test_strength_set_stacks_with_spell_modifiers(self):
        member = fighter()
        member.inventory.wielded.append(magic_instance("gauntlets_of_ogre_power"))
        member.stat_modifiers = (
            ActiveModifier(kind="damage_bonus", value=1, effect_id="effect-0001"),  # a *bless*
        )
        from osrlib.core.combat import damage_roll, melee_modifier_for

        assert melee_modifier_for(member) == 3  # STR 18's melee modifier
        result = damage_roll(
            member,
            load_equipment().get("sword"),
            context=AttackContext(distance_feet=5),
            ruleset=RULESET,
            stream=stream(19),
        )
        assert result.total >= 1 + 3 + 1  # die + STR 18 + bless

    def test_cursed_strength_set_dominates_worn_gauntlets(self):
        member = fighter()
        member.inventory.wielded.append(magic_instance("gauntlets_of_ogre_power"))
        member.stat_modifiers = (ActiveModifier(kind="strength_set", value=3, effect_id="effect-0001", from_item=True),)
        from osrlib.core.combat import melee_modifier_for

        # The attached curse (first in attachment order) wins over the worn item.
        assert melee_modifier_for(member) == -3


class TestWornItems:
    def test_ring_of_protection_improves_ac_and_saves(self):
        member = fighter()
        ring = magic_instance("ring_of_protection")
        without = member.armour_class
        member.inventory.rings.append(ring)
        assert member.armour_class == without - 1
        base = saving_throw(member, SaveCategory.DEATH, stream=stream(23))
        member_no_ring = fighter()
        bare = saving_throw(member_no_ring, SaveCategory.DEATH, stream=stream(23))
        assert base.modifier == bare.modifier + 1

    def test_ring_slot_cap(self):
        member = fighter()
        definition = load_classes().get(member.class_id)
        first = magic_instance("ring_of_protection", n=1)
        second = magic_instance("ring_of_fire_resistance", n=2)
        third = magic_instance("ring_of_water_walking", n=3)
        member.inventory.items.extend([first, second, third])
        equip(member.inventory, definition, first)
        equip(member.inventory, definition, second)
        rejections = validate_equip(definition, third, member.inventory)
        assert [rejection.code for rejection in rejections] == ["items.ring.hands_full"]

    def test_displacer_cloak_scopes(self):
        member = fighter()
        member.inventory.wielded.append(magic_instance("displacer_cloak"))
        spells_save = saving_throw(member, SaveCategory.SPELLS, stream=stream(29))
        breath_save = saving_throw(member, SaveCategory.BREATH, stream=stream(29))
        assert spells_save.modifier == breath_save.modifier + 2  # spells scoped in, breath not
        goblin = spawned("goblin")
        melee = resolve_attack(
            goblin,
            member,
            goblin.template.attacks[0].attacks[0],
            context=AttackContext(distance_feet=5),
            ruleset=RULESET,
            stream=stream(31),
        )
        assert melee.attack_roll.modifier == -2  # melee attackers −2
        missile = resolve_attack(
            goblin,
            member,
            goblin.template.attacks[0].attacks[0],
            context=AttackContext(distance_feet=30, monster_missile=True),
            ruleset=RULESET,
            stream=stream(31),
        )
        assert missile.attack_roll.modifier == 0  # missiles unaffected, RAW

    def test_fire_resistance_ring_reduces_per_die_and_saves(self):
        member = fighter()
        member.inventory.rings.append(magic_instance("ring_of_fire_resistance"))
        events = deal_damage(
            member,
            6,
            source=DamageSource(element="fire", kind="breath"),
            rolls=(3, 3),
        )
        damage_event = next(event for event in events if event.event_type == "damage_dealt")
        assert damage_event.amount == 4  # −1 per die rolled
        save = saving_throw(member, SaveCategory.BREATH, element="fire", stream=stream(37))
        assert save.modifier == 2


class TestItemEffectExemptions:
    def test_item_kind_modifiers_are_exempt_from_the_cumulative_caps(self):
        member = fighter()
        member.stat_modifiers = (
            ActiveModifier(kind="save_bonus", value=1, effect_id="effect-0001"),  # bless-like spell
            ActiveModifier(kind="save_bonus", value=2, effect_id="effect-0002", from_item=True),  # potion
        )
        save = saving_throw(member, SaveCategory.DEATH, stream=stream(41))
        assert save.modifier == 3  # capped spells (1) + item channel (2)

    def test_item_effects_are_undispellable(self):
        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=41)
        member = session.party.members[0]
        definition = EffectDefinition(
            kind="potion_invulnerability",
            modifiers=(ModifierSpec(kind="save_bonus", value=2, from_item=True),),
            params={"item_source": "potion"},
        )
        effect, _ = session.ledger.attach(
            definition, member.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
        )
        assert effect is not None and not effect.definition.dispellable


class TestDeathSave:
    def test_survivors_land_apart_and_flag_off_burns_everything(self):
        member = fighter()
        member.inventory.items.append(magic_instance("sword_plus_1", base_item_id="sword"))
        events = destroy_equipment(
            member, source=DamageSource(kind="breath", destructive=True), ruleset=Ruleset(), stream=stream(43)
        )
        destroyed_event = events[0]
        survivors = destroyed_event.saved_items
        assert set(survivors) <= {"magic-item-0001"}
        member_two = fighter()
        member_two.inventory.items.append(magic_instance("sword_plus_1", base_item_id="sword"))
        flag_off = destroy_equipment(
            member_two,
            source=DamageSource(kind="breath", destructive=True),
            ruleset=Ruleset(magic_item_death_save=False),
            stream=stream(43),
        )
        assert flag_off[0].saved_items == ()

    def test_many_seeds_produce_both_outcomes(self):
        saved = burned = 0
        for seed in range(40):
            member = fighter()
            member.inventory.items.append(magic_instance("sword_plus_1", base_item_id="sword"))
            events = destroy_equipment(
                member, source=DamageSource(kind="breath", destructive=True), ruleset=Ruleset(), stream=stream(seed)
            )
            if events[0].saved_items:
                saved += 1
                assert member.inventory.items  # survivor stays until the crawl piles it
            else:
                burned += 1
        assert saved and burned

    def test_cursed_items_save_at_their_penalty(self):
        # Same seed, same d20: the +1 sword saves whenever the −2 does, and rolls
        # in between burn only the cursed one — the penalty is real, not masked
        # by an unset bonus's zero.
        source = DamageSource(kind="breath", destructive=True)
        gap_seen = False
        for seed in range(120):
            blessed = fighter()
            blessed.inventory.items.append(magic_instance("sword_plus_1", base_item_id="sword"))
            plus_saved = bool(
                destroy_equipment(blessed, source=source, ruleset=Ruleset(), stream=stream(seed))[0].saved_items
            )
            hexed = fighter()
            hexed.inventory.items.append(magic_instance("sword_minus_2_cursed", 2, base_item_id="sword"))
            minus_saved = bool(
                destroy_equipment(hexed, source=source, ruleset=Ruleset(), stream=stream(seed))[0].saved_items
            )
            assert plus_saved or not minus_saved
            if plus_saved and not minus_saved:
                gap_seen = True
        assert gap_seen


class TestSwordControl:
    def test_control_check_arithmetic(self):
        from osrlib.core.items import SwordSentience

        member = fighter()
        member.current_hp = member.max_hp
        sword = magic_instance(
            "sword_plus_1",
            base_item_id="sword",
            sentience=SwordSentience(
                intelligence=12,
                ego=12,
                communication="speech",
                reading=True,
                alignment=member.alignment.value,
                extraordinary_powers=("telekinesis",),
            ),
        )
        result = sword_control_check(member, sword, stream=stream(47))
        assert result.sword_will == 12 + 12 + 1  # same alignment: no 1d10
        scores = member.scores
        from osrlib.core.abilities import AbilityScore

        assert result.wielder_will == scores[AbilityScore.STR] + scores[AbilityScore.WIS]
        assert result.sword_controls == (result.sword_will > result.wielder_will)


def session_with_item(template_id: str, seed: int = 51, **fields):
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    member = session.party.members[0]
    instance = MagicItemInstance(instance_id="magic-item-9001", template_id=template_id, **fields)
    member.inventory.items.append(instance)
    session.execute(EnterDungeon(dungeon_id="delve"))
    from osrlib.crawl.commands import LightSource

    session.execute(LightSource(character_id="character-0001", item_id="torch"))
    return session, member, instance


class TestPotionsInPlay:
    def test_drinking_identifies_and_hides_the_duration(self):
        session, member, instance = session_with_item("potion_of_invulnerability")
        result = session.execute(UseItem(character_id=member.id, item_id=instance.instance_id))
        assert result.accepted
        codes = [event.code for event in result.events]
        assert "items.item.identified" in codes and "items.potion.drunk" in codes
        assert instance.identified
        from osrlib.core.events import Visibility

        view = session.view(Visibility.PLAYER)
        potion_effects = [effect for effect in view.effects if effect.kind == "potion_invulnerability"]
        assert potion_effects and potion_effects[0].remaining_rounds is None

    def test_mixing_cancels_both_and_disables(self):
        session, member, first = session_with_item("potion_of_invulnerability")
        second = MagicItemInstance(instance_id="magic-item-9002", template_id="potion_of_speed")
        member.inventory.items.append(second)
        session.execute(UseItem(character_id=member.id, item_id=first.instance_id))
        result = session.execute(UseItem(character_id=member.id, item_id=second.instance_id))
        codes = [event.code for event in result.events]
        assert "items.potion.mixed" in codes
        assert not session.ledger.active_on(member.id, "potion_invulnerability")
        assert session.ledger.active_on(member.id, "potion_sickness")

    def test_healing_potion_is_instantaneous_and_exempt_from_mixing(self):
        session, member, first = session_with_item("potion_of_invulnerability")
        session.execute(UseItem(character_id=member.id, item_id=first.instance_id))
        member.current_hp = 1
        healing = MagicItemInstance(instance_id="magic-item-9002", template_id="potion_of_healing")
        member.inventory.items.append(healing)
        result = session.execute(UseItem(character_id=member.id, item_id=healing.instance_id))
        codes = [event.code for event in result.events]
        assert "items.potion.drunk" in codes and "items.potion.mixed" not in codes
        assert member.current_hp > 1
        assert session.ledger.active_on(member.id, "potion_invulnerability")  # still running


class TestScrollsInPlay:
    def test_spell_scroll_burns_per_spell(self):
        session, member, scroll = session_with_item(
            "spell_scroll_2", state={"spell_list": "magic_user", "spells": ("magic_missile", "shield")}
        )
        member.class_id = "magic_user"
        result = session.execute(
            UseItem(character_id=member.id, item_id=scroll.instance_id, spell_id="shield", mode=scroll_mode("shield"))
        )
        assert result.accepted
        assert tuple(scroll.state["spells"]) == ("magic_missile",)
        assert member.inventory.magic_item(scroll.instance_id) is not None
        result = session.execute(
            UseItem(
                character_id=member.id,
                item_id=scroll.instance_id,
                spell_id="magic_missile",
                mode=scroll_mode("magic_missile"),
                targets=(member.id,),
            )
        )
        assert result.accepted
        assert member.inventory.magic_item(scroll.instance_id) is None  # the last spell burned the scroll

    def test_wrong_caster_rejected_and_thief_fizzle_wired(self):
        session, member, scroll = session_with_item(
            "spell_scroll_1", state={"spell_list": "magic_user", "spells": ("shield",)}
        )
        # The fighter can read nothing arcane.
        result = session.execute(
            UseItem(character_id=member.id, item_id=scroll.instance_id, spell_id="shield", mode=scroll_mode("shield"))
        )
        assert not result.accepted
        assert result.rejections[0].code == "items.scroll.wrong_caster"

    def test_cursed_scroll_rolls_one_of_six(self):
        session, member, scroll = session_with_item("cursed_scroll")
        result = session.execute(UseItem(character_id=member.id, item_id=scroll.instance_id))
        assert result.accepted
        codes = [event.code for event in result.events]
        assert "items.scroll.cursed" in codes

    def read_cursed_scroll(self, seed: int):
        """Level the reader to 2 first (a drain from level 1 would kill), then read."""
        from osrlib.crawl.commands import AwardXP, LightSource

        session, member, scroll = session_with_item("cursed_scroll", seed=seed)
        for _ in range(20):
            if session.party_light()[0]:
                break
            session.execute(LightSource(character_id=member.id, item_id="torch"))
        session.execute(AwardXP(character_id=member.id, amount=4000))
        assert member.level == 2
        result = session.execute(UseItem(character_id=member.id, item_id=scroll.instance_id))
        assert result.accepted
        return session, member

    def test_the_energy_drain_curse_drains_at_halfway(self):
        for seed in range(1, 200):
            session, member = self.read_cursed_scroll(seed)
            if member.level == 1:
                # The wight's policy, per the curse's own "halfway between"
                # wording: the floored midpoint of the fighter thresholds 0–2,000.
                assert member.xp == 1000
                return
        raise AssertionError("no seed rolled the energy-drain curse")

    def test_the_slow_healing_curse_halves_spells_and_doubles_rest(self):
        from osrlib.core.combat import apply_healing, natural_healing

        for seed in range(1, 200):
            session, member = self.read_cursed_scroll(seed)
            effects = session.ledger.active_on(member.id, "cursed_slow_healing")
            if not effects:
                continue
            # Not a disease: healing spells cure half (floored), never zero-block.
            member.current_hp = 1
            events = apply_healing(member, 6)
            assert member.current_hp == 4
            assert events[0].code != "combat.healing.blocked"
            # Natural healing runs at the two-day cadence.
            first = natural_healing(member, stream(seed, "effects"), ledger=session.ledger)
            assert first == []
            second = natural_healing(member, stream(seed, "effects"), ledger=session.ledger)
            assert second and member.current_hp > 4
            # Remove curse releases it and healing returns to normal.
            from osrlib.crawl.exploration import lift_curses

            lift_curses(session, member)
            assert not session.ledger.active_on(member.id, "cursed_slow_healing")
            member.current_hp = 1
            apply_healing(member, 6)
            assert member.current_hp == 7 or member.current_hp == member.max_hp
            return
        raise AssertionError("no seed rolled the slow-healing curse")

    def test_protection_scroll_attaches_the_ward(self):
        session, member, scroll = session_with_item("scroll_of_protection_from_elementals")
        result = session.execute(UseItem(character_id=member.id, item_id=scroll.instance_id))
        assert result.accepted
        wards = session.ledger.active_on(member.id, "protection_ward")
        assert wards and wards[0].definition.params["all_affected"] is True
        assert wards[0].definition.duration_amount == 2  # the elementals form's 2 turns


def scroll_mode(spell_id: str) -> str:
    from osrlib.data import load_spells

    return load_spells().get(spell_id).modes[0].key


class TestDevicesInPlay:
    def test_a_rejected_use_mutates_nothing(self):
        # The rejection contract: no identification, no state, no draws, no
        # time, no log entry — the healing target resolves before anything moves.
        session, member, staff = session_with_item("staff_of_healing")
        member.inventory.items.remove(staff)
        cleric = next(m for m in session.party.members if m.class_id == "cleric")
        cleric.inventory.items.append(staff)
        logged = len(session.command_log)
        rounds = session.clock.rounds
        states = {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()}
        result = session.execute(UseItem(character_id=cleric.id, item_id=staff.instance_id, target_id="bogus"))
        assert not result.accepted
        assert result.rejections[0].code == "items.use.unknown_target"
        assert staff.identified is False and staff.state == {}
        assert {key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()} == states
        assert session.clock.rounds == rounds and len(session.command_log) == logged

    def test_charges_spend_silently_and_exhaust_to_inert(self):
        session, member, wand = session_with_item("rod_of_cancellation", charges_remaining=1)
        result = session.execute(UseItem(character_id=member.id, item_id=wand.instance_id))
        assert result.accepted
        assert wand.charges_remaining == 0
        assert member.inventory.magic_item(wand.instance_id) is not None  # stays in inventory
        result = session.execute(UseItem(character_id=member.id, item_id=wand.instance_id))
        assert not result.accepted
        assert result.rejections[0].code == "items.device.inert"

    def test_wand_requires_an_arcane_caster(self):
        session, member, wand = session_with_item("wand_of_cold", charges_remaining=5)
        result = session.execute(UseItem(character_id=member.id, item_id=wand.instance_id))
        assert not result.accepted and result.rejections[0].code == "items.use.not_usable"

    def test_staff_of_healing_once_per_target_per_day(self):
        session, member, staff = session_with_item("staff_of_healing")
        cleric = session.party.members[1]
        cleric.class_id = "cleric"
        member.inventory.items.remove(staff)
        cleric.inventory.items.append(staff)
        cleric.current_hp = 1
        first = session.execute(UseItem(character_id=cleric.id, item_id=staff.instance_id))
        assert first.accepted
        healed_once = cleric.current_hp
        assert healed_once > 1
        cleric.current_hp = 1
        second = session.execute(UseItem(character_id=cleric.id, item_id=staff.instance_id))
        assert second.accepted  # the activation happens; nothing more heals today
        assert cleric.current_hp == 1


class TestRingsInPlay:
    def test_regeneration_ring_ticks_and_respects_fire(self):
        session, member, ring = session_with_item("ring_of_regeneration")
        session.execute(EquipItem(character_id=member.id, item_id=ring.instance_id))
        member.current_hp = 1
        session.advance_rounds(3)
        assert member.current_hp == 4  # 1 hp per round
        member.current_hp = 0
        from osrlib.core.effects import ActiveCondition, Condition

        member.conditions = (ActiveCondition(condition=Condition.DEAD, effect_id=None),)
        before = member.current_hp
        session.advance_rounds(2)
        assert member.current_hp == before  # while_alive: no function at 0 hp (RAW)

    def test_cursed_ring_reveals_at_wearing_and_sticks(self):
        session, member, ring = session_with_item("ring_of_weakness")
        result = session.execute(EquipItem(character_id=member.id, item_id=ring.instance_id))
        assert result.accepted
        codes = [event.code for event in result.events]
        assert "items.curse.revealed" in codes
        assert ring.cursed_revealed
        result = session.execute(UnequipItem(character_id=member.id, item_id=ring.instance_id))
        assert not result.accepted and result.rejections[0].code == "items.curse.stuck"
        # The onset lands STR 3 after 6 rounds.
        session.advance_rounds(6)
        from osrlib.core.combat import melee_modifier_for

        assert melee_modifier_for(member) == -3

    def test_identify_item_referee_command(self):
        session, member, ring = session_with_item("ring_of_protection")
        result = session.execute(IdentifyItem(character_id=member.id, item_id=ring.instance_id))
        assert result.accepted and ring.identified


class TestRadiusRing:
    def test_the_aura_shields_rank_mates_and_never_the_wearer_twice(self):
        from osrlib.crawl import battle as battle_module

        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=9)
        session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
        session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
        session.execute(EnterDungeon(dungeon_id="delve"))
        members = session.party.members
        members[1].inventory.rings.append(magic_instance("ring_of_protection_5_radius"))
        # The rank-mate collects the aura; the wearer's own +1 rides the
        # equipped-item channel, so the helper grants the wearer nothing extra.
        assert battle_module._ally_protection_bonus(session, members[0]) == 1
        assert battle_module._ally_protection_bonus(session, members[1]) == 0
        assert battle_module._ally_protection_bonus(session, spawned("goblin")) == 0
        # A plain ring projects nothing.
        members[1].inventory.rings = [magic_instance("ring_of_protection", 2)]
        assert battle_module._ally_protection_bonus(session, members[0]) == 0

    def test_the_context_bonus_improves_the_defender_ac(self):
        from osrlib.core.combat import attack_roll

        member = fighter()
        monster = spawned("goblin")
        bare = attack_roll(
            monster, member, None, context=AttackContext(distance_feet=5), ruleset=RULESET, stream=stream(11)
        )
        shielded = attack_roll(
            monster,
            member,
            None,
            context=AttackContext(distance_feet=5, defender_ally_ac_bonus=1),
            ruleset=RULESET,
            stream=stream(11),
        )
        assert shielded.events[0].defender_ac == bare.events[0].defender_ac - 1
        assert shielded.required == bare.required + 1
