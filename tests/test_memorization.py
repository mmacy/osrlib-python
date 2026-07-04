"""Tests for memorization, spell books, starting spells, and the drain interplay."""

import pytest

from osrlib.core.alignment import Alignment
from osrlib.core.character import (
    choose_starting_spells,
    create_character,
    validate_starting_spells,
)
from osrlib.core.classes import apply_xp, drain_levels
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import (
    MemorizedSpell,
    add_spell_to_book,
    caster_profile,
    memorize_spells,
)
from osrlib.data import load_classes, load_spells

MASTER_SEED = 20_260_703


@pytest.fixture
def streams():
    return RngStreams(master_seed=MASTER_SEED)


def make_character(streams, class_id, *, level=1, starting_spells=()):
    # Creation seed-searches independently (the demihuman classes have score
    # requirements); leveling draws from the shared fixture streams.
    for seed in range(MASTER_SEED, MASTER_SEED + 100):
        try:
            result = create_character(
                name=f"Test {class_id}",
                class_id=class_id,
                alignment=Alignment.NEUTRAL,
                ruleset=Ruleset(),
                stream=RngStreams(master_seed=seed).get("character_creation"),
                starting_spell_ids=starting_spells,
            )
            break
        except ValueError as error:
            if "requirements_not_met" not in str(error):
                raise
    else:
        pytest.fail(f"no seed satisfied {class_id} requirements")
    character = result.character
    character.id = f"pc-{class_id}"
    definition = load_classes().get(class_id)
    while character.level < level:
        # Over-award: the one-level-per-award clamp turns any big award into exactly
        # one level, regardless of the class XP-modifier percentage.
        apply_xp(character, definition, definition.row(definition.max_level).xp, streams.get("advancement"))
    return character, definition


class TestCasterProfile:
    def test_profiles(self):
        classes = load_classes()
        assert caster_profile(classes.get("cleric")).kind == "divine"
        assert caster_profile(classes.get("magic_user")).kind == "arcane"
        assert caster_profile(classes.get("elf")).spell_list == "magic_user"
        assert caster_profile(classes.get("fighter")) is None


class TestStartingSpells:
    def test_arcane_creation_grants_a_book_at_capacity(self, streams):
        magic_user, _ = make_character(streams, "magic_user", starting_spells=("sleep",))
        assert magic_user.spell_book == ("sleep",)

    def test_arcane_creation_without_spells_is_illegal(self):
        # Magic-user has no score requirements, so the failure is the missing book.
        with pytest.raises(ValueError, match="capacity_mismatch"):
            create_character(
                name="Bookless",
                class_id="magic_user",
                alignment=Alignment.NEUTRAL,
                ruleset=Ruleset(),
                stream=RngStreams(master_seed=MASTER_SEED).get("character_creation"),
            )

    def test_clerics_start_with_nothing(self, streams):
        cleric, _ = make_character(streams, "cleric")
        assert cleric.spell_book == ()
        rejections = validate_starting_spells(load_classes().get("cleric"), load_spells(), ["cure_light_wounds"])
        assert [rejection.code for rejection in rejections] == ["magic.book.not_arcane"]

    def test_over_capacity_and_wrong_list_rejected(self):
        definition = load_classes().get("magic_user")
        catalog = load_spells()
        rejections = validate_starting_spells(definition, catalog, ["sleep", "charm_person"])
        assert "magic.book.capacity_mismatch" in [rejection.code for rejection in rejections]
        rejections = validate_starting_spells(definition, catalog, ["bless"])
        assert "magic.book.wrong_list" in [rejection.code for rejection in rejections]

    def test_stepwise_choice_writes_once(self, streams):
        elf, definition = make_character(streams, "elf", starting_spells=("magic_missile",))
        rejections = choose_starting_spells(elf, definition, load_spells(), ["sleep"])
        assert [rejection.code for rejection in rejections] == ["magic.book.already_chosen"]


