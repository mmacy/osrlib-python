"""Acceptance tests for four monster behaviours a playtest found wrong: a cobra's spit, ghouls, statues, and surprise.

**The spitting cobra's spit.** The OSE SRD's snake page says "Blinding spit: Range: 6'. A hit causes permanent
blindness (save vs poison)." The spit's attack lists the `blindness` effect, and the cobra's ability for it was
`manual`, so a spit hit did nothing. The cobra's ability is now tagged `blindness` and isn't `manual`, and battle
applies it as #180 applies the other attack effects: a spit hit makes the target save versus death, the category for
poison, or be blinded for good. The SRD's combat rules say "A blind character is unable to attack."

**Ghouls and thouls attack others.** The ghoul's and the thoul's entries say "After paralysing a target, ghouls will
attack others", and the same of thouls. Their `paralysis` ability carries the param `attacks_others`, and the default
policy, `ScriptedPolicy`, reads that param, not the monster's id. A monster with it picks a target that isn't
paralysed while one is in reach, and picks a paralysed member only when no other target is left. The policy picks a
target once a round, so the attacks left in the round's routine after a paralysing hit stay on that target.

**Statues.** A member turned to stone is out of the fight. This is a judgement, not SRD text, and the adaptations
register records it. Under `ScriptedPolicy`, no monster picks a petrified member while another target is in reach. A
paralysed member comes before a statue, even for a ghoul. When every member in reach is stone, the monster attacks a
statue, exactly as it did before. It doesn't hold, because a party that is all stone declares nothing and could never
end the battle. It doesn't reach past the statues, because melee reaches only the front rank, in both directions.

**The surprise gaze.** The basilisk's entry says "Characters surprised by a basilisk meet its gaze." #196 ran that
gaze only in the monsters' free round, which comes only when the encounter goes straight to battle. Now every living
member of a party a basilisk surprises meets its gaze once, as the encounter opens, whatever the stance and whatever
the encounter does next. That holds when the basilisk is surprised too, because the clause is about the characters
and the gaze takes no action. A battle that opens later, in a free round or not, brings no second surprise gaze.

**Determinism.** A monster whose candidates include no paralysed or petrified member draws exactly as before, and so
does one whose candidates are all statues, so no golden moves. Saving throws are forced where a test needs a
particular outcome: a `save_bonus` of +20 makes every save pass and one of -20 makes every save fail. Each test
asserts its own preconditions, so a test that stops exercising its case fails loudly.
"""

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character, party_to_document
from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, EffectDefinition, ModifierSpec, has_condition
from osrlib.core.events import AttackRolledEvent, SavingThrowRolledEvent
from osrlib.core.rng import RngStream
from osrlib.core.ruleset import Ruleset
from osrlib.core.tables import ReactionResult
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.battle import MonsterAction, ScriptedPolicy
from osrlib.crawl.commands import (
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import MONSTER_ACTION_STREAM, GameSession
from osrlib.data import load_classes, load_monsters
from osrlib.persistence import replay_game, session_state

CHUNK = pytest.mark.xfail(reason="chunk: monster-tactics", strict=True)

HIT_CODES = frozenset({"combat.attack.hit", "combat.attack.auto_hit"})

ALWAYS_PASS = 20
"""A save bonus no d20 can fail with."""

ALWAYS_FAIL = -20
"""A save penalty no d20 can pass with."""

SEEDS = range(1, 80)
"""Seeds the helpers scan for a setup they need, like an encounter neither side is surprised in."""

POLICY_CALLS = 40
"""Policy calls in a run: with two candidates, one of them goes unpicked by chance once in about 10^12 runs."""


# ---------------------------------------------------------------------- setup


def _character(name: str, class_id: str = "fighter", *, level: int = 3, hp: int = 400) -> Character:
    """A character with average scores and enough hit points to outlast a long fight."""
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


def _members(*names: str) -> list[Character]:
    return [_character(name) for name in names]


def _adventure() -> Adventure:
    """One level: a 10' corridor two cells long, so the party's front rank is two abreast."""
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    return Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))


