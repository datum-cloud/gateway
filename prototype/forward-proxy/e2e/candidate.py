import hashlib
import json
import pathlib
import shutil
import sys


runtime = pathlib.Path(sys.argv[1])
state = pathlib.Path(sys.argv[2])
artifact = pathlib.Path(sys.argv[3])
files = ("Dockerfile", "auth.lua", "envoy.json", "render.py")
hashes = {name: hashlib.sha256((runtime / name).read_bytes()).hexdigest() for name in files}
if sys.argv[4] == "capture":
    shutil.copyfile(runtime / "render.py", state / "render.py")
    artifact.write_text(json.dumps(hashes, indent=2) + "\n")
else:
    if hashes != json.loads(artifact.read_text()):
        raise SystemExit("Runtime source changed during image build; rerun against a stable candidate")
