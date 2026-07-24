"""SRD dungeon stocking: roll one keyed area's contents from the stocking tables.

The stocking *tables* ship as data already — the room-contents d6 with its
per-row treasure-presence chance ([`StockingTable`][osrlib.core.treasure.StockingTable]),
the compiled level-band encounter tables
([`load_encounter_tables`][osrlib.data.load_encounter_tables]), and the treasure
generators. This module is the procedure that consumes them: given a dungeon
level, an effective monster catalog, and one RNG stream,
[`stock_area`][osrlib.crawl.stocking.stock_area] rolls what a single keyed area
holds and answers a frozen [`StockedArea`][osrlib.crawl.stocking.StockedArea] —
content models an author can review, place, and edit, never engine state.

Determinism is the whole point: every draw comes from the one passed
[`RngStream`][osrlib.core.rng.RngStream], in a fixed order, so a stocked area is
reproducible from `(the stream's state, the level, the table)` alone. The order,
mirrored on the crawl's own wandering resolution so stocking a row yields
precisely what a wandering encounter on that row would:

1. the stocking d6 (room contents), then the treasure d6 — but only when the
   selected row's `treasure_chance_in_six` is non-zero (a printed `None` chance
   consumes no die);
2. on a monster room, the encounter table's d20 row, then that row's count dice
   (a fixed count consumes none), clamped `max(1, count)`;
3. then either one `variant_dice` roll (the hydra form: the printed HD dice
   select the template once) or, for a packed-variant pool row, one uniform pick
   per individual.

The boundary is deliberate. A monster room's rolled individuals group by template
into [`KeyedMonster`][osrlib.crawl.dungeon.KeyedMonster] lines whose `count_fixed`
is set at stocking time — printed modules give concrete counts, and a concrete
number is what an author reviews and edits. An empty or trap room that rolls treasure gets an
unguarded [`AreaTreasureSpec`][osrlib.crawl.dungeon.AreaTreasureSpec]; a monster
room's treasure is the encounter itself (its `hoard` flag), never a second
declaration. Traps and specials produce no models — B/X ships example lists as
referee prose, not tables — and an NPC-party row has no authorable content at
all: [`stock_area`][osrlib.crawl.stocking.stock_area] reports the rolled kind and
count and stops. The procedure ends where the referee's design begins.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from osrlib.core.dice import parse, roll
from osrlib.core.monsters import MonsterCatalog
from osrlib.core.rng import RngStream
from osrlib.core.tables import EncounterTable
from osrlib.core.treasure import roll_room_contents
from osrlib.crawl.dungeon import AreaTreasureSpec, KeyedEncounter, KeyedMonster
from osrlib.data import load_encounter_tables

__all__ = [
    "StockedArea",
    "StockedNpcParty",
    "stock_area",
]


class StockedNpcParty(BaseModel):
    """An NPC-party stocking roll: the rolled party kind and its count.

    An NPC-party encounter-table row has no authorable content model — a party
    is generated at play from class treasure, not a keyed encounter and not
    treasure letters. The dice still spoke (the row, then its count), so
    [`stock_area`][osrlib.crawl.stocking.stock_area] reports what they said and
    leaves the party for the author to place by hand.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["basic", "expert"]
    count: int = Field(ge=1)


class StockedArea(BaseModel):
    """The whole answer of stocking one keyed area — content models, never state.

    `contents` is the stocking d6's outcome and `treasure_present` the treasure
    d6's (always `False` when the row's printed chance is zero, e.g. a special).
    At most one authorable payload rides alongside: `encounter` for a monster
    room, `npc_party` for a monster room whose d20 row rolled an NPC party, or
    `treasure` (only ever the unguarded form) for an empty or trap room that
    rolled treasure. A monster room's treasure is its encounter's `hoard` flag,
    so `treasure` stays `None` there; traps and specials carry no model of their
    own — those are the referee's to design.
    """

    model_config = ConfigDict(frozen=True)

    contents: Literal["empty", "monster", "special", "trap"]
    treasure_present: bool
    encounter: KeyedEncounter | None = None
    npc_party: StockedNpcParty | None = None
    treasure: AreaTreasureSpec | None = None


