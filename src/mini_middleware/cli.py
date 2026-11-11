"""Command-line interface: python -m mini_middleware <command>."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .adapters.folder import FolderAdapter
from .adapters.http_inbound import make_server
from .config import Config
from .logging_setup import configure_logging
from .mock_erp import MockErp
from .pipeline import Outcome, Pipeline
from .reconcile import reconcile


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mini_middleware",
        description="Reference implementation of integration patterns (illustrative, unofficial).",
    )
    p.add_argument("--db", help="SQLite database path (env: MM_DB_PATH)")
    p.add_argument("--erp-url", help="ERP endpoint (env: MM_ERP_URL)")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="command", required=True)

    ing = sub.add_parser("ingest", help="process one or more XML files now")
    ing.add_argument("files", nargs="+", type=Path)

    w = sub.add_parser("watch", help="poll a folder for XML files")
    w.add_argument("--dir", type=Path, default=Path("data/drop"))
    w.add_argument("--interval", type=float, default=2.0)
    w.add_argument("--once", action="store_true", help="scan once and exit")

    s = sub.add_parser("serve", help="run the HTTP inbound endpoint")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)

    m = sub.add_parser("mock-erp", help="run the mock ERP server")
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--port", type=int, default=8099)
    m.add_argument("--slow-seconds", type=float, default=2.0)

    d = sub.add_parser("dlq", help="inspect and replay dead letters")
    dsub = d.add_subparsers(dest="dlq_command", required=True)
    dl = dsub.add_parser("list")
    dl.add_argument("--status", default="DEAD", help="DEAD, REPLAYED, DISCARDED or ALL")
    dshow = dsub.add_parser("show")
    dshow.add_argument("id", type=int)
    drep = dsub.add_parser("replay")
    drep.add_argument("id", type=int)
    drep.add_argument("--payload-file", type=Path, help="corrected XML to send instead")
    dall = dsub.add_parser("replay-all")
    dall.add_argument("--reason", help="only replay dead letters with this reason code")
    ddis = dsub.add_parser("discard")
    ddis.add_argument("id", type=int)
    ddis.add_argument("--reason", required=True)

    r = sub.add_parser("reconcile", help="compare local delivery records with the ERP")
    r.add_argument("--json", action="store_true")

    sub.add_parser("demo", help="run the scripted end-to-end demonstration")
    return p


def _config(args: argparse.Namespace) -> Config:
    cfg = Config.from_env()
    changes = {}
    if args.db:
        changes["db_path"] = Path(args.db)
    if args.erp_url:
        changes["erp_url"] = args.erp_url
    return dataclasses.replace(cfg, **changes) if changes else cfg


def _print_result(label: str, result) -> None:
    print(f"{label}: {result.outcome.value} correlation_id={result.correlation_id}"
          + (f" reason={result.reason}" if result.reason else "")
          + (f" dlq_id={result.dlq_id}" if result.dlq_id else ""))


def _dlq_table(rows: list[dict]) -> str:
    if not rows:
        return "(no dead letters)"
    out = [f"{'ID':>4}  {'STATUS':<9} {'REASON':<20} {'TRIES':>5}  {'KEY':<28} DETAIL"]
    for r in rows:
        out.append(
            f"{r['id']:>4}  {r['status']:<9} {r['reason']:<20} {r['attempts']:>5}  "
            f"{(r['idempotency_key'] or '-'):<28} {r['detail'][:60]}"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    configure_logging(args.log_level)

    if args.command == "demo":
        from .demo import run_demo

        return run_demo()

    cfg = _config(args)

    if args.command == "mock-erp":
        erp = MockErp(args.host, args.port, token=cfg.erp_token, slow_seconds=args.slow_seconds)
        print(f"mock ERP listening on {erp.url}", file=sys.stderr)
        try:
            erp.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    pipeline = Pipeline(cfg)

    if args.command == "ingest":
        worst = 0
        for f in args.files:
            result = pipeline.process(f.read_bytes(), source=f"file:{f.name}")
            _print_result(f.name, result)
            worst = 1 if result.outcome is Outcome.DEAD_LETTERED else worst
        return worst

    if args.command == "watch":
        adapter = FolderAdapter(args.dir, pipeline, max_bytes=cfg.max_message_bytes)
        if args.once:
            adapter.recover_orphans()
            for name, result in adapter.scan_once():
                if result:
                    _print_result(name, result)
            return 0
        try:
            adapter.watch(args.interval)
        except KeyboardInterrupt:
            pass
        return 0

    if args.command == "serve":
        server = make_server(pipeline, args.host, args.port, cfg.http_token, cfg.max_message_bytes)
        print(f"inbound endpoint on http://{args.host}:{args.port}/messages", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    if args.command == "dlq":
        dlq = pipeline.dlq
        if args.dlq_command == "list":
            print(_dlq_table(dlq.list(None if args.status.upper() == "ALL" else args.status.upper())))
            return 0
        if args.dlq_command == "show":
            row = dlq.get(args.id)
            if not row:
                print(f"no dead letter {args.id}", file=sys.stderr)
                return 1
            print(json.dumps(row, indent=2))
            return 0
        if args.dlq_command == "replay":
            override = args.payload_file.read_bytes() if args.payload_file else None
            try:
                result = pipeline.replay(args.id, payload_override=override)
            except (KeyError, ValueError) as exc:
                print(str(exc), file=sys.stderr)
                return 1
            _print_result(f"replay {args.id}", result)
            return 1 if result.outcome is Outcome.DEAD_LETTERED else 0
        if args.dlq_command == "replay-all":
            failures = 0
            for row in dlq.list("DEAD"):
                if args.reason and row["reason"] != args.reason:
                    continue
                result = pipeline.replay(row["id"])
                _print_result(f"replay {row['id']}", result)
                failures += result.outcome is Outcome.DEAD_LETTERED
            return 1 if failures else 0
        if args.dlq_command == "discard":
            dlq.discard(args.id, args.reason)
            print(f"discarded {args.id}")
            return 0

    if args.command == "reconcile":
        report = reconcile(pipeline.store, pipeline.dlq, pipeline.client)
        print(json.dumps(report.as_dict(), indent=2) if args.json else report.render_text())
        return 0 if report.clean else 2

    return 2  # pragma: no cover
