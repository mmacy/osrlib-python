"""Shared crawl-test content: a small two-level adventure and a stock party.

The delve dungeon, level 1 (5 × 4):

```text
    x0   x1   x2   x3   x4
y0  ENT——corr——corr           (entrance at (0,0); corridor east to (2,0))
y1       pit  [room_a  ]——sec——(4,1) stairs down
y2            [room_a  ]
```

- A stuck normal door on (2,0)'s south edge into room_a.
- room_a spans (2,1), (3,1), (2,2), (3,2); keyed goblins ×2; a treasure cache
  (chest) with coins and a poison-needle treasure trap.
- A secret door on (3,1)'s east edge to the corridor cell (4,1), which carries
  stairs down to level 2 (0,0).
- The pit room trap covers (1,1), reached by an open edge south of (1,0).

Level 2 (3 × 3): open corridor row y0; a keyed skeleton area at (2,0).
"""

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.items import Coins
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.dungeon import (
    AreaSpec,
    Direction,
    DoorSpec,
    DungeonSpec,
    Edge,
    EdgeKind,
    FeatureSpec,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    TransitionSpec,
    TrapEffect,
    TrapSpec,
    WanderingSpec,
    edge_key,
)
from osrlib.crawl.party import Party
from osrlib.data import load_classes

__all__ = [
    "build_adventure",
    "build_party",
]


def _open(edges: dict, position, direction) -> None:
    edges[edge_key(position, direction)] = Edge(kind=EdgeKind.OPEN)


def _door(edges: dict, position, direction, **door_fields) -> None:
    edges[edge_key(position, direction)] = Edge(kind=EdgeKind.DOOR, door=DoorSpec(**door_fields))


def build_adventure(wandering_chance: int = 1) -> Adventure:
    """Build the shared two-level test adventure.

    Args:
        wandering_chance: The levels' wandering chance-in-six; 0 keeps tests quiet.
    """
    edges_1: dict[str, Edge] = {}
    _open(edges_1, (0, 0), Direction.EAST)
    _open(edges_1, (1, 0), Direction.EAST)
    _open(edges_1, (1, 0), Direction.SOUTH)  # into the pit room
    _door(edges_1, (2, 0), Direction.SOUTH, stuck=True)  # into room_a
    _open(edges_1, (2, 1), Direction.EAST)
    _open(edges_1, (2, 1), Direction.SOUTH)
    _open(edges_1, (3, 1), Direction.SOUTH)
    _open(edges_1, (2, 2), Direction.EAST)
    _door(edges_1, (3, 1), Direction.EAST, kind="secret")  # to the stairs corridor
    _door(edges_1, (4, 1), Direction.SOUTH, locked=True)  # a locked closet at (4,2)

    pit = TrapSpec(
        kind="room",
        trigger="enter",
        effect=TrapEffect(fall_feet=10),
    )
    needle = TrapSpec(
        kind="treasure",
        trigger="open",
        effect=TrapEffect(save={"category": "death", "on_save": "negates"}, kills=True),
    )
    chest = FeatureSpec(
        id="chest",
        kind="treasure_cache",
        description="An iron-bound chest.",
        cell=(3, 2),
        item_ids=("holy_water",),
        coins=Coins(gp=200),
        trap=needle,
    )
    level_1 = LevelSpec(
        number=1,
        width=5,
        height=4,
        edges=edges_1,
        areas=(
            AreaSpec(
                id="pit_room",
                name="Dusty cell",
                cells=((1, 1),),
                trap=pit,
            ),
            AreaSpec(
                id="room_a",
                name="Guard room",
                description="Bones and bedrolls.",
                cells=((2, 1), (3, 1), (2, 2), (3, 2)),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),)),
                features=(chest,),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_down",
                position=(4, 1),
                to_dungeon_id="delve",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.EAST,
            ),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=wandering_chance, interval_turns=2),
    )

    edges_2: dict[str, Edge] = {}
    _open(edges_2, (0, 0), Direction.EAST)
    _open(edges_2, (1, 0), Direction.EAST)
    level_2 = LevelSpec(
        number=2,
        width=3,
        height=3,
        edges=edges_2,
        areas=(
            AreaSpec(
                id="crypt",
                name="Crypt",
                cells=((2, 0),),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="skeleton", count_fixed=3),), aware=True),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_up",
                position=(0, 0),
                to_dungeon_id="delve",
                to_level_number=1,
                to_position=(4, 1),
                to_facing=Direction.WEST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=wandering_chance, interval_turns=2),
    )

    return Adventure(
        name="The Test Delve",
        description="A two-level test dungeon.",
        town=TownSpec(name="Threshold", services=("inn", "trader"), travel_turns={"delve": 6}),
        dungeons=(DungeonSpec(id="delve", name="The Delve", levels=(level_1, level_2)),),
    )


