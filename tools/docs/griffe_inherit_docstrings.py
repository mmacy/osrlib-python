"""A griffe extension that gives an undocumented attribute override its parent's docstring.

Each command class redeclares `command_type` and `allowed_modes` to fix their values, and a
redeclaration has no docstring, so without this extension the reference renders those fields as
bare names. The `Command` base class documents both. This extension copies the nearest documented
ancestor's docstring onto each undocumented override at build time, so every command page
describes the fields without the source repeating the same text in every class.
"""

import griffe


class InheritAttributeDocstrings(griffe.Extension):
    """Copy a documented ancestor's attribute docstring onto an undocumented override."""

    def on_package(self, *, pkg: griffe.Module, **kwargs: object) -> None:
        """Walk the loaded package once every module is loaded, so base classes resolve."""
        self._walk(pkg)

    def _walk(self, obj: griffe.Object) -> None:
        for member in obj.members.values():
            if member.is_alias:
                continue
            if member.is_class:
                self._inherit(member)
            if member.is_module or member.is_class:
                self._walk(member)

    def _inherit(self, cls: griffe.Class) -> None:
        for name, member in cls.members.items():
            if member.is_alias or not member.is_attribute or member.docstring is not None:
                continue
            for ancestor in cls.mro():
                inherited = ancestor.members.get(name)
                if inherited is not None and not inherited.is_alias and inherited.docstring is not None:
                    member.docstring = griffe.Docstring(inherited.docstring.value, parent=member)
                    break
