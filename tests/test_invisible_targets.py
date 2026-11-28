"""A spell, a scroll, or a staff of healing can't be aimed at an invisible monster the party meets (issue #321).

In a battle, an invisible combatant can't be targeted. A battle's `cast` and scroll declarations refuse any target
that has the `invisible` condition with `battle.declaration.invisible_target`: a monster, an NPC adventurer, another
member, or the caster named by id. A battle's staff of healing refuses a touch on another creature that's invisible,
members included, with `items.use.invisible_target`.

Outside a battle, a scroll read with `UseItem` refuses an invisible monster or NPC adventurer of the open encounter
with `magic.cast.invisible_target`, and a staff of healing refuses one with `items.use.invisible_target`. An
invisible party member is still a target there, for `CastSpell`, a scroll, and the staff, because an ally cooperates
with the caster. `CastSpell` is never legal while an encounter is open, so it never names a monster. Each refusal has
the id in `target` and costs no draw, no time, and no state.

The visibility check never reports a dead creature's invisibility, because the player view counts the conditions of
living creatures alone. A spell or a scroll gives a dead creature of the encounter `magic.cast.unknown_target`
first, and a staff of healing gives a dead or petrified creature `items.use.target_cannot_be_healed` first.
"""

import pytest

import positions_fixtures as pf
from osrlib.core.effects import Condition, EffectDefinition, has_condition, kill
from osrlib.core.events import Visibility
from osrlib.core.items import MagicItemInstance
from osrlib.crawl.commands import CastSpell, Command, CommandResult, EngageBattle, GrantItem, UseItem
from osrlib.crawl.session import GameSession

ROSTER = (pf.HILD, pf.WREN, pf.TAM)

INVISIBLE = EffectDefinition(kind="invisibility", condition=Condition.INVISIBLE, permanent=True)

PETRIFIED = EffectDefinition(kind="test_petrification", condition=Condition.PETRIFIED, permanent=True)


def refusal(game: GameSession, command: Command) -> list[tuple[str, dict]]:
    """Send a command that should be refused, check that it cost nothing, and return its rejections in order."""
    before = pf.state_without_logs(game)
    result = game.execute(command)
    assert not result.accepted, "the command was accepted"
    assert result.events == ()
    assert pf.state_without_logs(game) == before, "the refusal changed state, drew, or took time"
    return [(rejection.code, dict(rejection.params)) for rejection in result.rejections]


def accepted(result: CommandResult) -> CommandResult:
    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
    return result


def scroll(game: GameSession, name: str, spell_id: str, *, spell_list: str = "magic_user") -> MagicItemInstance:
    instance = MagicItemInstance(
        instance_id=f"magic-item-{9100 + len(game.member(pf.named(game, name)).inventory.items)}",
        template_id="spell_scroll_1",
        identified=True,
        state={"spell_list": spell_list, "spells": (spell_id,)},
    )
    game.member(pf.named(game, name)).inventory.items.append(instance)
    return instance


def staff_of_healing(game: GameSession, name: str) -> MagicItemInstance:
    member_id = pf.named(game, name)
    accepted(game.execute(GrantItem(character_id=member_id, item_id="staff_of_healing")))
    return next(
        item
        for item in game.member(member_id).inventory.items
        if isinstance(item, MagicItemInstance) and item.template_id == "staff_of_healing"
    )


def encounter() -> tuple[GameSession, str]:
    """Two goblins in an encounter the party hasn't engaged, with a torch lit, and the first goblin invisible."""
    game = pf.battle(ROSTER, engage=False, light=True)
    goblin = game.encounter.groups[0].monster_ids[0]
    pf.attach(game, INVISIBLE, goblin)
    return game, goblin


# ---------------------------------------------------------------------- the issue's repro


@pytest.mark.parametrize("field", ["target_id", "targets"])
def test_a_scroll_read_in_an_encounter_refuses_an_invisible_goblin(field):
    game, goblin = encounter()
    instance = scroll(game, "Wren", "magic_missile")
    named = {"target_id": goblin} if field == "target_id" else {"targets": (goblin,)}
    use = UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, **named)

    assert refusal(game, use) == [("magic.cast.invisible_target", {"target": goblin})]


def test_a_scroll_read_in_an_encounter_still_takes_a_visible_goblin():
    game, _ = encounter()
    visible = game.encounter.groups[0].monster_ids[1]
    instance = scroll(game, "Wren", "magic_missile")

    accepted(
        game.execute(UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, target_id=visible))
    )


