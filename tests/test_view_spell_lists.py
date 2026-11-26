"""The player view lists the spells each caster may prepare or learn (issue #326).

A remote front end builds its prepare and learn dialogs from `PlayerView` alone. Before, the view named a spell
only once a member had it in a spell book, memorized, or running as an effect, so a player couldn't find out
that the adventure bundles a spell their cleric may pray for or their magic-user may learn.
`MemberView.preparable_spells` and `MemberView.learnable_spells` list what `PrepareSpells` and `LearnSpell`
accept for the member, `MemberView.spell_slots` gives the slots to fit them in, and `PlayerView.spell_names`,
`PlayerView.reversed_spell_names`, and `PlayerView.spell_levels` name and level every one of them.

The rules are the OSE SRD's. Rules of Magic, for a divine caster: "When praying for spells, divine spell casters
may choose any spells in their class’ spell list that they are of high enough level to cast." For an arcane
caster: "Arcane spell casters memorize spells by studying spell books (see Spell Books) and are thus limited to
choosing from the spells in their spell book." Spell Books: "A character’s spell book contains exactly the number
of spells that the character is capable of memorizing", and when that number increases, "the number of spells in
the spell book may be brought in line with the number of spells the character can memorize." So neither list
holds a spell of a level the member has no slot for, and neither command accepts one.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import positions_fixtures as pf
from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.classes import ClassDefinition
from osrlib.core.effects import Condition, grant_condition
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import MemorizedSpell, SpellTemplate
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import AwardXP, Command, EnterDungeon, LearnSpell, PrepareSpells, Rest, SpawnMonsters
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import MemberView, PlayerView
from osrlib.data import load_classes, load_spells
from osrlib.persistence import load_game, save_game

SAVE_2_7_0 = Path(__file__).parent / "saves" / "view_spell_lists_2.7.0.json"
"""A save of `casters()` after a night's rest, written by osrlib 2.7.0 before the lists existed."""

# The built-in spell lists, from the OSE SRD's Cleric Spells and Magic-User Spells pages, in id order, which is
# the built-in catalog's order.
CLERIC_1 = (
    "cure_light_wounds",
    "detect_evil_c",
    "detect_magic_c",
    "light_c",
    "protection_from_evil_c",
    "purify_food_and_water",
    "remove_fear",
    "resist_cold",
)
CLERIC_2 = (
    "bless",
    "find_traps",
    "hold_person_c",
    "know_alignment",
    "resist_fire",
    "silence_15_radius",
    "snake_charm",
    "speak_with_animals",
)
MAGIC_USER_2 = (
    "continual_light_mu",
    "detect_evil_mu",
    "detect_invisible",
    "esp",
    "invisibility",
    "knock",
    "levitate",
    "locate_object_mu",
    "mirror_image",
    "phantasmal_force",
    "web",
    "wizard_lock",
)

PREPARE_SPELL_CODES = {
    "magic.memorize.not_a_caster",
    "magic.memorize.unknown_spell",
    "magic.memorize.wrong_list",
    "magic.memorize.not_in_book",
    "magic.memorize.slots_exceeded",
}
"""The codes by which `PrepareSpells` refuses one plain copy of a spell for a reason of the spell and the member."""

LEARN_SPELL_CODES = {
    "magic.book.not_arcane",
    "magic.book.unknown_spell",
    "magic.book.wrong_list",
    "magic.book.duplicate",
    "magic.book.capacity_exceeded",
}
"""The codes by which `LearnSpell` refuses a spell for a reason of the spell and the member."""


# ---------------------------------------------------------------------- the adventure's spells


def bundled(base_id: str, spell_id: str, **changes) -> SpellTemplate:
    """A built-in spell under a new id and name, with `changes` applied, as an adventure bundles it."""
    document = {
        **load_spells().get(base_id).model_dump(),
        "id": spell_id,
        "name": spell_id.replace("_", " ").title(),
        **changes,
    }
    return SpellTemplate.model_validate(document)


def _balm() -> SpellTemplate:
    document = bundled("cure_light_wounds", "balm").model_dump()
    document["reversed_form"] = {**document["reversed_form"], "name": "Bane"}
    return SpellTemplate.model_validate(document)


