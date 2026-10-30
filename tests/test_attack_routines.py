"""Acceptance tests for monsters with more than one attack routine.

A monster template lists one or more attack routines, and a monster uses one of them in a round.
The OSE SRD joins them with "or", but its text says when each is used: a thoul claws or uses a
weapon as it likes, an elephant tramples on a 3-in-4 chance, and a dragon's breath is no attack
at all. These tests pin how the catalog labels each routine and how battle uses the labels:

- [`AttackRoutine.use`][osrlib.core.monsters.AttackRoutine.use] holds a
  [`RoutineUse`][osrlib.core.monsters.RoutineUse]. The first routine of every template is
  `PRIMARY`. A later routine is `ALTERNATIVE` when the SRD offers it as a free choice each round,
  `SITUATIONAL` when the SRD ties it to a situation osrlib doesn't model (range, a charge or a
  trample chance, water, a ship, a form, or a loadout), and `SPECIAL` when it is a breath, a gaze,
  or a spell that battle never rolls. The SRD compiler labels every routine and refuses to compile
  a monster whose later routine it can't label.
- [`MonsterAction.routine`][osrlib.crawl.battle.MonsterAction.routine] names a routine by its index
  in `template.attacks`. `None` resolves the first routine, as battle does today. An index the
  template doesn't have makes the action impossible, and the session skips it.
- The default policy, [`ScriptedPolicy`][osrlib.crawl.battle.ScriptedPolicy], picks a melee
  routine on the monster-action stream, with equal chance, among the `PRIMARY` and `ALTERNATIVE`
  routines. It never picks a `SITUATIONAL` or `SPECIAL` one. With one candidate it draws nothing,
  so a monster without an alternative draws exactly as it does today and no golden moves.
- A game's own policy may name any routine. Battle rolls a `SITUATIONAL` routine as melee and
  rolls nothing for a `SPECIAL` one. It never rolls an entry named `gaze` or `breath` as an attack.

Saving throws are forced where a test needs a particular outcome,
with a `save_bonus` of +20 or -20, so a test never depends on which draw a save comes from. Each
test asserts its own preconditions, so a test that stops exercising its case fails loudly.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.clock import ROUNDS_PER_TURN
from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, EffectDefinition, ModifierSpec, has_condition
from osrlib.core.events import AttackRolledEvent, DamageDealtEvent, LevelDrainedEvent, SavingThrowRolledEvent
from osrlib.core.monsters import AttackRoutine, MonsterAttack, RoutineUse
from osrlib.core.rng import RngStream
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
from osrlib.data import load_classes, load_monsters, load_spells

REPO = Path(__file__).resolve().parents[1]

HIT_CODES = frozenset({"combat.attack.hit", "combat.attack.auto_hit"})

ALWAYS_PASS = 20
"""A save bonus no d20 can fail with."""

ALWAYS_FAIL = -20
"""A save penalty no d20 can pass with."""

P = RoutineUse.PRIMARY
A = RoutineUse.ALTERNATIVE
S = RoutineUse.SITUATIONAL
X = RoutineUse.SPECIAL

EXPECTED_USES: dict[str, tuple[RoutineUse, ...]] = {
    # A free choice each round: the SRD prints "or" and names no situation.
    "minotaur": (P, A),
    "mule": (P, A),
    "spitting_cobra": (P, A),
    "thoul": (P, A),
    "wererat": (P, A),  # "Weapons: May also use weapons in animal form."
    # A breath that resolves through `kind="breath"`.
    "black_dragon": (P, X),
    "blue_dragon": (P, X),
    "chimera": (P, X),
    "dragon_turtle": (P, X),
    "gold_dragon": (P, X),
    "green_dragon": (P, X),
    "hellhound_3": (P, X),
    "hellhound_4": (P, X),
    "hellhound_5": (P, X),
    "hellhound_6": (P, X),
    "hellhound_7": (P, X),
    "red_dragon": (P, X),
    "sea_dragon": (P, X),
    "white_dragon": (P, X),
    # A breath or gaze with no attack roll, left to the referee.
    "gorgon": (P, X),
    "vampire_7": (P, X),
    "vampire_8": (P, X),
    "vampire_9": (P, X),
    # A spell.
    "medium": (P, X),  # "Each individual has one 1st level arcane spell memorized."
    "nixie": (P, X),  # "10 nixies together can cast a charm"
    # Gore in swine form, a weapon in human form, and "Charm person: 3 times a day."
    "devil_swine": (P, S, X),
    # Range: a thrown or spat missile.
    "ape_white": (P, S),
    "cloud_giant": (P, S),
    "cyclops": (P, S),
    "fire_giant": (P, S),
    "frost_giant": (P, S),
    "manticore": (P, S),
    "stone_giant": (P, S),
    # "Jump: ... 50% chance of jumping at a random opponent" and "Spit: Used defensively. 10' range."
    "cave_locust": (P, S, S),
    # "Trample: 3-in-4 chance of trampling each round."
    "elephant": (P, S),
    "mastodon": (P, S),
    "normal_rhinoceros": (P, S),
    "stegosaurus": (P, S),
    "titanothere": (P, S),
    "triceratops": (P, S),
    "woolly_rhinoceros": (P, S),
    # Water and ships.
    "bull_shark": (P, S),  # "Attack by ramming, then biting the stunned prey."
    "freshwater_termite": (P, S),  # "Irritant spray: Above water; maximum once a turn."
    "saltwater_termite": (P, S),
    "swamp_termite": (P, S),
    "sea_serpent_lesser": (P, S),  # "Squeeze: Coil around and crush a vessel"
    "sperm_whale": (P, S),  # "Ram ships: 10% likely to attack vessels"
    # A loadout: "Weapons: 4×1-handed or 2×2-handed."
    "bone_golem": (P, S),
}
"""The label of every routine of every template with more than one, as the OSE SRD's text gives it."""

