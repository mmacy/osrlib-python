"""Outside a battle, a command names as a target only a creature the party can act on now (issue #317).

That's a party member, in any state, or a living creature in a group of the open encounter that hasn't fled: the
creatures a battle declaration's `targets` can name. `CastSpell` is legal only in `town` and `exploring`, where no
encounter is open, so it can name only party members and cells. `UseItem` is legal in `exploring` and
`encounter`, so a scroll read or a staff of healing can also name a creature of the open encounter. The session
refuses any other id in the validation pre-phase with the code it gives an id that matches nothing:
`magic.cast.unknown_target` for a spell or a scroll, and `items.use.unknown_target` for a device. The refusal
costs no draw, no time, and no state, and its rejections are the ones a never-existing id gets, so it reveals
nothing about a creature the party can't see.

The creatures that stay in the session's registry but are in neither set: a monster or an NPC adventurer of an
encounter that has ended, a monster of a group that fled, and a monster the session spawned into no encounter. A
member removed with `RemoveMember` leaves the registry, so it already gets the unknown-target answer, and a guard
keeps it that way.

A dead monster of the open encounter isn't a target a spell or a scroll can name, as in a battle: the battle's
`_cast_targets` refuses "An unknown, dead, out-of-encounter, or fled enemy" with `magic.cast.unknown_target`, and
the player view reports the creature as `"defeated"`. The OSE SRD's *raise dead* restores life "To a recently
deceased human or demihuman", which a monster isn't. A staff of healing keeps the battle's order of checks: a
creature outside the encounter is unknown first, and a dead creature of the encounter can't be healed. The
spells that name a dead or petrified creature, *raise dead* and *stone to flesh*, still work on a party member.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.effects import Condition, EffectDefinition, has_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.crawl._party_location import _cell_ref
from osrlib.crawl.adventure import Adventure
from osrlib.crawl.commands import (
    CastSpell,
    Command,
    CommandResult,
    EndEncounter,
    EngageBattle,
    GrantItem,
    RemoveMember,
    SessionMode,
    SpawnMonsters,
    TravelToTown,
    UseItem,
)
from osrlib.crawl.session import DeathRecord, GameSession
from osrlib.crawl.views import PlayerView
from osrlib.data import load_monsters

ROSTER = (pf.HILD, pf.WREN, pf.TAM)
"""A fighter, a magic-user, and a cleric, at level 3 with 400 hit points each, unless a test sets their levels."""

NEVER = "monster-9999"
"""An id the session never gave out."""

PETRIFIED = EffectDefinition(kind="test_petrification", condition=Condition.PETRIFIED, permanent=True)
"""A permanent turning to stone, as a gaze leaves it."""

CRAVEN = load_monsters().get("goblin").model_copy(update={"id": "craven_goblin", "name": "Craven Goblin", "morale": 2})
"""A goblin of morale 2, which the OSE SRD's morale page says "will never fight", so its group flees as the battle
opens and gets away at once."""


# ---------------------------------------------------------------------- fixtures


def refusal(game: GameSession, command: Command) -> list[tuple[str, dict]]:
    """Send a command that should be refused, check that it cost nothing, and return its rejections in order.

    Nothing means no event, no draw from any stream, no time on the clock, and no change to any state block.
    """
    before = pf.state_without_logs(game)
    result = game.execute(command)
    assert not result.accepted, "the command was accepted"
    assert result.events == ()
    assert pf.state_without_logs(game) == before, "the refusal changed state, drew, or took time"
    return [(rejection.code, dict(rejection.params)) for rejection in result.rejections]


def accepted(result: CommandResult) -> CommandResult:
    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
    return result


def as_never(rejections: list[tuple[str, dict]], target_id: str) -> list[tuple[str, dict]]:
    """The rejections with `target_id` in each `target` param swapped for `NEVER`, to compare with an unknown id."""
    return [
        (code, {key: (NEVER if value == target_id else value) for key, value in params.items()})
        for code, params in rejections
    ]


def player_view(game: GameSession) -> PlayerView:
    view = game.view(Visibility.PLAYER)
    assert isinstance(view, PlayerView)
    return view


def scroll(game: GameSession, name: str, spell_id: str, *, spell_list: str = "magic_user") -> MagicItemInstance:
    """Put an identified scroll of one spell in the member's pack."""
    instance = MagicItemInstance(
        instance_id=f"magic-item-{9100 + len(game.member(pf.named(game, name)).inventory.items)}",
        template_id="spell_scroll_1",
        identified=True,
        state={"spell_list": spell_list, "spells": (spell_id,)},
    )
    game.member(pf.named(game, name)).inventory.items.append(instance)
    return instance


