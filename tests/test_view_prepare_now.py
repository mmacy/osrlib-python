"""The member view says when a caster picks a spell's form, and whether they can prepare now (issue #338).

A remote front end builds its prepare dialog, its cast dialog, and its town screen from `PlayerView` alone. Before,
the view didn't say which kind of caster a member is, so a client inferred it from the spell book, and it didn't
show the sleep rule's state, so a client read the event log for the last rest and the last preparation, which a
client that restored a save doesn't have. `MemberView.reverses_at`, `MemberView.can_prepare_now`, and
`MemberView.needs_sleep_to_prepare` state the three facts, from the checks `PrepareSpells` makes.

The rules are the OSE SRD's Rules of Magic. Arcane casters: "The normal or reversed form of a spell must be selected
when the spell is memorized." Divine casters: "Divine spell casters can cast the reversed version of a spell by
speaking the words and performing the gestures backwards when it is cast." And for both: "A spell caster can memorize
new spells once a day, after an uninterrupted night’s sleep."
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import positions_fixtures as pf
from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.classes import ClassDefinition
from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, grant_condition
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.core.spells import MemorizedSpell
from osrlib.crawl.commands import EnterDungeon, PrepareSpells, Rest, SpawnMonsters
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import MemberView, PlayerView
from osrlib.data import load_classes
from osrlib.persistence import load_game, save_game

CHUNK = "chunk: view-prepare-and-valuables"

SAVE_2_8_0 = Path(__file__).parent / "saves" / "view_prepare_now_2.8.0.json"
"""A save of `scenario("prepared")`, written by osrlib 2.8.0 before the three fields existed."""

MOMENT_CODES = {"session.command.wrong_mode", "session.command.member_incapacitated", "magic.memorize.needs_sleep"}
"""The codes by which `PrepareSpells` refuses a caster for the moment, whatever the selection."""


# ---------------------------------------------------------------------- the party


def _custom(base_id: str, class_id: str, tag: str) -> ClassDefinition:
    """A class of your own with `base_id`'s progression, whose casting ability is `tag` on the cleric's list."""
    document = load_classes().get(base_id).model_dump(mode="json")
    document.update(id=class_id, name=class_id.title())
    document["abilities"] = [
        {**ability, "tag": tag, "params": {"spell_list": "cleric"}}
        if ability["tag"] in ("arcane_magic", "divine_magic")
        else ability
        for ability in document["abilities"]
    ]
    return ClassDefinition.model_validate(document)


SAGE = _custom("magic_user", "sage", "arcane_magic")
"""An arcane class that casts from the cleric's list, with the magic-user's progression."""
WARDEN = _custom("cleric", "warden", "divine_magic")
"""A divine class that casts from the cleric's list, with the cleric's progression."""


def member(name: str, class_id: str, level: int, *book: str) -> Character:
    """A built-in-class character at `level`, with `book` as the spell book."""
    return Character.model_validate({**pf.character(name, class_id, level=level).model_dump(), "spell_book": book})


def custom(name: str, definition: ClassDefinition, level: int, *book: str) -> Character:
    return Character(
        name=name,
        class_id=definition.id,
        custom_class=definition,
        race="human",
        level=level,
        xp=definition.row(level).xp,
        scores={ability: 10 for ability in AbilityScore},
        alignment="lawful",
        max_hp=400,
        current_hp=400,
        spell_book=book,
    )


def party() -> Party:
    """Casters of each kind, two of them of classes of your own, beside a fighter who casts nothing.

    - Hild, a 3rd-level fighter, casts nothing.
    - Aelin, a 1st-level elf, and Wren, a 3rd-level magic-user, are arcane casters whose books hold *light*.
    - Tam, a 1st-level cleric, has no slots, and Mara, a 4th-level cleric, has three.
    - Sefa, a 1st-level sage, and Bryn, a 2nd-level warden, have the same spell book, *cure light wounds*, and cast
      from the same list, the cleric's. Sefa's class is arcane and Bryn's divine, so they differ only in their kind.
      `Character` doesn't tie a spell book to the kind of caster, so Bryn, a divine caster, has a book, and a client
      that inferred the kind from the book would take Bryn for an arcane caster.
    """
    return Party(
        members=[
            member("Hild", "fighter", 3),
            member("Aelin", "elf", 1, "light_mu"),
            member("Wren", "magic_user", 3, "magic_missile", "light_mu"),
            member("Tam", "cleric", 1),
            member("Mara", "cleric", 4),
            custom("Sefa", SAGE, 1, "cure_light_wounds"),
            custom("Bryn", WARDEN, 2, "cure_light_wounds"),
        ]
    )


CASTERS = ("Aelin", "Wren", "Tam", "Mara", "Sefa", "Bryn")

REVERSES_AT = {
    "Hild": None,
    "Aelin": "memorize",
    "Wren": "memorize",
    "Tam": "cast",
    "Mara": "cast",
    "Sefa": "memorize",
    "Bryn": "cast",
}

REVERSIBLE = {
    "Hild": "light_c",
    "Aelin": "light_mu",
    "Wren": "light_mu",
    "Tam": "cure_light_wounds",
    "Mara": "cure_light_wounds",
    "Sefa": "cure_light_wounds",
    "Bryn": "cure_light_wounds",
}
"""A spell on each member's list that reverses: *light* reverses into *darkness*, and *cure light wounds* into
*cause light wounds*. The fighter has no list, so any spell does."""


# ---------------------------------------------------------------------- the moments


def _prepare(game: GameSession, name: str, *spell_ids: str) -> None:
    selections = tuple(MemorizedSpell(spell_id=spell_id) for spell_id in spell_ids)
    result = game.execute(PrepareSpells(character_id=pf.named(game, name), selections=selections))
    assert result.accepted, (name, [rejection.code for rejection in result.rejections])


def _new(seed: int = 7) -> GameSession:
    return GameSession.new(party(), pf.corridor(), seed=seed)


def _slept(seed: int = 7) -> GameSession:
    game = _new(seed)
    assert game.execute(Rest(kind="night")).accepted
    return game


def _prepared(seed: int = 7) -> GameSession:
    """After a night's rest, Wren prepares *light* and Mara *cure light wounds*. Nobody else prepares."""
    game = _slept(seed)
    _prepare(game, "Wren", "light_mu")
    _prepare(game, "Mara", "cure_light_wounds")
    return game