def test_the_battle_refuses_the_same_goblin():
    """The rule the out-of-battle paths now share."""
    game, goblin = encounter()
    wren = pf.named(game, "Wren")
    instance = scroll(game, "Wren", "magic_missile")
    accepted(game.execute(EngageBattle()))
    declaration = pf.declare(wren, "use_item", item_id=instance.instance_id, targets=(goblin,))

    assert pf.refused(game, declaration) == [
        ("battle.declaration.invisible_target", {"target": goblin, "character": wren})
    ]


# ---------------------------------------------------------------------- party members


def wounded_and_invisible(game: GameSession, name: str) -> str:
    """Take 10 hit points from the member and make them invisible, and return their id."""
    member = game.member(pf.named(game, name))
    member.current_hp = member.max_hp - 10
    pf.attach(game, INVISIBLE, member.id)
    return member.id


@pytest.mark.parametrize("name", ["Hild", "Tam"], ids=["another_member", "the_caster"])
def test_cast_spell_heals_an_invisible_member(name):
    """An ally cooperates with the caster, so outside a battle an invisible member, the caster included, is a target."""
    game = pf.session(0, ROSTER, spells={"Tam": ("cure_light_wounds",)})
    member_id = wounded_and_invisible(game, name)
    cast = CastSpell(
        character_id=pf.named(game, "Tam"), spell_id="cure_light_wounds", mode="heal", targets=(member_id,)
    )

    accepted(game.execute(cast))
    member = game.member(member_id)
    assert member.current_hp > member.max_hp - 10
    # The OSE SRD's invisibility: "If the subject attacks or casts a spell, the invisibility is broken".
    assert has_condition(member, Condition.INVISIBLE) is (name != "Tam")


def test_a_scroll_read_heals_an_invisible_member():
    game = pf.session(0, ROSTER, light=True)
    hild = wounded_and_invisible(game, "Hild")
    instance = scroll(game, "Tam", "cure_light_wounds", spell_list="cleric")
    use = UseItem(character_id=pf.named(game, "Tam"), item_id=instance.instance_id, mode="heal", target_id=hild)

    accepted(game.execute(use))
    assert game.member(hild).current_hp > game.member(hild).max_hp - 10


def test_a_scroll_read_in_an_encounter_refuses_an_invisible_npc_adventurer():
    game = pf.battle(ROSTER, engage=False, light=True, npc_party=True)
    adventurer = game.encounter.groups[0].monster_ids[0]
    pf.attach(game, INVISIBLE, adventurer)
    instance = scroll(game, "Wren", "magic_missile")
    use = UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, target_id=adventurer)

    assert refusal(game, use) == [("magic.cast.invisible_target", {"target": adventurer})]


@pytest.mark.parametrize("name", ["Hild", "Tam"], ids=["another_member", "the_caster"])
def test_a_battle_cast_still_refuses_an_invisible_member(name):
    """In a battle, an invisible combatant can't be targeted, a member or the caster named by id included."""
    game = pf.battle(ROSTER, spells={"Tam": ("cure_light_wounds",)})
    member_id = pf.named(game, name)
    tam = pf.named(game, "Tam")
    pf.attach(game, INVISIBLE, member_id)
    declaration = pf.declare(tam, "cast", spell_id="cure_light_wounds", spell_mode="heal", targets=(member_id,))

    assert pf.refused(game, declaration) == [
        ("battle.declaration.invisible_target", {"target": member_id, "character": tam})
    ]


def test_an_unknown_id_is_refused_before_visibility():
    game, _ = encounter()
    instance = scroll(game, "Wren", "magic_missile")
    use = UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, target_id="monster-9999")

    assert refusal(game, use) == [("magic.cast.unknown_target", {"target": "monster-9999"})]


# ---------------------------------------------------------------------- staff of healing


def test_the_staff_refuses_an_invisible_goblin():
    game, goblin = encounter()
    staff = staff_of_healing(game, "Tam")
    use = UseItem(character_id=pf.named(game, "Tam"), item_id=staff.instance_id, target_id=goblin)

    assert refusal(game, use) == [("items.use.invisible_target", {"target": goblin})]


def test_the_staff_heals_an_invisible_member():
    game = pf.session(0, ROSTER)
    hild = wounded_and_invisible(game, "Hild")
    staff = staff_of_healing(game, "Tam")
    use = UseItem(character_id=pf.named(game, "Tam"), item_id=staff.instance_id, target_id=hild)

    accepted(game.execute(use))
    assert game.member(hild).current_hp > game.member(hild).max_hp - 10