def _session(seed: int, members: list[Character]) -> GameSession:
    game = GameSession.new(Party(members=members), _adventure(), seed=seed)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def _attach(game: GameSession, member: Character, definition: EffectDefinition) -> None:
    effect, _ = game.ledger.attach(
        definition, member.id, clock=game.clock, allocator=game.allocator, registry=game.registry()
    )
    assert effect is not None


def _force_saves(game: GameSession, member: Character, bonus: int) -> None:
    """Make every save the member rolls pass (+20) or fail (-20)."""
    _attach(
        game, member, EffectDefinition(kind="test_save_ward", modifiers=(ModifierSpec(kind="save_bonus", value=bonus),))
    )


def _paralyse(game: GameSession, member: Character) -> None:
    _attach(game, member, EffectDefinition(kind="test_paralysis", condition=Condition.PARALYSED, permanent=True))
    assert has_condition(member, Condition.PARALYSED)


def _petrify(game: GameSession, member: Character) -> None:
    _attach(game, member, EffectDefinition(kind="test_petrification", condition=Condition.PETRIFIED, permanent=True))
    assert has_condition(member, Condition.PETRIFIED)


def _make_invisible(game: GameSession, member: Character) -> None:
    _attach(game, member, EffectDefinition(kind="test_invisibility", condition=Condition.INVISIBLE, permanent=True))
    assert has_condition(member, Condition.INVISIBLE)


def _battle(template_id: str, names: tuple[str, ...] = ("Hild", "Brakk"), *, distance_feet: int = 5) -> GameSession:
    """A battle against one monster that neither side was surprised in, built by command, before its first round."""
    for seed in SEEDS:
        game = _session(seed, _members(*names))
        result = game.execute(SpawnMonsters(template_id=template_id, count_fixed=1, distance_feet=distance_feet))
        assert result.accepted
        surprise = [event for event in result.events if event.code == "encounter.surprise.rolled"]
        if not surprise or any(event.roll is not None and event.roll <= event.threshold for event in surprise):
            continue
        if game.mode is SessionMode.ENCOUNTER:
            assert game.execute(EngageBattle()).accepted
        if game.mode is not SessionMode.BATTLE or game.battle.round != 0:
            continue
        assert game.encounter.groups[0].distance_feet == distance_feet
        assert game.monsters[_monster_id(game)].template.id == template_id
        return game
    raise AssertionError(f"precondition: no seed opened a {template_id} battle with neither side surprised")


def _monster_id(game: GameSession) -> str:
    return game.encounter.groups[0].monster_ids[0]


def _group_id(game: GameSession) -> str:
    return game.encounter.groups[0].id


def _front_rank_ids(game: GameSession) -> list[str]:
    return [member.id for member in game.party.ranks(2)[0]]


def _choose(game: GameSession) -> list[MonsterAction]:
    """Call the default policy for the battle's only group, as the session does."""
    return ScriptedPolicy().choose(game, game.encounter.groups[0], game.streams.get(MONSTER_ACTION_STREAM))


def _targets(game: GameSession, calls: int = POLICY_CALLS) -> list[str]:
    """The target of each melee action the default policy chooses over `calls` calls."""
    targets = []
    for _ in range(calls):
        actions = _choose(game)
        assert [action.kind for action in actions] == ["melee"], "precondition: the monster attacks in melee"
        targets.append(actions[0].target_id)
    return targets


def _play(game: GameSession, *declared: BattleDeclaration) -> list:
    """Resolve one round: the members in `declared` as given, every other able member holding."""
    assert game.mode is SessionMode.BATTLE, "precondition: the battle is still running"
    by_id = {declaration.character_id: declaration for declaration in declared}
    able = [member for member in game.party.living_members() if not incapacitated(member)]
    declarations = tuple(
        by_id.get(member.id, BattleDeclaration(character_id=member.id, action="hold")) for member in able
    )
    result = game.execute(ResolveBattleRound(declarations=declarations))
    assert result.accepted, [rejection.code for rejection in result.rejections]
    return list(result.events)


