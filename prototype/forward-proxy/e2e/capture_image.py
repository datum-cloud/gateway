import hashlib
import json
import pathlib
import sys
import tarfile


archive, docker_id_file, destination = map(pathlib.Path, sys.argv[1:])
docker_id = docker_id_file.read_text().strip()
with tarfile.open(archive) as image:
    entries = json.load(image.extractfile("manifest.json"))
    if len(entries) != 1:
        raise SystemExit("Expected exactly one built candidate platform")
    content = image.extractfile(entries[0]["Config"]).read()
    config_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if docker_id != config_digest:
        manifest = image.extractfile("blobs/sha256/" + docker_id.removeprefix("sha256:")).read()
        if "sha256:" + hashlib.sha256(manifest).hexdigest() != docker_id:
            raise SystemExit("Saved candidate manifest identity mismatch")
        if json.loads(manifest).get("config", {}).get("digest") != config_digest:
            raise SystemExit("Saved candidate manifest does not identify its config")
pathlib.Path(destination).write_text(json.dumps({
    "built_docker_id": docker_id,
    "built_config_digest": config_digest,
}, indent=2) + "\n")
