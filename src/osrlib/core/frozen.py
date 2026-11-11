"""The read-only mapping in the frozen content models, like the numbers in a monster ability's `params`.

The class, monster, item, spell, and table models that [`osrlib.data`][osrlib.data] loads are
frozen, and each loader returns one cached catalog that every caller in the process shares. A field
of those models that maps names to values, like
[`MonsterAbility.params`][osrlib.core.monsters.MonsterAbility.params], is a
[`FrozenMap`][osrlib.core.frozen.FrozenMap], so nothing in a catalog can be changed at any depth. A
change to the troll's regeneration would otherwise change every troll in every session. An
[`Adventure`][osrlib.crawl.adventure.Adventure] is shared the same way by every session you start
from it, so its mapping fields, like a level's
[`edges`][osrlib.crawl.dungeon.LevelSpec.edges], are `FrozenMap`s too.

You read a `FrozenMap` the way you read a dict. When you build a model, you pass a dict for the
field, and the model stores a `FrozenMap` of it. `model_dump` gives you a dict again. To change an
entry, build a new model. Either dump the one you have, change the dump, and validate it, or copy
it with `model_copy(update=...)`. Every osrlib model validates a copy that has an update, so the
copy stores a `FrozenMap` of a dict you pass, and it raises `pydantic.ValidationError` for a value
that building the model would refuse.

Typical usage:

```python
from osrlib.core.monsters import MonsterTemplate
from osrlib.data import load_monsters

troll = load_monsters().get("troll")
print(troll.ability("regeneration").params["per_round"])
# 3

# A bog troll that regenerates faster, built from a dump of the troll.
document = troll.model_dump()
for ability in document["abilities"]:
    if ability["tag"] == "regeneration":
        ability["params"]["per_round"] = 5
bog_troll = MonsterTemplate.model_validate(
    {**document, "id": "bog_troll", "name": "Bog Troll", "display_name": "bog troll", "family_id": "bog_troll"}
)
print(bog_troll.ability("regeneration").params["per_round"], troll.ability("regeneration").params["per_round"])
# 5 3

# The same change to the ability alone, as a copy.
regeneration = troll.ability("regeneration")
faster = regeneration.model_copy(update={"params": {**regeneration.params, "per_round": 5}})
print(faster.params["per_round"], type(faster.params).__name__)
# 5 FrozenMap
```
"""

from collections.abc import ItemsView, Iterable, Iterator, KeysView, Mapping, ValuesView
from typing import Any, Self, get_args, get_origin, overload

from pydantic import BaseModel, GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

__all__ = [
    "FrozenMap",
]