def _rolls(events, attacker_id: str) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id == attacker_id]


def _saves(events, target_id: str, category: str | None = None) -> list[SavingThrowRolledEvent]:
    return [
        event
        for event in events
        if isinstance(event, SavingThrowRolledEvent)
        and event.target_id == target_id
        and (category is None or event.category == category)
    ]


def _state_without_logs(game: GameSession) -> dict:
    """Every state block, the clock and the random number streams among them, without the two logs."""
    state = session_state(game)
    state.pop("command_log")
    state.pop("event_log")
    return state


def _with_paralysis_param(monster, attacks_others: bool | None) -> None:
    """Give a spawned monster a copy of its template whose `paralysis` ability has `attacks_others` set or removed."""
    template = monster.template
    abilities = []
    for ability in template.abilities:
        if ability.tag == "paralysis":
            params = {key: value for key, value in ability.params.items() if key != "attacks_others"}
            if attacks_others is not None:
                params["attacks_others"] = attacks_others
            ability = ability.model_copy(update={"params": params})
        abilities.append(ability)
    assert any(ability.tag == "paralysis" for ability in abilities), "precondition: the monster paralyses"
    monster.template = template.model_copy(update={"abilities": tuple(abilities)})


# ---------------------------------------------------------------------- the spitting cobra


class _SpitEveryRound:
    """The default policy with the cobra's first routine, the spit, named every round."""

    def choose(self, session, group, stream):
        actions = ScriptedPolicy().choose(session, group, stream)
        return [action.model_copy(update={"routine": 0}) if action.kind == "melee" else action for action in actions]


def _cobra_battle(bonus: int | None) -> tuple[GameSession, Character, str]:
    """A one-member battle against a spitting cobra that spits every round."""
    game = _battle("spitting_cobra", ("Hild",))
    hild = game.party.members[0]
    if bonus is not None:
        _force_saves(game, hild, bonus)
    game.action_policies[_group_id(game)] = _SpitEveryRound()
    return game, hild, _monster_id(game)


def _until_spit_hits(game: GameSession, cobra_id: str, *, max_rounds: int = 30) -> list:
    for _ in range(max_rounds):
        events = _play(game)
        rolled = _rolls(events, cobra_id)
        assert [event.attack_name for event in rolled] == ["spit"], "precondition: the cobra spat this round"
        if rolled[0].code in HIT_CODES:
            return events
    pytest.fail(f"precondition: the cobra's spit hit within {max_rounds} rounds")


