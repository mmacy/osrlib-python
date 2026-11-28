"""The player view lists each named spell's usages, with what a cast dialog needs (issue #344).

A remote client has only the player view and the event stream. `CastSpell` and a battle `cast` declaration take a
usage's key, so `PlayerView.spell_modes` and `PlayerView.reversed_spell_modes` give, for each spell id in
`spell_names`, the usages of each form with their keys, their labels, and the checks the session makes of a cast's
targets. These tests reach each state through commands and check the view against what the session accepts.
"""

import positions_fixtures as pf
from osrlib.core.combat import TargetingMode
from osrlib.core.events import Visibility
from osrlib.core.spells import MemorizedSpell, SpellTemplate
from osrlib.crawl.commands import BattleDeclaration, CastSpell, EnterDungeon, PrepareSpells, Rest
from osrlib.crawl.dungeon import cell_ref
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView, SpellModeView
from osrlib.data import load_spells
from test_custom_spells import adventure, custom_spell, prepare, session


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _keys(modes: tuple[SpellModeView, ...]) -> list[tuple[str, str]]:
    return [(mode.key, mode.display_name) for mode in modes]


def _lantern() -> SpellTemplate:
    """A magic-user spell of the adventure's own: light under a new id, with labels its author wrote."""
    document = custom_spell("light_mu", "lantern").model_dump()
    document["modes"] = [{**mode, "display_name": f"Lantern: {mode['key']}"} for mode in document["modes"]]
    reverse = document["reversed_form"]
    document["reversed_form"] = {
        **reverse,
        "name": "Shroud",
        "modes": [{**mode, "display_name": f"Shroud: {mode['key']}"} for mode in reverse["modes"]],
    }
    return SpellTemplate.model_validate(document)


LANTERN = _lantern()


def test_the_repro_lists_light_and_darkness_by_key_and_label():
    """The issue's repro: a cleric prepares *light*, and the view now has its usages and its reverse's."""
    party = Party(members=[pf.character("Hild", "fighter"), pf.character("Oswin", "cleric")])
    game = GameSession.new(party, adventure(), seed=7)
    assert game.execute(Rest(kind="night")).accepted
    oswin = pf.named(game, "Oswin")
    selections = (MemorizedSpell(spell_id="light_c"),)
    assert game.execute(PrepareSpells(character_id=oswin, selections=selections)).accepted

    view = _player_view(game)

    assert _keys(view.spell_modes["light_c"]) == [
        ("illuminate", "Conjure light"),
        ("blind", "Blind a creature"),
        ("cancel", "Cancel darkness"),
    ]
    assert _keys(view.reversed_spell_modes["light_c"]) == [
        ("darken", "Conjure darkness"),
        ("blind", "Blind a creature"),
        ("cancel", "Cancel light"),
    ]


def test_every_named_spell_has_its_usages_and_no_other_spell_does():
    """The two maps have the same keys as `spell_names` and `reversed_spell_names`, in the catalog's order."""
    party = Party(members=[pf.character("Hild", "fighter"), pf.character("Oswin", "cleric")])
    game = GameSession.new(party, adventure(), seed=7)
    catalog = load_spells()

    view = _player_view(game)

    assert list(view.spell_modes) == list(view.spell_names)
    assert list(view.reversed_spell_modes) == list(view.reversed_spell_names)
    for spell_id, modes in view.spell_modes.items():
        assert [mode.key for mode in modes] == [mode.key for mode in catalog.get(spell_id).modes]
    for spell_id, modes in view.reversed_spell_modes.items():
        reverse = catalog.get(spell_id).reversed_form
        assert reverse is not None
        assert [mode.key for mode in modes] == [mode.key for mode in reverse.modes]
    assert _keys(view.reversed_spell_modes["cure_light_wounds"]) == [("harm", "Harm")]


