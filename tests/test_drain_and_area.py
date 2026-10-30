"""Two rules gaps: rules that still read a drained monster's full Hit Dice, and *death spell*'s 60' cube.

**Hit Dice after a drain.** The OSE SRD's wight page says a target of energy drain "permanently loses one experience
level (or Hit Die)" and with it "all other benefits due to the drained level (e.g. spells, saving throws, etc.)".
osrlib already judges a drained monster by the Hit Dice it has left for its THAC0, its saves, and *sleep*. Three more
rules measure a creature's power by its Hit Dice, and each one should read the Hit Dice the monster has left:

- *Confusion*'s re-save. The spell page reads "Subjects of 2+1 HD or greater: May save versus spells each round" and
  "Subjects of 2 HD or lower: May not make a saving throw." A 3 HD boar drained to 2 HD is a subject of 2 HD.
- The protection scrolls' Hit Dice bands. The scroll page reads "A number of undead monsters are barred from entering
  the circle of protection. The number depends on their Hit Dice: 1–3 HD: 2d12 undead affected, 4–5 HD: 2d6 undead
  affected, 6+ HD: 1d6 undead affected." A 4 HD wraith drained to 3 HD counts in the 1–3 band.
- The invulnerabilities optional rule. The combat page lets a monster that only magic or silver can harm also be
  harmed by "A monster with 5 HD or greater." A 5 HD grizzly drained to 4 HD no longer qualifies.

One read stays with the template, and the guards below pin it. The cleric's turning table compares the roll "against
the HD of the type of undead monsters targeted", and its header reads "Hit Dice of Monster Type". The column belongs to
the kind of monster, and osrlib rolls once per kind, so a drained wight still turns on the wight's column.

**A group-aimed *death spell*.** A `cast` in an `hd_budget` mode aimed at a group with `target_group_id` takes the
group's living members as candidates. *Sleep*'s page gives the spell a range and a Hit Dice budget but no area, so it
takes every living member. *Death spell*'s page says "Up to 4d8 Hit Dice of creatures within a 60' cube area", so a
group-aimed *death spell* takes exactly the candidates its area would take, under the area footprint rule: the
group's living members in spawn order up to the footprint's capacity, then the engaged front rank under
`aoe_friendly_fire`. Named `targets` still win when both are set.
"""

from crawl_fixtures import build_adventure
from osrlib.core.abilities import AbilityScore
from osrlib.core.character import ADVANCEMENT_STREAM, Character
from osrlib.core.clock import GameClock, TimeUnit
from osrlib.core.combat import AttackContext, check_immunity, damage_source_for, drain_monster_hd
from osrlib.core.effects import Condition, EffectDefinition, EffectsLedger, has_condition
from osrlib.core.events import Visibility
from osrlib.core.monsters import MONSTER_SPAWN_STREAM, IdAllocator, MonsterInstance, spawn_monster
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MAGIC_STREAM, MemorizedSpell, turn_undead
from osrlib.core.tables import ReactionResult
from osrlib.crawl import battle as battle_module
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.battle import MELEE_RANGE_FEET, ScriptedPolicy
from osrlib.crawl.commands import (
    BattleDeclaration,
    CommandResult,
    EngageBattle,
    EnterDungeon,
    GrantItem,
    LightSource,
    ResolveBattleRound,
    SessionMode,
)
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_monsters, load_spells

FIGHTER = "character-0001"
SECOND_FIGHTER = "character-0002"
MAGE = "character-0003"

# ---------------------------------------------------------------------- shared helpers


def adventurer(name: str, class_id: str, level: int, hp: int, **fields) -> Character:
    definition = load_classes().get(class_id)
    return Character(
        name=name,
        class_id=class_id,
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores={ability: 11 for ability in AbilityScore},
        alignment="lawful",
        max_hp=hp,
        current_hp=hp,
        **fields,
    )


