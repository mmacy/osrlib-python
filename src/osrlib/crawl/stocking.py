"""Roll what one keyed room contains, from the B/X stocking tables.

B/X fills an unkeyed dungeon room with dice: the referee rolls a d6 for whether the room has a
monster, a trap, something special, or nothing, and a second d6 for whether it has treasure. This module runs that
procedure for you while you write an adventure. It isn't part of play. It doesn't read or change a
session, and it returns content for you to place, not game state.

The members, in the order you use them:

- [`stock_area`][osrlib.crawl.stocking.stock_area] takes a dungeon level number, a monster catalog,
  and an [`RngStream`][osrlib.core.rng.RngStream], and rolls one room.
- [`StockedArea`][osrlib.crawl.stocking.StockedArea] is what it returns. Its `contents` field gives
  the kind of room. At most one of its `encounter`, `npc_party`, and `treasure` is set. Copy an
  `encounter` (a [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter]) or a `treasure` (an
  [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec]) onto the
  [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] for that room.
- [`StockedNpcParty`][osrlib.crawl.stocking.StockedNpcParty] is the value of `npc_party` when the
  monster roll comes up as a party of NPC adventurers (characters the referee runs).

The tables come from the compiled data: the room-contents table is
[`StockingTable`][osrlib.core.treasure.StockingTable], and the encounter tables come from
[`load_encounter_tables`][osrlib.data.load_encounter_tables].

Every draw comes from the stream you pass, in a fixed order, so the same stream state and the same
level give the same room every time. The order is the one the session uses for a wandering
encounter, so stocking a monster room gives exactly what a wandering encounter on the same table row would:

1. The room-contents d6. Then the treasure d6, but only when the row it selected gives a chance of
   treasure. For a row with no chance, `stock_area` doesn't roll a second die.
2. On a monster room, the d20 for the encounter table's row, then that row's count dice. For a row
   with a fixed count, it rolls no count dice. A count below 1 counts as 1.
3. Then, for a row whose monster depends on a die roll, like a hydra whose number of heads is
   rolled, one roll that picks the monster for the whole group. For a row that lists several
   monsters, one pick for each individual, each monster equally likely.

`stock_area` groups a monster room's individuals into [`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster]
lines with fixed counts, the way a printed adventure gives concrete numbers that you can review and
edit. An empty or trap room with treasure gets an `AreaTreasureSpec` for unguarded treasure.
A monster room's treasure is the encounter's `hoard` flag instead. A trap or a special room gets no
model, because B/X describes those with example lists for the referee rather than with tables, and
an NPC party gets only its kind and size. You design the traps, the specials, and the NPC parties
yourself.

Typical usage:

```python
from osrlib.core.rng import RngStream
from osrlib.crawl.dungeon import AreaSpec
from osrlib.crawl.stocking import stock_area
from osrlib.data import load_monsters

catalog = load_monsters()
stream = RngStream.from_seed_material(23, "stocking")
areas = []
for number in range(1, 4):
    stocked = stock_area(1, catalog=catalog, stream=stream)
    room = AreaSpec(id=f"room-{number}", cells=((number, 0),), encounter=stocked.encounter, treasure=stocked.treasure)
    areas.append(room)

print([(area.id, area.encounter is not None, area.treasure is not None) for area in areas])
# [('room-1', True, False), ('room-2', True, False), ('room-3', False, True)]
print([(line.template_id, line.count_fixed) for line in areas[0].encounter.monsters])
# [('skeleton', 7)]
```
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.dice import roll
from osrlib.core.monsters import MonsterCatalog
from osrlib.core.rng import RngStream
from osrlib.core.tables import EncounterTable, select_encounter_individuals
from osrlib.core.treasure import roll_room_contents
from osrlib.crawl.dungeon import AreaTreasureSpec, KeyedEncounter, KeyedMonster
from osrlib.data import load_encounter_tables

__all__ = [
    "StockedArea",
    "StockedNpcParty",
    "stock_area",
]


class StockedNpcParty(BaseModel):
    """The kind and size of NPC party that a monster room's encounter roll produced.

    An NPC party is a band of adventurers the referee runs. No content model describes one, because
    the session builds the party during play from character classes and gear, not from monster
    templates and treasure letters. So [`stock_area`][osrlib.crawl.stocking.stock_area] has no model
    to hand back. It reports the kind and the count the roll gave instead.

    You get one in [`StockedArea.npc_party`][osrlib.crawl.stocking.StockedArea.npc_party], only on a
    room whose `contents` is `"monster"`. To put a party in play, execute
    [`SpawnNpcParty`][osrlib.crawl.commands.SpawnNpcParty] with this `kind` as its `party_kind`, from
    your own code or as a trigger's consequence.

    Attributes:
        kind: Which NPC party table the roll selected.
        count: How many adventurers are in the party.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["basic", "expert"]
    """Which NPC party table the encounter row selected: `"basic"` for a party of low-level
    adventurers, or `"expert"` for a higher-level one."""
    count: int = Field(ge=1)
    """How many adventurers are in the party, from the row's count. A count below 1 counts as 1."""