def stock_area(
    level_number: int,
    *,
    catalog: MonsterCatalog,
    stream: RngStream,
    table: EncounterTable | None = None,
) -> StockedArea:
    """Roll one keyed area's contents from the SRD stocking tables.

    Runs the room-contents d6 and, when the row calls for it, the treasure d6;
    on a monster room it then rolls the encounter table (the caller's `table`
    when set, else the level's compiled band). Every draw comes from `stream`
    in the fixed order documented on the module, so the result is reproducible
    from the stream's state alone.

    Args:
        level_number: The dungeon level being stocked — selects the compiled
            encounter band when no `table` override is given, and (via the
            treasure generators at play) the unguarded band.
        catalog: The effective monster catalog (the shipped catalog composed
            with the adventure's bundled templates, the way
            `GameSession.effective_monsters` composes it). Each rolled monster
            id is resolved through it exactly as `session.spawn` would at play,
            so a stocked encounter references only monsters the catalog holds
            and an authored override table naming a bundled monster resolves.
        stream: The RNG stream every draw advances — what makes the result reproducible.
        table: An authored encounter-table override (the level's
            `WanderingSpec.table`), mirroring `wandering_check`'s own
            resolution; `None` uses `load_encounter_tables().for_level`.

    Returns:
        The stocked area.

    Raises:
        ValueError: If the encounter table rolls a monster id the effective
            catalog does not hold — a malformed table, the same refusal
            `session.spawn` raises at play.

    Examples:
        ```python
        from osrlib.core.rng import RngStream
        from osrlib.crawl.stocking import stock_area
        from osrlib.data import load_monsters

        area = stock_area(1, catalog=load_monsters(), stream=RngStream.from_seed_material(42, "stock:1/1/7"))
        assert area.contents in ("empty", "monster", "special", "trap")
        ```
    """
    result = roll_room_contents(stream)
    contents = result.row.contents
    treasure_present = result.treasure_present
    if contents == "monster":
        return _stock_monster_room(level_number, catalog, stream, table, treasure_present)
    # Empty and trap rooms that rolled treasure get an unguarded cache; a special
    # never rolls treasure (its printed chance is zero) and, like every non-monster
    # room, carries no encounter — the referee designs the special and the trap.
    treasure = AreaTreasureSpec(unguarded=True) if treasure_present and contents in ("empty", "trap") else None
    return StockedArea(contents=contents, treasure_present=treasure_present, treasure=treasure)


def _stock_monster_room(
    level_number: int,
    catalog: MonsterCatalog,
    stream: RngStream,
    table: EncounterTable | None,
    treasure_present: bool,
) -> StockedArea:
    """Roll a monster room's d20 encounter and build its keyed lines — the crawl's own resolution."""
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
    # A list (never the model's variadic tuple) so index access is unconditioned by
    # length narrowing; the entry model guarantees at least one id.
    ids = list(entry.monster_ids)
    if entry.variant_dice is not None:
        # The hydra form: the printed HD dice select the template once.
        dice = roll(entry.variant_dice, stream)
        template_ids = [ids[dice.total - _dice_minimum(entry.variant_dice)]] * count
    elif len(ids) > 1:
        # Packed-variant pool: each individual picks uniformly (the crawl's convention).
        template_ids = [ids[stream.randbelow(len(ids))] for _ in range(count)]
    else:
        template_ids = [ids[0]] * count
    # Resolve every distinct rolled id through the effective catalog exactly as
    # session.spawn would — a stocked encounter references only real monsters.
    for template_id in dict.fromkeys(template_ids):
        catalog.get(template_id)
    return StockedArea(
        contents="monster",
        treasure_present=treasure_present,
        encounter=KeyedEncounter(monsters=_group_monsters(template_ids), hoard=treasure_present),
    )


def _group_monsters(template_ids: list[str]) -> tuple[KeyedMonster, ...]:
    """Fold individuals into one `KeyedMonster` line per template, first-appearance order, count fixed."""
    counts: dict[str, int] = {}
    for template_id in template_ids:
        counts[template_id] = counts.get(template_id, 0) + 1
    return tuple(KeyedMonster(template_id=template_id, count_fixed=count) for template_id, count in counts.items())


def _dice_minimum(expression: str) -> int:
    """The lowest total a dice expression can roll — a variant row's first template offset."""
    parsed = parse(expression)
    return parsed.count + parsed.modifier
