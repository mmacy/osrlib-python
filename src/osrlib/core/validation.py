"""The refusal a validator returns when the rules don't allow an input.

The module has one member,
[`Rejection`][osrlib.core.validation.Rejection]. You usually read rejections rather than
build them. Every kernel validator returns a list of them, empty when the input is
legal. When a session refuses a command, the
[`CommandResult`][osrlib.crawl.commands.CommandResult] it returns lists them too. Read
their codes to find out what went wrong, and turn them into the message you show the
player.

Each rules area has a validator and a function that applies the change:
[`validate_adjustment`][osrlib.core.abilities.validate_adjustment] with
[`apply_adjustment`][osrlib.core.abilities.apply_adjustment],
[`validate_purchase`][osrlib.core.items.validate_purchase] with
[`purchase`][osrlib.core.items.purchase], and so on. Call the validator, show the
rejections if there are any, and call the apply function only on an empty list. Passing
the apply function a change that its validator refuses is a mistake in your code, not a
player's illegal move, so the apply function raises `ValueError` instead of returning a
rejection. The exceptions
osrlib raises for other kinds of failure are in [`osrlib.errors`][osrlib.errors].

Validators run before anything changes. When a validator refuses an input, osrlib draws
no random numbers, advances no game time, and changes nothing. You can offer a move, show
why the validator refused it, and let the player pick again with the game in the same
state.

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
    """One reason a validator refused an input, as a code plus the facts behind it.

    A validator returns these. Match on `code` to decide what to tell the player, and read
    `params` for the numbers and names to put in the sentence. osrlib writes no English
    into a rejection, so write your own wording for the codes you expect, and show the
    code itself for the rest.
    [The rejection code reference](https://mmacy.github.io/osrlib-python/reference/rejection-codes/)
    lists every code osrlib returns, with what it means.

    Build one yourself only when you write your own validator, for a house rule or a
    custom item, and want it to report a refusal the same way osrlib does.

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
    """Which refusal this is, as a dotted message code like `items.equip.armour_forbidden`.

    Match on this value to choose what to show the player. A code is two or more
    snake_case segments joined by dots, and the first segment is the subsystem that
    refused. Building a rejection with any other format raises a pydantic
    `ValidationError`.
    """

    params: dict[str, int | str | tuple[int | str, ...]] = {}
    """The facts behind the refusal, like the ability that was too low or the class that may not wear the armour.

    Values are integers, strings, or tuples of either. Which keys appear depends on the
    code, so read the keys of the codes you handle and skip any you don't recognize. A
    string is an id or a value name, except where an adventure's author wrote the text,
    like a gate's refusal line in the `refusal` key.
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