@pytest.mark.parametrize("by_id", [False, True], ids=["left_out", "by_id"])
def test_an_invisible_user_still_heals_themself_with_the_staff(by_id):
    game = pf.session(0, ROSTER)
    tam = pf.named(game, "Tam")
    pf.attach(game, INVISIBLE, tam)
    staff = staff_of_healing(game, "Tam")
    use = UseItem(character_id=tam, item_id=staff.instance_id, target_id=tam if by_id else None)

    accepted(game.execute(use))
    assert has_condition(game.member(tam), Condition.INVISIBLE)


def test_a_battle_staff_touch_refuses_an_invisible_member():
    """New in this change: a battle refuses any invisible target, so the staff's touch on another member too."""
    game = pf.battle(ROSTER)
    hild, tam = pf.named(game, "Hild"), pf.named(game, "Tam")
    pf.attach(game, INVISIBLE, hild)
    staff = staff_of_healing(game, "Tam")
    declaration = pf.declare(tam, "use_item", item_id=staff.instance_id, targets=(hild,))

    assert pf.refused(game, declaration) == [("items.use.invisible_target", {"target": hild, "character": tam})]


# ---------------------------------------------------------------------- dead and petrified creatures


def test_the_staff_gives_a_dead_invisible_goblin_the_answer_a_dead_goblin_gets():
    """The view lists no invisible condition for the dead goblin, so the refusal can't name one."""
    game, goblin = encounter()
    kill(game.combatant(goblin))
    group = game.view(Visibility.PLAYER).encounter.groups[0]
    assert "invisible" not in group.visible_conditions, "precondition: the view shows no invisible goblin"
    staff = staff_of_healing(game, "Tam")
    use = UseItem(character_id=pf.named(game, "Tam"), item_id=staff.instance_id, target_id=goblin)

    assert refusal(game, use) == [("items.use.target_cannot_be_healed", {"item": staff.instance_id, "target": goblin})]


def test_a_battle_staff_touch_gives_a_dead_invisible_member_the_answer_a_dead_member_gets():
    game = pf.battle(ROSTER)
    hild, tam = pf.named(game, "Hild"), pf.named(game, "Tam")
    pf.attach(game, INVISIBLE, hild)
    kill(game.member(hild))
    staff = staff_of_healing(game, "Tam")
    declaration = pf.declare(tam, "use_item", item_id=staff.instance_id, targets=(hild,))

    assert pf.refused(game, declaration) == [
        ("items.use.target_cannot_be_healed", {"item": staff.instance_id, "target": hild, "character": tam})
    ]


def test_a_battle_staff_touch_gives_a_dead_invisible_goblin_the_answer_a_dead_goblin_gets():
    game = pf.battle(ROSTER)
    goblin = game.encounter.groups[0].monster_ids[0]
    pf.attach(game, INVISIBLE, goblin)
    kill(game.combatant(goblin))
    tam = pf.named(game, "Tam")
    staff = staff_of_healing(game, "Tam")
    declaration = pf.declare(tam, "use_item", item_id=staff.instance_id, targets=(goblin,))

    assert pf.refused(game, declaration) == [
        ("items.use.target_cannot_be_healed", {"item": staff.instance_id, "target": goblin, "character": tam})
    ]


def test_a_scroll_read_gives_a_dead_invisible_goblin_the_unknown_answer():
    game, goblin = encounter()
    kill(game.combatant(goblin))
    instance = scroll(game, "Wren", "magic_missile")
    use = UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, target_id=goblin)

    assert refusal(game, use) == [("magic.cast.unknown_target", {"target": goblin})]


def test_a_battle_cast_gives_a_dead_invisible_goblin_the_unknown_answer():
    game = pf.battle(ROSTER, spells={"Wren": ("magic_missile",)})
    goblin = game.encounter.groups[0].monster_ids[0]
    pf.attach(game, INVISIBLE, goblin)
    kill(game.combatant(goblin))
    wren = pf.named(game, "Wren")
    declaration = pf.declare(wren, "cast", spell_id="magic_missile", spell_mode="missiles", targets=(goblin,))

    assert pf.refused(game, declaration) == [("magic.cast.unknown_target", {"target": goblin, "character": wren})]


def test_a_petrified_invisible_goblin_shows_in_the_view_and_is_refused_as_invisible():
    """A petrified goblin is alive, so the view counts its invisibility, and the refusal reveals no more."""
    game, goblin = encounter()
    pf.attach(game, PETRIFIED, goblin)
    group = game.view(Visibility.PLAYER).encounter.groups[0]
    assert group.condition_counts.get("invisible") == 1, "precondition: the view counts the invisible goblin"
    instance = scroll(game, "Wren", "magic_missile")
    use = UseItem(character_id=pf.named(game, "Wren"), item_id=instance.instance_id, target_id=goblin)

    assert refusal(game, use) == [("magic.cast.invisible_target", {"target": goblin})]
