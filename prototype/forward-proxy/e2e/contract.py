PHASE_TESTS = {
    "baseline": (
        "readiness", "tenant_a_default_http", "tenant_a_exit_b_http", "tenant_b_default_http",
        "tenant_a_default_tls", "tenant_a_exit_b_tls", "tenant_b_default_tls",
        "http_connection_reuse", "reused_connection_authorization", "concurrent_requests_tunnels", "missing_auth", "wrong_auth",
        "forbidden_profile", "unknown_profile", "denied_then_allowed", "spoofed_cluster_http",
        "spoofed_cluster_tls", "duplicate_auth", "duplicate_auth_reversed", "duplicate_profile",
        "duplicate_forbidden_profile", "spoofed_tenant", "missing_auth_connect",
        "wrong_auth_connect", "forbidden_profile_connect", "duplicate_auth_connect",
        "duplicate_profile_connect",
    ),
    "reject": (
        "warm_connections", "warmed_http_no_fallback", "warmed_tls_no_fallback",
        "warmed_other_exit_healthy", "fresh_http_no_fallback", "fresh_tls_no_fallback",
        "other_exit_http", "other_exit_tls",
    ),
    "missing": (
        "fresh_http_no_fallback", "fresh_tls_no_fallback", "other_exit_http", "other_exit_tls",
    ),
}
