"""B/X (1981 Basic/Expert) rules engine for turn-based dungeon crawlers.

osrlib applies the rules of a game and stores its state. It has no user interface (UI), never
waits for a player, and makes no network calls. You write the program a player uses, and that
program calls osrlib. You pass osrlib a command, an object that describes one player action.
osrlib applies the rules and returns typed events, each a record of one thing that happened. Your
program turns those events into what the player sees. Every random number comes from a named,
seeded stream, so if you replay the same commands from the same seed, you get the same game.

Each name is defined in one module, and the package root doesn't re-export any names, so import
each name from the module that defines it. osrlib has three layers of modules.

## The core kernel

The kernel, under `osrlib.core`, contains the rules without a session, a dungeon, or an
adventure. You pass a kernel function the inputs for one rule, plus a seeded stream and a
ruleset, and it returns the outcome and the events that describe it. Call kernel functions
directly to roll a character, resolve an attack, or price a sword without starting a game.
Kernel modules never import from `osrlib.crawl`, so you can use them without it.

| Module | What it's for |
| --- | --- |
| [`osrlib.core.rng`][] | Turn a master seed into a separate random-number stream for each subsystem. |
| [`osrlib.core.dice`][] | Parse a dice expression like `"2d6+1"`, and roll it. |
| [`osrlib.core.ruleset`][] | Turn optional rules on or off, in one model the kernel reads. |
| [`osrlib.core.clock`][] | Count game time in rounds, and report each turn and day that passes. |
| [`osrlib.core.alignment`][] | The three alignments that characters and monsters share. |
| [`osrlib.core.validation`][] | The refusal a rules check returns instead of raising an exception. |
| [`osrlib.core.abilities`][] | Look up the modifier or chance an ability score gives, and make ability checks. |
| [`osrlib.core.classes`][] | Read a class's titles and level table, and award experience points (XP). |
| [`osrlib.core.monsters`][] | Read monster templates, and spawn a monster with rolled hit points. |
| [`osrlib.core.items`][] | Buy and equip items, and work out how much a character carries. |
| [`osrlib.core.spells`][] | Memorize and cast spells, and turn undead. |
| [`osrlib.core.tables`][] | Look up the printed tables, like the attack roll a THAC0 needs against an armour class. |
| [`osrlib.core.character`][] | Roll a 1st-level character, in one call or one step at a time. |
| [`osrlib.core.npc`][] | Generate a party of non-player characters (NPCs). |
| [`osrlib.core.creature`][] | The attributes rules functions read, so they accept a character or a monster. |
| [`osrlib.core.combat`][] | Roll initiative, attacks, damage, saving throws, and morale. |
| [`osrlib.core.effects`][] | Run a timed effect, like a *sleep* spell, against the clock and end it on time. |
| [`osrlib.core.treasure`][] | Roll treasure from a treasure type: coins, gems, jewellery, and magic items. |
| [`osrlib.core.events`][] | The base class of every event, and the events the kernel functions return. |

## The crawl framework

The crawl framework, under `osrlib.crawl`, is the game built on those rules: a party that
explores a mapped dungeon, driven by commands. You author the content, start a session, and pass
it one command per player action. The session runs the exploration, encounter, and battle
procedures for you and returns the events. The table lists the modules for authoring content
first, then the session and the modules it runs.

| Module | What it's for |
| --- | --- |
| [`osrlib.crawl.dungeon`][] | Build dungeon levels from cells, edges, doors, areas, and traps. |
| [`osrlib.crawl.adventure`][] | Combine dungeons and a town into an adventure a session can play, and validate it. |
| [`osrlib.crawl.party`][] | Put characters in marching order, and move them as one group. |
| [`osrlib.crawl.gates`][] | Write the conditions the party must meet before it can go through a door or a transition. |
| [`osrlib.crawl.triggers`][] | Write what happens when an event you describe occurs in play. |
| [`osrlib.crawl.quests`][] | Write quests: their objectives and the events that complete them. |
| [`osrlib.crawl.narrative`][] | Write the text shown with a gate, trigger, or quest, and the guidance for a narrator. |
| [`osrlib.crawl.stocking`][] | Roll a room's monsters and treasure from the B/X tables. |
| [`osrlib.crawl.content_pack`][] | Move room contents from one adventure to another, without the map. |
| [`osrlib.crawl.session`][] | Start a game from a party, an adventure, and a seed, and run it one command at a time. |
| [`osrlib.crawl.commands`][] | Every command you can execute, and the session modes each one is legal in. |
| [`osrlib.crawl.events`][] | Every event a command can return, and the parser that reads a stored one. |
| [`osrlib.crawl.views`][] | What the players may see of a session, or the fuller view for the referee (game master). |
| [`osrlib.crawl.interpreter`][] | Register this listener on a session to run the adventure's triggers and quests. |
| [`osrlib.crawl.exploration`][] | How the session runs movement, doors, searching, light, rest, and wandering checks. |
| [`osrlib.crawl.encounter`][] | How the session runs surprise, distance, reaction, evasion, and pursuit. |
| [`osrlib.crawl.battle`][] | How the session runs a battle, one round at a time. |

## Shared services

The shared services are at the top level, and both layers use them: the compiled rules content,
the exceptions, the message formatter, and the save and version documents.

| Module | What it's for |
| --- | --- |
| [`osrlib.data`][] | Load the compiled rules content and look up an entry by id, like `"fighter"` or `"goblin"`. |
| [`osrlib.errors`][] | The exceptions osrlib raises, and the failure each one means. |
| [`osrlib.messages`][] | Turn an event into a line of default English text. |
| [`osrlib.persistence`][] | Save a session to a document, and restore it by loading or replaying. |
| [`osrlib.versioning`][] | The two version numbers in every saved document, and the wrapper that contains them. |

## Quickstart

The quickstart goes through the whole loop: characters, party, adventure, session, commands,
events, save, and load. For a step-by-step explanation of this example, see the quickstart at
https://mmacy.github.io/osrlib-python/getting-started/quickstart/.

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
