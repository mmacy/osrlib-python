"""A griffe extension that shows the bases of a private base class in its place.

Every osrlib model subclasses the private `osrlib.core.frozen._Model`, which subclasses pydantic's
`BaseModel`. The reference doesn't document private names, so without this extension each model
page would list `_Model` as its base, with no link. This extension replaces a private base in a
class's bases with that base's own bases, so a model page lists `BaseModel`, linked to pydantic's
reference.
"""

import griffe


class ShowPublicBases(griffe.Extension):
    """Replace each private base class in a class's bases with the bases of that private class."""

    def on_package(self, *, pkg: griffe.Module, **kwargs: object) -> None:
        """Walk the loaded package once every module is loaded, so each private base resolves."""
        self._walk(pkg)

    def _walk(self, obj: griffe.Object) -> None:
        for member in obj.members.values():
            if member.is_alias:
                continue
            if member.is_class:
                member.bases = self._public_bases(member)
            if member.is_module or member.is_class:
                self._walk(member)

    def _public_bases(self, cls: griffe.Class) -> list[griffe.Expr | str]:
        bases: list[griffe.Expr | str] = []
        for base in cls.bases:
            path = base.canonical_path if isinstance(base, griffe.Expr) else base
            private = path.rsplit(".", 1)[-1].startswith("_")
            target = self._loaded_class(cls, path) if private else None
            if target is None:
                bases.append(base)
            else:
                bases.extend(self._public_bases(target))
        return bases

    def _loaded_class(self, cls: griffe.Class, path: str) -> griffe.Class | None:
        try:
            target = cls.modules_collection[path]
        except KeyError:
            return None
        return target if isinstance(target, griffe.Class) else None