def staff_of_healing(game: GameSession, name: str) -> MagicItemInstance:
    """Grant the member a staff of healing by command, and return it."""
    member_id = pf.named(game, name)
    accepted(game.execute(GrantItem(character_id=member_id, item_id="staff_of_healing")))
    return next(
        item
        for item in game.member(member_id).inventory.items
        if isinstance(item, MagicItemInstance) and item.template_id == "staff_of_healing"
    )


def die(game: GameSession, member_id: str) -> None:
    """Kill a party member where they stand, with the death record the session keeps for *raise dead*."""
    kill(game.member(member_id))
    game.death_records[member_id] = DeathRecord(round=game.clock.rounds, cause="damage")


def ended(roster=ROSTER, **battle_args) -> tuple[GameSession, list[str]]:
    """An encounter with two goblins that the referee ended, so the session is exploring with none open.

    The goblins stay in `session.registry()`, and the player view lists no encounter. The first goblin is alive and
    the second dead, killed before the encounter ended, as a wand might have killed it.
    """
    game = pf.battle(roster, engage=False, **battle_args)
    goblins = list(game.encounter.groups[0].monster_ids)
    kill(game.combatant(goblins[1]))
    accepted(game.execute(EndEncounter(outcome="departed")))
    assert game.mode is SessionMode.EXPLORING and game.encounter is None, "precondition: no encounter is open"
    assert player_view(game).encounter is None, "precondition: the player view lists no encounter"
    assert set(goblins) <= set(game.registry()), "precondition: the goblins are still in the registry"
    return game, goblins


def magic_missile(game: GameSession, target_id: str) -> CastSpell:
    return CastSpell(
        character_id=pf.named(game, "Wren"), spell_id="magic_missile", mode="missiles", targets=(target_id,)
    )


def heal_with(game: GameSession, staff: MagicItemInstance, target_id: str) -> UseItem:
    return UseItem(character_id=pf.named(game, "Tam"), item_id=staff.instance_id, target_id=target_id)


def read(game: GameSession, instance: MagicItemInstance, target_id: str, *, name: str = "Wren") -> UseItem:
    return UseItem(character_id=pf.named(game, name), item_id=instance.instance_id, target_id=target_id)


def craven_adventure() -> Adventure:
    return Adventure.model_validate({**pf.corridor().model_dump(), "monsters": [CRAVEN.model_dump()]})


def fled_and_ended() -> tuple[GameSession, str]:
    """A craven goblin met, fought, and fled, which ended the encounter. Returns the session and the goblin's id."""
    for seed in pf.SEEDS:
        game = pf.session(seed, ROSTER, spells={"Wren": ("magic_missile",)}, adventure=craven_adventure())
        accepted(game.execute(SpawnMonsters(template_id="craven_goblin", count_fixed=1, distance_feet=60)))
        if game.mode is SessionMode.ENCOUNTER:
            accepted(game.execute(EngageBattle()))
        if game.mode is not SessionMode.EXPLORING:
            continue
        craven = next(iter(game.monsters))
        assert game.monsters[craven].template.id == "craven_goblin", "precondition: the only monster is the craven"
        return game, craven
    raise AssertionError("precondition: no seed let the craven goblin flee and end the encounter")


