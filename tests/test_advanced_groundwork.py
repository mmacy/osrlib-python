"""The Advanced Fantasy groundwork proof: fixture content through the full kernel lifecycle.

The spec's flavor strategy promises Advanced classes, races, and spells as additive
data. This suite proves the landing zone with hand-authored fixtures — invented
mechanics in B/X shape, never scraped from the SRD and never shipped:

- The **warden**, a human divine half-caster with spell slots from level 1, its own
  save table, requirements, two-tier prime requisites, and a `divine_magic` tag naming
  the new spell list `warden`.
- The **gnome**, whose race id exercises the opened `race` field and whose class-ability
  tags (infravision, a detection check) are drawn from the compiled Classic census so
  the fixture feeds the same tag consumers the shipped classes do.
- **Mend wounds**, a reversible spell on the `warden` list, giving the re-keyed divine
  choose-reversal-at-cast freedom its non-cleric test subject.

The authoring path this suite walks — build `ClassDefinition`/`SpellTemplate` models,
round-trip them through JSON into `ClassCatalog`/`SpellCatalog` validation (the
identical path the shipped loaders apply), then drive the stepwise kernel functions
with the definitions in hand — is the path a real Advanced catalog takes through the
SRD pipeline, and seeds the Phase 7 authoring guide.

The one declared seam: `Character` resolves the shipped catalog through the
module-level `load_classes` binding in `core/character.py` (via `Character.definition`)
on every construction, revalidation, and document load. The `fixture_classes` fixture
monkeypatches exactly that binding with a loader returning the shipped catalog extended
by the fixtures. This is a test seam, not a shipped surface — catalog content ships
through the SRD pipeline.
"""

import json

import pytest

from osrlib.core.abilities import AbilityAdjustment, AbilityScore, apply_adjustment, validate_adjustment
from osrlib.core.alignment import Alignment
from osrlib.core.character import (
    Character,
    party_from_document,
    party_to_document,
    roll_ability_scores,
    roll_hit_points,
    validate_class_choice,
    validate_starting_spells,
)
from osrlib.core.classes import ClassCatalog, ClassDefinition, detection_chance, drain_levels, level_up, xp_modifier_pct
from osrlib.core.clock import GameClock
from osrlib.core.effects import EffectsLedger
from osrlib.core.items import equip, purchase, validate_equip
from osrlib.core.monsters import IdAllocator
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import (
    MAGIC_STREAM,
    MemorizedSpell,
    SpellCatalog,
    SpellTemplate,
    cast_spell,
    caster_profile,
    memorize_spells,
    validate_cast,
)
from osrlib.data import load_classes, load_equipment, load_spells

MASTER_SEED = 20_260_704

_WARDEN_SAVES = {"death": 10, "wands": 11, "paralysis": 13, "breath": 15, "spells": 14}
_WARDEN_SAVES_HIGH = {"death": 8, "wands": 9, "paralysis": 11, "breath": 13, "spells": 12}
_GNOME_SAVES = (
    {"death": 8, "wands": 9, "paralysis": 10, "breath": 13, "spells": 12},
    {"death": 6, "wands": 7, "paralysis": 8, "breath": 10, "spells": 9},
    {"death": 4, "wands": 5, "paralysis": 6, "breath": 7, "spells": 6},
)


def _warden_row(level: int, xp: int, slots: tuple[int, ...]) -> dict[str, object]:
    high = level >= 5
    return {
        "level": level,
        "xp": xp,
        "hit_dice": {"count": level, "die": 6},
        "thac0": 17 if high else 19,
        "attack_bonus": 2 if high else 0,
        "saves": _WARDEN_SAVES_HIGH if high else _WARDEN_SAVES,
        "spell_slots": slots,
    }


def build_warden() -> ClassDefinition:
    """The divine half-caster fixture: slots from level 1, a shape no Classic class has."""
    return ClassDefinition.model_validate(
        {
            "id": "warden",
            "name": "Warden",
            "race": "human",
            "requirements": {"wis": 9, "con": 9},
            "prime_requisites": ("wis", "con"),
            "xp_tiers": (
                {"modifier_pct": 10, "minimums": {"wis": 16, "con": 13}},
                {"modifier_pct": 5, "minimums": {"wis": 13, "con": 13}},
            ),
            "hit_die": 6,
            "max_level": 6,
            "armour": {"kind": "leather_only", "shields_allowed": True},
            "weapons": {"kind": "allowed", "weapon_ids": ("mace", "sling", "staff")},
            "languages": ("common",),
            "may_not_lower": ("str",),
            "abilities": (
                {
                    "tag": "divine_magic",
                    "name": "Divine Magic",
                    "prose": "Wardens pray for their spells from 1st level.",
                    "params": {"spell_list": "warden"},
                },
            ),
            "level_titles": ("Watcher", "Keeper", "Warden"),
            "progression": (
                _warden_row(1, 0, (1,)),
                _warden_row(2, 2000, (2,)),
                _warden_row(3, 4000, (2, 1)),
                _warden_row(4, 8000, (3, 1)),
                _warden_row(5, 16_000, (3, 2)),
                _warden_row(6, 32_000, (4, 2)),
            ),
        }
    )


