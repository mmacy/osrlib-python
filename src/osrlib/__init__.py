"""B/X (1981 Basic/Expert) rules engine for turn-based dungeon crawlers.

osrlib applies the rules and keeps the state of a game. It draws nothing, asks a player
nothing, waits for nothing, and makes no network calls. You hand it a command, it resolves
the rules, and it returns typed events saying what happened. Turning those into something a
player reads is your program's job. Every random draw comes from a named, seeded stream, so
the same seed and the same commands replay the same game.

Every name has one import home and the package root re-exports nothing, so you import from
the module that defines the symbol. The modules fall into three layers, and each table below
lists one layer's modules in the order you meet them.

## The core kernel

The kernel, under `osrlib.core`, is the rules on their own: no session, no dungeon, no
adventure. You hand a function the inputs a rule needs, plus a seeded stream and a ruleset,
and it returns the outcome and the events that describe it. Call it directly to roll a
character, resolve an attack, or price a sword with no game running. Kernel modules never
import from `osrlib.crawl`, so what you build on the kernel keeps working whatever the crawl
framework does above it.

| Module | What it's for |
| --- | --- |
| [`osrlib.core.rng`][] | A master seed in, one named stream per subsystem out. |
| [`osrlib.core.ruleset`][] | The optional rules you switch on, in one model the kernel reads. |
| [`osrlib.core.dice`][] | A dice expression in, a parsed expression or a roll and its own dice out. |
| [`osrlib.core.alignment`][] | The three alignments, shared by characters and monsters. |
| [`osrlib.core.abilities`][] | An ability score in, the modifier or chance the tables grant it out. |
| [`osrlib.core.classes`][] | A class definition and a character in, titles, XP, and advancement out. |
| [`osrlib.core.character`][] | Creation choices and a stream in, a character or refusals out. |
| [`osrlib.core.items`][] | Templates and an inventory in, purchases, equipment, and encumbrance out. |
| [`osrlib.core.spells`][] | A caster and a spell in, memorization, casting, and turning undead out. |
| [`osrlib.core.monsters`][] | A monster template in, a spawned instance with its own hit points out. |
| [`osrlib.core.combat`][] | Combatants and a stream in, initiative, attacks, damage, and saves out. |
| [`osrlib.core.effects`][] | A condition or effect in, a ledger that ticks and expires it out. |
| [`osrlib.core.treasure`][] | A treasure type and a stream in, coins, valuables, and magic items out. |
| [`osrlib.core.tables`][] | Hit dice or an armour class in, the printed row for it out. |
| [`osrlib.core.npc`][] | A party level and a stream in, a generated NPC adventuring party out. |
| [`osrlib.core.clock`][] | Rounds in, turns and days out, with the boundaries each crossing reports. |
| [`osrlib.core.events`][] | The base class every event inherits, and the contract its code follows. |
| [`osrlib.core.validation`][] | The refusal value a rules check returns instead of raising. |

## The crawl framework

The crawl framework, under `osrlib.crawl`, is the game around those rules: a party in a
mapped dungeon, driven by commands. You author the content, start a session, and hand it one
command per player action. The session runs the exploration, encounter, and battle procedures
for you and returns the events. Start at the session and work outwards.

| Module | What it's for |
| --- | --- |
| [`osrlib.crawl.dungeon`][] | Cells, edges, doors, areas, and traps in, a mapped dungeon out. |
| [`osrlib.crawl.adventure`][] | Dungeons and a town in, one adventure a session can play out. |
| [`osrlib.crawl.party`][] | Characters in, marching order, group movement, and combat ranks out. |
| [`osrlib.crawl.session`][] | A party, an adventure, and a seed in, a game taking commands out. |
| [`osrlib.crawl.commands`][] | Every command you can execute, each with the modes it's legal in. |
| [`osrlib.crawl.events`][] | Every event a command can emit, and the parser that reads one back. |
| [`osrlib.crawl.views`][] | A session in, what a player may see or what a referee may see out. |
| [`osrlib.crawl.exploration`][] | Movement, doors, searching, light, rest, and wandering checks. |
| [`osrlib.crawl.encounter`][] | A meeting in, surprise, distance, reaction, evasion, and pursuit out. |
| [`osrlib.crawl.battle`][] | An encounter that came to blows in, a round-by-round battle out. |
| [`osrlib.crawl.stocking`][] | An empty area and a stream in, its monsters and treasure out. |
| [`osrlib.crawl.gates`][] | A gate's condition and a session in, whether the gate opens out. |
| [`osrlib.crawl.triggers`][] | An event pattern in, a match against what just happened out. |
| [`osrlib.crawl.quests`][] | Objectives and the clauses that complete them, as authored content. |
| [`osrlib.crawl.narrative`][] | The authored text on a mechanical object, one block per audience. |
| [`osrlib.crawl.interpreter`][] | The listener you register on a session to play the adventure's triggers and quests. |
| [`osrlib.crawl.content_pack`][] | Keyed room content out of one adventure and into another. |

## Shared services

The shared services sit at the top level and serve both layers: the compiled rules content,
the exceptions, the message formatter, and the save and version documents.

| Module | What it's for |
| --- | --- |
| [`osrlib.data`][] | A content id in, the frozen rules entry behind it out. |
| [`osrlib.errors`][] | The exceptions the library raises, and which failure each one stands for. |
| [`osrlib.messages`][] | An event in, a line of default English out. |
| [`osrlib.persistence`][] | A session in, a save document out, and back again by loading or replaying. |
| [`osrlib.versioning`][] | The two version stamps on every document, and the envelope for them. |

## Quickstart

The quickstart runs the whole loop: characters, party, adventure, session, commands,
events, save, and load. For the documentation, including a stepwise walk through this
example, see https://mmacy.github.io/osrlib-python/

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, MoveParty, SessionMode
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.messages import format_message
from osrlib.persistence import load_game, save_game

# Roll two 1st-level characters; every random draw comes from a named, seeded stream.
rules = Ruleset()
creation = RngStreams(master_seed=7).get(CHARACTER_CREATION_STREAM)
fighter = create_character(name="Hild", class_id="fighter", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
cleric = create_character(name="Osric", class_id="cleric", alignment=Alignment.LAWFUL, ruleset=rules, stream=creation)
party = Party(members=[fighter.character, cleric.character])

# The smallest adventure: a town and a one-corridor dungeon, two cells joined west-east.
crypt = DungeonSpec(
    id="crypt",
    name="The Old Crypt",
    levels=(LevelSpec(number=1, width=2, height=1, entrance=(0, 0), edges={"1,0:west": Edge(kind=EdgeKind.OPEN)}),),
)
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))

# A session starts in town; entering the dungeon switches it to exploring.
session = GameSession.new(party, adventure, seed=7)
session.execute(EnterDungeon(dungeon_id="crypt"))
assert session.mode is SessionMode.EXPLORING

# Commands in, events out: every rules resolution is a typed event with a message code.
result = session.execute(MoveParty(direction=Direction.EAST))
assert result.accepted
lines = [format_message(event) for event in result.events]
assert lines  # every event formats to a default English line

# The whole session round-trips through JSON: same seed, same commands, same game.
document = save_game(session)
restored = load_game(document)
assert save_game(restored) == document
```
"""
