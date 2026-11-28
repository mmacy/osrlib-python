"""An embedded item template takes its display names from the session's catalog (issue #316, part 3).

An `ItemInstance` embeds its whole template, and `MemberView.inventory`, the referee view, and a save dump it whole.
A save or a party document that osrlib 2.7.0 wrote embeds templates without `display_name` and
`display_name_plural`, so validation gives them the defaults from the price-list `name`: "Holy water (vial)" where a
new session has "vial of holy water". The session gives every embedded template whose id its catalog has the
catalog template's two names, when it starts, when a save loads, and when `AddMember` adds a member. Nothing else in
the template changes, because the rest is rules data that travels with the character. A template whose id the
catalog lacks keeps the names it has.

`tests/test_view_features.py::test_a_save_written_by_2_7_0_loads_with_the_new_fields` is the guard from another
chunk: the player view of a loaded 2.7.0 save equals the view of the same state built fresh.
"""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from crawl_fixtures import build_party
from osrlib.core.character import Character, party_from_document
from osrlib.core.events import Visibility
from osrlib.core.items import GearTemplate, ItemInstance, ItemTemplate
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import AddMember, GrantItem
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.events import parse_any_event
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.crawl.views import PlayerView, RefereeView
from osrlib.data import load_equipment
from osrlib.persistence import load_game, save_game
from test_view_item_names import CATALOG_NAMES, PARTY_2_7_0, SAVE_2_7_0, UNNAMED_IDOL, _adventure, _embedded_templates

NPCS_2_7_0 = Path(__file__).parent / "saves" / "view_item_names_npcs_2.7.0.json"
"""A save that osrlib 2.7.0 wrote, kept as it wrote it, in which a basic NPC party of four has attacked the party.

The NPCs carry chainmail, a dagger, a mace, standard rations, a shield, a sword, torches, and a waterskin, each
embedding a template with no display names.
"""

WRITTEN_IDOL = ("idol of the drowned saint", "idols of the drowned saint")
"""The names `_adventure()`'s bundled idol has, which its author wrote."""

CARRIED_IN_SAVE = ("holy_water", "torch", "arrows", "short_bow", "chainmail", "sword", "temple_idol")
"""The stacks the fighter carries in the 2.7.0 save. The two flasks of oil lie in the pile."""

TEMPLATE = TypeAdapter(ItemTemplate)
NAME_FIELDS = ("display_name", "display_name_plural")


def _load(path: Path) -> GameSession:
    return load_game(json.loads(path.read_text(encoding="utf-8")))


def _party_from_2_7_0() -> Party:
    return Party(members=party_from_document(json.loads(PARTY_2_7_0.read_text(encoding="utf-8"))))


def _empty_crypt() -> Adventure:
    """An adventure that bundles nothing, so its catalog has no `temple_idol`."""
    level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
    return Adventure(
        name="An Empty Crypt",
        town=TownSpec(name="Threshold"),
        dungeons=(DungeonSpec(id="crypt", name="Crypt", levels=(level,)),),
    )


def _names(templates) -> dict[str, tuple[str, str]]:
    """Each template's id with its two names, from dumped templates. A repeated id must have the same names."""
    names: dict[str, tuple[str, str]] = {}
    for template in templates:
        pair = (template["display_name"], template["display_name_plural"])
        assert names.setdefault(template["id"], pair) == pair, template["id"]
    return names


def _player_templates(view: PlayerView) -> list[dict]:
    return [template for member in view.party for template in _embedded_templates(member.inventory)]


def _referee_templates(view: RefereeView) -> list[dict]:
    members = [*view.party.members, *view.npcs]
    return [
        template for member in members for template in _embedded_templates(member.inventory.model_dump(mode="json"))
    ]


def _without_names(templates: list[dict]) -> list[str]:
    """The templates without their names, as sorted JSON, so the order a document walks them in doesn't matter.

    The names are the two fields and the entries of `overrides_applied` that name them, which record that the
    compiler took a name from `tools/srd_compile/overrides/`. Those entries describe the names, so they travel with
    them: a fresh session's torch has them, and a torch loaded from 2.7.0 must have the same view.
    """
    stripped = []
    for template in templates:
        kept = {key: value for key, value in template.items() if key not in NAME_FIELDS}
        kept["overrides_applied"] = [entry for entry in template["overrides_applied"] if entry not in NAME_FIELDS]
        stripped.append(kept)
    return sorted(json.dumps(template, sort_keys=True) for template in stripped)


