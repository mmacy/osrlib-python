"""Acceptance tests for guarding against a petrifying gaze in battle: averting the eyes, a mirror, and surprise.

Since #180 the session runs the basilisk's and the medusa's gaze once a round against the party's front rank, and
`resolve_gaze` spares a creature with the `averted_eyes` condition. No command gave a member that condition, so every
member in melee met the gaze every round. These tests pin the contract that closes the gap, from the OSE SRD's
basilisk and medusa entries.

**The stance.** "Unless averting eyes or using a mirror, characters in melee are affected each round." A member's
`BattleDeclaration.gaze_defense` is `avert_eyes`, `mirror`, or `None`. It goes with any action and lasts the round
it's declared in: from the moment the round is accepted, so it holds whichever side acts first, until the round ends.
Neither stance draws anything or emits an event of its own, and a round with no stance draws exactly as before.

**The penalties.** "Averting eyes: -4 penalty to-hit; the basilisk gains a +2 bonus to attack." The −4 applies to
every attack roll the averting member makes that round, whatever it's aimed at, because the SRD gives it without
naming a target. The +2 applies only to a gazer's attacks on the averting member, because the SRD names the gazer
for it. Other monsters gain nothing.

**The mirror.** "The reflection of a basilisk is harmless. Fighting by looking in a mirror incurs a -1 penalty to
attack." The equipment catalog has a `mirror`, so a member who carries one may fight by it: the gaze passes them by,
their attack rolls that round take −1, and no monster gains a bonus. A member who carries no mirror is refused with
`battle.declaration.no_mirror`. A gazer that sees its own reflection is left to the referee.

**Surprise.** "Characters surprised by a basilisk meet its gaze." In the monsters' free round after they surprise
the party, each basilisk gazes once at every living member, back rank included, at whatever distance the encounter
opened. That gaze takes the place of the front-rank gaze, so no member meets the gaze twice in the round. The
medusa's entry has no such clause, so a medusa's free round is unchanged.

Saving throws are forced where a test needs a particular outcome: a `save_bonus` of +20 makes every save pass and
one of −20 makes every save fail. The penalty tests compare two sessions on the same seed and setup, one with the
stance and one without, and check the difference in each attack's `modifier`, which no draw affects. Each test
asserts its own preconditions, so a test that stops exercising its case fails loudly.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from osrlib.core.abilities import AbilityScore
from osrlib.core.character import Character, party_to_document
from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, EffectDefinition, ModifierSpec, has_condition
from osrlib.core.events import AttackRolledEvent, InitiativeRolledEvent, SavingThrowRolledEvent
from osrlib.core.ruleset import Ruleset
from osrlib.core.spells import MemorizedSpell
from osrlib.core.tables import ReactionResult
from osrlib.crawl import encounter as encounter_module
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    AnyCommand,
    BattleDeclaration,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    GrantItem,
    ResolveBattleRound,
    SessionMode,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_classes
from osrlib.persistence import replay_game, session_state

ALWAYS_PASS = 20
"""A save bonus no d20 can fail with."""

ALWAYS_FAIL = -20
"""A save penalty no d20 can pass with."""

PARALYSIS = "paralysis"
"""The save category of the gaze and of the basilisk's petrifying bite."""

SEEDS = range(1, 80)
"""Seeds the helpers scan for a setup they need, like an encounter neither side is surprised in."""


# ---------------------------------------------------------------------- setup


def _character(name: str, class_id: str = "fighter", *, level: int = 3, hp: int = 400, **fields) -> Character:
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
        **fields,
    )


def _members(*names: str) -> list[Character]:
    return [_character(name) for name in names]


def _adventure() -> Adventure:
    """One level: a 10' corridor two cells long, so the party's front rank is two abreast."""
    level = LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)})
    crypt = DungeonSpec(id="crypt", name="Crypt", levels=(level,))
    return Adventure(name="A", town=TownSpec(name="Threshold", travel_turns={"crypt": 1}), dungeons=(crypt,))