def test_a_bundled_spell_has_the_usages_its_author_wrote():
    """The view reads `GameSession.effective_spells`, which has the spells the adventure bundles."""
    game = session(LANTERN, book=("lantern", "magic_missile", "web"))

    view = _player_view(game)

    assert _keys(view.spell_modes["lantern"]) == [
        ("illuminate", "Lantern: illuminate"),
        ("blind", "Lantern: blind"),
        ("cancel", "Lantern: cancel"),
    ]
    assert _keys(view.reversed_spell_modes["lantern"]) == [
        ("darken", "Shroud: darken"),
        ("blind", "Shroud: blind"),
        ("cancel", "Shroud: cancel"),
    ]


def test_the_target_counts_are_the_ones_the_session_checks():
    """*Light* takes one target, and the session refuses none and two with `magic.cast.target_count`."""
    game = session(book=("light_mu", "magic_missile", "web"))
    assert prepare(game, "light_mu").accepted
    wren = pf.named(game, "Wren")
    hild = pf.named(game, "Hild")
    illuminate = _player_view(game).spell_modes["light_mu"][0]
    assert (illuminate.targeting, illuminate.min_targets, illuminate.max_targets) == (TargetingMode.SINGLE, 1, 1)

    for targets in ((), (wren, hild)):
        result = game.execute(CastSpell(character_id=wren, spell_id="light_mu", mode="illuminate", targets=targets))
        assert [rejection.code for rejection in result.rejections] == ["magic.cast.target_count"]
    accepted = game.execute(CastSpell(character_id=wren, spell_id="light_mu", mode="illuminate", targets=(hild,)))
    assert accepted.accepted


def test_a_cell_is_a_target_only_where_the_view_says():
    """*Light*'s `illuminate` lights a place, and its `blind` works only on a creature, which allows a save."""
    game = session(book=("light_mu", "magic_missile", "web"))
    assert prepare(game, "light_mu", "light_mu").accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    wren = pf.named(game, "Wren")
    modes = {mode.key: mode for mode in _player_view(game).spell_modes["light_mu"]}
    assert (modes["illuminate"].accepts_cells, modes["blind"].accepts_cells) == (True, False)
    location = game.dungeon_state.location
    assert location.dungeon_id is not None and location.level_number is not None and location.position is not None
    here = cell_ref(location.dungeon_id, location.level_number, location.position)

    blind = game.execute(CastSpell(character_id=wren, spell_id="light_mu", mode="blind", targets=(here,)))
    assert [rejection.code for rejection in blind.rejections] == ["magic.cast.target_must_be_creature"]
    lit = game.execute(CastSpell(character_id=wren, spell_id="light_mu", mode="illuminate", targets=(here,)))
    assert lit.accepted


def test_magic_missile_lists_each_casters_missiles_and_a_scrolls():
    """The OSE SRD's *magic missile* gives "2 more missiles for every 5 experience levels", so a 6th-level caster
    shoots three, a 3rd-level caster one, and a scroll, read at the lowest level that casts the spell, one.

    Aelin, a 3rd-level elf with an empty book, may learn the spell, so her sheet has its count too. Tam, a cleric,
    casts from another list and has none.
    """
    roster = (pf.HILD, pf.WREN, ("Aelin", "elf"), pf.TAM)
    game = pf.session(1, roster, spells={"Wren": ("magic_missile",)}, levels={"Wren": 6})
    wren, hild = pf.named(game, "Wren"), pf.named(game, "Hild")
    view = _player_view(game)

    (missiles,) = view.spell_modes["magic_missile"]

    assert missiles.one_target_per_missile
    assert missiles.scroll_missile_count == 1
    assert (missiles.min_targets, missiles.max_targets) == (1, None)
    sheets = {member.name: member for member in view.party}
    assert sheets["Wren"].missile_counts == {"magic_missile": 3}
    assert sheets["Aelin"].missile_counts == {"magic_missile": 1}
    assert sheets["Tam"].missile_counts == {}
    assert sheets["Wren"].reversed_missile_counts == {}
    two = game.execute(CastSpell(character_id=wren, spell_id="magic_missile", mode="missiles", targets=(hild, hild)))
    assert [rejection.code for rejection in two.rejections] == ["magic.cast.target_count"]
    three = CastSpell(character_id=wren, spell_id="magic_missile", mode="missiles", targets=(hild, hild, hild))
    assert game.execute(three).accepted


