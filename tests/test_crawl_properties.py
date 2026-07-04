"""Property tests: the fuzz contract, spatial invariants, and the leak test."""

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crawl_fixtures import build_adventure, build_party
from osrlib.core.events import Visibility
from osrlib.core.items import Coins
from osrlib.crawl.commands import (
    ALL_COMMAND_CLASSES,
    BattleDeclaration,
    EnterDungeon,
    GrantItem,
    LightSource,
    MoveParty,
)
from osrlib.crawl.dungeon import Direction, PartyLocation
from osrlib.crawl.session import GameSession

CHARACTER_IDS = ["character-0001", "character-0002", "character-0003", "character-0004", "character-0099"]
ITEM_IDS = [
    "torch",
    "sword",
    "rations_standard",
    "waterskin",
    "iron_spikes",
    "gemstone_of_wishing",
    # The planted magic instances (see plant_magic_items): the fuzz must be able
    # to equip, use, and drop them.
    "magic-item-9001",
    "magic-item-9002",
    "magic-item-9003",
]
DIRECTIONS = list(Direction)


def plant_magic_items(session) -> None:
    """Give the first member unidentified magic items the fuzz can reach."""
    from osrlib.core.items import MagicItemInstance

    member = session.party.members[0]
    member.inventory.items.append(
        MagicItemInstance(instance_id="magic-item-9001", template_id="armour_plus_1", base_item_id="chainmail")
    )
    member.inventory.items.append(
        MagicItemInstance(instance_id="magic-item-9002", template_id="potion_of_giant_strength")
    )
    member.inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-9003",
            template_id="wand_of_fire_balls",
            charges_remaining=7,
            state={"secret": 1},
        )
    )


