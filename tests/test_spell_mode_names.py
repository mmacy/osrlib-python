"""Every mode of a spell with more than one has a label and prose of its own (issue #340).

A client's cast dialog lists a form's modes for the player to choose from. In 2.8.0 the three modes of
*darkness*, the reverse of *light*, had the same prose and the same targeting, so a client could tell them
apart only by their keys. `SpellMode.display_name` is a short label for each mode, and each mode of the
reverses of *light* and *continual light* has the part of the reverse's text that covers it.

The labels are instructions in sentence case, with no closing punctuation. A built-in label comes from the
lead-in the OSE SRD prints before a usage, turned into an instruction where the lead-in is a gerund
("Blinding a creature:" is "Blind a creature"), and is written for osrlib where the page prints no lead-in or
one that isn't an instruction ("Against a group:" is "Hold a group"). A mode that has no label, such as the
one mode of a single-mode spell or a mode of a spell written for 2.8.0, gets one from its key.

The compiled data in `src/osrlib/data/spells.json` is what these tests read, so they pass once the SRD
compiler fills the labels and divides the prose.
"""

import json
from pathlib import Path

import pytest

import positions_fixtures as pf
from osrlib.core.spells import SpellMode, SpellTemplate
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_spells
from osrlib.persistence import load_game
from test_custom_spells import wizard

CHUNK = "chunk: spell-mode-names"

REPO = Path(__file__).resolve().parent.parent
SAVE_2_8_0 = Path(__file__).parent / "saves" / "spell_mode_names_2.8.0.json"
"""A save written by osrlib 2.8.0, before modes had labels.

Wren, a 3rd-level magic-user, has the adventure's own spell `gloaming` (the built-in `light_mu` under a new id
and name, as 2.8.0 dumped it) and the built-in `light_mu` in her book. After a night's rest she prepared
`gloaming` reversed and `light_mu`.
"""

# Each form of a built-in spell that has more than one mode: its modes' keys and labels, in the form's order.
# The OSE SRD lead-ins the labels come from are in the comments. Where a comment gives none, the page prints
# no lead-in for the usage, or a lead-in that is a noun or a phrase rather than something the caster does.
MULTI_MODE_LABELS: dict[tuple[str, bool], tuple[tuple[str, str], ...]] = {
    # "Battle:" and "Ritual:" name the situations, not what the caster does.
    ("bless", False): (("battle", "Bless allies in battle"), ("ritual", "Bless a ritual")),
    # One paragraph: "a charm upon either 3d6 creatures of 3 HD or less or a single creature of more than 3 HD".
    ("charm_monster", False): (("individual", "Charm an individual"), ("group", "Charm a group")),
    # "Conjuring light:", "Blinding a creature:", "Cancelling darkness:".
    ("continual_light_c", False): (
        ("illuminate", "Conjure light"),
        ("blind", "Blind a creature"),
        ("cancel", "Cancel darkness"),
    ),
    # The reverse prints one paragraph and no lead-ins. Its labels mirror the normal form's.
    ("continual_light_c", True): (
        ("darken", "Conjure darkness"),
        ("blind", "Blind a creature"),
        ("cancel", "Cancel light"),
    ),
    ("continual_light_mu", False): (
        ("illuminate", "Conjure light"),
        ("blind", "Blind a creature"),
        ("cancel", "Cancel darkness"),
    ),
    ("continual_light_mu", True): (
        ("darken", "Conjure darkness"),
        ("blind", "Blind a creature"),
        ("cancel", "Cancel light"),
    ),
    # "Cure a subject of any disease:" and "Kill green slime:" are instructions already.
    ("cure_disease", False): (("cure", "Cure a subject of any disease"), ("kill_green_slime", "Kill green slime")),
    # "Healing a living subject:" and "Curing paralysis:".
    ("cure_light_wounds", False): (("heal", "Heal a living subject"), ("cure_paralysis", "Cure paralysis")),
    # "Against an individual:" and "Against a group:".
    ("hold_monster", False): (("individual", "Hold an individual"), ("group", "Hold a group")),
    ("hold_person_c", False): (("individual", "Hold an individual"), ("group", "Hold a group")),
    ("hold_person_mu", False): (("individual", "Hold an individual"), ("group", "Hold a group")),
    ("light_c", False): (("illuminate", "Conjure light"), ("blind", "Blind a creature"), ("cancel", "Cancel darkness")),
    ("light_c", True): (("darken", "Conjure darkness"), ("blind", "Blind a creature"), ("cancel", "Cancel light")),
    ("light_mu", False): (
        ("illuminate", "Conjure light"),
        ("blind", "Blind a creature"),
        ("cancel", "Cancel darkness"),
    ),
    ("light_mu", True): (("darken", "Conjure darkness"), ("blind", "Blind a creature"), ("cancel", "Cancel light")),
    # "Characters:" and "Items:" name the subjects. The words are the usages' own: "Neutralize the effects of
    # poison on a character" and "Remove poison from an item".
    ("neutralize_poison", False): (
        ("neutralize", "Neutralize poison on a character"),
        ("items", "Remove poison from an item"),
    ),
    # "Restore life:" and "Destroy undead:" are instructions already.
    ("raise_dead", False): (("restore_life", "Restore life"), ("destroy_undead", "Destroy undead")),
    # No numbered usages: the area, and "Casting upon a creature:" among the bullets.
    ("silence_15_radius", False): (("creature", "Silence a creature"), ("area", "Silence an area")),
    # "The spell may target either: 1. A single creature with 4+1 Hit Dice ... 2. A total of 2d8 Hit Dice".
    ("sleep", False): (("single_4_plus", "Put a creature to sleep"), ("hd_budget", "Put a group to sleep")),
}

