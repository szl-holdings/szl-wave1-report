"""SZL Wave 1 report: consolidate harness receipts into one verifiable bakeoff report."""
from __future__ import annotations
import hashlib, json, math
from html import escape
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple
from .native import declared_harness, schema, verify

GENESIS = "0" * 64
REQUIRED_HARNESSES = ("szl-calibration", "szl-engine-bench", "szl-quant-bench",
                      "szl-retrieval-bench")

def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)

def _payload(receipt: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in receipt.items() if k not in ("prev_hash", "chain_hash")}

def verify_chain(receipts: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Verify hash linkage of one harness chain. Returns (ok, detail)."""
    return verify(receipts)

@dataclass
class LaneResult:
    harness: str
    lane: str
    status: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    detail: str = ""
    evidence_source: str = "UNVERIFIED_SOURCE_RECEIPT"


def _status(res):
    if "state" in res and "status" in res and res["state"] != res["status"]:
        return "INVALID"
    return res.get("state", res.get("status"))


def _result(harness, res, source="UNVERIFIED_SOURCE_RECEIPT"):
    if not isinstance(res, dict):
        return [LaneResult(harness, "unknown", "INVALID", detail="result is not an object")]
    label = str(res.get("engine") or res.get("lane") or res.get("name") or "run")
    status = _status(res)
    # Explicit failure always wins over stale metrics or a positive run count.
    if status and status != "MEASURED":
        return [LaneResult(harness, label, str(status),
                           detail=str(res.get("reason", res.get("detail", status))),
                           evidence_source=source)]
    metrics = res.get("metrics", res.get("aggregate"))
    if isinstance(metrics, dict) and metrics and (status == "MEASURED" or res.get("runs")):
        values = {k: v for k, v in metrics.items()
                  if type(v) in (int, float) and math.isfinite(v)}
        if values:
            return [LaneResult(harness, label, "MEASURED", values, evidence_source=source)]
    return [LaneResult(harness, label, "UNAVAILABLE",
                       detail="receipt contains no supported measured metrics", evidence_source=source)]

def _flatten(harness: str, receipt: Dict[str, Any]) -> List[LaneResult]:
    out: List[LaneResult] = []
    field_name = schema(receipt)
    if field_name == "hash":
        body = receipt["payload"]
        status = body.get("state", body.get("status", "MEASURED"
                          if receipt["kind"] == "calibration.score.v1" else "UNAVAILABLE"))
        return _result(harness, {**body, "state": status,
                                "lane": receipt["kind"]}, "CALLER_DECLARED_CALIBRATION_INPUT")
    body = receipt["run"] if field_name == "self_hash" else receipt
    source = body.get("provenance", {}).get("source_kind", "UNVERIFIED_SOURCE_RECEIPT")
    if _status(body) and _status(body) != "MEASURED":
        return _result(harness, body, source)
    if "result" in body and isinstance(body["result"], dict):
        body = body["result"]
    if _status(body) and _status(body) != "MEASURED":
        return _result(harness, body, source)
    if body.get("type") == "engine_bench":
        verdict = body.get("verdict", {})
        if _status(verdict) == "MEASURED" and isinstance(verdict.get("engines"), dict):
            return [lane for name, metrics in verdict["engines"].items()
                    for lane in _result(harness, {"engine": name, "state": "MEASURED",
                                                 "metrics": metrics}, source)]
        return _result(harness, {**verdict, "lane": "engine-comparison"}, source)
    # Do not promote a blocked parent because it retains a stale curve/table.
    state = _status(body)
    if state and state != "MEASURED":
        return _result(harness, body, source)
    if isinstance(body.get("curve"), list):
        for row in body["curve"]:
            if not isinstance(row, dict):
                out.extend(_result(harness, row, source))
                continue
            out.extend(_result(harness, {**row, "lane": f"quant-{row.get('bits')}-bit",
                                        "state": _status(row) or state, "metrics": row}, source))
        return out or _result(harness, body, source)
    if isinstance(body.get("leaderboard"), list):
        return [lane for row in body["leaderboard"]
                for lane in _result(harness, {**row, "state": _status(row) or state,
                                             "metrics": row} if isinstance(row, dict) else row, source)]
    if isinstance(body.get("results"), list):
        return [lane for row in body["results"] for lane in _result(harness, row, source)]
    return _result(harness, body, source)

def aggregate(chains: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """chains: harness name -> ordered receipt list. Fail closed on any bad chain."""
    harness_reports: Dict[str, Any] = {}
    terminal_hashes: Dict[str, str] = {}
    lanes: List[LaneResult] = []
    if not isinstance(chains, dict) or not chains:
        return {"report_status": "INVALID", "reason": "no source chains supplied"}
    if any(not isinstance(name, str) or not name.strip() for name in chains):
        return {"report_status": "INVALID", "reason": "harness names must be nonempty strings"}
    for name in sorted(chains):
        chain = chains[name]
        ok, detail = verify_chain(chain)
        if not ok:
            return {"report_status": "INVALID", "reason": f"{name}: {detail}"}
        terminal = chain[-1][schema(chain[-1])]
        if terminal in terminal_hashes.values():
            return {"report_status": "INVALID", "reason": f"{name}: duplicate source chain under another harness"}
        try:
            identities = {declared_harness(receipt) for receipt in chain} - {None}
            if identities and identities != {name}:
                raise ValueError("hashed harness declaration does not match supplied name")
        except (TypeError, ValueError) as exc:
            return {"report_status": "INVALID", "reason": f"{name}: {exc}"}
        harness_reports[name] = {"chain": detail, "receipts": len(chain),
                                 "identity": "HASHED_SOURCE_DECLARATION" if identities else "UNVERIFIED"}
        terminal_hashes[name] = terminal
        for r in chain:
            try:
                lanes.extend(_flatten(name, r))
            except (TypeError, AttributeError, KeyError, ValueError) as exc:
                lanes.append(LaneResult(name, "unknown", "INVALID", detail=str(exc)))
    master_input = canonical({k: terminal_hashes[k] for k in sorted(terminal_hashes)})
    master_hash = hashlib.sha256(master_input.encode("utf-8")).hexdigest()
    missing = sorted(set(REQUIRED_HARNESSES) - set(chains))
    unverified = sorted(name for name in REQUIRED_HARNESSES if name in harness_reports
                        and harness_reports[name]["identity"] == "UNVERIFIED")
    return {"report_status": "VALID", "harnesses": harness_reports,
            "coverage_status": "INCOMPLETE" if missing else (
                "IDENTITY_UNVERIFIED" if unverified else "ALL_FOUR_DECLARED_HARNESSES_PRESENT"),
            "unverified_harness_identities": unverified,
            "identity_authority": "HASHED_SOURCE_DECLARATION_NOT_ISSUER_AUTHENTICATION",
            "missing_harnesses": missing, "wave1_acceptance": "NOT_EVALUATED",
            "authority": "INTEGRITY_ONLY_NOT_INDEPENDENT_EVIDENCE",
            "terminal_chain_hashes": terminal_hashes, "master_receipt_hash": master_hash,
            "measured_lanes": [l for l in lanes if l.status == "MEASURED"],
            "blocked_lanes": [l for l in lanes if l.status == "BLOCKED"],
            "invalid_lanes": [l for l in lanes if l.status in ("INVALID", "FAILED")],
            "unavailable_lanes": [l for l in lanes if l.status not in ("MEASURED", "BLOCKED", "INVALID", "FAILED")]}

def _md(value):
    return escape(str(value), quote=False).replace("|", "&#124;").replace("`", "&#96;").replace("\r", " ").replace("\n", " ")


def render_markdown(report: Dict[str, Any]) -> str:
    if report.get("report_status") != "VALID":
        return f"# SZL Wave 1 report\n\n**INVALID** — {_md(report.get('reason', 'unknown'))}\n"
    lines = ["# SZL Wave 1 report", "",
             f"Master receipt hash: `{report['master_receipt_hash']}`", "",
             f"Coverage: {report['coverage_status']}. Wave 1 acceptance: NOT_EVALUATED.",
             "Hash validity proves integrity, not real-model execution or evidence-gate completion.", "",
             "Missing harnesses: " + (", ".join(report["missing_harnesses"]) or "none"), "",
             "## Chains", ""]
    for name, info in sorted(report["harnesses"].items()):
        lines.append(f"- `{_md(name)}` — {_md(info['chain'])}; identity {_md(info['identity'])}")
    lines += ["", "## Measured (source-reported)", "", "| Harness | Lane | Metrics | Evidence source |", "|---|---|---|---|"]
    for l in report["measured_lanes"]:
        m = ", ".join(f"{_md(k)}={_md(v)}" for k, v in sorted(l.metrics.items()))
        lines.append(f"| {_md(l.harness)} | {_md(l.lane)} | {m} | {_md(l.evidence_source)} |")
    if report["blocked_lanes"]:
        lines += ["", "## Blocked", ""]
        for l in report["blocked_lanes"]:
            lines.append(f"- `{_md(l.harness)}/{_md(l.lane)}` — {_md(l.detail)}")
    for title, key in (("Invalid or failed lanes", "invalid_lanes"), ("Unavailable lanes", "unavailable_lanes")):
        if report[key]:
            lines += ["", f"## {title}", ""]
            for lane in report[key]:
                lines.append(f"- `{_md(lane.harness)}/{_md(lane.lane)}` — {_md(lane.status)}: {_md(lane.detail)}")
    lines += ["", "Cross-harness numbers are never ranked against each other.",
              "Hardware and fairness keys are carried by each source receipt.", ""]
    return "\n".join(lines)