def command_strategy():
    """Schema-valid commands with plausible-to-nonsense field values."""
    samples = []
    for command_class in ALL_COMMAND_CLASSES:
        name = command_class.__name__
        if name in ("PlaceParty",):
            samples.append(
                st.builds(
                    command_class,
                    location=st.sampled_from(
                        [
                            PartyLocation(kind="town"),
                            PartyLocation(
                                kind="dungeon", dungeon_id="delve", level_number=1, position=(0, 0), facing="east"
                            ),
                            PartyLocation(
                                kind="dungeon", dungeon_id="delve", level_number=2, position=(2, 0), facing="south"
                            ),
                        ]
                    ),
                )
            )
            continue
        if name == "SellTreasure":
            samples.append(
                st.builds(
                    command_class,
                    item_ids=st.lists(st.sampled_from(["valuable-0001", "torch"]), min_size=1, max_size=2).map(tuple),
                )
            )
            continue
        if name == "SpawnNpcParty":
            samples.append(
                st.builds(
                    command_class,
                    party_kind=st.sampled_from(["basic", "expert"]),
                    count_dice=st.sampled_from([None, "1d3"]),
                    distance_feet=st.integers(min_value=0, max_value=90),
                )
            )
            continue
        if name == "SpawnMonsters":
            samples.append(
                st.builds(
                    command_class,
                    template_id=st.sampled_from(["goblin", "skeleton", "gazebo"]),
                    count_fixed=st.integers(min_value=1, max_value=3),
                    distance_feet=st.integers(min_value=0, max_value=90),
                )
            )
            continue
        if name == "ResolveBattleRound":
            declaration = st.builds(
                BattleDeclaration,
                character_id=st.sampled_from(CHARACTER_IDS),
                action=st.sampled_from(["attack", "cast", "turn_undead", "move", "use_item", "hold"]),
                target_group_id=st.sampled_from([None, "group-0001", "group-9999"]),
                weapon_id=st.sampled_from([None, "sword", "crossbow"]),
                spell_id=st.sampled_from([None, "sleep", "fire_ball"]),
                spell_mode=st.sampled_from([None, "hd_budget", "damage"]),
                move=st.sampled_from([None, "close", "withdraw", "retreat"]),
                item_id=st.sampled_from([None, "holy_water"]),
            )
            samples.append(st.builds(command_class, declarations=st.tuples(declaration)))
            continue
        fields = {}
        for field_name, field in command_class.model_fields.items():
            if field_name == "command_type":
                continue
            annotation = str(field.annotation)
            if field_name in ("character_id",):
                fields[field_name] = st.sampled_from(CHARACTER_IDS)
            elif field_name in ("item_id",):
                fields[field_name] = st.sampled_from(ITEM_IDS)
            elif field_name in ("item_ids",):
                fields[field_name] = st.lists(st.sampled_from(ITEM_IDS), min_size=1, max_size=2).map(tuple)
            elif field_name in ("order",):
                fields[field_name] = st.permutations(CHARACTER_IDS[:4]).map(tuple)
            elif field_name in ("direction", "facing"):
                fields[field_name] = st.sampled_from(DIRECTIONS)
            elif field_name == "coins":
                fields[field_name] = st.builds(Coins, gp=st.integers(min_value=0, max_value=50))
            elif field_name in ("feature_id", "dungeon_id", "spell_id", "template_id", "key"):
                fields[field_name] = st.sampled_from(["delve", "chest", "pile", "sleep", "goblin", "lever"])
            elif field_name == "service":
                fields[field_name] = st.sampled_from(["cure_light_wounds", "remove_curse", "raise_dead"])
            elif field_name == "kind" and "secret_doors" in annotation:
                fields[field_name] = st.sampled_from(["secret_doors", "room_traps", "construction"])
            elif field_name == "kind":
                fields[field_name] = st.sampled_from(["turn", "night", "day"])
            elif field_name == "drop":
                fields[field_name] = st.sampled_from(["none", "treasure", "food"])
            elif field_name == "mode":
                fields[field_name] = st.sampled_from(["hd_budget", "damage", "illuminate"])
            elif field_name == "value":
                fields[field_name] = st.sampled_from([True, 7, "open"])
            elif field_name == "amount" or field_name == "n":
                fields[field_name] = st.integers(min_value=0, max_value=100)
            elif field_name == "quantity":
                fields[field_name] = st.integers(min_value=1, max_value=6)
            elif field_name == "unit":
                fields[field_name] = st.sampled_from(["round", "turn"])
            elif field_name in ("x", "y", "level_number"):
                fields[field_name] = st.integers(min_value=0 if field_name != "level_number" else 1, max_value=4)
            elif field_name in ("open", "wedged", "discovered", "unlocked"):
                fields[field_name] = st.sampled_from([None, True, False])
            elif field_name == "reversed":
                fields[field_name] = st.booleans()
            elif field_name in ("targets", "selections"):
                fields[field_name] = st.just(())
            elif field_name == "count_dice":
                fields[field_name] = st.just(None)
            elif field_name == "count_fixed":
                fields[field_name] = st.just(1)
            elif field_name == "distance_feet":
                fields[field_name] = st.integers(min_value=0, max_value=60)
            else:
                fields[field_name] = st.just(field.default) if not field.is_required() else st.none()
        samples.append(st.builds(command_class, **fields))
    return st.one_of(samples)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32),
    commands=st.lists(command_strategy(), min_size=1, max_size=25),
)
def test_fuzzed_command_sequences_never_raise_and_hold_the_invariants(seed, commands):
    """The spec's fuzz contract: schema-valid commands reject, never throw."""
    session = GameSession.new(build_party(), build_adventure(), seed=seed)
    plant_magic_items(session)
    last_rounds = session.clock.rounds
    for command in commands:
        session.execute(command)  # must never raise
        # The clock never decreases.
        assert session.clock.rounds >= last_rounds
        last_rounds = session.clock.rounds
        # The party never occupies a wall or an out-of-bounds cell.
        location = session.dungeon_state.location
        if location.kind == "dungeon":
            level = session.adventure.dungeon(location.dungeon_id).level(location.level_number)
            assert level.in_bounds(location.position)


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32),
    commands=st.lists(command_strategy(), min_size=1, max_size=12),
)
def test_randomly_driven_sessions_save_and_load_round_trip(seed, commands):
    from osrlib.persistence import load_game, save_game, session_state

    session = GameSession.new(build_party(), build_adventure(), seed=seed)
    for command in commands:
        session.execute(command)
    restored = load_game(json.loads(json.dumps(save_game(session))))
    assert session_state(restored) == session_state(session)