class TestTheSpittingCobrasSpit:
    """A spit hit makes the target save versus poison or be blinded for good."""

    @CHUNK
    def test_the_catalog_gives_the_spit_an_ability_battle_applies(self):
        cobra = load_monsters().get("spitting_cobra")
        spit = next(attack for routine in cobra.attacks for attack in routine.attacks if attack.name == "spit")
        assert list(spit.effects) == ["blindness"], "precondition: the spit's attack lists the effect"

        blindness = cobra.ability("blindness")
        assert blindness is not None, "the spit's effect has an ability of the same tag, as #180's effects do"
        assert not blindness.manual
        assert "A hit causes permanent blindness (save vs poison)." in blindness.prose
        assert cobra.ability("blinding_spit") is None, "one ability holds the spit, under the effect's tag"

    @CHUNK
    def test_a_failed_save_blinds_for_good(self):
        game, hild, cobra = _cobra_battle(ALWAYS_FAIL)

        events = _until_spit_hits(game, cobra)

        saves = _saves(events, hild.id)
        assert [(save.category, save.code) for save in saves] == [("death", "combat.save.failed")], (
            "save versus poison, which is the death category"
        )
        assert has_condition(hild, Condition.BLIND)
        blinding = [
            effect for effect in game.ledger.active_on(hild.id) if effect.definition.condition is Condition.BLIND
        ]
        assert len(blinding) == 1
        assert blinding[0].expires_round is None, "the blindness is permanent"
        assert not blinding[0].definition.dispellable, "what a monster inflicts isn't dispellable"

        # The blindness outlasts the battle's rounds.
        for _ in range(3):
            _play(game)
        assert has_condition(hild, Condition.BLIND)

    @CHUNK
    def test_a_passed_save_leaves_the_target_seeing(self):
        game, hild, cobra = _cobra_battle(ALWAYS_PASS)

        events = _until_spit_hits(game, cobra)

        saves = _saves(events, hild.id)
        assert [(save.category, save.code) for save in saves] == [("death", "combat.save.passed")]
        assert not has_condition(hild, Condition.BLIND)

    def test_a_spit_that_misses_applies_nothing(self):
        """A guard: a miss rolls no save and blinds no one, before and after the chunk."""
        game, hild, cobra = _cobra_battle(ALWAYS_FAIL)

        for _ in range(30):
            events = _play(game)
            rolled = _rolls(events, cobra)
            assert [event.attack_name for event in rolled] == ["spit"], "precondition: the cobra spat this round"
            if rolled[0].code not in HIT_CODES:
                assert not _saves(events, hild.id)
                assert not has_condition(hild, Condition.BLIND)
                return
        pytest.fail("precondition: the cobra's spit missed within 30 rounds")

    @CHUNK
    def test_a_member_the_spit_blinded_cannot_attack(self):
        """The SRD's combat rules: "A blind character is unable to attack." The refused round costs nothing."""
        game, hild, cobra = _cobra_battle(ALWAYS_FAIL)
        _until_spit_hits(game, cobra)
        assert has_condition(hild, Condition.BLIND), "precondition: the spit blinded Hild"
        before = _state_without_logs(game)

        result = game.execute(
            ResolveBattleRound(
                declarations=(
                    BattleDeclaration(character_id=hild.id, action="attack", target_group_id=_group_id(game)),
                )
            )
        )

        assert not result.accepted
        assert "combat.attack.attacker_blind" in [rejection.code for rejection in result.rejections]
        assert not result.events
        assert _state_without_logs(game) == before


# ---------------------------------------------------------------------- ghouls and thouls

PARALYSERS_THAT_ATTACK_OTHERS = ("ghoul", "thoul")

OTHER_PARALYSERS = ("carcass_crawler", "gelatinous_cube")


