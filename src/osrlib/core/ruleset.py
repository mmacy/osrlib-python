"""The `Ruleset` model: optional-rule and adaptation flags.

Every SRD optional rule and every documented adaptation is a named flag with a
default. Flags are read at resolution time, so a `Ruleset` is fixed for the life of a
session (it participates in saves and replays). The model is frozen and rejects unknown
flags — a typo'd flag name errors instead of silently doing nothing.

Phase 1 defines only the flags Phase 1 reads. The spec's remaining 1.0 flags are added
by the phases that implement their behavior; shipping a flag whose behavior doesn't
exist yet would be a lie in the API. Additive flag growth is schema-legal.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "EncumbranceMode",
    "Ruleset",
]


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

    Attributes:
        hp_reroll_at_first_level: SRD optional rule: re-roll starting hit-point rolls
            of 1–2 (the raw die, before the CON modifier) until the die shows 3 or
            more. Default off.
        encumbrance: Which encumbrance system tracks carried weight and drives
            movement rates. Default basic.
        variable_weapon_damage: SRD optional rule, default on. Off means every weapon
            *and gear combat facet* deals 1d6 (RAW: "PC attacks inflict 1d6 damage");
            unarmed attacks stay 1d2 (the specific unarmed rule, not a weapon) and
            monster damage is unaffected (monsters always "deal the damage indicated
            in the description") — pinned.
        individual_initiative: SRD optional rule, default off: 1d6 per participant,
            DEX-modified for characters (plus the halfling's `initiative_bonus` tag);
            monsters take a caller-supplied modifier (default 0), the RAW
            referee-judgment surface.
        thac0_arithmetic: SRD optional rule, default off: replaces the attack-matrix
            lookup with unclamped `THAC0 − AC` subtraction. The ascending-AC attack
            procedure is algebraically identical, so this one flag covers both
            presentations; the matrix differs only through its 2..20 clamping.
        weapon_reload: SRD optional rule, default off: a reload-quality weapon may
            not fire two rounds running. The attack validator rejects when the
            caller-supplied context says the weapon fired last round; round
            bookkeeping is the Phase 4 battle machine's job — the kernel enforces the
            rule given honest context (pinned).
        hd5_counts_as_magical: SRD invulnerabilities optional rule, default off,
            implemented *in full*: both a monster of 5+ HD and another invulnerable
            monster bypass silver/magic-only gates. Pinned boundary from the rule's
            own wording: the flag touches only weapon-material gates whose keys are a
            subset of {silver, magic}, and "another invulnerable monster" means a
            monster bearing such a gate.
        deprivation_penalties: Documented adaptation, default off. Consumption is
            tracked regardless; with the flag on, the SRD's "at the referee's
            discretion, for example" starvation list gets pinned defaults drawn from
            its own examples: after one full day without food or water, −1 to attack
            rolls (an engine-written modifier effect) and the rest cadence doubles
            (fatigue after three unrested turns instead of six); after two days,
            movement also halves; from the third day on, a daily 1d4 hit-point loss
            ticks on the effects stream. Water and food deprivation don't stack —
            the worse track applies. The schedule's numbers are invented over the
            SRD's open list (see `docs/adaptations.md`).
        magic_item_death_save: The SRD's referee-optional save for magic items on a
            destructive death, default on (the spec's default): each magic item in
            the doomed inventory rolls the owner's save values against the
            destructive source's category, plus the item's best combat bonus;
            survivors land in a drop pile at the victim's cell instead of
            vanishing (pinned — surviving the blast but not the looting would be
            no survival at all).
        aoe_friendly_fire: Documented adaptation, default on: an area effect landing
            on a monster group at melee range catches engaged party members among
            its candidates (the Phase 4 footprint rule). Off means areas never
            include party members among a monster group's candidates.
        formation_width_limit: Documented adaptation, default on: corridor width
            caps combatants fighting abreast — rank width 3 inside a keyed area and
            2 in corridor cells (RAW's "2–3 characters in a 10' passage"). Off lifts
            the cap: every combatant may melee.
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
    deprivation_penalties: bool = False
    aoe_friendly_fire: bool = True
    formation_width_limit: bool = True
