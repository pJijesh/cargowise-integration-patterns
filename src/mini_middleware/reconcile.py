"""Reconciliation: compare what we believe we delivered with what the ERP says it holds."""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field

from .delivery import ErpClient
from .dlq import DeadLetterStore
from .idempotency import IdempotencyStore


@dataclass
class ReconciliationReport:
    generated_at: str
    local_delivered: int
    erp_records: int
    missing_in_erp: list[dict] = field(default_factory=list)
    unexpected_in_erp: list[dict] = field(default_factory=list)
    reference_mismatches: list[dict] = field(default_factory=list)
    open_dead_letters: int = 0
    stuck_in_progress: list[dict] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not (
            self.missing_in_erp
            or self.unexpected_in_erp
            or self.reference_mismatches
            or self.open_dead_letters
            or self.stuck_in_progress
        )

    def as_dict(self) -> dict:
        d = asdict(self)
        d["clean"] = self.clean
        return d

    def render_text(self) -> str:
        lines = [
            f"Reconciliation report ({self.generated_at})",
            f"  delivered according to middleware : {self.local_delivered}",
            f"  records held by ERP               : {self.erp_records}",
            f"  message states                    : {self.status_counts or '{}'}",
            f"  open dead letters                 : {self.open_dead_letters}",
        ]
        for title, rows in (
            ("MISSING_IN_ERP (we say delivered, ERP has no record)", self.missing_in_erp),
            ("UNEXPECTED_IN_ERP (ERP has it, we have no delivery record)", self.unexpected_in_erp),
            ("REFERENCE_MISMATCH (same key, different shipment reference)", self.reference_mismatches),
            ("STUCK_IN_PROGRESS (lease expired, worker probably died)", self.stuck_in_progress),
        ):
            if rows:
                lines.append(f"  {title}:")
                lines.extend(f"    - {row}" for row in rows)
        if self.open_dead_letters:
            lines.append("  OPEN_DEAD_LETTERS: review with `dlq list`, then replay or discard")
        lines.append("  result: " + ("CLEAN" if self.clean else "DISCREPANCIES FOUND"))
        return "\n".join(lines)


def reconcile(
    store: IdempotencyStore, dead_letters: DeadLetterStore, client: ErpClient
) -> ReconciliationReport:
    local = {r["idempotency_key"]: r for r in store.delivered()}
    remote = {r["key"]: r for r in client.fetch_records()}
    report = ReconciliationReport(
        generated_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        local_delivered=len(local),
        erp_records=len(remote),
        open_dead_letters=dead_letters.count_open(),
        stuck_in_progress=store.stuck(),
        status_counts=store.counts(),
    )
    for key, row in sorted(local.items()):
        if key not in remote:
            report.missing_in_erp.append({"key": key, "reference": row["reference"]})
        elif remote[key].get("reference") != row["reference"]:
            report.reference_mismatches.append(
                {"key": key, "ours": row["reference"], "erp": remote[key].get("reference")}
            )
    for key, row in sorted(remote.items()):
        if key not in local:
            report.unexpected_in_erp.append({"key": key, "reference": row.get("reference")})
    return report
