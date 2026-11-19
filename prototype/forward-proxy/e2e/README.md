# Forward proxy E2E

This runner builds the **current checkout's candidate runtime**, loads it into a
real KIND cluster through the pinned `datum-cloud/test-infra` tasks, and tests
origin-observed socket source addresses. It does not deploy production controllers
or change the Docker context, host sysctls, root Taskfile, or workflows.

## Run and independently clean up

From the repository root:

```bash
export RUN_ID="local-$(date +%s)"
export ARTIFACT_DIR="/tmp/gateway-forward-proxy-$RUN_ID"
export DOCKER_CONTEXT=colima
bash prototype/forward-proxy/e2e/run.sh

# Safe to repeat, including after a failed or externally cancelled run:
bash prototype/forward-proxy/e2e/cleanup.sh
```

`RUN_ID` must be globally unique on the selected Docker daemon, lowercase DNS-safe,
and at most 40 characters. `ARTIFACT_DIR` must be absolute and fresh. Both variables
must be identical for independent cleanup. `DOCKER_CONTEXT` defaults to `colima`;
CI should explicitly select its own working context (usually `default`). No Docker
context is selected globally. Use the same checkout for independent cleanup.

Prerequisites: Bash, Python 3, Git, tar, OpenSSL supporting `-addext`, Docker with
BuildKit, KIND 0.30.0, Task, kubectl, kustomize, and envsubst. CI's Task 3.45.4,
kubectl 1.32.0, and kustomize 5.7.1 are suitable. Internet access is needed for
uncached images, Flux manifests, and a missing infra clone. The default node image
is the pinned Taskfile's `kindest/node:v1.32.0`; `K8S_VERSION` can override it for
explicit diagnostics, and is not an automatic fallback. Allow roughly 15 minutes
for a cold run. Each external operation and test phase has a timeout.

## Infrastructure and candidate provenance

`test-infra.ref` pins `c60b37875d26e4050a2256e49b4ab182f27934e4`.
The runner reads that exact commit from `.test-infra` using `git archive`, without
checkout, fetch, or modification of that clone. Uncommitted clone changes are not
used. If the object is unavailable, it clones into private run state and archives
the exact pin. It never touches `../test-infra` or its user files.

The actual archived Taskfile runs `create-kind`, `kind-load-image`, and
`cluster-down`. **`create-kind` installs Flux** and waits for four controller
deployments; the upstream installer also includes the image controllers.
This is not an addon-free cluster. No cert-manager, Kyverno, Envoy Gateway, or
production gateway controllers are installed. `REPO_DIR`, `KIND_CFG`, and
`KUBECONFIG_FILE` point into run-specific state. The KIND configuration is derived
from the pinned configuration: audit policy mounts and kubeadm audit patches stay;
unrelated fixed host ports 30000, 30443, and 30080 are removed. The audit
policy is copied through the pinned Python helper into a unique Docker volume;
the KIND read-only audit file mount uses that volume's daemon-side mountpoint.
This works even when a remote Docker daemon cannot bind the caller's checkout.
The volume contains only the exact archived policy, and cleanup removes it.
The bootstrap
composite action is deliberately not used: its nested checkout has no pinned ref.
A private wrapper adds only `--retain` to KIND creation, so failure diagnostics
can be collected before the actual `cluster-down` task removes the node.