class TestAddSpellToBook:
    def test_leveling_opens_capacity(self, streams):
        magic_user, definition = make_character(streams, "magic_user", level=2, starting_spells=("sleep",))
        result = add_spell_to_book(magic_user, definition, load_spells(), "charm_person")
        assert result.accepted
        assert magic_user.spell_book == ("sleep", "charm_person")
        assert result.events[0].code == "magic.book.added"

    def test_capacity_per_level_enforced(self, streams):
        magic_user, definition = make_character(streams, "magic_user", starting_spells=("sleep",))
        result = add_spell_to_book(magic_user, definition, load_spells(), "charm_person")
        assert [rejection.code for rejection in result.rejections] == ["magic.book.capacity_exceeded"]

    def test_duplicates_wrong_list_and_non_arcane_rejected(self, streams):
        magic_user, definition = make_character(streams, "magic_user", level=2, starting_spells=("sleep",))
        catalog = load_spells()
        assert add_spell_to_book(magic_user, definition, catalog, "sleep").rejections[0].code == "magic.book.duplicate"
        assert add_spell_to_book(magic_user, definition, catalog, "bless").rejections[0].code == "magic.book.wrong_list"
        cleric, cleric_definition = make_character(streams, "cleric")
        assert (
            add_spell_to_book(cleric, cleric_definition, catalog, "bless").rejections[0].code == "magic.book.not_arcane"
        )


class TestMemorizeSpells:
    def test_divine_free_choice_from_the_list(self, streams):
        cleric, definition = make_character(streams, "cleric", level=4)
        result = memorize_spells(
            cleric,
            definition,
            load_spells(),
            [
                MemorizedSpell(spell_id="cure_light_wounds"),
                MemorizedSpell(spell_id="protection_from_evil_c"),
                MemorizedSpell(spell_id="hold_person_c"),
            ],
        )
        assert result.accepted
        assert [copy.spell_id for copy in cleric.memorized_spells] == [
            "cure_light_wounds",
            "protection_from_evil_c",
            "hold_person_c",
        ]
        event = result.events[0]
        assert event.code == "magic.memorize.prepared"
        assert [prepared.spell_id for prepared in event.prepared] == [copy.spell_id for copy in cleric.memorized_spells]

    def test_arcane_choice_is_book_bound(self, streams):
        magic_user, definition = make_character(streams, "magic_user", starting_spells=("sleep",))
        result = memorize_spells(magic_user, definition, load_spells(), [MemorizedSpell(spell_id="charm_person")])
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.not_in_book"]
        assert magic_user.memorized_spells == ()  # rejection mutates nothing

    def test_duplicate_memorization_is_legal(self, streams):
        cleric, definition = make_character(streams, "cleric", level=3)
        result = memorize_spells(
            cleric,
            definition,
            load_spells(),
            [MemorizedSpell(spell_id="cure_light_wounds"), MemorizedSpell(spell_id="cure_light_wounds")],
        )
        assert result.accepted

    def test_slot_counts_enforced_per_level(self, streams):
        cleric, definition = make_character(streams, "cleric", level=2)
        result = memorize_spells(
            cleric,
            definition,
            load_spells(),
            [MemorizedSpell(spell_id="cure_light_wounds"), MemorizedSpell(spell_id="bless")],
        )
        codes = [rejection.code for rejection in result.rejections]
        assert "magic.memorize.slots_exceeded" in codes  # bless is 2nd level; level-2 cleric has no 2nd-level slots

    def test_divine_never_fixes_the_reversed_form(self, streams):
        cleric, definition = make_character(streams, "cleric", level=2)
        result = memorize_spells(
            cleric, definition, load_spells(), [MemorizedSpell(spell_id="cure_light_wounds", reversed=True)]
        )
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.divine_reverses_at_cast"]

    def test_arcane_fixes_the_form_and_reversible_only(self, streams):
        elf, definition = make_character(streams, "elf", level=3, starting_spells=("light_mu",))
        add_spell_to_book(elf, definition, load_spells(), "sleep")
        result = memorize_spells(
            elf,
            definition,
            load_spells(),
            [MemorizedSpell(spell_id="light_mu", reversed=True), MemorizedSpell(spell_id="sleep")],
        )
        assert result.accepted
        assert elf.memorized_spells[0].reversed is True
        result = memorize_spells(elf, definition, load_spells(), [MemorizedSpell(spell_id="sleep", reversed=True)])
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.not_reversible"]

    def test_non_caster_rejected(self, streams):
        fighter, definition = make_character(streams, "fighter")
        result = memorize_spells(fighter, definition, load_spells(), [])
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.not_a_caster"]

    def test_full_replacement_semantics(self, streams):
        cleric, definition = make_character(streams, "cleric", level=2)
        memorize_spells(cleric, definition, load_spells(), [MemorizedSpell(spell_id="cure_light_wounds")])
        memorize_spells(cleric, definition, load_spells(), [MemorizedSpell(spell_id="bless")])
        # Level 2 has one 1st-level slot and no 2nd: bless is rejected, the old list stays.
        assert [copy.spell_id for copy in cleric.memorized_spells] == ["cure_light_wounds"]
        result = memorize_spells(cleric, definition, load_spells(), [MemorizedSpell(spell_id="remove_fear")])
        assert result.accepted
        assert [copy.spell_id for copy in cleric.memorized_spells] == ["remove_fear"]

    def test_wrong_list_and_unknown_ids(self, streams):
        cleric, definition = make_character(streams, "cleric", level=2)
        result = memorize_spells(cleric, definition, load_spells(), [MemorizedSpell(spell_id="sleep")])
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.wrong_list"]
        result = memorize_spells(cleric, definition, load_spells(), [MemorizedSpell(spell_id="wish")])
        assert [rejection.code for rejection in result.rejections] == ["magic.memorize.unknown_spell"]


