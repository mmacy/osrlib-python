"""Shared setup for the acceptance tests of member positions on the range track (issue #209).

The battles are built by command from seeded sessions, as the other battle tests build them. The party is the one
from the design's walk-through: two fighters and a cleric in plate (20' a round under basic encumbrance), a thief in
leather (30'), an elf in chain mail (20'), and a magic-user in no armour (40'). Every member has ability scores of
10, so no modifier from a score touches an attack, and 400 hit points, so nobody falls by chance.

`battle` takes the first seed from `SEEDS` that opens the battle it describes, so two calls with the same arguments
give the same session. `HoldPolicy` keeps the monsters still while a test sets members at the distances it needs,
without depending on the monsters' tactics. The helpers assert their own preconditions.
"""

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.combat import incapacitated
from osrlib.core.effects import EffectDefinition
from osrlib.core.events import AttackRolledEvent, Event, InitiativeRolledEvent, SavingThrowRolledEvent
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell, memorize_spells
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.battle import MonsterAction
from osrlib.crawl.commands import (
    BattleDeclaration,
    CommandResult,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    GrantItem,
    LightSource,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
    SpawnNpcParty,
)
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.events import GroupMovedEvent, MemberMovedEvent, MemberWithdrewEvent
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_spells
from osrlib.persistence import session_state

SEEDS = range(0, 300)
"""Seeds `battle` scans for one that opens the battle a test asks for."""

HILD = ("Hild", "fighter")
OSRIC = ("Osric", "fighter")
NIM = ("Nim", "thief")
AELIN = ("Aelin", "elf")
WREN = ("Wren", "magic_user")
TAM = ("Tam", "cleric")

WALK = (HILD, OSRIC, NIM, AELIN, WREN, TAM)
"""The walk-through party, in marching order."""

ARMOUR = {"Hild": "plate_mail", "Osric": "plate_mail", "Tam": "plate_mail", "Nim": "leather", "Aelin": "chainmail"}
"""What each member wears. Plate and chain mail give 20' a round, leather 30', and no armour 40'."""

WEAPONS = {"Hild": ("sword",), "Osric": ("sword",), "Nim": ("dagger",), "Aelin": ("short_bow",), "Tam": ("mace",)}
"""What each member wields. Wren wields nothing and fights unarmed."""

RATE = {"Hild": 20, "Osric": 20, "Nim": 30, "Aelin": 20, "Wren": 40, "Tam": 20}
"""Each member's encounter rate in feet per round, with the armour in `ARMOUR`."""

HASTE = EffectDefinition(kind="haste", params={"attacks_multiplier": 2, "movement_multiplier": 2})


def character(name: str, class_id: str, *, level: int = 3, hp: int = 400) -> Character:
    """A character with scores of 10 and `hp` hit points."""
    definition = load_classes().get(class_id)
    return Character(
        name=name,
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores={ability: 10 for ability in AbilityScore},
        alignment="lawful",
        max_hp=hp,
        current_hp=hp,
    )


def corridor() -> Adventure:
    """One level: a 10' corridor two cells long, so the formation is two wide."""
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    return Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))


def hall() -> Adventure:
    """One level: a room two cells square, so the formation is four wide."""
    edges = {
        "1,0:west": Edge(kind=EdgeKind.OPEN),
        "1,1:west": Edge(kind=EdgeKind.OPEN),
        "0,1:north": Edge(kind=EdgeKind.OPEN),
        "1,1:north": Edge(kind=EdgeKind.OPEN),
    }
    room = AreaSpec(id="hall", name="Hall", cells=((0, 0), (1, 0), (0, 1), (1, 1)))
    level = LevelSpec(number=1, width=2, height=2, entrance=(0, 0), edges=edges, areas=(room,))
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    return Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))


