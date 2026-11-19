#!/usr/bin/env bash
set -euo pipefail
E2E_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$E2E_DIR/../../.." && pwd)
export ROOT
: "${RUN_ID:?Set a unique DNS-safe RUN_ID}"
: "${ARTIFACT_DIR:?Set an absolute ARTIFACT_DIR}"
[[ "$RUN_ID" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#RUN_ID} -le 40 ]] || { echo 'Invalid RUN_ID (max 40 characters)' >&2; exit 2; }
[[ "$ARTIFACT_DIR" = /* && "$ARTIFACT_DIR" != / ]] || { echo 'ARTIFACT_DIR must be absolute and not /' >&2; exit 2; }
export DOCKER_CONTEXT=${DOCKER_CONTEXT:-colima}
export KIND_EXPERIMENTAL_PROVIDER=docker
NAME="fp-$RUN_ID"
PYTHON_IMAGE=python:3.13-alpine@sha256:9ef1a599f863231c3d91e941389bfdc4a2d6070557243d54df1a4e57015b960e
export PYTHON_IMAGE
STATE="$E2E_DIR/.state/$RUN_ID"
INFRA="$STATE/infra"
export KUBECONFIG="$STATE/kubeconfig"
bounded() { python3 "$E2E_DIR/bounded.py" "$@"; }
infra_task() {
  bounded 600 task --dir "$INFRA" "$@" "CLUSTER_NAME=$NAME" \
    "REPO_DIR=$INFRA" "KUBECONFIG_FILE=$KUBECONFIG" "KIND_CFG=$STATE/kind.yaml" "WAIT_TIMEOUT=240s"
}
kube() { bounded 90 kubectl --request-timeout=30s "$@"; }
