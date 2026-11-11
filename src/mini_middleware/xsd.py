"""A deliberately small XSD validator built on the standard library.

The Python standard library cannot validate XML against an XSD. Rather than add a
dependency, this module implements the subset of XSD 1.0 that the illustrative schema
in this repository uses:

* top-level `xs:element`, named `xs:simpleType` and `xs:complexType`
* `xs:sequence` with `minOccurs` / `maxOccurs`
* `xs:attribute` with `use="required"`
* `xs:simpleContent` / `xs:extension`
* restriction facets: enumeration, pattern, minInclusive, maxInclusive, minLength, maxLength
* built-ins: string, decimal, integer, positiveInteger, nonNegativeInteger, boolean, date, dateTime

Not supported: xs:choice, xs:all, xs:import/include, substitution groups, identity
constraints, mixed content. Built-in types must use the `xs:` prefix. For anything beyond
this subset, use a full validator such as lxml. The test suite cross-checks this
implementation against lxml when lxml is installed.
"""

from __future__ import annotations

import datetime as _dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

XS = "{http://www.w3.org/2001/XMLSchema}"
_DATETIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?"
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(Z|[+-]\d{2}:\d{2})?")
_DECIMAL_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")
_INTEGER_RE = re.compile(r"[+-]?\d+")


class SchemaError(Exception):
    """Raised when the schema uses a feature outside the supported subset."""


@dataclass
class SimpleType:
    base: "str | SimpleType"
    enumeration: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    min_inclusive: Decimal | None = None
    max_inclusive: Decimal | None = None
    min_length: int | None = None
    max_length: int | None = None


@dataclass
class AttrDecl:
    name: str
    type: "str | SimpleType"
    required: bool


@dataclass
class ComplexType:
    children: list["ElementDecl"] = field(default_factory=list)
    attributes: list[AttrDecl] = field(default_factory=list)
    text_type: "str | SimpleType | None" = None  # set for simpleContent


@dataclass
class ElementDecl:
    name: str
    type: "str | SimpleType | ComplexType"
    min_occurs: int = 1
    max_occurs: int | None = 1  # None means unbounded


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_builtin(ref: str) -> bool:
    return ref.startswith("xs:") or ref.startswith("xsd:")


def _check_builtin(name: str, text: str) -> str | None:
    """Return an error message, or None if `text` is a valid lexical value."""
    if name == "string":
        return None
    value = text.strip()
    if name == "decimal":
        return None if _DECIMAL_RE.fullmatch(value) else f"{text!r} is not a valid decimal"
    if name in ("integer", "int", "positiveInteger", "nonNegativeInteger"):
        if not _INTEGER_RE.fullmatch(value):
            return f"{text!r} is not a valid integer"
        number = int(value)
        if name == "positiveInteger" and number < 1:
            return f"{text!r} must be a positive integer"
        if name == "nonNegativeInteger" and number < 0:
            return f"{text!r} must not be negative"
        return None
    if name == "boolean":
        return None if value in ("true", "false", "1", "0") else f"{text!r} is not a boolean"
    if name == "date":
        if not _DATE_RE.fullmatch(value):
            return f"{text!r} is not a valid date"
        try:
            _dt.date.fromisoformat(value[:10])
        except ValueError:
            return f"{text!r} is not a real calendar date"
        return None
    if name == "dateTime":
        if not _DATETIME_RE.fullmatch(value):
            return f"{text!r} is not a valid dateTime"
        try:
            _dt.datetime.fromisoformat(value)
        except ValueError:
            return f"{text!r} is not a real date and time"
        return None
    raise SchemaError(f"unsupported built-in type xs:{name}")


def _builtin_root(t: "str | SimpleType") -> str:
    while isinstance(t, SimpleType):
        t = t.base
    return t