class StockedArea(BaseModel):
    """The content the stocking rolls produced for one keyed room, for you to place on an area.

    [`stock_area`][osrlib.crawl.stocking.stock_area] returns one. Read `contents` to find out what
    kind of room you rolled, then copy the payload that came with it onto the
    [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] you're building for that room.

    There is at most one payload:

    - `encounter` on a monster room.
    - `npc_party` on a monster room whose encounter roll produced an NPC party.
    - `treasure` on an empty or trap room that rolled treasure.

    A monster room's treasure is its encounter's `hoard` flag, so `treasure` is always `None` on a
    monster room. A trap or a special room has no payload, because B/X leaves those to the referee.

    Attributes:
        contents: What the room-contents d6 rolled.
        treasure_present: Whether the treasure d6 gave the room treasure.
        encounter: The monsters, on a monster room.
        npc_party: The NPC party, when the encounter roll produced one.
        treasure: The unguarded treasure, on an empty or trap room that rolled some.
    """

    model_config = ConfigDict(frozen=True)

    contents: Literal["empty", "monster", "special", "trap"]
    """What the room-contents d6 rolled: `"empty"`, `"monster"`, `"special"`, or `"trap"`. A special
    is a magical or unusual feature, and a trap is a room trap. B/X describes both with example lists
    for the referee, so neither comes with a model."""
    treasure_present: bool
    """Whether the treasure d6 rolled within the row's chance of treasure. It's always `False` when
    the row gives no chance, as for a special room, and then `stock_area` rolls no die. On a
    monster room it's the same value as the encounter's `hoard` flag."""
    encounter: KeyedEncounter | None = None
    """The monsters in the room, on a monster room, and `None` otherwise. Each line has a fixed count
    that is already rolled. The encounter's `hoard` is the same as `treasure_present`. It's also
    `None` on a monster room whose encounter roll produced an NPC party."""
    npc_party: StockedNpcParty | None = None
    """The NPC party the encounter roll produced, or `None`. It's set only on a monster room, and
    never together with `encounter`. See [`StockedNpcParty`][osrlib.crawl.stocking.StockedNpcParty]."""
    treasure: AreaTreasureSpec | None = None
    """Unguarded treasure for an empty or trap room that rolled some, and `None` otherwise. It's
    always the unguarded form, never treasure type letters, because a treasure type is a monster's
    hoard and no monster lives here. The session rolls the treasure the first time the party
    enters the area."""


