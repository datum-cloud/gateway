# Stock Envoy forward proxy POC (demo only)

This standalone IPv4 HTTP/1.1 explicit proxy uses unmodified Envoy **1.40.2**,
pinned to the multiarchitecture image digest
`envoyproxy/envoy@sha256:8de97e654644827d62afdbf7e1d3dc9fee01a8690d410dd1c63a30dc6b6cecbb`.
It accepts ordinary absolute-form HTTP requests and terminates the HTTP CONNECT
handshake into a raw TCP tunnel. HTTPS inside that tunnel remains end-to-end TLS:
there is no TLS interception, certificate generation, or upstream TLS wrapper.

## Integration interface

Run from the repository root:

```sh
docker --context colima build \
  -f prototype/forward-proxy/runtime/Dockerfile -t datum-forward-proxy:poc .
docker --context colima run --rm datum-forward-proxy:poc --version
docker --context colima run --rm datum-forward-proxy:poc \
  -c /etc/envoy/envoy.json --mode validate --log-level warning
```

The Dockerfile also accepts the requested generic invocation
`docker build -f prototype/forward-proxy/runtime/Dockerfile -t IMAGE .`.
Use `--context colima` on this development machine; do not change the global
Docker context. The image entrypoint is `/usr/local/bin/envoy`, not a wrapper.
Its default command loads `/etc/envoy/envoy.json`, uses two workers, and logs at
warning level. It runs as UID/GID `65532:65532` without needing privileged ports.
The image contains both `/etc/envoy/envoy.json` and `/etc/envoy/auth.lua`.

| Interface | Default |
| --- | --- |
| Proxy listener | `0.0.0.0:3128`, plaintext HTTP/1.1 |
| Admin listener | `127.0.0.1:9901`, inside the container network namespace |
| `exit-a` source | `198.18.0.11`, ephemeral source port |
| `exit-b` source | `198.18.0.12`, ephemeral source port |
| Ordinary request timeout | 30 seconds |
| CONNECT total route timeout | Disabled; 300-second stream idle timeout still applies |
| Upstream connect timeout | 5 seconds |

Render a replacement bootstrap using Python's standard library only:

```sh
python3 prototype/forward-proxy/runtime/render.py \
  --exit-a 198.18.0.11 --exit-b 198.18.0.12 --output /tmp/envoy.json
```

Both IP arguments are optional and default to the table above; `--output` is
required. The output parent directory must already exist. The renderer rejects
hostnames, IPv6, unspecified/multicast/broadcast addresses, and equal source IPs.
`envoy.json` is the checked-in default output; regenerate it after renderer edits.
The Lua path is always `/etc/envoy/auth.lua`.

An integration harness can mount the replacement without changing the command:

```sh
docker --context colima run --rm \
  --mount type=bind,src=/tmp/envoy.json,dst=/etc/envoy/envoy.json,readonly \
  -p 127.0.0.1:3128:3128 datum-forward-proxy:poc
```

**Networking is the harness's responsibility.** Provision both source IPs in
Envoy's network namespace and provide routes/return paths before testing
successful forwarding. The image does not assign addresses or configure NAT.
Keep mounted files readable by UID 65532 and the network namespace's
`net.ipv4.ip_nonlocal_bind=0`. With that normal kernel setting, `freebind: false`
means missing source addresses fail rather than falling back to another exit or
an unbound socket; do not override it with kernel-level nonlocal binding.
Validation alone does not prove those addresses exist. External NAT may rewrite
the source: a bound address is not automatically a public egress identity.

Docker bind paths are resolved by the daemon. In this Colima environment, the
repository bind mount was not visible in a test container. If needed, create a
container, use `docker --context colima cp /tmp/envoy.json CONTAINER:/etc/envoy/envoy.json`,
then start it, or use a daemon-visible read-only volume. Do not publish admin.

## Authentication and selection

| Demo Basic credentials | Allowed profiles | Default |
| --- | --- | --- |
| `tenant-a:demo-a` | `exit-a`, `exit-b` | `exit-a` |
| `tenant-b:demo-b` | `exit-b` | `exit-b` |

Supply Basic credentials in `Proxy-Authorization`. The optional
`X-Datum-Egress-Profile` value must be exactly `exit-a` or `exit-b` and authorized
for that tenant. Missing/bad/duplicate credentials produce **407** with a Basic
challenge. Unknown, empty, duplicate, or unauthorized selectors produce **403**.
Authentication takes precedence over profile checks. The Basic scheme is
case-insensitive; the demo accepts the canonical Base64 encoding of its two
fixed credentials, not a general credential database. Ordinary `https://`
absolute-form requests are rejected with 400; use CONNECT for HTTPS.

```sh
curl --noproxy '' -x http://127.0.0.1:3128 --proxy-user tenant-a:demo-a \
  --proxy-header 'X-Datum-Egress-Profile: exit-b' http://ORIGIN:PORT/
curl --noproxy '' -x http://127.0.0.1:3128 --proxy-user tenant-b:demo-b \
  https://TLS_ORIGIN:PORT/
```

Use `--proxy-header`, not an origin header, for CONNECT selection. Authentication
and selection run on each ordinary request or CONNECT handshake, not on inner
encrypted requests. Once established, a tunnel keeps its original exit.

The filter order is Lua authentication, fail-closed metadata RBAC, DFP, router.
Lua strips `Proxy-Authorization`, `Proxy-Connection`, all `X-Datum-*`, and all
`X-Envoy-*` request headers before publishing an authorized profile in per-stream
dynamic metadata. Origin `Authorization` is preserved. In particular,
`X-Datum-Upstream-Cluster` is neither trusted nor generated. Removing `X-Envoy-*`
also prevents client retry/timeout/internal-routing overrides. Envoy may still
generate its own upstream transport headers, such as
`x-envoy-expected-rq-timeout-ms`; these are not client selection headers.
RBAC requires the
successful authorization metadata before DFP can resolve a target, including if
Lua fails before publishing it. Only the Lua filter writes this metadata.

