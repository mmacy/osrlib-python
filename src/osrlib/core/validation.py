"""Structured rejection reasons returned by pure validators.

Kernel validators (class choice, the ability adjustment step, purchase and equip
legality) return lists of [`Rejection`][osrlib.core.validation.Rejection] values rather
than raising: an illegal *choice* is an in-fiction refusal, not a programmer error.
Session command rejections carry these values verbatim in the `CommandResult` envelope.

Calling an apply-step with input its validator rejects — applying an illegal
adjustment, equipping forbidden armour — is programmer misuse and raises stdlib
`ValueError`, per the errors convention in [`osrlib.errors`][osrlib.errors].
"""

import re

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = [
    "Rejection",
]

_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


class Rejection(BaseModel):
    """A structured reason a validator refused an input.

    `code` is dotted snake_case namespaced by subsystem, like event message codes
    (`creation.class.requirements_not_met`, `items.equip.armour_forbidden`). `params`
    carries the structured facts a front end needs to render the refusal — never baked
    English prose.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    params: dict[str, int | str | tuple[int | str, ...]] = {}

    @field_validator("code")
    @classmethod
    def _code_must_be_dotted_snake_case(cls, value: str) -> str:
        if _CODE_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "rejection code must be two or more dot-separated snake_case segments "
                f"(like 'creation.class.requirements_not_met'), got {value!r}"
            )
        return value
