"""Regenerate the Phase 1 creation golden file.

Run from the repo root:

```sh
uv run python tests/generate_creation_goldens.py
```

A commit that changes the golden must explain why in its message.
"""

import json

from osrlib.core.character import party_to_document
from osrlib.core.rng import RngStreams
from test_creation_goldens import GOLDEN_PATH, MASTER_SEED, build_golden_party


def main() -> None:
    """Write the golden party document for the pinned master seed."""
    results = build_golden_party(RngStreams(master_seed=MASTER_SEED))
    document = party_to_document([result.character for result in results])
    GOLDEN_PATH.parent.mkdir(exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN_PATH}")


if __name__ == "__main__":
    main()