# The reverse of light, as the SRD prints it: "Creates a 15’ radius area of magical blackness, preventing normal
# sight (but not infravision). Like *light*, it may alternatively be used to blind creatures or to dispel a
# *light* spell." Each mode takes the words that cover it, and the shared "Like light, it may alternatively be
# used to" goes with both of the modes the second sentence names.
DARKNESS = (
    "Creates a 15’ radius area of magical blackness, preventing normal sight (but not infravision). "
    "Like light, it may alternatively be used to blind creatures or to dispel a light spell."
)
DARKNESS_MODE_PROSE = {
    "darken": "Creates a 15’ radius area of magical blackness, preventing normal sight (but not infravision).",
    "blind": "Like light, it may alternatively be used to blind creatures.",
    "cancel": "Like light, it may alternatively be used to dispel a light spell.",
}
CONTINUAL_DARKNESS = (
    "Creates a 30’ radius area of magical blackness, preventing both normal sight and infravision. "
    "Light sources brought within the area of darkness do not illuminate it. "
    "Like continual light, it may alternatively be used to blind creatures or to dispel a continual light spell."
)
CONTINUAL_DARKNESS_MODE_PROSE = {
    "darken": (
        "Creates a 30’ radius area of magical blackness, preventing both normal sight and infravision. "
        "Light sources brought within the area of darkness do not illuminate it."
    ),
    "blind": "Like continual light, it may alternatively be used to blind creatures.",
    "cancel": "Like continual light, it may alternatively be used to dispel a continual light spell.",
}

# Charm monster's page is one passage for both modes. Its first sentence offers the two: "This spell places a
# charm upon either 3d6 creatures of 3 HD or less or a single creature of more than 3 HD." Each mode keeps the
# alternative it is, and the rest of the passage, which covers both.
CHARM_MONSTER_REST = (
    " Each subject must make a save versus spells or be charmed as follows:\n"
    "Friendship: Charmed creatures regard the caster as a close friend and will come to the caster’s defence.\n"
    "Commands: If they share a language, the caster may give charmed creatures commands, which they will obey.\n"
    "Subject’s nature: Charmed creatures may resist commands that contradict their habits or alignment.\n"
    "Suicidal commands: Are always refused.\n"
    "Restrictions: Undead are not affected.\n"
    "Duration: The charm lasts indefinitely, but the subject is allowed to make further saves versus spells at "
    "intervals, depending on its INT score. If one of these saves succeeds, the spell ends.\n"
    "INT 3–8: New save once every month.\n"
    "INT 9–12: New save each week.\n"
    "INT 13–18: New save once every day."
)
CHARM_MONSTER_PROSE = {
    "individual": "This spell places a charm upon a single creature of more than 3 HD." + CHARM_MONSTER_REST,
    "group": "This spell places a charm upon 3d6 creatures of 3 HD or less." + CHARM_MONSTER_REST,
}

# Silence 15' radius: the page describes the area, then "Casting upon a creature:" as a bullet. The area mode has
# the text without that bullet. The creature mode has all of it, because the area that moves with the creature
# is the same silent area.
SILENCE_AREA = (
    "A 15’ radius area is rendered utterly silent.\n"
    "Within the area: All sound is prevented. Conversation and spell casting are impossible.\n"
    "Noise from outside the area: Can be heard by those within it."
)
SILENCE_UPON_A_CREATURE = (
    "Casting upon a creature: Silence may be cast upon a creature, which must save versus spells. If the save "
    "fails, the 15’ radius area of silence moves with the creature. If the save succeeds, the spell’s effect "
    "remains stationary—the targeted creature may move out of it."
)