Routes match trusted metadata to two explicitly named DFP clusters. Each cluster
owns its upstream pool and source binding; only the DNS cache is shared. There
is no header-selected cluster, fallback exit, unbound cluster, retry, hedge, or
mirror. CONNECT routes use `connect_matcher` and `connect_config: {}`. The HCM
CONNECT upgrade reuses the complete authentication/filter chain. The wildcard
virtual-host domain accepts destination names; it is not a wildcard cluster.

## Security boundaries

- **Do not expose this demo to untrusted networks.** Its fixed credentials are
  public and Basic authentication to the proxy is plaintext. Use an isolated
  fixture network. There is no production identity lifecycle or rate limiting.
- Authentication authorizes an exit, **not a destination**. No SSRF, private-IP,
  metadata-service, DNS-rebinding, or destination-port policy is implemented.
  Authorized clients can reach any reachable destination, potentially including
  the loopback admin endpoint. Production use requires destination restrictions
  and network isolation, not just loopback admin binding.
- CONNECT carries arbitrary TCP, not verified TLS. It can transport arbitrary
  inner protocol messages. Envoy cannot strip credentials or selection headers
  inside encrypted tunnel payloads, and does not authenticate inner requests.
- Pools are isolated **by profile**, not by tenant. Tenants authorized for the
  same exit can share that exit's pool. DNS uses one shared IPv4-only cache;
  resolver traffic is not bound to a selected exit. This is not a privacy or
  network-namespace isolation boundary.
- No request/access logger is configured; Lua does not log credentials. Keep
  warning-level application logging. Do not enable debug/trace header logging,
  taps, or header-bearing access logs around Basic credentials.
- A mounted bootstrap or Lua file is trusted executable configuration. Do not
  let clients modify these files, admin, filter metadata, or route configuration.
- No live address withdrawal/revocation mechanism is implemented. Existing
  pooled connections/tunnels may survive address or policy changes. Use a fresh
  process when testing failure of an unassigned bind address.

## Focused tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s prototype/forward-proxy/tests/unit -p 'test_*.py' -v
lua prototype/forward-proxy/tests/unit/test_auth.lua \
  prototype/forward-proxy/runtime/auth.lua
```

The Python suite checks the renderer, image contract, cluster bindings, routing,
filter order, and fail-closed guard. The Lua suite executes the actual auth file
against a small handle mock; it does not replace stock Envoy validation or e2e
tests. Without a local Lua executable, run it in an ephemeral test container:

```sh
tar -C prototype/forward-proxy -cf - runtime/auth.lua tests/unit/test_auth.lua | \
  docker --context colima run --rm -i alpine:3.22 sh -c \
  'mkdir /work; tar -xf - -C /work; apk add --no-cache lua5.1 >/dev/null; lua5.1 /work/tests/unit/test_auth.lua /work/runtime/auth.lua'
```

The Alpine image/package is test-only, not part of the candidate runtime.
Real TLS passthrough/certificate identity, origin-observed source addresses,
pool reuse under profile switching, header stripping, and missing-bind failures
are validated by the separate E2E harness. Denied-request DNS side effects are
not separately instrumented by that suite.

### Validation record (2026-11-19)

- The root-context Docker build above succeeded using `--context colima`.
- `--version` reported
  `b2d89e0ae53916f28770771fbdd49c5c99c5c5a2/1.40.2/Clean/RELEASE/BoringSSL`.
- `--mode validate --log-level warning` exited 0 with
  `configuration '/etc/envoy/envoy.json' OK`, without deprecation warnings.
- The Python command above passed all 9 tests; the streamed Alpine/Lua command
  passed all 47 authentication cases.
- An additional ephemeral stock-Envoy smoke harness used `docker create`,
  `docker cp`, and `docker exec` with a Python HTTP origin in the same network
  namespace and rendered sources `127.0.0.11`/`127.0.0.12`. All 10 cases passed:
  missing/bad credentials (407), unauthorized/unknown profiles (403), tenant-a
  default/explicit-b, tenant-b with forged internal selector, tenant-a again on
  the same downstream keepalive connection, and both tenants' raw CONNECT
  tunnels. The origin observed the expected source and no proxy credentials,
  selection headers, or client retry header. All fixture containers were removed.
  This was a raw-tunnel smoke test, not the independent TLS certificate e2e test.

## Primary versioned references

Verified against Envoy **v1.40.2**, not `latest`:

- [DFP filter and shared DNS cache](https://www.envoyproxy.io/docs/envoy/v1.40.2/configuration/http/http_filters/dynamic_forward_proxy_filter)
- [CONNECT termination and upgrade filter chains](https://www.envoyproxy.io/docs/envoy/v1.40.2/intro/arch_overview/http/upgrades)
- [Source binding and freebind](https://www.envoyproxy.io/docs/envoy/v1.40.2/api-v3/config/core/v3/address.proto)
- [Absolute-form HTTP protocol option](https://www.envoyproxy.io/docs/envoy/v1.40.2/api-v3/config/core/v3/protocol.proto)
- [Lua headers, dynamic metadata, and route cache](https://www.envoyproxy.io/docs/envoy/v1.40.2/configuration/http/http_filters/lua_filter)
- [Route metadata matchers and CONNECT route action](https://www.envoyproxy.io/docs/envoy/v1.40.2/api-v3/config/route/v3/route_components.proto)
- [RBAC dynamic sourced metadata](https://www.envoyproxy.io/docs/envoy/v1.40.2/api-v3/config/rbac/v3/rbac.proto)