def _sparks() -> SpellTemplate:
    """A reversible spell of the adventure's own: light, whose reverse shoots magic missile's missiles."""
    document = custom_spell("light_mu", "sparks").model_dump()
    missiles = load_spells().get("magic_missile").model_dump()["modes"]
    document["reversed_form"] = {**document["reversed_form"], "name": "Sparks reversed", "modes": missiles}
    return SpellTemplate.model_validate(document)


def test_a_bundled_reverse_that_shoots_missiles_has_its_own_count():
    """A reverse's usage can take one target per missile, so the member's sheet counts the reverse separately."""
    game = session(_sparks(), book=("sparks", "magic_missile", "web"))
    wren, hild = pf.named(game, "Wren"), pf.named(game, "Hild")
    assert game.execute(Rest(kind="night")).accepted
    selections = (MemorizedSpell(spell_id="sparks", reversed=True),)
    assert game.execute(PrepareSpells(character_id=wren, selections=selections)).accepted
    view = _player_view(game)
    sheet = next(member for member in view.party if member.id == wren)

    assert sheet.reversed_missile_counts == {"sparks": 1}
    assert "sparks" not in sheet.missile_counts
    assert view.reversed_spell_modes["sparks"][0].one_target_per_missile
    cast = CastSpell(character_id=wren, spell_id="sparks", mode="missiles", reversed=True, targets=(hild, hild))
    assert [rejection.code for rejection in game.execute(cast).rejections] == ["magic.cast.target_count"]
    assert game.execute(cast.model_copy(update={"targets": (hild,)})).accepted


def test_a_target_group_is_required_or_optional_as_the_view_says():
    """*Fire ball* takes its targets from a group, and *sleep*'s `hd_budget` usage may take them from one."""
    roster = (pf.HILD, pf.OSRIC, pf.WREN)
    game = pf.battle(roster, spells={"Wren": ("fire_ball", "sleep")}, levels={"Wren": 5})
    wren = pf.named(game, "Wren")
    view = _player_view(game)
    fire_ball = view.spell_modes["fire_ball"][0]
    sleep = {mode.key: mode for mode in view.spell_modes["sleep"]}
    assert (fire_ball.targeting, fire_ball.target_group) == (TargetingMode.AREA, "required")
    assert (sleep["hd_budget"].target_group, sleep["single_4_plus"].target_group) == ("optional", "none")

    no_group = BattleDeclaration(character_id=wren, action="cast", spell_id="fire_ball", spell_mode="damage")
    assert [code for code, _ in pf.refused(game, no_group)] == ["battle.declaration.unknown_group"]
    at_group = BattleDeclaration(
        character_id=wren, action="cast", spell_id="sleep", spell_mode="hd_budget", target_group_id=pf.group_id(game)
    )
    assert pf.round_(game, at_group).accepted


def test_each_form_has_its_range():
    """*Remove fear* is cast by touch, and its reverse, *cause fear*, reaches 120'. *Light* reaches 120' too."""
    party = Party(members=[pf.character("Hild", "fighter"), pf.character("Oswin", "cleric")])
    view = _player_view(GameSession.new(party, adventure(), seed=7))

    assert list(view.spell_ranges) == list(view.spell_names)
    assert list(view.reversed_spell_ranges) == list(view.reversed_spell_names)
    assert view.spell_ranges["remove_fear"].kind == "touch"
    cause_fear = view.reversed_spell_ranges["remove_fear"]
    assert (cause_fear.kind, cause_fear.feet) == ("feet", 120)
    assert (view.spell_ranges["light_c"].kind, view.spell_ranges["light_c"].feet) == ("feet", 120)


