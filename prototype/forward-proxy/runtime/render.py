#!/usr/bin/env python3
"""Render the IPv4-only, stock Envoy forward-proxy demonstration bootstrap."""

import argparse
import ipaddress
import json
from pathlib import Path


TYPE_PREFIX = "type.googleapis.com/envoy."
AUTH_FILE = "/etc/envoy/auth.lua"
PROFILES = ("exit-a", "exit-b")


def source_ip(value):
    address = ipaddress.IPv4Address(value)
    if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
        raise ValueError("source address must be a specific unicast IPv4 address")
    return str(address)


def profile_match(profile):
    return {
        "filter": "datum.auth",
        "path": [{"key": "profile"}],
        "value": {"string_match": {"exact": profile}},
    }


def render(exit_a="198.18.0.11", exit_b="198.18.0.12"):
    sources = [source_ip(exit_a), source_ip(exit_b)]
    if sources[0] == sources[1]:
        raise ValueError("exit-a and exit-b require distinct source addresses")
    dns_cache = {
        "name": "forward_proxy_dns",
        "dns_lookup_family": "V4_ONLY",
        "max_hosts": 1024,
        "dns_cache_circuit_breaker": {"max_pending_requests": 128},
    }
    routes = []
    for profile in PROFILES:
        for connect in (True, False):
            match = {"connect_matcher": {}} if connect else {"prefix": "/"}
            match["dynamic_metadata"] = [profile_match(profile)]
            action = {
                "cluster": profile,
                "timeout": "0s" if connect else "30s",
                "retry_policy": {"num_retries": 0},
            }
            if connect:
                action["upgrade_configs"] = [
                    {"upgrade_type": "CONNECT", "connect_config": {}}
                ]
            routes.append({"match": match, "route": action})
    for match in ({"connect_matcher": {}}, {"prefix": "/"}):
        routes.append({"match": match, "direct_response": {"status": 403}})
    manager = {
        "@type": TYPE_PREFIX + "extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
        "stat_prefix": "forward_proxy",
        "codec_type": "HTTP1",
        "http_protocol_options": {"allow_absolute_url": True},
        "stream_idle_timeout": "300s",
        "upgrade_configs": [{"upgrade_type": "CONNECT"}],
        "route_config": {
            "name": "authenticated_profiles",
            "request_headers_to_remove": [
                "proxy-authorization",
                "proxy-connection",
                "x-datum-egress-profile",
                "x-datum-upstream-cluster",
            ],
            "virtual_hosts": [{"name": "destinations", "domains": ["*"], "routes": routes}],
        },
        "http_filters": [
            {
                "name": "envoy.filters.http.lua",
                "typed_config": {
                    "@type": TYPE_PREFIX + "extensions.filters.http.lua.v3.Lua",
                    "default_source_code": {"filename": AUTH_FILE},
                },
            },
            {
                "name": "envoy.filters.http.rbac",
                "typed_config": {
                    "@type": TYPE_PREFIX + "extensions.filters.http.rbac.v3.RBAC",
                    "rules": {
                        "action": "ALLOW",
                        "policies": {
                            profile: {
                                "permissions": [{"sourced_metadata": {
                                    "metadata_matcher": profile_match(profile),
                                    "metadata_source": "DYNAMIC",
                                }}],
                                "principals": [{"any": True}],
                            }
                            for profile in PROFILES
                        },
                    },
                },
            },
            {
                "name": "envoy.filters.http.dynamic_forward_proxy",
                "typed_config": {
                    "@type": TYPE_PREFIX + "extensions.filters.http.dynamic_forward_proxy.v3.FilterConfig",
                    "dns_cache_config": dns_cache,
                },
            },
            {
                "name": "envoy.filters.http.router",
                "typed_config": {"@type": TYPE_PREFIX + "extensions.filters.http.router.v3.Router"},
            },
        ],
    }
    clusters = []
    for profile, source in zip(PROFILES, sources):
        clusters.append({
            "name": profile,
            "connect_timeout": "5s",
            "lb_policy": "CLUSTER_PROVIDED",
            "cluster_type": {
                "name": "envoy.clusters.dynamic_forward_proxy",
                "typed_config": {
                    "@type": TYPE_PREFIX + "extensions.clusters.dynamic_forward_proxy.v3.ClusterConfig",
                    "dns_cache_config": dns_cache,
                    "allow_coalesced_connections": False,
                },
            },
            "upstream_bind_config": {
                "source_address": {"address": source, "port_value": 0},
                "freebind": False,
            },
            "circuit_breakers": {"thresholds": [{"max_retries": 0}]},
        })
    return {
        "admin": {"address": {"socket_address": {"address": "127.0.0.1", "port_value": 9901}}},
        "static_resources": {
            "listeners": [{
                "name": "forward_proxy",
                "address": {"socket_address": {"address": "0.0.0.0", "port_value": 3128}},
                "filter_chains": [{"filters": [{
                    "name": "envoy.filters.network.http_connection_manager",
                    "typed_config": manager,
                }]}],
            }],
            "clusters": clusters,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exit-a", default="198.18.0.11")
    parser.add_argument("--exit-b", default="198.18.0.12")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        config = render(args.exit_a, args.exit_b)
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