def build_gnome() -> ClassDefinition:
    """The demi-human fixture: an open race id and Classic-census ability tags."""
    thresholds = (0, 2200, 4400, 8800, 17_600, 35_200, 70_400, 140_800)
    progression = []
    for level, xp in enumerate(thresholds, start=1):
        band = 0 if level <= 3 else (1 if level <= 6 else 2)
        progression.append(
            {
                "level": level,
                "xp": xp,
                "hit_dice": {"count": level, "die": 6},
                "thac0": (19, 17, 15)[band],
                "attack_bonus": (0, 2, 4)[band],
                "saves": _GNOME_SAVES[band],
                "spell_slots": (),
            }
        )
    return ClassDefinition.model_validate(
        {
            "id": "gnome",
            "name": "Gnome",
            "race": "gnome",
            "requirements": {"con": 9},
            "prime_requisites": ("int",),
            "xp_tiers": (
                {"modifier_pct": 10, "minimums": {"int": 16}},
                {"modifier_pct": 5, "minimums": {"int": 13}},
                {"modifier_pct": 0, "minimums": {"int": 9}},
                {"modifier_pct": -10, "minimums": {"int": 6}},
                {"modifier_pct": -20, "minimums": {"int": 3}},
            ),
            "hit_die": 6,
            "max_level": 8,
            "armour": {"kind": "any", "shields_allowed": True},
            "weapons": {
                "kind": "forbidden",
                "weapon_ids": ("long_bow", "two_handed_sword"),
                "manual_notes": ("Small or normal sized weapons appropriate to stature.",),
            },
            "languages": ("common", "gnomish"),
            "abilities": (
                {
                    "tag": "infravision",
                    "name": "Infravision",
                    "prose": "Gnomes have infravision to 60 feet.",
                    "params": {"range_feet": 60},
                },
                {
                    "tag": "detect_room_traps",
                    "name": "Detect Room Traps",
                    "prose": "A gnome searching a room notices traps on a 2-in-6.",
                    "params": {"chance_in_six": 2},
                },
            ),
            "progression": tuple(progression),
        }
    )


def build_mend_wounds() -> SpellTemplate:
    """The reversible fixture spell on the `warden` list — the reversal re-key's subject."""
    return SpellTemplate.model_validate(
        {
            "id": "mend_wounds",
            "name": "Mend Wounds",
            "spell_list": "warden",
            "level": 1,
            "duration": "Instant",
            "duration_spec": {"kind": "instant"},
            "range": "The caster or a creature touched",
            "range_spec": {"kind": "touch"},
            "modes": (
                {
                    "key": "mend",
                    "targeting": {"mode": "single"},
                    "effect": {"kind": "heal", "params": {"dice": "1d6+1"}},
                    "prose": "Restores 1d6+1 hit points of damage.",
                },
            ),
            "reversed_form": {
                "name": "Rend Wounds",
                "modes": (
                    {
                        "key": "rend",
                        "targeting": {"mode": "single"},
                        "effect": {"kind": "damage", "params": {"dice": "1d6+1", "touch_attack": True}},
                        "prose": "Inflicts 1d6+1 hit points of damage by touch.",
                    },
                ),
            },
        }
    )


def extended_class_catalog() -> ClassCatalog:
    """The shipped catalog extended by the fixtures, through catalog validation."""
    return ClassCatalog(classes=(*load_classes().classes, build_warden(), build_gnome()))


def extended_spell_catalog() -> SpellCatalog:
    """The shipped spells extended by the fixture spell, through catalog validation."""
    return SpellCatalog(spells=(*load_spells().spells, build_mend_wounds()))