class TestDrainInterplay:
    def test_drain_forgets_newest_first_per_level(self, streams):
        cleric, definition = make_character(streams, "cleric", level=6)
        catalog = load_spells()
        result = memorize_spells(
            cleric,
            definition,
            catalog,
            [
                MemorizedSpell(spell_id="cure_light_wounds"),
                MemorizedSpell(spell_id="bless"),
                MemorizedSpell(spell_id="remove_fear"),
                MemorizedSpell(spell_id="hold_person_c"),
                MemorizedSpell(spell_id="cure_disease"),
            ],
        )
        assert result.accepted
        # Level 6 slots (2,2,1,1,0) -> level 4 slots (2,1,0,0,0): one 2nd-level copy
        # and the 3rd-level copy must go, newest-first within each level.
        drain = drain_levels(cleric, definition, levels=2, xp_policy="level_minimum", stream=streams.get("advancement"))
        forgotten = [event for event in drain.events if event.event_type == "spell_forgotten"]
        assert [event.spell_id for event in forgotten] == ["cure_disease", "hold_person_c"]
        assert [copy.spell_id for copy in cleric.memorized_spells] == [
            "cure_light_wounds",
            "bless",
            "remove_fear",
        ]

    def test_book_never_auto_shrinks(self, streams):
        magic_user, definition = make_character(streams, "magic_user", level=2, starting_spells=("sleep",))
        add_spell_to_book(magic_user, definition, load_spells(), "charm_person")
        drain_levels(magic_user, definition, xp_policy="level_minimum", stream=streams.get("advancement"))
        assert magic_user.level == 1
        assert magic_user.spell_book == ("sleep", "charm_person")  # over capacity, grandfathered
        # ...and cannot add more until capacity catches up.
        result = add_spell_to_book(magic_user, definition, load_spells(), "magic_missile")
        assert result.rejections[0].code == "magic.book.capacity_exceeded"

    def test_non_caster_drain_untouched(self, streams):
        fighter, definition = make_character(streams, "fighter", level=3)
        drain = drain_levels(fighter, definition, xp_policy="halfway", stream=streams.get("advancement"))
        assert all(event.event_type != "spell_forgotten" for event in drain.events)
