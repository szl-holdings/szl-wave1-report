import copy
import hashlib
import json
import zipfile

import pytest

from szl_wave1_report.__main__ import main, publish_outputs
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
    args = ["--chain", f"szl-calibration={source}", "--output", str(output)]
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
    name = "szl-engine-bench" if run.get("type") == "engine_bench" else "harness"
    report = aggregate({name: [native(run)]})
    assert not report["measured_lanes"]
    assert report["invalid_lanes"]


def test_same_chain_cannot_count_as_four_harnesses():
    record = native({"state": "MEASURED", "metrics": {"mrr": .6}})
    names = ("szl-calibration", "szl-retrieval-bench", "szl-engine-bench", "szl-quant-bench")
    assert aggregate({name: [record] for name in names})["report_status"] == "INVALID"


def test_declared_identity_cannot_be_relabelled():
    record = native({"type": "engine_bench", "verdict": {"state": "BLOCKED"}})
    report = aggregate({"szl-quant-bench": [record]})
    assert report["report_status"] == "INVALID"
    assert "declaration" in report["reason"]


def test_unknown_identity_does_not_become_verified_coverage():
    names = ("szl-calibration", "szl-retrieval-bench", "szl-engine-bench", "szl-quant-bench")
    report = aggregate({name: [native({"state": "BLOCKED", "note": name})] for name in names})
    assert report["coverage_status"] == "IDENTITY_UNVERIFIED"
    assert len(report["unverified_harness_identities"]) == 4


def test_multiple_destinations_fail_before_publication(tmp_path):
    output = tmp_path / "report.json"
    with pytest.raises(ValueError, match="multiple destinations"):
        publish_outputs([(output, "{}"), (tmp_path / "missing" / "report.md", "report")])
    assert not output.exists()
    assert not list(tmp_path.glob(".szl-wave-*"))


def test_concurrent_destination_creation_is_preserved(tmp_path, monkeypatch):
    import os
    original_link = os.link
    output = tmp_path / "report.json"

    def concurrent_file(source, destination):
        destination.write_text("another writer", encoding="utf-8")
        original_link(source, destination)

    monkeypatch.setattr(os, "link", concurrent_file)
    with pytest.raises(FileExistsError):
        publish_outputs([(output, "{}")])
    assert output.read_text(encoding="utf-8") == "another writer"
    assert not list(tmp_path.glob(".szl-wave-*"))


def test_replacement_after_publication_is_never_deleted(tmp_path, monkeypatch):
    import os
    original_link = os.link
    output = tmp_path / "report.json"

    def replace_published(source, destination):
        original_link(source, destination)
        destination.unlink()
        destination.write_text("concurrent replacement", encoding="utf-8")

    monkeypatch.setattr(os, "link", replace_published)
    publish_outputs([(output, "{}")])
    assert output.read_text(encoding="utf-8") == "concurrent replacement"
    assert not list(tmp_path.glob(".szl-wave-*"))


def test_removed_dual_output_cannot_enter_exposed_path_rollback(tmp_path, monkeypatch):
    import os
    first, second = tmp_path / "first.json", tmp_path / "second.md"
    first.write_text("already owned by another writer", encoding="utf-8")

    def must_not_publish(*args):
        pytest.fail("multi-path invocation reached publication")

    monkeypatch.setattr(os, "link", must_not_publish)
    with pytest.raises(ValueError):
        publish_outputs([(first, "{}"), (second, "markdown")])
    assert first.read_text(encoding="utf-8") == "already owned by another writer"
    assert not second.exists()


def test_missing_and_unwritable_parent_fail_before_exposure(tmp_path, monkeypatch):
    from szl_wave1_report import __main__ as cli
    with pytest.raises(OSError):
        publish_outputs([(tmp_path / "missing" / "report.json", "{}")])
    def permission_denied(**kwargs):
        raise PermissionError("parent not writable")
    monkeypatch.setattr(cli.tempfile, "mkstemp", permission_denied)
    with pytest.raises(PermissionError):
        publish_outputs([(tmp_path / "report.json", "{}")])
    assert not (tmp_path / "report.json").exists()


def test_cli_deterministic_bundle_contains_both_representations(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(calibration()) + "\n", encoding="utf-8")
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    for path in (first, second):
        assert main(["--chain", f"szl-calibration={source}", "--format", "bundle",
                     "--output", str(path)]) == 0
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["wave-report.json", "wave-report.md"]
        wire = json.loads(archive.read("wave-report.json"))
        digest = wire.pop("report_sha256")
        assert digest == hashlib.sha256(canonical(wire).encode()).hexdigest()
        assert wire["master_receipt_hash"].encode() in archive.read("wave-report.md")


def test_cli_markdown_only_and_removed_dual_output(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(calibration()) + "\n", encoding="utf-8")
    markdown = tmp_path / "report.md"
    assert main(["--chain", f"szl-calibration={source}", "--format", "markdown",
                 "--output", str(markdown)]) == 0
    assert markdown.read_text(encoding="utf-8").startswith("# SZL Wave 1 report")
    output = tmp_path / "never-published.json"
    with pytest.raises(SystemExit):
        main(["--chain", f"szl-calibration={source}", "--output", str(output),
              "--markdown", str(tmp_path / "never-published.md")])
    assert not output.exists()
