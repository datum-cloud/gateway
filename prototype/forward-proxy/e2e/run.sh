#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
umask 077
mkdir -p "$ARTIFACT_DIR"
for previous in results.json baseline.jsonl reject.jsonl missing.jsonl; do
  [[ ! -e "$ARTIFACT_DIR/$previous" ]] || { echo 'Use a fresh ARTIFACT_DIR' >&2; exit 2; }
done
mkdir -p "$E2E_DIR/.state"
mkdir "$STATE" || { echo "Run state exists; cleanup this RUN_ID first" >&2; exit 2; }
finish() {
  status=$?
  trap - EXIT INT TERM
  python3 "$E2E_DIR/report.py" "$ARTIFACT_DIR" 1 || true
  bounded 120 bash "$E2E_DIR/diagnostics.sh" || true
  bash "$E2E_DIR/cleanup.sh" >"$ARTIFACT_DIR/cleanup.log" 2>&1 || status=1
  python3 "$E2E_DIR/report.py" "$ARTIFACT_DIR" "$status" || status=1
  printf '%s\n' "$status" >"$ARTIFACT_DIR/exit-code"
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for tool in docker kind task kubectl kustomize envsubst openssl python3 git; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 2; }
done
bounded 20 docker info >/dev/null
if bounded 20 docker container inspect "$NAME-control-plane" >/dev/null 2>&1 || bounded 20 docker network inspect "$NAME" >/dev/null 2>&1; then
  echo 'RUN_ID already has resources; choose another ID' >&2
  trap - EXIT INT TERM
  rmdir "$STATE"
  exit 2
fi
export E2E_REAL_KIND
E2E_REAL_KIND=$(command -v kind)
mkdir "$STATE/bin"
cat >"$STATE/bin/kind" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" = create && "${2:-}" = cluster ]]; then
  exec "$E2E_REAL_KIND" "$@" --retain
fi
exec "$E2E_REAL_KIND" "$@"
SH
chmod 700 "$STATE/bin/kind"
export PATH="$STATE/bin:$PATH"
PIN=$(tr -d '\n' <"$E2E_DIR/test-infra.ref")
SOURCE="$ROOT/.test-infra"
if ! git -C "$SOURCE" cat-file -e "$PIN^{commit}" 2>/dev/null; then
  bounded 120 git clone https://github.com/datum-cloud/test-infra.git "$STATE/source"
  SOURCE="$STATE/source"
fi
mkdir "$INFRA"
git -C "$SOURCE" archive "$PIN" | tar -x -C "$INFRA"
printf '%s\n' "$PIN" >"$ARTIFACT_DIR/test-infra.ref"
bounded 30 docker volume create --label "gateway.e2e.run=$RUN_ID" "$NAME-audit" >/dev/null
bounded 120 docker run --rm -i --name "$NAME-audit-setup" --network none \
  --mount "type=volume,source=$NAME-audit,target=/audit" --cap-drop ALL "$PYTHON_IMAGE" \
  python -c 'import pathlib,sys; path=pathlib.Path("/audit/audit-policy.yaml"); path.write_bytes(sys.stdin.buffer.read()); path.chmod(0o644)' \
  <"$INFRA/cluster/audit-policy.yaml"
