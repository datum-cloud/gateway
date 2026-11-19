import collections
import json
import pathlib
import sys

from contract import PHASE_TESTS


directory = pathlib.Path(sys.argv[1])
exit_code = int(sys.argv[2])
observations = {}
invalid = False
expected = [f"{phase}.{name}" for phase, names in PHASE_TESTS.items() for name in names]
for phase in PHASE_TESTS:
    path = directory / f"{phase}.jsonl"
    if not path.exists():
        continue
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            name = item["name"]
            status = item["status"]
            if (name in expected and name.startswith(phase + ".") and name not in observations
                    and status in ("passed", "failed", "error", "skipped")):
                observations[name] = status
            else:
                invalid = True
        except (ValueError, KeyError, TypeError):
            invalid = True
tests = [{"name": name, "status": observations.get(name, "skipped")} for name in expected]
counts = collections.Counter(item["status"] for item in tests)
if invalid or ((exit_code != 0 or counts["skipped"]) and not (counts["failed"] or counts["error"])):
    tests.append({"name": "runner.lifecycle", "status": "error"})
    counts["error"] += 1
status = "error" if counts["error"] else "failed" if counts["failed"] else "passed"
report = {
    "schema_version": 1,
    "status": status,
    "tests": tests,
    "counts": {key: counts[key] for key in ("passed", "failed", "error", "skipped")},
}
temporary = directory / "results.json.tmp"
temporary.write_text(json.dumps(report, indent=2) + "\n")
temporary.replace(directory / "results.json")
raise SystemExit(0 if status == "passed" else 1)
