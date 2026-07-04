"""Regenerate the Phase 3 milestone spell goldens.

Run from the repo root:

```sh
uv run python tests/generate_phase3_goldens.py
```

A commit that changes the goldens must explain why in its message.
"""

import json

from test_phase3_goldens import (
    SPELL_BATTLE_GOLDEN_PATH,
    TURNING_GOLDEN_PATH,
    run_spell_battle,
    run_turning_scenario,
)


def main() -> None:
    """Write the two spell goldens for the pinned master seed."""
    for path, scenario in (
        (SPELL_BATTLE_GOLDEN_PATH, run_spell_battle),
        (TURNING_GOLDEN_PATH, run_turning_scenario),
    ):
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(scenario(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