AUDIT_ROOT=$(bounded 20 docker volume inspect "$NAME-audit" --format '{{.Mountpoint}}')
python3 - "$INFRA/cluster/kind-config.yaml" "$STATE/kind.yaml" "$AUDIT_ROOT/audit-policy.yaml" <<'PY'
import pathlib
import sys
text = pathlib.Path(sys.argv[1]).read_text()
start = text.index("    extraPortMappings:")
end = text.index("    kubeadmConfigPatches:")
text = (text[:start] + text[end:]).replace("${REPO_DIR}/cluster/audit-policy.yaml", sys.argv[3])
pathlib.Path(sys.argv[2]).write_text(text)
PY
IMAGE="gateway-forward-e2e:$RUN_ID"
FIXTURE="gateway-forward-fixture:$RUN_ID"
python3 "$E2E_DIR/candidate.py" "$ROOT/prototype/forward-proxy/runtime" "$STATE" "$ARTIFACT_DIR/candidate-source.json" capture
bounded 600 docker build --provenance=false -f "$ROOT/prototype/forward-proxy/runtime/Dockerfile" -t "$IMAGE" "$ROOT" >"$ARTIFACT_DIR/build.log" 2>&1
python3 "$E2E_DIR/candidate.py" "$ROOT/prototype/forward-proxy/runtime" "$STATE" "$ARTIFACT_DIR/candidate-source.json" verify
bounded 20 docker image inspect "$IMAGE" --format '{{.Id}}' >"$ARTIFACT_DIR/candidate-image-id"
bounded 120 docker image save "$IMAGE" --output "$STATE/candidate.tar"
bounded 30 python3 "$E2E_DIR/capture_image.py" "$STATE/candidate.tar" "$ARTIFACT_DIR/candidate-image-id" "$ARTIFACT_DIR/candidate-content.json"
rm "$STATE/candidate.tar"
infra_task create-kind "K8S_VERSION=${K8S_VERSION:-v1.32.0}" >"$ARTIFACT_DIR/create-kind.log" 2>&1
infra_task kind-load-image "IMAGES=$IMAGE" >"$ARTIFACT_DIR/load-image.log" 2>&1
bounded 120 python3 "$E2E_DIR/identity.py" loaded "$NAME-control-plane" "$IMAGE" "$ARTIFACT_DIR"
OCTET=$(python3 - "$RUN_ID" <<'PY'
import hashlib
import sys
print(1 + int.from_bytes(hashlib.sha256(sys.argv[1].encode()).digest()[:2], 'big') % 253)
PY
)
PREFIX="198.18.$OCTET"
bounded 30 docker network create --subnet "$PREFIX.0/24" --label "gateway.e2e.run=$RUN_ID" "$NAME" >/dev/null
bounded 30 docker network connect --ip "$PREFIX.2" "$NAME" "$NAME-control-plane"
INTERFACE=$(bounded 20 docker exec "$NAME-control-plane" ip -j -4 addr | python3 -c 'import json,sys; print(next(item["ifname"] for item in json.load(sys.stdin) if any(addr["local"] == sys.argv[1] for addr in item["addr_info"])))' "$PREFIX.2")
for suffix in 11 12; do
  bounded 20 docker exec "$NAME-control-plane" ip addr add "$PREFIX.$suffix/24" dev "$INTERFACE"
  bounded 20 docker exec "$NAME-control-plane" iptables -t nat -I POSTROUTING 1 -s "$PREFIX.$suffix/32" -d "$PREFIX.3/32" -j ACCEPT
done
bounded 20 docker exec "$NAME-control-plane" sysctl -w net.ipv4.ip_nonlocal_bind=0 >/dev/null
mkdir "$STATE/fixture"
cp "$E2E_DIR/fixtures/Dockerfile" "$E2E_DIR/fixtures/origin.py" "$STATE/fixture/"
cp "$E2E_DIR/tests.py" "$STATE/fixture/tests.py"
bounded 30 openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=origin.e2e \
  -addext 'subjectAltName=DNS:origin.e2e' -addext 'basicConstraints=critical,CA:TRUE' \
  -keyout "$STATE/fixture/key.pem" -out "$STATE/fixture/cert.pem" >/dev/null 2>&1
