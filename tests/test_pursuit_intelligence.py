"""Pursuit bait follows each monster's intelligence as its SRD page states it (#366).

`Evasion_and_Pursuit.md`: "Intelligent monsters have a 3-in-6 chance of stopping pursuit to
pick up treasure dropped by fleeing characters" and "Unintelligent monsters have a 3-in-6
chance of stopping pursuit to eat food dropped by fleeing characters."
"""

import re
from pathlib import Path

import pytest

from crawl_fixtures import build_adventure, build_party
from osrlib.core.items import Coins
from osrlib.core.monsters import MonsterTemplate
from osrlib.core.tables import ReactionResult
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import EnterDungeon, EquipItem, Evade, GrantCoins, GrantItem, LightSource
from osrlib.crawl.session import ENCOUNTER_STREAM, GameSession
from osrlib.data import load_monsters
from test_encounter import start

SRD = Path(__file__).resolve().parents[1] / "srd"

# Every built-in monster whose SRD page states its intelligence or its lack of it, with the
# page's words. Every other built-in template leaves `intelligent` as None.
CURATED = {
    "basilisk": (False, "Unintelligent, but highly magical."),
    "black_pudding": (False, "mindless blobs of amorphous black jelly"),
    "blink_dog": (True, "Highly intelligent, dingo-like dogs"),
    "crab_giant": (False, "Unintelligent crustaceans"),
    "djinni_lesser": (True, "free-willed, intelligent beings from the elemental plane of air"),
    "doppelganger": (True, "Intelligent, human-sized shape-shifters"),
    "efreeti_lesser": (True, "free-willed, intelligent beings from the elemental plane of fire"),
    "flame_salamander": (True, "intelligent serpents with lizard-like heads and legs"),
    "hellhound_3": (True, "Monstrous, cunning, and very intelligent hounds"),
    "hellhound_4": (True, "Monstrous, cunning, and very intelligent hounds"),
    "hellhound_5": (True, "Monstrous, cunning, and very intelligent hounds"),
    "hellhound_6": (True, "Monstrous, cunning, and very intelligent hounds"),
    "hellhound_7": (True, "Monstrous, cunning, and very intelligent hounds"),
    "invisible_stalker_monster": (True, "Highly intelligent, magical creatures"),
    "narwhal": (True, "intelligent, magical whales"),
    "sea_dragon": (True, "Intelligent, aquatic dragons"),
    "shadow": (True, "Intelligent, incorporeal (but not undead) monsters"),
    "troglodyte": (True, "Intelligent, reptilian humanoids"),
    "troll": (True, "Intelligent, wicked, 8’ tall humanoids"),
    "werebear": (True, "Highly intelligent, also in bear form."),
    "wererat": (True, "Intelligent, humanoid rats"),
}


def page_text(page: str) -> str:
    """Return an SRD page's text with each Markdown link reduced to its text."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", (SRD / page).read_text(encoding="utf-8"))


def test_the_catalog_carries_exactly_the_curated_intelligence():
    catalog = load_monsters()
    for template in catalog.monsters:
        expected = CURATED.get(template.id, (None, None))[0]
        assert template.intelligent is expected, template.id
        assert ("intelligent" in template.overrides_applied) is (expected is not None), template.id
    assert set(CURATED) <= {template.id for template in catalog.monsters}


@pytest.mark.parametrize("template_id", sorted(CURATED))
def test_each_curated_value_quotes_its_page(template_id):
    intelligent, quote = CURATED[template_id]
    assert quote in page_text(load_monsters().get(template_id).page)
    words = quote.lower()
    if intelligent:
        assert "intelligent" in words and "unintelligent" not in words
    else:
        assert "unintelligent" in words or "mindless" in words


def lit_session(adventure: Adventure, seed: int) -> GameSession:
    """Enter the test dungeon with a torch lit, so that the monsters can't surprise the party."""
    session = GameSession.new(build_party(), adventure, seed=seed)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    session.execute(EnterDungeon(dungeon_id="delve"))
    for _ in range(20):
        lit = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    return session


def pursued(template_id: str, seed: int = 5, monsters: tuple[MonsterTemplate, ...] = ()) -> GameSession:
    """Slow the party to 60' in plate mail, give it both kinds of bait, and meet a hostile monster."""
    adventure = build_adventure(wandering_chance=0)
    if monsters:
        adventure = adventure.model_copy(update={"monsters": monsters})
    session = lit_session(adventure, seed)
    session.execute(GrantItem(character_id="character-0001", item_id="plate_mail"))
    session.execute(EquipItem(character_id="character-0001", item_id="plate_mail"))
    session.execute(GrantCoins(character_id="character-0001", coins=Coins(gp=100)))
    session.execute(GrantItem(character_id="character-0001", item_id="rations_standard", quantity=7))
    start(session, template_id=template_id, count=1, distance=40, pinned_stance=ReactionResult.HOSTILE)
    return session


def evade(session: GameSession, drop: str) -> tuple[list[str], bool]:
    """Evade with `drop`, and return the event codes and whether a distraction roll was drawn."""
    before = session.streams.get(ENCOUNTER_STREAM).export_state()
    result = session.execute(Evade(drop=drop))
    assert result.accepted
    codes = [event.code for event in result.events]
    assert "encounter.evasion.pursuit" in codes
    return codes, session.streams.get(ENCOUNTER_STREAM).export_state() != before


@pytest.mark.parametrize("template_id", sorted(CURATED))
def test_the_wrong_bait_never_distracts(template_id):
    intelligent, _ = CURATED[template_id]
    codes, rolled = evade(pursued(template_id), "food" if intelligent else "treasure")
    assert not rolled
    assert "encounter.pursuit.distracted" not in codes


@pytest.mark.parametrize("template_id", sorted(CURATED))
def test_the_matching_bait_can_distract(template_id):
    intelligent, _ = CURATED[template_id]
    distracted = False
    for seed in range(30):
        session = pursued(template_id, seed)
        codes, rolled = evade(session, "treasure" if intelligent else "food")
        assert rolled
        if "encounter.pursuit.distracted" in codes:
            distracted = True
            assert session.mode.value == "exploring"
            break
    assert distracted  # 3-in-6 hits within thirty seeds


@pytest.mark.parametrize(
    ("base_id", "wants"),
    [
        ("basilisk", "treasure"),  # treasure type F
        ("invisible_stalker_monster", "food"),  # no treasure letters
    ],
)
def test_a_template_that_does_not_say_keeps_the_treasure_type_stand_in(base_id, wants):
    document = load_monsters().get(base_id).model_dump()
    custom_id = f"house_{base_id}"
    template = MonsterTemplate.model_validate(
        {**document, "id": custom_id, "family_id": custom_id, "intelligent": None}
    )
    other = "food" if wants == "treasure" else "treasure"
    _, rolled = evade(pursued(custom_id, monsters=(template,)), other)
    assert not rolled
    _, rolled = evade(pursued(custom_id, monsters=(template,)), wants)
    assert rolled
