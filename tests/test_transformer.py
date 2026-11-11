from decimal import Decimal

import xml.etree.ElementTree as ET

from mini_middleware.samples import make_message
from mini_middleware.transformer import fingerprint, to_canonical, to_erp_xml
from mini_middleware.validation import parse_and_validate


def _canonical(**kw):
    return to_canonical(parse_and_validate(make_message("M1", "SIM-OK-1", **kw)))


def test_idempotency_key_combines_sender_and_message_id():
    assert _canonical().idempotency_key == "PARTNER-A:M1"
    assert _canonical(sender="OTHER").idempotency_key == "OTHER:M1"


def test_pounds_are_converted_to_kilograms_and_rounded():
    msg = _canonical(weight="100", unit="LB")
    assert msg.gross_weight_kg == Decimal("45.36")
    assert _canonical(weight="12.345", unit="KG").gross_weight_kg == Decimal("12.35")


def test_erp_message_shape_and_event_code_mapping():
    erp = ET.fromstring(to_erp_xml(_canonical(status="ARRIVED", mode="AIR")))
    assert erp.tag == "ErpInboundMessage"
    assert erp.findtext("MessageKey") == "PARTNER-A:M1"
    assert erp.findtext("ExternalRef") == "SIM-OK-1"
    assert erp.findtext("EventCode") == "ARR"
    assert erp.findtext("TransportMode") == "AIR"
    assert erp.findtext("Cargo/WeightKg") == "1250.50"
    assert [p.get("role") for p in erp.iterfind("Parties/Party")] == ["SHIPPER", "CONSIGNEE"]


def test_fingerprint_ignores_whitespace_but_not_content():
    a = make_message("M1", "SIM-OK-1")
    assert fingerprint(a) == fingerprint(a.replace("\n    ", "\n\n        "))
    assert fingerprint(a) != fingerprint(make_message("M1", "SIM-OK-1", packages=13))
    assert fingerprint(a) == fingerprint(a.encode())
