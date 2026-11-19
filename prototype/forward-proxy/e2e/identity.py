import json
import pathlib
import re
import subprocess
import sys


mode, node, image, directory = sys.argv[1:]
directory = pathlib.Path(directory)
evidence_path = directory / "image-identity.json"


def digest(value):
    value = value.rsplit("@", 1)[-1].rsplit("://", 1)[-1]
    if not value.startswith("sha256:"):
        value = "sha256:" + value
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("Unexpected image identity format")
    return value


def output(*command):
    return subprocess.check_output(command, text=True, timeout=20).strip()


if mode == "loaded":
    source = json.loads((directory / "candidate-content.json").read_text())
    built = digest(source["built_config_digest"])
    docker_id = digest(source["built_docker_id"])
    after = digest(output("docker", "image", "inspect", image, "--format", "{{.Id}}"))
    evidence = {"built_docker_id": docker_id, "built_config_digest": built, "docker_after_load_id": after, "loaded_verified": False}
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
    if docker_id != after:
        raise SystemExit("kind-load-image changed the built candidate image")
    inspection = json.loads(output("docker", "exec", node, "crictl", "inspecti", image))["status"]
    node_id = digest(inspection["id"])
    evidence["node_image_digest"] = node_id
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
    verified = set()

    def verify_content(identity, depth=0):
        if identity == built:
            verified.add(identity)
            return True
        if depth > 3:
            return False
        try:
            content = json.loads(output("docker", "exec", node, "ctr", "--namespace", "k8s.io", "content", "get", identity))
        except (subprocess.SubprocessError, ValueError):
            return False
        if content.get("config", {}).get("digest") == built:
            verified.add(identity)
            verified.add(built)
            return True
        if any(verify_content(digest(item["digest"]), depth + 1) for item in content.get("manifests", [])):
            verified.add(identity)
            return True
        return False

    if not verify_content(node_id):
        raise SystemExit("KIND candidate content does not match built image config digest")
    for reference in inspection.get("repoDigests", []):
        verify_content(digest(reference))
    evidence["verified_content_digests"] = sorted(verified)
    evidence["loaded_verified"] = True
else:
    evidence = json.loads(evidence_path.read_text())
    pod = json.loads(output("kubectl", "--request-timeout=15s", "get", "pod", "gateway", "-o", "json"))
    container = next(item for item in pod["status"]["containerStatuses"] if item["name"] == "gateway")
    pod_id = digest(container["imageID"])
    evidence["pod_image_digest"] = pod_id
    evidence["pod_verified"] = pod_id in evidence["verified_content_digests"]
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
    if not evidence["pod_verified"]:
        raise SystemExit("Running gateway pod does not match verified candidate image content")
evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
