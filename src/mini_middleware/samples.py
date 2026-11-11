"""Builders for illustrative sample messages (used by the demo and the tests)."""

from __future__ import annotations

NAMESPACE = "urn:example:illustrative:shipment-message:1"


def make_message(
    message_id: str,
    reference: str,
    *,
    sequence: int = 1,
    sender: str = "PARTNER-A",
    mode: str = "SEA",
    status: str = "DEPARTED",
    origin: str = "GBSOU",
    destination: str = "NLRTM",
    weight: str = "1250.50",
    unit: str = "KG",
    packages: int = 12,
    sent_at: str = "2026-03-01T09:30:00Z",
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ShipmentMessage xmlns="{NAMESPACE}" version="1.0">
  <Header>
    <MessageId>{message_id}</MessageId>
    <Sequence>{sequence}</Sequence>
    <SentAt>{sent_at}</SentAt>
    <Sender>{sender}</Sender>
  </Header>
  <Shipment>
    <Reference>{reference}</Reference>
    <Mode>{mode}</Mode>
    <Status>{status}</Status>
    <Origin>{origin}</Origin>
    <Destination>{destination}</Destination>
    <Parties>
      <Party role="SHIPPER"><Name>Example Exports Ltd</Name></Party>
      <Party role="CONSIGNEE"><Name>Sample Imports BV</Name></Party>
    </Parties>
    <GrossWeight unit="{unit}">{weight}</GrossWeight>
    <PackageCount>{packages}</PackageCount>
    <Events>
      <Event code="DEP" occurredAt="2026-03-01T08:00:00Z"/>
    </Events>
  </Shipment>
</ShipmentMessage>
"""
