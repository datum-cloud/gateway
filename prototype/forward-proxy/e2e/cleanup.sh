#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
failed=0
for suffix in client origin diagnostics audit-setup; do
  if bounded 20 docker container inspect "$NAME-$suffix" >/dev/null 2>&1; then
    bounded 30 docker rm -f "$NAME-$suffix" >/dev/null || failed=1
  fi
done
if [[ -f "$INFRA/Taskfile.yml" ]]; then
  infra_task cluster-down || failed=1
else
  bounded 120 kind delete cluster --name "$NAME" || failed=1
fi
if bounded 20 docker container inspect "$NAME-control-plane" >/dev/null 2>&1; then
  echo 'Node remains after cluster cleanup' >&2
  failed=1
fi
if bounded 20 docker network inspect "$NAME" >/dev/null 2>&1; then
  bounded 30 docker network rm "$NAME" >/dev/null || failed=1
fi
if bounded 20 docker volume inspect "$NAME-audit" >/dev/null 2>&1; then
  bounded 30 docker volume rm "$NAME-audit" >/dev/null || failed=1
fi
for image in "gateway-forward-e2e:$RUN_ID" "gateway-forward-fixture:$RUN_ID"; do
  if bounded 20 docker image inspect "$image" >/dev/null 2>&1; then
    bounded 30 docker image rm "$image" >/dev/null || failed=1
  fi
done
if [[ "$failed" = 0 ]]; then
  rm -rf "$STATE"
fi
exit "$failed"