@pytest.fixture
def fixture_classes(monkeypatch):
    """The one declared seam: `core/character.py`'s `load_classes` binding, patched.

    `Character.definition` reads this binding on every construction, every
    revalidation triggered by field assignment, and every document load — one patch
    covers the whole lifecycle.
    """
    catalog = extended_class_catalog()
    monkeypatch.setattr("osrlib.core.character.load_classes", lambda: catalog)
    return catalog


def base_scores(**overrides: int) -> dict[AbilityScore, int]:
    scores = {ability: 11 for ability in AbilityScore}
    scores.update({AbilityScore(key): value for key, value in overrides.items()})
    return scores


def build_character(definition: ClassDefinition, *, level: int = 1, scores=None, name: str | None = None) -> Character:
    return Character(
        id=f"pc-{name or definition.id}",
        name=name or f"Fixture {definition.name}",
        class_id=definition.id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores=scores or base_scores(),
        alignment=Alignment.LAWFUL,
        max_hp=6 * level,
        current_hp=6 * level,
    )


class CastHarness:
    """A minimal casting scene: streams, ledger, clock, and a live registry."""

    def __init__(self, seed: int = MASTER_SEED) -> None:
        self.streams = RngStreams(master_seed=seed)
        self.ruleset = Ruleset()
        self.clock = GameClock()
        self.ledger = EffectsLedger()
        self.allocator = IdAllocator()
        self.registry: dict[str, object] = {}

    def cast(self, caster, definition, spell, mode, *, reversed=False, targets=()):
        for combatant in (caster, *targets):
            self.registry.setdefault(combatant.id, combatant)
        return cast_spell(
            caster,
            spell,
            mode,
            profile=caster_profile(definition),
            reversed=reversed,
            targets=targets,
            ledger=self.ledger,
            clock=self.clock,
            allocator=self.allocator,
            registry=self.registry,
            ruleset=self.ruleset,
            stream=self.streams.get(MAGIC_STREAM),
            effects_stream=self.streams.get("effects"),
        )


class TestCatalogValidation:
    """Fixture definitions validate exactly the way the shipped loaders validate."""

    def test_class_catalog_round_trips_through_loader_validation(self):
        catalog = extended_class_catalog()
        payload = json.loads(json.dumps(catalog.model_dump(mode="json")))
        reloaded = ClassCatalog.model_validate(payload)
        assert reloaded == catalog
        assert reloaded.get("warden").race == "human"
        assert reloaded.get("gnome").race == "gnome"

    def test_spell_catalog_round_trips_through_loader_validation(self):
        catalog = extended_spell_catalog()
        payload = json.loads(json.dumps(catalog.model_dump(mode="json")))
        reloaded = SpellCatalog.model_validate(payload)
        assert reloaded == catalog
        mend = reloaded.get("mend_wounds")
        assert mend.spell_list == "warden"
        assert mend.reversed_form is not None

    def test_race_and_spell_list_ids_are_validated(self):
        with pytest.raises(ValueError):
            ClassDefinition.model_validate(build_warden().model_dump() | {"race": "Not A Slug"})
        with pytest.raises(ValueError):
            SpellTemplate.model_validate(build_mend_wounds().model_dump() | {"spell_list": "Warden List"})

    def test_fixture_spell_is_on_the_extended_list(self):
        catalog = extended_spell_catalog()
        assert [spell.id for spell in catalog.by_list("warden")] == ["mend_wounds"]


