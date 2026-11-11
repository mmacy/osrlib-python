"""Generating treasure in play: area caches, lair hoards, the treasure monsters carry, and NPC parties.

The engine generates an area's cache the first time the party enters the area, a lair's hoard the first time its keyed
encounter spawns, and carried treasure when monsters spawn. An NPC party, with its treasure, comes from a
wandering-monster roll or from the referee's `SpawnNpcParty` command.
"""

from typing import Literal

from osrlib.crawl._party_location import _area_ref, _dungeon_coords, _level, _position
from osrlib.crawl.dungeon import cell_ref


def _treasure_tier(session) -> str:
    """The tier the crawl passes to generation, evaluated at generation time.

    `basic` while the party's highest living level is 1-3, `expert` at 4+, which is
    the master table's definition of Basic and Expert characters.
    """
    highest = max((member.level for member in session.party.living_members()), default=1)
    return "expert" if highest >= 4 else "basic"


def _merge_generated(bundle, generated) -> None:
    from osrlib.core.items import Coins

    bundle.coins = Coins(
        **{
            denomination: getattr(bundle.coins, denomination) + getattr(generated.coins, denomination)
            for denomination in ("pp", "gp", "ep", "sp", "cp")
        }
    )
    bundle.valuables.extend(generated.valuables)
    bundle.magic_items.extend(generated.magic_items)


def _generate_carried_treasure(session, instances):
    """Generate carried treasure at spawn: individual letters per monster, group per group.

    For individual letters (P-T), it reads each instance's template, since packed-variant
    pools may mix. For group letters (U-V), it reads the first instance's, because the
    row's members share a stat block. The `multiplier` repeats the whole listed
    generation (for example, the Noble's `V × 3`). Returns `(member_treasure,
    group_bundle)` for the encounter group.
    """
    from osrlib.core.treasure import TREASURE_STREAM, generate_treasure, plan_treasure_ref
    from osrlib.crawl.dungeon import TreasureBundle

    stream = session.streams.get(TREASURE_STREAM)
    tier = _treasure_tier(session)
    member_treasure = {}
    for instance in instances:
        plan = plan_treasure_ref(instance.template.treasure)
        if not plan.individual:
            continue
        bundle = TreasureBundle()
        for letter in plan.individual:
            for _ in range(plan.multiplier):
                generated = generate_treasure(letter, tier=tier, stream=stream, allocator=session.allocator)
                _merge_generated(bundle, generated)
        if not bundle.empty:
            member_treasure[instance.id] = bundle
    group_plan = plan_treasure_ref(instances[0].template.treasure) if instances else None
    group_bundle = None
    if group_plan is not None and group_plan.group:
        bundle = TreasureBundle()
        for letter in group_plan.group:
            for _ in range(group_plan.multiplier):
                generated = generate_treasure(letter, tier=tier, stream=stream, allocator=session.allocator)
                _merge_generated(bundle, generated)
        if not bundle.empty:
            group_bundle = bundle
    return member_treasure, group_bundle


def _field_npc_party(session, kind: Literal["basic", "expert"], count: int):
    """Generate an NPC party, register its members, and return them with the events.

    Members register in `session.npcs` (allocator prefix `npc`). The referee-only
    roster event contains classes and levels, while the player-facing encounter event
    contains "adventurers" and the count.
    """
    from osrlib.core.npc import NPC_PARTY_STREAM, generate_npc_party
    from osrlib.core.treasure import TREASURE_STREAM
    from osrlib.crawl.dungeon import TreasureBundle
    from osrlib.crawl.events import NpcPartySpawnedEvent

    party = generate_npc_party(
        kind,
        count=count,
        npc_stream=session.streams.get(NPC_PARTY_STREAM),
        treasure_stream=session.streams.get(TREASURE_STREAM),
        allocator=session.allocator,
    )
    npc_ids: list[str] = []
    for member in party.members:
        if member.id is None:  # unreachable: the generator allocates ids
            raise ValueError("a generated NPC carries no id")
        session.npcs[member.id] = member
        npc_ids.append(member.id)
    bundle = TreasureBundle(
        coins=party.treasure.coins,
        valuables=list(party.treasure.valuables),
        magic_items=list(party.treasure.magic_items),
    )
    event = NpcPartySpawnedEvent(
        party_kind=kind,
        npc_ids=tuple(npc_ids),
        class_ids=tuple(member.class_id for member in party.members),
        levels=tuple(member.level for member in party.members),
        alignment=party.alignment.value,
    )
    return party, (None if bundle.empty else bundle), [event]


