"""Acceptance tests for monster attack effects in battle (issue #167, item 14).

A monster attack lists what a hit does beyond damage, as tags like `paralysis`, `poison`,
`disease`, `energy_drain`, and `petrification`, and the template carries an ability with the
same tag that holds the parameters. These tests drive real battles through
`GameSession.execute` and check that a hit applies each effect its attack lists, with the save
the SRD gives, that a gazer's gaze runs each round against the characters in melee with it,
and that a miss applies nothing.

Saving throws are forced where a test needs a particular outcome. A `save_bonus` modifier of
+20 makes every save pass and one of -20 makes every save fail, so a test never depends on
which stream or which draw a save comes from. Each test asserts its own preconditions, such as
a hit landing, so a test that stops exercising its case fails loudly.
"""

import re
from pathlib import Path

import pytest

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character
from osrlib.core.clock import ROUNDS_PER_TURN
from osrlib.core.combat import apply_healing, incapacitated, natural_healing
from osrlib.core.effects import EFFECTS_STREAM, Condition, EffectDefinition, ModifierSpec, has_condition
from osrlib.core.events import (
    AttackRolledEvent,
    ConditionGainedEvent,
    DamageDealtEvent,
    EffectAttachedEvent,
    LevelDrainedEvent,
    SavingThrowRolledEvent,
)
from osrlib.core.rng import RngStreams
from osrlib.crawl.adventure import Adventure, TownSpec
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
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes, load_monsters

HIT_CODES = frozenset({"combat.attack.hit", "combat.attack.auto_hit"})

ALWAYS_PASS = 20
"""A save bonus no d20 can fail with: a roll of 1 plus 20 beats every saving throw value."""

ALWAYS_FAIL = -20
"""A save penalty no d20 can pass with: a roll of 20 minus 20 is below every saving throw value."""

APPLIED_KINDS = frozenset({"paralysis", "poison", "disease", "energy_drain", "petrification", "blindness"})
"""The effect kinds this file shows battle applying, and `blindness`, which `test_monster_tactics.py`
shows. A monster's attack effect of one of these kinds counts as applied when the monster's ability
with that tag isn't `manual`."""


# ---------------------------------------------------------------------- setup


def _character(name: str = "Hild", class_id: str = "fighter", *, level: int = 3, hp: int = 400) -> Character:
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


def _game(seed: int, members: list[Character]) -> GameSession:
    """A session standing at the entrance of a one-cell-wide, two-cell corridor."""
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    adventure = Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))
    game = GameSession.new(Party(members=members), adventure, seed=seed)
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def _battle(seed: int, template_id: str, members: list[Character] | None = None, *, distance_feet: int = 5):
    """Start a battle against one monster, with neither side surprised, and return the session and the monster."""
    game = _game(seed, members or [_character()])
    result = game.execute(SpawnMonsters(template_id=template_id, count_fixed=1, distance_feet=distance_feet))
    assert result.accepted
    surprise = [event for event in result.events if event.code == "encounter.surprise.rolled"]
    assert surprise and all(event.roll is None or event.roll > event.threshold for event in surprise), (
        "precondition: neither side is surprised, so every attack happens in a resolved round"
    )
    assert game.execute(EngageBattle()).accepted
    assert game.mode is SessionMode.BATTLE
    monster = game.monsters[game.encounter.groups[0].monster_ids[0]]
    assert monster.template.id == template_id
    return game, monster


def _attach(game: GameSession, character: Character, definition: EffectDefinition) -> None:
    effect, _ = game.ledger.attach(
        definition, character.id, clock=game.clock, allocator=game.allocator, registry=game.registry()
    )
    assert effect is not None


def _force_saves(game: GameSession, character: Character, bonus: int) -> None:
    """Make every save the character rolls pass (+20) or fail (-20)."""
    _attach(
        game,
        character,
        EffectDefinition(kind="test_save_ward", modifiers=(ModifierSpec(kind="save_bonus", value=bonus),)),
    )


def _avert_eyes(game: GameSession, character: Character) -> None:
    _attach(game, character, EffectDefinition(kind="test_averting_eyes", condition=Condition.AVERTED_EYES))


def _round(game: GameSession) -> list:
    """Resolve one battle round in which every able member holds, and return its events."""
    able = [member for member in game.party.living_members() if not incapacitated(member)]
    declarations = tuple(BattleDeclaration(character_id=member.id, action="hold") for member in able)
    result = game.execute(ResolveBattleRound(declarations=declarations))
    assert result.accepted
    return list(result.events)