chmod 644 "$STATE/fixture/key.pem" "$STATE/fixture/cert.pem"
bounded 300 docker build --provenance=false -t "$FIXTURE" "$STATE/fixture" >"$ARTIFACT_DIR/fixture-build.log" 2>&1
bounded 30 docker run -d --name "$NAME-origin" --label "gateway.e2e.run=$RUN_ID" --network "$NAME" --ip "$PREFIX.3" "$FIXTURE" >/dev/null
bounded 30 python3 "$STATE/render.py" --exit-a "$PREFIX.11" --exit-b "$PREFIX.12" --output "$STATE/envoy.json"
kube create configmap gateway-config --from-file="envoy.json=$STATE/envoy.json" >/dev/null
cat >"$STATE/pod.yaml" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: gateway
spec:
  hostNetwork: true
  automountServiceAccountToken: false
  terminationGracePeriodSeconds: 1
  securityContext:
    runAsUser: 101
    runAsGroup: 101
    runAsNonRoot: true
  containers:
  - name: gateway
    image: $IMAGE
    imagePullPolicy: Never
    securityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop: [ALL]
    readinessProbe:
      tcpSocket:
        port: 3128
      periodSeconds: 1
    volumeMounts:
    - name: config
      mountPath: /etc/envoy/envoy.json
      subPath: envoy.json
      readOnly: true
  volumes:
  - name: config
    configMap:
      name: gateway-config
EOF
start_gateway() {
  kube apply -f "$STATE/pod.yaml" >/dev/null
  bounded 150 kubectl wait pod/gateway --for=condition=Ready --timeout=120s
  bounded 30 python3 "$E2E_DIR/identity.py" pod "$NAME-control-plane" "$IMAGE" "$ARTIFACT_DIR"
}
tests() {
  bounded 240 docker run --rm --name "$NAME-client" --network "$NAME" "$FIXTURE" \
    python tests.py --proxy "$PREFIX.2" --origin "$PREFIX.3" --exit-a "$PREFIX.11" --exit-b "$PREFIX.12" --phase "$1" 2>"$ARTIFACT_DIR/$1-client.log"
}
start_gateway
tests baseline | tee "$ARTIFACT_DIR/baseline.jsonl"
bounded 30 docker run -d --name "$NAME-client" --network "$NAME" "$FIXTURE" \
  python tests.py --proxy "$PREFIX.2" --origin "$PREFIX.3" --exit-a "$PREFIX.11" --exit-b "$PREFIX.12" --phase reject >/dev/null
ready=0
for attempt in $(seq 1 60); do
  if bounded 10 docker exec "$NAME-client" test -f /tmp/warm.ready; then ready=1; break; fi
  if [[ "$attempt" -lt 60 ]]; then sleep 1; fi
done
if [[ "$ready" != 1 ]]; then
  bounded 20 docker logs "$NAME-client" >"$ARTIFACT_DIR/reject.jsonl" 2>"$ARTIFACT_DIR/reject-client.log" || true
  echo 'Warm connection setup timed out' >&2
  exit 1
fi
bounded 20 docker exec "$NAME-control-plane" iptables -I OUTPUT 1 -s "$PREFIX.11" -d "$PREFIX.3" -p tcp -j REJECT
bounded 20 docker exec "$NAME-client" touch /tmp/fault.ready
reject_status=$(bounded 180 docker wait "$NAME-client")
bounded 20 docker logs "$NAME-client" >"$ARTIFACT_DIR/reject.jsonl" 2>"$ARTIFACT_DIR/reject-client.log"
bounded 20 docker rm "$NAME-client" >/dev/null
cat "$ARTIFACT_DIR/reject.jsonl"
[[ "$reject_status" = 0 ]]
bounded 20 docker exec "$NAME-control-plane" iptables -D OUTPUT -s "$PREFIX.11" -d "$PREFIX.3" -p tcp -j REJECT
kube delete pod gateway --wait=true >/dev/null
bounded 20 docker exec "$NAME-control-plane" ip addr del "$PREFIX.11/24" dev "$INTERFACE"
start_gateway
tests missing | tee "$ARTIFACT_DIR/missing.jsonl"
echo 'Forward proxy E2E passed (baseline, warmed rejection, fresh missing bind).'