def _session(seed: int, members: list[Character], *, grants=(), equips=()) -> GameSession:
    """A session in the corridor, after `grants` and `equips` as `(member index, item id)` pairs, all by command."""
    game = GameSession.new(Party(members=members), _adventure(), seed=seed)
    for index, item_id in grants:
        assert game.execute(GrantItem(character_id=game.party.members[index].id, item_id=item_id)).accepted
    for index, item_id in equips:
        assert game.execute(EquipItem(character_id=game.party.members[index].id, item_id=item_id)).accepted
    assert game.execute(EnterDungeon(dungeon_id="crypt")).accepted
    return game


def _force_saves(game: GameSession, bonus: int) -> None:
    """Make every save each party member rolls pass (+20) or fail (−20)."""
    for member in game.party.members:
        definition = EffectDefinition(kind="test_save_ward", modifiers=(ModifierSpec(kind="save_bonus", value=bonus),))
        effect, _ = game.ledger.attach(
            definition, member.id, clock=game.clock, allocator=game.allocator, registry=game.registry()
        )
        assert effect is not None


def _battle(
    template_id: str,
    names: tuple[str, ...] = ("Hild",),
    *,
    distance_feet: int = 5,
    grants=(),
    equips=(),
    save_bonus: int | None = ALWAYS_PASS,
    first_seed: int = SEEDS.start,
    members: list[Character] | None = None,
) -> GameSession:
    """A battle against one monster that neither side was surprised in, built by command.

    The helper takes the first seed from `first_seed` on that gives such a battle, so two calls with the same
    arguments give the same session, and a test can play one with a stance and one without.
    """
    for seed in range(first_seed, SEEDS.stop):
        roster = [member.model_copy(deep=True) for member in members] if members else _members(*names)
        game = _session(seed, roster, grants=grants, equips=equips)
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
        if save_bonus is not None:
            _force_saves(game, save_bonus)
        return game
    raise AssertionError(f"precondition: no seed opened a {template_id} battle with neither side surprised")


def _group_id(game: GameSession) -> str:
    return game.encounter.groups[0].id


def _monster_ids(game: GameSession) -> set[str]:
    return {monster_id for group in game.encounter.groups for monster_id in group.monster_ids}


def _declare(member_id: str, action: str = "hold", stance: str | None = None, **fields) -> BattleDeclaration:
    return BattleDeclaration(character_id=member_id, action=action, gaze_defense=stance, **fields)


def _play(game: GameSession, *declared: BattleDeclaration) -> list:
    """Resolve one round: the members in `declared` as given, every other able member holding with no stance."""
    by_id = {declaration.character_id: declaration for declaration in declared}
    able = [member for member in game.party.living_members() if not incapacitated(member)]
    declarations = tuple(by_id.get(member.id, _declare(member.id)) for member in able)
    assert set(by_id) <= {member.id for member in able}, "precondition: every declared member can act"
    result = game.execute(ResolveBattleRound(declarations=declarations))
    assert result.accepted, [rejection.code for rejection in result.rejections]
    return list(result.events)


def _saves(events, target_id: str, category: str | None = PARALYSIS) -> list[SavingThrowRolledEvent]:
    return [
        event
        for event in events
        if isinstance(event, SavingThrowRolledEvent)
        and event.target_id == target_id
        and (category is None or event.category == category)
    ]


def _petrifying_bite_hits(events, defender_id: str) -> int:
    """The basilisk's bites that hit the defender, each of which adds a save versus paralysis of its own."""
    return sum(
        1
        for event in events
        if isinstance(event, AttackRolledEvent)
        and event.defender_id == defender_id
        and event.attack_name == "bite"
        and event.code in ("combat.attack.hit", "combat.attack.auto_hit")
    )


def _gaze_saves(events, member_id: str) -> int:
    """How many times the member met a gaze: saves versus paralysis less the petrifying bites that hit them."""
    return len(_saves(events, member_id)) - _petrifying_bite_hits(events, member_id)


def _modifiers(events, *, attacker_ids, defender_ids) -> list[int]:
    return [
        event.modifier
        for event in events
        if isinstance(event, AttackRolledEvent)
        and event.attacker_id in attacker_ids
        and event.defender_id in defender_ids
        and event.code != "combat.attack.auto_hit"
    ]


