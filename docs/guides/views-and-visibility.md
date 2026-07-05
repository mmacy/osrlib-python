# Views and visibility

B/X hides information from players by design. A fighter doesn't know the goblin has 4
hit points left, only that it's bleeding; the referee alone sees the monsters' morale
roll and the reaction roll that decided whether they attack or flee. That asymmetry is
the whole point of having a referee, and osrlib encodes it directly instead of leaving
it to a front end's discipline. This page covers the two places visibility shows up —
on individual events, and on the two whole-state projections a
[`GameSession`][osrlib.crawl.session.GameSession] can build — and why a networked front
end must never let the client see more than the player is meant to. The complete
example appears [at the end of the page](#the-complete-example); the fragments along
the way are excerpts of it.

## Visibility on events

Every [`Event`][osrlib.core.events.Event] carries a
[`Visibility`][osrlib.core.events.Visibility]: `PLAYER` or `REFEREE`. Most events
default to `PLAYER` — a party moved, a door opened, damage was dealt. A specific set
default to `REFEREE` because B/X keeps them behind the screen: morale checks, reaction
rolls, wandering-monster checks, detection rolls, and the event that carries a
creature's actual hit-point numbers
([`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent]). A front end
that streams or narrates the raw event log as it happens — an LLM referee doing
turn-by-turn narration, say — is responsible for checking `.visibility` itself before
showing an event to a player, the same way it would filter a database query.

Most front ends never need to do that filtering by hand, though, because osrlib also
ships two ready-made projections of the *whole session*, one per audience, and either
one already applies this filtering for its consumer.

## The two views

[`GameSession.view`][osrlib.crawl.session.GameSession.view] takes a `Visibility` and
returns the matching projection:

```{.python .no-run}
player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)
```

[`PlayerView`][osrlib.crawl.views.PlayerView] is an enumerated whitelist, built
straight from session state — never from the event log, so it can't accidentally leak
a referee-visibility event that happened to mention a hidden number. It carries: the
adventure's and town's public names and descriptions; each party member's own public
sheet ([`MemberView`][osrlib.crawl.views.MemberView] — id, name, class, level, current
and max hit points, conditions, inventory, memorized spells — a player always sees
their own characters in full); the party's location and facing; the elapsed clock; the
session mode; the explored map, cell by cell, with its edges (an undiscovered secret
door renders as a plain wall — [`ExploredLevelView`][osrlib.crawl.views.ExploredLevelView]
and [`EdgeView`][osrlib.crawl.views.EdgeView]); known dropped piles and emptied
treasure caches in that explored space; active effects on party members with their
remaining duration (except a potion's — RAW has the referee track that secretly, so
the view reports it as unknown); fatigue, exhaustion, and deprivation status; and, when
one is running, the current encounter or battle's public shape
([`EncounterView`][osrlib.crawl.views.EncounterView] and
[`EncounterGroupView`][osrlib.crawl.views.EncounterGroupView] — a monster group's id,
label, living count, distance, and visible conditions, but never its hit points).
Unidentified magic items are masked to a category-level description rather than their
true name — see [`MagicItemCategory`][osrlib.core.items.MagicItemCategory] — so even a
character's own inventory doesn't leak what a `detect magic` hasn't earned them yet.

[`RefereeView`][osrlib.crawl.views.RefereeView] is the opposite instinct: everything,
minus the RNG stream states and the master seed. Its single `state` field is the same
serialized shape [`session_state`][osrlib.persistence.session_state] produces for a
save — full monster instances with real hit points, the flag store, the NPC roster,
session counters, and the complete event log, referee-visibility events included. It
exists for LLM referees and tools that need the truth, not a player's approximation of
it; a wire client should never receive it.

## The stable difference

The clearest way to see the split is a spawned monster. The referee view's state
carries the monster's live hit points; the player-facing encounter group carries only
what the party could plausibly perceive — how many are still standing, how far away,
what conditions show:

```{.python .no-run}
# The referee sees the goblin's hit points; the player view never carries them.
referee_monster = referee_view.state["monsters"][0]
assert "current_hp" in referee_monster

player_group = player_view.encounter.groups[0]
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()
```

## Never trust the client

The moment a game goes over a network, this split becomes a security boundary, not
just a courtesy. The session — with its full referee-visible state — stays on the
server; a client never runs `execute` itself and never receives the referee view. Each
request sends a command, the server calls `session.execute(command)`, and the response
carries only `session.view(Visibility.PLAYER)` (or a rendering of the accepted
result's events, filtered the same way) back over the wire. A client that could see
the referee view, or execute commands against a local copy of the session, could read
monster hit points directly off the wire or replay commands the real game state never
sanctioned — exactly the kind of information and control B/X reserves for the person
running the table. [The FastAPI pattern](../front-ends/fastapi-pattern.md) walks
through this boundary end to end: one session per game, held server-side, with every
response passed through the player view before it leaves the process.

## The complete example

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import EnterDungeon, SessionMode, SpawnMonsters
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession

rules = Ruleset()
creation = RngStreams(master_seed=13).get(CHARACTER_CREATION_STREAM)
hero = create_character(
    name="Rurik",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=creation,
)
party = Party(members=[hero.character])

level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,))
session = GameSession.new(party, adventure, seed=13)

session.execute(EnterDungeon(dungeon_id="crypt"))

# The referee spawns a lone goblin and opens an encounter at 30 feet.
result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30))
assert result.accepted
assert session.mode is SessionMode.ENCOUNTER

player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)

# The referee sees the goblin's hit points; the player view never carries them.
referee_monster = referee_view.state["monsters"][0]
assert "current_hp" in referee_monster

player_group = player_view.encounter.groups[0]
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()
```

## Where next

- [Sessions, commands, and events](sessions-commands-events.md) — the command loop
  that produces the state these views project.
- [The FastAPI pattern](../front-ends/fastapi-pattern.md) — the player view as the
  wire contract, end to end.
- [LLM referees](../front-ends/llm-referees.md) — a narrator built on the referee
  view and the raw event log.
