"""B/X (1981 Basic/Expert) rules engine for turn-based dungeon crawlers.

osrlib applies the rules and keeps the state of a game. It draws nothing, asks a player
nothing, waits for nothing, and makes no network calls. You hand it a command, it resolves
the rules, and it returns typed events saying what happened. Turning those into something a
player reads is your program's job. Every random draw comes from a named, seeded stream, so
the same seed and the same commands replay the same game.

Every name has one import home and the package root re-exports nothing, so you import from
the module that defines the symbol. The modules fall into three layers.

The kernel, under `osrlib.core`, is the rules on their own: no session, no dungeon, no
adventure. Use it directly to roll a character, resolve an attack, or price a sword with no
game running.

- [`osrlib.core.rng`][osrlib.core.rng]: a master seed in, one named stream per subsystem out.
- [`osrlib.core.ruleset`][osrlib.core.ruleset]: the optional rules you switch on, in one model the kernel reads.
- [`osrlib.core.dice`][osrlib.core.dice]: a dice expression in, a parsed expression or a roll and its own dice out.
- [`osrlib.core.alignment`][osrlib.core.alignment]: the three alignments, shared by characters and monsters.
- [`osrlib.core.abilities`][osrlib.core.abilities]: an ability score in, the modifier or chance the tables grant it out.
- [`osrlib.core.classes`][osrlib.core.classes]: a class definition and a character in, titles, XP, advancement out.
- [`osrlib.core.character`][osrlib.core.character]: creation choices and a stream in, a character or refusals out.
- [`osrlib.core.items`][osrlib.core.items]: templates and an inventory in, purchases, equipment, and encumbrance out.
- [`osrlib.core.spells`][osrlib.core.spells]: a caster and a spell in, memorization, casting, and turning undead out.
- [`osrlib.core.monsters`][osrlib.core.monsters]: a monster template in, a spawned instance with its own hit points out.
- [`osrlib.core.combat`][osrlib.core.combat]: combatants and a stream in, initiative, attacks, damage, and saves out.
- [`osrlib.core.effects`][osrlib.core.effects]: a condition or effect in, a ledger that ticks and expires it out.
- [`osrlib.core.treasure`][osrlib.core.treasure]: a treasure type and a stream in, coins, valuables, magic items out.
- [`osrlib.core.tables`][osrlib.core.tables]: hit dice or an armour class in, the printed row for it out.
- [`osrlib.core.npc`][osrlib.core.npc]: a party level and a stream in, a generated NPC adventuring party out.
- [`osrlib.core.clock`][osrlib.core.clock]: rounds in, turns and days out, with the boundaries each crossing reports.
- [`osrlib.core.events`][osrlib.core.events]: the base class every event inherits, and the contract its code follows.
- [`osrlib.core.validation`][osrlib.core.validation]: the refusal value a rules check hands back instead of raising.

The crawl framework, under `osrlib.crawl`, is the game around those rules: a party in a
mapped dungeon, driven by commands. Start at the session and work outwards.

- [`osrlib.crawl.dungeon`][osrlib.crawl.dungeon]: cells, edges, doors, areas, and traps in, a mapped dungeon out.
- [`osrlib.crawl.adventure`][osrlib.crawl.adventure]: dungeons and a town in, one adventure a session can play out.
- [`osrlib.crawl.party`][osrlib.crawl.party]: characters in, marching order, group movement, and combat ranks out.
- [`osrlib.crawl.session`][osrlib.crawl.session]: a party, an adventure, and a seed in, a game taking commands out.
- [`osrlib.crawl.commands`][osrlib.crawl.commands]: every command you can execute, each with the modes it is legal in.
- [`osrlib.crawl.events`][osrlib.crawl.events]: every event a command can emit, and the parser that reads one back.
- [`osrlib.crawl.views`][osrlib.crawl.views]: a session in, what a player may see or what a referee may see out.
- [`osrlib.crawl.exploration`][osrlib.crawl.exploration]: movement, doors, searching, light, rest, and wandering checks.
- [`osrlib.crawl.encounter`][osrlib.crawl.encounter]: a meeting in, surprise, distance, reaction, evasion, pursuit out.
- [`osrlib.crawl.battle`][osrlib.crawl.battle]: an encounter that came to blows in, a round-by-round battle out.
- [`osrlib.crawl.stocking`][osrlib.crawl.stocking]: an empty area and a stream in, its monsters and treasure out.
- [`osrlib.crawl.gates`][osrlib.crawl.gates]: a condition and a session in, whether the way opens out.
- [`osrlib.crawl.triggers`][osrlib.crawl.triggers]: an event pattern in, a match against what just happened out.
- [`osrlib.crawl.quests`][osrlib.crawl.quests]: objectives and the clauses that complete them, as authored content.
- [`osrlib.crawl.narrative`][osrlib.crawl.narrative]: the authored text on a mechanical object, one block per audience.
- [`osrlib.crawl.interpreter`][osrlib.crawl.interpreter]: a listener you register in, an adventure playing itself out.
- [`osrlib.crawl.content_pack`][osrlib.crawl.content_pack]: keyed room content out of one adventure and into another.

The shared services sit at the top level and serve both layers.

- [`osrlib.data`][osrlib.data]: a content id in, the frozen rules entry behind it out.
- [`osrlib.errors`][osrlib.errors]: the exceptions the library raises, and which failure each one stands for.
- [`osrlib.messages`][osrlib.messages]: an event in, a line of default English out.
- [`osrlib.persistence`][osrlib.persistence]: a session in, a save document out, and back again by loading or replaying.
- [`osrlib.versioning`][osrlib.versioning]: the two version stamps on every document, and the envelope for them.

The quickstart below runs the whole loop: characters, party, adventure, session, commands,
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
