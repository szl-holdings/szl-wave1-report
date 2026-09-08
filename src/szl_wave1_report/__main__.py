"""Read native receipt artifacts and emit a recomputable local report."""
import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile

from .report import aggregate, canonical, render_markdown


def publish_outputs(outputs):
    """Publish exactly one complete artifact; never unlink an exposed path.

    Independent output paths have no portable all-or-none transaction. A ZIP
    bundle supplies both representations through one atomic no-replace link.
    """
    if len(outputs) != 1:
        raise ValueError("multiple destinations are not atomic; use --format bundle")
    destination, content = outputs[0]
    destination = Path(destination)
    if isinstance(content, str):
        content = content.encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".szl-wave-", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)  # fails if another process won the destination
    finally:
        # Cleanup only our private staging name, never the published name.
        temporary.unlink(missing_ok=True)


def report_bytes(wire, report, format_name):
    json_bytes = (json.dumps(wire, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if format_name == "json":
        return json_bytes
    markdown_bytes = render_markdown(report).encode("utf-8")
    if format_name == "markdown":
        return markdown_bytes
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in (("wave-report.json", json_bytes), ("wave-report.md", markdown_bytes)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()


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
    parser.add_argument("--output", type=Path, required=True, help="new report artifact; never overwritten")
    parser.add_argument("--format", choices=("json", "markdown", "bundle"), default="json")
    parser.add_argument("--markdown", type=Path, help="removed: use --format bundle for JSON plus Markdown")
    args = parser.parse_args(argv)
    try:
        if args.markdown is not None:
            raise ValueError("separate --markdown output is not atomic; use --format bundle --output report.zip")
        if args.output.exists():
            raise ValueError(f"refusing to overwrite {args.output}")
        if not args.output.parent.is_dir():
            raise ValueError("output parent directory must already exist")
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
        publish_outputs([(args.output, report_bytes(wire, report, args.format))])
        print(json.dumps({"report_status": report["report_status"],
                          "report_sha256": wire["report_sha256"],
                          "output": str(args.output)}))
        return 0 if report["report_status"] == "VALID" else 2
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
