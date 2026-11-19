import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
SPEC = importlib.util.spec_from_file_location("render", RUNTIME / "render.py")
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.config = renderer.render()
        self.resources = self.config["static_resources"]
        self.listener = self.resources["listeners"][0]
        self.manager = self.listener["filter_chains"][0]["filters"][0]["typed_config"]

    def test_checked_in_bootstrap_matches_renderer(self):
        self.assertEqual(json.loads((RUNTIME / "envoy.json").read_text()), self.config)

    def test_listener_and_loopback_admin(self):
        self.assertEqual(self.listener["address"]["socket_address"],
                         {"address": "0.0.0.0", "port_value": 3128})
        self.assertEqual(self.config["admin"]["address"]["socket_address"],
                         {"address": "127.0.0.1", "port_value": 9901})
        self.assertTrue(self.manager["http_protocol_options"]["allow_absolute_url"])
        self.assertEqual(self.manager["codec_type"], "HTTP1")

    def test_auth_and_fail_closed_guard_precede_dns_and_router(self):
        filters = self.manager["http_filters"]
        self.assertEqual([entry["name"] for entry in filters], [
            "envoy.filters.http.lua", "envoy.filters.http.rbac",
            "envoy.filters.http.dynamic_forward_proxy", "envoy.filters.http.router",
        ])
        self.assertEqual(filters[0]["typed_config"]["default_source_code"],
                         {"filename": "/etc/envoy/auth.lua"})
        rules = filters[1]["typed_config"]["rules"]
        self.assertEqual(rules["action"], "ALLOW")
        self.assertEqual(set(rules["policies"]), {"exit-a", "exit-b"})
        for profile, policy in rules["policies"].items():
            self.assertEqual(policy["permissions"], [{"sourced_metadata": {
                "metadata_source": "DYNAMIC",
                "metadata_matcher": renderer.profile_match(profile),
            }}])

    def test_two_bound_clusters_share_only_dns(self):
        clusters = self.resources["clusters"]
        self.assertEqual([cluster["name"] for cluster in clusters], ["exit-a", "exit-b"])
        dns_cache = self.manager["http_filters"][2]["typed_config"]["dns_cache_config"]
        self.assertEqual(dns_cache["dns_lookup_family"], "V4_ONLY")
        for cluster, address in zip(clusters, ["198.18.0.11", "198.18.0.12"]):
            self.assertEqual(cluster["upstream_bind_config"], {
                "source_address": {"address": address, "port_value": 0}, "freebind": False,
            })
            self.assertEqual(cluster["cluster_type"]["name"], "envoy.clusters.dynamic_forward_proxy")
            typed = cluster["cluster_type"]["typed_config"]
            self.assertEqual(typed["dns_cache_config"], dns_cache)
            self.assertFalse(typed["allow_coalesced_connections"])
            self.assertEqual(cluster["circuit_breakers"]["thresholds"], [{"max_retries": 0}])
            self.assertNotIn("transport_socket", cluster)

    def test_routes_require_metadata_and_terminate_connect(self):
        routes = self.manager["route_config"]["virtual_hosts"][0]["routes"]
        self.assertEqual(len(routes), 6)
        for route in routes[:4]:
            action = route["route"]
            self.assertIn(action["cluster"], ("exit-a", "exit-b"))
            self.assertEqual(route["match"]["dynamic_metadata"],
                             [renderer.profile_match(action["cluster"])])
            self.assertEqual(action["retry_policy"], {"num_retries": 0})
            if "connect_matcher" in route["match"]:
                self.assertEqual(action["upgrade_configs"],
                                 [{"upgrade_type": "CONNECT", "connect_config": {}}])
                self.assertEqual(action["timeout"], "0s")
        self.assertEqual(routes[4:], [
            {"match": {"connect_matcher": {}}, "direct_response": {"status": 403}},
            {"match": {"prefix": "/"}, "direct_response": {"status": 403}},
        ])
        serialized = json.dumps(self.config)
        for forbidden in ("cluster_header", "weighted_clusters", "request_mirror_policies",
                          "retry_host_predicate", "hedge_policy", "transport_socket"):
            self.assertNotIn('"' + forbidden + '"', serialized)

    def test_no_header_or_token_logging(self):
        self.assertNotIn("access_log", self.manager)
        self.assertNotIn("logInfo", (RUNTIME / "auth.lua").read_text())
        self.assertNotIn("logDebug", (RUNTIME / "auth.lua").read_text())
        self.assertTrue({"proxy-authorization", "x-datum-egress-profile",
                         "x-datum-upstream-cluster"}.issubset(
                             self.manager["route_config"]["request_headers_to_remove"]))

    def test_renderer_cli_and_custom_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "envoy.json"
            result = subprocess.run([
                sys.executable, str(RUNTIME / "render.py"),
                "--exit-a", "192.0.2.11", "--exit-b", "192.0.2.12", "--output", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text()), renderer.render("192.0.2.11", "192.0.2.12"))

    def test_invalid_source_addresses_rejected(self):
        for value in ("hostname", "::1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "1.2.3.999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                renderer.render(value)
        with self.assertRaises(ValueError):
            renderer.render("192.0.2.1", "192.0.2.1")

    def test_docker_contract(self):
        dockerfile = (RUNTIME / "Dockerfile").read_text()
        self.assertIn("FROM envoyproxy/envoy@sha256:8de97e654644827d62afdbf7e1d3dc9fee01a8690d410dd1c63a30dc6b6cecbb", dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/local/bin/envoy"]', dockerfile)
        self.assertIn('"/etc/envoy/envoy.json"', dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)


if __name__ == "__main__":
    unittest.main()