class TestGhoulsAttackOthers:
    """A ghoul or a thoul passes over a member it has paralysed while another target is in reach."""

    @CHUNK
    def test_the_catalog_marks_the_ghouls_and_the_thouls_paralysis(self):
        catalog = load_monsters()
        for template_id in PARALYSERS_THAT_ATTACK_OTHERS:
            paralysis = catalog.get(template_id).ability("paralysis")
            assert paralysis is not None and not paralysis.manual
            assert "will attack others" in paralysis.prose, "precondition: the SRD's clause is in the prose"
            assert paralysis.params.get("attacks_others") is True, f"{template_id}'s paralysis has attacks_others"

    def test_the_other_paralysers_have_no_such_clause(self):
        """A guard: the carcass crawler's and the gelatinous cube's entries don't say they attack others."""
        catalog = load_monsters()
        for template_id in OTHER_PARALYSERS:
            paralysis = catalog.get(template_id).ability("paralysis")
            assert paralysis is not None and not paralysis.manual
            assert "attack others" not in paralysis.prose
            assert "attacks_others" not in paralysis.params

    @CHUNK
    @pytest.mark.parametrize("template_id", PARALYSERS_THAT_ATTACK_OTHERS)
    def test_it_picks_a_member_who_isnt_paralysed(self, template_id):
        game = _battle(template_id)
        hild, brakk = game.party.members
        _paralyse(game, hild)
        assert _front_rank_ids(game) == [hild.id, brakk.id], "precondition: both stand in the front rank"

        assert set(_targets(game)) == {brakk.id}

    @CHUNK
    @pytest.mark.parametrize("template_id", PARALYSERS_THAT_ATTACK_OTHERS)
    def test_it_picks_a_paralysed_member_before_a_statue(self, template_id):
        game = _battle(template_id)
        hild, brakk = game.party.members
        _paralyse(game, hild)
        _petrify(game, brakk)

        assert set(_targets(game)) == {hild.id}

    @pytest.mark.parametrize("template_id", PARALYSERS_THAT_ATTACK_OTHERS)
    def test_it_attacks_a_paralysed_member_when_no_other_target_is_in_reach(self, template_id):
        """A guard: an invisible member is out of reach, so the paralysed one is the only target left."""
        game = _battle(template_id)
        hild, brakk = game.party.members
        _paralyse(game, hild)
        _make_invisible(game, brakk)

        assert set(_targets(game)) == {hild.id}

    @pytest.mark.parametrize("template_id", PARALYSERS_THAT_ATTACK_OTHERS)
    def test_it_attacks_a_lone_paralysed_member(self, template_id):
        """A guard: with no one else in the party, the paralysed member is the target."""
        game = _battle(template_id, ("Hild",))
        hild = game.party.members[0]
        _paralyse(game, hild)

        assert set(_targets(game)) == {hild.id}

    @pytest.mark.parametrize("template_id", OTHER_PARALYSERS)
    def test_a_paralyser_without_the_clause_still_attacks_a_paralysed_member(self, template_id):
        """A guard: only the ghoul's and the thoul's entries say they turn to others."""
        game = _battle(template_id)
        hild, brakk = game.party.members
        _paralyse(game, hild)

        assert set(_targets(game)) == {hild.id, brakk.id}

    @CHUNK
    def test_the_policy_reads_the_param_and_not_the_monster(self):
        """A carcass crawler given `attacks_others` turns to others, so the rule is data, not a list of ids."""
        game = _battle("carcass_crawler")
        hild, brakk = game.party.members
        _with_paralysis_param(game.monsters[_monster_id(game)], True)
        _paralyse(game, hild)

        assert set(_targets(game)) == {brakk.id}

    def test_a_ghoul_without_the_param_attacks_a_paralysed_member(self):
        """A guard: a ghoul whose paralysis lacks `attacks_others` picks among both members, as before."""
        game = _battle("ghoul")
        hild, brakk = game.party.members
        _with_paralysis_param(game.monsters[_monster_id(game)], None)
        _paralyse(game, hild)

        assert set(_targets(game)) == {hild.id, brakk.id}

    @CHUNK
    @pytest.mark.parametrize("template_id", PARALYSERS_THAT_ATTACK_OTHERS)
    def test_in_battle_it_turns_from_the_member_it_paralysed(self, template_id):
        """The playtest's case: once a member is paralysed, the monster's attacks go to the member still standing."""
        game = _battle(template_id)
        hild, brakk = game.party.members
        _force_saves(game, hild, ALWAYS_FAIL)
        _force_saves(game, brakk, ALWAYS_PASS)
        monster_id = _monster_id(game)

        after = []
        for _ in range(30):
            paralysed_before_the_round = has_condition(hild, Condition.PARALYSED)
            events = _play(game)
            if paralysed_before_the_round:
                after += _rolls(events, monster_id)

        assert has_condition(hild, Condition.PARALYSED), "precondition: the monster paralysed Hild"
        assert not has_condition(brakk, Condition.PARALYSED), "precondition: Brakk is still standing"
        assert len(after) >= 5, "precondition: the monster attacked in several rounds after Hild was paralysed"
        assert {event.defender_id for event in after} == {brakk.id}

    @pytest.mark.parametrize("template_id", PARALYSERS_THAT_ATTACK_OTHERS)
    def test_its_routine_stays_on_one_target_within_a_round(self, template_id):
        """A guard: the policy picks a target once a round, so a paralysing hit doesn't move the rest of the routine."""
        game = _battle(template_id)
        for member in game.party.members:
            _force_saves(game, member, ALWAYS_FAIL)
        monster_id = _monster_id(game)

        multi_attack_rounds = 0
        for _ in range(12):
            if all(incapacitated(member) for member in game.party.members):
                break
            rolled = _rolls(_play(game), monster_id)
            assert len({event.defender_id for event in rolled}) <= 1
            multi_attack_rounds += len(rolled) > 1
        assert multi_attack_rounds, "precondition: the monster made more than one attack in some round"


