"""The house rules for a game, as one frozen set of flags.

[`Ruleset`][osrlib.core.ruleset.Ruleset] is the entry point and almost the whole module.
Build one at the start of a game, pass it to
[`GameSession.new`][osrlib.crawl.session.GameSession.new] or, away from a session, to
each kernel function that takes a `ruleset` argument. The model is frozen, so a game
uses one ruleset from start to finish. The rules functions read its flags on every
call, so there's no setup step. A save contains the ruleset, so a loaded game uses the
rules it began with.

`Ruleset()` with no arguments gives you the SRD rules as written. Each flag is an optional
rule, an adaptation, or a house rule. An optional rule is an alternative the SRD prints
beside its default procedure. An adaptation is a default osrlib chooses for a decision that
the tabletop game leaves to a human referee. A program that runs without a referee has to
make that decision. A house rule changes a procedure in a way the SRD doesn't print, and
it's off unless you turn it on.
Three flags pick among alternatives instead of switching one behavior on and off: the two
enum flags, [`EncumbranceMode`][osrlib.core.ruleset.EncumbranceMode] and
[`XpAwardTiming`][osrlib.core.ruleset.XpAwardTiming], and the string flag
`ability_score_method`.

Each flag is a field of [`Ruleset`][osrlib.core.ruleset.Ruleset], documented there and in
[the ruleset options guide](https://mmacy.github.io/osrlib-python/guides/ruleset-options/)
with what each flag changes in play.
[The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) lists each
default osrlib supplies for a decision the tabletop game leaves to a referee, with the
reasoning behind each one.

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

# Code reads every flag here. Don't add a flag before the code that reads it exists.
# Adding a flag with a default doesn't require a schema_version bump.

from typing import Literal

from pydantic import ConfigDict

from osrlib.core._descriptions import _StrEnum
from osrlib.core.frozen import _Model

__all__ = [
    "EncumbranceMode",
    "Ruleset",
    "XpAwardTiming",
]


class XpAwardTiming(_StrEnum):
    """When a party receives the experience points it has earned.

    Set it on [`Ruleset.xp_award_timing`][osrlib.core.ruleset.Ruleset]. The choice affects
    the game's pacing. With `ON_RETURN`, the party gets its experience only once it's back
    in town alive. With `IMMEDIATE`, it gets experience as it earns it, which suits a game
    with no trip home.

    Saved games contain the lowercase values, and they stay fixed within a
    `schema_version`, the version stamp that identifies a serialized model's format.
    """

    ON_RETURN = "on_return"
    """Award experience when the party survives and returns to town, as in the tabletop rules.

    osrlib awards the experience for defeated monsters and recovered treasure only when the
    party gets back to town. Treasure lost on the way earns nothing.
    """

    IMMEDIATE = "immediate"
    """Award experience as it's earned: monsters at the end of each encounter, treasure as it's picked up.

    This is an adaptation for continuous play, where the party may never make a trip
    home. The party gets nothing extra when it reaches town, and dropping treasure doesn't
    take the experience back.
    """


class EncumbranceMode(_StrEnum):
    """Which system osrlib uses to track what the party carries, and how much it slows them down.

    Set it on [`Ruleset.encumbrance`][osrlib.core.ruleset.Ruleset]. The weights and the
    movement rates are in [`osrlib.core.items`][osrlib.core.items], and
    [`movement_rate_feet`][osrlib.core.items.movement_rate_feet] reads this choice.

    Saved games contain the lowercase values, and they stay fixed within a
    `schema_version`, the version stamp that identifies a serialized model's format.
    """

    NONE = "none"
    """Track nothing: every character moves at the base rate of 120 feet per turn, whatever they carry."""

    BASIC = "basic"
    """Set movement rate from worn armour, and from whether the character carries a significant amount of treasure.

    This is the SRD's default and the mode `Ruleset()` uses. A character whose tracked
    weight passes the maximum load can't move at all.
    """

    DETAILED = "detailed"
    """Total the coin weight of armour, gear, and treasure, and set movement rate from banded weight thresholds.

    It counts more than `BASIC` does. The maximum load is the same, and past it the
    character can't move.
    """


class Ruleset(_Model):
    """The rules for a game: one flag per decision osrlib lets you make.

    Build one at the start of a game and pass it to
    [`GameSession.new`][osrlib.crawl.session.GameSession.new], or to each kernel function
    that takes a `ruleset` argument. `Ruleset()` gives you the SRD rules as written, so pass
    only the flags you want to change. The model is frozen, so you can't change a ruleset
    mid-game. Saves and replays contain it, so a resumed game uses the rules it started with. An
    unknown field name raises a pydantic `ValidationError`, so a misspelled flag fails
    instead of doing nothing.

    Each flag is one of three kinds. An optional rule is an alternative the SRD prints
    beside its standard procedure, and osrlib's default is the book's. An adaptation is a
    default osrlib supplies where the tabletop game leaves the decision to a human
    referee. A house rule is a change the SRD doesn't print, and its default is the SRD's
    procedure. [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/)
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

    Turn it on and osrlib rerolls the raw die until it shows 3 or more, before the CON
    modifier is added, so a first-level character starts with at least 3 hit points before
    that modifier.
    """

    max_hp_at_first_level: bool = False
    """Count a first-level character's hit die as its maximum. A house rule, off by default.

    The SRD doesn't print this rule. Its character creation has you roll the class's hit die. Turn
    the flag on and a first-level character's hit points are the hit die's size plus the CON
    modifier, and at least 1, so a fighter with CON 7 starts with 8 - 1 = 7.
    [`roll_hit_points`][osrlib.core.character.roll_hit_points] still throws the dice it throws with
    the flag off, so the creation stream ends in the same place and the starting gold roll after it is
    the same. With `hp_reroll_at_first_level` also on, a die showing 1 or 2 is still thrown again and
    [`HitPointRoll.rolls`][osrlib.core.character.HitPointRoll.rolls] lists every die, but the
    reroll can't change the hit points. The flag affects only the hit points rolled at character
    creation. [`level_up`][osrlib.core.classes.level_up] rolls each later level's hit die as usual.
    """

    ability_score_method: Literal["3d6", "4d6_drop_lowest"] = "3d6"
    """How a new character's ability scores are rolled. `"3d6"` by default, the SRD's method.

    `"4d6_drop_lowest"` is a house rule, not an SRD rule. With it, each ability throws four d6 and
    the lowest die doesn't count, so the score is the sum of the other three, from 3 to 18.
    [`create_character`][osrlib.core.character.create_character] passes the method to
    [`roll_ability_scores`][osrlib.core.character.roll_ability_scores], and the result lists the
    dropped die of each ability in
    [`AbilityScoreRolls.dropped`][osrlib.core.character.AbilityScoreRolls.dropped]. The method
    affects only the characters you create. The NPC adventurers that encounters generate always
    roll 3d6.
    """

    encumbrance: EncumbranceMode = EncumbranceMode.BASIC
    """Which system tracks carried weight and sets movement rate. Basic by default.

    See [`EncumbranceMode`][osrlib.core.ruleset.EncumbranceMode] for what each mode
    counts, and [`osrlib.core.items`][osrlib.core.items] for the weights and rates it
    reads.
    """

    variable_weapon_damage: bool = True
    """Let each weapon deal the damage its description lists. An optional rule, on by default.

    Turn it off and every weapon deals 1d6, and so does every piece of gear swung as one,
    which is the SRD's baseline combat system. The exception is a character wearing a
    girdle of giant strength, whose weapon attacks deal 2d8. With the flag on, the girdle
    doubles weapon damage instead. Either way, an unarmed attack deals 1d2, and monsters
    deal the damage printed in their descriptions.
    """

    individual_initiative: bool = False
    """Roll initiative for each combatant instead of once per side. An optional rule, off by default.

    Turn it on and every participant rolls a separate 1d6. A character adds its DEX
    modifier, and a halfling also adds its class initiative bonus. The tabletop game
    leaves a monster's initiative modifier to the referee, so you pass one to
    [`participant_modifier`][osrlib.core.combat.participant_modifier], and it's 0 if you
    don't.
    """

    thac0_arithmetic: bool = False
    """Work out the attack target number by subtraction instead of reading the attack matrix.

    An optional rule, off by default. Turn it on and the number to roll is `THAC0 − AC`
    with no clamping. The SRD's ascending-armour-class procedure is the same arithmetic,
    so this one flag covers both. Every number in the matrix is within 2 to 20, and the
    subtraction has no such limit, so the two differ only when `THAC0 − AC` falls outside that
    range.
    """

    weapon_reload: bool = False
    """Stop a weapon with the reload quality, mainly the crossbow, from firing two rounds running.

    An optional rule, off by default. Turn it on and the attack validator refuses the shot
    when the combat context you pass shows the weapon fired last round. In a session, the
    battle code records what fired each round. Without a session, you set that in the
    context yourself.
    """

    hd5_counts_as_magical: bool = False
    """Let big monsters hurt creatures that only silver or magic weapons can harm. An optional rule, off by default.

    The SRD prints this among its invulnerability rules. Turn it on, and a monster with 5 or
    more Hit Dice gets past such a defense. So does a monster that has the same kind of
    defense itself. A monster that energy drain has taken Hit Dice from counts only the Hit
    Dice it has left. osrlib applies the rule only where the defense is limited to silver
    and magic, as the rule is worded. It reads "another invulnerable monster" as a monster
    with such a defense.
    """

    magic_item_death_save: bool = True
    """Give a dead character's magic items a saving throw against the effect that killed their owner.

    An optional rule the SRD leaves to the referee, on by default. It applies when a
    destructive source, like a breath weapon or *disintegrate*, kills a character and
    destroys their equipment. osrlib rolls a save for each magic item in the dead
    character's inventory, using the owner's save values against the source's category
    and adding the item's best combat bonus. An item that saves isn't destroyed. Turn the
    flag off and every item is destroyed.
    """

    xp_award_timing: XpAwardTiming = XpAwardTiming.ON_RETURN
    """When the party receives earned experience. On return by default, which is the tabletop rule.

    See [`XpAwardTiming`][osrlib.core.ruleset.XpAwardTiming]. The immediate setting is an
    adaptation, and it suits a game whose party never goes home.
    """

    deprivation_penalties: bool = False
    """Attach mechanical penalties to going without food or water. An adaptation, off by default.

    The tabletop rules leave starvation penalties to the referee. osrlib tracks the party's
    food and water either way, so this flag changes only what happens once they run out.
    Turn it on and osrlib applies this schedule, which it bases on the SRD's examples:

    - After one full day without food or water, −1 to attack rolls, and the character needs
      rest twice as often, so fatigue sets in after three turns without rest instead of six.
    - After two days, movement rate also halves.
    - From the third day on, the character also loses 1d4 hit points each day.

    Hunger and thirst don't add together: the longer of the two counts.
    [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) has the
    reasoning.
    """

    aoe_friendly_fire: bool = True
    """Let an area effect cast at a monster group in melee catch the party members fighting it.

    An adaptation, on by default. Turn it off and an area effect aimed at a monster group
    never counts party members among its targets, so a fireball is safe to cast at
    monsters the party is fighting in melee.
    """

    formation_width_limit: bool = True
    """Cap how many combatants can fight side by side, by how wide the passage is.

    An adaptation, on by default. The cap is the party's frontage, the width of the space
    it fights across, divided by the five feet one combatant needs. Each dungeon cell is
    ten feet wide, so two may fight side by side in a one-cell corridor and four in a room
    two cells across. The SRD notes that two or three characters can fight side by side
    in a ten-foot passage, and osrlib uses the lower number. Turn the flag off and there's
    no cap, so every combatant may melee.
    """

    failed_trap_removal_springs: bool = True
    """Spring a treasure trap on the thief who fails to remove it. An adaptation, on by default.

    The SRD says a thief may try only once to remove a treasure trap, and it doesn't say what
    a failure does. Turn the flag off and a failed
    [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap] leaves the trap armed.
    The party still knows where it is. That thief can't try again, another thief in the
    party can, and the trap still gets its 2-in-6 chance to spring when someone takes the
    treasure.
    """

    back_stab_needs_both_skills: bool = True
    """Require both of a hiding thief's skill rolls for a back-stab. An adaptation, on by default.

    A thief can hide during a battle with a `hide` declaration and then back-stab from hiding, as
    [`BattleDeclaration.back_stab`][osrlib.crawl.commands.BattleDeclaration.back_stab] describes.
    The session rolls the thief's hide in shadows skill when the hide resolves, and move silently
    when the back-stab comes up. With the flag on, the attack is a back-stab only when both rolls
    passed. Turn it off and either roll passing is enough. The session makes both rolls under
    either setting, so the flag changes no draw. The monsters' choice of targets reads the hide in
    shadows roll alone under either setting.

    The SRD says hide in shadows "Requires the thief to be motionless—attacking or moving while
    hiding is not possible", and that with move silently "A thief may attempt to sneak past
    enemies unnoticed". Neither setting is the rules as written.
    [The adaptations register](https://mmacy.github.io/osrlib-python/adaptations/) has the
    reasoning.
    """