def _slept_again(seed: int = 7) -> GameSession:
    game = _prepared(seed)
    assert game.execute(Rest(kind="night")).accepted
    return game


def _encounter(seed: int = 7) -> GameSession:
    """The prepared party walks into the corridor and meets a goblin."""
    game = _prepared(seed)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    assert game.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=60)).accepted
    assert game.mode.value in ("encounter", "battle")
    return game


def _incapacitated(seed: int = 7) -> GameSession:
    """The prepared party in town, with Aelin and Wren asleep under a spell and Tam dead."""
    game = _prepared(seed)
    for name in ("Aelin", "Wren"):
        grant_condition(game.member(pf.named(game, name)), Condition.ASLEEP, None)
    tam = game.member(pf.named(game, "Tam"))
    tam.current_hp = 0
    grant_condition(tam, Condition.DEAD, None)
    return game


SCENARIOS: dict[str, Callable[..., GameSession]] = {
    "new": _new,
    "slept": _slept,
    "prepared": _prepared,
    "slept_again": _slept_again,
    "encounter": _encounter,
    "incapacitated": _incapacitated,
}


def scenario(name: str, seed: int = 7) -> GameSession:
    return SCENARIOS[name](seed)


CAN = (True, False)
"""`(can_prepare_now, needs_sleep_to_prepare)` for a caster who may prepare now."""
SLEEP = (False, True)
"""For a caster the sleep rule refuses, in town."""
NEITHER = (False, False)
"""For a member who casts nothing, and for a caster who has slept but whom the moment refuses."""


def _moment(default: tuple[bool, bool], **names: tuple[bool, bool]) -> dict[str, tuple[bool, bool]]:
    return {"Hild": NEITHER, **dict.fromkeys(CASTERS, default), **names}