def drain(monster: MonsterInstance, levels: int = 1) -> MonsterInstance:
    """Drain Hit Dice from a monster as a wight's touch does, and check that it worked.

    The drain draws from a stream of its own, so the session's streams are untouched.
    """
    before = monster.hit_dice_count
    events = drain_monster_hd(monster, levels=levels, stream=RngStreams(master_seed=99).get(ADVANCEMENT_STREAM))
    assert events[0].code == "combat.drain.drained"
    assert monster.drained_hd == levels and monster.hit_dice_count == before - levels
    assert not has_condition(monster, Condition.DEAD)
    return monster


def battle(
    template_id: str,
    count: int,
    *,
    distance: int,
    seed: int = 5,
    ruleset: Ruleset | None = None,
) -> GameSession:
    """A battle in the test delve's entrance corridor against one group.

    The party is two 3rd-level fighters, who make up the front rank, and a 12th-level magic-user behind them with
    *sleep* and *death spell* memorized. The corridor is one cell wide, which holds two abreast.
    """
    party = Party(
        members=[
            adventurer("Brakk", "fighter", 3, 20),
            adventurer("Hild", "fighter", 3, 20),
            adventurer(
                "Elara",
                "magic_user",
                12,
                30,
                spell_book=("sleep", "death_spell"),
                memorized_spells=(MemorizedSpell(spell_id="sleep"), MemorizedSpell(spell_id="death_spell")),
            ),
        ]
    )
    session = GameSession.new(party, build_adventure(wandering_chance=0), seed=seed, ruleset=ruleset)
    assert [member.id for member in session.party.members] == [FIGHTER, SECOND_FIGHTER, MAGE]
    assert session.execute(GrantItem(character_id=FIGHTER, item_id="torch", quantity=6)).accepted
    assert session.execute(GrantItem(character_id=FIGHTER, item_id="tinder_box")).accepted
    assert session.execute(EnterDungeon(dungeon_id="delve")).accepted
    for _ in range(20):
        lit = session.execute(LightSource(character_id=FIGHTER, item_id="torch"))
        if any(event.code == "exploration.light.lit" for event in lit.events):
            break
    assert session.party_light()[0], "precondition: the party carries a lit torch"
    instances = session.spawn(template_id, count)
    encounter_module.start_encounter(
        session,
        groups=[(template_id, instances)],
        kind="spawned",
        distance_feet=distance,
        pinned_stance=ReactionResult.HOSTILE,
        party_aware=True,
    )
    if session.mode is SessionMode.ENCOUNTER:
        assert session.execute(EngageBattle()).accepted
    assert session.mode is SessionMode.BATTLE, "precondition: the encounter became a battle"
    assert len(session.encounter.groups) == 1 and len(session.encounter.groups[0].monster_ids) == count
    return session


def round_with(session: GameSession, *declarations: BattleDeclaration) -> CommandResult:
    """Resolve one battle round: the given declarations, and a `hold` for every other member who can declare."""
    from osrlib.core.combat import incapacitated

    covered = {declaration.character_id for declaration in declarations}
    holds = [
        BattleDeclaration(character_id=member.id, action="hold")
        for member in session.party.living_members()
        if member.id not in covered and not incapacitated(member)
    ]
    return session.execute(ResolveBattleRound(declarations=(*declarations, *holds)))


def living_ids(session: GameSession) -> tuple[str, ...]:
    group = session.encounter.groups[0]
    return tuple(
        monster_id
        for monster_id in group.monster_ids
        if not has_condition(session.combatant(monster_id), Condition.DEAD)
    )


# ---------------------------------------------------------------------- confusion's re-save