def session(
    seed: int,
    roster=WALK,
    *,
    spells: dict[str, tuple[str, ...]] | None = None,
    levels: dict[str, int] | None = None,
    light: bool = False,
    adventure: Adventure | None = None,
    ruleset: Ruleset | None = None,
) -> GameSession:
    """The roster in the dungeon, armoured and armed by command, with `spells` memorized before the session opens.

    With `light`, the first member lights a torch, so the monsters can't be surprised.
    """
    members = [character(name, class_id, level=(levels or {}).get(name, 3)) for name, class_id in roster]
    catalog = load_spells()
    for member in members:
        prepared = (spells or {}).get(member.name, ())
        if prepared:
            definition = load_classes().get(member.class_id)
            if definition.id in ("magic_user", "elf"):
                member.spell_book = tuple(dict.fromkeys(prepared))
            memorize_spells(member, definition, catalog, [MemorizedSpell(spell_id=spell_id) for spell_id in prepared])
    game = GameSession.new(Party(members=members), adventure or corridor(), seed=seed, ruleset=ruleset)
    for member in game.party.members:
        gear = [*((ARMOUR[member.name],) if member.name in ARMOUR else ()), *WEAPONS.get(member.name, ())]
        for item_id in gear:
            assert game.execute(GrantItem(character_id=member.id, item_id=item_id)).accepted
            assert game.execute(EquipItem(character_id=member.id, item_id=item_id)).accepted
        if member.name == "Aelin":
            assert game.execute(GrantItem(character_id=member.id, item_id="arrows", quantity=40)).accepted
    if light:
        first = game.party.members[0].id
        assert game.execute(GrantItem(character_id=first, item_id="torch", quantity=6)).accepted
        assert game.execute(GrantItem(character_id=first, item_id="tinder_box")).accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    if light:
        for _ in range(20):
            lit = game.execute(LightSource(character_id=game.party.members[0].id, item_id="torch"))
            if any(event.code == "exploration.light.lit" for event in lit.events):
                break
        assert game.party_light()[0], "precondition: the party carries a lit torch"
    return game


def _opened(game: GameSession, result: CommandResult, *, surprised: bool, engage: bool) -> bool:
    """Whether the encounter `result` opened is the one wanted. The party is never surprised."""
    assert result.accepted, [rejection.code for rejection in result.rejections]
    sides = {event.side: event.surprised for event in result.events if event.code == "encounter.surprise.rolled"}
    if sides.get("party") is not False or sides.get("monsters") is not surprised:
        return False
    if not engage:
        return game.mode is SessionMode.ENCOUNTER
    if game.mode is SessionMode.ENCOUNTER:
        assert game.execute(EngageBattle()).accepted
    if game.mode is not SessionMode.BATTLE or game.battle.round != 0:
        return False
    assert game.battle.monsters_hold_rounds == (1 if surprised else 0), "precondition: the free round as wanted"
    return True


def battle(
    roster=WALK,
    *,
    template_id: str = "goblin",
    count: int = 2,
    distance: int = 30,
    surprised: bool = False,
    npc_party: bool = False,
    engage: bool = True,
    first_seed: int = SEEDS.start,
    **session_args,
) -> GameSession:
    """A battle against one group spawned by command at `distance`, at round 0, with the party unsurprised.

    With `surprised`, the party surprised the monsters, so the first round is the party's free round. Without it,
    neither side was surprised. With `npc_party`, the group is a basic party of NPC adventurers. Without `engage`,
    the session stays in `encounter` mode.
    """
    for seed in range(first_seed, SEEDS.stop):
        game = session(seed, roster, **session_args)
        if npc_party:
            spawn = SpawnNpcParty(party_kind="basic", distance_feet=distance)
        else:
            spawn = SpawnMonsters(template_id=template_id, count_fixed=count, distance_feet=distance)
        if not _opened(game, game.execute(spawn), surprised=surprised, engage=engage):
            continue
        assert game.encounter.groups[0].distance_feet == distance, "precondition: the group at its distance"
        return game
    raise AssertionError(f"precondition: no seed opened the battle (surprised={surprised})")


def seeds(**battle_args):
    """Each seed from which `battle` builds the battle `battle_args` describe, in order."""
    seed = SEEDS.start
    while True:
        seed = battle(first_seed=seed, **battle_args).master_seed
        yield seed
        seed += 1


def named(game: GameSession, name: str) -> str:
    return next(member.id for member in game.party.members if member.name == name)


def group_id(game: GameSession, index: int = 0) -> str:
    return game.encounter.groups[index].id


def declare(member_id: str, action: str = "hold", **fields) -> BattleDeclaration:
    return BattleDeclaration(character_id=member_id, action=action, **fields)


def close(game: GameSession, member_id: str, *, group: str | None = None) -> BattleDeclaration:
    """A `move` that closes on the first group, or on `group`."""
    return declare(member_id, "move", move="close", target_group_id=group or group_id(game))