def test_each_form_has_its_printed_range():
    """The view gives the range line a client shows, which is the only statement of a `special` range."""
    party = Party(members=[pf.character("Hild", "fighter"), pf.character("Oswin", "cleric", level=14)])
    view = _player_view(GameSession.new(party, adventure(), seed=7))

    assert list(view.spell_range_texts) == list(view.spell_names)
    assert list(view.reversed_spell_range_texts) == list(view.reversed_spell_names)
    assert view.spell_range_texts["remove_fear"] == "The caster or a creature touched"
    assert view.reversed_spell_range_texts["remove_fear"] == "120’"
    assert view.spell_ranges["create_food"].kind == "special"
    assert view.spell_range_texts["create_food"] == "Appears in the caster’s presence"
    assert view.reversed_spell_range_texts["light_c"] == view.spell_range_texts["light_c"]


def test_a_battle_cast_is_refused_beyond_the_range_the_view_shows():
    """*Magic missile* reaches 150', so a group at 150' is in range and one at 160' isn't."""
    reach = None
    for distance, accepted in ((150, True), (160, False)):
        game = pf.battle((pf.HILD, pf.WREN), spells={"Wren": ("magic_missile",)}, distance=distance)
        wren = pf.named(game, "Wren")
        view = _player_view(game)
        reach = view.spell_ranges["magic_missile"]
        (group,) = view.encounter.groups if view.encounter is not None else ()
        assert group.member_distance_feet[wren] == distance
        target = group.members[0].id
        cast = BattleDeclaration(
            character_id=wren, action="cast", spell_id="magic_missile", spell_mode="missiles", targets=(target,)
        )
        if accepted:
            assert pf.round_(game, cast).accepted
        else:
            assert pf.refused(game, cast) == [
                (
                    "magic.cast.out_of_range",
                    {"spell": "magic_missile", "distance_feet": 160, "range_feet": 150, "character": wren},
                )
            ]
    assert reach is not None and (reach.kind, reach.feet) == ("feet", 150)


def test_a_touch_spell_on_a_distant_monster_is_out_of_range():
    """*Cause light wounds*, the reverse of *cure light wounds*, is cast by touch, which reaches 5'."""
    game = pf.battle((pf.HILD, pf.TAM), spells={"Tam": ("cure_light_wounds",)}, distance=30)
    tam = pf.named(game, "Tam")
    view = _player_view(game)
    assert view.reversed_spell_ranges["cure_light_wounds"].kind == "touch"
    assert view.encounter is not None
    target = view.encounter.groups[0].members[0].id
    cast = BattleDeclaration(
        character_id=tam,
        action="cast",
        spell_id="cure_light_wounds",
        spell_mode="harm",
        reversed=True,
        targets=(target,),
    )

    assert [code for code, _ in pf.refused(game, cast)] == ["magic.cast.out_of_range"]


def test_a_view_from_2_11_0_validates_with_the_new_maps_empty():
    game = session(book=("light_mu", "magic_missile", "web"))
    document = _player_view(game).model_dump(mode="json")
    new_maps = (
        "spell_modes",
        "reversed_spell_modes",
        "spell_ranges",
        "reversed_spell_ranges",
        "spell_range_texts",
        "reversed_spell_range_texts",
    )
    for key in new_maps:
        del document[key]
    for member in document["party"]:
        del member["missile_counts"]
        del member["reversed_missile_counts"]

    old = PlayerView.model_validate(document)

    assert (old.spell_modes, old.reversed_spell_modes) == ({}, {})
    assert (old.spell_ranges, old.reversed_spell_ranges) == ({}, {})
    assert (old.spell_range_texts, old.reversed_spell_range_texts) == ({}, {})
    assert all(member.missile_counts == {} == member.reversed_missile_counts for member in old.party)


def test_the_view_round_trips_through_json():
    game = session(book=("light_mu", "magic_missile", "web"))
    view = _player_view(game)

    assert PlayerView.model_validate_json(view.model_dump_json()) == view


ONLY_CREATURES = (
    ("fly", "grant"),
    ("infravision", "grant"),
    ("water_breathing", "grant"),
    ("haste", "haste"),
    ("cure_light_wounds", "cure_paralysis"),
    ("cure_disease", "cure"),
    ("neutralize_poison", "neutralize"),
    ("remove_fear", "remove"),
    ("stone_to_flesh", "restore"),
)
"""Usages that allow no save and deal no damage but work only on a creature: the ones whose targeting is
`creatures_only`, and the cures that end only conditions."""


