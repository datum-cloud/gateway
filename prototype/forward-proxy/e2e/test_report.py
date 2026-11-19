import ast
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from contract import PHASE_TESTS


class ReportTests(unittest.TestCase):
    def report(self, lines=(), exit_code=0, complete=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            if complete:
                for phase, names in PHASE_TESTS.items():
                    (directory / f"{phase}.jsonl").write_text("\n".join(
                        json.dumps({"name": f"{phase}.{name}", "status": "passed"}) for name in names
                    ))
            if lines:
                with (directory / "baseline.jsonl").open("a") as target:
                    target.write("\n" + "\n".join(lines))
            subprocess.run([
                sys.executable, str(pathlib.Path(__file__).with_name("report.py")),
                str(directory), str(exit_code),
            ], check=False)
            return json.loads((directory / "results.json").read_text())

    def test_success(self):
        report = self.report(complete=True)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["counts"], {"passed": 39, "failed": 0, "error": 0, "skipped": 0})

    def test_failure(self):
        report = self.report(['{"name":"baseline.readiness","status":"failed"}'], 1)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["counts"]["failed"], 1)

    def test_infrastructure_error(self):
        report = self.report([], 201)
        self.assertEqual(report["status"], "error")
        self.assertIn({"name": "runner.lifecycle", "status": "error"}, report["tests"])
        self.assertEqual(report["counts"]["skipped"], 39)

    def test_cleanup_failure_cannot_pass(self):
        report = self.report(exit_code=1, complete=True)
        self.assertEqual(report["status"], "error")

    def test_no_test_cannot_pass(self):
        self.assertEqual(self.report([], 0)["status"], "error")

    def test_only_contract_fields_survive(self):
        report = self.report([
            'arbitrary raw output',
            '{"name":"baseline.readiness","status":"passed","secret":"never-export"}',
            '{"name":"unsafe name","status":"passed"}',
        ], 0)
        self.assertEqual(set(report), {"schema_version", "status", "tests", "counts"})
        self.assertIn({"name": "baseline.readiness", "status": "passed"}, report["tests"])
        self.assertNotIn("never-export", json.dumps(report))

    def test_incomplete_coverage_cannot_pass(self):
        report = self.report(['{"name":"baseline.readiness","status":"passed"}'])
        self.assertEqual(report["status"], "error")

    def test_duplicate_cannot_pass(self):
        report = self.report(['{"name":"baseline.readiness","status":"passed"}'], complete=True)
        self.assertEqual(report["status"], "error")

    def test_contract_matches_executable_phases(self):
        tree = ast.parse(pathlib.Path(__file__).with_name("tests.py").read_text())
        branches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Attribute)
                    and node.test.left.attr == "phase"]
        for branch in branches:
            phase = branch.test.comparators[0].value
            names = []
            for statement in branch.body:
                for call in ast.walk(statement):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "check":
                        names.append(call.args[0].value)
            self.assertEqual(set(names), set(PHASE_TESTS[phase]))
            self.assertEqual(len(names), len(PHASE_TESTS[phase]))


if __name__ == "__main__":
    unittest.main()