def _forms() -> list[tuple[SpellTemplate, bool, tuple[SpellMode, ...]]]:
    """Every form of every built-in spell: the template, whether it's the reverse, and the form's modes."""
    forms = []
    for spell in load_spells().spells:
        forms.append((spell, False, spell.modes))
        if spell.reversed_form is not None:
            forms.append((spell, True, spell.reversed_form.modes))
    return forms


def _label_from_key(key: str) -> str:
    """The label a mode without one gets: underscores become spaces, and the first letter becomes a capital."""
    words = key.replace("_", " ")
    return words[:1].upper() + words[1:]


# ---------------------------------------------------------------------- the built-in labels


@pytest.mark.xfail(reason=CHUNK)
def test_every_multi_mode_form_has_the_labels_the_srd_supports():
    """Each form with more than one mode has the labels in `MULTI_MODE_LABELS`, and there are no other such forms."""
    multi_mode = {(spell.id, reversed): modes for spell, reversed, modes in _forms() if len(modes) > 1}
    assert set(multi_mode) == set(MULTI_MODE_LABELS)
    for form, modes in multi_mode.items():
        assert tuple((mode.key, mode.display_name) for mode in modes) == MULTI_MODE_LABELS[form], form


@pytest.mark.xfail(reason=CHUNK)
def test_sibling_modes_never_share_a_label():
    """No two modes of one form, normal or reversed, have the same label, so a dialog can list them by label."""
    for spell, reversed, modes in _forms():
        labels = [mode.display_name for mode in modes]
        assert all(labels), (spell.id, reversed)
        assert len(set(labels)) == len(labels), (spell.id, reversed, labels)


@pytest.mark.xfail(reason=CHUNK)
def test_every_built_in_label_is_an_instruction_in_sentence_case():
    """A label starts with a capital, has no space at either end, and ends without punctuation."""
    for spell, reversed, modes in _forms():
        for mode in modes:
            label = mode.display_name
            assert label, (spell.id, reversed, mode.key)
            assert label == label.strip(), (spell.id, mode.key, label)
            assert label[0].isupper(), (spell.id, mode.key, label)
            assert label[-1] not in ".,:;!?", (spell.id, mode.key, label)


@pytest.mark.xfail(reason=CHUNK)
def test_the_mode_of_a_single_mode_form_is_labelled_from_its_key():
    """A form with one mode has no choice to label, so its mode gets the label a mode without one gets."""
    spells = load_spells()
    assert spells.get("fire_ball").mode("damage").display_name == "Damage"
    assert spells.get("magic_missile").mode("missiles").display_name == "Missiles"
    assert spells.get("cure_light_wounds").mode("harm", reversed=True).display_name == "Harm"
    assert spells.get("bless").mode("battle", reversed=True).display_name == "Battle"  # blight's one mode
    assert spells.get("speak_with_plants").mode("cast").display_name == "Cast"  # a manual spell's one mode
    for spell, reversed, modes in _forms():
        if len(modes) == 1:
            assert modes[0].display_name == _label_from_key(modes[0].key), (spell.id, reversed)


# ---------------------------------------------------------------------- the prose of each mode


@pytest.mark.xfail(reason=CHUNK)
def test_sibling_modes_never_share_prose():
    """No two modes of one form have the same prose, so the prose shown for a mode describes that mode."""
    for spell, reversed, modes in _forms():
        prose = [mode.prose for mode in modes]
        assert all(prose), (spell.id, reversed)
        assert len(set(prose)) == len(prose), (spell.id, reversed, [mode.key for mode in modes])


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("spell_id", ["light_c", "light_mu"])
def test_each_mode_of_darkness_has_the_text_that_covers_it(spell_id):
    modes = load_spells().get(spell_id).reversed_form.modes
    assert {mode.key: mode.prose for mode in modes} == DARKNESS_MODE_PROSE


@pytest.mark.xfail(reason=CHUNK)
@pytest.mark.parametrize("spell_id", ["continual_light_c", "continual_light_mu"])
def test_each_mode_of_continual_darkness_has_the_text_that_covers_it(spell_id):
    modes = load_spells().get(spell_id).reversed_form.modes
    assert {mode.key: mode.prose for mode in modes} == CONTINUAL_DARKNESS_MODE_PROSE


@pytest.mark.xfail(reason=CHUNK)
def test_each_mode_of_charm_monster_has_its_own_alternative():
    modes = load_spells().get("charm_monster").modes
    assert {mode.key: mode.prose for mode in modes} == CHARM_MONSTER_PROSE


@pytest.mark.xfail(reason=CHUNK)
def test_the_area_mode_of_silence_leaves_out_casting_upon_a_creature():
    silence = load_spells().get("silence_15_radius")
    assert silence.mode("area").prose == SILENCE_AREA
    assert silence.mode("creature").prose == SILENCE_AREA + "\n" + SILENCE_UPON_A_CREATURE