def _party_acted_first(game: GameSession, events) -> bool:
    initiative = next(event for event in events if isinstance(event, InitiativeRolledEvent))
    return initiative.order[0] in {member.id for member in game.party.members}


def _state_without_logs(game: GameSession) -> dict:
    """Every state block, the clock and the random number streams among them, without the two logs."""
    state = session_state(game)
    state.pop("command_log")
    state.pop("event_log")
    return state


def _stream_states(game: GameSession) -> dict:
    return {key: state.model_dump(mode="json") for key, state in game.streams.export_states().items()}


# ---------------------------------------------------------------------- the field


class TestTheField:
    """The declaration's new field. These pass before and after the chunk, because the stub ships the field."""

    def test_it_defaults_to_no_stance(self):
        assert BattleDeclaration(character_id="character-0001", action="hold").gaze_defense is None

    @pytest.mark.parametrize("stance", ["avert_eyes", "mirror"])
    def test_it_takes_either_stance_with_any_action(self, stance):
        for action in ("attack", "cast", "turn_undead", "move", "use_item", "hold"):
            assert _declare("character-0001", action, stance).gaze_defense == stance

    def test_it_refuses_anything_else_when_the_declaration_is_built(self):
        with pytest.raises(ValidationError):
            BattleDeclaration(character_id="character-0001", action="hold", gaze_defense="squint")

    def test_a_round_with_a_stance_round_trips_through_its_json(self):
        command = ResolveBattleRound(declarations=(_declare("character-0001", "hold", "avert_eyes"),))
        adapter = TypeAdapter(AnyCommand)
        restored = adapter.validate_python(command.model_dump(mode="json"))
        assert restored == command
        assert restored.declarations[0].gaze_defense == "avert_eyes"


# ---------------------------------------------------------------------- averting spares the gaze


class TestAStanceSparesTheGaze:
    """A member who averts their eyes, or fights by a mirror, never meets the gaze in that round."""

    @pytest.mark.parametrize("template_id", ["basilisk", "medusa"])
    def test_an_averting_member_never_meets_the_gaze_whichever_side_acts_first(self, template_id):
        game = _battle(template_id, ("Hild", "Brakk"))
        hild, brakk = game.party.members
        orders = set()

        for _ in range(6):
            assert game.encounter.groups[0].distance_feet == 5, "precondition: the gazer is in melee"
            events = _play(game, _declare(hild.id, "hold", "avert_eyes"))
            orders.add(_party_acted_first(game, events))
            assert _gaze_saves(events, brakk.id) == 1, "precondition: the gaze ran this round"
            assert _gaze_saves(events, hild.id) == 0, "the averting member met the gaze"
            assert not has_condition(hild, Condition.PETRIFIED)

        assert orders == {True, False}, "precondition: both sides acted first in some round"

    def test_a_failed_save_petrifies_the_one_who_looked_and_not_the_one_who_averted(self):
        game = _battle("medusa", ("Hild", "Brakk"), save_bonus=ALWAYS_FAIL)
        hild, brakk = game.party.members

        events = _play(game, _declare(hild.id, "hold", "avert_eyes"))

        assert _saves(events, brakk.id), "precondition: the gaze reached the member who looked"
        assert has_condition(brakk, Condition.PETRIFIED)
        assert not _saves(events, hild.id)
        assert not has_condition(hild, Condition.PETRIFIED)

    def test_a_mirror_spares_the_gaze(self):
        game = _battle("medusa", ("Hild", "Brakk"), grants=((0, "mirror"),), save_bonus=ALWAYS_FAIL)
        hild, brakk = game.party.members
        assert hild.inventory.carried_item("mirror") is not None, "precondition: Hild carries a mirror"

        for round_number in range(1, 4):
            events = _play(game, _declare(hild.id, "hold", "mirror"))
            if round_number == 1:
                assert _saves(events, brakk.id), "precondition: the gaze reached the member who looked"
                assert has_condition(brakk, Condition.PETRIFIED)
            assert not _saves(events, hild.id), f"the mirror didn't spare Hild in round {round_number}"
            assert not has_condition(hild, Condition.PETRIFIED)

    @pytest.mark.parametrize("stance", ["avert_eyes", "mirror"])
    def test_the_stance_lasts_one_round(self, stance):
        game = _battle("medusa", ("Hild", "Brakk"), grants=((0, "mirror"),))
        hild, brakk = game.party.members

        guarded = _play(game, _declare(hild.id, "hold", stance))
        assert _gaze_saves(guarded, brakk.id) == 1, "precondition: the gaze ran in the guarded round"
        assert _gaze_saves(guarded, hild.id) == 0
        assert not has_condition(hild, Condition.AVERTED_EYES), "the stance ends with its round"

        exposed = _play(game)
        assert _gaze_saves(exposed, brakk.id) == 1, "precondition: the gaze ran in the next round"
        assert _gaze_saves(exposed, hild.id) == 1, "with no stance declared, the member meets the gaze again"

    @pytest.mark.parametrize("action", ["attack", "cast", "use_item", "turn_undead", "hold", "move"])
    def test_the_stance_goes_with_every_action(self, action):
        hild = _character("Hild", "cleric", memorized_spells=(MemorizedSpell(spell_id="cure_light_wounds"),))
        for seed in SEEDS:
            game = _battle(
                "medusa", grants=((0, "potion_of_healing"),), first_seed=seed, members=[hild, _character("Brakk")]
            )
            hild_id, brakk_id = (member.id for member in game.party.members)
            fields = {
                "attack": {"target_group_id": _group_id(game)},
                "cast": {"spell_id": "cure_light_wounds", "spell_mode": "heal", "targets": (hild_id,)},
                "use_item": {"item_id": game.party.members[0].inventory.carried_item("potion_of_healing").instance_id},
                "turn_undead": {},
                "hold": {},
                "move": {"move": "fighting_withdrawal"},
            }[action]
            others = [_declare(brakk_id, "move", move="fighting_withdrawal")] if action == "move" else []
            events = _play(game, _declare(hild_id, action, "avert_eyes", **fields), *others)
            if _gaze_saves(events, brakk_id) != 1:
                # The party withdrew out of reach before the medusa acted. Try another seed.
                assert action == "move", "precondition: the gaze ran this round"
                continue
            assert _gaze_saves(events, hild_id) == 0, f"averting with {action} met the gaze"
            return
        raise AssertionError("precondition: no seed let the medusa act before the party withdrew")


