#!/usr/bin/env python3
"""Classifier-only eval of the ``jev`` router strategy over the labeled dataset.

Runs every case in ``tests/data/router_eval/cases.jsonl`` through
``JevStrategy.classify`` directly (no engine guards, no history) and reports
the same headline metrics as the Pilot gate (``eval_lib``) plus the
calibration numbers Jev makes possible because it returns real probabilities:

- accuracy, under/over-routing rate, severity-weighted under-routing,
  macro-F1, per-class recall, confusion matrix (all rows; a degraded
  ``jev_unavailable`` row counts as the default tier, as the engine would)
- the same block over healthy rows only
- ECE (10 bins on the top-1 probability) and NLL
- per-``lang`` accuracy slices
- ``high_risk_floor_applied`` count, ``jev_unavailable`` count by reason
- total usage tokens and the estimated cost at $0.042 / 1M input tokens

Every case costs one paid API call; the network half is not unit-tested.
``--dry-run`` prints the first request body and exits without a key.

Usage:
    uv run python scripts/pilot_router/evaluate_jev.py --dry-run --limit 1
    TYPESAFE_API_KEY=... uv run python scripts/pilot_router/evaluate_jev.py

For the engine-level view (guards, history, ``routing_source`` assertion) use
``uv run python scripts/router_eval.py --strategy jev`` instead.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
for _p in (SRC_DIR, REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agentos.agentos_router.jev import (  # noqa: E402
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_HIGH_RISK_THRESHOLD,
    DEFAULT_INPUT_MAX_CHARS,
    DEFAULT_MODEL,
    SOURCE_UNAVAILABLE,
    JevStrategy,
    build_jev_request,
    resolve_jev_api_key,
)
from scripts.pilot_router import eval_lib  # noqa: E402

DATA_DIR = REPO_ROOT / "tests" / "data" / "router_eval"
DEFAULT_CASES = DATA_DIR / "cases.jsonl"
#: ``scripts/pilot_router/data/`` is gitignored (see the repo ``.gitignore``).
DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "jev_eval.json"

CLASSES = list(eval_lib.CLASSES)
VALID_TIERS = ["c0", "c1", "c2", "c3"]
#: typesafe.ai list price for Jev input tokens (USD per 1M).
COST_PER_M_INPUT_TOKENS_USD = 0.042
ECE_BINS = 10

#: Mirrors ``scripts/router_eval.py:TIERS`` (descriptions only — the Jev
#: criteria are built from ``description``; provider/model are irrelevant to a
#: classifier-only run). ``tests/test_scripts/test_evaluate_jev.py`` pins the
#: two in sync so both evals see identical route criteria.
TIERS: dict[str, dict[str, str]] = {
    "c0": {"description": "short text and trivial follow-ups"},
    "c1": {"description": "normal coding and agent tasks"},
    "c2": {"description": "structured multi-step work"},
    "c3": {"description": "deep reasoning and hard recovery turns"},
}


# ---------------------------------------------------------------------------
# Config / data
# ---------------------------------------------------------------------------


def build_router_cfg(
    *,
    model: str = DEFAULT_MODEL,
    high_risk_threshold: float = DEFAULT_HIGH_RISK_THRESHOLD,
) -> SimpleNamespace:
    """Duck-typed router config: what ``JevStrategy`` reads, nothing more."""
    return SimpleNamespace(
        tiers=TIERS,
        default_tier="c1",
        routing_timeout_seconds=15.0,
        jev=SimpleNamespace(
            api_key=None,
            api_key_env=DEFAULT_API_KEY_ENV,
            base_url=DEFAULT_BASE_URL,
            model=model,
            input_max_chars=DEFAULT_INPUT_MAX_CHARS,
            high_risk_threshold=high_risk_threshold,
            timeout_seconds=None,
            short_circuit_enabled=True,
            agentic_floor_enabled=False,
        ),
    )


def load_cases(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


# ---------------------------------------------------------------------------
# Classify
# ---------------------------------------------------------------------------


async def classify_cases(
    strategy: JevStrategy, cases: list[dict[str, Any]], *, concurrency: int
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            started = time.perf_counter()
            _tier, confidence, source, extra = await strategy.classify(
                str(case["message"]), list(VALID_TIERS)
            )
            elapsed = time.perf_counter() - started
        return {
            "id": case.get("id"),
            "lang": case.get("lang"),
            "tags": list(case.get("tags") or []),
            "gold_class": case["gold_class"],
            "route_class": extra.get("route_class"),
            "pred_class": extra.get("final_route_class"),
            "source": source,
            "reason": extra.get("reason"),
            "probabilities": extra.get("probabilities"),
            "confidence": confidence,
            "high_risk_noul": extra.get("high_risk_noul"),
            "high_risk_floor_applied": bool(extra.get("high_risk_floor_applied")),
            "usage": extra.get("usage"),
            "latency_seconds": elapsed,
        }

    return await asyncio.gather(*(one(case) for case in cases))


# ---------------------------------------------------------------------------
# Reduce (pure)
# ---------------------------------------------------------------------------


def _prob_vector(row: dict[str, Any]) -> list[float]:
    probs = row.get("probabilities")
    if not isinstance(probs, dict) or not probs:
        return []
    return [float(probs.get(cls, 0.0) or 0.0) for cls in CLASSES]


def _metrics_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gold = eval_lib.to_indices([r["gold_class"] for r in rows])
    pred = eval_lib.to_indices([r["pred_class"] for r in rows])
    return eval_lib.compute_router_metrics(gold, pred).to_dict()


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce classified rows into the report dict (pure; no network)."""
    healthy = [r for r in rows if r.get("source") != SOURCE_UNAVAILABLE]
    unavailable = [r for r in rows if r.get("source") == SOURCE_UNAVAILABLE]

    gold_all = eval_lib.to_indices([r["gold_class"] for r in rows])
    probs_all = [_prob_vector(r) for r in rows]
    n_scored = sum(1 for p in probs_all if p)

    per_lang: dict[str, dict[str, Any]] = {}
    by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_lang[str(row.get("lang") or "unknown")].append(row)
    for lang in sorted(by_lang):
        subset = by_lang[lang]
        gold = eval_lib.to_indices([r["gold_class"] for r in subset])
        pred = eval_lib.to_indices([r["pred_class"] for r in subset])
        per_lang[lang] = {
            "n": len(subset),
            "accuracy": eval_lib.accuracy(gold, pred),
            "under_routing_rate": eval_lib.under_routing_rate(gold, pred),
            "over_routing_rate": eval_lib.over_routing_rate(gold, pred),
        }

    unavailable_by_reason: Counter[str] = Counter()
    for row in unavailable:
        reason = str(row.get("reason") or "")
        unavailable_by_reason[reason.removeprefix(f"{SOURCE_UNAVAILABLE}: ") or "unknown"] += 1

    input_tokens = 0
    output_tokens = 0
    for row in rows:
        usage = row.get("usage")
        if isinstance(usage, dict):
            input_tokens += int(usage.get("input_tokens", 0) or 0)
            output_tokens += int(usage.get("output_tokens", 0) or 0)

    short_circuit = sum(1 for r in healthy if "short-circuit" in str(r.get("reason") or ""))

    return {
        "n": len(rows),
        "n_healthy": len(healthy),
        "n_unavailable": len(unavailable),
        "short_circuit_count": short_circuit,
        "metrics": _metrics_block(rows),
        "metrics_healthy": _metrics_block(healthy),
        "calibration": {
            "n_scored": n_scored,
            "ece_bins": ECE_BINS,
            "ece": eval_lib.ece(probs_all, gold_all, bins=ECE_BINS),
            "nll": eval_lib.nll(probs_all, gold_all),
        },
        "per_lang": per_lang,
        "high_risk_floor_applied": sum(1 for r in rows if r.get("high_risk_floor_applied")),
        "unavailable_by_reason": dict(sorted(unavailable_by_reason.items())),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": input_tokens / 1_000_000 * COST_PER_M_INPUT_TOKENS_USD,
        },
    }