BALM = _balm()
"""A 1st-level cleric spell that reverses, as the issue's repro bundles it."""
SANCTUM = bundled("remove_curse_c", "sanctum")
"""A 3rd-level cleric spell. No cleric in the party has a 3rd-level slot."""
GLIMMER = bundled("shield", "glimmer")
"""A 1st-level magic-user spell. Every magic-user and elf in the party has a full book at 1st level."""
MIRE = bundled("web", "mire")
"""A 2nd-level magic-user spell, which a magic-user with room at 2nd level may learn."""
MEND_WOUNDS = bundled("cure_light_wounds", "mend_wounds", spell_list="warden")
"""A 1st-level spell on the warden's own list."""
WARD_OF_AGES = bundled("bless", "ward_of_ages", spell_list="warden")
"""A 2nd-level spell on the warden's list. A 2nd-level warden has no 2nd-level slot."""
HEX = bundled("sleep", "hex", spell_list="witch")
"""A spell on a list no party member uses, like one only the adventure's monsters cast."""

SPELLS = (BALM, SANCTUM, GLIMMER, MIRE, MEND_WOUNDS, WARD_OF_AGES, HEX)
HIDDEN = ("sanctum", "glimmer", "ward_of_ages", "hex")


def adventure(*spells: SpellTemplate) -> Adventure:
    base = pf.corridor()
    return Adventure.model_validate({**base.model_dump(), "spells": [spell.model_dump() for spell in spells]})


def _warden() -> ClassDefinition:
    """A divine class of your own that casts from its own list, with the cleric's progression."""
    document = load_classes().get("cleric").model_dump(mode="json")
    document.update(id="warden", name="Warden")
    document["abilities"] = [
        {**ability, "params": {"spell_list": "warden"}} if ability["tag"] == "divine_magic" else ability
        for ability in document["abilities"]
    ]
    return ClassDefinition.model_validate(document)


WARDEN = _warden()


# ---------------------------------------------------------------------- the party


def member(name: str, class_id: str, level: int, *book: str) -> Character:
    """A built-in-class character at `level`, with `book` as the spell book."""
    return Character.model_validate({**pf.character(name, class_id, level=level).model_dump(), "spell_book": book})


def warden(name: str, level: int) -> Character:
    return Character(
        name=name,
        class_id="warden",
        custom_class=WARDEN,
        race="human",
        level=level,
        xp=WARDEN.row(level).xp,
        scores={ability: 10 for ability in AbilityScore},
        alignment="lawful",
        max_hp=400,
        current_hp=400,
    )


def party() -> Party:
    """Casters of every kind beside two who cast nothing.

    - Hild, a 3rd-level fighter, and Nim, a 3rd-level thief, cast nothing.
    - Aelin, a 1st-level elf, has one 1st-level slot, and her book's one spell fills it.
    - Wren, a 3rd-level magic-user, has two 1st-level slots and one 2nd-level slot. Her book fills the 1st level
      and has nothing at the 2nd. *Light* reverses into *darkness*.
    - Sefa, a 1st-level magic-user drained from a higher level, has one 1st-level slot and a book that still holds
      *web*, a 2nd-level spell.
    - Ivo, a 3rd-level magic-user, has a book with *frost dart*, a spell from another adventure that this session's
      catalog doesn't have.
    - Tam, a 1st-level cleric, has no slots.
    - Oswin, a 2nd-level cleric, has one 1st-level slot.
    - Mara, a 4th-level cleric, has two 1st-level slots and one 2nd-level slot.
    - Bryn, a 2nd-level warden, has the cleric's slots and casts from the warden's list.
    """
    return Party(
        members=[
            member("Hild", "fighter", 3),
            member("Nim", "thief", 3),
            member("Aelin", "elf", 1, "sleep"),
            member("Wren", "magic_user", 3, "magic_missile", "light_mu"),
            member("Sefa", "magic_user", 1, "sleep", "web"),
            member("Ivo", "magic_user", 3, "frost_dart", "sleep"),
            member("Tam", "cleric", 1),
            member("Oswin", "cleric", 2),
            member("Mara", "cleric", 4),
            warden("Bryn", 2),
        ]
    )


def casters(*, rested: bool = True) -> GameSession:
    """The party in town with every spell in `SPELLS` bundled, after a night's rest unless `rested` is false."""
    game = GameSession.new(party(), adventure(*SPELLS), seed=7)
    if rested:
        assert game.execute(Rest(kind="night")).accepted
    return game