class FrozenMap[K, V](Mapping[K, V]):
    """A mapping whose entries can't be changed, the type of each mapping field in the content models.

    The content models are the class, monster, item, spell, and table models that
    [`osrlib.data`][osrlib.data] loads, the [`EffectDefinition`][osrlib.core.effects.EffectDefinition]
    that an effect runs from, and the models an [`Adventure`][osrlib.crawl.adventure.Adventure] or a
    [`ContentPack`][osrlib.crawl.content_pack.ContentPack] contains. A session's state, like its
    dungeon state and its flags, isn't content, and its mappings are dicts you can change.

    Read one the way you read a dict: index it, call `get`, iterate it, or test a key with `in`.
    Assigning or deleting an entry raises `TypeError`, and it has no `update`, `pop`, or `clear`.
    It compares equal to a dict with the same entries, so `ability.params == {"per_round": 3}` is
    True. It's hashable when its values are, and `copy.deepcopy` and `pickle` copy it.

    You rarely build one yourself. A model field annotated `Annotated[Mapping[K, V], FrozenMap]`
    accepts any mapping, like a dict, and stores a `FrozenMap` of it, both when you build the model
    and when you copy it with `model_copy(update=...)`. The type checker reads the
    field as a `Mapping`, which has no methods that change it. The field's `model_dump` value is a
    new dict, which you can change and pass to `model_validate` to build a new model. For a dict
    you can change without building a model, call `dict()` on the mapping.

    To give a model of your own a mapping field that can't be changed, annotate it the same way,
    and use `FrozenMap()` as the default for an empty one.

    Examples:
        ```python
        from osrlib.data import load_monsters

        params = load_monsters().get("troll").ability("regeneration").params
        print(params["per_round"], params["delay_rounds"], params["blocked_by"])
        # 3 3 ('fire', 'acid')
        print(params == {"blocked_by": ("fire", "acid"), "delay_rounds": 3, "per_round": 3, "revive": "2d6"})
        # True

        mine = dict(params)  # a dict of your own, which you can change
        mine["per_round"] = 5
        print(mine["per_round"], params["per_round"])
        # 5 3
        ```
    """

    __slots__ = ("_entries",)

    _entries: dict[K, V]

    def __init__(self, entries: Mapping[K, V] | Iterable[tuple[K, V]] = (), /) -> None:
        """Copy the entries of a mapping, or of an iterable of key and value pairs, into a new `FrozenMap`.

        Args:
            entries: The entries, like a dict. A later change to it doesn't change the `FrozenMap`.
                Leave it out for an empty one.
        """
        self._entries = dict(entries)

    def __getitem__(self, key: K) -> V:
        """Return the value for `key`, and raise `KeyError` when the mapping has no entry for it."""
        return self._entries[key]

    def __iter__(self) -> Iterator[K]:
        """Iterate over the keys, in the order the entries were given."""
        return iter(self._entries)

    def __len__(self) -> int:
        """Return the number of entries."""
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        """Return whether the mapping has an entry for `key`."""
        return key in self._entries

    @overload
    def get(self, key: K, /) -> V | None: ...

    @overload
    def get(self, key: K, default: V, /) -> V: ...

    @overload
    def get[T](self, key: K, default: T, /) -> V | T: ...

    def get(self, key: K, default: Any = None, /) -> Any:
        """Return the value for `key`, or `default` when the mapping has no entry for it."""
        return self._entries.get(key, default)

    def keys(self) -> KeysView[K]:
        """Return a read-only view of the keys, in the order the entries were given."""
        return self._entries.keys()

    def values(self) -> ValuesView[V]:
        """Return a read-only view of the values, in the order the entries were given."""
        return self._entries.values()

    def items(self) -> ItemsView[K, V]:
        """Return a read-only view of the key and value pairs, in the order the entries were given."""
        return self._entries.items()

    def __eq__(self, other: object) -> bool:
        """Return whether `other` is a mapping with the same entries, in any order."""
        if isinstance(other, FrozenMap):
            return self._entries == other._entries
        if isinstance(other, dict):
            return self._entries == other
        if isinstance(other, Mapping):
            return self._entries == dict(other.items())
        return NotImplemented

    def __hash__(self) -> int:
        """Return a hash of the entries, and raise `TypeError` when a value isn't hashable."""
        return hash(frozenset(self._entries.items()))

    def __repr__(self) -> str:
        """Return `FrozenMap(...)` around the repr of a dict with the same entries."""
        return f"FrozenMap({self._entries!r})"

    def __reduce__(self) -> tuple[type[Self], tuple[dict[K, V]]]:
        """Return how `pickle` and `copy` rebuild the mapping: the class, called with a dict of the entries."""
        return (type(self), (self._entries,))

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        """Return the schema pydantic validates and dumps the field with: a dict validated as `Mapping[K, V]`.

        Validation stores the dict in a new `FrozenMap`, and dumping returns a dict. As `Annotated` metadata,
        `source` is the `Mapping[K, V]` the metadata follows. A JSON schema encodes a `FrozenMap()` default with
        the class as its own type, so that case builds the same `Mapping[K, V]` schema.
        """
        origin = get_origin(source) or source
        if isinstance(origin, type) and issubclass(origin, FrozenMap):
            key, value = get_args(source) or (Any, Any)
            mapping = handler.generate_schema(Mapping[key, value])
        else:
            mapping = handler(source)
        return core_schema.no_info_after_validator_function(
            cls._from_validated,
            mapping,
            serialization=core_schema.plain_serializer_function_ser_schema(_as_dict, return_schema=mapping),
        )

    @classmethod
    def _from_validated(cls, entries: dict[K, V]) -> Self:
        # Pydantic builds a new dict for every validation, so no caller has a reference to this one.
        frozen = cls.__new__(cls)
        frozen._entries = entries
        return frozen


def _as_dict(value: Mapping[Any, Any]) -> dict[Any, Any]:
    """Return the entries the serializer dumps. A model that `model_construct` built can contain a plain dict."""
    return value._entries if isinstance(value, FrozenMap) else dict(value)


class _Model(BaseModel):
    """The base class of every osrlib model. It validates a copy made with `model_copy(update=...)`.

    Pydantic's `model_copy` sets the fields in `update` without validating them. Every osrlib model is frozen or
    validates each assignment, so a copy that skipped validation could break a rule that building the model or
    assigning a field enforces.
    """

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Return a copy of the model, with the fields in `update` changed.

        A copy with an update is validated the way building the model is. It raises the `ValidationError` that
        building a model with the same fields would raise, and a dict you pass for a read-only mapping field becomes a
        [`FrozenMap`][osrlib.core.frozen.FrozenMap]. Validation reuses the models nested in the copy, so a copy costs
        about as much as building the model with its top-level fields. A copy with no update isn't validated again.

        Args:
            update: The fields to change, by name, with their new values. Leave it out for a copy with the same
                fields.
            deep: Whether to copy the values of the fields too, as `copy.deepcopy` does. The values in `update`
                aren't copied.

        Returns:
            The copy. The model you copied doesn't change.

        Raises:
            pydantic.ValidationError: If building the model with the copy's fields would raise it.
        """
        copied = super().model_copy(update=update, deep=deep)
        if not update:
            return copied
        validated = type(self).model_validate(copied.__dict__)
        # Pydantic's copy marks the original's set fields and the updated ones as set. Its private state comes from
        # the class's __copy__, which can clear a hook that mustn't reach a copy.
        fields_set = {name for name in copied.__pydantic_fields_set__ if name in type(self).model_fields}
        object.__setattr__(validated, "__pydantic_fields_set__", fields_set)
        object.__setattr__(validated, "__pydantic_private__", copied.__pydantic_private__)
        return validated