# ---------------------------------------------------------------------- not averting is unchanged


class TestNotAvertingIsUnchanged:
    """A member with no stance meets the gaze and keeps their numbers, whatever the members beside them do."""

    def test_a_looker_beside_an_averter_still_meets_the_gaze(self):
        game = _battle("basilisk", ("Hild", "Brakk"))
        hild, brakk = game.party.members

        for _ in range(3):
            events = _play(game, _declare(hild.id, "hold", "avert_eyes"))
            assert _gaze_saves(events, hild.id) == 0
            assert _gaze_saves(events, brakk.id) == 1

    def test_a_looker_beside_an_averter_keeps_their_attack_numbers(self):
        """A guard: the averter's stance changes nothing about the member beside them, or about attacks on them."""
        plain = _battle("basilisk", ("Hild", "Brakk"))
        guarded = _battle("basilisk", ("Hild", "Brakk"))
        hild, brakk = (member.id for member in plain.party.members)
        basilisk = _monster_ids(plain)

        plain_events, guarded_events = [], []
        for _ in range(4):
            attack = _declare(brakk, "attack", target_group_id=_group_id(plain))
            plain_events += _play(plain, attack)
            guarded_events += _play(guarded, attack, _declare(hild, "hold", "avert_eyes"))

        plain_on_brakk = _modifiers(plain_events, attacker_ids=basilisk, defender_ids={brakk})
        assert plain_on_brakk, "precondition: the basilisk attacked Brakk"
        assert _modifiers(guarded_events, attacker_ids=basilisk, defender_ids={brakk}) == plain_on_brakk
        plain_by_brakk = _modifiers(plain_events, attacker_ids={brakk}, defender_ids=basilisk)
        assert len(plain_by_brakk) == 4, "precondition: Brakk attacked every round"
        assert _modifiers(guarded_events, attacker_ids={brakk}, defender_ids=basilisk) == plain_by_brakk


