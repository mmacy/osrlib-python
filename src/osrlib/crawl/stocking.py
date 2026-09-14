"""Stocking: roll what one keyed room holds, from the B/X tables.

[`stock_area`][osrlib.crawl.stocking.stock_area] is the entry point, and the two models here are what
it returns. Give it a dungeon level number,
a monster catalog, and an RNG stream, and it rolls one room the way the B/X procedure does: the
room-contents d6, the treasure chance that row prints, and, on a monster room, the level's encounter
table and the count that row calls for. What comes back is a frozen
[`StockedArea`][osrlib.crawl.stocking.StockedArea] holding content models you can read, edit, and
place: a [`KeyedEncounter`][osrlib.crawl.dungeon.KeyedEncounter] to drop on an
[`AreaSpec`][osrlib.crawl.dungeon.AreaSpec], or an
[`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec] to put on one. It is an authoring tool,
not part of play: nothing here touches a session, and a stocked area is content rather than state.

The tables it rolls on ship as data already. The room-contents d6 with its per-row treasure chance is
[`StockingTable`][osrlib.core.treasure.StockingTable], the encounter tables are
[`load_encounter_tables`][osrlib.data.load_encounter_tables], and the treasure generators live in
`osrlib.core.treasure`. This module is the procedure that puts them together.

Every draw comes from the one stream you pass, in a fixed order, so the same stream state and the same
level give the same room every time. The order matches the crawl's own wandering resolution, so
stocking a row yields exactly what a wandering encounter on that row would:

1. the room-contents d6, then the treasure d6, the second only when the selected row prints a
   non-zero treasure chance (a row with no printed chance consumes no die).
2. on a monster room, the encounter table's d20 row, then that row's count dice (a row with a fixed
   count consumes none), with the result held at 1 or more.
3. then either one `variant_dice` roll, which is the hydra form where the printed hit-dice roll
   selects the template once, or, for a packed-variant pool row, one uniform pick per individual.

Where the procedure stops is deliberate. A monster room's rolled individuals are grouped into
[`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster] lines with fixed counts, because a printed module
gives concrete numbers and a concrete number is what you review and edit. An empty or trap room that
rolled treasure gets an unguarded `AreaTreasureSpec`. A monster room's treasure is the encounter's
own `hoard` flag rather than a second declaration. Traps and specials produce no model at all, because
B/X prints example lists for those as referee prose rather than as tables, and an NPC-party row has no
authorable content either, so `stock_area` reports the kind and the count and stops. The procedure
ends where the dice end, and the rest is yours to design.

Typical usage:

```python
from osrlib.core.rng import RngStream
from osrlib.crawl.stocking import stock_area
from osrlib.data import load_monsters

catalog = load_monsters()
stream = RngStream.from_seed_material(7, "stocking")
for _ in range(3):
    area = stock_area(1, catalog=catalog, stream=stream)
    lines = [(line.template_id, line.count_fixed) for line in (area.encounter.monsters if area.encounter else ())]
    print(area.contents, area.treasure_present, lines)
# monster False [('gecko', 3)]
# monster False [('trader', 3)]
# special False []
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
    """The party kind and size a monster room's encounter row rolled.

    An NPC party has no authorable content model. A party is built at play from character classes and
    their gear rather than from a keyed encounter or treasure letters, so there is nothing for
    [`stock_area`][osrlib.crawl.stocking.stock_area] to hand back and place. The roll still named a
    row and a count, so it reports that much and leaves the party for you to write by hand.

    You get one on [`StockedArea.npc_party`][osrlib.crawl.stocking.StockedArea], and only on a room
    whose `contents` is `"monster"`.

    Attributes:
        kind: Which encounter list the party came off.
        count: How many are in it.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["basic", "expert"]
    """Which of the two NPC-party lists the row came off: `"basic"` or `"expert"`. The two lists
    describe different sorts of party, and the row you rolled names one of the two."""
    count: int = Field(ge=1)
    """How many are in the party, from the row's count, held at 1 or more."""


class StockedArea(BaseModel):
    """Everything the rolls produced for one keyed room: content models you place, never game state.

    This is what [`stock_area`][osrlib.crawl.stocking.stock_area] returns. Read `contents` first to
    find out what sort of room you rolled, then take whichever payload came with it and write it onto
    an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] you are building.

    At most one payload rides along: `encounter` for a monster room, `npc_party` for a monster room
    whose encounter row turned out to be an NPC party, or `treasure` for an empty or trap room that
    rolled treasure. A monster room's treasure is its encounter's `hoard` flag rather than a separate
    declaration, so `treasure` is always `None` there. Traps and specials have no payload at all,
    because B/X leaves those to the referee.

    Attributes:
        contents: The room-contents d6's result.
        treasure_present: The treasure d6's result.
        encounter: The monsters, on a monster room.
        npc_party: The NPC party, when the encounter row rolled one.
        treasure: The unguarded treasure, on an empty or trap room that rolled some.
    """

    model_config = ConfigDict(frozen=True)

    contents: Literal["empty", "monster", "special", "trap"]
    """The room-contents d6's result: `"empty"`, `"monster"`, `"special"`, or `"trap"`. A special is
    a magical or unusual feature and a trap is a room trap, and B/X prints example lists for both as
    referee prose, so neither comes with a model."""
    treasure_present: bool
    """Whether the treasure d6 came up in the room's favour. It is always `False` when the row prints
    no treasure chance, which is the case for a special, and no die is rolled then. On a monster room
    this is the same value as the encounter's `hoard` flag."""
    encounter: KeyedEncounter | None = None
    """The monsters in the room, on a monster room, and `None` otherwise. The lines carry fixed
    counts, already rolled, and the encounter's `hoard` follows `treasure_present`. It is `None` on a
    monster room whose row rolled an NPC party."""
    npc_party: StockedNpcParty | None = None
    """The NPC party the encounter row rolled, or `None`. Only ever set on a monster room, and never
    alongside `encounter`. See [`StockedNpcParty`][osrlib.crawl.stocking.StockedNpcParty]."""
    treasure: AreaTreasureSpec | None = None
    """Unguarded treasure for an empty or trap room that rolled some, and `None` otherwise. It is
    always the unguarded form rather than named letters, since nothing is lairing here to have a
    printed hoard."""