# ---------------------------------------------------------------------- statues


EVERY_KIND = ("goblin", "ogre", "basilisk", "ghoul", "thoul", "carcass_crawler")
"""Monsters of several kinds: plain fighters, a petrifier, and paralysers with and without the clause."""


class TestStatues:
    """A member turned to stone is out of the fight, so no monster picks one while another target is in reach."""

    @CHUNK
    @pytest.mark.parametrize("template_id", EVERY_KIND)
    def test_no_monster_picks_a_statue_while_another_target_is_in_reach(self, template_id):
        game = _battle(template_id)
        hild, brakk = game.party.members
        _petrify(game, hild)
        assert _front_rank_ids(game) == [hild.id, brakk.id], "precondition: the statue still stands in the front rank"

        assert set(_targets(game)) == {brakk.id}

    @CHUNK
    def test_a_paralysed_member_comes_before_a_statue_for_any_monster(self):
        game = _battle("goblin")
        hild, brakk = game.party.members
        _petrify(game, hild)
        _paralyse(game, brakk)

        assert set(_targets(game)) == {brakk.id}

    @pytest.mark.parametrize("template_id", ["goblin", "basilisk", "ghoul"])
    def test_a_front_rank_of_statues_is_attacked_as_before(self, template_id):
        """A guard: with only statues in reach, the monster picks one of them, with the same draw as before.

        It neither holds nor reaches past them to the member behind, who stands out of melee.
        """
        game = _battle(template_id, ("Hild", "Brakk", "Sable"))
        hild, brakk, sable = game.party.members
        _petrify(game, hild)
        _petrify(game, brakk)
        front = _front_rank_ids(game)
        assert front == [hild.id, brakk.id], "precondition: Sable stands behind the statues"
        assert not incapacitated(sable)
        stream = game.streams.get(MONSTER_ACTION_STREAM)

        for _ in range(POLICY_CALLS):
            replica = RngStream.restore(stream.export_state())
            actions = _choose(game)
            assert [action.kind for action in actions] == ["melee"]
            assert actions[0].target_id == front[replica.randbelow(len(front))]
            assert stream.export_state() == replica.export_state()

    @CHUNK
    def test_in_battle_a_basilisk_bites_the_member_still_standing(self):
        """The playtest's case: a basilisk kept biting members it had already turned to stone."""
        game = _battle("basilisk")
        hild, brakk = game.party.members
        _petrify(game, hild)
        _force_saves(game, brakk, ALWAYS_PASS)
        basilisk = _monster_id(game)

        rolled = []
        for _ in range(10):
            rolled += _rolls(_play(game), basilisk)

        assert len(rolled) >= 5, "precondition: the basilisk bit in several rounds"
        assert not has_condition(brakk, Condition.PETRIFIED), "precondition: Brakk is still flesh"
        assert {event.defender_id for event in rolled} == {brakk.id}


# ---------------------------------------------------------------------- draws


class TestDrawsAsBefore:
    """A monster with no paralysed or petrified candidate draws exactly as before, so no golden moves."""

    @pytest.mark.parametrize(("template_id", "routines"), [("goblin", 1), ("basilisk", 1), ("ghoul", 1), ("thoul", 2)])
    def test_two_members_standing_draw_the_target_then_the_routine(self, template_id, routines):
        """A guard: the target draw is `randbelow` over the front rank, then the routine draw when there's a choice."""
        game = _battle(template_id)
        front = _front_rank_ids(game)
        assert len(front) == 2
        stream = game.streams.get(MONSTER_ACTION_STREAM)

        for _ in range(POLICY_CALLS):
            replica = RngStream.restore(stream.export_state())
            actions = _choose(game)
            assert [action.kind for action in actions] == ["melee"]
            assert actions[0].target_id == front[replica.randbelow(len(front))]
            if routines > 1:
                replica.randbelow(routines)
            assert stream.export_state() == replica.export_state(), "the policy drew differently than before"

    @pytest.mark.parametrize("template_id", ["goblin", "carcass_crawler"])
    def test_a_paralysed_member_changes_nothing_for_a_monster_without_the_clause(self, template_id):
        """A guard: only `attacks_others` and stone change the pool, so these draw as before."""
        game = _battle(template_id)
        _paralyse(game, game.party.members[0])
        front = _front_rank_ids(game)
        stream = game.streams.get(MONSTER_ACTION_STREAM)

        for _ in range(POLICY_CALLS):
            replica = RngStream.restore(stream.export_state())
            actions = _choose(game)
            assert actions[0].target_id == front[replica.randbelow(len(front))]
            assert stream.export_state() == replica.export_state()