EXPECTED = {
    "Hild": {"preparable": (), "learnable": (), "slots": ()},
    "Nim": {"preparable": (), "learnable": (), "slots": ()},
    # The book's spell is the one 1st-level spell, so the book is full at the only level with a slot.
    "Aelin": {"preparable": ("sleep",), "learnable": (), "slots": (1, 0, 0, 0, 0)},
    # Catalog order, so *light* comes before *magic missile* although the book has them the other way round.
    # The 1st level is full, so only the 2nd-level spells can be learned: the built-in ones, then the bundled one.
    "Wren": {
        "preparable": ("light_mu", "magic_missile"),
        "learnable": (*MAGIC_USER_2, "mire"),
        "slots": (2, 1, 0, 0, 0, 0),
    },
    # *Web* stays in the book but takes no slot at 1st level, and the book is full at the 1st.
    "Sefa": {"preparable": ("sleep",), "learnable": (), "slots": (1, 0, 0, 0, 0, 0)},
    # `LearnSpell` refuses every spell while the book holds one the catalog lacks (`magic.book.unknown_spell`),
    # and `PrepareSpells` refuses *frost dart* alone.
    "Ivo": {"preparable": ("sleep",), "learnable": (), "slots": (2, 1, 0, 0, 0, 0)},
    "Tam": {"preparable": (), "learnable": (), "slots": (0, 0, 0, 0, 0)},
    "Oswin": {"preparable": (*CLERIC_1, "balm"), "learnable": (), "slots": (1, 0, 0, 0, 0)},
    "Mara": {"preparable": (*sorted(CLERIC_1 + CLERIC_2), "balm"), "learnable": (), "slots": (2, 1, 0, 0, 0)},
    "Bryn": {"preparable": ("mend_wounds",), "learnable": (), "slots": (1, 0, 0, 0, 0)},
}


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _sheet(view: PlayerView, name: str) -> MemberView:
    return next(sheet for sheet in view.party if sheet.name == name)


def _lists(view: PlayerView) -> dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]]]:
    return {sheet.name: (sheet.preparable_spells, sheet.learnable_spells, sheet.spell_slots) for sheet in view.party}


# ---------------------------------------------------------------------- what each member may choose


@pytest.mark.parametrize("name", list(EXPECTED))
def test_each_member_lists_the_spells_they_may_prepare_and_learn(name):
    sheet = _sheet(_player_view(casters()), name)

    expected = EXPECTED[name]
    assert sheet.preparable_spells == expected["preparable"]
    assert sheet.learnable_spells == expected["learnable"]
    assert sheet.spell_slots == expected["slots"]


def _probe(
    make: Callable[[], GameSession], name: str, command: Callable[[str, str], Command], codes: set[str]
) -> set[str]:
    """The ids the command accepts for the member, checking that every refusal is for the spell and the member.

    A refused command changes nothing, so the probes share a session until one is accepted, and then a fresh one
    is made, because an accepted preparation or learning changes what the next probe sees.
    """
    game = make()
    probes = sorted(
        {spell.id for spell in game.effective_spells.spells}
        | {spell_id for character in game.party.members for spell_id in character.spell_book}
        | {"no_such_spell"}
    )
    accepted: set[str] = set()
    for spell_id in probes:
        result = game.execute(command(pf.named(game, name), spell_id))
        if result.accepted:
            accepted.add(spell_id)
            game = make()
            continue
        refused = {rejection.code for rejection in result.rejections}
        assert refused and refused <= codes, (name, spell_id, refused)
    return accepted


def _prepare(character_id: str, spell_id: str) -> Command:
    return PrepareSpells(character_id=character_id, selections=(MemorizedSpell(spell_id=spell_id),))


def _learn(character_id: str, spell_id: str) -> Command:
    return LearnSpell(character_id=character_id, spell_id=spell_id)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_a_spell_is_preparable_exactly_when_prepare_spells_accepts_it(name):
    """Every spell id the session knows, every id in a book, and one id nobody has, prepared one plain copy at a time.

    The party has slept, is in town, and can act, so the command refuses only for the spell and the member.
    """
    sheet = _sheet(_player_view(casters()), name)

    accepted = _probe(casters, name, _prepare, PREPARE_SPELL_CODES)

    assert set(sheet.preparable_spells) == accepted
    assert len(sheet.preparable_spells) == len(accepted)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_a_spell_is_learnable_exactly_when_learn_spell_accepts_it(name):
    sheet = _sheet(_player_view(casters()), name)

    accepted = _probe(casters, name, _learn, LEARN_SPELL_CODES)

    assert set(sheet.learnable_spells) == accepted
    assert len(sheet.learnable_spells) == len(accepted)


