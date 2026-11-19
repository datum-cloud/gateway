import hashlib
import io
import json
import pathlib
import runpy
import sys
import tempfile
import tarfile
import unittest
from unittest.mock import patch


class ImageIdentityTests(unittest.TestCase):
    built = "sha256:" + "a" * 64
    manifest = "sha256:" + "b" * 64
    other = "sha256:" + "c" * 64

    def execute(self, mode, directory, command_output):
        if mode == "loaded":
            source = (directory / "candidate-image-id").read_text()
            (directory / "candidate-content.json").write_text(json.dumps({"built_docker_id": source, "built_config_digest": source}))
        with patch.object(sys, "argv", ["identity.py", mode, "node", "candidate:run", str(directory)]):
            with patch("subprocess.check_output", side_effect=command_output):
                runpy.run_path(str(pathlib.Path(__file__).with_name("identity.py")), run_name="__main__")

    def outputs(self, command, **kwargs):
        if command[:3] == ("docker", "image", "inspect"):
            return self.built
        if "inspecti" in command:
            return json.dumps({"status": {"id": self.manifest, "repoDigests": ["candidate@" + self.manifest]}})
        if "ctr" in command:
            return json.dumps({"config": {"digest": self.built}})
        if command[0] == "kubectl":
            return json.dumps({"status": {"containerStatuses": [{"name": "gateway", "imageID": "containerd://" + self.manifest}]}})
        raise AssertionError("Unexpected command")

    def test_manifest_to_config_and_pod(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "candidate-image-id").write_text(self.built)
            self.execute("loaded", directory, self.outputs)
            self.execute("pod", directory, self.outputs)
            evidence = json.loads((directory / "image-identity.json").read_text())
            self.assertTrue(evidence["loaded_verified"])
            self.assertTrue(evidence["pod_verified"])
            self.assertEqual(evidence["built_config_digest"], self.built)

    def test_pull_overwrite_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "candidate-image-id").write_text(self.other)
            with self.assertRaises(SystemExit):
                self.execute("loaded", directory, self.outputs)

    def test_loaded_content_mismatch_rejected(self):
        def changed(command, **kwargs):
            if "ctr" in command:
                return json.dumps({"config": {"digest": self.other}})
            return self.outputs(command, **kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "candidate-image-id").write_text(self.built)
            with self.assertRaises(SystemExit):
                self.execute("loaded", directory, changed)

    def test_pod_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            (directory / "candidate-image-id").write_text(self.built)
            self.execute("loaded", directory, self.outputs)
            def changed(command, **kwargs):
                return json.dumps({"status": {"containerStatuses": [{"name": "gateway", "imageID": self.other}]}})
            with self.assertRaises(SystemExit):
                self.execute("pod", directory, changed)


class CaptureImageTests(unittest.TestCase):
    def capture(self, manifest_store, wrong_config=False):
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            config = b'{"rootfs":{"type":"layers","diff_ids":[]}}'
            config_hash = hashlib.sha256(config).hexdigest()
            manifest = json.dumps({"config": {"digest": "sha256:" + ("c" * 64 if wrong_config else config_hash)}}).encode()
            manifest_hash = hashlib.sha256(manifest).hexdigest()
            files = {
                "manifest.json": json.dumps([{"Config": "blobs/sha256/" + config_hash}]).encode(),
                "blobs/sha256/" + config_hash: config,
                "blobs/sha256/" + manifest_hash: manifest,
            }
            with tarfile.open(directory / "image.tar", "w") as archive:
                for name, content in files.items():
                    entry = tarfile.TarInfo(name)
                    entry.size = len(content)
                    archive.addfile(entry, io.BytesIO(content))
            (directory / "id").write_text("sha256:" + (manifest_hash if manifest_store else config_hash))
            with patch.object(sys, "argv", ["capture_image.py", str(directory / "image.tar"), str(directory / "id"), str(directory / "output.json")]):
                runpy.run_path(str(pathlib.Path(__file__).with_name("capture_image.py")), run_name="__main__")
            self.assertEqual(json.loads((directory / "output.json").read_text())["built_config_digest"], "sha256:" + config_hash)

    def test_classic_store(self):
        self.capture(False)

    def test_manifest_store(self):
        self.capture(True)

    def test_manifest_config_mismatch_rejected(self):
        with self.assertRaises(SystemExit):
            self.capture(True, wrong_config=True)
