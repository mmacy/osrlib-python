"""The refusal a validator hands back when the rules say no.

The module has one member,
[`Rejection`][osrlib.core.validation.Rejection]. You don't usually build one, you read
them. Every kernel validator returns a list of them, empty when the input is legal, and
so does every command a session refuses to run. Ask them what went wrong and turn them
into whatever your interface shows the player.

Each rules area pairs a validator with the function that applies the change:
[`validate_adjustment`][osrlib.core.abilities.validate_adjustment] with
[`apply_adjustment`][osrlib.core.abilities.apply_adjustment],
[`validate_purchase`][osrlib.core.items.validate_purchase] with the purchase itself,
and so on. Call the validator, show the rejections if there are any, and call the apply
step only on an empty list. Applying a change its validator refuses is a mistake in your
code rather than a move the rules disallow, so the apply step raises `ValueError`
instead of returning a rejection. The exceptions osrlib raises for the other kinds of
mistake are in [`osrlib.errors`][osrlib.errors].

Validation runs as a pure pre-phase: a refused input draws no randomness, advances no
game time, and changes nothing, so you can offer a move, show why it was refused, and
let the player pick again with the game in exactly the state it was.

Typical usage:

```python
from osrlib.core.abilities import AbilityAdjustment, AbilityScore, validate_adjustment

scores = {
    AbilityScore.STR: 12,
    AbilityScore.INT: 13,
    AbilityScore.WIS: 9,
    AbilityScore.DEX: 11,
    AbilityScore.CON: 14,
    AbilityScore.CHA: 10,
}

# Lowering WIS below 9 is against the rules, so the validator refuses it.
adjustment = AbilityAdjustment(lowered={AbilityScore.WIS: 2}, raised={AbilityScore.STR: 1})
rejections = validate_adjustment(scores, adjustment, prime_requisites=(AbilityScore.STR,))
assert [rejection.code for rejection in rejections] == ["creation.adjustment.below_floor"]
assert rejections[0].params == {"ability": "wis", "score": 9, "amount": 2}
```
"""

import re

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = [
    "Rejection",
]

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


class Rejection(BaseModel):
    """One reason the rules refused an input, as a code plus the facts behind it.

    A validator returns these. Match on `code` to decide what to tell the player, and read
    `params` for the numbers and names to put in the sentence. Match on the code rather
    than on any text, because osrlib never puts English in a rejection. Write your own
    wording for the codes your game can produce, and show the code itself for the rest.
    [The rejection code reference][rejection-codes] lists every code the engine emits with
    what it means.

    Build one yourself only when you're writing a validator of your own, for a house
    rule or a custom item, and want it to refuse in the same shape the kernel does.

    The model is frozen, so you can keep a rejection around and compare rejections for
    equality.

    Examples:
        ```python
        from osrlib.core.validation import Rejection

        rejection = Rejection(code="items.equip.armour_forbidden", params={"class": "magic_user"})
        assert rejection.code.split(".")[0] == "items"
        assert rejection.params["class"] == "magic_user"
        ```
    """

    model_config = ConfigDict(frozen=True)

    code: str
    """The refusal's identity: two or more snake_case segments joined by dots, the first naming the subsystem.

    `creation.class.requirements_not_met` and `items.equip.armour_forbidden` are both
    codes. Any other shape raises a pydantic `ValidationError` when the model is built.
    Match on this value to choose what to show the player.
    """

    params: dict[str, int | str | tuple[int | str, ...]] = {}
    """The facts the refusal turns on, such as the ability that was too low or the class that may not wear the armour.

    Values are integers, strings, or tuples of either. Which keys appear depends on the
    code, so read the keys the codes you handle use and skip any you don't recognize.
    """

    @field_validator("code")
    @classmethod
    def _code_must_be_dotted_snake_case(cls, value: str) -> str:
        if _CODE_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "rejection code must be two or more dot-separated snake_case segments "
                f"(like 'creation.class.requirements_not_met'), got {value!r}"
            )
        return value
