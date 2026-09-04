"""SZL Wave 1 report: consolidate harness receipts into one verifiable bakeoff report."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

GENESIS = "0" * 64

def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)

def _payload(receipt: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in receipt.items() if k not in ("prev_hash", "chain_hash")}

def verify_chain(receipts: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Verify hash linkage of one harness chain. Returns (ok, detail)."""
    if not receipts:
        return False, "empty chain"
    prev = GENESIS
    for i, r in enumerate(receipts):
        if r.get("prev_hash") != prev:
            return False, f"link broken at receipt {i}: prev_hash mismatch"
        expected = hashlib.sha256((prev + canonical(_payload(r))).encode("utf-8")).hexdigest()
        if r.get("chain_hash") != expected:
            return False, f"payload tampered at receipt {i}: chain_hash mismatch"
        prev = r["chain_hash"]
    return True, f"chain valid ({len(receipts)} receipts)"

@dataclass
class LaneResult:
    harness: str
    lane: str
    status: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    detail: str = ""

def _flatten(harness: str, receipt: Dict[str, Any]) -> List[LaneResult]:
    out: List[LaneResult] = []
    results = receipt.get("results")
    if not isinstance(results, list):
        return out
    for res in results:
        if not isinstance(res, dict):
            continue
        runs = res.get("runs") or []
        metrics = res.get("metrics")
        if runs and isinstance(metrics, dict):
            label = res.get("engine") or res.get("lane") or res.get("name") or "run"
            out.append(LaneResult(harness, str(label), "MEASURED",
                                  {k: v for k, v in metrics.items() if isinstance(v, (int, float, str))}))
        elif res.get("status") == "BLOCKED":
            label = res.get("engine") or res.get("lane") or "lane"
            out.append(LaneResult(harness, str(label), "BLOCKED", detail=str(res.get("detail", ""))[:120]))
        elif res.get("status") == "INVALID":
            label = res.get("engine") or res.get("lane") or "lane"
            out.append(LaneResult(harness, str(label), "INVALID", detail=str(res.get("detail", ""))[:120]))
    return out

def aggregate(chains: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """chains: harness name -> ordered receipt list. Fail closed on any bad chain."""
    harness_reports: Dict[str, Any] = {}
    terminal_hashes: Dict[str, str] = {}
    lanes: List[LaneResult] = []
    for name in sorted(chains):
        chain = chains[name]
        ok, detail = verify_chain(chain)
        if not ok:
            return {"report_status": "INVALID", "reason": f"{name}: {detail}"}
        harness_reports[name] = {"chain": detail, "receipts": len(chain)}
        terminal_hashes[name] = chain[-1]["chain_hash"]
        for r in chain:
            lanes.extend(_flatten(name, r))
    master_input = canonical({k: terminal_hashes[k] for k in sorted(terminal_hashes)})
    master_hash = hashlib.sha256(master_input.encode("utf-8")).hexdigest()
    return {"report_status": "VALID", "harnesses": harness_reports,
            "terminal_chain_hashes": terminal_hashes, "master_receipt_hash": master_hash,
            "measured_lanes": [l for l in lanes if l.status == "MEASURED"],
            "blocked_lanes": [l for l in lanes if l.status == "BLOCKED"],
            "invalid_lanes": [l for l in lanes if l.status == "INVALID"]}

def render_markdown(report: Dict[str, Any]) -> str:
    if report.get("report_status") != "VALID":
        return f"# SZL Wave 1 report\n\n**INVALID** — {report.get('reason', 'unknown')}\n"
    lines = ["# SZL Wave 1 report", "",
             f"Master receipt hash: `{report['master_receipt_hash'][:16]}...`", "",
             "## Chains", ""]
    for name, info in sorted(report["harnesses"].items()):
        lines.append(f"- `{name}` — {info['chain']}")
    lines += ["", "## Measured", "", "| Harness | Lane | Metrics |", "|---|---|---|"]
    for l in report["measured_lanes"]:
        m = ", ".join(f"{k}={v}" for k, v in sorted(l.metrics.items()))
        lines.append(f"| {l.harness} | {l.lane} | {m} |")
    if report["blocked_lanes"]:
        lines += ["", "## Blocked", ""]
        for l in report["blocked_lanes"]:
            lines.append(f"- `{l.harness}/{l.lane}` — {l.detail}")
    lines += ["", "Cross-harness numbers are never ranked against each other.",
              "Hardware and fairness keys are carried by each source receipt.", ""]
    return "\n".join(lines)
