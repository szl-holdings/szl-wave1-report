"""Read native receipt artifacts and emit a recomputable local report."""
import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .report import aggregate, canonical, render_markdown


def publish_outputs(outputs):
    """Stage every output, then publish without replacing any existing path."""
    staged = []
    published = []
    try:
        for destination, content in outputs:
            descriptor, temporary = tempfile.mkstemp(prefix=".szl-wave-", dir=destination.parent)
            staged.append((Path(temporary), destination))
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, destination in staged:
            os.link(temporary, destination)  # fails if another process won the destination
            published.append((temporary, destination))
    except BaseException:
        for temporary, destination in reversed(published):
            if destination.exists() and os.path.samefile(temporary, destination):
                destination.unlink()  # remove only this invocation's partial publication
        raise
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def load_receipts(path):
    path = Path(path)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("source artifact exceeds 128 MiB")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    body = json.loads(text)
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("chain"), list):
        return body["chain"]
    raise ValueError("input must be a receipt list, JSONL chain, or object with chain list")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chain", action="append", required=True, metavar="HARNESS=PATH")
    parser.add_argument("--output", type=Path, required=True, help="new report JSON; never overwritten")
    parser.add_argument("--markdown", type=Path, help="optional new Markdown report")
    args = parser.parse_args(argv)
    try:
        if args.markdown and args.markdown.resolve() == args.output.resolve():
            raise ValueError("JSON and Markdown destinations must differ")
        for destination in (args.output, args.markdown):
            if destination is not None and destination.exists():
                raise ValueError(f"refusing to overwrite {destination}")
        chains = {}
        for item in args.chain:
            name, separator, path = item.partition("=")
            if not separator or not name.strip() or not path or name in chains:
                raise ValueError("each --chain must have a unique nonempty HARNESS=PATH")
            chains[name] = load_receipts(path)
        report = aggregate(chains)
        wire = json.loads(json.dumps(report, default=lambda value: asdict(value)
                                     if is_dataclass(value) else str(value), allow_nan=False))
        wire["report_sha256"] = hashlib.sha256(canonical(wire).encode()).hexdigest()
        outputs = [(args.output, json.dumps(wire, indent=2, ensure_ascii=False, allow_nan=False) + "\n")]
        if args.markdown:
            outputs.append((args.markdown, render_markdown(report)))
        publish_outputs(outputs)
        print(json.dumps({"report_status": report["report_status"],
                          "report_sha256": wire["report_sha256"],
                          "output": str(args.output)}))
        return 0 if report["report_status"] == "VALID" else 2
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