def stock_area(
    level_number: int,
    *,
    catalog: MonsterCatalog,
    stream: RngStream,
    table: EncounterTable | None = None,
) -> StockedArea:
    """Roll one keyed room's contents from the B/X stocking tables.

    Call it once for each room you want the dice to fill while you write an adventure. It rolls the
    room-contents d6 and, when the row it selects gives a chance of treasure, the treasure d6. On a
    monster room it then rolls on an encounter table: `table` when you pass one, and the compiled
    table for `level_number` otherwise. Copy the payload of the
    [`StockedArea`][osrlib.crawl.stocking.StockedArea] it returns onto the
    [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] you're building, then check the finished adventure
    with [`validate_adventure`][osrlib.crawl.adventure.validate_adventure].

    Every draw comes from `stream`, in the order the [`osrlib.crawl.stocking`][osrlib.crawl.stocking]
    module describes, so the same stream state and level always give the same room. To stock a whole
    level, pass the same stream to each call. It advances from one room to the next.

    Don't call it during play. It writes nothing, reads no session, and spawns nothing. The
    wandering-monster check during play is
    [`wandering_check`][osrlib.crawl.exploration.wandering_check], which rolls on the same tables.

    Args:
        level_number: The dungeon level the room is on, 1 or more. Without a `table`, it selects the
            compiled encounter table for that level. It doesn't affect unguarded treasure here: the
            session rolls that during play, from the number of the level the area is on.
        catalog: The monster catalog to look up each rolled monster id in. Pass
            [`load_monsters`][osrlib.data.load_monsters]`()` when the adventure bundles no monsters.
            When it does, pass the built-in catalog combined with the bundled templates, which is
            what [`GameSession.effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters]
            returns for a session. Because `stock_area` looks up every id, a stocked encounter refers only to
            monsters that exist.
        stream: The [`RngStream`][osrlib.core.rng.RngStream] that every draw advances. It's the only
            source of randomness in the call.
        table: An encounter table to roll on instead of the compiled one for the level, usually a
            level's [`WanderingSpec.table`][osrlib.crawl.dungeon.WanderingSpec.table]. The session's
            wandering check rolls on that table too, so passing it stocks the room with the monsters
            you chose for the level. `None`, the default, uses
            [`load_encounter_tables`][osrlib.data.load_encounter_tables] for the level.

    Returns:
        What the room contains. See [`StockedArea`][osrlib.crawl.stocking.StockedArea].

    Raises:
        ValueError: If the encounter roll gives a monster id that `catalog` doesn't contain. Pass a
            catalog that includes the adventure's bundled monsters, or fix the table. Spawning raises
            the same error during play.

    Examples:
        ```python
        from osrlib.core.rng import RngStream
        from osrlib.crawl.dungeon import AreaSpec
        from osrlib.crawl.stocking import stock_area
        from osrlib.data import load_monsters

        stocked = stock_area(1, catalog=load_monsters(), stream=RngStream.from_seed_material(7, "stocking"))
        print(stocked.contents, stocked.treasure_present)
        # monster False

        guard_room = AreaSpec(id="guard-room", cells=((2, 0),), encounter=stocked.encounter, treasure=stocked.treasure)
        print([(line.template_id, line.count_fixed) for line in guard_room.encounter.monsters])
        # [('gecko', 3)]
        ```
    """
    result = roll_room_contents(stream)
    contents = result.row.contents
    treasure_present = result.treasure_present
    if contents == "monster":
        return _stock_monster_room(level_number, catalog, stream, table, treasure_present)
    # An empty or trap room that rolled treasure gets unguarded treasure. A special room
    # never gets treasure, because its chance is zero, and no room except a monster room
    # has an encounter: the referee designs the special and the trap.
    treasure = AreaTreasureSpec(unguarded=True) if treasure_present and contents in ("empty", "trap") else None
    return StockedArea(contents=contents, treasure_present=treasure_present, treasure=treasure)


def _stock_monster_room(
    level_number: int,
    catalog: MonsterCatalog,
    stream: RngStream,
    table: EncounterTable | None,
    treasure_present: bool,
) -> StockedArea:
    """Roll a monster room's d20 encounter and build its keyed lines, the way the crawl resolves one."""
    resolved_table = table if table is not None else load_encounter_tables().for_level(level_number)
    row = resolved_table.rows[stream.randbelow(20)]
    if row.count_fixed is not None:
        count = row.count_fixed
    else:
        assert row.count_dice is not None  # the row model guarantees exactly one of the two
        count = roll(row.count_dice, stream).total
    count = max(1, count)
    entry = row.entry
    if entry.kind == "npc_party":
        # No authorable content: the row rolled its count, and the party is the
        # author's to place. The encounter and treasure stay None.
        return StockedArea(
            contents="monster",
            treasure_present=treasure_present,
            npc_party=StockedNpcParty(kind=entry.party_kind, count=count),
        )
    template_ids = select_encounter_individuals(entry, count, stream)
    # Resolve every distinct rolled id through the effective catalog exactly as
    # session.spawn would, so a stocked encounter references only real monsters.
    for template_id in dict.fromkeys(template_ids):
        catalog.get(template_id)
    return StockedArea(
        contents="monster",
        treasure_present=treasure_present,
        encounter=KeyedEncounter(monsters=_group_monsters(template_ids), hoard=treasure_present),
    )


def _group_monsters(template_ids: list[str]) -> tuple[KeyedMonster, ...]:
    """Group the rolled individuals into one `KeyedMonster` line per template, in first-appearance order.

    Counts are fixed rather than dice: the roll has already happened, and a concrete number is what an
    author reviews and edits.
    """
    counts: dict[str, int] = {}
    for template_id in template_ids:
        counts[template_id] = counts.get(template_id, 0) + 1
    return tuple(KeyedMonster(template_id=template_id, count_fixed=count) for template_id, count in counts.items())