def _hits(events, defender_id: str, attack_names=None) -> list[AttackRolledEvent]:
    return [
        event
        for event in events
        if isinstance(event, AttackRolledEvent)
        and event.code in HIT_CODES
        and event.defender_id == defender_id
        and (attack_names is None or event.attack_name in attack_names)
    ]


def _attack_rolls(events, defender_id: str) -> list[AttackRolledEvent]:
    return [event for event in events if isinstance(event, AttackRolledEvent) and event.defender_id == defender_id]


def _saves(events, target_id: str, category: str | None = None) -> list[SavingThrowRolledEvent]:
    return [
        event
        for event in events
        if isinstance(event, SavingThrowRolledEvent)
        and event.target_id == target_id
        and (category is None or event.category == category)
    ]


def _effect_attacks(monster, kind: str) -> set[str]:
    """The names of the monster's attacks that list the effect kind."""
    return {attack.name for routine in monster.template.attacks for attack in routine.attacks if kind in attack.effects}


def _until_hit(game: GameSession, defender: Character, attack_names, *, max_rounds: int = 10) -> list:
    """Resolve rounds until one of the named attacks hits the defender, and return that round's events."""
    for _ in range(max_rounds):
        assert game.mode is SessionMode.BATTLE, "precondition: the battle is still running"
        events = _round(game)
        if _hits(events, defender.id, attack_names):
            return events
    pytest.fail(f"precondition: none of {sorted(attack_names)} hit {defender.id} in {max_rounds} rounds")


def _effects_with(game: GameSession, character: Character, condition: Condition) -> list:
    return [effect for effect in game.ledger.active_on(character.id) if effect.definition.condition is condition]


# ---------------------------------------------------------------------- paralysis

PARALYSERS = ("ghoul", "thoul", "carcass_crawler", "gelatinous_cube")


class TestParalysis:
    """A hit by a paralysing attack makes the defender save versus paralysis or be paralysed for 2d4 turns."""

    @pytest.mark.parametrize("template_id", PARALYSERS)
    def test_a_failed_save_paralyses_for_2d4_turns(self, template_id):
        game, monster = _battle(4, template_id)
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_FAIL)

        events = _until_hit(game, fighter, _effect_attacks(monster, "paralysis"))

        saves = _saves(events, fighter.id, "paralysis")
        assert saves, "a paralysing hit makes the defender save versus paralysis"
        assert all(save.code == "combat.save.failed" for save in saves)
        assert has_condition(fighter, Condition.PARALYSED)
        effects = _effects_with(game, fighter, Condition.PARALYSED)
        assert effects
        for effect in effects:
            rounds = effect.expires_round - effect.attached_round
            assert rounds % ROUNDS_PER_TURN == 0 and 2 <= rounds // ROUNDS_PER_TURN <= 8, "2d4 turns"
            assert not effect.definition.dispellable, "what a monster inflicts isn't dispellable"

    @pytest.mark.parametrize("template_id", PARALYSERS)
    def test_a_passed_save_leaves_the_defender_free(self, template_id):
        game, monster = _battle(4, template_id)
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_PASS)
        names = _effect_attacks(monster, "paralysis")

        events = _until_hit(game, fighter, names)

        saves = _saves(events, fighter.id, "paralysis")
        assert len(saves) == len(_hits(events, fighter.id, names)), "each paralysing hit makes its own save"
        assert all(save.code == "combat.save.passed" for save in saves)
        assert not has_condition(fighter, Condition.PARALYSED)
        assert not _effects_with(game, fighter, Condition.PARALYSED)

    @pytest.mark.parametrize("template_id", ["ghoul", "thoul"])
    def test_elves_are_unaffected_by_ghoul_paralysis(self, template_id):
        game, monster = _battle(4, template_id, [_character("Elara", "elf"), _character("Hild")])
        elf, fighter = game.party.members
        for member in (elf, fighter):
            _force_saves(game, member, ALWAYS_FAIL)
        names = _effect_attacks(monster, "paralysis")

        events = []
        for _ in range(12):
            events += _round(game)
            if _hits(events, elf.id, names) and _hits(events, fighter.id, names):
                break
        assert _hits(events, elf.id, names), "precondition: a paralysing attack hit the elf"
        assert _hits(events, fighter.id, names), "precondition: a paralysing attack hit the fighter"

        assert has_condition(fighter, Condition.PARALYSED), "the same attacks paralyse a fighter"
        assert not _saves(events, elf.id, "paralysis"), "an unaffected elf rolls no save"
        assert not has_condition(elf, Condition.PARALYSED)

    def test_the_carcass_crawler_paralyses_elves(self):
        game, monster = _battle(4, "carcass_crawler", [_character("Elara", "elf")])
        elf = game.party.members[0]
        _force_saves(game, elf, ALWAYS_FAIL)

        events = _until_hit(game, elf, _effect_attacks(monster, "paralysis"))

        assert _saves(events, elf.id, "paralysis")
        assert has_condition(elf, Condition.PARALYSED)