The candidate build uses the repository root as context and
`prototype/forward-proxy/runtime/Dockerfile`. No fixture substitutes for Envoy or
its Lua authentication. Runtime source hashes are checked around the build;
concurrent changes abort the run instead of mixing revisions. A captured copy of
the actual `runtime/render.py` generates the per-run `/etc/envoy/envoy.json`, mounted
from a ConfigMap. The candidate image provides `/etc/envoy/auth.lua`. Image ID and
source hashes are raw diagnostic provenance, not claims that the tree was clean.
`kind-load-image` tries `docker pull` even for an existing tag. The runner compares
the Docker image ID before and after that task, verifies the node's
`crictl inspecti` identity against that config (resolving containerd manifest/index
digests to their config digest when necessary), then verifies each gateway pod's
`status.containerStatuses[].imageID` against the verified content. A mismatch
fails the run. `image-identity.json` contains only normalized SHA256 identities
and verification booleans; it is separate from the minimal results contract.
Docker's classic store uses config digests for image IDs, while its containerd
store can use manifest digests. A private `docker image save` archive establishes
the exact config digest and verifies its relationship to the built Docker ID;
the archive is immediately removed and never placed in artifacts.
The fixture base is pinned Python 3.13 Alpine by digest. Build attestations are
disabled to avoid introducing a multi-manifest attestation into KIND image loads.

## Network and privilege boundary

Each run has a KIND node, a uniquely named secondary Docker bridge, an origin
container, and a client container. The bridge subnet is
`198.18.<hash-of-run-id>.0/24`: node `.2`, independent origin `.3`, exits `.11/.12`.
Docker rejects collisions; choose another RUN_ID rather than reusing another run's
network. At most 253 distinct subnets are available; uniqueness is checked by
Docker rather than guaranteed by the hash.

The gateway pod uses `hostNetwork: true` to share **the KIND node's namespace**,
not the Docker host namespace. The fixture installs both exit aliases on the
node's secondary interface with `docker exec`. KIND's masquerade chain can also
rewrite host-network traffic to a newly attached bridge: two narrowly scoped
node-namespace POSTROUTING exemptions preserve `.11/.12` only when destined for
the origin's `.3` address. There is no CNI SNAT between Envoy and the origin.
These are fixture-only rules, not a production routing recommendation.
Envoy runs as non-root with all capabilities dropped and privilege
escalation disabled. Only the disposable KIND node performs privileged address,
network-namespace-local `ip_nonlocal_bind`, and iptables operations. Origin and
client run as UID 65534 in separate containers/namespaces, without extra privileges.

The origin reports its actual accepted socket's peer address and a monotonically
assigned connection ID. HTTP uses absolute-form proxy requests. HTTPS uses a real
CONNECT tunnel and TLS with certificate verification enabled: the client trusts
only the newly generated self-signed fixture CA and verifies `origin.e2e`. No
`verify=False` or insecure TLS flag is used. The proxy cannot inspect encrypted
application headers inside CONNECT; header-stripping assertions apply to HTTP and
the normal TLS echo content, not to headers intentionally encrypted by a client.

## Assertions

- Tenant A defaults to exit A and can select exit B; tenant B defaults to B.
- HTTP and CONNECT/TLS origins observe the precise configured `.11` or `.12` peer.
- One persistent downstream HTTP connection alternates tenant/profile; each exit
  reuses its own upstream connection ID and never the other exit's ID.
- Multiple requests reuse each TLS tunnel, and concurrent HTTP requests and TLS
  tunnels retain the correct source identity.
- Missing/wrong authentication yields 407, unauthorized/unknown profiles yield
  403, duplicate credentials and selectors are rejected, and forged internal
  cluster/tenant selection cannot grant an unauthorized exit.
- Proxy credentials and internal routing headers do not reach the HTTP origin.
- A synchronized fault phase warms HTTP and TLS connections **before** rejecting
  exit A packets. Existing A connections and fresh attempts fail without fallback;
  an existing B tunnel and fresh B requests still work.
- A separate phase removes A's address and **recreates the gateway pod**, destroying
  every upstream pool before testing fresh bind failure. B remains healthy. This
  deliberately does not confuse an existing connection surviving address removal
  with a successful fresh bind. `ip_nonlocal_bind=0` and the runtime's disabled
  freebind are required. REJECT is tested, not a silent blackhole.

The suite exercises IPv4, HTTP/1.1, and local origin reachability. It does not prove
production routing, public Internet egress, DNS policy, IPv6, HTTP/2, production
credential management, or kernel policy behavior beyond this fixture. KIND's own
node image and Flux transitive image tags follow the pinned infrastructure source;
not every downloaded dependency is content-digest pinned by this suite.