def second_encounter(*modes: SessionMode, **battle_args) -> tuple[GameSession, list[str]]:
    """The session `ended` gives, with a second encounter open on a new group of two goblins 5' away.

    The session is in one of `modes`: `encounter`, or `battle` when the goblins attacked at once. Returns the
    session and the first encounter's goblins.
    """
    seed = pf.SEEDS.start
    while seed < pf.SEEDS.stop:
        game, goblins = ended(first_seed=seed, **battle_args)
        accepted(game.execute(SpawnMonsters(template_id="goblin", count_fixed=2, distance_feet=5)))
        if game.mode in modes:
            assert all(goblin not in group.monster_ids for goblin in goblins for group in game.encounter.groups), (
                "precondition: the second encounter's group is new"
            )
            return game, goblins
        seed = game.master_seed + 1
    raise AssertionError("precondition: no seed opened a second encounter")


def pursued_with_a_fled_group() -> tuple[GameSession, str, str]:
    """An open encounter in `encounter` mode with one group that fled and one that pursues the party.

    The party fought two goblins, the referee added a craven goblin that fled as it joined, and the party retreated.
    The goblins give chase, so the encounter stays open in `encounter` mode with the craven goblin's group fled.
    Returns the session, a pursuing goblin's id, and the craven goblin's id.
    """
    adventure = craven_adventure()
    for seed in pf.seeds(roster=ROSTER, adventure=adventure, distance=60, light=True):
        game = pf.battle(ROSTER, adventure=adventure, distance=60, light=True, first_seed=seed)
        pf.hold_monsters(game)
        accepted(game.execute(SpawnMonsters(template_id="craven_goblin", count_fixed=1, distance_feet=60)))
        pf.release_monsters(game)
        pf.play(game, *(pf.declare(member_id, "move", move="retreat") for member_id in pf.able(game)))
        if game.mode is not SessionMode.ENCOUNTER:
            continue
        pursuers, craven = game.encounter.groups
        assert not pursuers.fled and craven.fled, "precondition: the goblins pursue and the craven goblin fled"
        assert game.encounter.pursuit is not None, "precondition: the goblins are pursuing"
        return game, pursuers.monster_ids[0], craven.monster_ids[0]
    raise AssertionError("precondition: no seed left the craven goblin fled and the goblins pursuing")


# ---------------------------------------------------------------------- CastSpell outside an encounter


def test_the_issue_a_cast_at_a_goblin_of_an_ended_encounter_is_refused():
    """The issue's repro: *magic missile* at `monster-0001` after the referee's `EndEncounter`."""
    game, (goblin, _) = ended(spells={"Wren": ("magic_missile",)})

    assert refusal(game, magic_missile(game, goblin)) == [("magic.cast.unknown_target", {"target": goblin})]


def test_the_refusal_is_the_one_an_id_that_never_existed_gets():
    game, (goblin, dead_goblin) = ended(spells={"Wren": ("magic_missile",)})
    never = refusal(game, magic_missile(game, NEVER))

    assert as_never(refusal(game, magic_missile(game, goblin)), goblin) == never
    assert as_never(refusal(game, magic_missile(game, dead_goblin)), dead_goblin) == never


def test_an_area_spell_names_the_first_creature_the_party_cant_act_on():
    """*Fire ball* takes creature ids outside a battle, and each one is checked in the order given."""
    game, goblins = ended(levels={"Wren": 5}, spells={"Wren": ("fire_ball",)})
    cast = CastSpell(character_id=pf.named(game, "Wren"), spell_id="fire_ball", mode="damage", targets=tuple(goblins))

    assert refusal(game, cast) == [("magic.cast.unknown_target", {"target": goblins[0]})]


def test_a_cast_in_town_at_a_goblin_of_an_ended_encounter_is_refused():
    game, (goblin, _) = ended(spells={"Wren": ("magic_missile",)})
    accepted(game.execute(TravelToTown()))
    assert game.mode is SessionMode.TOWN, "precondition: the party is in town"

    assert refusal(game, magic_missile(game, goblin)) == [("magic.cast.unknown_target", {"target": goblin})]


