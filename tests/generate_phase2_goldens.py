"""Regenerate the Phase 2 milestone battle goldens.

Run from the repo root:

```sh
uv run python tests/generate_phase2_goldens.py
```

A commit that changes the goldens must explain why in its message.
"""

import json

from test_phase2_goldens import TROLL_GOLDEN_PATH, WIGHT_GOLDEN_PATH, run_troll_battle, run_wight_battle


def main() -> None:
    """Write the two battle goldens for the pinned master seed."""
    for path, battle in ((TROLL_GOLDEN_PATH, run_troll_battle), (WIGHT_GOLDEN_PATH, run_wight_battle)):
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(battle(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