FREE_CHOICE = tuple(sorted(template_id for template_id, uses in EXPECTED_USES.items() if A in uses))
"""Templates with an alternative routine, which the default policy picks as often as the first."""

ONE_CANDIDATE = tuple(sorted(template_id for template_id, uses in EXPECTED_USES.items() if A not in uses))
"""Templates with more than one routine, of which the default policy may pick only the first."""

ROUNDS = 200
"""Policy calls in a run, enough that an equal-chance routine can't go unpicked or wildly over-picked."""


# ---------------------------------------------------------------------- setup


def _character(name: str = "Hild", *, level: int = 3, hp: int = 400) -> Character:
    """A fighter with average scores, no armour, and enough hit points to outlast a long fight."""
    definition = load_classes().get("fighter")
    return Character(
        name=name,
        class_id="fighter",
        race=definition.race,
        level=level,
        xp=definition.row(level).xp,
        scores={ability: 10 for ability in AbilityScore},
        alignment="lawful",
        max_hp=hp,
        current_hp=hp,
    )


def _game(seed: int, members: list[Character]) -> GameSession:
    """A session standing at the entrance of a one-cell-wide, two-cell corridor."""
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    game = GameSession.new(Party(members=members), adventure, seed=seed)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def _battle(template_id: str, *, seed: int = 4, members: list[Character] | None = None, distance_feet: int = 5):
    """Start a battle against one monster and return the session and the monster.

    Tries seeds from `seed` upward until neither side is surprised, so every attack happens in a
    resolved round and the test doesn't depend on one seed's surprise roll.
    """
    for attempt in range(seed, seed + 50):
        game = _game(attempt, members or [_character()])
        result = game.execute(SpawnMonsters(template_id=template_id, count_fixed=1, distance_feet=distance_feet))
        assert result.accepted
        surprise = [event for event in result.events if event.code == "encounter.surprise.rolled"]
        if not surprise or any(event.roll is not None and event.roll <= event.threshold for event in surprise):
            continue
        assert game.execute(EngageBattle()).accepted
        assert game.mode is SessionMode.BATTLE
        monster = game.monsters[game.encounter.groups[0].monster_ids[0]]
        assert monster.template.id == template_id
        assert game.encounter.groups[0].distance_feet == distance_feet
        return game, monster
    pytest.fail(f"precondition: no seed from {seed} starts an unsurprised battle with {template_id}")


def _spend_breath(monster) -> None:
    """Use up a daily breath weapon, so the default policy melees instead of opening with breath."""
    breath = monster.template.ability("breath_weapon")
    if breath is not None and breath.params.get("uses_per_day") is not None:
        monster.breath_uses_today = int(breath.params["uses_per_day"])


def _force_saves(game: GameSession, character: Character, bonus: int) -> None:
    """Make every save the character rolls pass (+20) or fail (-20)."""
    effect, _ = game.ledger.attach(
        EffectDefinition(kind="test_save_ward", modifiers=(ModifierSpec(kind="save_bonus", value=bonus),)),
        character.id,
        clock=game.clock,
        allocator=game.allocator,
        registry=game.registry(),
    )
    assert effect is not None