def test_an_arcane_caster_may_prepare_the_reverse_of_a_listed_spell_that_has_one():
    """*Light* reverses into *darkness*, and *magic missile* doesn't reverse (`magic.memorize.not_reversible`)."""
    game = casters()
    view = _player_view(game)
    wren = _sheet(view, "Wren")

    assert wren.preparable_spells == ("light_mu", "magic_missile")
    assert view.reversed_spell_names["light_mu"] == "Darkness"
    assert "magic_missile" not in view.reversed_spell_names
    darkness = PrepareSpells(character_id=wren.id, selections=(MemorizedSpell(spell_id="light_mu", reversed=True),))
    assert game.execute(darkness).accepted
    refused = casters().execute(
        PrepareSpells(character_id=wren.id, selections=(MemorizedSpell(spell_id="magic_missile", reversed=True),))
    )
    assert [rejection.code for rejection in refused.rejections] == ["magic.memorize.not_reversible"]


def test_a_selection_that_fills_every_slot_from_the_lists_is_accepted():
    """Mara fills her two 1st-level slots and her one 2nd-level slot from her list, counted by `spell_levels`."""
    game = casters()
    view = _player_view(game)
    mara = _sheet(view, "Mara")
    picks: list[str] = []
    for spell_level, slots in enumerate(mara.spell_slots, start=1):
        at_level = [spell_id for spell_id in mara.preparable_spells if view.spell_levels[spell_id] == spell_level]
        picks.extend(at_level[:slots])

    assert picks == ["cure_light_wounds", "detect_evil_c", "bless"]
    selections = tuple(MemorizedSpell(spell_id=spell_id) for spell_id in picks)
    assert game.execute(PrepareSpells(character_id=mara.id, selections=selections)).accepted


# ---------------------------------------------------------------------- the moment doesn't change the lists


def test_the_lists_are_the_same_before_the_party_has_slept():
    """`PrepareSpells` refuses with `magic.memorize.needs_sleep`, which is about the moment, not the spell."""
    game = casters(rested=False)
    oswin = pf.named(game, "Oswin")

    refused = game.execute(_prepare(oswin, "balm"))

    assert [rejection.code for rejection in refused.rejections] == ["magic.memorize.needs_sleep"]
    assert _lists(_player_view(game)) == _lists(_player_view(casters()))
    assert _sheet(_player_view(game), "Oswin").preparable_spells == EXPECTED["Oswin"]["preparable"]


def test_the_lists_are_the_same_during_an_encounter():
    """Both commands refuse with `session.command.wrong_mode` while monsters are in sight."""
    game = casters()
    rested = _lists(_player_view(game))
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    assert game.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=60)).accepted
    assert game.mode.value in ("encounter", "battle")

    oswin, wren = pf.named(game, "Oswin"), pf.named(game, "Wren")
    for refused in (game.execute(_prepare(oswin, "balm")), game.execute(_learn(wren, "mire"))):
        assert [rejection.code for rejection in refused.rejections] == ["session.command.wrong_mode"]
    assert _lists(_player_view(game)) == rested
    assert "balm" in _sheet(_player_view(game), "Oswin").preparable_spells


def test_the_lists_are_the_same_while_the_member_cant_act():
    """An asleep cleric and an asleep magic-user are refused with `session.command.member_incapacitated`."""
    game = casters()
    rested = _lists(_player_view(game))
    for name in ("Oswin", "Wren"):
        grant_condition(game.member(pf.named(game, name)), Condition.ASLEEP, None)

    oswin, wren = pf.named(game, "Oswin"), pf.named(game, "Wren")
    for refused in (game.execute(_prepare(oswin, "balm")), game.execute(_learn(wren, "mire"))):
        assert [rejection.code for rejection in refused.rejections] == ["session.command.member_incapacitated"]
    assert _lists(_player_view(game)) == rested
    assert "mire" in _sheet(_player_view(game), "Wren").learnable_spells


# ---------------------------------------------------------------------- the lists follow the member