# ---------------------------------------------------------------------- the penalties


def _paired(template_id: str, *, distance_feet: int = 5, grants=(), equips=()) -> tuple[GameSession, GameSession]:
    """Two identical one-member battles, one to play with a stance and one without."""
    sessions = tuple(_battle(template_id, distance_feet=distance_feet, grants=grants, equips=equips) for _ in range(2))
    assert _state_without_logs(sessions[0]) == _state_without_logs(sessions[1]), "precondition: identical setups"
    return sessions


class TestThePenalties:
    """Averting costs −4 on every attack roll and gives a gazer +2 against the averter. A mirror costs −1."""

    def test_averting_costs_minus_four_and_the_gazer_gains_plus_two_for_that_round(self):
        plain, guarded = _paired("basilisk")
        hild = plain.party.members[0].id
        basilisk = _monster_ids(plain)
        attack = _declare(hild, "attack", target_group_id=_group_id(plain))

        plain_first = _play(plain, attack)
        guarded_first = _play(guarded, _declare(hild, "attack", "avert_eyes", target_group_id=_group_id(guarded)))

        mine = _modifiers(plain_first, attacker_ids={hild}, defender_ids=basilisk)
        theirs = _modifiers(plain_first, attacker_ids=basilisk, defender_ids={hild})
        assert len(mine) == 1 and len(theirs) == 1, "precondition: Hild and the basilisk each attacked once"
        assert _modifiers(guarded_first, attacker_ids={hild}, defender_ids=basilisk) == [mine[0] - 4]
        assert _modifiers(guarded_first, attacker_ids=basilisk, defender_ids={hild}) == [theirs[0] + 2]

        # The next round has no stance, and the numbers are back.
        plain_next = _play(plain, attack)
        guarded_next = _play(guarded, attack)
        assert _modifiers(guarded_next, attacker_ids={hild}, defender_ids=basilisk) == _modifiers(
            plain_next, attacker_ids={hild}, defender_ids=basilisk
        )
        assert _modifiers(guarded_next, attacker_ids=basilisk, defender_ids={hild}) == _modifiers(
            plain_next, attacker_ids=basilisk, defender_ids={hild}
        )

    def test_the_minus_four_applies_to_an_attack_on_any_monster_and_no_other_monster_gains(self):
        plain, guarded = _paired("ogre")
        hild = plain.party.members[0].id
        ogre = _monster_ids(plain)

        plain_events = _play(plain, _declare(hild, "attack", target_group_id=_group_id(plain)))
        guarded_events = _play(guarded, _declare(hild, "attack", "avert_eyes", target_group_id=_group_id(guarded)))

        mine = _modifiers(plain_events, attacker_ids={hild}, defender_ids=ogre)
        theirs = _modifiers(plain_events, attacker_ids=ogre, defender_ids={hild})
        assert len(mine) == 1 and theirs, "precondition: Hild and the ogre each attacked"
        assert _modifiers(guarded_events, attacker_ids={hild}, defender_ids=ogre) == [mine[0] - 4]
        assert _modifiers(guarded_events, attacker_ids=ogre, defender_ids={hild}) == theirs, "only a gazer gains +2"

    def test_the_minus_four_applies_to_a_missile_attack(self):
        plain, guarded = _paired("ogre", distance_feet=60, grants=((0, "short_bow"),), equips=((0, "short_bow"),))
        hild = plain.party.members[0].id
        ogre = _monster_ids(plain)

        plain_events = _play(plain, _declare(hild, "attack", target_group_id=_group_id(plain)))
        guarded_events = _play(guarded, _declare(hild, "attack", "avert_eyes", target_group_id=_group_id(guarded)))

        shots = [event for event in plain_events if isinstance(event, AttackRolledEvent) and event.attacker_id == hild]
        assert len(shots) == 1 and shots[0].attack_name == "Short bow", "precondition: Hild shot her bow"
        assert _modifiers(guarded_events, attacker_ids={hild}, defender_ids=ogre) == [shots[0].modifier - 4]

    def test_a_mirror_costs_minus_one_and_gives_the_gazer_nothing(self):
        plain, guarded = _paired("basilisk", grants=((0, "mirror"),))
        hild = plain.party.members[0].id
        basilisk = _monster_ids(plain)

        plain_events = _play(plain, _declare(hild, "attack", target_group_id=_group_id(plain)))
        guarded_events = _play(guarded, _declare(hild, "attack", "mirror", target_group_id=_group_id(guarded)))

        mine = _modifiers(plain_events, attacker_ids={hild}, defender_ids=basilisk)
        theirs = _modifiers(plain_events, attacker_ids=basilisk, defender_ids={hild})
        assert len(mine) == 1 and len(theirs) == 1, "precondition: Hild and the basilisk each attacked once"
        assert _modifiers(guarded_events, attacker_ids={hild}, defender_ids=basilisk) == [mine[0] - 1]
        assert _modifiers(guarded_events, attacker_ids=basilisk, defender_ids={hild}) == theirs