def _round(game: GameSession) -> list:
    """Resolve one battle round in which every able member holds, and return its events."""
    assert game.mode is SessionMode.BATTLE, "precondition: the battle is still running"
    able = [member for member in game.party.living_members() if not incapacitated(member)]
    declarations = tuple(BattleDeclaration(character_id=member.id, action="hold") for member in able)
    result = game.execute(ResolveBattleRound(declarations=declarations))
    assert result.accepted
    return list(result.events)


def _rolls(events, attacker_id: str) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.attacker_id == attacker_id]


def _hits(events, attacker_id: str, name: str) -> list[AttackRolledEvent]:
    return [event for event in _rolls(events, attacker_id) if event.code in HIT_CODES and event.attack_name == name]


def _saves(events, target_id: str) -> list[SavingThrowRolledEvent]:
    return [event for event in events if isinstance(event, SavingThrowRolledEvent) and event.target_id == target_id]


def _routine_names(template) -> list[set[str]]:
    """The attack names in each of the template's routines, in routine order."""
    return [{attack.name for attack in routine.attacks} for routine in template.attacks]


def _expanded(routine) -> list[str]:
    """The attack names a routine rolls in a round, each repeated by its count."""
    return [attack.name for attack in routine.attacks for _ in range(attack.count)]


def _candidates(template_id: str) -> set[int]:
    """The indexes of the routines the default policy may pick, by the expected labels."""
    uses = EXPECTED_USES.get(template_id, (P,))
    return {index for index, use in enumerate(uses) if use in (P, A)}


def _choose(game: GameSession) -> list[MonsterAction]:
    """Call the default policy for the battle's only group, as the session does."""
    return ScriptedPolicy().choose(game, game.encounter.groups[0], game.streams.get(MONSTER_ACTION_STREAM))


def _melee_routines(game: GameSession, calls: int = ROUNDS) -> list[int]:
    """The routine of every melee action the default policy chooses over `calls` calls.

    `None` counts as the first routine. A chance-in-six breath, like the hellhound's, is left out.
    """
    chosen = []
    for _ in range(calls):
        actions = _choose(game)
        assert len(actions) == 1, "precondition: one monster, one action"
        if actions[0].kind == "breath":
            continue
        assert actions[0].kind == "melee", "precondition: the monster melees"
        chosen.append(0 if actions[0].routine is None else actions[0].routine)
    assert len(chosen) >= calls // 2, "precondition: the monster meleed in most calls"
    return chosen


class RoutinePolicy:
    """A game's own policy: every living monster melees the first party member with one routine."""

    def __init__(self, routine: int | None) -> None:
        self.routine = routine

    def choose(self, session, group, stream):
        target = session.party.living_members()[0]
        return [
            MonsterAction(monster_id=monster_id, kind="melee", target_id=target.id, routine=self.routine)
            for monster_id in group.monster_ids
            if not has_condition(session.combatant(monster_id), Condition.DEAD)
        ]


def _with_policy(game: GameSession, routine: int | None) -> None:
    game.action_policies[game.encounter.groups[0].id] = RoutinePolicy(routine)


# ---------------------------------------------------------------------- the contract


class TestTheContract:
    """The label and the routine index are fields with defaults that keep today's behavior."""

    def test_routine_use_has_four_values(self):
        assert [(use.name, use.value) for use in RoutineUse] == [
            ("PRIMARY", "primary"),
            ("ALTERNATIVE", "alternative"),
            ("SITUATIONAL", "situational"),
            ("SPECIAL", "special"),
        ]

    def test_a_routine_is_primary_unless_labelled(self):
        routine = AttackRoutine(attacks=(MonsterAttack(count=1, name="bite", damage="1d4"),))
        assert routine.use is RoutineUse.PRIMARY
        labelled = AttackRoutine.model_validate(
            {"attacks": [{"count": 1, "name": "bite", "damage": "1d4"}], "use": "situational"}
        )
        assert labelled.use is RoutineUse.SITUATIONAL

    def test_the_routine_field_defaults_to_none(self):
        action = MonsterAction(monster_id="monster-0001", kind="melee", target_id="character-0001")
        assert action.routine is None
        named = MonsterAction(monster_id="monster-0001", kind="melee", target_id="character-0001", routine=1)
        assert named.routine == 1


# ---------------------------------------------------------------------- the catalog


