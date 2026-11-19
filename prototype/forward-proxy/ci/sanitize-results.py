#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


STATUSES = ("passed", "failed", "error", "skipped")
OUTCOMES = ("success", "failure", "cancelled", "skipped")
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,79}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
MAX_BYTES = 256 * 1024
MAX_TESTS = 256


def read_report(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("not_regular")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("oversized")
    return validate_report(json.loads(raw))


def validate_report(report):
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int:
        raise ValueError("schema")
    if report["schema_version"] != 1 or report.get("status") not in STATUSES[:3]:
        raise ValueError("schema")
    tests = report.get("tests")
    if not isinstance(tests, list) or not 1 <= len(tests) <= MAX_TESTS:
        raise ValueError("tests")
    counts = dict.fromkeys(STATUSES, 0)
    sanitized_tests = []
    seen = set()
    for test in tests:
        if not isinstance(test, dict):
            raise ValueError("test")
        name, status = test.get("name"), test.get("status")
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or name in seen:
            raise ValueError("name")
        if status not in STATUSES:
            raise ValueError("status")
        seen.add(name)
        counts[status] += 1
        sanitized_tests.append({"name": name, "status": status})
    supplied_counts = report.get("counts")
    if not isinstance(supplied_counts, dict) or any(
        type(supplied_counts.get(status)) is not int or supplied_counts[status] != counts[status]
        for status in STATUSES
    ):
        raise ValueError("counts")
    if report["status"] == "passed" and (counts["failed"] or counts["error"] or counts["skipped"] or not counts["passed"]):
        raise ValueError("false_pass")
    return {"schema_version": 1, "status": report["status"], "tests": sanitized_tests, "counts": counts}


def commit(value):
    return value if isinstance(value, str) and COMMIT_PATTERN.fullmatch(value) else None


def checkout_commit():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        )
        return commit(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def write_artifacts(raw_dir, output_dir, metadata):
    output_dir.mkdir(parents=True, exist_ok=False)
    report = None
    try:
        report = read_report(raw_dir / "results.json")
    except (OSError, ValueError, TypeError, RecursionError):
        pass
    passed = bool(
        report is not None
        and report["status"] == "passed"
        and metadata["e2e_outcome"] == "success"
        and metadata["cleanup_outcome"] == "success"
        and metadata["candidate_sha"] is not None
        and metadata["candidate_sha"] == metadata["checkout_sha"]
        and metadata["test_infra_ref"] is not None
    )
    provenance = {
        "schema_version": 1,
        **metadata,
        "report_valid": report is not None,
        "status": "passed" if passed else "error",
    }
    (output_dir / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    if report is not None:
        (output_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return passed


def main():
    root = Path(__file__).resolve().parents[3]
    pin_file = root / "prototype/forward-proxy/e2e/test-infra.ref"
    dockerfile = root / "prototype/forward-proxy/runtime/Dockerfile"
    metadata = {
        "candidate_sha": commit(os.environ.get("GITHUB_SHA")),
        "checkout_sha": checkout_commit(),
        "test_infra_ref": commit(pin_file.read_text().strip()) if pin_file.is_file() else None,
        "runtime_dockerfile_sha256": hashlib.sha256(dockerfile.read_bytes()).hexdigest() if dockerfile.is_file() else None,
    }
    for key in ("e2e_outcome", "cleanup_outcome"):
        value = os.environ.get(key.upper(), "skipped")
        metadata[key] = value if value in OUTCOMES else "skipped"
    passed = write_artifacts(
        Path(os.environ["ARTIFACT_DIR"]), Path(os.environ["SANITIZED_ARTIFACT_DIR"]), metadata
    )
    print("E2E report accepted and run passed." if passed else "E2E did not pass; see sanitized provenance.")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