# ---------------------------------------------------------------------- the surprise gaze


def _opening(
    template_id: str,
    stance: ReactionResult,
    *,
    distance_feet: int = 60,
    battle: bool | None = False,
    both_surprised: bool = False,
    save_bonus: int = ALWAYS_PASS,
    names: tuple[str, ...] = ("Hild", "Brakk", "Sable"),
) -> tuple[GameSession, list]:
    """A party surprised by one monster, and the opening events of the encounter.

    `battle` says whether the opening must end in a battle (True), in encounter mode (False), or either (None).
    With `both_surprised`, the monster rolls for surprise too and loses. Otherwise it's aware and can't be surprised.
    The party's front rank is Hild and Brakk, and Sable stands in the back rank.
    """
    for seed in SEEDS:
        game = _session(seed, _members(*names))
        for member in game.party.members:
            _force_saves(game, member, save_bonus)
        instances = game.spawn(template_id, 1)
        events = encounter_module.start_encounter(
            game,
            groups=[(template_id, instances)],
            kind="spawned",
            distance_feet=distance_feet,
            pinned_stance=stance,
            monsters_aware=not both_surprised,
        )
        started = next(event for event in events if event.code == "encounter.started")
        if not started.party_surprised or started.monsters_surprised != both_surprised:
            continue
        opened_battle = "battle.started" in [event.code for event in events]
        if battle is not None and opened_battle != battle:
            continue
        return game, events
    raise AssertionError(f"precondition: no seed let a {template_id} surprise the party as the test needs")


ENCOUNTER_STANCES = (ReactionResult.FRIENDLY, ReactionResult.INDIFFERENT, ReactionResult.UNCERTAIN)
"""Stances that leave a surprised party in encounter mode after the round it loses."""