def test_the_issues_cleric_sees_the_bundled_spell_at_second_level():
    """The issue's repro: a 1st-level cleric awarded 2,500 XP reaches 2nd level, which has one 1st-level slot."""
    game = GameSession.new(Party(members=[member("Tam", "cleric", 1)]), adventure(BALM), seed=7)
    tam = pf.named(game, "Tam")
    first = _player_view(game)
    assert _sheet(first, "Tam").preparable_spells == ()
    assert "balm" not in first.spell_names

    assert game.execute(AwardXP(character_id=tam, amount=2500)).accepted
    assert game.execute(Rest(kind="night")).accepted
    view = _player_view(game)

    assert _sheet(view, "Tam").level == 2
    assert _sheet(view, "Tam").preparable_spells == (*CLERIC_1, "balm")
    assert (view.spell_names["balm"], view.spell_levels["balm"], view.reversed_spell_names["balm"]) == (
        "Balm",
        1,
        "Bane",
    )
    assert game.execute(_prepare(tam, "balm")).accepted


def test_a_cleric_who_gains_a_spell_level_lists_its_spells():
    """A 3rd-level cleric has two 1st-level slots. 3,000 XP more makes 6,000 and 4th level, with a 2nd-level slot."""
    game = GameSession.new(Party(members=[member("Mara", "cleric", 3)]), adventure(BALM, SANCTUM), seed=7)
    mara = pf.named(game, "Mara")
    before = _player_view(game)
    assert _sheet(before, "Mara").preparable_spells == (*CLERIC_1, "balm")
    assert "bless" not in before.spell_names

    assert game.execute(AwardXP(character_id=mara, amount=3000)).accepted
    after = _player_view(game)

    sheet = _sheet(after, "Mara")
    assert (sheet.level, sheet.spell_slots) == (4, (2, 1, 0, 0, 0))
    assert sheet.preparable_spells == (*sorted(CLERIC_1 + CLERIC_2), "balm")
    assert after.spell_levels["bless"] == 2
    assert "sanctum" not in after.spell_names


def test_learning_a_spell_moves_it_from_learnable_to_preparable():
    """Wren's one 2nd-level slot gives her book room for one 2nd-level spell, so learning *mire* fills it."""
    game = casters()
    wren = pf.named(game, "Wren")

    assert game.execute(_learn(wren, "mire")).accepted
    sheet = _sheet(_player_view(game), "Wren")

    assert sheet.spell_book == ("magic_missile", "light_mu", "mire")
    assert sheet.preparable_spells == ("light_mu", "magic_missile", "mire")
    assert sheet.learnable_spells == ()


# ---------------------------------------------------------------------- names, reverses, and levels


def test_every_listed_spell_is_named_and_levelled_from_the_sessions_catalog():
    game = casters()
    view = _player_view(game)
    catalog = game.effective_spells
    listed = {
        spell_id for expected in EXPECTED.values() for spell_id in (*expected["preparable"], *expected["learnable"])
    }

    assert listed <= set(view.spell_names)
    for spell_id in listed:
        spell = catalog.get(spell_id)
        assert view.spell_names[spell_id] == spell.name
        assert view.spell_levels[spell_id] == spell.level
        if spell.reversed_form is not None:
            assert view.reversed_spell_names[spell_id] == spell.reversed_form.name
        else:
            assert spell_id not in view.reversed_spell_names
    assert (view.spell_names["mend_wounds"], view.spell_levels["mend_wounds"]) == ("Mend Wounds", 1)
    assert (view.spell_names["mire"], view.spell_levels["mire"]) == ("Mire", 2)


def test_spell_levels_has_a_level_for_every_named_spell():
    """Sefa's *web* is in her book with no slot to take, so it's named and levelled, not listed."""
    view = _player_view(casters())

    assert view.spell_levels
    assert set(view.spell_levels) == set(view.spell_names)
    assert view.spell_levels["web"] == 2
    assert "web" not in _sheet(view, "Sefa").preparable_spells
    assert (view.spell_levels["sleep"], view.spell_levels["light_mu"], view.spell_levels["bless"]) == (1, 1, 2)