def _member(name: str, class_id: str) -> Character:
    definition = load_classes().get(class_id)
    scores = {ability: 11 for ability in AbilityScore}
    if class_id == "cleric":
        scores[AbilityScore.WIS] = 13
    return Character(
        name=name,
        class_id=class_id,
        race=definition.race,
        level=1,
        xp=0,
        scores=scores,
        alignment="lawful",
        max_hp=6,
        current_hp=6,
        spell_book=("sleep",) if class_id == "magic_user" else (),
    )


def build_party() -> Party:
    """Build a stock four-member party (ids unassigned; the session assigns them)."""
    return Party(
        members=[
            _member("Brakk", "fighter"),
            _member("Sable", "thief"),
            _member("Wynn", "cleric"),
            _member("Elara", "magic_user"),
        ]
    )


def build_milestone_adventure() -> Adventure:
    """The milestone delve: two levels tuned so every scripted beat is reachable.

    Level 1 "halls" (7 × 2): entrance corridor east; a pit room off (1, 0); the
    goblin guard room behind a stuck door with a 400 gp coffer; a secret door east
    of the guard room to the stairs down. Wandering chance 2 every 2 turns.

    Level 2 (7 × 2, quiet): the skeleton crypt (aware, hostile — the turn-undead
    declaration beat), and two goblin kennels (hostile, speed 60 = the armoured
    party's 60) for the flee-with-dropped-treasure distraction and the 30-round
    exhaustion terminal.
    """
    edges_1: dict[str, Edge] = {}
    for x in range(3):
        _open(edges_1, (x, 0), Direction.EAST)
    _open(edges_1, (1, 0), Direction.SOUTH)  # pit hall
    _door(edges_1, (3, 0), Direction.SOUTH, stuck=True)  # guard room
    _open(edges_1, (3, 1), Direction.EAST)
    _door(edges_1, (4, 1), Direction.EAST, kind="secret")  # to the stairs corridor
    _open(edges_1, (5, 1), Direction.EAST)

    level_1 = LevelSpec(
        number=1,
        width=7,
        height=2,
        edges=edges_1,
        areas=(
            AreaSpec(
                id="pit_hall",
                name="Dusty hall",
                cells=((1, 1),),
                trap=TrapSpec(kind="room", trigger="enter", effect=TrapEffect(fall_feet=10)),
            ),
            AreaSpec(
                id="guard_room",
                name="Guard room",
                cells=((3, 1), (4, 1)),
                encounter=KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=6),)),
                features=(FeatureSpec(id="coffer", kind="treasure_cache", cell=(4, 1), coins=Coins(gp=400)),),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_down",
                position=(6, 1),
                to_dungeon_id="halls",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=Direction.EAST,
            ),
        ),
        entrance=(0, 0),
        wandering=WanderingSpec(chance_in_six=2, interval_turns=2),
    )

    edges_2: dict[str, Edge] = {}
    for x in range(6):
        _open(edges_2, (x, 0), Direction.EAST)
    _door(edges_2, (1, 0), Direction.SOUTH)  # crypt
    _open(edges_2, (3, 0), Direction.SOUTH)  # kennel a
    _open(edges_2, (5, 0), Direction.SOUTH)  # kennel b
    _open(edges_2, (6, 0), Direction.SOUTH)  # kennel c

    from osrlib.core.tables import ReactionResult

    level_2 = LevelSpec(
        number=2,
        width=7,
        height=2,
        edges=edges_2,
        areas=(
            AreaSpec(
                id="crypt",
                name="Crypt",
                cells=((1, 1),),
                encounter=KeyedEncounter(
                    monsters=(KeyedMonster(template_id="skeleton", count_fixed=4),),
                    aware=True,
                    stance=ReactionResult.HOSTILE,
                ),
            ),
            AreaSpec(
                id="kennel_a",
                name="Kennel",
                cells=((3, 1),),
                encounter=KeyedEncounter(
                    monsters=(KeyedMonster(template_id="goblin", count_fixed=3),),
                    aware=True,
                    stance=ReactionResult.HOSTILE,
                ),
            ),
            AreaSpec(
                id="kennel_b",
                name="Second kennel",
                cells=((5, 1),),
                encounter=KeyedEncounter(
                    monsters=(KeyedMonster(template_id="goblin", count_fixed=3),),
                    aware=True,
                    stance=ReactionResult.HOSTILE,
                ),
            ),
            AreaSpec(
                id="kennel_c",
                name="Third kennel",
                cells=((6, 1),),
                encounter=KeyedEncounter(
                    monsters=(KeyedMonster(template_id="goblin", count_fixed=3),),
                    aware=True,
                    stance=ReactionResult.HOSTILE,
                ),
            ),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_up",
                position=(0, 0),
                to_dungeon_id="halls",
                to_level_number=1,
                to_position=(6, 1),
                to_facing=Direction.WEST,
            ),
        ),
        wandering=WanderingSpec(chance_in_six=0, interval_turns=2),
    )

    return Adventure(
        name="The Milestone Delve",
        description="Enter from town, delve two levels, and return alive.",
        town=TownSpec(name="Threshold", services=("trader", "inn"), travel_turns={"halls": 6}),
        dungeons=(DungeonSpec(id="halls", name="The Halls", levels=(level_1, level_2)),),
    )