EXPECTED = {
    # The party hasn't slept in a new session, so every caster needs a night's sleep first.
    "new": _moment(SLEEP),
    "slept": _moment(CAN),
    # Wren and Mara prepared after the party's one sleep, so they need another. The others haven't prepared since it.
    "prepared": _moment(CAN, Wren=SLEEP, Mara=SLEEP),
    "slept_again": _moment(CAN),
    # Nobody can prepare in an encounter (`session.command.wrong_mode`), and the sleep fact doesn't change.
    "encounter": _moment(NEITHER, Wren=SLEEP, Mara=SLEEP),
    # Aelin and Wren are asleep, and Tam is dead (`session.command.member_incapacitated`).
    "incapacitated": _moment(CAN, Aelin=NEITHER, Wren=SLEEP, Tam=NEITHER, Mara=SLEEP),
}


def _player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def _sheet(view: PlayerView, name: str) -> MemberView:
    return next(sheet for sheet in view.party if sheet.name == name)


def _moments(view: PlayerView) -> dict[str, tuple[bool, bool]]:
    return {sheet.name: (sheet.can_prepare_now, sheet.needs_sleep_to_prepare) for sheet in view.party}


def _fields(view: PlayerView) -> dict[str, tuple[str | None, bool, bool]]:
    return {
        sheet.name: (sheet.reverses_at, sheet.can_prepare_now, sheet.needs_sleep_to_prepare) for sheet in view.party
    }


# ---------------------------------------------------------------------- when a caster picks a spell's form


def _members() -> list:
    """Every member, marked for the chunk unless the field's default is already the member's value.

    The fighter's `reverses_at` is `None`, the default, so the fighter's cases run unmarked, as guards.
    """
    return [
        pytest.param(name, marks=pytest.mark.xfail(reason=CHUNK)) if form is not None else name
        for name, form in REVERSES_AT.items()
    ]


@pytest.mark.parametrize("name", _members())
def test_each_member_says_when_they_choose_a_spells_form(name):
    sheet = _sheet(_player_view(scenario("slept")), name)

    assert sheet.reverses_at == REVERSES_AT[name]


@pytest.mark.parametrize("name", _members())
def test_reverses_at_agrees_with_the_refusal_of_a_reversed_selection(name):
    """One reversed copy of a spell that reverses, from each member who has slept.

    An arcane caster's is accepted. A divine caster's is refused with `magic.memorize.divine_reverses_at_cast` alone,
    even Tam's, who has no slot for it, because the session doesn't count a copy it refuses for its form. The
    fighter's is refused with `magic.memorize.not_a_caster`.
    """
    game = scenario("slept")
    sheet = _sheet(_player_view(game), name)
    reversed_copy = MemorizedSpell(spell_id=REVERSIBLE[name], reversed=True)

    result = game.execute(PrepareSpells(character_id=sheet.id, selections=(reversed_copy,)))

    codes = [rejection.code for rejection in result.rejections]
    expected = {
        "memorize": [],
        "cast": ["magic.memorize.divine_reverses_at_cast"],
        None: ["magic.memorize.not_a_caster"],
    }[sheet.reverses_at]
    assert codes == expected
    assert sheet.reverses_at == REVERSES_AT[name]


@pytest.mark.xfail(reason=CHUNK)
def test_the_kind_comes_from_the_class_and_not_from_the_book():
    """Sefa and Bryn have the same book and the same list, and only their classes' casting tags differ."""
    view = _player_view(scenario("slept"))
    sefa, bryn = _sheet(view, "Sefa"), _sheet(view, "Bryn")

    assert sefa.spell_book == bryn.spell_book == ("cure_light_wounds",)
    assert (sefa.reverses_at, bryn.reverses_at) == ("memorize", "cast")


@pytest.mark.xfail(reason=CHUNK)
def test_reverses_at_doesnt_change_with_the_moment():
    """The kind is the class's, so it's the same before the party sleeps, during an encounter, and while asleep."""
    for name in SCENARIOS:
        view = _player_view(scenario(name))
        assert {sheet.name: sheet.reverses_at for sheet in view.party} == REVERSES_AT, name


# ---------------------------------------------------------------------- whether a member can prepare now


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("moment", list(EXPECTED))
def test_each_member_says_whether_they_can_prepare_now_and_whether_they_need_sleep(moment):
    view = _player_view(scenario(moment))

    assert _moments(view) == EXPECTED[moment]


