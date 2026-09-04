# szl-wave1-report

The Wave 1 capstone. Four SZL harnesses emit hash-chained receipts:

- [`szl-retrieval-bench`](https://github.com/szl-holdings/szl-retrieval-bench) — retrieval quality (nDCG / Recall / MRR / MAP)
- [`szl-engine-bench`](https://github.com/szl-holdings/szl-engine-bench) — serving throughput and latency (TTFT, tok/s)
- [`szl-quant-bench`](https://github.com/szl-holdings/szl-quant-bench) — quantization quality curves (cosine / KL / top-1)
- [`szl-calibration`](https://github.com/szl-holdings/szl-calibration) — receipted calibration gates

This package verifies every chain, then consolidates the results into one
report with a single **master receipt hash** — the chain-of-chains. If any
source chain is broken, the whole report is `INVALID`. No partial credit.

## Doctrine

- **Fail closed.** One tampered or broken chain voids the report; the reason names the harness.
- **Only `MEASURED` enters the table.** `BLOCKED` lanes are listed with their reasons, never interpolated.
- **No cross-harness ranking.** A tok/s number and an nDCG number never appear in the same ordered list.
- **Order-independent.** The master hash is computed over sorted harness names — same chains, same hash, any machine.

## Usage

```bash
pip install -e . pytest
python -m pytest tests/ -q
```

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
- This tool verifies and formats; it never re-measures.

## License

Apache-2.0 — see `LICENSE`.
