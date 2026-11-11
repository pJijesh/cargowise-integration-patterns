"""Map the inbound illustrative message to a canonical form, then to the ERP-side format.

Both formats are invented for this repository. They are not CargoWise formats.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

NS = "{urn:example:illustrative:shipment-message:1}"
LB_TO_KG = Decimal("0.45359237")

_STATUS_TO_EVENT = {
    "BOOKED": "BKG",
    "DEPARTED": "DEP",
    "ARRIVED": "ARR",
    "DELIVERED": "DLV",
    "CANCELLED": "CNL",
}


@dataclass(frozen=True)
class CanonicalShipment:
    message_id: str
    sender: str
    sequence: int
    sent_at: str
    reference: str
    mode: str
    status: str
    origin: str
    destination: str
    gross_weight_kg: Decimal
    package_count: int
    parties: tuple[tuple[str, str], ...]
    events: tuple[tuple[str, str], ...]

    @property
    def idempotency_key(self) -> str:
        """Business-level deduplication key: who sent it plus their message id."""
        return f"{self.sender}:{self.message_id}"


def _text(parent: ET.Element, name: str) -> str:
    el = parent.find(f"{NS}{name}")
    return (el.text or "").strip() if el is not None else ""


def to_canonical(root: ET.Element) -> CanonicalShipment:
    """Convert a validated message tree into the canonical dataclass."""
    header = root.find(f"{NS}Header")
    shipment = root.find(f"{NS}Shipment")
    assert header is not None and shipment is not None, "call after schema validation"
    weight_el = shipment.find(f"{NS}GrossWeight")
    assert weight_el is not None
    weight = Decimal((weight_el.text or "0").strip())
    if weight_el.get("unit") == "LB":
        weight = weight * LB_TO_KG
    weight = weight.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    parties = tuple(
        (p.get("role", ""), _text(p, "Name")) for p in shipment.iterfind(f"{NS}Parties/{NS}Party")
    )
    events = tuple(
        (e.get("code", ""), e.get("occurredAt", ""))
        for e in shipment.iterfind(f"{NS}Events/{NS}Event")
    )
    return CanonicalShipment(
        message_id=_text(header, "MessageId"),
        sender=_text(header, "Sender"),
        sequence=int(_text(header, "Sequence")),
        sent_at=_text(header, "SentAt"),
        reference=_text(shipment, "Reference"),
        mode=_text(shipment, "Mode"),
        status=_text(shipment, "Status"),
        origin=_text(shipment, "Origin"),
        destination=_text(shipment, "Destination"),
        gross_weight_kg=weight,
        package_count=int(_text(shipment, "PackageCount")),
        parties=parties,
        events=events,
    )


def to_erp_xml(msg: CanonicalShipment) -> bytes:
    """Render the ERP-side message (a different, equally illustrative, shape)."""
    root = ET.Element("ErpInboundMessage", {"schemaVersion": "1"})
    ET.SubElement(root, "MessageKey").text = msg.idempotency_key
    ET.SubElement(root, "ExternalRef").text = msg.reference
    ET.SubElement(root, "TransportMode").text = msg.mode
    ET.SubElement(root, "EventCode").text = _STATUS_TO_EVENT[msg.status]
    route = ET.SubElement(root, "Route")
    ET.SubElement(route, "From").text = msg.origin
    ET.SubElement(route, "To").text = msg.destination
    parties = ET.SubElement(root, "Parties")
    for role, name in msg.parties:
        ET.SubElement(parties, "Party", {"role": role}).text = name
    cargo = ET.SubElement(root, "Cargo")
    ET.SubElement(cargo, "WeightKg").text = str(msg.gross_weight_kg)
    ET.SubElement(cargo, "Pieces").text = str(msg.package_count)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def fingerprint(raw: bytes | str) -> str:
    """Hash of the canonicalised XML, so whitespace-only differences do not count."""
    canonical = ET.canonicalize(raw, strip_text=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