def _casters_at_a_cell() -> tuple[GameSession, dict[str, str], str]:
    """A 14th-level magic-user with an empty book and a 14th-level cleric in the dungeon, with nothing memorized.

    Between them they may learn or prepare every spell in the catalog, so the view names every spell. The function
    returns the session, the caster's id for each spell list, and the cell the party stands on.
    """
    party = Party(members=[pf.character("Wren", "magic_user", level=14), pf.character("Oswin", "cleric", level=14)])
    game = GameSession.new(party, adventure(), seed=7)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    location = game.dungeon_state.location
    assert location.dungeon_id is not None and location.level_number is not None and location.position is not None
    casters = {"magic_user": pf.named(game, "Wren"), "cleric": pf.named(game, "Oswin")}
    return game, casters, cell_ref(location.dungeon_id, location.level_number, location.position)


def _cell_codes(game: GameSession, caster: str, spell_id: str, key: str, here: str, *, reversed: bool) -> list[str]:
    cast = CastSpell(character_id=caster, spell_id=spell_id, mode=key, reversed=reversed, targets=(here,))
    result = game.execute(cast)
    assert not result.accepted, "precondition: nothing is memorized, so the cast is refused and changes nothing"
    return [rejection.code for rejection in result.rejections]


def test_every_usage_takes_a_cell_exactly_when_the_view_says_so():
    """Each usage of each form in the catalog, cast at a cell by a caster with nothing memorized.

    The session refuses the cast with `magic.cast.not_memorized`, and it still makes its target checks, so it also
    refuses the cell with `magic.cast.target_must_be_creature` exactly when the view's `accepts_cells` is false.
    """
    game, casters, here = _casters_at_a_cell()
    view = _player_view(game)
    checked = 0
    for spell in game.effective_spells.spells:
        assert spell.id in view.spell_modes, "precondition: the view names every spell"
        forms = [(False, view.spell_modes[spell.id])]
        if spell.reversed_form is not None:
            forms.append((True, view.reversed_spell_modes[spell.id]))
        for reversed, usages in forms:
            for usage in usages:
                codes = _cell_codes(game, casters[spell.spell_list], spell.id, usage.key, here, reversed=reversed)
                assert "magic.cast.not_memorized" in codes
                refused_cell = "magic.cast.target_must_be_creature" in codes
                assert usage.accepts_cells is not refused_cell, (spell.id, reversed, usage.key)
                checked += 1
    assert checked == sum(
        len(spell.modes) + (len(spell.reversed_form.modes) if spell.reversed_form else 0)
        for spell in game.effective_spells.spells
    )


def test_the_cures_and_the_creature_only_usages_refuse_a_cell():
    game, casters, here = _casters_at_a_cell()
    view = _player_view(game)
    for spell_id, key in ONLY_CREATURES:
        (usage,) = [usage for usage in view.spell_modes[spell_id] if usage.key == key]
        assert not usage.accepts_cells, spell_id
        caster = casters[game.effective_spells.get(spell_id).spell_list]
        assert "magic.cast.target_must_be_creature" in _cell_codes(game, caster, spell_id, key, here, reversed=False)


def test_light_lights_a_cell_and_clears_one():
    """*Light*'s `illuminate` and `cancel` take a cell, and the session casts both at one."""
    game = session(book=("light_mu", "magic_missile", "web"))
    assert prepare(game, "light_mu", "light_mu").accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    wren = pf.named(game, "Wren")
    location = game.dungeon_state.location
    assert location.dungeon_id is not None and location.level_number is not None and location.position is not None
    here = cell_ref(location.dungeon_id, location.level_number, location.position)
    modes = {usage.key: usage for usage in _player_view(game).spell_modes["light_mu"]}

    assert (modes["illuminate"].accepts_cells, modes["cancel"].accepts_cells) == (True, True)
    for key in ("illuminate", "cancel"):
        cast = CastSpell(character_id=wren, spell_id="light_mu", mode=key, targets=(here,))
        result = game.execute(cast)
        assert result.accepted, (key, [rejection.code for rejection in result.rejections])