@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=10**12, max_value=2**64),
    commands=st.lists(command_strategy(), min_size=1, max_size=15),
)
def test_the_player_view_never_leaks(seed, commands):
    """The leak property test — fuzzed sessions, not one fixture (pinned)."""
    session = GameSession.new(build_party(), build_adventure(), seed=seed)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    # Unidentified magic items ride along under ids the fuzz never uses (the
    # 8000s — ITEM_IDS carries only the 9000s), so these must stay masked
    # whatever the command sequence does.
    from osrlib.core.items import MagicItemInstance

    member = session.party.members[0]
    member.inventory.items.append(
        MagicItemInstance(instance_id="magic-item-8001", template_id="potion_of_giant_strength")
    )
    member.inventory.items.append(
        MagicItemInstance(
            instance_id="magic-item-8002",
            template_id="wand_of_fire_balls",
            charges_remaining=7,
            state={"secret": 1},
        )
    )
    for command in commands:
        session.execute(command)
    view = session.view(Visibility.PLAYER)
    blob = view.model_dump_json()
    # Unidentified items mask behind category display names: no true ids, no
    # charges, no per-item state, no sentience — and no hoard not yet found.
    assert "potion_of_giant_strength" not in blob
    assert "wand_of_fire_balls" not in blob
    assert "charges" not in blob
    assert "sensory_powers" not in blob and "drains_remaining" not in blob and '"secret"' not in blob
    assert "cache-" not in blob
    # The seed lives only in the save (13+ digit seeds can't collide with content).
    assert str(seed) not in blob
    # Session flags are referee-only: the view carries no flag store at all.
    parsed_view = json.loads(blob)
    assert "flags" not in parsed_view
    # Unexplored geometry, trap specs, and secret doors stay hidden.
    explored = {
        (dungeon_level, tuple(cell))
        for dungeon_level, cells in session.dungeon_state.explored.items()
        for cell in cells
    }
    parsed = json.loads(blob)
    for level_view in parsed["explored"]:
        key = f"{level_view['dungeon_id']}:{level_view['level_number']}"
        for cell in level_view["cells"]:
            assert (key, tuple(cell)) in explored
    assert "TrapSpec" not in blob and "trap_ref" not in blob
    # Monster internals: no HP fields beyond the party's own.
    if parsed.get("encounter"):
        for group in parsed["encounter"]["groups"]:
            assert set(group) == {"label", "count", "distance_feet", "visible_conditions"}
    # Referee-visibility outcomes never appear (views carry no events at all).
    assert "exploration.detection.rolled" not in blob