class TestWardenCreation:
    """The stepwise creation procedure against the fixture requirements."""

    def test_rolled_scores_validate_the_class_choice(self):
        warden = build_warden()
        streams = RngStreams(master_seed=MASTER_SEED)
        outcomes = set()
        for _ in range(40):
            rolls = roll_ability_scores(streams.get("character_creation"))
            rejections = validate_class_choice(rolls.scores, warden)
            meets = rolls.scores[AbilityScore.WIS] >= 9 and rolls.scores[AbilityScore.CON] >= 9
            assert (rejections == []) is meets
            for rejection in rejections:
                assert rejection.code == "creation.class.requirements_not_met"
            outcomes.add(meets)
        assert outcomes == {True, False}

    def test_adjustment_honours_may_not_lower(self):
        warden = build_warden()
        scores = base_scores(str=13, int=13, wis=13)
        illegal = AbilityAdjustment(lowered={AbilityScore.STR: 2}, raised={AbilityScore.WIS: 1})
        rejections = validate_adjustment(scores, illegal, warden.prime_requisites, warden.may_not_lower)
        assert any(rejection.code == "creation.adjustment.class_restriction" for rejection in rejections)
        legal = AbilityAdjustment(lowered={AbilityScore.INT: 2}, raised={AbilityScore.WIS: 1})
        assert validate_adjustment(scores, legal, warden.prime_requisites, warden.may_not_lower) == []
        adjusted = apply_adjustment(scores, legal, warden.prime_requisites, warden.may_not_lower)
        assert adjusted[AbilityScore.INT] == 11 and adjusted[AbilityScore.WIS] == 14

    def test_two_tier_prime_requisites(self):
        warden = build_warden()
        assert xp_modifier_pct(warden, base_scores(wis=16, con=13)) == 10
        assert xp_modifier_pct(warden, base_scores(wis=13, con=13)) == 5
        assert xp_modifier_pct(warden, base_scores(wis=13, con=9)) == 0

    def test_first_level_hit_points(self):
        warden = build_warden()
        streams = RngStreams(master_seed=MASTER_SEED)
        roll = roll_hit_points(warden, 0, Ruleset(), streams.get("character_creation"))
        assert 1 <= roll.hit_points <= 6

    def test_divine_warden_starts_with_no_spell_book(self):
        warden = build_warden()
        catalog = extended_spell_catalog()
        assert validate_starting_spells(warden, catalog, ()) == []
        rejections = validate_starting_spells(warden, catalog, ("mend_wounds",))
        assert [rejection.code for rejection in rejections] == ["magic.book.not_arcane"]

    def test_character_constructs_under_the_seam(self, fixture_classes):
        character = build_character(build_warden())
        assert character.definition.id == "warden"
        assert character.thac0 == 19 and character.attack_bonus == 0
        assert character.saves.death == 10 and character.saves.spells == 14

    def test_equip_legality_under_the_fixture_policies(self, fixture_classes):
        warden = build_warden()
        character = build_character(warden)
        character.inventory.purse.gp = 200
        equipment = load_equipment()
        leather = purchase(character.inventory, equipment.get("leather"))
        assert validate_equip(warden, leather) == []
        equip(character.inventory, warden, leather)
        mace = purchase(character.inventory, equipment.get("mace"))
        assert validate_equip(warden, mace) == []
        chain = purchase(character.inventory, equipment.get("chainmail"))
        assert [rejection.code for rejection in validate_equip(warden, chain)] == ["items.equip.armour_not_allowed"]
        sword = purchase(character.inventory, equipment.get("sword"))
        assert [rejection.code for rejection in validate_equip(warden, sword)] == ["items.equip.weapon_not_allowed"]


class TestWardenAdvancement:
    """Leveling, drain, and the derived values off the fixture progression rows."""

    def test_level_up_reveals_slots_and_drain_reverses(self, fixture_classes):
        warden = build_warden()
        character = build_character(warden)
        streams = RngStreams(master_seed=MASTER_SEED)
        advancement = streams.get("advancement")
        assert warden.row(character.level).spell_slots == (1,)
        result = level_up(character, warden, advancement)
        assert character.level == 2 and result.hp_roll is not None
        assert warden.row(character.level).spell_slots == (2,)
        drain = drain_levels(character, warden, xp_policy="halfway", stream=advancement)
        assert character.level == 1 and drain.levels_lost == 1
        assert character.xp == (warden.row(2).xp + warden.row(1).xp) // 2
        assert warden.row(character.level).spell_slots == (1,)

    def test_saves_and_attack_values_read_the_fixture_rows(self, fixture_classes):
        warden = build_warden()
        character = build_character(warden, level=5)
        assert character.thac0 == 17 and character.attack_bonus == 2
        assert character.saves.death == 8 and character.saves.breath == 13

    def test_level_cap_is_the_fixture_maximum(self, fixture_classes):
        warden = build_warden()
        character = build_character(warden, level=6)
        streams = RngStreams(master_seed=MASTER_SEED)
        with pytest.raises(ValueError, match="capped at level 6"):
            level_up(character, warden, streams.get("advancement"))