class TestTheCatalog:
    """The compiled data labels every routine as the OSE SRD's text gives it."""

    def test_the_label_table_covers_every_template_with_more_than_one_routine(self):
        catalog = load_monsters()
        multi = {template.id for template in catalog.monsters if len(template.attacks) > 1}
        assert multi == set(EXPECTED_USES)
        for template_id, uses in EXPECTED_USES.items():
            assert len(catalog.get(template_id).attacks) == len(uses), template_id

    def test_the_routines_these_tests_rely_on_are_in_the_data(self):
        catalog = load_monsters()
        cobra = catalog.get("spitting_cobra")
        assert _routine_names(cobra) == [{"spit"}, {"bite"}]
        bite = cobra.attacks[1].attacks[0]
        assert bite.damage == "1d3"
        assert bite.effects == ("poison",)
        poison = cobra.ability("poison")
        assert poison is not None and not poison.manual
        assert poison.params["onset_dice"] == "1d10" and poison.params["onset_unit"] == "turn"
        assert _routine_names(catalog.get("vampire_7")) == [{"touch"}, {"gaze"}]
        assert _routine_names(catalog.get("gorgon")) == [{"gore"}, {"breath"}]
        assert _routine_names(catalog.get("red_dragon")) == [{"claw", "bite"}, {"breath"}]
        assert _routine_names(catalog.get("hellhound_3")) == [{"bite"}, {"breath"}]
        assert catalog.get("hellhound_3").attacks[1].attacks[0].damage == "3d6", (
            "the hellhound's breath entry has damage dice, so rolling it as melee would deal them"
        )
        assert _routine_names(catalog.get("medium")) == [{"dagger"}, {"spell"}]
        assert _routine_names(catalog.get("nixie")) == [{"weapon"}, {"group spell"}]
        assert _routine_names(catalog.get("devil_swine")) == [{"gore"}, {"weapon"}, {"magic"}]
        assert _routine_names(catalog.get("cave_locust")) == [{"bite"}, {"jump"}, {"spit"}]
        assert _routine_names(catalog.get("elephant")) == [{"tusk"}, {"trample"}]
        assert _routine_names(catalog.get("basilisk")) == [{"bite", "gaze"}]

    @pytest.mark.parametrize("template_id", sorted(EXPECTED_USES))
    def test_each_routine_carries_the_label_the_srd_gives(self, template_id):
        template = load_monsters().get(template_id)
        assert tuple(routine.use for routine in template.attacks) == EXPECTED_USES[template_id]

    def test_every_first_routine_is_primary(self):
        armed = [template for template in load_monsters().monsters if template.attacks]
        assert len(armed) > len(EXPECTED_USES), "precondition: most templates have attacks"
        for template in armed:
            assert template.attacks[0].use is RoutineUse.PRIMARY, template.id

    def test_summoned_creatures_carry_labels_too(self):
        """The monster stat blocks printed on spell pages compile through the same routine parser."""
        conjured = [monster for spell in load_spells().spells for monster in spell.conjured_monsters]
        assert conjured, "precondition: a spell page prints a monster stat block"
        for template in conjured:
            assert template.attacks[0].use is RoutineUse.PRIMARY, template.id
            assert all(routine.use is not RoutineUse.PRIMARY for routine in template.attacks[1:]), template.id

    @pytest.mark.parametrize("filename", ["monsters.json", "spells.json"])
    def test_the_generated_data_writes_every_label(self, filename):
        """Every routine in the generated data names its label, so a reader of the JSON sees it."""
        document = json.loads((REPO / "src" / "osrlib" / "data" / filename).read_text(encoding="utf-8"))
        routines = []

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("attacks"), list):
                    routines.extend(item for item in node["attacks"] if isinstance(item, dict) and "attacks" in item)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(document)
        assert routines, f"precondition: {filename} has attack routines"
        assert all(routine.get("use") in {use.value for use in RoutineUse} for routine in routines)


# ---------------------------------------------------------------------- the compiler