def strike(game: GameSession, member_id: str, *, group: str | None = None, **fields) -> BattleDeclaration:
    """An `attack` on the first group, or on `group`, with the session's choice of weapon unless `fields` names one."""
    return declare(member_id, "attack", target_group_id=group or group_id(game), **fields)


def able(game: GameSession) -> list[str]:
    return [member.id for member in game.party.living_members() if not incapacitated(member)]


def plan(game: GameSession, *declared: BattleDeclaration) -> tuple[BattleDeclaration, ...]:
    """The members in `declared` as given, and a `hold` for every other able member, in marching order."""
    by_id = {declaration.character_id: declaration for declaration in declared}
    members = able(game)
    assert set(by_id) <= set(members), "precondition: every declared member can act"
    return tuple(by_id.get(member_id, declare(member_id)) for member_id in members)


def round_(game: GameSession, *declared: BattleDeclaration) -> CommandResult:
    return game.execute(ResolveBattleRound(declarations=plan(game, *declared)))


def play(game: GameSession, *declared: BattleDeclaration) -> list[Event]:
    """Send one round, assert it was accepted, and return its events."""
    result = round_(game, *declared)
    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
    return list(result.events)


def state_without_logs(game: GameSession) -> dict:
    """Every state block, the clock and the random number streams among them, without the two logs."""
    state = session_state(game)
    state.pop("command_log")
    state.pop("event_log")
    return state


def refused(game: GameSession, *declared: BattleDeclaration) -> list[tuple[str, dict]]:
    """Send a round that should be refused, check that it cost nothing, and return its rejections in order."""
    before = state_without_logs(game)
    log_size = len(game.command_log)
    result = round_(game, *declared)
    assert not result.accepted, "the round was accepted"
    assert result.events == ()
    assert state_without_logs(game) == before, "a refused round changed the session"
    assert len(game.command_log) == log_size
    return [(rejection.code, dict(rejection.params)) for rejection in result.rejections]


class HoldPolicy:
    """A test policy: every monster of the group holds, so the group never moves or attacks."""

    def choose(self, session, group, stream):
        return [MonsterAction(monster_id=monster_id, kind="hold") for monster_id in group.monster_ids]


def hold_monsters(game: GameSession) -> None:
    """Keep every group in the encounter still, by registering `HoldPolicy` for it."""
    for group in game.encounter.groups:
        game.action_policies[group.id] = HoldPolicy()


def release_monsters(game: GameSession) -> None:
    """Give every group its default policy back."""
    game.action_policies.clear()


def advance(game: GameSession, member_id: str) -> int:
    """The member's advance in the running battle. A member with no entry stands on the line."""
    return game.battle.advance_feet.get(member_id, 0)


def advances(game: GameSession) -> dict[str, int]:
    """Every living member's advance, zero for a member on the line."""
    return {member.id: advance(game, member.id) for member in game.party.living_members()}


def attach(game: GameSession, definition: EffectDefinition, target_id: str) -> None:
    effect, _ = game.ledger.attach(
        definition, target_id, clock=game.clock, allocator=game.allocator, registry=game.registry()
    )
    assert effect is not None


def moved(events) -> list[MemberMovedEvent]:
    return [event for event in events if isinstance(event, MemberMovedEvent)]


def group_moves(events) -> list[GroupMovedEvent]:
    return [event for event in events if isinstance(event, GroupMovedEvent)]


def trades(events) -> list[MemberWithdrewEvent]:
    return [event for event in events if isinstance(event, MemberWithdrewEvent)]


def attacks_by(events, attacker_id: str) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id == attacker_id]


def monster_attacks(game: GameSession, events) -> list[AttackRolledEvent]:
    """The attack rolls the other side made: monsters' and NPC adventurers'."""
    party = {member.id for member in game.party.members}
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id not in party]


def saves_by_members(game: GameSession, events) -> set[str]:
    """The party members who rolled a saving throw in `events`."""
    party = {member.id for member in game.party.members}
    return {event.target_id for event in events if isinstance(event, SavingThrowRolledEvent)} & party


def monsters_first(game: GameSession, events) -> bool:
    initiative = next(event for event in events if isinstance(event, InitiativeRolledEvent))
    return initiative.order[0] not in {member.id for member in game.party.members}


def position(events, event) -> int:
    """Where `event` sits in `events`, by identity, since two events can be equal."""
    return next(index for index, candidate in enumerate(events) if candidate is event)