# ---------------------------------------------------------------------- energy drain


class TestEnergyDrain:
    """A hit by a draining touch takes levels with no saving throw."""

    @pytest.mark.parametrize(
        ("template_id", "levels", "xp_after"),
        [
            # Fighter thresholds: level 2 at 2,000 XP, level 3 at 4,000, level 4 at 8,000.
            ("wight", 1, 6_000),  # halfway between 8,000 and 4,000
            ("wraith", 1, 4_000),  # the new level's threshold
            ("spectre", 2, 2_000),  # two levels, to the new level's threshold
        ],
    )
    def test_a_hit_drains_levels_with_no_save(self, template_id, levels, xp_after):
        game, monster = _battle(4, template_id, [_character(level=4)])
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_PASS)

        events = _until_hit(game, fighter, _effect_attacks(monster, "energy_drain"))

        assert len(_hits(events, fighter.id)) == 1, "precondition: one touch landed"
        drained = [event for event in events if isinstance(event, LevelDrainedEvent) and event.target_id == fighter.id]
        assert [event.code for event in drained] == ["combat.drain.drained"]
        assert drained[0].levels_lost == levels
        assert fighter.level == 4 - levels, "no save, however good, stops the drain"
        assert fighter.xp == xp_after

    def test_draining_the_last_level_kills(self):
        game, monster = _battle(4, "wight", [_character(level=1)])
        fighter = game.party.members[0]

        events = _until_hit(game, fighter, _effect_attacks(monster, "energy_drain"))

        drained = [event for event in events if isinstance(event, LevelDrainedEvent) and event.target_id == fighter.id]
        assert [event.code for event in drained] == ["combat.drain.slain"]
        assert has_condition(fighter, Condition.DEAD)


# ---------------------------------------------------------------------- poison


class TestPoison:
    """A hit by a poisonous attack makes the defender save versus death or die, at once or after the onset."""

    @pytest.mark.parametrize("template_id", ["pit_viper", "killer_bee", "giant_rockfish"])
    def test_a_failed_save_against_a_deadly_poison_kills(self, template_id):
        game, monster = _battle(4, template_id)
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_FAIL)

        events = _until_hit(game, fighter, _effect_attacks(monster, "poison"))

        saves = _saves(events, fighter.id, "death")
        assert saves and saves[0].code == "combat.save.failed"
        assert has_condition(fighter, Condition.DEAD)
        assert game.death_records[fighter.id].cause == "poison", "neutralize poison can revive the victim"

    def test_a_failed_save_against_a_slow_poison_kills_when_the_onset_ends(self):
        game, monster = _battle(4, "black_widow")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_FAIL)

        events = _until_hit(game, fighter, _effect_attacks(monster, "poison"))

        saves = _saves(events, fighter.id, "death")
        assert saves and all(save.code == "combat.save.failed" for save in saves)
        assert not has_condition(fighter, Condition.DEAD), "the black widow's poison kills in 1 turn, not at once"
        assert has_condition(fighter, Condition.POISONED)
        poisons = _effects_with(game, fighter, Condition.POISONED)
        assert len(poisons) == len(saves)
        first = min(poisons, key=lambda effect: effect.expires_round)
        assert first.expires_round - first.attached_round == ROUNDS_PER_TURN
        assert first.definition.expiry == "death"

        # Later bites now pass their saves, so the first poison alone decides when the fighter dies.
        for effect in game.ledger.active_on(fighter.id, "test_save_ward"):
            game.ledger.release(effect.effect_id, game.registry())
        _force_saves(game, fighter, ALWAYS_PASS)
        for _ in range(ROUNDS_PER_TURN + 1):
            if has_condition(fighter, Condition.DEAD):
                break
            _round(game)
        assert has_condition(fighter, Condition.DEAD)
        record = game.death_records[fighter.id]
        assert record.round == first.expires_round
        assert record.cause == "poison"

    def test_a_passed_save_leaves_the_bite_damage(self):
        game, monster = _battle(4, "black_widow")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_PASS)
        names = _effect_attacks(monster, "poison")

        events = _until_hit(game, fighter, names)

        saves = _saves(events, fighter.id, "death")
        assert len(saves) == len(_hits(events, fighter.id, names))
        assert all(save.code == "combat.save.passed" for save in saves)
        damage = [event for event in events if isinstance(event, DamageDealtEvent) and event.target_id == fighter.id]
        assert damage and all(event.amount > 0 for event in damage), "the save doesn't affect the bite's damage"
        assert not has_condition(fighter, Condition.POISONED)
        assert not _effects_with(game, fighter, Condition.POISONED)

    def test_the_crab_spiders_save_bonus_applies(self):
        game, monster = _battle(4, "crab_spider")
        fighter = game.party.members[0]

        events = _until_hit(game, fighter, _effect_attacks(monster, "poison"))

        saves = _saves(events, fighter.id, "death")
        assert saves
        first = saves[0]
        assert first.modifier == 2, "save versus poison with a +2 bonus"
        if first.code == "combat.save.failed":
            assert has_condition(fighter, Condition.POISONED)
            effect = min(_effects_with(game, fighter, Condition.POISONED), key=lambda effect: effect.attached_round)
            rounds = effect.expires_round - effect.attached_round
            assert rounds % ROUNDS_PER_TURN == 0 and 1 <= rounds // ROUNDS_PER_TURN <= 4, "death in 1d4 turns"


