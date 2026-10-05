#!/usr/bin/env python3
"""
Per-phase token usage + cost report for youtube-skill-fetch playlists.

Reads `distilled/<playlist>/cost.json` (written by the phase orchestrators
via `scripts/accounting.py`) and prints a per-phase breakdown.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PHASE_ORDER = ["phase2", "phase3", "phase4", "topical", "summary", "eval"]


def load_cost(playlist: str) -> dict | None:
    path = Path("distilled") / playlist / "cost.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def sort_phases(phases: dict) -> list[tuple[str, dict]]:
    known = [(n, phases[n]) for n in PHASE_ORDER if n in phases]
    extras = [(n, p) for n, p in phases.items() if n not in PHASE_ORDER]
    return known + sorted(extras)


def fmt_int(n: int) -> str:
    return f"{n:,}"


def fmt_usd(x: float) -> str:
    return f"${x:.4f}"


def print_table(playlist: str, data: dict) -> None:
    print(f"\nPlaylist: {playlist}")
    print(f"  Generated: {data.get('generated_at', 'unknown')}")
    print()
    header = f"  {'Phase':<10} {'Model':<32} {'Calls':>6}  {'Input':>12}  {'Output':>10}  {'Cache R':>10}  {'Cache W':>10}  {'Cost':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    phases = sort_phases(data.get("phases", {}))
    tot_calls = tot_in = tot_out = tot_cr = tot_cw = 0
    tot_cost = 0.0
    for name, p in phases:
        calls = p.get("calls", 0)
        inp = p.get("input_tokens", 0)
        out = p.get("output_tokens", 0)
        cr = p.get("cache_read_tokens", 0)
        cw = p.get("cache_write_tokens", 0)
        cost = p.get("estimated_cost_usd", 0.0)
        tot_calls += calls
        tot_in += inp
        tot_out += out
        tot_cr += cr
        tot_cw += cw
        tot_cost += cost
        print(
            f"  {name:<10} {p.get('model',''):<32} "
            f"{calls:>6}  {fmt_int(inp):>12}  {fmt_int(out):>10}  "
            f"{fmt_int(cr):>10}  {fmt_int(cw):>10}  {fmt_usd(cost):>10}"
        )
    print("  " + "-" * (len(header) - 2))
    print(
        f"  {'TOTAL':<10} {'':<32} "
        f"{tot_calls:>6}  {fmt_int(tot_in):>12}  {fmt_int(tot_out):>10}  "
        f"{fmt_int(tot_cr):>10}  {fmt_int(tot_cw):>10}  {fmt_usd(tot_cost):>10}"
    )

    reported_total = data.get("total_estimated_cost_usd")
    if reported_total is not None and abs(reported_total - tot_cost) > 0.0005:
        print(
            f"  (note: cost.json reports total={fmt_usd(reported_total)}, "
            f"recomputed={fmt_usd(tot_cost)})"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("playlists", nargs="+", help="playlist name(s) under distilled/")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    missing: list[str] = []
    results: dict[str, dict] = {}
    for name in args.playlists:
        data = load_cost(name)
        if data is None:
            missing.append(name)
        else:
            results[name] = data

    if args.json:
        json.dump({"playlists": results, "missing": missing}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if results else 1

    for name in missing:
        print(
            f"WARN: distilled/{name}/cost.json not found — "
            f"no Claude phase has run for this playlist yet.",
            file=sys.stderr,
        )

    for name, data in results.items():
        print_table(name, data)

    if len(results) > 1:
        grand = sum(d.get("total_estimated_cost_usd", 0.0) for d in results.values())
        print(f"\nGrand total across {len(results)} playlists: {fmt_usd(grand)}")

    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