def test_odometer_never_drifts_from_the_closed_form():
    """Ping-pong movement: turns and odometer match the closed-form arithmetic."""
    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=3)
    session.execute(GrantItem(character_id="character-0001", item_id="torch", quantity=6))
    session.execute(GrantItem(character_id="character-0001", item_id="tinder_box"))
    session.execute(EnterDungeon(dungeon_id="delve"))
    for _ in range(20):
        result = session.execute(LightSource(character_id="character-0001", item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in result.events):
            break
    start_turns = session.clock.turns
    threshold = 3 * 120
    accrued = 0
    turns = 0
    explored: set = {(0, 0)}
    position = (0, 0)
    for step in range(150):
        direction = Direction.EAST if position == (0, 0) else Direction.WEST
        target = (1, 0) if direction is Direction.EAST else (0, 0)
        result = session.execute(MoveParty(direction=direction))
        assert result.accepted
        accrued += 10 if target in explored else 30
        explored.add(target)
        if accrued >= threshold:
            accrued = 0
            turns += 1
        position = target
        assert session.odometer_thirds == accrued, f"drift at step {step}"
    assert session.clock.turns - start_turns == turns


@settings(max_examples=50, deadline=None)
@given(
    gp=st.integers(min_value=0, max_value=500),
    gems=st.integers(min_value=0, max_value=5),
    jewellery=st.integers(min_value=0, max_value=3),
    potions=st.integers(min_value=0, max_value=3),
    scrolls=st.integers(min_value=0, max_value=2),
    wands=st.integers(min_value=0, max_value=2),
    rod=st.booleans(),
    staff=st.booleans(),
    ring=st.booleans(),
    bag=st.sampled_from(["none", "empty", "holding"]),
    sword=st.booleans(),
    armour=st.booleans(),
)
def test_weights_match_the_closed_form_with_valuables_and_magic_items_aboard(
    gp, gems, jewellery, potions, scrolls, wands, rod, staff, ring, bag, sword, armour
):
    """The plan's closed form: treasure weight is purse + valuables + the five
    priced magic categories (plus the bag's loaded figure while it holds), and
    enchanted arms weigh as equipment — base weight, armour halved."""
    from osrlib.core.items import (
        CoinPurse,
        Inventory,
        MagicItemInstance,
        ValuableInstance,
        equipment_weight_coins,
        treasure_weight_coins,
    )

    inventory = Inventory(purse=CoinPurse(gp=gp))
    counter = iter(range(1, 100))

    def add(template_id: str, **kwargs) -> None:
        inventory.items.append(
            MagicItemInstance(instance_id=f"magic-item-{next(counter):04d}", template_id=template_id, **kwargs)
        )

    for index in range(gems):
        inventory.valuables.append(
            ValuableInstance(instance_id=f"valuable-g{index}", kind="gem", value_gp=50, weight_coins=1)
        )
    for index in range(jewellery):
        inventory.valuables.append(
            ValuableInstance(instance_id=f"valuable-j{index}", kind="jewellery", value_gp=300, weight_coins=10)
        )
    for _ in range(potions):
        add("potion_of_healing")
    for _ in range(scrolls):
        add("scroll_of_protection_from_undead")
    for _ in range(wands):
        add("wand_of_fire_balls")
    if rod:
        add("rod_of_cancellation")
    if staff:
        add("staff_of_healing")
    if ring:
        add("ring_of_protection")
    if bag != "none":
        add("bag_of_holding", state={"holding": True} if bag == "holding" else {})
    if sword:
        add("sword_plus_1", base_item_id="sword")
    if armour:
        add("armour_plus_1", base_item_id="chainmail")

    # The TreasureWeight rows, hard-coded: potion 10, scroll 1, wand 10, rod 20,
    # staff 40; rings weigh zero; the bag's printed loaded weight is 600.
    expected_treasure = (
        gp
        + gems * 1
        + jewellery * 10
        + potions * 10
        + scrolls * 1
        + wands * 10
        + (20 if rod else 0)
        + (40 if staff else 0)
        + (600 if bag == "holding" else 0)
    )
    assert treasure_weight_coins(inventory) == expected_treasure
    # Sword 60 as printed; chainmail 400 halved to 200 per RAW.
    assert equipment_weight_coins(inventory) == (60 if sword else 0) + (200 if armour else 0)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=2**32),
    ops=st.lists(st.tuples(st.sampled_from(["grant", "drop"]), st.integers(min_value=0, max_value=300)), max_size=8),
)
def test_the_valuation_delta_never_awards_negative(seed, ops):
    """Whatever gets granted and abandoned, the award clamps at zero and XP never falls."""
    from osrlib.crawl.commands import DropItems, GrantCoins, TravelToTown

    session = GameSession.new(build_party(), build_adventure(wandering_chance=0), seed=seed)
    member = session.party.members[0]
    # A pre-departure purse the party can abandon below its snapshot.
    session.execute(GrantCoins(character_id=member.id, coins=Coins(gp=100)))
    session.execute(EnterDungeon(dungeon_id="delve"))
    before = [m.xp for m in session.party.members]
    for op, amount in ops:
        if op == "grant":
            session.execute(GrantCoins(character_id=member.id, coins=Coins(gp=amount)))
        else:
            session.execute(DropItems(character_id=member.id, item_ids=(), coins=Coins(gp=amount)))
    result = session.execute(TravelToTown())
    assert result.accepted
    for event in result.events:
        if event.code == "session.xp.adventure_award":
            assert event.treasure_xp >= 0
            assert event.monster_xp >= 0
            assert event.share >= 0
    assert all(m.xp >= b for m, b in zip(session.party.members, before, strict=True))