# ---------------------------------------------------------------------- disease


class TestDisease:
    """A mummy's touch gives mummy rot to anyone it hits, with no saving throw."""

    def test_a_hit_infects_with_mummy_rot(self):
        game, monster = _battle(4, "mummy")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_PASS)

        _until_hit(game, fighter, _effect_attacks(monster, "disease"))

        assert has_condition(fighter, Condition.DISEASED), "no save, however good, stops the disease"
        effects = _effects_with(game, fighter, Condition.DISEASED)
        assert effects and all(effect.expires_round is None for effect in effects), "only magic removes it"
        assert fighter.current_hp < fighter.max_hp

        # Magical healing is ineffective.
        blocked = apply_healing(fighter, 5)
        assert blocked[0].code == "combat.healing.blocked"
        # Natural healing is ten times slower: one recovery in ten full days of rest.
        stream = RngStreams(master_seed=1).get(EFFECTS_STREAM)
        days = [natural_healing(fighter, stream, ledger=game.ledger) for _ in range(10)]
        assert [bool(events) for events in days] == [False] * 9 + [True]


# ---------------------------------------------------------------------- petrification


class TestPetrifyingTouch:
    """A hit by a petrifying attack makes the defender save versus petrification or turn to stone."""

    def _setup(self, template_id, bonus):
        game, monster = _battle(4, template_id)
        fighter = game.party.members[0]
        _force_saves(game, fighter, bonus)
        if template_id == "basilisk":
            # Averting the eyes keeps the gaze out of it, so only the bite can petrify.
            _avert_eyes(game, fighter)
        names = _effect_attacks(monster, "petrification") - {"gaze"}
        return game, fighter, names

    @pytest.mark.parametrize("template_id", ["cockatrice", "basilisk"])
    def test_a_failed_save_turns_the_defender_to_stone(self, template_id):
        game, fighter, names = self._setup(template_id, ALWAYS_FAIL)

        events = _until_hit(game, fighter, names)

        saves = _saves(events, fighter.id, "paralysis")
        assert saves and saves[0].code == "combat.save.failed", "save versus petrification"
        assert has_condition(fighter, Condition.PETRIFIED)
        effects = _effects_with(game, fighter, Condition.PETRIFIED)
        assert effects and all(effect.expires_round is None for effect in effects), "stone lasts until undone"

    @pytest.mark.parametrize("template_id", ["cockatrice", "basilisk"])
    def test_a_passed_save_leaves_the_defender_flesh(self, template_id):
        game, fighter, names = self._setup(template_id, ALWAYS_PASS)

        events = _until_hit(game, fighter, names)
        for _ in range(3):
            events += _round(game)

        saves = _saves(events, fighter.id, "paralysis")
        assert saves and len(saves) == len(_hits(events, fighter.id, names)), "one save per petrifying hit, no more"
        assert all(save.code == "combat.save.passed" for save in saves)
        assert not has_condition(fighter, Condition.PETRIFIED)