def confuse(session: GameSession, monster: MonsterInstance) -> None:
    """Put *confusion* on a monster with the spell's own params, as a casting would."""
    params = load_spells().get("confusion").modes[0].effect.params
    assert params["resave_hd_min_count"] == 3 and params["resave_at_hd_count_2_with_bonus"] is True
    confusion = EffectDefinition(
        kind="confusion",
        condition=Condition.CONFUSED,
        duration_unit=TimeUnit.ROUND,
        duration_amount=12,
        params=dict(params),
    )
    effect, _ = session.ledger.attach(
        confusion, monster.id, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    assert effect is not None and has_condition(monster, Condition.CONFUSED)


def confusion_resaves(template_id: str, drained: int) -> list:
    """Confuse one monster, drained of `drained` Hit Dice, and return its re-saves from one battle round."""
    session = battle(template_id, 1, distance=60, seed=9)
    monster = session.monsters[session.encounter.groups[0].monster_ids[0]]
    if drained:
        drain(monster, drained)
    confuse(session, monster)
    result = round_with(session)
    assert result.accepted, [rejection.code for rejection in result.rejections]
    return [
        event
        for event in result.events
        if getattr(event, "category", None) == "spells" and getattr(event, "target_id", None) == monster.id
    ]


class TestConfusionResavesByTheHitDiceLeft:
    """*Confusion*: "Subjects of 2+1 HD or greater: May save versus spells each round". "Subjects of 2 HD or lower: May
    not make a saving throw." A drained subject is judged by the Hit Dice it has left, with its template's modifier."""

    def test_an_undrained_3_hd_boar_resaves(self):
        boar = load_monsters().get("boar").hit_dice
        assert (boar.count, boar.modifier) == (3, 0)
        assert len(confusion_resaves("boar", 0)) == 1

    def test_a_boar_drained_to_2_hd_makes_no_resave(self):
        assert confusion_resaves("boar", 1) == []

    def test_an_undrained_2_plus_1_lizard_man_resaves(self):
        lizard = load_monsters().get("lizard_man").hit_dice
        assert (lizard.count, lizard.modifier) == (2, 1)
        assert len(confusion_resaves("lizard_man", 0)) == 1

    def test_a_lizard_man_drained_to_1_plus_1_makes_no_resave(self):
        assert confusion_resaves("lizard_man", 1) == []

    def test_an_ogre_drained_to_2_plus_1_still_resaves(self):
        # The template's +1 stays after a drain, so the ogre is a 2+1 subject.
        ogre = load_monsters().get("ogre").hit_dice
        assert (ogre.count, ogre.modifier) == (4, 1)
        assert len(confusion_resaves("ogre", 2)) == 1


# ---------------------------------------------------------------------- the protection scrolls' bands


def ward_scene(*, affected_1_3: int, affected_4_5: int, drained_first: bool) -> tuple[GameSession, list]:
    """Two wraiths at melee range and a *protection from undead* ward on the party with fixed band counts.

    Returns:
        The session and the two wraiths in spawn order. The first is drained to 3 HD when `drained_first` is set.
    """
    session = battle("wraith", 2, distance=MELEE_RANGE_FEET, seed=5)
    group = session.encounter.groups[0]
    assert group.distance_feet <= MELEE_RANGE_FEET
    wraiths = [session.monsters[monster_id] for monster_id in group.monster_ids]
    assert all(wraith.hit_dice_count == 4 for wraith in wraiths)
    assert all("undead" in wraith.template.categories for wraith in wraiths)
    if drained_first:
        drain(wraiths[0])
        assert wraiths[0].hit_dice_count == 3
    ward = EffectDefinition(
        kind="protection_ward",
        duration_unit=TimeUnit.TURN,
        duration_amount=6,
        params={
            "party_wide": True,
            "bars_categories": ("undead",),
            "targets": "undead",
            "affected_1_3": affected_1_3,
            "affected_4_5": affected_4_5,
            "affected_6_plus": 0,
        },
    )
    effect, _ = session.ledger.attach(
        ward, FIGHTER, clock=session.clock, allocator=session.allocator, registry=session.registry()
    )
    assert effect is not None
    return session, wraiths


def chosen_kinds(session: GameSession) -> dict[str, str]:
    """What the default monster policy has each wraith do this round. A barred wraith holds, the other melees."""
    actions = ScriptedPolicy().choose(session, session.encounter.groups[0], RngStreams(master_seed=1).get("probe"))
    return {action.monster_id: action.kind for action in actions}


class TestProtectionWardBandsByTheHitDiceLeft:
    """The scroll page: "The number depends on their Hit Dice: 1–3 HD: 2d12 undead affected, 4–5 HD: 2d6 undead
    affected, 6+ HD: 1d6 undead affected." A drained monster counts in the band of the Hit Dice it has left."""

    def test_an_undrained_pair_shares_the_4_to_5_band(self):
        session, (first, second) = ward_scene(affected_1_3=0, affected_4_5=1, drained_first=False)
        assert chosen_kinds(session) == {first.id: "hold", second.id: "melee"}

    def test_a_wraith_drained_to_3_hd_counts_in_the_1_to_3_band(self):
        session, (drained, whole) = ward_scene(affected_1_3=1, affected_4_5=0, drained_first=True)
        assert chosen_kinds(session) == {drained.id: "hold", whole.id: "melee"}

    def test_a_wraith_drained_to_3_hd_leaves_the_4_to_5_band(self):
        # With one wraith barred in the 4-5 band, the barred one is the whole wraith, not the first in spawn order.
        session, (drained, whole) = ward_scene(affected_1_3=0, affected_4_5=1, drained_first=True)
        assert chosen_kinds(session) == {drained.id: "melee", whole.id: "hold"}


# ---------------------------------------------------------------------- 5 HD or greater counts as magical


def kernel_monster(template_id: str, *, drained: int = 0) -> MonsterInstance:
    instance = spawn_monster(
        load_monsters().get(template_id),
        id=f"{template_id}-1",
        stream=RngStreams(master_seed=3).get(MONSTER_SPAWN_STREAM),
    )
    return drain(instance, drained) if drained else instance


def harms_the_wight(attacker: MonsterInstance) -> bool:
    """Whether the attacker's first attack gets past a wight's silver-or-magic gate under the optional rule."""
    wight = kernel_monster("wight")
    assert set(wight.template.defenses.harmed_only_by) == {"silver", "magic"}
    attack = attacker.template.attacks[0].attacks[0]
    source = damage_source_for(attacker, attack, AttackContext())
    assert check_immunity(wight, source, ruleset=Ruleset(), attacker=attacker), "precondition: the gate holds"
    return not check_immunity(wight, source, ruleset=Ruleset(hd5_counts_as_magical=True), attacker=attacker)


class TestFiveHitDiceCountsByTheHitDiceLeft:
    """The invulnerabilities optional rule: such a monster may "also be harmed by attacks from ... A monster with 5 HD
    or greater." A drained monster qualifies by the Hit Dice it has left."""

    def test_an_undrained_5_hd_grizzly_harms_a_wight(self):
        grizzly = kernel_monster("grizzly_bear")
        assert grizzly.hit_dice_count == 5 and not grizzly.template.defenses.harmed_only_by
        assert harms_the_wight(grizzly)

    def test_a_grizzly_drained_to_4_hd_cannot_harm_a_wight(self):
        grizzly = kernel_monster("grizzly_bear", drained=1)
        assert grizzly.hit_dice_count == 4
        assert not harms_the_wight(grizzly)

    def test_a_troll_drained_to_5_hd_still_harms_a_wight(self):
        troll = kernel_monster("troll", drained=1)
        assert troll.hit_dice_count == 5
        assert harms_the_wight(troll)

    def test_a_drained_wraith_still_harms_a_wight_as_another_invulnerable_monster(self):
        # The rule's other bullet, "Another invulnerable monster", reads the monster's defenses, not its Hit Dice.
        wraith = kernel_monster("wraith", drained=1)
        assert wraith.hit_dice_count == 3
        assert harms_the_wight(wraith)


# ---------------------------------------------------------------------- the turning column stays with the type


class TestTheTurningColumnStaysWithTheType:
    """The cleric page compares the turning roll "against the HD of the type of undead monsters targeted", in a table
    headed "Hit Dice of Monster Type". The column belongs to the kind of monster, so a drained wight keeps its own."""

    def turn(self, wight: MonsterInstance):
        cleric = adventurer("Wynn", "cleric", 3, 14)
        streams = RngStreams(master_seed=12)
        return turn_undead(
            cleric,
            load_classes().get("cleric"),
            [wight],
            ledger=EffectsLedger(),
            clock=GameClock(),
            allocator=IdAllocator(),
            registry={wight.id: wight},
            stream=streams.get(MAGIC_STREAM),
        )

    def test_an_undrained_wight_turns_on_column_3(self):
        wight = kernel_monster("wight")
        assert (wight.template.hit_dice.count, wight.template.hit_dice.asterisks) == (3, 1)
        assert [outcome.column for outcome in self.turn(wight).outcomes] == ["3"]

    def test_a_wight_drained_to_2_hd_still_turns_on_column_3(self):
        wight = kernel_monster("wight", drained=1)
        assert wight.hit_dice_count == 2  # the Hit Dice left would read as column 2*
        assert [outcome.column for outcome in self.turn(wight).outcomes] == ["3"]


# ---------------------------------------------------------------------- an hd_budget spell with an area


CORRIDOR_WIDTH = 2
"""The test delve's entrance corridor is one cell wide, and the formation width rule fits two abreast in it."""

CUBE_CAPACITY = 12
"""*Death spell*'s 60' cube in that corridor holds ceil(60 / 10) × 2 = 12 creatures, under the area footprint rule."""

GOBLINS = 16
"""More goblins than the cube holds."""

DEATH_SEED = 2
"""A master seed whose *death spell* budget, cast at the sixteen goblins, pays for more than the cube holds."""

SLEEP_SEED = 4
"""A master seed whose *sleep* budget, cast at the sixteen goblins, pays for more than a 60' cube would hold."""


def death_spell(*, group_id: str | None = None, targets: tuple[str, ...] = ()) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=MAGE,
        action="cast",
        spell_id="death_spell",
        spell_mode="kill",
        target_group_id=group_id,
        targets=targets,
    )


