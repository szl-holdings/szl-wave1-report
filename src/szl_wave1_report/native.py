"""Verify supported native formats without rewriting the source receipts."""
import hashlib
import json

ZERO = "0" * 64
CALIBRATION_GENESIS = hashlib.sha256(b"SZL-CALIBRATION-GENESIS-V1").hexdigest()
HASH_FIELDS = ("chain_hash", "self_hash", "hash")


def declared_harness(receipt):
    """Read only hashed identity metadata; never infer identity from a filename."""
    field = schema(receipt)
    if field == "hash":
        return "szl-calibration" if receipt.get("kind") == "calibration.score.v1" else None
    body = receipt["run"] if field == "self_hash" else receipt
    declarations = set()
    if isinstance(body.get("harness"), str):
        declarations.add(body["harness"])
    types = {"quant_curve": "szl-quant-bench", "engine_bench": "szl-engine-bench",
             "bm25_real_cross_implementation": "szl-retrieval-bench"}
    if body.get("type") in types:
        declarations.add(types[body["type"]])
    context = body.get("context")
    if isinstance(context, dict) and isinstance(context.get("source"), dict):
        repository = context["source"].get("repository")
        if isinstance(repository, str) and repository.startswith("szl-holdings/"):
            declarations.add(repository.split("/", 1)[1])
    if len(declarations) > 1:
        raise ValueError("conflicting hashed harness declarations")
    return next(iter(declarations), None)


def schema(receipt):
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    fields = [name for name in HASH_FIELDS if name in receipt]
    if len(fields) != 1:
        raise ValueError("receipt must identify exactly one supported hash schema")
    return fields[0]


def encoded(value, *, ensure_ascii=True):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=ensure_ascii, allow_nan=False)


def verify(receipts):
    if not isinstance(receipts, list) or not receipts:
        return False, "empty or malformed chain"
    try:
        field = schema(receipts[0])
        prev = CALIBRATION_GENESIS if field == "hash" else ZERO
        for index, receipt in enumerate(receipts):
            if schema(receipt) != field:
                raise ValueError(f"mixed receipt schemas at index {index}")
            if receipt.get("prev_hash") != prev:
                raise ValueError(f"link broken at receipt {index}: prev_hash mismatch")
            body = {k: v for k, v in receipt.items() if k != field}
            if field == "chain_hash":
                body.pop("prev_hash")
                text = prev + encoded(body)
            elif field == "self_hash":
                if not isinstance(body.get("run"), dict):
                    raise ValueError("native run must be an object")
                text = encoded(body, ensure_ascii=False)
            else:
                required = {"index", "timestamp_utc", "kind", "payload", "prev_hash", "signature"}
                if set(body) != required or type(body["index"]) is not int or body["index"] != index:
                    raise ValueError("calibration receipt fields or index invalid")
                if not isinstance(body["payload"], dict):
                    raise ValueError("calibration payload must be an object")
                text = encoded(body)
            expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if receipt[field] != expected:
                raise ValueError(f"payload tampered at receipt {index}: {field} mismatch")
            prev = expected
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        return False, str(exc)
    return True, f"chain valid ({len(receipts)} receipts; {field})"
