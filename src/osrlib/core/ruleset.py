"""The house rules a game plays under, as one frozen set of flags.

[`Ruleset`][osrlib.core.ruleset.Ruleset] is the entry point and almost the whole module.
Build one at the start of a game, pass it to
[`GameSession.new`][osrlib.crawl.session.GameSession.new] or, away from a session, to
each kernel function that takes a `ruleset` argument, and leave it alone after that. The
resolution functions read it as they work, so every flag takes effect the moment you set
it, and the ruleset travels inside saves so a game resumes under the rules it began
with.

`Ruleset()` with no arguments plays the SRD as written, which is what you want unless
you have a reason to change something. Each flag is either an optional rule the SRD
prints as an alternative to its own default, or an adaptation: a decision the tabletop
game leaves to a human referee, which a program running unattended has to make somehow.
The two enum flags, [`EncumbranceMode`][osrlib.core.ruleset.EncumbranceMode] and
[`XpAwardTiming`][osrlib.core.ruleset.XpAwardTiming], pick among alternatives instead of
switching one behavior on and off.

[The ruleset options guide][ruleset-options] walks through each flag with the play it
changes, and [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/),
the page that lists every place osrlib supplies a default the tabletop game leaves to a
referee, gives the reasoning behind each one.

Typical usage:

```python
from osrlib.core.ruleset import EncumbranceMode, Ruleset, XpAwardTiming

# The SRD as written.
rules = Ruleset()
assert rules.variable_weapon_damage is True
assert rules.encumbrance is EncumbranceMode.BASIC
assert rules.xp_award_timing is XpAwardTiming.ON_RETURN

# A game that pays experience out as it's earned and tracks no encumbrance.
house_rules = Ruleset(xp_award_timing=XpAwardTiming.IMMEDIATE, encumbrance=EncumbranceMode.NONE)
assert house_rules.variable_weapon_damage is True
```
"""

# Every flag here is read by a behavior that exists. Never ship a flag whose behavior
# doesn't. Adding new flags with defaults is schema-legal.

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "EncumbranceMode",
    "Ruleset",
    "XpAwardTiming",
]


class XpAwardTiming(StrEnum):
    """When a party is paid the experience points it has earned.

    Set it on [`Ruleset.xp_award_timing`][osrlib.core.ruleset.Ruleset]. The choice is
    about pacing: `ON_RETURN` makes getting out of the dungeon alive the thing that pays,
    and `IMMEDIATE` pays as the party goes, which suits a game with no trip home.

    The lowercase values serialize into saved games. Changing one is a `schema_version`
    bump, the version stamp that marks a serialized model's shape.
    """

    ON_RETURN = "on_return"
    """Award experience when the party survives and returns to safety, as the tabletop rules have it.

    Experience for defeated monsters and recovered treasure is held until the party gets
    back to town, and treasure lost on the way is never paid for.
    """

    IMMEDIATE = "immediate"
    """Award experience as it's earned: monsters at the end of each encounter, treasure as it's picked up.

    This is an adaptation for continuous play, where the party may never make a trip
    home. Reaching town pays nothing extra, and dropping treasure doesn't take the
    experience back.
    """


class EncumbranceMode(StrEnum):
    """Which system tracks what the party is carrying, and how much it slows them down.

    Set it on [`Ruleset.encumbrance`][osrlib.core.ruleset.Ruleset]. The weights and the
    movement rates live in [`osrlib.core.items`][osrlib.core.items], which reads this
    choice.

    The lowercase values serialize into saved games. Changing one is a `schema_version`
    bump, the version stamp that marks a serialized model's shape.
    """

    NONE = "none"
    """Track nothing: every character moves at the base rate of 120 feet per turn, whatever they carry."""

    BASIC = "basic"
    """Set movement rate from worn armour, and from whether the character is carrying a significant amount of treasure.

    This is the SRD's own default and the mode a `Ruleset()` picks. A character whose
    tracked weight passes the maximum load cannot move at all.
    """

    DETAILED = "detailed"
    """Total the coin weight of armour, gear, and treasure, and set movement rate from banded weight thresholds.

    Heavier bookkeeping than `BASIC`, and the same maximum load: past it, the character
    cannot move.
    """