def _check_simple(t: "str | SimpleType", text: str) -> list[str]:
    if isinstance(t, str):
        err = _check_builtin(t, text)
        return [err] if err else []
    errors = _check_simple(t.base, text)
    if errors:
        return errors
    root = _builtin_root(t)
    value = text if root == "string" else text.strip()
    if t.enumeration and value not in t.enumeration:
        errors.append(f"{value!r} is not one of {t.enumeration}")
    for pattern in t.patterns:
        if not re.fullmatch(pattern, value):
            errors.append(f"{value!r} does not match pattern {pattern!r}")
    if t.min_length is not None and len(value) < t.min_length:
        errors.append(f"{value!r} is shorter than {t.min_length}")
    if t.max_length is not None and len(value) > t.max_length:
        errors.append(f"{value!r} is longer than {t.max_length}")
    if t.min_inclusive is not None or t.max_inclusive is not None:
        try:
            number = Decimal(value)
        except InvalidOperation:
            return errors + [f"{value!r} is not numeric"]
        if t.min_inclusive is not None and number < t.min_inclusive:
            errors.append(f"{value} is below the minimum {t.min_inclusive}")
        if t.max_inclusive is not None and number > t.max_inclusive:
            errors.append(f"{value} is above the maximum {t.max_inclusive}")
    return errors


