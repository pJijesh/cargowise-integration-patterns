"""Parsing and schema validation at the ingest boundary."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from .xsd import Schema, load_schema

SCHEMA_PATH = Path(__file__).parent / "schemas" / "shipment_message.xsd"
_DOCTYPE_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)

MALFORMED_XML = "MALFORMED_XML"
VALIDATION_FAILED = "VALIDATION_FAILED"


class ValidationFailed(Exception):
    """The message cannot be processed. Retrying will never help (a poison message)."""

    def __init__(self, reason: str, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.reason = reason
        self.errors = errors


@lru_cache(maxsize=1)
def get_schema() -> Schema:
    return load_schema(SCHEMA_PATH)


def parse_and_validate(raw: bytes | str) -> ET.Element:
    """Parse `raw` and validate it against the illustrative XSD.

    DOCTYPE and ENTITY declarations are rejected outright. Business messages have no need
    for them, and refusing them removes the entity-expansion class of attacks without
    needing a third-party parser.
    """
    probe = raw.encode("utf-8") if isinstance(raw, str) else raw
    if _DOCTYPE_RE.search(probe):
        raise ValidationFailed(MALFORMED_XML, ["DOCTYPE and ENTITY declarations are not accepted"])
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValidationFailed(MALFORMED_XML, [f"XML is not well formed: {exc}"]) from exc
    errors = get_schema().validate(root)
    if errors:
        raise ValidationFailed(VALIDATION_FAILED, errors)
    return root
