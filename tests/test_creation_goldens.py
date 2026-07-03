"""Creation goldens and the Phase 1 milestone test.

From one master seed: roll and equip a legal 1st-level party of all seven classes,
serialize it with schema and engine version stamps, reload it, and level a character
up — deterministic across runs and platforms. The serialized party document is
compared byte-for-byte (as JSON) against the committed golden file; regenerate it
with `uv run python tests/generate_creation_goldens.py` and explain why in the commit
message.

All creation draws come from the `character_creation` stream and the level-up draw
from the `advancement` stream, so the golden is scoped: a combat- or treasure-rules
change can never invalidate it.

The kits below are canonical purchases costing at most 30 gp (the minimum starting
gold), so they are affordable at any roll. The fighter's adjustment and the extra
language choices are legal for MASTER_SEED's rolled scores specifically — the test
would fail loudly on a seed change, which is the point of a golden.
"""

import json
from pathlib import Path

from osrlib.core.abilities import AbilityAdjustment, AbilityScore
from osrlib.core.character import (
    Alignment,
    CharacterCreationResult,
    create_character,
    party_from_document,
    party_to_document,
)
from osrlib.core.classes import apply_xp
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.data import load_classes
from osrlib.versioning import SCHEMA_VERSION, engine_version

MASTER_SEED = 6
GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase1_party.json"

# class id → (name, alignment, adjustment, extra languages, purchases, equips)
PARTY_PLAN = {
    "cleric": (
        "Adelheid",
        Alignment.LAWFUL,
        None,
        (),
        [("mace", 1), ("leather", 1), ("torch", 1)],
        ["mace", "leather"],
    ),
    "dwarf": (
        "Borin",
        Alignment.LAWFUL,
        None,
        (),
        [("war_hammer", 1), ("leather", 1), ("iron_spikes", 1), ("torch", 1)],
        ["war_hammer", "leather"],
    ),
    "elf": ("Cirdan", Alignment.NEUTRAL, None, (), [("sword", 1), ("leather", 1)], ["sword", "leather"]),
    # Seed 6 rolls the fighter STR 16 / INT 16: lowering INT by 2 to raise STR to 17
    # exercises the adjustment step inside the golden.
    "fighter": (
        "Dagmar",
        Alignment.NEUTRAL,
        AbilityAdjustment(lowered={AbilityScore.INT: 2}, raised={AbilityScore.STR: 1}),
        (),
        [("sword", 1), ("leather", 1)],
        ["sword", "leather"],
    ),
    # Seed 6 rolls the halfling INT 16: two extra languages allowed.
    "halfling": (
        "Elderberry",
        Alignment.LAWFUL,
        None,
        ("dragon", "gnoll"),
        [("sling", 1), ("sling_stones", 1), ("leather", 1), ("rations_standard", 1)],
        ["sling", "leather"],
    ),
    "magic_user": (
        "Falk",
        Alignment.CHAOTIC,
        None,
        (),
        [("dagger", 1), ("oil_flask", 1), ("torch", 1), ("rations_standard", 1), ("waterskin", 1)],
        ["dagger"],
    ),
    # Seed 6 rolls the thief INT 15: one extra language allowed.
    "thief": (
        "Grima",
        Alignment.CHAOTIC,
        None,
        ("kobold",),
        [("dagger", 1), ("leather", 1), ("rope", 1), ("torch", 1)],
        ["dagger", "leather"],
    ),
}


def build_golden_party(streams: RngStreams) -> list[CharacterCreationResult]:
    """Create the canonical seven-class party, in class-id order, from one stream."""
    stream = streams.get("character_creation")
    results = []
    for class_id in sorted(PARTY_PLAN):
        name, alignment, adjustment, extra_languages, purchases, equip_ids = PARTY_PLAN[class_id]
        results.append(
            create_character(
                name=name,
                class_id=class_id,
                alignment=alignment,
                ruleset=Ruleset(),
                stream=stream,
                adjustment=adjustment,
                extra_languages=extra_languages,
                purchases=purchases,
                equip_ids=equip_ids,
            )
        )
    return results


class TestCreationGoldens:
    def test_party_document_matches_golden(self):
        results = build_golden_party(RngStreams(master_seed=MASTER_SEED))
        document = party_to_document([result.character for result in results])
        rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        assert rendered == GOLDEN_PATH.read_text(encoding="utf-8"), (
            "golden mismatch; if the change is intentional, regenerate with "
            "`uv run python tests/generate_creation_goldens.py` and explain why in the commit message"
        )

    def test_document_carries_version_stamps(self):
        document = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        assert document["kind"] == "party"
        assert document["schema_version"] == SCHEMA_VERSION
        assert document["engine_version"] == engine_version()

    def test_adjustment_applied_in_golden(self):
        results = build_golden_party(RngStreams(master_seed=MASTER_SEED))
        fighter = next(result.character for result in results if result.character.class_id == "fighter")
        assert fighter.scores[AbilityScore.STR] == 17
        assert fighter.scores[AbilityScore.INT] == 14
        rolled = next(result for result in results if result.character.class_id == "fighter").ability_rolls
        assert rolled.scores[AbilityScore.STR] == 16
        assert rolled.scores[AbilityScore.INT] == 16

    def test_every_member_is_legal_and_equipped(self):
        results = build_golden_party(RngStreams(master_seed=MASTER_SEED))
        assert [result.character.class_id for result in results] == sorted(PARTY_PLAN)
        for result in results:
            character = result.character
            assert character.level == 1
            assert character.max_hp >= 1
            assert character.inventory.wielded
            if character.class_id != "magic_user":
                assert character.inventory.worn_armour is not None


class TestMilestone:
    def test_roll_equip_serialize_reload_level_up(self):
        streams = RngStreams(master_seed=MASTER_SEED)
        results = build_golden_party(streams)
        characters = [result.character for result in results]

        document = party_to_document(characters)
        reloaded = party_from_document(json.loads(json.dumps(document)))
        assert reloaded == characters

        fighter = next(character for character in reloaded if character.class_id == "fighter")
        hp_before = fighter.max_hp
        award = apply_xp(fighter, load_classes().get("fighter"), 2_500, streams.get("advancement"))
        assert fighter.level == 2
        # Seed 6 fighter: STR 17 after adjustment → +10% XP modifier, floored.
        assert award.modifier_pct == 10
        assert award.modified_award == 2_750
        assert fighter.xp == 2_750
        assert award.level_up.hp_roll is not None
        assert fighter.max_hp == hp_before + award.level_up.hp_gained
        row = load_classes().get("fighter").row(fighter.level)
        assert (row.thac0, row.attack_bonus) == (19, 0)

    def test_deterministic_across_containers(self):
        first = build_golden_party(RngStreams(master_seed=MASTER_SEED))
        second = build_golden_party(RngStreams(master_seed=MASTER_SEED))
        assert [result.character for result in first] == [result.character for result in second]