# ---------------------------------------------------------------------- a 2.7.0 save


def test_a_2_7_0_save_embeds_the_catalogs_names_in_every_carried_stack():
    """The inventory dump, the referee view, and the next save all carry the catalog's names.

    The save's adventure bundles the idol with no names, so the catalog's idol has the defaults, and the carried idol
    takes those.
    """
    game = _load(SAVE_2_7_0)
    expected = {item_id: CATALOG_NAMES.get(item_id, UNNAMED_IDOL) for item_id in CARRIED_IN_SAVE}

    player = game.view(Visibility.PLAYER)
    referee = game.view(Visibility.REFEREE)
    assert isinstance(player, PlayerView) and isinstance(referee, RefereeView)

    assert _names(_player_templates(player)) == expected
    assert _names(_referee_templates(referee)) == expected
    assert _names(_embedded_templates(save_game(game))) == expected


def test_a_2_7_0_save_gives_npc_adventurers_the_catalogs_names():
    """An NPC's gear is in the referee view and the save, and it takes the catalog's names too."""
    game = _load(NPCS_2_7_0)
    catalog = load_equipment()

    referee = game.view(Visibility.REFEREE)
    assert isinstance(referee, RefereeView)

    npc_templates = [
        template for npc in referee.npcs for template in _embedded_templates(npc.inventory.model_dump(mode="json"))
    ]
    names = _names(npc_templates)
    assert set(names) == {"chainmail", "dagger", "mace", "rations_standard", "shield", "sword", "torch", "waterskin"}
    assert names == {
        item_id: (catalog.get(item_id).display_name, catalog.get(item_id).display_name_plural) for item_id in names
    }
    assert names["torch"] == ("torch", "torches")
    assert names["rations_standard"] == ("standard ration", "standard rations")
    assert _names(_embedded_templates(save_game(game)["payload"]["npcs"])) == names


@pytest.mark.parametrize(
    "path",
    [pytest.param(SAVE_2_7_0, id="party"), pytest.param(NPCS_2_7_0, id="npcs")],
)
def test_loading_a_2_7_0_save_changes_only_the_two_names_of_an_embedded_template(path: Path):
    """Every embedded template, read as this osrlib reads it, is the same before and after but for its names."""
    document = json.loads(path.read_text(encoding="utf-8"))
    before = [TEMPLATE.validate_python(template).model_dump(mode="json") for template in _embedded_templates(document)]

    after = _embedded_templates(save_game(load_game(document)))

    assert len(after) == len(before)
    assert _without_names(after) == _without_names(before)
    assert _names(after) != _names(before)


def test_loading_a_2_7_0_save_draws_nothing_and_keeps_the_logs():
    """The names are no rule and no draw: the streams, the command log, and the event log come back as they were."""
    document = json.loads(SAVE_2_7_0.read_text(encoding="utf-8"))

    again = save_game(load_game(document))["payload"]

    for key in ("rng_streams", "command_log", "clock_rounds", "allocator"):
        assert again[key] == document["payload"][key], key
    # A later release can add a field with a default to an event, which the next save writes out. Each entry keeps
    # every key and value it had, and any key it gains holds that field's default.
    original_log = document["payload"]["event_log"]
    assert len(again["event_log"]) == len(original_log)
    for resaved, original in zip(again["event_log"], original_log, strict=True):
        assert {key: resaved[key] for key in original} == original
        added = set(resaved) - set(original)
        if added:
            event = parse_any_event(original)
            assert event is not None, original
            fields = type(event).model_fields
            assert {key: resaved[key] for key in added} == {key: fields[key].default for key in added}


# ---------------------------------------------------------------------- a 2.7.0 party document