## Reports, secrets, and cleanup

`ARTIFACT_DIR` is **raw local diagnostics, never an upload directory**. On normal
completion, test failure, infrastructure failure, or handled INT/TERM, the runner
atomically writes `results.json`:

```json
{
  "schema_version": 1,
  "status": "error",
  "tests": [{"name": "runner.lifecycle", "status": "error"}],
  "counts": {"passed": 0, "failed": 0, "error": 1, "skipped": 0}
}
```

The only fields are the agreed schema, status, stable lowercase test identifiers,
test statuses, and integer counts. There are no exception messages, response
headers/bodies, credentials, environment values, file contents, or command output
in that report. CI should validate and reconstruct these fields into a separate
upload directory, and require runner exit zero plus at least one passed test and
no failures/errors. A SIGKILL, invalid input, or an early prerequisite failure can
prevent report creation; CI must treat absence as an error, never a pass.
All 39 mandatory phase-qualified test identifiers are declared in `contract.py`.
Missing tests appear as skipped, but incomplete coverage, duplicate observations,
or any skipped mandatory test prevents overall success. Report generation itself
returns nonzero unless every mandatory test passed and the lifecycle succeeded.
Failure diagnostics include bounded gateway/node/origin logs, pod description,
node interface addresses and namespace identifiers, and selected numeric Envoy
exit-cluster counters. They do not request admin config dumps or kubeconfig, and
the candidate has no request/access logging enabled. Never upload these raw files.

Private state is `e2e/.state/$RUN_ID` (gitignored, private permissions). Kubeconfig, rendered config,
and TLS private key are never copied to ARTIFACT_DIR. Cleanup deletes the exact
run's node, origin/client/helper containers, audit volume, network, candidate/fixture image tags, and
private state. It leaves shared images, Docker build caches, and unrelated
containers alone. The disposable fixture key can remain in Docker's build cache;
it is not a production secret and the cache is not exported by this runner.
Cleanup failure preserves state for a retry and makes the run fail. SIGKILL cannot
run traps: CI's independent cleanup step is mandatory. Logs are raw, local-only.

Fast local harness checks:

```bash
python3 -m unittest discover -s prototype/forward-proxy/e2e -p 'test_*.py'
bash -n prototype/forward-proxy/e2e/run.sh prototype/forward-proxy/e2e/cleanup.sh
shellcheck --severity=warning -x prototype/forward-proxy/e2e/*.sh
```

### Local execution evidence (2026-11-19)

Run `agent-netfix-1795118799` completed on Colima ARM64 with default Kubernetes
v1.32.0 and the pinned test-infra source: **39 passed, 0 failed, 0 errors, 0 skipped**,
runner exit 0. All 27 baseline, 8 warmed-REJECT, and 4 fresh missing-address tests
executed. Docker's before/after image ID, the node image content, and both gateway
pod instances passed image identity checks. Normal cleanup completed; independent
cleanup was also exercised after completed runs.

The sanitized report is locally available at
`/tmp/gateway-forward-e2e-artifacts-agent-netfix-1795118799/results.json`.
This path is execution evidence, not a portable fixture or a committed artifact.
The 16 local harness unit tests, Bash syntax checks, and ShellCheck warning-level
checks also passed. A separate unprivileged fixture smoke check exercised HTTP,
verified TLS, and connection IDs before the complete E2E run succeeded.

Earlier attempts exposed host inotify exhaustion, an inaccessible caller-side
audit bind path on the Docker daemon, Docker manifest-vs-config ID differences,
and KIND's masquerading of the secondary bridge. No host limits or unrelated
clusters were changed. The daemon-side audit volume, digest normalization, and
source-specific fixture NAT exemptions address the latter three issues. Host
inotify exhaustion can still block KIND startup on an overloaded environment;
such a run produces an error report, not a successful skip.