def _compile_edited_srd(tmp_path: Path, filename: str, old: str, new: str) -> subprocess.CompletedProcess:
    """Compile a copy of the SRD with one page edited, and return the finished process."""
    srd = tmp_path / "srd"
    shutil.copytree(REPO / "srd", srd)
    page = srd / filename
    text = page.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"precondition: {filename} has {old!r} once"
    page.write_text(text.replace(old, new), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    return subprocess.run(
        [sys.executable, "-m", "tools.srd_compile", "--srd-dir", str(srd), "--out-dir", str(out)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


class TestTheCompilerRefusesAnUnlabelledRoutine:
    """A new SRD monster with a later routine the compiler can't label stops the build.

    The edit gives a page a second routine with an attack name no monster has, so no label table or
    breath, gaze, or spell rule can cover it.
    """

    def test_a_monster_page(self, tmp_path):
        process = _compile_edited_srd(
            tmp_path,
            "Goblin.md",
            "| Attacks | 1 × weapon (1d6 or by weapon) |",
            "| Attacks | 1 × weapon (1d6 or by weapon) or 1 × tentacle lash (1d4) |",
        )
        assert process.returncode != 0, "the compiler wrote data with an unlabelled routine"
        assert "goblin" in process.stderr.lower(), "the failure names the monster"

    def test_a_stat_block_on_a_spell_page(self, tmp_path):
        process = _compile_edited_srd(
            tmp_path,
            "Sticks_to_Snakes.md",
            "**Att** 1 × bite (1d4),",
            "**Att** 1 × bite (1d4) or 1 × tentacle lash (1d2),",
        )
        assert process.returncode != 0, "the compiler wrote data with an unlabelled routine"
        assert "snake" in process.stderr.lower(), "the failure names the monster"


# ---------------------------------------------------------------------- the default policy


class TestTheDefaultPolicyPicksARoutine:
    """Under `ScriptedPolicy`, a monster picks among its primary and alternative routines."""

    @pytest.mark.parametrize("template_id", FREE_CHOICE)
    def test_a_free_choice_is_an_equal_chance(self, template_id):
        game, _ = _battle(template_id)

        chosen = _melee_routines(game)

        assert set(chosen) == {0, 1}
        assert 0.3 * len(chosen) < chosen.count(1) < 0.7 * len(chosen), "each routine has an equal chance"

    @pytest.mark.parametrize("template_id", ONE_CANDIDATE)
    def test_a_situational_or_special_routine_is_never_picked(self, template_id):
        game, monster = _battle(template_id)
        _spend_breath(monster)
        assert _candidates(template_id) == {0}

        chosen = _melee_routines(game, calls=60)

        assert set(chosen) == {0}

    def test_the_choice_draws_on_the_monster_action_stream_every_round(self):
        game, _ = _battle("spitting_cobra")
        stream = game.streams.get(MONSTER_ACTION_STREAM)
        others = {name: state for name, state in game.streams.export_states().items() if name != MONSTER_ACTION_STREAM}

        for _ in range(20):
            replica = RngStream.restore(stream.export_state())
            replica.randbelow(1)  # the target pick in a one-member party
            actions = _choose(game)
            assert [action.kind for action in actions] == ["melee"]
            assert actions[0].routine in {None, 0, 1}
            assert stream.export_state() != replica.export_state(), "the policy drew nothing for the routine"

        after = {name: state for name, state in game.streams.export_states().items() if name != MONSTER_ACTION_STREAM}
        assert after == others

    def test_a_group_that_closes_draws_nothing_for_a_routine(self):
        game, _ = _battle("spitting_cobra", distance_feet=40)
        stream = game.streams.get(MONSTER_ACTION_STREAM)
        before = stream.export_state()

        actions = _choose(game)

        assert [action.kind for action in actions] == ["close"]
        assert stream.export_state() == before


class TestOneCandidateDrawsAsToday:
    """A monster with no alternative routine draws only its target, exactly as before the change.

    A one-member party gives each melee monster one target, so the policy's only draw for it is
    `randbelow(1)`. The test replays that draw on a copy of the stream and compares positions.
    These pass today and must still pass after the change: they are why the goldens don't move.
    """

    @pytest.mark.parametrize("template_id", ["goblin", "troll", "basilisk", *ONE_CANDIDATE])
    def test_the_target_pick_is_the_only_draw(self, template_id):
        game, monster = _battle(template_id)
        _spend_breath(monster)
        breath = monster.template.ability("breath_weapon")
        stream = game.streams.get(MONSTER_ACTION_STREAM)

        for _ in range(20):
            replica = RngStream.restore(stream.export_state())
            actions = _choose(game)
            assert len(actions) == 1
            if breath is not None and breath.params.get("per_round_chance_in_six") is not None:
                gate = replica.randbelow(6) + 1
                if gate <= int(breath.params["per_round_chance_in_six"]):
                    assert actions[0].kind == "breath"
                    assert stream.export_state() == replica.export_state()
                    continue
            assert actions[0].kind == "melee"
            assert actions[0].target_id == game.party.members[0].id
            assert actions[0].routine in {None, 0}
            replica.randbelow(1)
            assert stream.export_state() == replica.export_state(), "the policy drew more than the target pick"

    @pytest.mark.parametrize(
        ("template_id", "names"),
        [
            ("vampire_7", {"touch"}),
            ("gorgon", {"gore"}),
            ("red_dragon", {"claw", "bite"}),
            ("hellhound_3", {"bite"}),
            ("medium", {"dagger"}),
            ("devil_swine", {"gore"}),
            ("cave_locust", {"bite"}),
            ("elephant", {"tusk"}),
            ("stone_giant", {"weapon"}),
        ],
    )
    def test_battle_rolls_only_the_first_routine(self, template_id, names):
        game, monster = _battle(template_id, members=[_character(level=12)])
        _spend_breath(monster)

        rolled = set()
        rounds = 0
        while rounds < 10 and game.mode is SessionMode.BATTLE:
            rolled |= {event.attack_name for event in _rolls(_round(game), monster.id)}
            rounds += 1

        assert rounds >= 3, "precondition: the battle ran long enough to see the routine"
        assert rolled == names


# ---------------------------------------------------------------------- the later routine's effects


class TestTheLaterRoutineApplies:
    """The effects of a later routine apply when battle resolves it, with the save the SRD gives."""

    def test_the_spitting_cobra_spits_and_bites_under_the_default_policy(self):
        game, cobra = _battle("spitting_cobra")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_PASS)

        rolled = []
        for _ in range(30):
            rolled += [event.attack_name for event in _rolls(_round(game), cobra.id)]

        assert set(rolled) == {"spit", "bite"}
        assert len(rolled) == 30, "one attack a round: the routines are alternatives, not a sequence"

    def test_a_failed_save_against_the_cobras_bite_kills_in_1d10_turns(self):
        game, cobra = _battle("spitting_cobra")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_FAIL)

        for _ in range(40):
            events = _round(game)
            if _hits(events, cobra.id, "bite"):
                break
        else:
            pytest.fail("precondition: the cobra's bite hit within 40 rounds")

        saves = _saves(events, fighter.id)
        assert [save.category for save in saves] == ["death"], "save versus poison is the death category"
        assert saves[0].code == "combat.save.failed"
        assert has_condition(fighter, Condition.POISONED)
        assert not has_condition(fighter, Condition.DEAD), "the cobra's poison kills in 1d10 turns, not at once"
        poison = next(
            effect for effect in game.ledger.active_on(fighter.id) if effect.definition.condition is Condition.POISONED
        )
        onset = poison.expires_round - poison.attached_round
        assert ROUNDS_PER_TURN <= onset <= 10 * ROUNDS_PER_TURN
        assert onset % ROUNDS_PER_TURN == 0
        assert poison.definition.expiry == "death"

    def test_a_passed_save_leaves_the_bite_damage(self):
        game, cobra = _battle("spitting_cobra")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_PASS)

        for _ in range(40):
            events = _round(game)
            if _hits(events, cobra.id, "bite"):
                break
        else:
            pytest.fail("precondition: the cobra's bite hit within 40 rounds")

        saves = _saves(events, fighter.id)
        assert [(save.category, save.code) for save in saves] == [("death", "combat.save.passed")]
        damage = [event for event in events if isinstance(event, DamageDealtEvent) and event.target_id == fighter.id]
        assert damage and all(event.amount >= 1 for event in damage)
        assert not has_condition(fighter, Condition.POISONED)

    def test_the_cobras_spit_applies_no_save_in_battle(self):
        """Blindness stays with the referee, as the register says, whichever routine policy is in force."""
        game, cobra = _battle("spitting_cobra")
        fighter = game.party.members[0]
        _with_policy(game, None)

        hit = False
        for _ in range(20):
            events = _round(game)
            rolled = _rolls(events, cobra.id)
            assert rolled and {event.attack_name for event in rolled} == {"spit"}
            hit = hit or bool(_hits(events, cobra.id, "spit"))
            assert not _saves(events, fighter.id)
        assert hit, "precondition: a spit hit"
        assert not has_condition(fighter, Condition.BLIND)