# ---------------------------------------------------------------------- gaze


class TestGaze:
    """A petrifying gaze reaches every character in melee with the gazer, every round, with no attack roll."""

    @pytest.mark.parametrize("template_id", ["basilisk", "medusa"])
    def test_the_gaze_reaches_each_character_in_melee_every_round(self, template_id):
        members = [_character("Hild"), _character("Brakk"), _character("Sable")]
        game, monster = _battle(4, template_id, members)
        front, rear = game.party.members[:2], game.party.members[2]
        for member in game.party.members:
            _force_saves(game, member, ALWAYS_PASS)
        petrifying = _effect_attacks(monster, "petrification") - {"gaze"}

        for _ in range(4):
            assert game.encounter.groups[0].distance_feet == 5, "precondition: the gazer is in melee"
            events = _round(game)
            for member in front:
                gaze_saves = len(_saves(events, member.id, "paralysis")) - len(_hits(events, member.id, petrifying))
                assert gaze_saves == 1, f"{member.id} meets the gaze once this round"
            assert not _attack_rolls(events, rear.id), "precondition: the rear rank isn't in melee"
            assert not _saves(events, rear.id), "a character out of melee doesn't meet the gaze"
            gaze_rolls = [
                event for event in events if isinstance(event, AttackRolledEvent) and event.attack_name == "gaze"
            ]
            assert not gaze_rolls, "a gaze takes no attack roll"

    def test_a_failed_save_against_the_gaze_turns_the_defender_to_stone(self):
        game, monster = _battle(4, "medusa")
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_FAIL)
        assert "petrification" not in {
            effect for routine in monster.template.attacks for attack in routine.attacks for effect in attack.effects
        }, "precondition: only the medusa's gaze can petrify"

        events = _round(game)

        assert _saves(events, fighter.id, "paralysis")
        assert has_condition(fighter, Condition.PETRIFIED)

    def test_averting_the_eyes_avoids_the_gaze(self):
        game, _ = _battle(4, "medusa", [_character("Hild"), _character("Brakk")])
        averter, looker = game.party.members
        for member in (averter, looker):
            _force_saves(game, member, ALWAYS_FAIL)
        _avert_eyes(game, averter)

        events = []
        for _ in range(3):
            events += _round(game)

        assert has_condition(looker, Condition.PETRIFIED), "the gaze reached the one who looked"
        assert not _saves(events, averter.id, "paralysis")
        assert not has_condition(averter, Condition.PETRIFIED)

    def test_the_gaze_waits_for_melee(self):
        game, _ = _battle(4, "basilisk", distance_feet=60)
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_FAIL)

        events = _round(game)

        assert game.encounter.groups[0].distance_feet > 5, "precondition: the basilisk is still closing"
        assert not _saves(events, fighter.id)
        assert not has_condition(fighter, Condition.PETRIFIED)


# ---------------------------------------------------------------------- misses


MISS_SEEDS = {
    "ghoul": 22,
    "wight": 4,
    "black_widow": 4,
    "pit_viper": 4,
    "mummy": 4,
    "cockatrice": 4,
}


class TestMisses:
    """A miss applies nothing, however badly the defender would have saved."""

    @pytest.mark.parametrize("template_id", sorted(MISS_SEEDS))
    def test_a_round_of_misses_applies_nothing(self, template_id):
        game, _ = _battle(MISS_SEEDS[template_id], template_id)
        fighter = game.party.members[0]
        _force_saves(game, fighter, ALWAYS_FAIL)
        level = fighter.level

        events = _round(game)

        assert _attack_rolls(events, fighter.id), "precondition: the monster attacked"
        assert not _hits(events, fighter.id), "precondition: every attack missed"
        assert not _saves(events, fighter.id)
        assert not [event for event in events if isinstance(event, LevelDrainedEvent)]
        assert not [
            event for event in events if isinstance(event, ConditionGainedEvent) and event.target_id == fighter.id
        ]
        assert not [
            event for event in events if isinstance(event, EffectAttachedEvent) and event.target_ref == fighter.id
        ]
        assert fighter.level == level
        assert not fighter.conditions


# ---------------------------------------------------------------------- determinism


