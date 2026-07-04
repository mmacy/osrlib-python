"""Generate the Phase 4 milestone golden: the scripted delve.

The driver plays the milestone adventure adaptively — retrying tinder, re-entering
the pit hall until it springs, fighting whatever the wandering dice send — and the
recorded accepted-command log becomes the golden script. A master seed qualifies
only when every milestone beat landed: town outfitting and travel, two levels
explored with a forced door, a listen, a search finding the secret door, a torch
burning out and relit, the sprung pit, the keyed goblin battle with a morale rout,
wandering encounters fought through the machine (an area *fire ball* under the
footprint rule and a machine-detected disruption among them), the crypt's
turn-undead declaration routing skeletons, a kennel flight with dropped treasure
and a successful distraction, another kennel's 30-round exhaustion terminal, the
night camp with re-preparation, and the return to town — with save checkpoints
mid-exploration, mid-battle, and at the end.

Run `uv run python tests/generate_phase4_goldens.py` and explain any golden change
in the commit message.
"""

import json
from pathlib import Path

from crawl_fixtures import build_milestone_adventure, build_milestone_party
from osrlib.core.character import party_to_document
from osrlib.core.combat import incapacitated
from osrlib.core.effects import Condition, has_condition
from osrlib.core.events import Event
from osrlib.crawl.commands import (
    BattleDeclaration,
    Command,
    DropItems,
    EngageBattle,
    EnterDungeon,
    EquipItem,
    Evade,
    ForceDoor,
    GrantCoins,
    LightSource,
    ListenAtDoor,
    MoveParty,
    OpenDoor,
    PrepareSpells,
    PurchaseEquipment,
    ReorderParty,
    ResolveBattleRound,
    Rest,
    Search,
    SessionMode,
    TakeTreasure,
    TravelToTown,
    UseStairs,
    Wait,
    WedgeDoor,
)
from osrlib.crawl.dungeon import Coins, Direction
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message

GOLDEN_PATH = Path(__file__).parent / "goldens" / "phase4_delve.json"


def exploration_x(session) -> int:
    return session.dungeon_state.location.position[0]


REQUIRED_CODES = (
    "exploration.item.acquired",
    "exploration.location.entered",
    "exploration.door.forced",
    "exploration.listen.heard",
    "exploration.search.found",
    "exploration.light.expired",
    "exploration.trap.sprung",
    "exploration.wandering.checked",
    "encounter.started",
    "encounter.surprise.rolled",
    "encounter.reaction.rolled",
    "battle.started",
    "battle.spell.declared",
    "magic.cast.disrupted",
    "combat.morale.broke",
    "battle.side.fled",
    "magic.turning.turned",
    "battle.monster.defeated",
    "encounter.pursuit.distracted",
    "encounter.exhaustion.gained",
    "exploration.rest.rested",
    "magic.memorize.prepared",
    "battle.ended.victory",
)


class Beats:
    """Track which milestone beats the run has hit."""

    def __init__(self) -> None:
        self.codes: set[str] = set()
        self.wandering_battle_won = False

    def note(self, events) -> None:
        for event in events:
            code = getattr(event, "code", None)
            if code:
                self.codes.add(code)

    def satisfied(self) -> list[str]:
        missing = [code for code in REQUIRED_CODES if code not in self.codes]
        if not self.wandering_battle_won:
            missing.append("<wandering battle won>")
        return missing


