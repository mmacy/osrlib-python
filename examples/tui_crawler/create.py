"""Party creation for the crawler: interactive prompts, or the fixed script party.

Both paths drive the Phase 1 creation kernel; the game owns prompting and choice,
the kernel owns the dice and the rules.
"""

from osrlib.core.alignment import Alignment
from osrlib.core.character import create_character
from osrlib.core.rng import RngStream
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.party import Party

# The scripted party: one of each role, kit bought from starting gold.
_SCRIPT_PARTY = (
    (
        "Brakka",
        "fighter",
        Alignment.LAWFUL,
        (("sword", 1), ("chainmail", 1), ("shield", 1)),
        ("sword", "chainmail", "shield"),
        (),
    ),
    ("Wynn", "cleric", Alignment.LAWFUL, (("mace", 1), ("chainmail", 1)), ("mace", "chainmail"), ()),
    ("Sable", "thief", Alignment.NEUTRAL, (("sword", 1), ("leather", 1)), ("sword", "leather"), ()),
    ("Elandril", "magic_user", Alignment.NEUTRAL, (("dagger", 1),), ("dagger",), ("sleep",)),
)

_CLASS_CHOICES = ("cleric", "dwarf", "elf", "fighter", "halfling", "magic_user", "thief")
_ALIGNMENTS = {"l": Alignment.LAWFUL, "n": Alignment.NEUTRAL, "c": Alignment.CHAOTIC}


def scripted_party(stream: RngStream, ruleset: Ruleset) -> Party:
    """Build the fixed script party — the non-interactive and test path."""
    members = []
    for name, class_id, alignment, purchases, equip_ids, spells in _SCRIPT_PARTY:
        result = create_character(
            name=name,
            class_id=class_id,
            alignment=alignment,
            ruleset=ruleset,
            stream=stream,
            starting_spell_ids=spells,
            purchases=purchases,
            equip_ids=equip_ids,
        )
        members.append(result.character)
    return Party(members=members)


def interactive_party(stream: RngStream, ruleset: Ruleset) -> Party:
    """Prompt for a party of four, driving the same creation kernel."""
    members = []
    print("Create your party of four.")
    for slot in range(1, 5):
        name = input(f"[{slot}/4] Name: ").strip() or f"Adventurer {slot}"
        class_id = ""
        while class_id not in _CLASS_CHOICES:
            class_id = input(f"  Class {_CLASS_CHOICES}: ").strip()
        alignment = None
        while alignment is None:
            alignment = _ALIGNMENTS.get(input("  Alignment [l/n/c]: ").strip().lower())
        spells = ("sleep",) if class_id in ("magic_user", "elf") else ()
        try:
            result = create_character(
                name=name,
                class_id=class_id,
                alignment=alignment,
                ruleset=ruleset,
                stream=stream,
                starting_spell_ids=spells,
            )
        except ValueError as error:
            print(f"  The dice refuse ({error}); rolling this hero again.")
            continue
        character = result.character
        print(f"  {character.name} the {class_id}: HP {character.max_hp}, gold {character.inventory.purse.gp}")
        members.append(character)
    return Party(members=members)
