# Example rulesets

Each JSON file here is a whole [`Ruleset`](https://mmacy.github.io/osrlib-python/guides/ruleset-options/), with every field written out, so a copy shows every rule a game can change.

## Creation house rules

`creation_house_rules.json` turns on two house rules for character creation, so it's not the SRD as written. Neither rule is in the SRD, and `Ruleset()` leaves both off:

- `ability_score_method` is `"4d6_drop_lowest"`, so each ability score is the sum of the highest three of four d6 instead of 3d6.
- `max_hp_at_first_level` is `true`, so a first-level character's hit die counts as its maximum, plus the CON modifier.

Every other field has the value `Ruleset()` gives it. To use the ruleset, load the file and pass the result where you'd pass a `Ruleset`, such as to `create_character` and `GameSession.new`:

```python
from pathlib import Path

from osrlib.core.ruleset import Ruleset

rules = Ruleset.model_validate_json(Path("examples/rulesets/creation_house_rules.json").read_text(encoding="utf-8"))
```
