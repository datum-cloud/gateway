# Managed forward-proxy prototype

This is a small data-plane experiment, not the full managed-service control
plane. It implements stock Envoy HTTP/1.1 forward requests and terminating
CONNECT tunnels, with two independently authorized outbound identities.

## Layout and ownership

- `runtime/`: pinned Envoy image, configuration renderer, and demonstration
  authentication policy. No custom Envoy binary or external auth service.
- `tests/unit/`: fast configuration/authentication checks.
- `e2e/`: actual KIND environment provisioned through pinned
  `datum-cloud/test-infra` tasks, separate origin/client containers, fault
  injection, bounded execution, diagnostics, and cleanup.
- `ci/`: pinned tool installation and structured artifact sanitization.
- `../../.github/workflows/forward-proxy.yaml` (repository root): candidate
  checkout, fast checks, E2E execution, independent cleanup, sanitized artifacts.

See each directory's README for commands and prerequisites. Test results and
execution limitations are recorded in `RESULTS.md`; configuration inspection
alone is not end-to-end validation.

## Request contract

Configure an HTTP proxy on port 3128. Supply HTTP Basic `Proxy-Authorization`
and optionally `X-Datum-Egress-Profile: exit-a` or `exit-b`. Demonstration
credentials are deliberately public and are not suitable for deployment:

| Credential | Authorized profiles | Default |
| --- | --- | --- |
| `tenant-a:demo-a` | `exit-a`, `exit-b` | `exit-a` |
| `tenant-b:demo-b` | `exit-b` | `exit-b` |

Missing or invalid credentials receive 407; forbidden or unknown profiles
receive 403. Client-supplied internal routing headers are not trusted. The
server authenticates each ordinary HTTP request, including requests on reused
downstream connections. CONNECT authorization applies to the tunnel handshake;
subsequent TLS bytes remain opaque and are not intercepted.

Each profile has its own upstream cluster and explicit `upstream_bind_config`.
There is no alternate source address or unbound fallback cluster. Pool isolation
is by exit identity, not by customer: two authorized customers selecting the
same exit may share that exit's ordinary HTTP upstream pool. Customers cannot
select an unauthorized exit by reusing a downstream connection. CONNECT tunnels
are separate byte streams.

`freebind: false` requires configured source addresses when opening connections.
Removing an address does not necessarily terminate a connection that was
already established. The E2E suite distinguishes failure of warmed flows under
an explicit packet-reject rule from fresh-bind failure after address removal
and proxy restart. Neither case may silently switch to the healthy exit.

## Network and security boundaries

The E2E environment assigns both addresses in the disposable KIND node network
namespace, which the Envoy pod shares via `hostNetwork`. A separate origin
container observes actual socket peer addresses on a dedicated Docker bridge;
it does not trust forwarded headers. This setup avoids confusing CNI/NAT
behavior with source binding. Privileged networking operations apply only to
the disposable fixture, not the non-root Envoy process.

The listener is plaintext and uses fixed demo credentials. It is not a safe
public proxy: there is no production destination/SSRF policy, private-network
blocking, credential rotation/revocation service, quota system, or abuse
enforcement. Do not expose it to an untrusted network. TLS to the destination is
end-to-end inside CONNECT; protecting the client-to-proxy authentication channel
is separate future work.

## Product scope

The product context is `datum-cloud/enhancements` PR #945; repository creation
was `datum-cloud/infra` PR #9572. Browserbase owns browser sessions and its tenant
mapping. Datum supplies reusable proxy infrastructure, without mandatory
per-tenant or per-session platform APIs.

These local source bindings are **not residential exits**. Residential service
requires a future external provider integration, with distinct routing and
availability semantics. GSLB, Galactic changes, production deployment, supplier
integration, full platform controllers, and reverse proxy implementation are
out of scope. Forward and reverse proxy fleets will be separate.

## Reproducibility and CI

The runtime is Envoy v1.40.2 pinned by image digest. The exact test-infra revision
is in `e2e/test-infra.ref`; its provisioning, image-loading, and teardown tasks
are reused without modifying that repository. CI builds the checked-out gateway
candidate locally and loads that image into KIND, not a released registry image.

The PR job uses read-only repository permissions and no privileged secrets.
Raw diagnostics remain distinct from the uploaded allowlisted structured
reports. Cleanup runs within the runner and independently in CI. Abrupt machine
loss or forced termination can still require explicit cleanup; the test runner
is not a production resource reconciler.