_MILESTONE_LEVELS = {"fighter": 3, "elf": 3, "thief": 3, "cleric": 3, "magic_user": 5}


def build_milestone_party(master_seed: int) -> list:
    """Build the milestone party from the seed's creation and advancement streams.

    Five members created via `create_character` (no purchases — the town shopping
    beat buys gear in-session), leveled by exact-threshold XP awards, with the
    magic-user's book grown to (magic missile, sleep, web ×0 — web and fire ball
    added at level 5 capacity) and everyone's spells memorized pre-session (the
    party arrives prepared; in-session re-preparation is the night-camp beat).

    Args:
        master_seed: The golden's master seed.

    Returns:
        The created characters, in marching order (ids unassigned).

    Raises:
        ValueError: If the rolled scores make a class choice illegal — the golden
            generator rejects the seed and tries the next.
    """
    from osrlib.core.character import ADVANCEMENT_STREAM, CHARACTER_CREATION_STREAM, create_character
    from osrlib.core.classes import apply_xp
    from osrlib.core.rng import RngStreams
    from osrlib.core.ruleset import Ruleset
    from osrlib.core.spells import MemorizedSpell, add_spell_to_book, memorize_spells
    from osrlib.data import load_spells

    streams = RngStreams(master_seed=master_seed)
    creation = streams.get(CHARACTER_CREATION_STREAM)
    advancement = streams.get(ADVANCEMENT_STREAM)
    ruleset = Ruleset(hp_reroll_at_first_level=True)
    spells = load_spells()
    members = []
    roster = (
        ("Brakk", "fighter", ()),
        ("Faelwen", "elf", ("sleep",)),
        ("Sable", "thief", ()),
        ("Wynn", "cleric", ()),
        ("Elara", "magic_user", ("magic_missile",)),
    )
    for name, class_id, starting_spells in roster:
        result = create_character(
            name=name,
            class_id=class_id,
            alignment="lawful",
            ruleset=ruleset,
            stream=creation,
            starting_spell_ids=starting_spells,
        )
        member = result.character
        definition = load_classes().get(class_id)
        while member.level < _MILESTONE_LEVELS[class_id]:
            # Double the remaining XP so a class penalty can't floor the modified
            # award short of the threshold; the one-level-per-award clamp caps it.
            remaining = definition.row(member.level + 1).xp - member.xp
            apply_xp(member, definition, remaining * 2, advancement)
        members.append(member)
    magic_user = members[4]
    for spell_id in ("sleep", "web", "fire_ball"):
        book = add_spell_to_book(magic_user, load_classes().get("magic_user"), spells, spell_id)
        if book.rejections:
            raise ValueError(f"book growth failed: {[r.code for r in book.rejections]}")
    memorize_spells(
        magic_user,
        load_classes().get("magic_user"),
        spells,
        [
            MemorizedSpell(spell_id="magic_missile"),
            MemorizedSpell(spell_id="magic_missile"),
            MemorizedSpell(spell_id="web"),
            MemorizedSpell(spell_id="web"),
            MemorizedSpell(spell_id="fire_ball"),
        ],
    )
    memorize_spells(
        members[3],
        load_classes().get("cleric"),
        spells,
        [MemorizedSpell(spell_id="cure_light_wounds"), MemorizedSpell(spell_id="cure_light_wounds")],
    )
    memorize_spells(members[1], load_classes().get("elf"), spells, [MemorizedSpell(spell_id="sleep")])
    return members
