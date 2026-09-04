"""Tests for szl_wave1_report — every assertion here was executed green before this file was pushed."""
import copy
import hashlib

from szl_wave1_report.report import GENESIS, aggregate, canonical, render_markdown, verify_chain


def make_receipt(prev, harness, results, seq=0):
    r = {"harness": harness, "version": "test", "seq": seq, "results": results}
    payload = {k: v for k, v in r.items() if k not in ("prev_hash", "chain_hash")}
    r["prev_hash"] = prev
    r["chain_hash"] = hashlib.sha256((prev + canonical(payload)).encode()).hexdigest()
    return r


def build_chain(harness, runs_label="laneA", n=2):
    chain, prev = [], GENESIS
    for i in range(n):
        results = [{"engine": runs_label, "runs": [1, 2, 3],
                    "metrics": {"tok_per_s": 50.0 + i, "ttft_p50_ms": 1.5}}] if i == n - 1 else \
                   [{"status": "BLOCKED", "engine": "vllm", "detail": "endpoint not set"}]
        r = make_receipt(prev, harness, results, seq=i)
        prev = r["chain_hash"]
        chain.append(r)
    return chain


def test_valid_chain_verifies():
    ok, detail = verify_chain(build_chain("szl-engine-bench"))
    assert ok, detail


def test_tampered_payload_detected():
    chain = build_chain("szl-engine-bench")
    chain[0]["results"] = [{"status": "MEASURED", "fake": True}]
    assert not verify_chain(chain)[0]


def test_broken_link_detected():
    chain = build_chain("szl-engine-bench")
    chain[1]["prev_hash"] = "f" * 64
    assert not verify_chain(chain)[0]


def test_empty_chain_fails_closed():
    assert not verify_chain([])[0]


def test_aggregate_two_harnesses_valid():
    rep = aggregate({"szl-engine-bench": build_chain("szl-engine-bench"),
                     "szl-retrieval-bench": build_chain("szl-retrieval-bench", runs_label="bm25")})
    assert rep["report_status"] == "VALID"
    assert len(rep["measured_lanes"]) == 2
    assert len(rep["blocked_lanes"]) == 2


def test_master_hash_order_independent():
    a, b = build_chain("szl-engine-bench"), build_chain("szl-retrieval-bench", runs_label="bm25")
    r1 = aggregate({"szl-engine-bench": a, "szl-retrieval-bench": b})
    r2 = aggregate({"szl-retrieval-bench": b, "szl-engine-bench": a})
    assert r1["master_receipt_hash"] == r2["master_receipt_hash"]


def test_one_bad_chain_voids_report():
    good = build_chain("szl-engine-bench")
    bad = build_chain("szl-quant-bench")
    bad[0]["results"] = [{"status": "MEASURED", "fake": True}]
    rep = aggregate({"szl-engine-bench": good, "szl-quant-bench": bad})
    assert rep["report_status"] == "INVALID"
    assert "szl-quant-bench" in rep["reason"]


def test_markdown_renders_measured_blocked_and_master():
    rep = aggregate({"szl-engine-bench": build_chain("szl-engine-bench"),
                     "szl-retrieval-bench": build_chain("szl-retrieval-bench", runs_label="bm25")})
    md = render_markdown(rep)
    assert "| szl-engine-bench | laneA |" in md
    assert "| szl-retrieval-bench | bm25 |" in md
    assert "`szl-engine-bench/vllm`" in md
    assert "endpoint not set" in md
    assert "Master receipt hash" in md


def test_invalid_report_renders_honestly():
    bad = build_chain("szl-quant-bench")
    bad[0]["results"] = [{"tampered": True}]
    rep = aggregate({"szl-quant-bench": bad})
    assert "**INVALID**" in render_markdown(rep)


def test_receipt_without_runs_is_not_measured():
    r = make_receipt(GENESIS, "szl-quant-bench", [{"note": "no runs key"}])
    rep = aggregate({"szl-quant-bench": [r]})
    assert rep["report_status"] == "VALID"
    assert len(rep["measured_lanes"]) == 0
