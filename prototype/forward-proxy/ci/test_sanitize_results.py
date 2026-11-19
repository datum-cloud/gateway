import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("sanitize_results", Path(__file__).with_name("sanitize-results.py"))
sanitizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sanitizer)


class SanitizeResultsTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "schema_version": 1,
            "status": "passed",
            "tests": [{"name": "http_proxy", "status": "passed"}],
            "counts": {"passed": 1, "failed": 0, "error": 0, "skipped": 0},
        }
        self.metadata = {
            "candidate_sha": "a" * 40,
            "checkout_sha": "a" * 40,
            "test_infra_ref": "b" * 40,
            "e2e_outcome": "success",
            "cleanup_outcome": "success",
        }

    def test_reconstructs_only_permitted_fields(self):
        self.report["headers"] = "Bearer secret"
        self.report["tests"][0]["exception"] = "private response body"
        result = sanitizer.validate_report(self.report)
        self.assertEqual(set(result), {"schema_version", "status", "tests", "counts"})
        self.assertEqual(result["tests"], [{"name": "http_proxy", "status": "passed"}])
        self.assertEqual(result["counts"], self.report["counts"])
        self.assertNotIn("secret", json.dumps(result))

    def test_rejects_invalid_reports(self):
        patches = [
            {"schema_version": True},
            {"schema_version": 2},
            {"status": "unknown"},
            {"tests": []},
            {"tests": [{"name": "Authorization: Bearer token", "status": "passed"}]},
            {"tests": [{"name": "name\n", "status": "passed"}]},
            {"tests": [{"name": "a" * 81, "status": "passed"}]},
            {"tests": [{"name": "valid", "status": "exception text"}]},
            {"tests": self.report["tests"] * 2},
            {"tests": self.report["tests"] * 257},
            {"counts": {"passed": True, "failed": 0, "error": 0, "skipped": 0}},
            {"counts": {"passed": 42, "failed": 0, "error": 0, "skipped": 0}},
            {"tests": [{"name": "valid", "status": "failed"}],
             "counts": {"passed": 0, "failed": 1, "error": 0, "skipped": 0}},
            {"tests": [{"name": "valid", "status": "skipped"}],
             "counts": {"passed": 0, "failed": 0, "error": 0, "skipped": 1}},
            {"tests": [{"name": "valid", "status": "passed"},
                       {"name": "mandatory", "status": "skipped"}],
             "counts": {"passed": 1, "failed": 0, "error": 0, "skipped": 1}},
        ]
        for patch in patches:
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):
                    sanitizer.validate_report({**self.report, **patch})

    def test_rejects_oversized_symlink_and_fifo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            path.write_bytes(b" " * (sanitizer.MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                sanitizer.read_report(path)
            link = Path(directory) / "linked.json"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                sanitizer.read_report(link)
            fifo = Path(directory) / "pipe.json"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                sanitizer.read_report(fifo)

    def test_missing_and_malformed_reports_fail_closed(self):
        for contents in (None, "not JSON", "null", '{"schema_version":1}'):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                raw = Path(directory) / "raw"
                output = Path(directory) / "sanitized"
                raw.mkdir()
                if contents is not None:
                    (raw / "results.json").write_text(contents)
                self.assertFalse(sanitizer.write_artifacts(raw, output, self.metadata))
                provenance = json.loads((output / "provenance.json").read_text())
                self.assertFalse(provenance["report_valid"])
                self.assertEqual(provenance["status"], "error")
                self.assertEqual([path.name for path in output.iterdir()], ["provenance.json"])

    def test_success_requires_report_runner_cleanup_and_provenance(self):
        patches = ({}, {"e2e_outcome": "failure"}, {"e2e_outcome": "cancelled"},
                   {"e2e_outcome": "skipped"}, {"cleanup_outcome": "failure"},
                   {"candidate_sha": None}, {"checkout_sha": "c" * 40}, {"test_infra_ref": None})
        for patch in patches:
            with self.subTest(patch=patch), tempfile.TemporaryDirectory() as directory:
                raw = Path(directory) / "raw"
                output = Path(directory) / "sanitized"
                raw.mkdir()
                (raw / "results.json").write_text(json.dumps(self.report))
                (raw / "kubeconfig").write_text("secret")
                (raw / "environment.log").write_text("secret")
                passed = sanitizer.write_artifacts(raw, output, {**self.metadata, **patch})
                self.assertEqual(passed, not patch)
                self.assertEqual({path.name for path in output.iterdir()}, {"results.json", "provenance.json"})

    def test_failure_report_cannot_pass_successful_process(self):
        report = copy.deepcopy(self.report)
        report["status"] = "failed"
        report["tests"][0]["status"] = "failed"
        report["counts"].update(passed=0, failed=1)
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            (raw / "results.json").write_text(json.dumps(report))
            self.assertFalse(sanitizer.write_artifacts(raw, raw / "sanitized", self.metadata))


if __name__ == "__main__":
    unittest.main()