def test_a_cast_at_an_npc_adventurer_of_an_ended_encounter_is_refused():
    game = pf.battle(ROSTER, npc_party=True, engage=False, spells={"Wren": ("magic_missile",)})
    adventurer = game.encounter.groups[0].monster_ids[0]
    accepted(game.execute(EndEncounter(outcome="parley")))
    assert adventurer in game.npcs, "precondition: the NPC adventurer is still in the session"

    assert refusal(game, magic_missile(game, adventurer)) == [("magic.cast.unknown_target", {"target": adventurer})]


def test_a_cast_at_a_goblin_whose_group_fled_is_refused():
    """The craven goblin's group flees as the battle opens, and with no one left to fight, the encounter ends."""
    game, craven = fled_and_ended()

    assert refusal(game, magic_missile(game, craven)) == [("magic.cast.unknown_target", {"target": craven})]


def test_a_cast_at_a_monster_spawned_into_no_encounter_is_refused():
    """`GameSession.spawn` puts a monster in the session and nothing in front of the party."""
    game = pf.session(0, ROSTER, spells={"Wren": ("magic_missile",)})
    goblin = game.spawn("goblin", 1)[0].id
    assert game.encounter is None and goblin in game.registry(), "precondition: the goblin stands in no encounter"

    assert refusal(game, magic_missile(game, goblin)) == [("magic.cast.unknown_target", {"target": goblin})]


def test_a_cast_at_a_removed_member_is_refused():
    """`RemoveMember` takes the member out of the registry, so the answer is the unknown id's already."""
    game = pf.session(0, ROSTER, spells={"Wren": ("magic_missile",)})
    hild = pf.named(game, "Hild")
    accepted(game.execute(RemoveMember(character_id=hild, give_to=pf.named(game, "Tam"))))

    assert refusal(game, magic_missile(game, hild)) == [("magic.cast.unknown_target", {"target": hild})]


def test_a_cast_at_an_id_that_never_existed_is_refused():
    game = pf.session(0, ROSTER, spells={"Wren": ("magic_missile",)})

    assert refusal(game, magic_missile(game, NEVER)) == [("magic.cast.unknown_target", {"target": NEVER})]


# ---------------------------------------------------------------------- party members, in any state


def test_raise_dead_still_restores_a_dead_member():
    """A 9th-level cleric raises Hild, who died this round: the OSE SRD allows four days a level above 7th."""
    game = pf.session(0, ROSTER, levels={"Tam": 9}, spells={"Tam": ("raise_dead",)})
    hild = pf.named(game, "Hild")
    die(game, hild)
    cast = CastSpell(character_id=pf.named(game, "Tam"), spell_id="raise_dead", mode="restore_life", targets=(hild,))

    accepted(game.execute(cast))
    assert not has_condition(game.member(hild), Condition.DEAD)


def test_stone_to_flesh_still_restores_a_petrified_member():
    game = pf.session(0, ROSTER, levels={"Wren": 11}, spells={"Wren": ("stone_to_flesh",)})
    hild = pf.named(game, "Hild")
    pf.attach(game, PETRIFIED, hild)
    cast = CastSpell(character_id=pf.named(game, "Wren"), spell_id="stone_to_flesh", mode="restore", targets=(hild,))

    accepted(game.execute(cast))
    assert not has_condition(game.member(hild), Condition.PETRIFIED)


def test_a_scroll_of_raise_dead_still_restores_a_dead_member_after_an_encounter():
    """Wren died, and Hild carries the torch the cleric reads by."""
    game, _ = ended(light=True)
    wren = pf.named(game, "Wren")
    die(game, wren)
    instance = scroll(game, "Tam", "raise_dead", spell_list="cleric")
    use = UseItem(character_id=pf.named(game, "Tam"), item_id=instance.instance_id, mode="restore_life", target_id=wren)

    accepted(game.execute(use))
    assert not has_condition(game.member(wren), Condition.DEAD)