# ---------------------------------------------------------------------- a game's own policy


class TestAGamesPolicyChoosesTheRoutine:
    """A policy registered in `session.action_policies` names the routine, and battle resolves that routine."""

    def test_no_routine_means_the_first(self):
        game, cobra = _battle("spitting_cobra")
        _with_policy(game, None)

        rolled = set()
        for _ in range(10):
            rolled |= {event.attack_name for event in _rolls(_round(game), cobra.id)}

        assert rolled == {"spit"}

    def test_the_named_alternative_is_the_one_rolled(self):
        game, cobra = _battle("spitting_cobra")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_PASS)
        _with_policy(game, 1)

        rolled = []
        for _ in range(10):
            rolled += [event.attack_name for event in _rolls(_round(game), cobra.id)]

        assert rolled == ["bite"] * 10

    @pytest.mark.parametrize(
        ("template_id", "index"),
        [
            ("elephant", 1),
            ("stone_giant", 1),
            ("cave_locust", 1),
            ("cave_locust", 2),
            ("devil_swine", 1),
            ("bull_shark", 1),
            ("bone_golem", 1),
        ],
    )
    def test_a_named_situational_routine_is_rolled_as_melee(self, template_id, index):
        game, monster = _battle(template_id, members=[_character(level=12)])
        routine = monster.template.attacks[index]
        _force_saves(game, game.party.members[0], ALWAYS_PASS)
        _with_policy(game, index)

        for _ in range(6):
            rolled = [event.attack_name for event in _rolls(_round(game), monster.id)]
            assert rolled == _expanded(routine), "each round rolls the named routine's attacks, and only those"
        assert routine.use is RoutineUse.SITUATIONAL, "the routine rolled is one the default policy never picks"

    def test_the_thouls_weapon_routine_doesnt_paralyse(self):
        game, thoul = _battle("thoul")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_FAIL)
        _with_policy(game, 1)

        hit = False
        for _ in range(10):
            events = _round(game)
            rolled = _rolls(events, thoul.id)
            assert [event.attack_name for event in rolled] == ["weapon"]
            hit = hit or bool(_hits(events, thoul.id, "weapon"))
            assert not _saves(events, fighter.id)
        assert hit, "precondition: a weapon hit"
        assert not has_condition(fighter, Condition.PARALYSED)

    def test_a_wrapped_default_policy_can_switch_to_a_situational_routine(self):
        class TrampleAlways:
            def choose(self, session, group, stream):
                actions = ScriptedPolicy().choose(session, group, stream)
                return [
                    action.model_copy(update={"routine": 1}) if action.kind == "melee" else action for action in actions
                ]

        game, elephant = _battle("elephant", members=[_character(level=12)])
        game.action_policies[game.encounter.groups[0].id] = TrampleAlways()

        rolled = set()
        for _ in range(6):
            rolled |= {event.attack_name for event in _rolls(_round(game), elephant.id)}

        assert rolled == {"trample"}

    @pytest.mark.parametrize(("template_id", "index"), [("spitting_cobra", 2), ("spitting_cobra", -1), ("goblin", 1)])
    def test_an_index_the_template_lacks_is_skipped(self, template_id, index):
        """A negative index is not one the template has, so it isn't read from the end of the list."""
        game, monster = _battle(template_id)
        fighter = game.party.members[0]
        _with_policy(game, index)

        for _ in range(3):
            events = _round(game)
            assert not _rolls(events, monster.id)
        assert fighter.current_hp == fighter.max_hp

    def test_naming_the_first_routine_matches_naming_none(self):
        """For a monster with one routine, `routine=0` and `routine=None` play the same fight."""

        def play(routine):
            game, _ = _battle("goblin", seed=11)
            _with_policy(game, routine)
            events = []
            for _ in range(5):
                events += _round(game)
            return [event.model_dump(mode="json") for event in events]

        with_zero = play(0)
        assert any(event["event_type"] == "attack_rolled" for event in with_zero), "precondition: the goblin attacked"
        assert with_zero == play(None)