def stock_area(
    level_number: int,
    *,
    catalog: MonsterCatalog,
    stream: RngStream,
    table: EncounterTable | None = None,
) -> StockedArea:
    """Roll one keyed room's contents from the B/X stocking tables.

    Call this once per room you want the dice to fill, while you are authoring. It rolls the
    room-contents d6 and, when that row prints a treasure chance, the treasure d6. On a monster room
    it then rolls the encounter table, using `table` when you pass one and the level's compiled band
    otherwise. Take the [`StockedArea`][osrlib.crawl.stocking.StockedArea] it returns and write its
    payload onto an [`AreaSpec`][osrlib.crawl.dungeon.AreaSpec] you are building, then check the
    finished adventure with
    [`validate_adventure`][osrlib.crawl.adventure.validate_adventure].

    Every draw comes from `stream` in the fixed order [`osrlib.crawl.stocking`][osrlib.crawl.stocking]
    sets out, so the result is reproducible from the stream's state and the level alone. Pass the same
    stream to successive calls to stock a whole level, and it advances between rooms.

    This is not something you call during play. It writes nothing, reads no session, and rolls
    content models rather than spawning anything. The session's own wandering check is
    [`wandering_check`][osrlib.crawl.exploration.wandering_check], and it works from the same tables.

    Args:
        level_number: The dungeon level being stocked. It selects the compiled encounter band when you
            pass no `table`, and it is the level whose unguarded-treasure band the resulting
            `AreaTreasureSpec` rolls on later, at play.
        catalog: The monster catalog to resolve rolled ids against. Pass the session's effective
            catalog, which is the shipped one composed with the adventure's bundled templates the way
            [`GameSession.effective_monsters`][osrlib.crawl.session.GameSession.effective_monsters]
            composes it, or [`load_monsters`][osrlib.data.load_monsters] on its own when the adventure
            bundles nothing. Every rolled id is resolved through it exactly as spawning would at play,
            so a stocked encounter can only reference monsters that exist.
        stream: The [`RngStream`][osrlib.core.rng.RngStream] every draw advances. It is what makes the
            result reproducible, and it is the only randomness in the call.
        table: An encounter table to roll on instead of the level's compiled band, usually a level's
            own `WanderingSpec.table`. This mirrors how the crawl's wandering check resolves its
            table, so a level with custom inhabitants stocks from them too. `None` uses
            [`load_encounter_tables`][osrlib.data.load_encounter_tables] for the level.

    Returns:
        What the room holds. See [`StockedArea`][osrlib.crawl.stocking.StockedArea].

    Raises:
        ValueError: If the encounter table rolls a monster id `catalog` does not hold, which means the
            table is malformed. This is the same refusal spawning raises at play, brought forward to
            authoring time.

    Examples:
        ```python
        from osrlib.core.rng import RngStream
        from osrlib.crawl.stocking import stock_area
        from osrlib.data import load_monsters

        area = stock_area(1, catalog=load_monsters(), stream=RngStream.from_seed_material(7, "stocking"))
        print(area.contents, area.treasure_present)
        # monster False

        print([(line.template_id, line.count_fixed) for line in area.encounter.monsters])
        # [('gecko', 3)]
        ```
    """
    result = roll_room_contents(stream)
    contents = result.row.contents
    treasure_present = result.treasure_present
    if contents == "monster":
        return _stock_monster_room(level_number, catalog, stream, table, treasure_present)
    # Empty and trap rooms that rolled treasure get an unguarded cache. A special never
    # rolls treasure, because its printed chance is zero, and like every non-monster room
    # it carries no encounter: the referee designs the special and the trap.
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
    """Fold the rolled individuals into one `KeyedMonster` line per template, in first-appearance order.

    Counts are fixed rather than dice: the roll has already happened, and a concrete number is what an
    author reviews and edits.
    """
    counts: dict[str, int] = {}
    for template_id in template_ids:
        counts[template_id] = counts.get(template_id, 0) + 1
    return tuple(KeyedMonster(template_id=template_id, count_fixed=count) for template_id, count in counts.items())
