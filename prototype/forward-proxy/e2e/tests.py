import argparse
import base64
import concurrent.futures
import http.client
import json
import pathlib
import socket
import ssl
import sys
import time


parser = argparse.ArgumentParser()
for option in ("proxy", "origin", "exit-a", "exit-b", "phase"):
    parser.add_argument("--" + option, required=True)
args = parser.parse_args()
results = []


def auth(tenant="a"):
    return "Basic " + base64.b64encode(f"tenant-{tenant}:demo-{tenant}".encode()).decode()


def headers(tenant="a", profile=None):
    values = [("Proxy-Authorization", auth(tenant))]
    if profile:
        values.append(("X-Datum-Egress-Profile", profile))
    return values


def connection():
    return http.client.HTTPConnection(args.proxy, 3128, timeout=8)


def request(client, values, path="/echo"):
    client.putrequest("GET", f"http://{args.origin}:8080{path}")
    for name, value in values:
        client.putheader(name, value)
    client.endheaders()
    response = client.getresponse()
    payload = response.read()
    return response.status, json.loads(payload) if response.status == 200 else None


def observed(payload, expected):
    if payload["peer"] != expected:
        print(json.dumps({"event": "peer_mismatch", "expected_ip": expected, "observed_ip": payload["peer"]}), file=sys.stderr, flush=True)
    assert payload["peer"] == expected
    names = {name.lower() for name, value in payload["headers"]}
    assert not names.intersection({"proxy-authorization", "x-datum-egress-profile", "x-datum-upstream-cluster"})


def once(values, expected=None, statuses=(200,)):
    client = connection()
    try:
        status, payload = request(client, values)
        assert status in statuses
        if expected:
            observed(payload, expected)
        return payload
    finally:
        client.close()


def tunnel(tenant="a", profile=None, extra=(), values=None):
    stream = socket.create_connection((args.proxy, 3128), timeout=8)
    try:
        authority = f"{args.origin}:8443"
        values = headers(tenant, profile) + list(extra) if values is None else values
        wire = f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n" + "".join(f"{name}: {value}\r\n" for name, value in values) + "\r\n"
        stream.sendall(wire.encode())
        response = bytearray()
        while not response.endswith(b"\r\n\r\n"):
            chunk = stream.recv(1)
            if not chunk:
                raise ConnectionError("CONNECT closed")
            response.extend(chunk)
            assert len(response) < 16384
        status = int(response.split(b" ", 2)[1])
        if status != 200:
            stream.close()
            return status, None
        context = ssl.create_default_context(cafile="cert.pem")
        secured = context.wrap_socket(stream, server_hostname="origin.e2e")
        client = http.client.HTTPConnection("origin.e2e", 8443, timeout=8)
        client.sock = secured
        return status, client
    except BaseException:
        stream.close()
        raise


def tunnel_request(client, expected):
    client.request("GET", "/tls", headers={"Host": "origin.e2e"})
    response = client.getresponse()
    assert response.status == 200
    payload = json.loads(response.read())
    observed(payload, expected)
    return payload


def tls_once(tenant, profile, expected, extra=()):
    status, client = tunnel(tenant, profile, extra)
    assert status == 200
    try:
        first = tunnel_request(client, expected)
        second = tunnel_request(client, expected)
        assert first["connection"] == second["connection"]
        return first["connection"]
    finally:
        client.close()


def check(name, action):
    try:
        action()
        result = "passed"
    except AssertionError:
        result = "failed"
    except Exception:
        result = "error"
    item = {"name": f"{args.phase}.{name}", "status": result}
    results.append(item)
    print(json.dumps(item), flush=True)


def denied_connect(values, statuses):
    status, client = tunnel(values=values)
    if client:
        client.close()
    assert status in statuses


def alternation():
    client = connection()
    seen = {}
    downstream = None
    try:
        for tenant, profile, expected in [
            ("a", None, args.exit_a), ("a", "exit-b", args.exit_b),
            ("b", None, args.exit_b), ("a", "exit-a", args.exit_a),
            ("b", "exit-b", args.exit_b), ("a", None, args.exit_a),
        ] * 3:
            status, payload = request(client, headers(tenant, profile))
            assert status == 200
            observed(payload, expected)
            if downstream is None:
                downstream = client.sock
            assert client.sock is downstream
            identity = payload["connection"]
            if expected in seen:
                assert identity == seen[expected]
            seen[expected] = identity
        assert seen[args.exit_a] != seen[args.exit_b]
    finally:
        client.close()


def denied_then_allowed():
    client = connection()
    try:
        status, _ = request(client, headers("b", "exit-a"))
        assert status == 403
        status, payload = request(client, headers("b"))
        assert status == 200
        observed(payload, args.exit_b)
    finally:
        client.close()


def concurrent_requests():
    def worker(index):
        tenant = "b" if index % 2 else "a"
        expected = args.exit_b if tenant == "b" else args.exit_a
        once(headers(tenant), expected)
        return tls_once(tenant, None, expected)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(worker, range(16)))
    assert len(set(identities)) == len(identities)


def reused_authorization():
    client = connection()
    try:
        status, payload = request(client, headers("a"))
        assert status == 200
        observed(payload, args.exit_a)
        downstream = client.sock
        for values, expected_status in (
            ([], 407), (headers("b", "exit-a"), 403),
            (headers("a") + headers("b"), 407),
        ):
            status, _ = request(client, values)
            assert status == expected_status
            assert client.sock is downstream
        status, payload = request(client, headers("b"))
        assert status == 200
        observed(payload, args.exit_b)
        assert client.sock is downstream
    finally:
        client.close()


