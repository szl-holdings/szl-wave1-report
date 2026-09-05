import copy
import hashlib
import json

import pytest

from szl_wave1_report.__main__ import main
from szl_wave1_report.native import CALIBRATION_GENESIS, encoded
from szl_wave1_report.report import aggregate, canonical, render_markdown, verify_chain


def native(run, prev="0" * 64):
    body = {"prev_hash": prev, "ts": "2026-09-05T00:00:00Z",
            "signature": "UNSIGNED_HONEST", "run": run}
    body["self_hash"] = hashlib.sha256(encoded(body, ensure_ascii=False).encode()).hexdigest()
    return body


def calibration():
    body = {"index": 0, "timestamp_utc": "2026-09-05T00:00:00Z",
            "kind": "calibration.score.v1", "payload": {"model_id": "example",
            "metrics": {"ece": .1, "auroc": None}}, "prev_hash": CALIBRATION_GENESIS,
            "signature": "UNSIGNED_HONEST"}
    body["hash"] = hashlib.sha256(encoded(body).encode()).hexdigest()
    return body


def test_native_unicode_chain_preserved_and_tamper_rejected():
    run = {"state": "MEASURED", "lane": "bm25", "aggregate": {"mrr": .6}, "note": "λ"}
    first = native({"type": "real_retrieval", "result": run})
    second = native({"state": "BLOCKED", "reason": "no engine"}, first["self_hash"])
    chain = [first, second]
    original = copy.deepcopy(chain)
    assert verify_chain(chain)[0]
    report = aggregate({"szl-retrieval-bench": chain})
    assert report["measured_lanes"][0].metrics == {"mrr": .6}
    assert report["terminal_chain_hashes"]["szl-retrieval-bench"] == second["self_hash"]
    assert chain == original
    assert report["coverage_status"] == "INCOMPLETE"
    assert report["wave1_acceptance"] == "NOT_EVALUATED"
    second["run"]["reason"] = "modified"
    assert not verify_chain(chain)[0]


def test_calibration_native_genesis_and_metrics():
    record = calibration()
    assert verify_chain([record])[0]
    report = aggregate({"szl-calibration": [record]})
    assert report["measured_lanes"][0].metrics == {"ece": .1}
    record["prev_hash"] = "0" * 64
    record["hash"] = hashlib.sha256(encoded({k: v for k, v in record.items() if k != "hash"}).encode()).hexdigest()
    assert not verify_chain([record])[0]


@pytest.mark.parametrize("bad", [None, [], {}, [None], [{}], ["bad"]])
def test_malformed_chains_return_false(bad):
    assert not verify_chain(bad)[0]


def test_empty_report_is_invalid():
    assert aggregate({})["report_status"] == "INVALID"


def test_mixed_or_ambiguous_schema_rejected():
    a = native({"state": "BLOCKED"})
    assert not verify_chain([a, calibration()])[0]
    a["chain_hash"] = a["self_hash"]
    assert not verify_chain([a])[0]


def test_invalid_status_cannot_be_promoted_by_metrics():
    record = native({"state": "INVALID", "lane": "bad", "runs": 3,
                     "metrics": {"speed": 99}, "reason": "wrong context"})
    report = aggregate({"engine": [record]})
    assert not report["measured_lanes"]
    assert len(report["invalid_lanes"]) == 1
    assert "wrong context" in render_markdown(report)


def test_quant_fixture_label_and_full_master_are_visible():
    record = native({"state": "MEASURED", "provenance": {"source_kind": "SYNTHETIC"},
                     "curve": [{"bits": 4, "cosine": .98}]})
    report = aggregate({"szl-quant-bench": [record]})
    markdown = render_markdown(report)
    assert "SYNTHETIC" in markdown
    assert report["master_receipt_hash"] in markdown
    assert "NOT_EVALUATED" in markdown


def test_engine_verdict_metrics_are_read_from_hashed_payload():
    record = native({"type": "engine_bench", "verdict": {"state": "MEASURED",
                     "engines": {"vllm": {"itl_p95_ms": 10.0}, "sglang": {"itl_p95_ms": 12.0}}}})
    report = aggregate({"szl-engine-bench": [record]})
    assert len(report["measured_lanes"]) == 2


def test_cli_jsonl_and_output_receipt_no_overwrite(tmp_path):
    source = tmp_path / "calibration.jsonl"
    source.write_text(json.dumps(calibration()) + "\n", encoding="utf-8")
    output = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    args = ["--chain", f"szl-calibration={source}", "--output", str(output), "--markdown", str(markdown)]
    assert main(args) == 0
    body = json.loads(output.read_text(encoding="utf-8"))
    digest = body.pop("report_sha256")
    assert digest == hashlib.sha256(canonical(body).encode()).hexdigest()
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        main(args)
    assert output.read_bytes() == before


def test_unknown_calibration_kind_does_not_become_measured():
    record = calibration()
    record["kind"] = "unknown"
    record["hash"] = hashlib.sha256(encoded({k: v for k, v in record.items() if k != "hash"}).encode()).hexdigest()
    report = aggregate({"szl-calibration": [record]})
    assert not report["measured_lanes"]
    assert report["unavailable_lanes"]


def test_markdown_escapes_source_formatting():
    record = native({"state": "BLOCKED", "lane": "x|y", "reason": "<b>line</b>\nnext"})
    markdown = render_markdown(aggregate({"h": [record]}))
    assert "x&#124;y" in markdown
    assert "&lt;b&gt;line&lt;/b&gt; next" in markdown


@pytest.mark.parametrize("run", [
    {"state": "INVALID", "result": {"state": "MEASURED", "metrics": {"mrr": .9}}},
    {"state": "INVALID", "type": "engine_bench", "verdict": {
        "state": "MEASURED", "engines": {"vllm": {"itl_p95_ms": 10.0}}}},
    {"state": "MEASURED", "curve": [{"state": "INVALID", "bits": 4, "cosine": .99}]},
    {"state": "MEASURED", "leaderboard": [{"status": "INVALID", "lane": "bm25", "mrr": .9}]},
    {"state": "MEASURED", "status": "INVALID", "curve": [{"bits": 4, "cosine": .99}]},
])
def test_invalid_parent_or_row_never_promoted(run):
    report = aggregate({"harness": [native(run)]})
    assert not report["measured_lanes"]
    assert report["invalid_lanes"]