class TestDeterminism:
    """The same seed and the same commands give the same events, effects included."""

    @staticmethod
    def _play(seed: int, template_id: str, members_level: int = 4):
        game, _ = _battle(seed, template_id, [_character("Hild", level=members_level), _character("Brakk")])
        events = []
        for _ in range(5):
            if game.mode is not SessionMode.BATTLE:
                break
            events += _round(game)
        return game, [event.model_dump(mode="json") for event in events]

    @pytest.mark.parametrize("template_id", ["ghoul", "wight", "black_widow", "mummy", "basilisk"])
    def test_replaying_a_battle_gives_the_same_events(self, template_id):
        first_game, first = self._play(4, template_id)
        second_game, second = self._play(4, template_id)

        member_ids = {member.id for member in first_game.party.members}
        applied = [
            event
            for event in first
            if event["event_type"] in ("saving_throw_rolled", "level_drained", "condition_gained")
            and event.get("target_id") in member_ids
        ]
        assert applied, "the battle applied an attack effect: a save, a drain, or a condition"
        assert first == second
        assert first_game.ledger.model_dump(mode="json") == second_game.ledger.model_dump(mode="json")


# ---------------------------------------------------------------------- coverage


def _register_sections() -> list[str]:
    """The adaptations register, split into one text block per Markdown heading."""
    text = (Path(__file__).resolve().parents[1] / "docs" / "adaptations.md").read_text(encoding="utf-8")
    return [section for section in re.split(r"(?m)^(?=#{1,6} )", text) if section.strip()]


def _mentions_kind(section: str, kind: str) -> bool:
    """A section names a kind as `kind` in code, or as its words, like "blood drain" for `blood_drain`."""
    if f"`{kind}`" in section:
        return True
    words = r"\s+".join(re.escape(word) for word in kind.split("_"))
    return re.search(rf"(?i)(?<![\w-]){words}(?![\w-])", section) is not None


def _monster_names(template) -> set[str]:
    """The ways a register entry can name a monster: its id, or its name in either word order."""
    name = re.sub(r"\s*\(.*?\)", "", template.name).strip().lower()
    names = {template.id.replace("_", " "), name}
    if ", " in name:
        head, _, tail = name.partition(", ")
        names.add(f"{tail} {head}")
    return names


def _mentions_monster(section: str, template) -> bool:
    if f"`{template.id}`" in section:
        return True
    return any(re.search(rf"(?i)(?<![\w-]){re.escape(name)}", section) for name in _monster_names(template))


def _applied(template, kind: str) -> bool:
    ability = template.ability(kind)
    return kind in APPLIED_KINDS and ability is not None and not ability.manual


def _gaze_abilities(template) -> list:
    return [
        ability for ability in template.abilities if "gaze" in ability.tag or ability.params.get("vector") == "gaze"
    ]


class TestCoverage:
    """Every attack effect and gaze in the monster catalog is applied by battle or named in the register.

    The kinds in `APPLIED_KINDS` are the ones the tests above show battle applying, and an attack
    effect of one of them counts as applied when the monster's ability for it isn't `manual`. A
    petrifying gaze that isn't `manual` counts as applied for the same reason. Everything else must
    appear in `docs/adaptations.md`, in a section that names both the effect and the monster.
    """

    def test_every_attack_effect_is_applied_or_registered(self):
        sections = _register_sections()
        missing = []
        for template in load_monsters().monsters:
            kinds = sorted(
                {kind for routine in template.attacks for attack in routine.attacks for kind in attack.effects}
            )
            for kind in kinds:
                if _applied(template, kind):
                    continue
                if not any(
                    _mentions_kind(section, kind) and _mentions_monster(section, template) for section in sections
                ):
                    missing.append(f"{template.id}: {kind}")
        assert not missing, f"attack effects battle ignores and the register doesn't name: {missing}"

    def test_every_gaze_is_applied_or_registered(self):
        sections = _register_sections()
        missing = []
        for template in load_monsters().monsters:
            for ability in _gaze_abilities(template):
                if ability.tag == "petrification" and not ability.manual:
                    continue
                named = any(
                    (_mentions_kind(section, ability.tag) or _mentions_kind(section, "gaze"))
                    and _mentions_monster(section, template)
                    for section in sections
                )
                if not named:
                    missing.append(f"{template.id}: {ability.tag}")
        assert not missing, f"gazes battle ignores and the register doesn't name: {missing}"

    def test_the_catalog_has_the_gazes_this_file_relies_on(self):
        catalog = load_monsters()
        for template_id in ("basilisk", "medusa"):
            gazes = _gaze_abilities(catalog.get(template_id))
            assert [ability.tag for ability in gazes] == ["petrification"]
            assert not gazes[0].manual
