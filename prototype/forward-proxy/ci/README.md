# Forward proxy POC CI

The dedicated `.github/workflows/forward-proxy.yaml` runs on pull requests,
pushes to `main`, and manual dispatch. It does not change the scaffold CI or
publishing workflows. Its single Ubuntu 24.04 x64 job runs Python unit/compile
checks followed by the **actual** Docker/KIND E2E runner, not a mock or a
previously published runtime image.

## Candidate and bootstrap provenance

- Checkout explicitly uses `github.sha`: the synthetic merge revision for a
  `pull_request`, the pushed commit for `push`, and the selected revision for
  dispatch. `git rev-parse HEAD` must match; checkout never persists credentials.
- `bash prototype/forward-proxy/e2e/run.sh` owns the local candidate build using
  root build context and `prototype/forward-proxy/runtime/Dockerfile`, cluster
  creation, loading that locally built image, and network assertions. CI does
  not replace this with a registry `latest` image or duplicate its bootstrap.
- `prototype/forward-proxy/e2e/test-infra.ref` is the **only** test-infra pin.
  The runner is responsible for checking out and verifying that exact revision.
  Inspection of the actual pinned `.test-infra` clone confirmed `create-kind`
  creates KIND and always invokes `install-flux`; `delete-kind` handles teardown.
  `create-kind` needs `envsubst`; `install-flux` needs standalone `kustomize`.
  Neither requires the Flux CLI when invoked directly. Do not substitute
  `cluster-up`/`ensure-tools`, which can download additional unpinned tools.
- Do not use the test-infra `kind-bootstrap` composite: its nested checkout
  omits `ref`, defeating an outer pin. The shared Taskfile defaults `REPO_REF`
  to `main`, so callers must preserve the runner's explicit pin when invoking
  any task that reaches `ensure-repo`.
- The Docker daemon setup is copied from the pinned test-infra CI pattern:
  disable `features.containerd-snapshotter`, restart, and verify the classic
  image store **before** candidate builds or KIND loads. This avoids Docker 29
  multi-platform missing-blob failures. It is a hosted-runner-only change,
  never a change to a developer's Docker daemon.

`provenance.json` records the event candidate SHA, observed checkout SHA,
requested test-infra pin, Dockerfile digest, runner/cleanup outcomes, and report
validity. The requested pin is not itself proof of a successful bootstrap;
that verification belongs to the E2E runner. A fallback always reports error.

## Dependencies and verified pins

Action tags were resolved with `git ls-remote` and full commit IDs are used in
the workflow: checkout v4.2.2, setup-python v5.6.0, upload-artifact v4.6.2.
Python is fixed to 3.12.12. The installer downloads the following Linux x64
releases and verifies embedded SHA-256 values against upstream release assets:

| Tool | Version | Upstream checksum source |
| --- | --- | --- |
| KIND | v0.30.0 | `kind-linux-amd64.sha256sum` |
| Task | v3.45.4 | `task_checksums.txt` |
| kubectl | v1.32.0 | `dl.k8s.io/release/v1.32.0/bin/linux/amd64/kubectl.sha256` |
| Kustomize | v5.7.1 | release `checksums.txt` |
| envsubst | gettext-base 0.21-14ubuntu2 | Ubuntu Noble signed apt metadata |

Kubernetes defaults to v1.32.0, matching the test-infra Taskfile and kubectl
minor version. The Ubuntu runner image, Docker daemon, git, curl, jq, coreutils,
and system package dependencies remain runner-managed; this is not a fully
hermetic environment. Pin updates must reverify upstream checksums, not merely
change download URLs. Exact apt versions intentionally fail rather than float.

## Fork safety, artifacts, and deadlines

Only `contents: read` is granted. There are no repository secrets, publishing,
registry credentials, OIDC, write grants, `pull_request_target`, or self-hosted
runners. Untrusted fork code runs only in the disposable GitHub-hosted job;
GitHub's approval policy for fork workflows still applies. Sibling Datum CI
uses workflow-level read-only permissions; this workflow additionally pins
actions by SHA and does not inherit publishing workflows' secrets or grants.
Normal GitHub artifact-service credentials are necessarily available to the
upload action; artifacts from candidate code are not trusted attestations.

`RUN_ID` is `fp-<run_id>-<run_attempt>-e2e`, DNS-safe and unique across runs and
reruns. `ARTIFACT_DIR` is an absolute, per-run raw-output directory under
`runner.temp`. The independent `always()` cleanup step receives identical
values, even after a partial bootstrap, failure, or cancellation.

The job has a 45-minute ceiling. The runner has a 25-minute process-group
deadline with TERM then KILL after 30 seconds; independent cleanup has three
minutes plus a 15-second kill grace. This reserves time for cleanup, report
validation, and uploading. `always()` is best effort: force cancellation,
runner loss, and the job-wide deadline can prevent any finalizer. Hosted runner
disposal is the final isolation boundary; no production resources are created.

The runner writes `ARTIFACT_DIR/results.json` with schema version 1, an overall
status (`passed`, `failed`, `error`), `tests` containing only stable `name` and
`status` fields (`passed`, `failed`, `error`, `skipped`), and per-status counts.
The sanitizer limits the source to 256 KiB and 1–256 tests, rejects symlinks and
non-regular files, enforces unique names matching `[a-z][a-z0-9_.-]{0,79}`,
recomputes counts, checks declared counts, and reconstructs permitted fields.
Unknown fields are discarded. Exception text, headers, bodies, logs, tokens,
kubeconfigs, and environment dumps are never copied to uploaded artifacts.

A pass requires a valid nonempty report with at least one passed test and no
failed/error/skipped tests, runner success, cleanup success, and matching candidate
provenance. A missing/malformed report produces only an error provenance file
and fails CI; a successful exit alone cannot pass E2E. Upload uses `always()`
and only two explicit filenames from a **separate** sanitized directory, never
the raw artifact directory. Retention is seven days.

## Local validation

The inherited `build-apiserver.yaml` now only validates scaffold manifests.
Its placeholder image/bundle publishing jobs, release trigger, inherited
secrets, and publishing permissions have been removed. Pushes and pull requests
do not publish images or deployment bundles; production publishing requires a
separate explicit implementation.

```sh
actionlint .github/workflows/forward-proxy.yaml
bash -n prototype/forward-proxy/ci/install-tools.sh
shellcheck prototype/forward-proxy/ci/install-tools.sh
python3 -m unittest discover -s prototype/forward-proxy/ci -p 'test_*.py'
python3 -m unittest discover -s prototype/forward-proxy/tests/unit
python3 -m unittest discover -s prototype/forward-proxy/e2e -p 'test_*.py'
```

These checks do not execute GitHub-hosted Actions. The installer and Docker
daemon step target the Linux hosted runner and must not be run on macOS.
Running the actual E2E locally requires the runner's documented Docker/KIND
dependencies and an available Docker daemon; linting and sanitizer fixtures
are not evidence of an E2E pass.
