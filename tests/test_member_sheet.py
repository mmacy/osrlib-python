"""The character sheet in the player view (issues #227 and #231).

A client on the far side of a network has only the
[`PlayerView`][osrlib.crawl.views.PlayerView], so each
[`MemberView`][osrlib.crawl.views.MemberView] has to have every number on a B/X character sheet.
These tests read the stock party's sheets from the view and compare them with the SRD's values and
with the live [`Character`][osrlib.core.character.Character].
"""

import json

from crawl_fixtures import _member, build_adventure, build_party
from osrlib.core.character import Character
from osrlib.core.classes import level_title
from osrlib.core.effects import kill
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.core.ruleset import EncumbranceMode, Ruleset
from osrlib.crawl.commands import EquipItem, GrantItem
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

FIGHTER, THIEF, CLERIC, MAGIC_USER = range(4)
"""The stock party's marching order."""

THIEF_SKILLS = (
    "climb_sheer_surfaces",
    "find_remove_treasure_traps",
    "hear_noise",
    "hide_in_shadows",
    "move_silently",
    "open_locks",
    "pick_pockets",
)


def session(party: Party | None = None, ruleset: Ruleset | None = None) -> GameSession:
    return GameSession.new(
        party or build_party(), build_adventure(wandering_chance=0), seed=7, ruleset=ruleset or Ruleset()
    )


def sheets(game: GameSession):
    return game.view(Visibility.PLAYER).party


def veteran_of(class_id: str, level: int) -> Character:
    """A stock member raised to `level`, with the experience points that level needs."""
    document = _member("Old Hand", class_id).model_dump()
    definition = _member("Old Hand", class_id).definition
    return Character.model_validate({**document, "level": level, "xp": definition.row(level).xp})


# ---------------------------------------------------------------------- the SRD's numbers


def test_a_first_level_fighters_sheet():
    sheet = sheets(session())[FIGHTER]
    assert sheet.race == "human"
    assert sheet.alignment == "lawful"
    assert sheet.title == "Veteran"
    assert (sheet.xp, sheet.xp_next_level) == (0, 2000)
    assert sheet.scores == {"str": 11, "int": 11, "wis": 11, "dex": 11, "con": 11, "cha": 11}
    assert (sheet.thac0, sheet.attack_bonus) == (19, 0)
    assert (sheet.armour_class, sheet.armour_class_ascending) == (9, 10)
    assert sheet.saves == {"death": 12, "wands": 13, "paralysis": 14, "breath": 15, "spells": 16}
    assert sheet.movement_rate_feet == 120
    assert sheet.languages == ("alignment_lawful", "common")
    assert sheet.open_doors_chance == 2
    assert sheet.spell_book == ()
    assert sheet.thief_skills == {}


def test_a_first_level_thiefs_skills():
    sheet = sheets(session())[THIEF]
    assert sheet.thief_skills == {
        "climb_sheer_surfaces": 87,
        "find_remove_treasure_traps": 10,
        "hear_noise": 2,
        "hide_in_shadows": 10,
        "move_silently": 20,
        "open_locks": 15,
        "pick_pockets": 20,
    }
    assert tuple(sheet.thief_skills) == THIEF_SKILLS


def test_a_magic_users_spell_book():
    sheet = sheets(session())[MAGIC_USER]
    assert sheet.spell_book == ("sleep",)
    assert sheet.title == "Medium"


def test_ability_modifiers_come_from_the_scores():
    document = _member("Brakk", "fighter").model_dump()
    document["scores"] = {"str": 18, "int": 9, "wis": 4, "dex": 16, "con": 13, "cha": 6}
    game = session(Party(members=[Character.model_validate(document)]))
    sheet = sheets(game)[0]
    assert sheet.scores == {"str": 18, "int": 9, "wis": 4, "dex": 16, "con": 13, "cha": 6}
    assert sheet.melee_modifier == 3
    assert sheet.open_doors_chance == 5
    assert sheet.magic_save_modifier == -2
    assert sheet.missile_modifier == 2
    assert sheet.initiative_modifier == 1
    assert sheet.hit_point_modifier == 1
    assert sheet.npc_reaction_modifier == -1
    assert (sheet.armour_class, sheet.armour_class_ascending) == (7, 12)


# ---------------------------------------------------------------------- the view against the live character