def test_a_cast_at_the_party_cell_is_unchanged():
    """A `cell:` reference isn't a creature, so this chunk leaves its checks alone."""
    game = pf.session(0, ROSTER, spells={"Wren": ("light_mu",)})
    cell = _cell_ref(game)
    cast = CastSpell(character_id=pf.named(game, "Wren"), spell_id="light_mu", mode="illuminate", targets=(cell,))

    accepted(game.execute(cast))


def test_the_staff_still_refuses_to_heal_a_dead_member():
    game, _ = ended()
    staff = staff_of_healing(game, "Tam")
    hild = pf.named(game, "Hild")
    die(game, hild)

    assert refusal(game, heal_with(game, staff, hild)) == [
        ("items.use.target_cannot_be_healed", {"item": staff.instance_id, "target": hild})
    ]


# ---------------------------------------------------------------------- UseItem outside an encounter


def test_a_scroll_read_at_a_goblin_of_an_ended_encounter_is_refused():
    game, (goblin, _) = ended(light=True)
    instance = scroll(game, "Wren", "magic_missile")

    assert refusal(game, read(game, instance, goblin)) == [("magic.cast.unknown_target", {"target": goblin})]


def test_a_scroll_read_at_a_monster_spawned_into_no_encounter_is_refused():
    game = pf.session(0, ROSTER, light=True)
    goblin = game.spawn("goblin", 1)[0].id
    assert game.encounter is None and goblin in game.registry(), "precondition: the goblin stands in no encounter"
    instance = scroll(game, "Wren", "magic_missile")

    assert refusal(game, read(game, instance, goblin)) == [("magic.cast.unknown_target", {"target": goblin})]


def test_a_scroll_names_its_targets_in_targets_too():
    game, (goblin, _) = ended(light=True)
    instance = scroll(game, "Wren", "magic_missile")
    use = UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, targets=(goblin,))

    assert refusal(game, use) == [("magic.cast.unknown_target", {"target": goblin})]


@pytest.mark.parametrize("which", [0, 1], ids=["living", "dead"])
def test_the_staff_wont_heal_a_goblin_of_an_ended_encounter(which):
    """The dead goblin gets the unknown id's answer too, not `items.use.target_cannot_be_healed`."""
    game, goblins = ended()
    staff = staff_of_healing(game, "Tam")
    goblin = goblins[which]
    never = refusal(game, heal_with(game, staff, NEVER))

    rejections = refusal(game, heal_with(game, staff, goblin))
    assert rejections == [("items.use.unknown_target", {"target": goblin})]
    assert as_never(rejections, goblin) == never


def test_a_potion_for_a_goblin_of_an_ended_encounter_is_refused_as_before():
    """A potion goes only to a party member, so the goblin gets the answer an id that never existed gets."""
    game, (goblin, _) = ended()
    hild = pf.named(game, "Hild")
    accepted(game.execute(GrantItem(character_id=hild, item_id="potion_of_healing")))
    potion = next(item for item in game.member(hild).inventory.items if isinstance(item, MagicItemInstance))
    use = UseItem(character_id=hild, item_id=potion.instance_id, target_id=goblin)

    rejections = refusal(game, use)
    assert rejections == [("items.use.target_not_allowed", {"item": potion.instance_id, "target": goblin})]
    assert as_never(rejections, goblin) == refusal(game, use.model_copy(update={"target_id": NEVER}))


# ---------------------------------------------------------------------- UseItem in an open encounter


def test_a_scroll_read_at_a_goblin_of_the_open_encounter_is_accepted():
    game = pf.battle(ROSTER, engage=False, light=True)
    goblin = game.encounter.groups[0].monster_ids[0]
    instance = scroll(game, "Wren", "magic_missile")

    events = accepted(game.execute(read(game, instance, goblin))).events
    assert any(getattr(event, "target_id", None) == goblin for event in events if event.code == "combat.damage.dealt")


def test_the_staff_heals_a_goblin_of_the_open_encounter():
    """The OSE SRD's staff of healing "Is effective on any individual", and the goblin stands in front of the party."""
    game = pf.battle(ROSTER, engage=False)
    goblin = game.encounter.groups[0].monster_ids[0]
    staff = staff_of_healing(game, "Tam")

    accepted(game.execute(heal_with(game, staff, goblin)))