# ---------------------------------------------------------------------- special routines


class TestSpecialRoutinesAreNeverRolled:
    """A special routine, and any entry named `gaze` or `breath`, is never rolled, whoever picked it."""

    def test_the_basilisks_gaze_entry_stays_skipped(self):
        game, basilisk = _battle("basilisk")
        _force_saves(game, game.party.members[0], ALWAYS_PASS)

        rolled = set()
        for _ in range(5):
            rolled |= {event.attack_name for event in _rolls(_round(game), basilisk.id)}

        assert rolled == {"bite"}

    @pytest.mark.parametrize(
        ("template_id", "index"),
        [
            ("vampire_7", 1),
            ("gorgon", 1),
            ("hellhound_3", 1),
            ("red_dragon", 1),
            ("chimera", 1),
            ("medium", 1),
            ("nixie", 1),
            ("devil_swine", 2),
        ],
    )
    def test_a_named_special_routine_rolls_nothing(self, template_id, index):
        """A breath, a gaze, or a spell named in a melee action does nothing that round.

        The hellhound's breath entry has damage dice, so rolling it as melee would deal them. The
        gorgon's petrifying breath and the vampire's charming gaze stay with the referee.
        """
        game, monster = _battle(template_id)
        fighter = game.party.members[0]
        level = fighter.level
        _force_saves(game, fighter, ALWAYS_FAIL)
        _with_policy(game, index)

        for _ in range(5):
            events = _round(game)
            assert not _rolls(events, monster.id)
            assert not [event for event in events if isinstance(event, LevelDrainedEvent)]
            assert not _saves(events, fighter.id)
        assert fighter.level == level
        assert fighter.current_hp == fighter.max_hp
        assert not has_condition(fighter, Condition.PETRIFIED)
        assert not has_condition(fighter, Condition.CHARMED)
        assert monster.template.attacks[index].use is RoutineUse.SPECIAL

    def test_the_vampires_touch_routine_still_drains(self):
        game, vampire = _battle("vampire_7", members=[_character(level=6)])
        fighter = game.party.members[0]
        _with_policy(game, 0)

        for _ in range(20):
            events = _round(game)
            if _hits(events, vampire.id, "touch"):
                break
        else:
            pytest.fail("precondition: the vampire's touch hit within 20 rounds")
        assert [event for event in events if isinstance(event, LevelDrainedEvent)]
        assert fighter.level < 6