class Schema:
    def __init__(self, root: ET.Element) -> None:
        if root.tag != f"{XS}schema":
            raise SchemaError("not an xs:schema document")
        self.target_namespace = root.get("targetNamespace", "")
        self.qualified = root.get("elementFormDefault") == "qualified"
        self._raw_simple: dict[str, ET.Element] = {}
        self._raw_complex: dict[str, ET.Element] = {}
        self._simple_cache: dict[str, SimpleType] = {}
        self._complex_cache: dict[str, ComplexType] = {}
        self._roots: dict[str, ElementDecl] = {}
        top_elements = []
        for child in root:
            if child.tag == f"{XS}simpleType":
                self._raw_simple[child.get("name", "")] = child
            elif child.tag == f"{XS}complexType":
                self._raw_complex[child.get("name", "")] = child
            elif child.tag == f"{XS}element":
                top_elements.append(child)
        for el in top_elements:
            decl = self._parse_element(el)
            self._roots[self._qname(decl.name, top_level=True)] = decl

    # ---- parsing -----------------------------------------------------------------

    def _qname(self, name: str, top_level: bool = False) -> str:
        if (top_level or self.qualified) and self.target_namespace:
            return f"{{{self.target_namespace}}}{name}"
        return name

    def _resolve_type(self, ref: str) -> "str | SimpleType | ComplexType":
        if _is_builtin(ref):
            return ref.split(":", 1)[1]
        local = ref.split(":")[-1]
        if local in self._raw_simple:
            return self._simple(local)
        if local in self._raw_complex:
            return self._complex(local)
        raise SchemaError(f"unknown type {ref!r}")

    def _simple(self, name: str) -> SimpleType:
        if name not in self._simple_cache:
            self._simple_cache[name] = self._parse_simple(self._raw_simple[name])
        return self._simple_cache[name]

    def _complex(self, name: str) -> ComplexType:
        if name not in self._complex_cache:
            self._complex_cache[name] = self._parse_complex(self._raw_complex[name])
        return self._complex_cache[name]

    def _simple_ref(self, ref: str) -> "str | SimpleType":
        resolved = self._resolve_type(ref)
        if isinstance(resolved, ComplexType):
            raise SchemaError(f"{ref!r} is a complex type where a simple type is required")
        return resolved

    def _parse_simple(self, node: ET.Element) -> SimpleType:
        restriction = node.find(f"{XS}restriction")
        if restriction is None:
            raise SchemaError("only xs:restriction simple types are supported")
        st = SimpleType(base=self._simple_ref(restriction.get("base", "")))
        for facet in restriction:
            tag, value = _local(facet.tag), facet.get("value", "")
            if tag == "enumeration":
                st.enumeration.append(value)
            elif tag == "pattern":
                st.patterns.append(value)
            elif tag == "minInclusive":
                st.min_inclusive = Decimal(value)
            elif tag == "maxInclusive":
                st.max_inclusive = Decimal(value)
            elif tag == "minLength":
                st.min_length = int(value)
            elif tag == "maxLength":
                st.max_length = int(value)
            else:
                raise SchemaError(f"unsupported facet xs:{tag}")
        return st

    def _parse_attribute(self, node: ET.Element) -> AttrDecl:
        return AttrDecl(
            name=node.get("name", ""),
            type=self._simple_ref(node.get("type", "xs:string")),
            required=node.get("use") == "required",
        )

    def _parse_complex(self, node: ET.Element) -> ComplexType:
        ct = ComplexType()
        for child in node:
            tag = _local(child.tag)
            if tag == "sequence":
                for item in child:
                    if item.tag != f"{XS}element":
                        raise SchemaError("only xs:element is supported inside xs:sequence")
                    ct.children.append(self._parse_element(item))
            elif tag == "attribute":
                ct.attributes.append(self._parse_attribute(child))
            elif tag == "simpleContent":
                ext = child.find(f"{XS}extension")
                if ext is None:
                    raise SchemaError("only xs:extension is supported in xs:simpleContent")
                ct.text_type = self._simple_ref(ext.get("base", ""))
                for attr in ext.findall(f"{XS}attribute"):
                    ct.attributes.append(self._parse_attribute(attr))
            else:
                raise SchemaError(f"unsupported complexType content xs:{tag}")
        return ct

    def _parse_element(self, node: ET.Element) -> ElementDecl:
        max_raw = node.get("maxOccurs", "1")
        decl_type: "str | SimpleType | ComplexType"
        inline_complex = node.find(f"{XS}complexType")
        inline_simple = node.find(f"{XS}simpleType")
        if inline_complex is not None:
            decl_type = self._parse_complex(inline_complex)
        elif inline_simple is not None:
            decl_type = self._parse_simple(inline_simple)
        else:
            decl_type = self._resolve_type(node.get("type", "xs:string"))
        return ElementDecl(
            name=node.get("name", ""),
            type=decl_type,
            min_occurs=int(node.get("minOccurs", "1")),
            max_occurs=None if max_raw == "unbounded" else int(max_raw),
        )

    # ---- validation --------------------------------------------------------------

    def validate(self, root: ET.Element) -> list[str]:
        """Return a list of human-readable errors. An empty list means valid."""
        decl = self._roots.get(root.tag)
        if decl is None:
            known = ", ".join(sorted(self._roots))
            return [f"unexpected root element {root.tag!r}; expected one of: {known}"]
        errors: list[str] = []
        self._check_element(root, decl, "/" + _local(root.tag), errors)
        return errors

    def _check_element(
        self, el: ET.Element, decl: ElementDecl, path: str, errors: list[str]
    ) -> None:
        t = decl.type
        attrs = {k: v for k, v in el.attrib.items() if not k.startswith("{")}
        if isinstance(t, (str, SimpleType)):
            if len(el):
                errors.append(f"{path}: child elements are not allowed here")
            for name in attrs:
                errors.append(f"{path}: attribute {name!r} is not allowed")
            errors.extend(f"{path}: {m}" for m in _check_simple(t, el.text or ""))
            return

        declared = {a.name for a in t.attributes}
        for attr in t.attributes:
            if attr.name not in attrs:
                if attr.required:
                    errors.append(f"{path}: missing required attribute {attr.name!r}")
                continue
            errors.extend(
                f"{path}/@{attr.name}: {m}" for m in _check_simple(attr.type, attrs[attr.name])
            )
        for name in attrs:
            if name not in declared:
                errors.append(f"{path}: attribute {name!r} is not allowed")

        if t.text_type is not None:
            if len(el):
                errors.append(f"{path}: child elements are not allowed here")
            errors.extend(f"{path}: {m}" for m in _check_simple(t.text_type, el.text or ""))
            return

        if (el.text or "").strip():
            errors.append(f"{path}: text is not allowed in element-only content")
        kids = list(el)
        idx = 0
        for child_decl in t.children:
            qname = self._qname(child_decl.name)
            count = 0
            while (
                idx < len(kids)
                and kids[idx].tag == qname
                and (child_decl.max_occurs is None or count < child_decl.max_occurs)
            ):
                count += 1
                child_path = f"{path}/{child_decl.name}"
                if child_decl.max_occurs != 1:
                    child_path += f"[{count}]"
                self._check_element(kids[idx], child_decl, child_path, errors)
                idx += 1
            if count < child_decl.min_occurs:
                errors.append(f"{path}: missing required element {child_decl.name!r}")
        if idx < len(kids):
            errors.append(
                f"{path}: unexpected element {_local(kids[idx].tag)!r} "
                "(unknown, repeated too often or out of order)"
            )


def load_schema(path: str | Path) -> Schema:
    return Schema(ET.parse(path).getroot())