# ---------------------------------------------------------------------- surprise


def _surprised(
    template_id: str, distance_feet: int, names=("Hild", "Brakk", "Sable"), *, save_bonus: int = ALWAYS_PASS
) -> tuple[GameSession, list]:
    """A party surprised by one attacking monster, and the opening events, which hold the monster's free round.

    The party's front rank is Hild and Brakk. Sable is in the back rank.
    """
    for seed in SEEDS:
        game = _session(seed, _members(*names))
        _force_saves(game, save_bonus)
        instances = game.spawn(template_id, 1)
        events = encounter_module.start_encounter(
            game,
            groups=[(template_id, instances)],
            kind="spawned",
            distance_feet=distance_feet,
            pinned_stance=ReactionResult.ATTACKS,
            monsters_aware=True,
        )
        if not any(event.code == "encounter.started" and event.party_surprised for event in events):
            continue
        codes = [event.code for event in events]
        assert "battle.started" in codes, "precondition: the monster attacked at once"
        assert game.battle is None or game.battle.round == 1, "precondition: the monster's free round ran"
        return game, events
    raise AssertionError(f"precondition: no seed let a {template_id} surprise the party")


class TestTheSurpriseGaze:
    """Characters a basilisk surprises meet its gaze in its free round, at any distance and in any rank."""

    @pytest.mark.parametrize("distance_feet", [60, 5])
    def test_a_basilisk_that_surprises_the_party_gazes_once_at_every_member(self, distance_feet):
        game, events = _surprised("basilisk", distance_feet)

        for member in game.party.members:
            assert _gaze_saves(events, member.id) == 1, f"{member.id} meets the basilisk's gaze once"
        gaze_rolls = [event for event in events if isinstance(event, AttackRolledEvent) and event.attack_name == "gaze"]
        assert not gaze_rolls, "a gaze takes no attack roll"

    def test_the_surprise_gaze_petrifies_on_a_failed_save(self):
        game, events = _surprised("basilisk", 60, save_bonus=ALWAYS_FAIL)

        for member in game.party.members:
            assert _saves(events, member.id), f"precondition: {member.id} met the gaze"
            assert has_condition(member, Condition.PETRIFIED)

    def test_a_medusa_that_surprises_the_party_from_afar_doesnt_gaze(self):
        """A guard: the medusa's entry has no surprise clause, so beyond 5' her free round reaches no one."""
        game, events = _surprised("medusa", 60)

        assert game.encounter.groups[0].distance_feet > 5, "precondition: the medusa is still out of melee"
        for member in game.party.members:
            assert not _saves(events, member.id, None)

    def test_a_medusa_that_surprises_the_party_in_melee_gazes_at_the_front_rank(self):
        """A guard: at 5' the medusa's free round has the ordinary front-rank gaze."""
        game, events = _surprised("medusa", 5)
        hild, brakk, sable = game.party.members

        assert len(_saves(events, hild.id)) == 1
        assert len(_saves(events, brakk.id)) == 1
        assert not _saves(events, sable.id, None)


# ---------------------------------------------------------------------- rejected rounds