class TestWardenMagic:
    """Memorization and the re-keyed divine choose-reversal-at-cast freedom."""

    def test_memorization_legality_on_the_warden_list(self, fixture_classes):
        warden = build_warden()
        catalog = extended_spell_catalog()
        character = build_character(warden)
        reversed_selection = (MemorizedSpell(spell_id="mend_wounds", reversed=True),)
        result = memorize_spells(character, warden, catalog, reversed_selection)
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.divine_reverses_at_cast"]
        wrong_list = (MemorizedSpell(spell_id="cure_light_wounds"),)
        result = memorize_spells(character, warden, catalog, wrong_list)
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.wrong_list"]
        result = memorize_spells(character, warden, catalog, (MemorizedSpell(spell_id="mend_wounds"),))
        assert result.accepted
        assert [copy.spell_id for copy in character.memorized_spells] == ["mend_wounds"]

    def test_memorized_unreversed_casts_reversed(self, fixture_classes):
        """The lock: divine reversal-at-cast works on a non-cleric list.

        The pre-opening key branched on the literal `"cleric"` list name and would
        have silently denied every non-cleric divine caster the choose-at-cast
        freedom; the re-key onto `profile.kind == "divine"` grants it.
        """
        warden_def = build_warden()
        catalog = extended_spell_catalog()
        harness = CastHarness()
        warden = build_character(warden_def)
        gnome = build_character(build_gnome(), name="tester")
        memorize_spells(warden, warden_def, catalog, (MemorizedSpell(spell_id="mend_wounds"),))
        spell = catalog.get("mend_wounds")
        rejections = validate_cast(
            warden, spell, "rend", profile=caster_profile(warden_def), reversed=True, targets=[gnome]
        )
        assert rejections == []
        result = harness.cast(warden, warden_def, spell, "rend", reversed=True, targets=[gnome])
        assert result.reversed is True
        assert warden.memorized_spells == ()
        assert gnome.id in result.affected_ids
        assert gnome.current_hp < gnome.max_hp

    def test_arcane_form_matching_is_unchanged(self, fixture_classes):
        magic_user_def = load_classes().get("magic_user")
        magic_user = build_character(magic_user_def, level=3, name="arcanist")
        magic_user.spell_book = ("light_mu",)
        magic_user.memorized_spells = (MemorizedSpell(spell_id="light_mu"),)
        rejections = validate_cast(
            magic_user,
            load_spells().get("light_mu"),
            "blind",
            profile=caster_profile(magic_user_def),
            reversed=True,
        )
        assert "magic.cast.not_memorized" in [rejection.code for rejection in rejections]


class TestClericReversalRegression:
    """Cleric reversal-at-cast behaves identically before and after the re-keying."""

    def test_cleric_choose_at_cast_freedom_unchanged(self):
        cleric_def = load_classes().get("cleric")
        harness = CastHarness()
        cleric = build_character(cleric_def, level=2, name="priest")
        target = build_character(load_classes().get("fighter"), name="victim")
        cleric.memorized_spells = (MemorizedSpell(spell_id="cure_light_wounds"),)
        spell = load_spells().get("cure_light_wounds")
        rejections = validate_cast(
            cleric, spell, "harm", profile=caster_profile(cleric_def), reversed=True, targets=[target]
        )
        assert rejections == []
        result = harness.cast(cleric, cleric_def, spell, "harm", reversed=True, targets=[target])
        assert result.reversed is True
        assert cleric.memorized_spells == ()


class TestGnomeFixture:
    """The opened race field and the Classic-census tag consumers."""

    def test_tags_feed_the_shipped_consumers(self, fixture_classes):
        gnome_def = build_gnome()
        gnome = build_character(gnome_def)
        assert detection_chance(gnome, gnome_def, "room_traps") == 2
        assert detection_chance(gnome, gnome_def, "secret_doors") == 1
        assert detection_chance(gnome, gnome_def, "construction") == 0
        infravision = next(ability for ability in gnome_def.abilities if ability.tag == "infravision")
        assert infravision.params["range_feet"] == 60

    def test_demi_human_saves_and_level_cap(self, fixture_classes):
        gnome_def = build_gnome()
        veteran = build_character(gnome_def, level=8)
        assert veteran.saves.death == 4 and veteran.saves.breath == 7
        streams = RngStreams(master_seed=MASTER_SEED)
        with pytest.raises(ValueError, match="capped at level 8"):
            level_up(veteran, gnome_def, streams.get("advancement"))

    def test_document_round_trip_carries_the_open_fields(self, fixture_classes):
        gnome = build_character(build_gnome(), name="scout")
        wandering_warden = build_character(build_warden(), name="wanderer")
        # An Advanced-shaped pairing on one sheet: class id `warden`, race `gnome`.
        gnome_warden = wandering_warden.model_copy(update={"race": "gnome"})
        document = party_to_document([gnome, gnome_warden])
        reloaded = party_from_document(document)
        assert [member.race for member in reloaded] == ["gnome", "gnome"]
        assert [member.class_id for member in reloaded] == ["gnome", "warden"]
        assert reloaded[1].definition.id == "warden"