def _assign_carried(session, carried) -> None:
    """Attach the spawn-time bundles to the encounter groups, in input order."""
    if session.encounter is None:
        return
    for group, (member_treasure, group_bundle) in zip(session.encounter.groups, carried, strict=True):
        group.member_treasure = member_treasure
        group.group_treasure = group_bundle


def _generate_cache(session, *, cell, treasure_types, entries_lists, extra_gp=0) -> list:
    """Store generated treasure as an engine-created cache in the state overlay."""
    from osrlib.core.items import Coins
    from osrlib.core.treasure import TREASURE_STREAM, generate_treasure_entries
    from osrlib.crawl.dungeon import GeneratedCache, TreasureBundle
    from osrlib.crawl.events import HoardGeneratedEvent

    stream = session.streams.get(TREASURE_STREAM)
    tier = _treasure_tier(session)
    bundle = TreasureBundle()
    for entries in entries_lists:
        _merge_generated(
            bundle, generate_treasure_entries(entries, tier=tier, stream=stream, allocator=session.allocator)
        )
    if extra_gp:
        bundle.coins = Coins(
            **{
                denomination: getattr(bundle.coins, denomination) + (extra_gp if denomination == "gp" else 0)
                for denomination in ("pp", "gp", "ep", "sp", "cp")
            }
        )
    if bundle.empty:
        return []
    cache_id = session.allocator.allocate("cache")
    dungeon_id, level_number, _ = _dungeon_coords(session)
    ref = cell_ref(dungeon_id, level_number, cell)
    session.dungeon_state.generated_caches[cache_id] = GeneratedCache(
        cell_ref=ref,
        treasure_types=tuple(treasure_types),
        coins=bundle.coins,
        valuables=bundle.valuables,
        magic_items=bundle.magic_items,
    )
    return [
        HoardGeneratedEvent(
            cache_ref=cache_id,
            treasure_types=tuple(treasure_types),
            coins_gp_value=bundle.coins.value_gp,
            valuable_ids=tuple(valuable.instance_id for valuable in bundle.valuables),
            magic_item_ids=tuple(item.instance_id for item in bundle.magic_items),
        )
    ]


def _generate_lair_hoard(session, area, templates) -> list:
    """Generate a keyed area's lair hoard when its keyed encounter first spawns.

    The keyed monsters' hoard letters (A-O), parenthetical letters, and `extra_gp`
    become one engine-created cache on the area's first listed cell.
    """
    from osrlib.core.treasure import plan_treasure_ref
    from osrlib.data import load_treasure_tables

    letters: list[str] = []
    extra_gp = 0
    for template in templates:
        plan = plan_treasure_ref(template.treasure)
        for _ in range(plan.multiplier):
            letters.extend(plan.lair)
        extra_gp += plan.extra_gp
    if not letters and not extra_gp:
        return []
    tables = load_treasure_tables()
    entries_lists = [tables.treasure_type(letter).entries for letter in letters]
    return _generate_cache(
        session, cell=area.cells[0], treasure_types=letters, entries_lists=entries_lists, extra_gp=extra_gp
    )


def _area_treasure_check(session) -> list:
    """Generate an area's declared treasure on first entry.

    Discovery needs a living discoverer. The engine generates nothing for a party
    with no one left standing, like one a chute dropped into the cell and killed.
    """
    from osrlib.data import load_treasure_tables

    if not session.party.living_members():
        return []
    level = _level(session)
    area = level.area_at(_position(session))
    if area is None or area.treasure is None:
        return []
    area_ref = _area_ref(session, area.id)
    state = session.dungeon_state
    if area_ref in state.generated_treasure_areas:
        return []
    state.generated_treasure_areas.append(area_ref)
    tables = load_treasure_tables()
    if area.treasure.unguarded:
        band = tables.unguarded.band_for_level(level.number)
        return _generate_cache(
            session, cell=_position(session), treasure_types=("unguarded",), entries_lists=[band.entries]
        )
    entries_lists = [tables.treasure_type(letter).entries for letter in area.treasure.letters]
    return _generate_cache(
        session, cell=_position(session), treasure_types=area.treasure.letters, entries_lists=entries_lists
    )
