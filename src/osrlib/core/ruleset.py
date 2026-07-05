"""The `Ruleset` model: optional-rule and adaptation flags.

Every SRD optional rule and every documented adaptation is a named flag with a
default. Flags are read at resolution time, so a `Ruleset` is fixed for the life of a
session (it participates in saves and replays). The model is frozen and rejects unknown
flags — a typo'd flag name errors instead of silently doing nothing.
"""

# Every flag here is read by an implemented behavior; never ship a flag whose behavior
# doesn't exist. Adding new flags with defaults is schema-legal.

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "EncumbranceMode",
    "Ruleset",
    "XpAwardTiming",
]


class XpAwardTiming(StrEnum):
    """When the XP award fires; see the adventure award procedure.

    The wire values are `"on_return"` and `"immediate"` — lowercase, serialized
    into saves; changing them is a `schema_version` bump.
    """

    ON_RETURN = "on_return"
    IMMEDIATE = "immediate"


class EncumbranceMode(StrEnum):
    """How carried weight is tracked; see [`osrlib.core.items`][osrlib.core.items].

    The wire values are `"none"`, `"basic"`, and `"detailed"` — lowercase, serialized
    into saves; changing them is a `schema_version` bump.
    """

    NONE = "none"
    BASIC = "basic"
    DETAILED = "detailed"


class Ruleset(BaseModel):
    """The optional-rule and adaptation flags a session plays under.

    Each flag is either an optional rule from the OSE SRD or a documented adaptation
    (see the adaptations register on the documentation site). Build a `Ruleset` when a
    session starts; it is frozen, and it travels with saves and replays so a game
    always resumes under the rules it began with.

    Attributes:
        hp_reroll_at_first_level: SRD optional rule, default off: first-level
            hit-point dice showing 1 or 2 (the raw die, before the CON modifier) are
            re-rolled until the die shows 3 or more.
        encumbrance: Which encumbrance system tracks carried weight and drives
            movement rates; see [`osrlib.core.items`][osrlib.core.items]. Default
            basic.
        variable_weapon_damage: SRD optional rule, default on: each weapon deals the
            damage listed in its description. Off means every weapon — and every
            piece of gear swung as one — deals 1d6, the SRD's baseline "PC attacks
            inflict 1d6 damage" rule. Unarmed attacks stay 1d2 either way (their own
            rule, not weapon damage), and monster damage is unaffected: monsters
            always deal the damage in their descriptions.
        individual_initiative: SRD optional rule, default off: each combat
            participant rolls its own 1d6 initiative, DEX-modified for characters
            (plus the halfling's `initiative_bonus` class tag). The tabletop game
            leaves monster initiative modifiers to the referee; osrlib takes a
            caller-supplied modifier, default 0.
        thac0_arithmetic: SRD optional rule, default off: the attack target number is
            unclamped `THAC0 − AC` arithmetic instead of the attack-matrix lookup.
            The SRD's ascending-AC attack procedure is algebraically identical, so
            this one flag covers both presentations; the matrix differs only through
            its 2..20 clamping.
        weapon_reload: SRD optional rule, default off: a weapon with the reload
            quality cannot fire two rounds running. The attack validator rejects the
            shot when the caller-supplied context says the weapon fired last round;
            round-to-round bookkeeping belongs to the battle layer, and the kernel
            enforces the rule given honest context.
        hd5_counts_as_magical: The SRD's invulnerabilities optional rule, default
            off: a monster of 5 or more Hit Dice — or another invulnerable monster —
            can harm creatures otherwise hurt only by silver or magic weapons.
            Following the rule's own wording, osrlib applies the flag only to
            weapon-material requirements limited to silver and magic, and reads
            "another invulnerable monster" as a monster bearing such a requirement
            itself.
        deprivation_penalties: A documented adaptation, default off. Food and water
            consumption is tracked either way; this flag controls whether going
            without carries penalties. The tabletop game leaves starvation penalties
            to the referee ("at the referee's discretion, for example..."); osrlib
            fixes a schedule drawn from the SRD's own examples — see the adaptations
            register. After one full day without food or water: −1 to attack rolls,
            and rest is needed twice as often (fatigue after three unrested turns
            instead of six). After two days: movement also halves. From the third day
            on: 1d4 hit points lost per day. Food and water deprivation don't stack —
            the worse track applies.
        magic_item_death_save: The SRD's referee-optional saving throw for magic
            items whose owner dies to a destructive effect, default on: each magic
            item in the doomed inventory rolls the owner's save values against the
            destructive source's category, adding the item's best combat bonus.
            Survivors land in a drop pile at the victim's cell rather than vanishing —
            surviving the blast but not the looting would be no survival at all.
        xp_award_timing: When XP awards fire, default `on_return` — the tabletop
            rule: XP for defeated monsters and recovered treasure is awarded when the
            party survives and returns to safety. `immediate` is a documented
            adaptation for continuous CRPG play: monster XP lands at each encounter's
            end, treasure XP at each acquisition, nothing more on reaching town, and
            dropped treasure never refunds.
        aoe_friendly_fire: A documented adaptation, default on: an area effect
            landing on a monster group at melee range catches engaged party members
            among its candidates. Off means area effects never include party members
            among a monster group's candidates.
        formation_width_limit: A documented adaptation, default on: passage width
            caps how many combatants fight abreast — three inside a keyed area, two
            in corridor cells — following the SRD's "2–3 characters fighting
            side-by-side in a 10' wide passage". Off lifts the cap: every combatant
            may melee.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hp_reroll_at_first_level: bool = False
    encumbrance: EncumbranceMode = EncumbranceMode.BASIC
    variable_weapon_damage: bool = True
    individual_initiative: bool = False
    thac0_arithmetic: bool = False
    weapon_reload: bool = False
    hd5_counts_as_magical: bool = False
    magic_item_death_save: bool = True
    xp_award_timing: XpAwardTiming = XpAwardTiming.ON_RETURN
    deprivation_penalties: bool = False
    aoe_friendly_fire: bool = True
    formation_width_limit: bool = True