def _selections(view: PlayerView, sheet: MemberView) -> list[tuple[MemorizedSpell, ...]]:
    """An empty selection, and one copy of the first spell at each spell level the member has a slot at."""
    picks = [()]
    for spell_level, slots in enumerate(sheet.spell_slots, start=1):
        at_level = [spell_id for spell_id in sheet.preparable_spells if view.spell_levels[spell_id] == spell_level]
        if slots and at_level:
            picks.append((MemorizedSpell(spell_id=at_level[0]),))
    return picks


def _members_at_moments() -> list:
    """Every member at every moment, marked for the chunk unless both fields' defaults are already the values.

    Both default to false, which is right for the fighter and for a caster whom the moment refuses after a sleep,
    so those cases run unmarked, as guards.
    """
    return [
        pytest.param(moment, name, marks=pytest.mark.xfail(reason=CHUNK))
        if EXPECTED[moment][name] != NEITHER
        else pytest.param(moment, name)
        for moment in EXPECTED
        for name in REVERSES_AT
    ]


@pytest.mark.parametrize(("moment", "name"), _members_at_moments())
def test_can_prepare_now_is_true_exactly_when_the_session_accepts_a_fitting_selection(moment, name):
    """Each selection that fits `preparable_spells` and `spell_slots`, sent from a fresh copy of the moment.

    When the member can't prepare now, each refusal of a caster is one of the moment's codes, and the sleep rule's
    code comes exactly when `needs_sleep_to_prepare` is true and nothing the session checks first applies. A
    selection made from the lists never draws a refusal for the spell or the member.
    """
    view = _player_view(scenario(moment))
    sheet = _sheet(view, name)
    first_checks_pass = view.mode in ("town", "exploring") and not incapacitated(scenario(moment).member(sheet.id))

    for selections in _selections(view, sheet):
        game = scenario(moment)
        result = game.execute(PrepareSpells(character_id=sheet.id, selections=selections))
        codes = {rejection.code for rejection in result.rejections}
        assert result.accepted == sheet.can_prepare_now, (selections, codes)
        if name in CASTERS and not result.accepted:
            assert codes <= MOMENT_CODES, (selections, codes)
            assert ("magic.memorize.needs_sleep" in codes) == (sheet.needs_sleep_to_prepare and first_checks_pass)
    assert (sheet.can_prepare_now, sheet.needs_sleep_to_prepare) == EXPECTED[moment][name]


@pytest.mark.parametrize("moment", [pytest.param("new", marks=pytest.mark.xfail(reason=CHUNK)), "slept", "prepared"])
def test_a_member_who_casts_nothing_is_refused_as_no_caster_whether_or_not_the_party_has_slept(moment):
    """The fighter's class casts nothing, which doesn't depend on the moment, so neither does the refusal.

    Before, a fighter in a new session was refused with `magic.memorize.needs_sleep`, which said the fighter could
    prepare after a night's rest, and after the rest with `magic.memorize.not_a_caster`.
    """
    game = scenario(moment)
    hild = pf.named(game, "Hild")

    result = game.execute(PrepareSpells(character_id=hild))

    assert [(rejection.code, rejection.params) for rejection in result.rejections] == [
        ("magic.memorize.not_a_caster", {"class": "fighter"})
    ]
    assert _sheet(_player_view(game), "Hild").needs_sleep_to_prepare is False


@pytest.mark.xfail(reason=CHUNK)
def test_preparing_turns_the_fields_over_until_the_next_sleep():
    """Wren prepares, needs a sleep, sleeps, and can prepare again, with nobody else changing."""
    game = scenario("slept")
    assert _sheet(_player_view(game), "Wren").can_prepare_now

    _prepare(game, "Wren", "light_mu")
    after = _player_view(game)
    assert (_sheet(after, "Wren").can_prepare_now, _sheet(after, "Wren").needs_sleep_to_prepare) == SLEEP
    assert _moments(after) == {**EXPECTED["slept"], "Wren": SLEEP}

    assert game.execute(Rest(kind="night")).accepted
    assert _moments(_player_view(game)) == EXPECTED["slept"]


# ---------------------------------------------------------------------- saves


def _round_trip(game: GameSession) -> GameSession:
    return load_game(json.loads(json.dumps(save_game(game))))


