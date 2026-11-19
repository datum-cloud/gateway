# Executed validation — 2026-11-19

These are executed results, not predictions from illustrative configuration.
The coordinator independently ran the integrated working tree after the three
implementation threads finished. No commit, push, PR, production deployment,
or GitHub Actions run was performed.

## Independent full E2E

Executed from the gateway repository root on macOS/arm64 using the working
`colima` Docker context (Docker server 29.2.1), KIND 0.30.0, and Kubernetes 1.32.0:

```sh
export DOCKER_CONTEXT=colima
export RUN_ID=coordinator-20261119-a
export ARTIFACT_DIR=/tmp/gateway-forward-proxy-coordinator-20261119-a
export PYTHONDONTWRITEBYTECODE=1
bash prototype/forward-proxy/e2e/run.sh
bash prototype/forward-proxy/e2e/cleanup.sh
```

Both commands exited **0**. The runner reported **39 passed, 0 failed, 0 errors,
0 skipped**: 27 baseline checks, eight warmed-exit rejection checks, and four
missing-address checks. Independent cleanup was executed after automatic
cleanup to verify idempotence.

A second independent run, `coordinator-20261119-b`, also exited 0 with all 39
checks passing and the same verified candidate image identity. Its evidence is
in `/tmp/gateway-forward-proxy-coordinator-20261119-b`; automatic cleanup
completed successfully. This repeated the full environment rather than reusing
the first run's cluster or report.

The exact archived test-infra revision was
`c60b37875d26e4050a2256e49b4ab182f27934e4`. Its real `create-kind` task installed
Flux and its `kind-load-image` and `cluster-down` tasks were used. The candidate
ran as the real Envoy pod; the origin and client ran in separate containers.

| Acceptance area | Executed evidence |
| --- | --- |
| HTTP source selection | Origin socket peer matched `198.18.74.11` for exit-a and `198.18.74.12` for exit-b. |
| HTTPS CONNECT | Both source addresses matched through real TLS tunnels; the client verified the fixture certificate and hostname. |
| Authentication | Missing/wrong credentials rejected for HTTP and CONNECT; defaults and allowed explicit profile selection succeeded. |
| Authorization/isolation | Tenant-b could not select exit-a; spoofed routing/tenant headers and duplicate auth/profile values did not bypass policy. |
| Reuse | One downstream connection alternated tenants/profiles while preserving the appropriate distinct upstream connection identities; missing/forbidden credentials on a reused connection were still rejected. |
| Concurrency | Concurrent ordinary requests and distinct TLS tunnels preserved their authorized exit identities. |
| Warm failure | OUTPUT REJECT for exit-a blocked warmed HTTP/TLS and fresh connections; exit-b stayed usable. |
| Missing bind | After removing exit-a and restarting Envoy to discard existing pools, HTTP and CONNECT failed while exit-b remained usable. |
| Namespace | Gateway and node network namespace IDs matched; assigned addresses lived on the node's secondary interface, inside Envoy's namespace. |
| Cleanup | Run resources and private state were removed; repeated standalone cleanup succeeded. |

The final Envoy counters after the missing-bind phase recorded two exit-a
connection failures and two 5xx responses; exit-b had zero connection failures.
These counters support, rather than replace, the client/origin assertions.

## Exact candidate evidence

The run captured hashes of `Dockerfile`, `auth.lua`, `envoy.json`, and `render.py`
before and after the build. The captured renderer produced the run-specific
mounted configuration. This tested **uncommitted working-tree code**, not a
claim that the prototype already exists at the repository's current commit.

- Built Docker manifest identity:
  `sha256:2deabfac321ff2ec31c882dca4d8f3b62c1caab64b519d8fbb6f493ddfbcfa5b`
- Built image config identity:
  `sha256:17eeedf88b96efa70bc2ece85f8fca52fe78c497a5d0b6c8417099c81b7c748b`
- Docker identity was unchanged after test-infra's image-load helper.
- KIND's image content resolved to that config identity; both gateway pod
  instances matched verified image content.

The machine-readable local evidence is in the run's `results.json`,
`candidate-source.json`, `candidate-content.json`, and `image-identity.json`.
Raw diagnostics are local only. The CI sanitizer was also executed against this
real report and accepted all 39 checks, writing only reconstructed results and
provenance into `/tmp/gateway-forward-proxy-coordinator-20261119-a-sanitized`.
For this local sanitizer exercise, GitHub outcome variables were supplied from
the observed command results; it was **not** an Actions execution or a clean
commit attestation.

## Cancellation and failure recovery

A separate run, `coordinator-20261119-cancel`, was interrupted with SIGTERM to
the runner's process group immediately after its KIND node existed. The
interrupted runner exited nonzero, its report did not claim a pass, and a
separate `cleanup.sh` process exited 0. Explicit inspection confirmed the KIND
node, dedicated network, audit-policy volume, and private run-state directory
were absent. Evidence is in
`/tmp/gateway-forward-proxy-coordinator-20261119-cancel`.

This exercises cancellation during partial bootstrap. Force-killing a runner,
machine loss, and job-wide timeout can prevent finalizers; independent cleanup
and disposable hosted-runner isolation remain necessary.

## Fast checks

The coordinator reran these checks successfully:

- Nine runtime Python tests, six CI sanitizer tests, and 16 harness tests.
- Stock pinned Envoy `--mode validate` against the candidate configuration.
- `actionlint .github/workflows/forward-proxy.yaml`.
- Bash syntax checks for the E2E and CI shell scripts.
- `shellcheck -x -P prototype/forward-proxy/e2e prototype/forward-proxy/e2e/*.sh`
  and ShellCheck of the CI installer.

The runtime implementation thread additionally executed 47 Lua unit cases and
ten live Envoy smoke cases; see `runtime/README.md` for its exact commands and
scope. The integrated E2E above independently covers the actual TLS path.

## Not validated or promised

- The GitHub-hosted Ubuntu workflow and Linux tool installer have **not** been
  executed through Actions. Their syntax, pins, report handling, and security
  configuration were checked; the same E2E entrypoint was run locally.
- IPv6, HTTP/2/3 proxy ingress, internet destinations, public routability,
  provider exits, and production performance/availability are not tested.
- Failure injection uses immediate packet REJECT, not a silent network
  blackhole. Timeouts are configured, but blackhole recovery is not claimed.
- Local source-IP binding is not residential routing. Fixtures explicitly
  exempt selected source-to-origin traffic from KIND masquerading; arbitrary
  deployment NAT behavior is not validated.
- There is no destination/SSRF enforcement or production credential lifecycle.
  Fixed demo credentials and a plaintext listener must stay isolated.
- The unrelated API-server scaffold was not modified or built by these checks.
