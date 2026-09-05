# szl-wave1-report

The Wave 1 capstone. Four SZL harnesses emit hash-chained receipts:

- [`szl-retrieval-bench`](https://github.com/szl-holdings/szl-retrieval-bench) — retrieval quality (nDCG / Recall / MRR / MAP)
- [`szl-engine-bench`](https://github.com/szl-holdings/szl-engine-bench) — serving throughput and latency (TTFT, tok/s)
- [`szl-quant-bench`](https://github.com/szl-holdings/szl-quant-bench) — quantization quality curves (cosine / KL / top-1)
- [`szl-calibration`](https://github.com/szl-holdings/szl-calibration) — receipted calibration gates

This package verifies every chain, then consolidates the results into one
report with a single **master receipt hash** — the chain-of-chains. If any
source chain is broken, the whole report is `INVALID`. No partial credit.

Version 0.2 reads the actual stdlib `self_hash`/`run` records, calibration's
`hash`/`payload` records (with its distinct genesis), and legacy flat
`chain_hash` records. It verifies each format with its native canonicalization;
it never rewrites or re-signs source records to make them pass.

## Doctrine

- **Fail closed.** One tampered or broken chain voids the report; the reason names the harness.
- **Only `MEASURED` enters the table.** `BLOCKED` lanes are listed with their reasons, never interpolated.
- **No cross-harness ranking.** A tok/s number and an nDCG number never appear in the same ordered list.
- **Order-independent.** The master hash is computed over sorted harness names — same chains, same hash, any machine.

## Usage

```bash
pip install -e . pytest
python -m pytest tests/ -q
python -m szl_wave1_report --chain szl-retrieval-bench=retrieval.json --chain szl-calibration=calibration.jsonl --output wave.json --markdown wave.md
```

The CLI accepts JSON receipt lists, JSON objects containing a `chain` list, or
JSONL chains. Destination files must be new. Invalid chains produce an INVALID
report and a nonzero exit; malformed files fail before a report is produced.
The JSON includes the full terminal hashes, the full master hash over their
sorted mapping, and `report_sha256` over canonical JSON without that field.
Both requested outputs are staged first and published without replacement;
publication failure rolls back only this invocation's files, preserving any
concurrent writer. This requires ordinary local hard-link support.

`report_status=VALID` means **chain integrity only**. The separate coverage field
lists missing harnesses, and `wave1_acceptance=NOT_EVALUATED` deliberately does
not close external evidence gates. Even all four intact chains can contain
synthetic measurements. Input provenance labels appear in the table; unknown
origins remain `UNVERIFIED_SOURCE_RECEIPT`. Calibration scores are caller-input
measurements, not proof of a particular model execution. These unsigned chains
need an independently retained terminal anchor to detect a full rehash or a
valid-prefix truncation. No model authenticity or independent witness is implied.
Duplicate chains and mismatched hashed harness declarations are rejected.
Missing hashed identity metadata yields `IDENTITY_UNVERIFIED` rather than full
four-harness coverage; source declarations are not authenticated identities.

```python
import json, pathlib
from szl_wave1_report import aggregate, render_markdown

chains = {}
for harness_dir in pathlib.Path("receipts/").iterdir():
    if harness_dir.is_dir():
        chains[harness_dir.name] = [
            json.loads(p.read_text()) for p in sorted(harness_dir.glob("*.json"))
        ]

report = aggregate(chains)
print(render_markdown(report))
pathlib.Path("wave1-report.md").write_text(render_markdown(report))
```

## Verified sample output (test fixture, not real-estate evidence)

Produced by the test suite in this repo on 2026-09-04 (fixture chains):

```markdown
# SZL Wave 1 report

Master receipt hash: `bcd77e3ed20be9c3...`

## Chains

- `szl-engine-bench` — chain valid (2 receipts)
- `szl-retrieval-bench` — chain valid (2 receipts)

## Measured

| Harness | Lane | Metrics |
|---|---|---|
| szl-engine-bench | laneA | tok_per_s=51.0, ttft_p50_ms=1.5 |
| szl-retrieval-bench | bm25 | tok_per_s=51.0, ttft_p50_ms=1.5 |

## Blocked

- `szl-engine-bench/vllm` — endpoint not set
- `szl-retrieval-bench/vllm` — endpoint not set
```

The fixture numbers above are pipeline checks. The real Wave 1 table appears
only when the four harnesses have emitted receipts from real runs on real
hardware — tracked by each harness's verification-gate issue.

## Scope

- Python 3.11+, standard library only.
- No network access, no mutation of source receipts.
- This tool verifies and formats; it never re-measures or closes provider/evidence gates.
- Explicit invalid, failed, blocked, or unavailable states cannot be promoted by stale metrics.

## License

Apache-2.0 — see `LICENSE`.