def sleep(*, group_id: str | None = None, targets: tuple[str, ...] = ()) -> BattleDeclaration:
    return BattleDeclaration(
        character_id=MAGE,
        action="cast",
        spell_id="sleep",
        spell_mode="hd_budget",
        target_group_id=group_id,
        targets=targets,
    )


def selected(result: CommandResult) -> tuple[str, ...]:
    assert result.accepted, [(rejection.code, dict(rejection.params)) for rejection in result.rejections]
    selections = [
        event for event in result.events if event.code == "combat.targeting.selected" and event.mode == "hd_budget"
    ]
    assert len(selections) == 1, "the cast selected its targets once, by Hit Dice budget"
    return selections[0].target_ids


def fingerprint(session: GameSession, result: CommandResult) -> dict:
    """What a resolved round did and left behind, for comparing two runs."""
    return {
        "accepted": result.accepted,
        "events": [event.model_dump(mode="json") for event in result.events],
        "streams": session.streams.export_states(),
        "view": session.view(Visibility.PLAYER).model_dump(mode="json"),
    }


def goblins_in_the_corridor(seed: int, distance: int = 60, ruleset: Ruleset | None = None) -> GameSession:
    session = battle("goblin", GOBLINS, distance=distance, seed=seed, ruleset=ruleset)
    assert battle_module._formation_width(session) == CORRIDOR_WIDTH, "precondition: the party is in the corridor"
    return session