class TestTheSurpriseGaze:
    """Every member of a party a basilisk surprises meets its gaze once, as the encounter opens."""

    @CHUNK
    @pytest.mark.parametrize("stance", ENCOUNTER_STANCES)
    def test_it_comes_whatever_the_stance(self, stance):
        game, events = _opening("basilisk", stance)

        assert game.mode is SessionMode.ENCOUNTER and game.battle is None, "precondition: no battle opened"
        for member in game.party.members:
            assert len(_saves(events, member.id, "paralysis")) == 1, f"{member.id} meets the basilisk's gaze once"

    @CHUNK
    def test_it_reaches_the_back_rank_in_melee_range_too(self):
        game, events = _opening("basilisk", ReactionResult.INDIFFERENT, distance_feet=5)

        assert game.mode is SessionMode.ENCOUNTER, "precondition: no battle opened"
        for member in game.party.members:
            assert len(_saves(events, member.id, "paralysis")) == 1

    @CHUNK
    def test_a_failed_save_turns_a_member_to_stone_outside_battle(self):
        game, events = _opening("basilisk", ReactionResult.INDIFFERENT, save_bonus=ALWAYS_FAIL)

        assert game.mode is SessionMode.ENCOUNTER, "precondition: no battle opened"
        for member in game.party.members:
            assert [save.code for save in _saves(events, member.id)] == ["combat.save.failed"]
            assert has_condition(member, Condition.PETRIFIED)

    @CHUNK
    def test_engaging_later_brings_no_second_gaze(self):
        game, opening = _opening("basilisk", ReactionResult.INDIFFERENT)
        assert game.mode is SessionMode.ENCOUNTER, "precondition: no battle opened"

        engage = game.execute(EngageBattle())
        assert engage.accepted and game.mode is SessionMode.BATTLE
        first_round = _play(game)

        assert game.encounter.groups[0].distance_feet > 5, "precondition: the basilisk is still out of melee"
        for member in game.party.members:
            assert len(_saves(opening, member.id)) == 1, "the member meets the gaze as the encounter opens"
            assert not _saves(engage.events, member.id)
            assert not _saves(first_round, member.id)

    @CHUNK
    def test_it_comes_when_the_basilisk_is_surprised_too(self):
        """The clause is about the characters, and a gaze is no action, so the basilisk's surprise changes nothing."""
        game, events = _opening("basilisk", ReactionResult.INDIFFERENT, both_surprised=True)

        for member in game.party.members:
            assert len(_saves(events, member.id, "paralysis")) == 1

    @pytest.mark.parametrize("stance", [ReactionResult.ATTACKS, ReactionResult.HOSTILE, ReactionResult.UNCERTAIN])
    def test_a_battle_that_opens_with_the_free_round_has_it_once(self, stance):
        """A guard: when the battle opens in the round the party loses, each member still meets the gaze once."""
        game, events = _opening("basilisk", stance, battle=True)

        assert game.battle is not None and game.battle.round == 1, "precondition: the monsters' free round ran"
        for member in game.party.members:
            assert len(_saves(events, member.id, "paralysis")) == 1

    def test_a_party_that_isnt_surprised_doesnt_meet_it_at_the_opening(self):
        """A guard: the clause needs the party surprised, and the basilisk is out of melee."""
        game = _session(SEEDS.start, _members("Hild", "Brakk"))
        instances = game.spawn("basilisk", 1)
        events = encounter_module.start_encounter(
            game,
            groups=[("basilisk", instances)],
            kind="spawned",
            distance_feet=60,
            pinned_stance=ReactionResult.INDIFFERENT,
            monsters_aware=True,
            party_aware=True,
        )

        started = next(event for event in events if event.code == "encounter.started")
        assert not started.party_surprised, "precondition: a party that heard the monsters isn't surprised"
        assert not [event for event in events if isinstance(event, SavingThrowRolledEvent)]

    def test_a_medusa_that_surprises_the_party_doesnt_gaze_at_the_opening(self):
        """A guard: the medusa's entry has no surprise clause."""
        game, events = _opening("medusa", ReactionResult.INDIFFERENT)

        for member in game.party.members:
            assert not _saves(events, member.id)

    @CHUNK
    def test_the_playtests_spawn_meets_it_and_replays(self):
        """The playtest's case, by command: a referee spawn the basilisk surprises, which stays an encounter."""
        for seed in SEEDS:
            members = _members("Hild", "Brakk", "Sable")
            party_document = party_to_document(members)
            game = _session(seed, members)
            result = game.execute(SpawnMonsters(template_id="basilisk", count_fixed=1, distance_feet=60))
            assert result.accepted
            started = next(event for event in result.events if event.code == "encounter.started")
            if not started.party_surprised or started.monsters_surprised or game.mode is not SessionMode.ENCOUNTER:
                continue
            for member in game.party.members:
                assert len(_saves(result.events, member.id, "paralysis")) == 1, f"{member.id} meets the gaze once"
            engage = game.execute(EngageBattle())
            assert engage.accepted
            assert not [event for event in engage.events if isinstance(event, SavingThrowRolledEvent)]

            replayed = replay_game(game.master_seed, party_document, game.adventure, Ruleset(), game.command_log)
            assert [event.model_dump(mode="json") for event in replayed.event_log] == [
                event.model_dump(mode="json") for event in game.event_log
            ]
            assert session_state(replayed) == session_state(game)
            return
        raise AssertionError("precondition: no seed let a spawned basilisk surprise the party without a battle")
