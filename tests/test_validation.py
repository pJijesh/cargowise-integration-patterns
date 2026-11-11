import pytest

from mini_middleware.samples import NAMESPACE, make_message
from mini_middleware.validation import (
    MALFORMED_XML,
    SCHEMA_PATH,
    VALIDATION_FAILED,
    ValidationFailed,
    parse_and_validate,
)


def test_valid_message_passes():
    root = parse_and_validate(make_message("M1", "SIM-OK-1"))
    assert root.tag == f"{{{NAMESPACE}}}ShipmentMessage"


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"mode": "TELEPORT"}, "TELEPORT"),
        ({"message_id": "bad id!"}, "does not match pattern"),
        ({"weight": "-5"}, "below the minimum"),
        ({"weight": "heavy"}, "not a valid decimal"),
        ({"unit": "STONE"}, "STONE"),
        ({"packages": 0}, "positive integer"),
        ({"sent_at": "yesterday"}, "not a valid dateTime"),
        ({"sent_at": "2026-13-45T00:00:00Z"}, "not a real date"),
        ({"origin": "GB"}, "does not match pattern"),
        ({"sequence": 0}, "positive integer"),
    ],
)
def test_invalid_field_values_are_rejected(kwargs, fragment):
    with pytest.raises(ValidationFailed) as exc:
        kwargs = dict(kwargs)
        message_id = kwargs.pop("message_id", "M1")
        parse_and_validate(make_message(message_id, "SIM-OK-1", **kwargs))
    assert exc.value.reason == VALIDATION_FAILED
    assert any(fragment in e for e in exc.value.errors), exc.value.errors


def test_missing_required_element_is_reported_with_path():
    xml = make_message("M1", "SIM-OK-1").replace("<Mode>SEA</Mode>", "")
    with pytest.raises(ValidationFailed) as exc:
        parse_and_validate(xml)
    assert any("/ShipmentMessage/Shipment" in e for e in exc.value.errors)


def test_out_of_order_elements_are_rejected():
    xml = make_message("M1", "SIM-OK-1").replace(
        "<Mode>SEA</Mode>\n    <Status>DEPARTED</Status>",
        "<Status>DEPARTED</Status>\n    <Mode>SEA</Mode>",
    )
    with pytest.raises(ValidationFailed):
        parse_and_validate(xml)


def test_unknown_element_and_attribute_are_rejected():
    xml = make_message("M1", "SIM-OK-1").replace("</Shipment>", "<Surprise/></Shipment>")
    with pytest.raises(ValidationFailed):
        parse_and_validate(xml)
    xml = make_message("M1", "SIM-OK-1").replace('" version="1.0">', '" version="1.0" extra="x">')
    with pytest.raises(ValidationFailed):
        parse_and_validate(xml)


def test_unsupported_version_or_namespace_is_rejected():
    with pytest.raises(ValidationFailed):
        parse_and_validate(make_message("M1", "SIM-OK-1").replace('" version="1.0">', '" version="2.0">'))
    with pytest.raises(ValidationFailed):
        parse_and_validate(make_message("M1", "SIM-OK-1").replace(":shipment-message:1", ":shipment-message:2"))


def test_malformed_xml_is_reported_as_malformed():
    with pytest.raises(ValidationFailed) as exc:
        parse_and_validate("<ShipmentMessage><oops></ShipmentMessage>")
    assert exc.value.reason == MALFORMED_XML


def test_doctype_and_entities_are_refused():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>'
    with pytest.raises(ValidationFailed) as exc:
        parse_and_validate(evil)
    assert exc.value.reason == MALFORMED_XML


def test_optional_events_block_may_be_absent():
    xml = make_message("M1", "SIM-OK-1")
    start, end = xml.index("<Events>"), xml.index("</Events>") + len("</Events>")
    parse_and_validate(xml[:start] + xml[end:])


def _lxml_cases():
    base = make_message("M1", "SIM-OK-1")
    yield "valid", base
    yield "bad-mode", make_message("M1", "SIM-OK-1", mode="TELEPORT")
    yield "negative-weight", make_message("M1", "SIM-OK-1", weight="-1")
    yield "zero-packages", make_message("M1", "SIM-OK-1", packages=0)
    yield "bad-ref", make_message("M1", "bad ref")
    yield "bad-date", make_message("M1", "SIM-OK-1", sent_at="2026-03-01")
    yield "missing-mode", base.replace("<Mode>SEA</Mode>", "")
    yield "swapped", base.replace(
        "<Mode>SEA</Mode>\n    <Status>DEPARTED</Status>",
        "<Status>DEPARTED</Status>\n    <Mode>SEA</Mode>",
    )
    yield "extra-element", base.replace("</Shipment>", "<Surprise/></Shipment>")
    yield "bad-version", base.replace('" version="1.0">', '" version="9">')
    yield "no-parties", base.replace(
        base[base.index("<Parties>") : base.index("</Parties>") + len("</Parties>")], "<Parties/>"
    )


def test_agrees_with_lxml_on_the_same_cases():
    """Cross-check the stdlib subset validator against a full XSD engine, when available."""
    lxml_etree = pytest.importorskip("lxml.etree")
    schema = lxml_etree.XMLSchema(lxml_etree.parse(str(SCHEMA_PATH)))
    for name, xml in _lxml_cases():
        expected = schema.validate(lxml_etree.fromstring(xml.encode()))
        try:
            parse_and_validate(xml)
            actual = True
        except ValidationFailed:
            actual = False
        assert actual == expected, f"{name}: ours={actual} lxml={expected}"
