#!/usr/bin/env python3
"""Batch audits: publish a set of reports from the command line.

    python -m scripts.audit_many --samples
    python -m scripts.audit_many --dir netlists/          # *.hex / *.txt files
    python -m scripts.audit_many --task 3 --netlist 0x…

Reports are written to the same SQLite database the web app serves, so anything
audited here is immediately live at /a/<id>.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from podx import engine, samples, store


def _one(task_id: int, netlist_hex: str, circuit_id: int | None = None, crosscheck: bool = True) -> dict:
    result = engine.audit(
        engine.AuditRequest(
            task_id=task_id,
            netlist_hex=netlist_hex,
            circuit_id=circuit_id,
            crosscheck=crosscheck,
        )
    )
    audit_id = store.save_audit(result)
    base = ""
    from podx.config import settings

    base = settings().base_url
    print(
        f"{audit_id:>5}  task {task_id:<4} {result.code:<20} "
        f"C={result.economics.cost if result.economics else '-':<8} {(base + '/a/' + str(audit_id)) if base else ''}"
    )
    return {"id": audit_id, "code": result.code, "sentence": result.sentence}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", action="store_true", help="audit every built-in sample")
    ap.add_argument("--dir", type=Path, help="audit every *.hex/*.txt file in a directory")
    ap.add_argument("--task", type=int, help="task id for --netlist")
    ap.add_argument("--netlist", help="netlist hex")
    ap.add_argument("--circuit", type=int, help="circuit id to attach to the report")
    ap.add_argument("--no-crosscheck", action="store_true", help="skip the protocol verifier call")
    args = ap.parse_args(argv)

    store.init()
    rows: list[dict] = []
    crosscheck = not args.no_crosscheck

    if args.samples:
        for s in samples.SAMPLES:
            rows.append(_one(s.task_id, s.hex, crosscheck=crosscheck))
    if args.dir:
        files = sorted(list(args.dir.glob("*.hex")) + list(args.dir.glob("*.txt")))
        for f in files:
            text = f.read_text().strip()
            task_id = args.task
            if task_id is None:
                head = text.splitlines()[0]
                if head.startswith("#"):
                    try:
                        task_id = int(head[1:].split()[0])
                    except (ValueError, IndexError):
                        task_id = None
                    text = "\n".join(text.splitlines()[1:])
            if task_id is None:
                print(f"skip {f.name}: no task id (use --task)", file=sys.stderr)
                continue
            rows.append(_one(task_id, text.strip(), args.circuit, crosscheck))
    if args.netlist:
        if args.task is None:
            print("--task is required with --netlist", file=sys.stderr)
            return 2
        rows.append(_one(args.task, args.netlist, args.circuit, crosscheck))

    if not rows:
        ap.print_help()
        return 2
    bad = [r for r in rows if r["code"] not in ("PASS", "BEST-FIRST-AT-RISK")]
    print(f"\n{len(rows)} audits stored, {len(bad)} with a finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