class TestARefusedRoundCostsNothing:
    """Validation stays a pure pre-phase: a refused round draws nothing, takes no time, and leaves no stance."""

    def test_a_round_refused_for_another_declaration_leaves_no_stance(self):
        """A guard: the stance is granted only once the whole round is accepted."""
        game = _battle("medusa", ("Hild", "Brakk"))
        hild, brakk = game.party.members
        before = _state_without_logs(game)

        result = game.execute(
            ResolveBattleRound(
                declarations=(
                    _declare(hild.id, "hold", "avert_eyes"),
                    _declare(brakk.id, "attack", target_group_id="no-such-group"),
                )
            )
        )

        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["battle.declaration.unknown_group"]
        assert not result.events
        assert not has_condition(hild, Condition.AVERTED_EYES)
        assert _state_without_logs(game) == before

    def test_a_mirror_stance_from_a_member_without_a_mirror_is_refused(self):
        game = _battle("medusa", ("Hild", "Brakk"), grants=((1, "mirror"),))
        hild, brakk = game.party.members
        assert hild.inventory.carried_item("mirror") is None, "precondition: Hild carries no mirror"
        before = _state_without_logs(game)

        result = game.execute(
            ResolveBattleRound(declarations=(_declare(hild.id, "hold", "mirror"), _declare(brakk.id, "hold", "mirror")))
        )

        assert not result.accepted
        assert [rejection.code for rejection in result.rejections] == ["battle.declaration.no_mirror"]
        assert not result.events
        assert _state_without_logs(game) == before

    def test_a_mirror_stance_from_a_member_with_a_mirror_is_accepted(self):
        """A guard: carrying a mirror, not wielding one, is all the stance needs."""
        game = _battle("medusa", ("Hild", "Brakk"), grants=((0, "mirror"),))
        hild, _ = game.party.members
        assert hild.inventory.carried_item("mirror") is not None, "precondition: Hild carries a mirror"

        _play(game, _declare(hild.id, "attack", "mirror", target_group_id=_group_id(game)))


# ---------------------------------------------------------------------- determinism


class TestDeterminism:
    """A stance draws nothing and emits nothing of its own, and a battle with stances replays exactly."""

    @pytest.mark.parametrize("stance", ["avert_eyes", "mirror"])
    def test_a_stance_that_spares_no_gaze_and_costs_no_attack_changes_nothing(self, stance):
        """A guard: a back-rank member who holds with a stance against an ogre leaves every draw and event as it was."""
        names = ("Hild", "Brakk", "Sable")
        plain = _battle("ogre", names, grants=((2, "mirror"),))
        guarded = _battle("ogre", names, grants=((2, "mirror"),))
        sable = plain.party.members[2].id

        for _ in range(3):
            plain_events = _play(plain)
            guarded_events = _play(guarded, _declare(sable, "hold", stance))
            assert [event.model_dump(mode="json") for event in guarded_events] == [
                event.model_dump(mode="json") for event in plain_events
            ]
            assert _stream_states(guarded) == _stream_states(plain)
        assert not has_condition(guarded.party.members[2], Condition.AVERTED_EYES)

    def test_a_battle_with_stances_replays_from_its_command_log(self):
        members = [_character("Hild"), _character("Brakk")]
        party_document = party_to_document(members)
        game = _battle("medusa", members=members, grants=((0, "mirror"),), save_bonus=None)
        hild, brakk = (member.id for member in game.party.members)

        spared = False
        for stance in ("avert_eyes", "mirror", None, "avert_eyes"):
            if game.mode is not SessionMode.BATTLE:
                break
            able = {member.id for member in game.party.living_members() if not incapacitated(member)}
            declared = [_declare(hild, "attack", stance, target_group_id=_group_id(game))] if hild in able else []
            events = _play(game, *declared)
            if stance is not None and hild in able and _saves(events, brakk):
                assert not _saves(events, hild), "precondition: the stance spared Hild the gaze Brakk met"
                spared = True
        assert spared, "precondition: some round's stance spared Hild a gaze"

        replayed = replay_game(game.master_seed, party_document, game.adventure, Ruleset(), game.command_log)

        assert [event.model_dump(mode="json") for event in replayed.event_log] == [
            event.model_dump(mode="json") for event in game.event_log
        ]
        assert _stream_states(replayed) == _stream_states(game)
        assert session_state(replayed) == session_state(game)