def unavailable_http(client=None):
    owned = client is None
    client = client or connection()
    try:
        try:
            status, _ = request(client, headers("a"))
            assert status in (502, 503, 504)
        except (OSError, http.client.HTTPException):
            pass
    finally:
        if owned:
            client.close()


def unavailable_tls(client=None):
    try:
        if client is None:
            status, client = tunnel()
            if status != 200:
                assert status in (502, 503, 504)
                return
        tunnel_request(client, args.exit_a)
    except (OSError, http.client.HTTPException):
        return
    finally:
        if client:
            client.close()
    raise AssertionError("Unavailable exit unexpectedly succeeded")


def readiness():
    deadline = time.monotonic() + 30
    while True:
        try:
            once(headers(), args.exit_a)
            return
        except (OSError, http.client.HTTPException, AssertionError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


if args.phase == "baseline":
    check("readiness", readiness)
    check("tenant_a_default_http", lambda: once(headers(), args.exit_a))
    check("tenant_a_exit_b_http", lambda: once(headers("a", "exit-b"), args.exit_b))
    check("tenant_b_default_http", lambda: once(headers("b"), args.exit_b))
    check("tenant_a_default_tls", lambda: tls_once("a", None, args.exit_a))
    check("tenant_a_exit_b_tls", lambda: tls_once("a", "exit-b", args.exit_b))
    check("tenant_b_default_tls", lambda: tls_once("b", None, args.exit_b))
    check("http_connection_reuse", alternation)
    check("reused_connection_authorization", reused_authorization)
    check("concurrent_requests_tunnels", concurrent_requests)
    check("missing_auth", lambda: once([], statuses=(407,)))
    check("wrong_auth", lambda: once([("Proxy-Authorization", "Basic aW52YWxpZDppbnZhbGlk")], statuses=(407,)))
    check("forbidden_profile", lambda: once(headers("b", "exit-a"), statuses=(403,)))
    check("unknown_profile", lambda: once(headers("a", "unknown"), statuses=(403,)))
    check("denied_then_allowed", denied_then_allowed)
    check("spoofed_cluster_http", lambda: once(headers("b") + [("X-Datum-Upstream-Cluster", "exit-a")], args.exit_b))
    check("spoofed_cluster_tls", lambda: tls_once("b", None, args.exit_b, [("X-Datum-Upstream-Cluster", "exit-a")]))
    check("duplicate_auth", lambda: once(headers("a") + headers("b"), statuses=(400, 407)))
    check("duplicate_auth_reversed", lambda: once(headers("b") + headers("a"), statuses=(400, 407)))
    check("duplicate_profile", lambda: once(headers("a", "exit-a") + [("x-datum-egress-profile", "exit-b")], statuses=(400, 403)))
    check("duplicate_forbidden_profile", lambda: once(headers("b", "exit-b") + [("X-Datum-Egress-Profile", "exit-a")], statuses=(400, 403)))
    check("spoofed_tenant", lambda: once(headers("b", "exit-a") + [("X-Datum-Tenant", "tenant-a")], statuses=(403,)))
    check("missing_auth_connect", lambda: denied_connect([], (407,)))
    check("wrong_auth_connect", lambda: denied_connect([("Proxy-Authorization", "Basic aW52YWxpZDppbnZhbGlk")], (407,)))
    check("forbidden_profile_connect", lambda: denied_connect(headers("b", "exit-a"), (403,)))
    check("duplicate_auth_connect", lambda: denied_connect(headers("a") + headers("b"), (400, 407)))
    check("duplicate_profile_connect", lambda: denied_connect(headers("b", "exit-b") + [("x-datum-egress-profile", "exit-a")], (400, 403)))
elif args.phase == "reject":
    warm_http = connection()
    warm_a = None
    warm_b = None

    def warm():
        global warm_a, warm_b
        status, payload = request(warm_http, headers())
        assert status == 200
        observed(payload, args.exit_a)
        _, warm_a = tunnel("a")
        _, warm_b = tunnel("b")
        tunnel_request(warm_a, args.exit_a)
        tunnel_request(warm_b, args.exit_b)
        pathlib.Path("/tmp/warm.ready").touch()
        deadline = time.monotonic() + 60
        while not pathlib.Path("/tmp/fault.ready").exists():
            if time.monotonic() > deadline:
                raise TimeoutError()
            time.sleep(0.1)

    check("warm_connections", warm)
    check("warmed_http_no_fallback", lambda: unavailable_http(warm_http))
    check("warmed_tls_no_fallback", lambda: unavailable_tls(warm_a))
    check("warmed_other_exit_healthy", lambda: tunnel_request(warm_b, args.exit_b))
    check("fresh_http_no_fallback", unavailable_http)
    check("fresh_tls_no_fallback", unavailable_tls)
    check("other_exit_http", lambda: once(headers("b"), args.exit_b))
    check("other_exit_tls", lambda: tls_once("b", None, args.exit_b))
    warm_http.close()
    if warm_b:
        warm_b.close()
elif args.phase == "missing":
    check("fresh_http_no_fallback", unavailable_http)
    check("fresh_tls_no_fallback", unavailable_tls)
    check("other_exit_http", lambda: once(headers("b"), args.exit_b))
    check("other_exit_tls", lambda: tls_once("b", None, args.exit_b))
else:
    raise ValueError("Unknown phase")

raise SystemExit(0 if results and all(item["status"] == "passed" for item in results) else 1)