def test_the_reverse_keeps_its_whole_text():
    """`ReversedForm.prose` is the whole of the reverse's text, which its modes now divide between them."""
    spells = load_spells()
    for spell_id in ("light_c", "light_mu"):
        assert spells.get(spell_id).reversed_form.prose == DARKNESS
    for spell_id in ("continual_light_c", "continual_light_mu"):
        assert spells.get(spell_id).reversed_form.prose == CONTINUAL_DARKNESS


def test_the_normal_light_modes_keep_the_prose_of_their_usages():
    """The normal forms' modes already had a usage each, and their prose doesn't change."""
    spells = load_spells()
    for spell_id in ("light_c", "light_mu", "continual_light_c", "continual_light_mu"):
        modes = spells.get(spell_id).modes
        assert [mode.prose.split(":", 1)[0] for mode in modes] == [
            "Conjuring light",
            "Blinding a creature",
            "Cancelling darkness",
        ]


# ---------------------------------------------------------------------- a mode you write


@pytest.mark.xfail(reason=CHUNK)
def test_a_mode_you_write_without_a_label_gets_one_from_its_key():
    assert SpellMode(key="darken", manual=True).display_name == "Darken"
    assert SpellMode.model_validate({"key": "kill_green_slime", "manual": True}).display_name == "Kill green slime"
    assert SpellMode(key="snuff", manual=True, display_name="").display_name == "Snuff"


def test_a_mode_you_write_keeps_the_label_you_give_it():
    mode = SpellMode(key="snuff", manual=True, display_name="Snuff the candles")
    assert mode.display_name == "Snuff the candles"
    assert SpellMode.model_validate(mode.model_dump()) == mode


@pytest.mark.xfail(reason=CHUNK)
def test_a_spell_document_from_2_8_0_loads_with_a_label_on_every_mode():
    """A spell dumped before modes had labels validates, and each of its modes is labelled from its key."""
    document = load_spells().get("light_mu").model_dump(mode="json")
    for mode in (*document["modes"], *document["reversed_form"]["modes"]):
        del mode["display_name"]

    spell = SpellTemplate.model_validate(document)

    assert [mode.display_name for mode in spell.modes] == ["Illuminate", "Blind", "Cancel"]
    assert [mode.display_name for mode in spell.reversed_form.modes] == ["Darken", "Blind", "Cancel"]


@pytest.mark.xfail(reason=CHUNK)
def test_a_save_from_osrlib_2_8_0_labels_the_adventure_spells_from_their_keys():
    """A bundled spell is its author's text, so loading labels it from its keys and keeps its prose.

    The built-in spells come from the catalog, so the same session has the compiled labels for them. A fresh
    session from the same adventure document has the same catalog as the restored one.
    """
    document = json.loads(SAVE_2_8_0.read_text(encoding="utf-8"))
    assert document["engine_version"] == "2.8.0"

    game = load_game(document)

    gloaming = game.effective_spells.get("gloaming")
    assert [mode.display_name for mode in gloaming.modes] == ["Illuminate", "Blind", "Cancel"]
    assert [mode.display_name for mode in gloaming.reversed_form.modes] == ["Darken", "Blind", "Cancel"]
    assert [mode.prose for mode in gloaming.reversed_form.modes] == [DARKNESS, DARKNESS, DARKNESS]
    light = game.effective_spells.get("light_mu")
    assert [mode.display_name for mode in light.reversed_form.modes] == [
        "Conjure darkness",
        "Blind a creature",
        "Cancel light",
    ]

    adventure = Adventure.model_validate(document["payload"]["adventure"])
    party = Party(members=[pf.character("Hild", "fighter"), wizard("gloaming", "light_mu")])
    fresh = GameSession.new(party, adventure, seed=7)
    assert fresh.effective_spells == game.effective_spells


# ---------------------------------------------------------------------- the changelog


@pytest.mark.xfail(reason=CHUNK)
def test_the_changelog_names_the_labels_and_shows_a_changed_mode_prose():
    """The (#340) bullet names `display_name` and gives the old and the new prose of one mode of darkness."""
    bullets = [line for line in (REPO / "CHANGELOG.md").read_text(encoding="utf-8").splitlines() if "(#340)" in line]
    assert bullets, "no CHANGELOG.md bullet for #340"
    text = " ".join(bullets)
    assert "display_name" in text
    assert DARKNESS in text, "the old prose of a mode of darkness, the whole reverse's text"
    # The new prose of `darken` is the start of the old text, so only `blind` and `cancel` show a change.
    assert any(DARKNESS_MODE_PROSE[key] in text for key in ("blind", "cancel")), "the new prose of that mode"
