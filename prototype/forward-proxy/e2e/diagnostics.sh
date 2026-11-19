#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
bounded 20 docker logs --tail 200 "$NAME-control-plane" >"$ARTIFACT_DIR/node.log" 2>&1 || true
bounded 20 docker exec "$NAME-control-plane" journalctl -u kubelet -u containerd --no-pager -n 100 >"$ARTIFACT_DIR/node-services.log" 2>&1 || true
bounded 20 docker exec "$NAME-control-plane" ip -j address >"$ARTIFACT_DIR/node-addresses.json" 2>/dev/null || true
bounded 20 docker exec "$NAME-control-plane" iptables -t nat -S >"$ARTIFACT_DIR/node-nat.txt" 2>/dev/null || true
bounded 20 docker exec "$NAME-control-plane" readlink /proc/self/ns/net >"$ARTIFACT_DIR/node-netns.txt" 2>/dev/null || true
bounded 20 docker logs --tail 100 "$NAME-origin" >"$ARTIFACT_DIR/origin.log" 2>&1 || true
if [[ -f "$KUBECONFIG" ]]; then
  kube get pods -A -o wide >"$ARTIFACT_DIR/pods.txt" 2>&1 || true
  kube logs gateway --tail=200 >"$ARTIFACT_DIR/gateway.log" 2>&1 || true
  kube describe pod gateway >"$ARTIFACT_DIR/gateway-describe.txt" 2>&1 || true
  kube exec gateway -- readlink /proc/self/ns/net >"$ARTIFACT_DIR/gateway-netns.txt" 2>/dev/null || true
fi
if bounded 20 docker image inspect "gateway-forward-fixture:$RUN_ID" >/dev/null 2>&1; then
  bounded 20 docker run --rm --name "$NAME-diagnostics" --network "container:$NAME-control-plane" \
    "gateway-forward-fixture:$RUN_ID" python -c '
import json, urllib.parse, urllib.request
pattern = r"^cluster\.(exit-a|exit-b)\.(upstream_cx_total|upstream_cx_connect_fail|upstream_cx_connect_timeout|upstream_cx_bind_errors|upstream_rq_total|upstream_rq_5xx|upstream_rq_pending_failure_eject)$"
url = "http://127.0.0.1:9901/stats?format=json&filter=" + urllib.parse.quote(pattern)
with urllib.request.urlopen(url, timeout=5) as response:
    stats = json.load(response)
print(json.dumps(stats))
' >"$ARTIFACT_DIR/envoy-counters.json" 2>/dev/null || true
fi