def json_safe(value: Any) -> Any:
    """Replace NaN/inf floats with ``None`` so the report is strict JSON."""
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Print
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def print_summary(report: dict[str, Any]) -> None:
    m = report["metrics"]
    h = report["metrics_healthy"]
    c = report["calibration"]
    u = report["usage"]
    print(
        f"cases: {report['n']}  healthy: {report['n_healthy']}  "
        f"unavailable: {report['n_unavailable']}  "
        f"short-circuit: {report['short_circuit_count']}"
    )
    print(
        f"accuracy: {_fmt(m['accuracy'])}  under: {_fmt(m['under_routing_rate'])}  "
        f"over: {_fmt(m['over_routing_rate'])}  "
        f"severity-under: {_fmt(m['severity_weighted_under_routing'])}  "
        f"macro-f1: {_fmt(m['macro_f1'])}"
    )
    print(f"healthy-only accuracy: {_fmt(h['accuracy'])} (n={h['n']})")
    print(
        "per-class recall: "
        + "  ".join(f"{cls}={_fmt(m['per_class_recall'][cls])}" for cls in CLASSES)
    )
    print("confusion (rows=gold, cols=pred):")
    for cls, line in zip(CLASSES, m["confusion_matrix"], strict=True):
        print(f"  {cls}: " + " ".join(f"{n:4d}" for n in line))
    print(
        f"calibration: ECE({c['ece_bins']} bins)={_fmt(c['ece'])}  NLL={_fmt(c['nll'])}  "
        f"(n_scored={c['n_scored']})"
    )
    for lang, block in report["per_lang"].items():
        print(
            f"lang {lang}: n={block['n']}  accuracy={_fmt(block['accuracy'])}  "
            f"under={_fmt(block['under_routing_rate'])}  "
            f"over={_fmt(block['over_routing_rate'])}"
        )
    print(f"high_risk floor applied: {report['high_risk_floor_applied']}")
    if report["unavailable_by_reason"]:
        print("jev_unavailable by reason:")
        for reason, count in report["unavailable_by_reason"].items():
            print(f"  {count:4d}  {reason}")
    print(
        f"usage: {u['input_tokens']} input / {u['output_tokens']} output tokens  "
        f"~${u['estimated_cost_usd']:.4f} at ${COST_PER_M_INPUT_TOKENS_USD}/1M input"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="labeled cases jsonl")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    parser.add_argument("--concurrency", type=int, default=4, help="parallel Jev calls")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSON report path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the first request body and exit without calling the API",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Jev model id")
    parser.add_argument(
        "--high-risk-threshold",
        type=float,
        default=DEFAULT_HIGH_RISK_THRESHOLD,
        help="high_risk noul at/above which the turn is floored at c3",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cases = load_cases(Path(args.cases), args.limit)
    if not cases:
        print(f"no cases in {args.cases}", file=sys.stderr)
        return 1
    router_cfg = build_router_cfg(model=args.model, high_risk_threshold=args.high_risk_threshold)

    if args.dry_run:
        body = build_jev_request(
            message=str(cases[0]["message"]),
            model=args.model,
            input_max_chars=DEFAULT_INPUT_MAX_CHARS,
            tiers=TIERS,
        )
        print(f"dry run: request body for case {cases[0].get('id')!r} (no network)")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        return 0

    key, _source = resolve_jev_api_key(router_cfg.jev)
    if not key:
        print(
            f"{DEFAULT_API_KEY_ENV} is not set. Export it "
            f"(or `agentos env set {DEFAULT_API_KEY_ENV} <key>` and `agentos env export`) "
            "before running; use --dry-run to inspect the request without a key.",
            file=sys.stderr,
        )
        return 2

    strategy = JevStrategy(router_cfg)
    print(
        f"classifying {len(cases)} cases via {args.model} "
        f"(concurrency={args.concurrency}, high_risk_threshold={args.high_risk_threshold})"
    )
    rows = asyncio.run(classify_cases(strategy, cases, concurrency=args.concurrency))
    report = summarize(rows)
    print_summary(report)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "cases": str(args.cases),
            "model": args.model,
            "high_risk_threshold": args.high_risk_threshold,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "report": json_safe(report),
        "rows": json_safe(rows),
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