def test_a_scroll_read_at_an_npc_adventurer_of_the_open_encounter_is_accepted():
    game = pf.battle(ROSTER, npc_party=True, engage=False, light=True)
    adventurer = game.encounter.groups[0].monster_ids[0]
    instance = scroll(game, "Wren", "magic_missile")

    accepted(game.execute(read(game, instance, adventurer)))


def test_a_scroll_read_at_a_dead_goblin_of_the_open_encounter_is_refused():
    """As in a battle, a dead enemy isn't a target. Today the missile lands on the corpse."""
    game = pf.battle(ROSTER, engage=False, light=True)
    goblin = game.encounter.groups[0].monster_ids[1]
    kill(game.combatant(goblin))
    instance = scroll(game, "Wren", "magic_missile")

    assert refusal(game, read(game, instance, goblin)) == [("magic.cast.unknown_target", {"target": goblin})]


def test_the_staff_still_refuses_to_heal_a_dead_goblin_of_the_open_encounter():
    """The battle's order: a creature of the encounter is known, and a dead one can't be healed."""
    game = pf.battle(ROSTER, engage=False)
    goblin = game.encounter.groups[0].monster_ids[1]
    kill(game.combatant(goblin))
    staff = staff_of_healing(game, "Tam")

    assert refusal(game, heal_with(game, staff, goblin)) == [
        ("items.use.target_cannot_be_healed", {"item": staff.instance_id, "target": goblin})
    ]


def test_a_scroll_read_at_a_goblin_of_an_earlier_encounter_is_refused_while_another_is_open():
    game, (old_goblin, _) = second_encounter(SessionMode.ENCOUNTER, light=True)
    instance = scroll(game, "Wren", "magic_missile")

    assert refusal(game, read(game, instance, old_goblin)) == [("magic.cast.unknown_target", {"target": old_goblin})]


def test_a_scroll_read_at_a_goblin_whose_group_fled_is_refused_during_a_pursuit():
    game, _, craven = pursued_with_a_fled_group()
    instance = scroll(game, "Wren", "magic_missile")

    assert refusal(game, read(game, instance, craven)) == [("magic.cast.unknown_target", {"target": craven})]


def test_a_scroll_read_at_a_pursuing_goblin_is_accepted():
    game, pursuer, _ = pursued_with_a_fled_group()
    instance = scroll(game, "Wren", "magic_missile")

    accepted(game.execute(read(game, instance, pursuer)))


def test_the_staff_wont_heal_a_goblin_whose_group_fled_during_a_pursuit():
    game, _, craven = pursued_with_a_fled_group()
    staff = staff_of_healing(game, "Tam")

    assert refusal(game, heal_with(game, staff, craven)) == [("items.use.unknown_target", {"target": craven})]


# ---------------------------------------------------------------------- the battle's staff of healing


def test_a_battle_staff_answers_a_dead_goblin_of_an_ended_encounter_as_an_unknown_id():
    """The battle checks the encounter after the staff's own checks, so today the dead goblin can't be healed.

    The answer the battle gives a living goblin of the ended encounter, and an id that never existed, is
    `items.use.unknown_target`. Sharing the out-of-battle check gives the dead goblin that answer too.
    """
    game, (old_goblin, old_dead) = second_encounter(SessionMode.ENCOUNTER, SessionMode.BATTLE)
    staff = staff_of_healing(game, "Tam")
    if game.mode is SessionMode.ENCOUNTER:
        accepted(game.execute(EngageBattle()))
    assert game.mode is SessionMode.BATTLE, "precondition: a battle on the second encounter"
    pf.hold_monsters(game)
    tam = pf.named(game, "Tam")

    def staff_round(target_id: str):
        return pf.refused(game, pf.declare(tam, "use_item", item_id=staff.instance_id, targets=(target_id,)))

    never = staff_round(NEVER)
    assert as_never(staff_round(old_goblin), old_goblin) == never
    assert as_never(staff_round(old_dead), old_dead) == never
