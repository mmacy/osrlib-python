"""NPC adventuring parties: generation pins, crawl activation, and statistics."""

from collections import Counter

from crawl_fixtures import build_adventure, build_party
from osrlib.core.items import MagicItemInstance
from osrlib.core.monsters import IdAllocator
from osrlib.core.npc import NPC_PARTY_STREAM, generate_npc_party, npc_defeat_xp
from osrlib.core.rng import RngStream
from osrlib.core.treasure import TREASURE_STREAM
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    GrantItem,
    ResolveBattleRound,
    SpawnNpcParty,
)
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_encounter_tables, load_spells

CHI_SQUARE_CRITICAL = {2: 13.82, 7: 24.32}


def chi_square(observed: dict, expected: dict) -> float:
    return sum((observed.get(key, 0) - expected[key]) ** 2 / expected[key] for key in expected)


def party_for(seed: int, kind: str = "expert", count: int = 6):
    return generate_npc_party(
        kind,
        count=count,
        npc_stream=RngStream.from_seed_material(seed, NPC_PARTY_STREAM),
        treasure_stream=RngStream.from_seed_material(seed, TREASURE_STREAM),
        allocator=IdAllocator(),
    )


class TestGeneration:
    def test_levels_respect_the_kind_dice_and_the_caps(self):
        for seed in range(30):
            basic = party_for(seed, kind="basic", count=5)
            assert all(1 <= member.level <= 3 for member in basic.members)
            expert = party_for(seed + 100, kind="expert", count=5)
            for member in expert.members:
                definition = load_classes().get(member.class_id)
                assert 3 <= member.level <= definition.max_level

    def test_one_alignment_roll_for_the_whole_party(self):
        for seed in range(20):
            party = party_for(seed)
            assert len({member.alignment for member in party.members}) == 1
            assert party.alignment is party.members[0].alignment

    def test_kits_and_the_expert_plate_upgrade(self):
        for seed in range(40):
            party = party_for(seed, kind="expert", count=6)
            for member in party.members:
                templates = [
                    instance.template.id
                    for instance in member.inventory.all_instances()
                    if not isinstance(instance, MagicItemInstance)
                ]
                assert "rations_standard" in templates
                assert "waterskin" in templates and "torch" in templates
                if member.class_id in ("cleric", "dwarf", "fighter"):
                    worn = member.inventory.worn_armour
                    if not isinstance(worn, MagicItemInstance):
                        assert worn is not None and worn.template.id == "plate_mail"
                if member.class_id == "dwarf":
                    # The battle axe is two-handed: the shield rides unwielded.
                    assert member.inventory.shield is None or isinstance(member.inventory.shield, MagicItemInstance)

    def test_casters_roll_their_slots_and_books_match(self):
        found_caster = False
        for seed in range(40):
            party = party_for(seed, kind="expert")
            for member in party.members:
                definition = load_classes().get(member.class_id)
                slots = definition.row(member.level).spell_slots
                if not slots:
                    assert member.memorized_spells == ()
                    continue
                found_caster = True
                counts = Counter(load_spells().get(copy.spell_id).level for copy in member.memorized_spells)
                for level, allowed in enumerate(slots, start=1):
                    assert counts.get(level, 0) == allowed
                if member.class_id in ("magic_user", "elf"):
                    assert set(member.spell_book) == {copy.spell_id for copy in member.memorized_spells}
        assert found_caster

    def test_expert_items_respect_usability(self):
        for seed in range(40):
            party = party_for(seed, kind="expert")
            for member in party.members:
                definition = load_classes().get(member.class_id)
                for instance in member.inventory.all_instances():
                    if isinstance(instance, MagicItemInstance):
                        from osrlib.core.npc import _item_usable

                        assert _item_usable(member, definition, instance)

    def test_basic_parties_take_no_items(self):
        for seed in range(20):
            party = party_for(seed, kind="basic", count=6)
            for member in party.members:
                assert not any(isinstance(instance, MagicItemInstance) for instance in member.inventory.all_instances())

    def test_group_treasure_is_u_plus_v(self):
        party = party_for(9)
        assert party.treasure is not None  # rolled once, shared — possibly empty coins

    def test_defeat_xp_is_level_as_hd(self):
        assert npc_defeat_xp(1) == 10
        assert npc_defeat_xp(4) == 75
        assert npc_defeat_xp(9) == 900