def test_every_sheet_matches_its_character():
    game = session(ruleset=Ruleset(encumbrance=EncumbranceMode.DETAILED))
    fighter = game.party.members[FIGHTER]
    for item_id in ("plate_mail", "shield", "sword"):
        assert game.execute(GrantItem(character_id=fighter.id, item_id=item_id)).accepted
        assert game.execute(EquipItem(character_id=fighter.id, item_id=item_id)).accepted
    for sheet, member in zip(sheets(game), game.party.members, strict=True):
        definition = member.definition
        assert sheet.race == member.race
        assert sheet.alignment == member.alignment.value
        assert sheet.title == level_title(definition, member.level)
        assert sheet.xp == member.xp
        assert sheet.xp_next_level == definition.row(member.level + 1).xp
        assert sheet.scores == {ability.value: score for ability, score in member.scores.items()}
        assert sheet.melee_modifier == member.melee_modifier
        assert sheet.missile_modifier == member.missile_modifier
        assert sheet.initiative_modifier == member.initiative_modifier
        assert sheet.hit_point_modifier == member.hit_point_modifier
        assert sheet.magic_save_modifier == member.magic_save_modifier
        assert sheet.npc_reaction_modifier == member.npc_reaction_modifier
        assert sheet.open_doors_chance == member.open_doors_chance
        assert sheet.thac0 == member.thac0
        assert sheet.attack_bonus == member.attack_bonus
        assert sheet.armour_class == member.armour_class
        assert sheet.armour_class_ascending == member.armour_class_ascending
        assert sheet.saves == member.saves.model_dump()
        assert sheet.movement_rate_feet == member.movement_rate(game.ruleset)
        assert sheet.languages == member.languages
        assert sheet.spell_book == member.spell_book
    armoured = sheets(game)[FIGHTER]
    assert (armoured.armour_class, armoured.armour_class_ascending) == (2, 17)
    assert armoured.movement_rate_feet < 120, "plate mail slows a member under detailed encumbrance"


def test_the_movement_rate_follows_the_sessions_ruleset():
    for mode in EncumbranceMode:
        game = session(ruleset=Ruleset(encumbrance=mode))
        fighter = game.party.members[FIGHTER]
        game.execute(GrantItem(character_id=fighter.id, item_id="plate_mail"))
        game.execute(EquipItem(character_id=fighter.id, item_id="plate_mail"))
        assert sheets(game)[FIGHTER].movement_rate_feet == fighter.movement_rate(game.ruleset), mode


# ---------------------------------------------------------------------- armour class and unidentified items


def test_armour_class_counts_an_unidentified_magic_shield():
    game = session()
    fighter = game.party.members[FIGHTER]
    assert game.execute(GrantItem(character_id=fighter.id, item_id="shield")).accepted
    assert game.execute(EquipItem(character_id=fighter.id, item_id="shield")).accepted
    plain = sheets(game)[FIGHTER]
    assert (plain.armour_class, plain.armour_class_ascending) == (8, 11)

    assert game.execute(GrantItem(character_id=fighter.id, item_id="shield_plus_1")).accepted
    magic = next(item for item in fighter.inventory.items if isinstance(item, MagicItemInstance))
    assert not magic.identified
    assert game.execute(EquipItem(character_id=fighter.id, item_id=magic.instance_id)).accepted

    sheet = sheets(game)[FIGHTER]
    assert (sheet.armour_class, sheet.armour_class_ascending) == (7, 12)
    assert (sheet.armour_class, sheet.armour_class_ascending) == (fighter.armour_class, fighter.armour_class_ascending)
    # The pack still doesn't say what the shield is.
    assert sheet.inventory["shield"]["display"] == "a shield with a faint aura"
    assert "template_id" not in sheet.inventory["shield"]


# ---------------------------------------------------------------------- the ends of a class's tables


def test_past_the_last_title_and_at_the_highest_level():
    game = session(Party(members=[veteran_of("fighter", 9), veteran_of("fighter", 10), veteran_of("fighter", 14)]))
    lord, past_titles, highest = sheets(game)
    assert lord.title == "Lord (Lady)"
    assert lord.xp_next_level == lord_definition_xp(10)
    assert past_titles.title is None
    assert past_titles.xp_next_level == lord_definition_xp(11)
    assert highest.title is None
    assert highest.xp_next_level is None
    assert highest.thac0 == 10
    assert highest.saves == {"death": 4, "wands": 5, "paralysis": 6, "breath": 5, "spells": 8}


def lord_definition_xp(level: int) -> int:
    return _member("Old Hand", "fighter").definition.row(level).xp


def test_a_thiefs_skills_follow_the_level():
    game = session(Party(members=[veteran_of("thief", 5)]))
    sheet = sheets(game)[0]
    row = next(row for row in game.party.members[0].definition.thief_skills if row.level == 5)
    assert sheet.thief_skills == {skill: getattr(row, skill) for skill in THIEF_SKILLS}
    assert sheet.thief_skills["open_locks"] > 15


# ---------------------------------------------------------------------- every member, in every state


def test_a_dead_member_still_has_a_sheet():
    game = session()
    kill(game.party.members[CLERIC])
    sheet = sheets(game)[CLERIC]
    assert "dead" in sheet.conditions
    assert sheet.title == "Acolyte"
    assert sheet.saves == game.party.members[CLERIC].saves.model_dump()
    assert sheet.scores["wis"] == 13


def test_the_sheet_is_plain_json():
    dumped = [sheet.model_dump(mode="json") for sheet in sheets(session())]
    assert json.loads(json.dumps(dumped)) == dumped
    assert dumped[FIGHTER]["saves"]["death"] == 12
    assert dumped[THIEF]["thief_skills"]["hear_noise"] == 2