# ---------------------------------------------------------------------- determinism


class TestDeterminism:
    """The same seed and the same commands give the same events, whichever routines are picked."""

    @staticmethod
    def _play(template_id: str):
        game, monster = _battle(template_id, seed=8, members=[_character("Hild"), _character("Brakk")])
        events = []
        for _ in range(20):
            if game.mode is not SessionMode.BATTLE:
                break
            events += _round(game)
        return game, monster, [event.model_dump(mode="json") for event in events]

    @pytest.mark.parametrize("template_id", ["spitting_cobra", "thoul", "minotaur"])
    def test_replaying_a_battle_gives_the_same_events(self, template_id):
        first_game, monster, first = self._play(template_id)
        second_game, _, second = self._play(template_id)

        names = {
            event["attack_name"]
            for event in first
            if event["event_type"] == "attack_rolled" and event["attacker_id"] == monster.id
        }
        expected = set().union(*(_routine_names(monster.template)[index] for index in _candidates(template_id)))
        assert names == expected, "precondition: the battle used every routine the policy may pick"
        assert first == second
        assert first_game.ledger.model_dump(mode="json") == second_game.ledger.model_dump(mode="json")
        assert first_game.streams.export_states() == second_game.streams.export_states()


# ---------------------------------------------------------------------- the register


def _register() -> str:
    return (REPO / "docs" / "adaptations.md").read_text(encoding="utf-8")


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text)


def _section(text: str, heading: str) -> str:
    match = re.search(rf"(?m)^### {re.escape(heading)}\n(.*?)(?=^#{{1,6}} |\Z)", text, re.S)
    assert match, f"precondition: the register has the section {heading!r}"
    return match.group(1)


class TestTheRegister:
    """The adaptations register describes the routine choice and no longer says battle uses only the first."""

    def test_the_register_no_longer_says_battle_uses_only_the_first_routine(self):
        stale = [
            sentence
            for sentence in _sentences(_register())
            if re.search(r"(?i)\bonly\b", sentence) and re.search(r"(?i)\bfirst\b[^.]*\broutine", sentence)
        ]
        assert not stale, f"the register still says battle uses only the first routine: {stale}"

    def test_the_default_policy_section_names_the_routine_choice(self):
        section = _section(_register(), "The default action policy and morale")
        assert re.search(r"(?i)\battack routines?\b", section), (
            "the default policy's section says how it picks a routine"
        )

    def test_the_register_leaves_situational_routines_to_a_games_policy(self):
        records = [
            sentence
            for sentence in _sentences(_register())
            if re.search(r"(?i)\bsituational\b", sentence)
            and re.search(r"(?i)\broutines?\b", sentence)
            and re.search(r"(?i)\bpolic(y|ies)\b", sentence)
        ]
        assert records, "the register says situational routines are left to a game's own policy"