def test_a_2_7_0_party_document_takes_the_adventures_names_for_its_bundled_item():
    """A new session of an adventure whose idol has written names: the carried idol takes them."""
    game = GameSession.new(_party_from_2_7_0(), _adventure(), seed=5)
    expected = {**CATALOG_NAMES, "temple_idol": WRITTEN_IDOL}

    player = game.view(Visibility.PLAYER)
    assert isinstance(player, PlayerView)

    assert _names(_player_templates(player)) == expected
    assert _names(_embedded_templates(save_game(game)["payload"]["party"])) == expected


def test_a_2_7_0_party_documents_bundled_item_keeps_its_names_without_the_adventure():
    """The same party in an adventure that bundles no idol: the idol keeps the defaults its template has."""
    game = GameSession.new(_party_from_2_7_0(), _empty_crypt(), seed=5)

    player = game.view(Visibility.PLAYER)
    assert isinstance(player, PlayerView)

    assert _names(_player_templates(player)) == {**CATALOG_NAMES, "temple_idol": UNNAMED_IDOL}


def test_a_new_session_changes_only_the_names_of_a_template_the_catalog_has():
    """A torch of the fighter's own, with the catalog's id but its own rules data, keeps all but its names."""
    party = build_party()
    old_torch = GearTemplate.model_validate(
        {
            "id": "torch",
            "name": "Old torch",
            "cost_gp": 3,
            "params": {"burn_turns": 2, "light_radius_feet": 10},
            "display_name": "old torch",
            "display_name_plural": "old torches",
        }
    )
    party.members[0].inventory.items.append(ItemInstance(template=old_torch, quantity=2))

    game = GameSession.new(party, _empty_crypt(), seed=5)

    (stack,) = game.party.members[0].inventory.items
    assert isinstance(stack, ItemInstance)
    assert (stack.template.display_name, stack.template.display_name_plural) == ("torch", "torches")
    assert _without_names([stack.template.model_dump(mode="json")]) == _without_names(
        [old_torch.model_dump(mode="json")]
    )
    catalog_torch = load_equipment().get("torch")
    assert stack.template.overrides_applied == tuple(
        entry for entry in catalog_torch.overrides_applied if entry in NAME_FIELDS
    )
    assert stack.quantity == 2


def test_a_member_added_from_a_2_7_0_document_takes_the_catalogs_names():
    """`AddMember` with the 2.7.0 fighter, given no id and none of the magic items a joining member can't carry.

    The member who joins has the catalog's names, and the command log keeps the character as it arrived.
    """
    document = json.loads(PARTY_2_7_0.read_text(encoding="utf-8"))
    fighter = next(member for member in document["payload"]["characters"] if member["id"] == "character-0001")
    fighter = {**fighter, "id": None}
    fighter["inventory"]["items"] = [
        entry for entry in fighter["inventory"]["items"] if entry["instance_type"] != "magic_item"
    ]
    game = GameSession.new(build_party(), _adventure(), seed=5)

    added = game.execute(AddMember(character=Character.model_validate(fighter)))
    assert added.accepted, [rejection.code for rejection in added.rejections]

    joined = game.party.members[-1]
    templates = _embedded_templates(joined.inventory.model_dump(mode="json"))
    assert _names(templates) == {**CATALOG_NAMES, "temple_idol": WRITTEN_IDOL}
    logged = _embedded_templates(game.command_log[-1].character.inventory.model_dump(mode="json"))
    assert _names(logged)["holy_water"][0] == "Holy water (vial)"


# ---------------------------------------------------------------------- a save this osrlib writes


def test_a_save_written_now_loads_to_the_same_save():
    """A session of current documents, a bundled item and a torch of the fighter's own included, re-saves unchanged.

    This holds before the chunk's change too, and it must keep holding after it.
    """
    party = build_party()
    party.members[0].inventory.items.append(
        ItemInstance(
            template=GearTemplate.model_validate({"id": "torch", "name": "Old torch", "cost_gp": 3}), quantity=2
        )
    )
    game = GameSession.new(party, _adventure(), seed=5)
    for item_id in ("holy_water", "chainmail", "arrows", "temple_idol", "potion_of_healing"):
        assert game.execute(GrantItem(character_id="character-0001", item_id=item_id)).accepted
    document = json.loads(json.dumps(save_game(game)))

    assert json.loads(json.dumps(save_game(load_game(document)))) == document