class TestAnHdBudgetSpellWithAnAreaAimedAtAGroup:
    """*Death spell*: "Up to 4d8 Hit Dice of creatures within a 60' cube area". Aimed at a group, it takes the
    candidates its area would take. *Sleep* has no area and still takes every living member."""

    def test_named_targets_pay_for_more_than_the_cube_holds(self):
        # The calibration for the next test: the seed's budget reaches past the twelfth goblin.
        session = goblins_in_the_corridor(DEATH_SEED)
        assert len(selected(round_with(session, death_spell(targets=living_ids(session))))) > CUBE_CAPACITY

    def test_a_death_spell_aimed_at_a_group_takes_only_what_its_cube_holds(self):
        by_group = goblins_in_the_corridor(DEATH_SEED)
        cube = living_ids(by_group)[:CUBE_CAPACITY]
        result = round_with(by_group, death_spell(group_id=by_group.encounter.groups[0].id))
        assert set(selected(result)) <= set(cube)

    def test_aiming_a_death_spell_at_a_group_is_naming_what_its_cube_holds(self):
        by_group = goblins_in_the_corridor(DEATH_SEED)
        by_name = goblins_in_the_corridor(DEATH_SEED)
        cube = living_ids(by_name)[:CUBE_CAPACITY]
        aimed = round_with(by_group, death_spell(group_id=by_group.encounter.groups[0].id))
        named = round_with(by_name, death_spell(targets=cube))
        assert selected(named) == cube, "precondition: the budget pays for the whole cube"
        assert fingerprint(by_group, aimed) == fingerprint(by_name, named)

    def test_a_death_spell_aimed_at_a_group_in_melee_catches_the_front_rank(self):
        # Under `aoe_friendly_fire`, an area that lands on a group at melee range takes the engaged front rank after
        # the monsters, as a *fire ball* does.
        by_group = battle("goblin", 2, distance=MELEE_RANGE_FEET, seed=1)
        by_name = battle("goblin", 2, distance=MELEE_RANGE_FEET, seed=1)
        assert by_group.ruleset.aoe_friendly_fire
        area = (*living_ids(by_name), FIGHTER, SECOND_FIGHTER)
        aimed = round_with(by_group, death_spell(group_id=by_group.encounter.groups[0].id))
        named = round_with(by_name, death_spell(targets=area))
        assert set(selected(named)) == set(area), "precondition: the budget pays for the goblins and the front rank"
        assert fingerprint(by_group, aimed) == fingerprint(by_name, named)

    def test_without_friendly_fire_a_death_spell_in_melee_takes_only_the_group(self):
        session = battle("goblin", 2, distance=MELEE_RANGE_FEET, seed=1, ruleset=Ruleset(aoe_friendly_fire=False))
        goblins = living_ids(session)
        result = round_with(session, death_spell(group_id=session.encounter.groups[0].id))
        assert set(selected(result)) <= set(goblins)

    def test_named_targets_beside_a_group_id_are_not_capped(self):
        session = goblins_in_the_corridor(DEATH_SEED)
        result = round_with(session, death_spell(group_id=session.encounter.groups[0].id, targets=living_ids(session)))
        assert len(selected(result)) > CUBE_CAPACITY

    def test_a_sleep_aimed_at_a_group_takes_more_than_a_cube_would_hold(self):
        session = goblins_in_the_corridor(SLEEP_SEED)
        assert load_spells().get("sleep").mode("hd_budget").targeting.dimensions == {}
        assert len(selected(round_with(session, sleep(group_id=session.encounter.groups[0].id)))) > CUBE_CAPACITY

    def test_aiming_a_sleep_at_a_group_is_still_naming_every_living_member(self):
        by_group = goblins_in_the_corridor(SLEEP_SEED)
        by_name = goblins_in_the_corridor(SLEEP_SEED)
        aimed = round_with(by_group, sleep(group_id=by_group.encounter.groups[0].id))
        named = round_with(by_name, sleep(targets=living_ids(by_name)))
        assert fingerprint(by_group, aimed) == fingerprint(by_name, named)