@pytest.mark.xfail(reason=CHUNK)
def test_a_session_restored_from_a_save_has_the_same_fields():
    """The save is taken between Wren's and Mara's preparation and the next sleep, and the restored session has no
    events from before the save to read."""
    game = scenario("prepared")

    loaded = _round_trip(game)

    assert _fields(_player_view(loaded)) == _fields(_player_view(game))
    assert _moments(_player_view(loaded)) == EXPECTED["prepared"]
    assert loaded.execute(Rest(kind="night")).accepted
    assert _moments(_player_view(loaded)) == EXPECTED["slept_again"]
    _prepare(loaded, "Bryn")
    assert _moments(_player_view(loaded)) == {**EXPECTED["slept_again"], "Bryn": SLEEP}


@pytest.mark.xfail(reason=CHUNK)
def test_a_save_from_osrlib_2_8_0_gives_the_fields():
    """The fields come from the classes and the sleep counts, which a 2.8.0 save already has.

    The whole view of the loaded save is the view of the same moment built now, so nothing else the save embeds
    reads differently either.
    """
    document = json.loads(SAVE_2_8_0.read_text(encoding="utf-8"))
    assert document["engine_version"] == "2.8.0"

    loaded = load_game(document)

    view = _player_view(loaded)
    assert {sheet.name: sheet.reverses_at for sheet in view.party} == REVERSES_AT
    assert _moments(view) == EXPECTED["prepared"]
    assert view == _player_view(scenario("prepared"))
    assert loaded.execute(Rest(kind="night")).accepted
    assert _moments(_player_view(loaded)) == EXPECTED["slept_again"]


# ---------------------------------------------------------------------- what the referee knows


def _twin(moment: str) -> GameSession:
    """The same moment with what only the referee knows changed: another master seed, and Mara's unidentified
    potion a potion of delusion in place of a potion of healing. Both potions show as `"a potion"`."""
    game = scenario(moment, seed=8191)
    mara = game.member(pf.named(game, "Mara"))
    mara.inventory.items.append(MagicItemInstance(instance_id="magic-item-9001", template_id="potion_of_delusion"))
    return game


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("moment", list(EXPECTED))
def test_a_twin_that_differs_in_what_the_referee_knows_has_the_same_fields(moment):
    game = scenario(moment)
    game.member(pf.named(game, "Mara")).inventory.items.append(
        MagicItemInstance(instance_id="magic-item-9001", template_id="potion_of_healing")
    )

    fields, twin = _fields(_player_view(game)), _fields(_player_view(_twin(moment)))

    assert fields == twin
    assert {name: (can, needs) for name, (_, can, needs) in fields.items()} == EXPECTED[moment]


# ---------------------------------------------------------------------- the schema and the docs


def test_a_player_view_from_before_the_fields_still_parses():
    """A client that kept a 2.8.0 player view's JSON reads it with every new field at its default."""
    document = json.loads(_player_view(scenario("prepared")).model_dump_json())
    for sheet in document["party"]:
        for field in ("reverses_at", "can_prepare_now", "needs_sleep_to_prepare"):
            sheet.pop(field)
    document.pop("valuable_names")

    view = PlayerView.model_validate(document)

    assert all(
        (sheet.reverses_at, sheet.can_prepare_now, sheet.needs_sleep_to_prepare) == (None, False, False)
        for sheet in view.party
    )
    assert view.valuable_names == {}


def _bullet(changelog: str, issue: str) -> str:
    """The changelog bullets that cite `issue`, wherever they are in the file, joined into one string."""
    lines = [line for line in changelog.splitlines() if line.startswith("- ") and f"({issue})" in line]
    assert lines, f"no changelog bullet cites ({issue})"
    return "\n".join(lines)


@pytest.mark.xfail(reason=CHUNK)
def test_the_views_guide_and_the_changelog_describe_the_fields():
    root = Path(__file__).parent.parent
    guide = (root / "docs" / "guides" / "views-and-visibility.md").read_text(encoding="utf-8")
    bullet = _bullet((root / "CHANGELOG.md").read_text(encoding="utf-8"), "#338")

    for name in ("reverses_at", "can_prepare_now", "needs_sleep_to_prepare", "magic.memorize.needs_sleep"):
        assert name in guide, name
    for name in ("reverses_at", "can_prepare_now", "needs_sleep_to_prepare"):
        assert name in bullet, name