class Ruleset(BaseModel):
    """The rules a game plays under: one flag per decision osrlib lets you make.

    Build one at the start of a game and pass it wherever a `ruleset` argument appears.
    `Ruleset()` plays the SRD as written, so name only the flags you want to change. The
    model is frozen, so you cannot edit a ruleset mid-game, and it goes into saves and
    replays, so a resumed game keeps the rules it started with. It also refuses a field
    name it doesn't know, which turns a misspelled flag into a pydantic
    `ValidationError` rather than a setting that quietly does nothing.

    Each flag is one of two kinds. An optional rule is an alternative the SRD itself
    prints beside its own procedure, and osrlib's default is the book's. An adaptation is
    a default osrlib supplies where the tabletop game hands the decision to a human
    referee, and [the adaptations register](https://mmacy.github.io/osrlib-python/adaptations/)
    gives the reasoning behind each one.

    Examples:
        ```python
        from osrlib.core.ruleset import Ruleset

        rules = Ruleset(individual_initiative=True, hp_reroll_at_first_level=True)
        assert rules.individual_initiative is True

        # The rest keep the book's defaults.
        assert rules.thac0_arithmetic is False
        assert rules.magic_item_death_save is True
        ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hp_reroll_at_first_level: bool = False
    """Reroll a first-level hit die that comes up 1 or 2. An optional rule, off by default.

    Turn it on and the raw die is rerolled until it shows 3 or more, before the CON
    modifier is added, so a first-level character starts with at least 3 hit points before
    that modifier.
    """

    encumbrance: EncumbranceMode = EncumbranceMode.BASIC
    """Which system tracks carried weight and sets movement rate. Basic by default.

    See [`EncumbranceMode`][osrlib.core.ruleset.EncumbranceMode] for what each mode
    counts, and [`osrlib.core.items`][osrlib.core.items] for the weights and rates it
    reads.
    """

    variable_weapon_damage: bool = True
    """Let each weapon deal the damage its own description lists. An optional rule, on by default.

    Turn it off and every weapon deals 1d6, and so does every piece of gear swung as one,
    which is the SRD's baseline combat system. Either way an unarmed attack deals 1d2, which is
    its own rule rather than weapon damage, and monsters always deal the damage printed
    in their descriptions.
    """

    individual_initiative: bool = False
    """Roll initiative for each combatant instead of once per side. An optional rule, off by default.

    Turn it on and every participant rolls its own 1d6. A character adds its DEX
    modifier, and a halfling adds its class initiative bonus on top. The tabletop game leaves a monster's
    initiative modifier to the referee, so osrlib takes one from the caller and defaults
    it to 0.
    """

    thac0_arithmetic: bool = False
    """Work out the attack target number by subtraction instead of reading the attack matrix.

    An optional rule, off by default. Turn it on and the number to roll is `THAC0 − AC`
    with no clamping. The SRD's ascending-armour-class procedure is the same arithmetic,
    so this one flag covers both presentations. The matrix differs only in keeping its
    cells within 2 to 20, which shows once modifiers push a total past those bounds.
    """

    weapon_reload: bool = False
    """Stop a weapon with the reload quality, mainly the crossbow, from firing two rounds running.

    An optional rule, off by default. Turn it on and the attack validator refuses the shot
    when the combat context you pass says the weapon fired last round. The kernel enforces the rule
    from the context it's given. Keeping track of what fired when is the battle layer's
    job.
    """

    hd5_counts_as_magical: bool = False
    """Let big monsters hurt creatures that only silver or magic weapons can harm. An optional rule, off by default.

    The SRD prints this among its invulnerability rules. Turn it on and a monster of 5 or
    more Hit Dice gets past such a defense, and so does a monster that has the same defense
    itself. Following the rule's own wording, osrlib applies it only where the defense is
    limited to silver and magic, and reads "another invulnerable monster" as one bearing
    such a defense.
    """

    magic_item_death_save: bool = True
    """Give a dead character's magic items a saving throw against the effect that killed their owner.

    An optional rule the SRD leaves to the referee, on by default. Each magic item in the
    doomed inventory rolls the owner's save values against the destructive source's
    category, adding the item's best combat bonus. What survives lands in a drop pile at
    the victim's cell, where the party can pick it up, rather than vanishing with the
    body.
    """

    xp_award_timing: XpAwardTiming = XpAwardTiming.ON_RETURN
    """When earned experience is actually paid out. On return by default, which is the tabletop rule.

    See [`XpAwardTiming`][osrlib.core.ruleset.XpAwardTiming]. The immediate setting is an
    adaptation, osrlib's default where the tabletop game hands the decision to a referee,
    and it suits a game whose party never goes home.
    """

    deprivation_penalties: bool = False
    """Attach mechanical penalties to going without food or water. An adaptation, off by default.

    An adaptation is a default osrlib supplies where the tabletop game hands the decision
    to a referee, and the tabletop rules leave starvation penalties to the referee's
    discretion. The party's food and water are tracked either way, so this flag changes
    only what happens once they run out. Turn it on and the schedule osrlib draws from the
    SRD's own examples applies. After one full day without: −1 to attack rolls, and rest needed
    twice as often, so fatigue sets in after three unrested turns rather than six. After
    two days, movement halves as well. From the third day on, 1d4 hit points are lost per
    day. Hunger and thirst don't stack, so whichever is worse applies.
    [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) has the
    reasoning.
    """

    aoe_friendly_fire: bool = True
    """Let an area effect cast at a monster group in melee catch the party members fighting it.

    An adaptation, on by default, an adaptation being a default osrlib supplies where the
    tabletop game hands the decision to a referee. Turn it off and an area effect aimed at
    a monster group never counts party members among its candidates, which makes a
    fireball safe to drop on a melee.
    """

    formation_width_limit: bool = True
    """Cap how many combatants can fight side by side, by how wide the passage is.

    An adaptation, on by default, an adaptation being a default osrlib supplies where the
    tabletop game hands the decision to a referee. Turn it on and three may fight abreast
    inside a keyed area, two in a corridor cell, following the SRD's note about two or
    three characters fighting side by side in a ten-foot passage. Turn it off and the cap
    lifts, so every combatant may melee.
    """