class Driver:
    """Play the delve adaptively, recording checkpoints."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        members = build_milestone_party(seed)
        self.party_document = party_to_document(members)
        self.session = GameSession.new(Party(members=members), build_milestone_adventure(), seed=seed)
        self.beats = Beats()
        self.checkpoints: dict[str, int] = {}

    class SeedRejected(Exception):
        pass

    def x(self, command: Command, *, expect: bool = True):
        result = self.session.execute(command)
        if expect and not result.accepted:
            raise self.SeedRejected(f"{command.command_type} rejected: {[r.code for r in result.rejections]}")
        self.beats.note(result.events)
        if (
            self.session.mode in (SessionMode.ENCOUNTER, SessionMode.BATTLE)
            and self.session.encounter is not None
            and self.session.encounter.kind == "wandering"
            and command.command_type not in ("evade", "wait", "drop_items")
        ):
            # A wandering encounter interrupted the script: fight it out. Keyed
            # encounters are the script's own beats and stay untouched.
            self.fight(wandering=True)
        self.require_alive()
        return result

    def require_alive(self) -> None:
        if self.session.mode is SessionMode.GAME_OVER:
            raise self.SeedRejected("TPK")
        for member in self.session.party.members:
            if has_condition(member, Condition.DEAD):
                raise self.SeedRejected(f"{member.name} died")

    def light_up(self) -> None:
        lit, _ = self.session.party_light()
        attempts = 0
        while not lit:
            attempts += 1
            if attempts > 25:
                raise self.SeedRejected("could not light a torch")
            self.x(LightSource(character_id="character-0001", item_id="torch"))
            lit, _ = self.session.party_light()

    def move(self, direction: Direction) -> None:
        self.light_up()
        self.x(MoveParty(direction=direction))

    # ------------------------------------------------------------------ combat

    def fight(self, *, wandering: bool = False, checkpoint: str | None = None) -> None:
        session = self.session
        if session.mode is SessionMode.ENCOUNTER:
            result = session.execute(EngageBattle())
            self.beats.note(result.events)
            if not result.accepted:
                raise self.SeedRejected("engage rejected")
        rounds = 0
        while session.mode is SessionMode.BATTLE:
            rounds += 1
            if rounds > 20:
                raise self.SeedRejected("battle dragged past twenty rounds")
            declarations = self.build_declarations(rounds, allow_area=wandering)
            result = session.execute(ResolveBattleRound(declarations=declarations))
            if not result.accepted:
                raise self.SeedRejected(f"round rejected: {[r.code for r in result.rejections]}")
            self.beats.note(result.events)
            if checkpoint and checkpoint not in self.checkpoints and session.mode is SessionMode.BATTLE:
                self.checkpoints[checkpoint] = len(session.command_log)
            self.require_alive()
        if session.mode is SessionMode.ENCOUNTER:
            # The monsters fled into a pursuit the party doesn't want: let them go
            # is not an option — run the pursuit out.
            while session.mode is SessionMode.ENCOUNTER:
                self.x(Wait(), expect=True)
        if wandering:
            self.beats.wandering_battle_won = True

    def build_declarations(self, round_number: int, *, allow_area: bool = False):
        session = self.session
        groups = [
            group
            for group in session.encounter.groups
            if not group.fled
            and not group.surrendered
            and any(not has_condition(session.monsters[mid], Condition.DEAD) for mid in group.monster_ids)
        ]
        target = min(groups, key=lambda group: group.distance_feet) if groups else None
        declarations = []
        undead = target is not None and "undead" in session.monsters[target.monster_ids[0]].template.categories
        from osrlib.crawl.battle import _monster_pool, _party_front_rank

        front = _party_front_rank(session)
        for member in session.party.living_members():
            if incapacitated(member):
                continue
            declaration = None
            if target is None:
                declaration = BattleDeclaration(character_id=member.id, action="hold")
                declarations.append(declaration)
                continue
            pool = _monster_pool(session, target)
            if member.class_id == "cleric" and undead and round_number == 1:
                declaration = BattleDeclaration(character_id=member.id, action="turn_undead")
            elif member.class_id == "cleric":
                wounded = next(
                    (
                        ally
                        for ally in session.party.living_members()
                        if ally.current_hp * 2 <= ally.max_hp and not incapacitated(ally)
                    ),
                    None,
                )
                if wounded is not None and any(
                    copy.spell_id == "cure_light_wounds" for copy in member.memorized_spells
                ):
                    declaration = BattleDeclaration(
                        character_id=member.id,
                        action="cast",
                        spell_id="cure_light_wounds",
                        spell_mode="heal",
                        targets=(wounded.id,),
                    )
            elif member.class_id == "magic_user":
                declaration = self.magic_user_declaration(member, target, pool, allow_area)
            if declaration is None:
                weapon = next(
                    (item.template.id for item in member.inventory.wielded if item.template.item_type == "weapon"),
                    None,
                )
                if member in front and target.distance_feet <= 5 and pool:
                    declaration = BattleDeclaration(
                        character_id=member.id, action="attack", target_group_id=target.id, weapon_id=weapon
                    )
                elif member is front[0] and target.distance_feet > 5:
                    declaration = BattleDeclaration(
                        character_id=member.id, action="move", move="close", target_group_id=target.id
                    )
                else:
                    declaration = BattleDeclaration(character_id=member.id, action="hold")
            declarations.append(declaration)
        return tuple(declarations)

    def magic_user_declaration(self, member, target, pool, allow_area: bool):
        session = self.session
        memorized = [copy.spell_id for copy in member.memorized_spells]
        living = [mid for mid in target.monster_ids if not has_condition(session.monsters[mid], Condition.DEAD)]
        # The area beat lands in the wandering fight — the keyed goblins must be
        # fought by hand so their morale gets a chance to break. Fire ball only
        # beyond melee (friendly fire would catch the front rank at 5').
        if allow_area and "fire_ball" in memorized and len(living) >= 2 and target.distance_feet > 5:
            return BattleDeclaration(
                character_id=member.id,
                action="cast",
                spell_id="fire_ball",
                spell_mode="damage",
                target_group_id=target.id,
            )
        # Missiles wait for melee range: a cast declared while blows are landing
        # is the disruption window the milestone needs.
        if "magic_missile" in memorized and pool and target.distance_feet <= 5:
            return BattleDeclaration(
                character_id=member.id,
                action="cast",
                spell_id="magic_missile",
                spell_mode="missiles",
                targets=(pool[0].id,),
            )
        return None

    # ------------------------------------------------------------------ the script

    def run(self) -> None:
        session = self.session
        # Town outfitting: coins from the sponsor, then the shopping trip.
        shopping = {
            "character-0001": (
                "sword",
                "chainmail",
                "torch",
                "torch",
                "torch",
                "torch",
                "tinder_box",
                "iron_spikes",
                "rations_standard",
                "waterskin",
            ),
            "character-0002": ("sword", "chainmail", "rations_standard", "waterskin"),
            "character-0003": ("sword", "leather", "rations_standard", "waterskin"),
            "character-0004": ("mace", "chainmail", "rations_standard", "waterskin"),
            "character-0005": ("dagger", "rations_standard", "waterskin"),
        }
        for character_id, items in shopping.items():
            self.x(GrantCoins(character_id=character_id, coins=Coins(gp=150)))
            self.x(PurchaseEquipment(character_id=character_id, item_ids=items))
        for character_id, items in shopping.items():
            for item_id in items:
                if item_id in ("sword", "mace", "dagger", "chainmail", "leather"):
                    self.x(EquipItem(character_id=character_id, item_id=item_id))
        self.x(EnterDungeon(dungeon_id="halls"))
        self.light_up()

        # The pit hall: re-enter until the 2-in-6 spring lands.
        self.move(Direction.EAST)
        for _ in range(8):
            self.move(Direction.SOUTH)
            if "exploration.trap.sprung" in self.beats.codes:
                break
            self.move(Direction.NORTH)
        if "exploration.trap.sprung" not in self.beats.codes:
            raise self.SeedRejected("the pit never sprang")
        self.move(Direction.NORTH)
        self.checkpoints["mid_exploration"] = len(session.command_log)

        # The guard room: listen, force, fight the goblins, loot the coffer.
        self.move(Direction.EAST)
        self.move(Direction.EAST)
        for listener in (
            "character-0003",
            "character-0002",
            "character-0004",
            "character-0001",
            "character-0005",
        ):
            self.light_up()
            self.x(ListenAtDoor(direction=Direction.SOUTH, character_id=listener))
            if "exploration.listen.heard" in self.beats.codes:
                break
        if "exploration.listen.heard" not in self.beats.codes:
            raise self.SeedRejected("nobody heard the goblins")
        for _ in range(12):
            self.x(ForceDoor(direction=Direction.SOUTH, character_id="character-0001"))
            if "exploration.door.forced" in self.beats.codes:
                break
        if "exploration.door.forced" not in self.beats.codes:
            raise self.SeedRejected("the door would not budge")
        self.x(WedgeDoor(direction=Direction.SOUTH))  # spike it open for the trip home
        self.light_up()
        self.x(MoveParty(direction=Direction.SOUTH))
        if session.mode is not SessionMode.BATTLE and session.mode is not SessionMode.ENCOUNTER:
            raise self.SeedRejected("the guard room was empty")
        self.fight(checkpoint="mid_battle")
        if "combat.morale.broke" not in self.beats.codes:
            raise self.SeedRejected("the goblins never routed")
        self.move(Direction.EAST)
        self.light_up()
        self.x(TakeTreasure(feature_id="coffer"))

        # The secret door to the stairs.
        for searcher in ("character-0002", "character-0005", "character-0001", "character-0003", "character-0004"):
            self.light_up()
            self.x(Search(character_id=searcher, kind="secret_doors"))
            if "exploration.search.found" in self.beats.codes:
                break
        if "exploration.search.found" not in self.beats.codes:
            raise self.SeedRejected("the secret door stayed hidden")
        # The magic-user takes the van from here: a blow must be able to find her
        # mid-incantation for the disruption beat.
        self.x(
            ReorderParty(
                order=(
                    "character-0005",
                    "character-0001",
                    "character-0002",
                    "character-0003",
                    "character-0004",
                )
            )
        )
        self.light_up()
        self.x(OpenDoor(direction=Direction.EAST))
        self.x(WedgeDoor(direction=Direction.EAST))  # keep the way home open
        self.move(Direction.EAST)
        self.move(Direction.EAST)
        self.x(UseStairs())

        # Level 2: the crypt — the turn-undead declaration routs the skeletons.
        self.move(Direction.EAST)
        self.light_up()
        self.x(OpenDoor(direction=Direction.SOUTH))
        self.x(MoveParty(direction=Direction.SOUTH))
        if session.encounter is None:
            raise self.SeedRejected("the crypt was empty")
        self.fight()
        if "magic.turning.turned" not in self.beats.codes:
            raise self.SeedRejected("the skeletons were not turned")
        self.move(Direction.NORTH)

        # The kennels: the first that opens as a clean encounter is fled with
        # dropped treasure until the goblins stop for the coin; the second runs
        # the pursuit to the 30-round exhaustion terminal. A kennel that opens
        # straight into battle (the party was surprised) is simply fought.
        distraction_done = False
        exhaustion_done = False
        for column in (3, 5, 6):
            while exploration_x(session) < column:
                self.move(Direction.EAST)
            self.light_up()
            self.x(MoveParty(direction=Direction.SOUTH))
            if session.encounter is None:
                raise self.SeedRejected("a kennel was empty")
            if session.mode is SessionMode.BATTLE:
                self.fight()
                self.move(Direction.NORTH)
                continue
            if not distraction_done:
                self.x(Evade(drop="none"), expect=True)
                while session.mode is SessionMode.ENCOUNTER and session.encounter.pursuit is not None:
                    self.x(
                        DropItems(character_id="character-0001", coins=Coins(gp=10)),
                        expect=True,
                    )
                if "encounter.pursuit.distracted" not in self.beats.codes:
                    raise self.SeedRejected("no distraction landed")
                if session.mode is not SessionMode.EXPLORING:
                    raise self.SeedRejected("the distraction pursuit did not end in escape")
                distraction_done = True
            elif not exhaustion_done:
                # No bait this time: with matched speeds the gap never closes and
                # the chase runs the full thirty rounds to the exhaustion terminal.
                self.x(Evade(drop="none"), expect=True)
                while session.mode is SessionMode.ENCOUNTER:
                    self.x(Wait())
                if "encounter.exhaustion.gained" not in self.beats.codes:
                    raise self.SeedRejected("the party never ran itself ragged")
                exhaustion_done = True
            self.move(Direction.NORTH)
            if distraction_done and exhaustion_done:
                break
        if not (distraction_done and exhaustion_done):
            raise self.SeedRejected("the kennels never yielded both flight beats")

        # Night camp: sleep off the exhaustion, re-prepare, and go home.
        self.x(Rest(kind="night"))
        from osrlib.core.spells import MemorizedSpell

        self.x(
            PrepareSpells(
                character_id="character-0005",
                selections=(
                    MemorizedSpell(spell_id="magic_missile"),
                    MemorizedSpell(spell_id="magic_missile"),
                    MemorizedSpell(spell_id="web"),
                    MemorizedSpell(spell_id="web"),
                    MemorizedSpell(spell_id="fire_ball"),
                ),
            )
        )
        while exploration_x(session) > 0:
            self.move(Direction.WEST)
        self.x(UseStairs())
        for _ in range(3):
            self.move(Direction.WEST)
        self.move(Direction.NORTH)  # back through the spiked-open guard door
        for _ in range(3):
            self.move(Direction.WEST)

        # Camp on the level-1 corridor until the dice have sent a wandering
        # encounter AND a blow has disrupted a casting (the resting −1 still
        # leaves the wandering chance at 1-in-6 per check).
        rests = 0
        while not self.beats.wandering_battle_won or "magic.cast.disrupted" not in self.beats.codes:
            rests += 1
            if rests > 150:
                raise self.SeedRejected("the corridor stayed too quiet")
            self.light_up()
            self.x(Rest(kind="turn"))

        self.x(TravelToTown())
        self.checkpoints["end"] = len(session.command_log)
        missing = self.beats.satisfied()
        if missing:
            raise self.SeedRejected(f"missing beats: {missing}")


def serialize_events(event_log) -> list[dict]:
    return [entry if isinstance(entry, dict) else entry.model_dump(mode="json") for entry in event_log]


def build_golden(seed: int) -> dict:
    driver = Driver(seed)
    driver.run()
    session = driver.session
    transcript = [format_message(entry) for entry in session.event_log if isinstance(entry, Event)]
    return {
        "master_seed": seed,
        "party_document": driver.party_document,
        "checkpoints": driver.checkpoints,
        "command_log": [command.model_dump(mode="json") for command in session.command_log],
        "event_log": serialize_events(session.event_log),
        "final_stream_states": {
            key: state.model_dump(mode="json") for key, state in session.streams.export_states().items()
        },
        "final_clock_rounds": session.clock.rounds,
        "defeated_monsters": [record.model_dump(mode="json") for record in session.defeated_monsters],
        "transcript": transcript,
    }


def main() -> None:
    for seed in range(20_260_704, 20_260_704 + 500):
        try:
            golden = build_golden(seed)
        except (Driver.SeedRejected, ValueError) as reason:
            print(f"seed {seed}: {reason}")
            continue
        GOLDEN_PATH.write_text(
            json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"wrote {GOLDEN_PATH} from seed {seed} ({len(golden['command_log'])} commands)")
        return
    raise SystemExit("no seed satisfied the milestone beats in 500 attempts")


if __name__ == "__main__":
    main()