class TestCrawlActivation:
    def build_session(self, seed: int) -> GameSession:
        session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
        session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
        session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
        session.execute(EnterDungeon(dungeon_id="delve"))
        return session

    def test_spawn_npc_party_fields_a_side(self):
        session = self.build_session(31)
        result = session.execute(SpawnNpcParty(party_kind="basic", count_dice="1d3", distance_feet=30))
        assert result.accepted
        roster = next(event for event in result.events if event.code == "encounter.npc_party.spawned")
        assert roster.visibility.value == "referee"
        started = next(event for event in result.events if event.code == "encounter.started")
        assert started.count == len(roster.npc_ids)
        assert session.npcs and session.mode.value in ("encounter", "battle")
        for npc_id in roster.npc_ids:
            assert session.combatant(npc_id) is not None

    def test_defeated_npcs_record_level_as_hd_under_npc_ids(self):
        for seed in (31, 44, 57, 68, 71):
            session = self.build_session(seed)
            result = session.execute(SpawnNpcParty(party_kind="basic", count_dice="1d2", distance_feet=10))
            assert result.accepted
            if session.mode.value == "encounter":
                session.execute(EngageBattle())
            rounds = 0
            while session.battle is not None and rounds < 30:
                group = session.encounter.groups[0]
                declarations = []
                living = session.party.living_members()
                for index, member in enumerate(living):
                    if group.distance_feet > 5:
                        declarations.append(
                            BattleDeclaration(
                                character_id=member.id, action="move", move="close", target_group_id=group.id
                            )
                        )
                    elif index < 2:
                        weapon = member.inventory.wielded[0].template.id if member.inventory.wielded else None
                        declarations.append(
                            BattleDeclaration(
                                character_id=member.id, action="attack", target_group_id=group.id, weapon_id=weapon
                            )
                        )
                    else:
                        declarations.append(BattleDeclaration(character_id=member.id, action="hold"))
                outcome = session.execute(ResolveBattleRound(declarations=tuple(declarations)))
                if not outcome.accepted:
                    break
                rounds += 1
            npc_records = [record for record in session.defeated_monsters if record.template_id.startswith("npc:")]
            if npc_records:
                for record in npc_records:
                    npc = session.npcs[record.monster_id]
                    assert record.xp == npc_defeat_xp(npc.level)
                return
        raise AssertionError("no seed produced a defeated NPC")

    def test_wandering_rows_spawn_parties_now(self):
        # The re-roll is gone: a wandering table of npc_party rows must field one.
        from osrlib.core.tables import EncounterTable, EncounterTableRow, NpcPartyEncounterEntry
        from osrlib.crawl import exploration

        rows = tuple(
            EncounterTableRow(
                roll=roll, name="Basic Adventurers", entry=NpcPartyEncounterEntry(party_kind="basic"), count_fixed=2
            )
            for roll in range(1, 21)
        )
        table = EncounterTable(id="all_npc", label="npc", min_level=1, rows=rows)
        adventure = build_adventure(wandering_chance=6)
        level = adventure.dungeons[0].levels[0]
        patched = level.model_copy(update={"wandering": level.wandering.model_copy(update={"table": table})})
        dungeon = adventure.dungeons[0].model_copy(update={"levels": (patched, *adventure.dungeons[0].levels[1:])})
        adventure = adventure.model_copy(update={"dungeons": (dungeon,)})
        session = GameSession.new(build_party(), adventure, seed=17)
        session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
        session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
        session.execute(EnterDungeon(dungeon_id="delve"))
        events, encountered = exploration.wandering_check(session)
        assert encountered
        assert any(event.code == "encounter.npc_party.spawned" for event in events)
        assert session.npcs

    def test_npc_group_morale_is_the_veterans(self):
        from osrlib.crawl import battle as battle_module

        session = self.build_session(83)
        session.execute(SpawnNpcParty(party_kind="basic", count_dice="1d2", distance_feet=30))
        group = session.encounter.groups[0]
        assert battle_module._group_morale_score(session, group) == battle_module.NPC_PARTY_MORALE == 9

    def test_npc_groups_are_intelligent_for_distraction(self):
        from osrlib.crawl import encounter as encounter_module

        session = self.build_session(89)
        session.execute(SpawnNpcParty(party_kind="basic", count_dice="1d2", distance_feet=30))
        group = session.encounter.groups[0]
        assert encounter_module._group_intelligent(session, group)


class TestNpcStatistics:
    def test_class_d8_uniformity(self):
        stream = RngStream.from_seed_material(101, NPC_PARTY_STREAM)
        tables = load_encounter_tables()
        trials = 16_000
        counts = Counter()
        for _ in range(trials):
            roll = stream.randbelow(8) + 1
            row = next(entry for entry in tables.npc_class_levels if entry.roll == roll)
            counts[row.roll] += 1
        expected = {roll: trials / 8 for roll in range(1, 9)}
        assert chi_square(counts, expected) < CHI_SQUARE_CRITICAL[7]

    def test_alignment_bands(self):
        trials = 3_000
        counts = Counter(party_for(seed, count=1).alignment.value for seed in range(trials))
        expected = {"lawful": trials / 3, "neutral": trials / 3, "chaotic": trials / 3}
        assert chi_square(counts, expected) < CHI_SQUARE_CRITICAL[2]
