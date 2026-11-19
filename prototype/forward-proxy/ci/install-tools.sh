#!/usr/bin/env bash
set -euo pipefail

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
  echo 'This installer is only for the ubuntu-24.04 x64 CI runner.' >&2
  exit 1
}
: "${RUNNER_TEMP:?RUNNER_TEMP must be set}"
: "${GITHUB_PATH:?GITHUB_PATH must be set}"

tool_dir=$(mktemp -d "$RUNNER_TEMP/forward-proxy-tools.XXXXXX")
download_dir=$(mktemp -d "$RUNNER_TEMP/forward-proxy-downloads.XXXXXX")
trap 'rm -rf "$download_dir"' EXIT

download() {
  local url=$1 digest=$2 destination=$3
  curl --fail --silent --show-error --location --retry 3 \
    --connect-timeout 15 --max-time 180 "$url" -o "$destination"
  printf '%s  %s\n' "$digest" "$destination" | sha256sum --check --status
}

download https://github.com/kubernetes-sigs/kind/releases/download/v0.30.0/kind-linux-amd64 \
  517ab7fc89ddeed5fa65abf71530d90648d9638ef0c4cde22c2c11f8097b8889 \
  "$tool_dir/kind"
download https://dl.k8s.io/release/v1.32.0/bin/linux/amd64/kubectl \
  646d58f6d98ee670a71d9cdffbf6625aeea2849d567f214bc43a35f8ccb7bf70 \
  "$tool_dir/kubectl"
download https://github.com/go-task/task/releases/download/v3.45.4/task_linux_amd64.tar.gz \
  4367eba04abcbcb407578d18d2439ee32604a872419601abec76a829c797fb82 \
  "$download_dir/task.tar.gz"
download https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v5.7.1/kustomize_v5.7.1_linux_amd64.tar.gz \
  ea375e7372f9aa029129d4b2d16c66b7750b7f1213c4f66f910d981c895818d8 \
  "$download_dir/kustomize.tar.gz"
tar -xzf "$download_dir/task.tar.gz" -C "$tool_dir" task
tar -xzf "$download_dir/kustomize.tar.gz" -C "$tool_dir" kustomize
chmod 755 "$tool_dir/"{kind,kubectl,task,kustomize}

sudo apt-get update -qq
sudo apt-get install --yes --no-install-recommends gettext-base=0.21-14ubuntu2
test "$(dpkg-query -W -f='${Version}' gettext-base)" = 0.21-14ubuntu2
printf '%s\n' "$tool_dir" >> "$GITHUB_PATH"
"$tool_dir/kind" version
"$tool_dir/kubectl" version --client
"$tool_dir/task" --version
"$tool_dir/kustomize" version
envsubst --version