def test_the_view_names_exactly_the_spells_it_shows():
    """The names come from the books, the memorized copies, the effects, and the two lists, and nowhere else."""
    view = _player_view(casters())
    shown = {
        spell_id
        for sheet in view.party
        for spell_id in (
            *sheet.spell_book,
            *(copy["spell_id"] for copy in sheet.memorized_spells),
            *sheet.preparable_spells,
            *sheet.learnable_spells,
        )
    } | {effect.kind for effect in view.effects}

    # *Frost dart* is in Ivo's book, and this session's catalog doesn't have it.
    assert set(view.spell_names) == shown - {"frost_dart"}
    # Mara's two cleric levels, Wren's 2nd-level choices, the bundled spells someone may choose, and the books' own
    # spells. Sefa's *web* is one of the 2nd-level magic-user spells.
    assert set(view.spell_names) == {
        *CLERIC_1,
        *CLERIC_2,
        *MAGIC_USER_2,
        "balm",
        "mend_wounds",
        "mire",
        "sleep",
        "magic_missile",
        "light_mu",
    }


def test_a_spell_no_member_may_choose_stays_hidden():
    """A bundled spell above every member's slots, at a full book's level, or on a list nobody uses isn't named."""
    view = _player_view(casters())
    blob = view.model_dump_json()

    for spell_id in HIDDEN:
        assert spell_id not in view.spell_names
        assert f'"{spell_id}"' not in blob
    for name in ("Sanctum", "Glimmer", "Ward Of Ages", "Hex"):
        assert name not in blob


def test_a_spell_on_a_scroll_appears_only_when_a_member_may_learn_it_anyway():
    """A scroll adds nothing to either list, because the OSE SRD adds a spell to a book by mentoring or research.

    Wren's book has room at the 2nd level and none at the 1st, so the scroll's *web* is listed because any
    2nd-level magic-user spell is, while its *sleep* and its 3rd-level *fire ball* stay hidden.
    """
    game = casters()
    wren = game.member(pf.named(game, "Wren"))
    wren.inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9001",
            template_id="spell_scroll_3",
            identified=True,
            state={"spell_list": "magic_user", "spells": ("hold_portal", "web", "fire_ball")},
        )
    )
    view = _player_view(game)

    assert "web" in _sheet(view, "Wren").learnable_spells
    assert "hold_portal" not in view.spell_names
    assert "fire_ball" not in view.spell_names
    assert _lists(view) == _lists(_player_view(casters()))


# ---------------------------------------------------------------------- saves


def test_a_loaded_save_has_the_same_lists():
    game = casters()

    loaded = load_game(json.loads(json.dumps(save_game(game))))

    view, reloaded = _player_view(game), _player_view(loaded)
    assert _lists(reloaded) == _lists(view)
    assert _sheet(reloaded, "Bryn").preparable_spells == ("mend_wounds",)
    assert (reloaded.spell_names, reloaded.spell_levels) == (view.spell_names, view.spell_levels)


def test_a_save_from_osrlib_2_7_0_gives_the_lists():
    """The lists are worked out from the characters and the catalog, so a save from before they existed has them."""
    document = json.loads(SAVE_2_7_0.read_text())
    assert document["engine_version"] == "2.7.0"

    view = _player_view(load_game(document))

    for name, expected in EXPECTED.items():
        sheet = _sheet(view, name)
        assert (sheet.preparable_spells, sheet.learnable_spells, sheet.spell_slots) == (
            expected["preparable"],
            expected["learnable"],
            expected["slots"],
        )
    assert view.spell_levels["balm"] == 1


def test_a_player_view_from_before_the_lists_still_parses():
    """A client that kept a 2.7.0 player view's JSON reads it with every new field at its default."""
    document = json.loads(_player_view(casters()).model_dump_json())
    for sheet in document["party"]:
        for field in ("preparable_spells", "learnable_spells", "spell_slots"):
            sheet.pop(field)
    document.pop("spell_levels")

    view = PlayerView.model_validate(document)

    assert view.spell_levels == {}
    assert all(
        (sheet.preparable_spells, sheet.learnable_spells, sheet.spell_slots) == ((), (), ()) for sheet in view.party
    )


# ---------------------------------------------------------------------- the docs


def test_the_views_guide_and_the_changelog_describe_the_lists():
    root = Path(__file__).parent.parent
    guide = (root / "docs" / "guides" / "views-and-visibility.md").read_text()
    changelog = (root / "CHANGELOG.md").read_text()
    released = changelog.split("## [2.8.0]", 1)[1].split("\n## [", 1)[0]

    for name in ("preparable_spells", "learnable_spells", "spell_slots", "spell_levels"):
        assert name in guide, name
        assert name in released, name
    assert "#326" in released
