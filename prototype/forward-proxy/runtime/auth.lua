local credentials = {
  ["dGVuYW50LWE6ZGVtby1h"] = {
    default = "exit-a",
    profiles = { ["exit-a"] = true, ["exit-b"] = true }
  },
  ["dGVuYW50LWI6ZGVtby1i"] = {
    default = "exit-b",
    profiles = { ["exit-b"] = true }
  }
}

function envoy_on_request(handle)
  local headers = handle:headers()
  local authorization = headers:get("proxy-authorization")
  local authorization_count = headers:getNumValues("proxy-authorization")
  local profile = headers:get("x-datum-egress-profile")
  local profile_count = headers:getNumValues("x-datum-egress-profile")
  local remove = {}
  for name, value in pairs(headers) do
    if string.sub(name, 1, 8) == "x-datum-" or string.sub(name, 1, 8) == "x-envoy-" then
      table.insert(remove, name)
    end
  end
  for _, name in ipairs(remove) do
    headers:remove(name)
  end
  headers:remove("proxy-authorization")
  headers:remove("proxy-connection")

  local scheme, token
  if authorization_count == 1 and authorization then
    scheme, token = string.match(authorization, "^(%S+)[ \t]+(%S+)$")
  end
  local tenant = scheme and string.lower(scheme) == "basic" and credentials[token]
  if not tenant then
    handle:respond({
      [":status"] = "407",
      ["proxy-authenticate"] = 'Basic realm="datum-forward-proxy"',
      ["content-type"] = "text/plain"
    }, "Proxy authentication required\n")
    return
  end

  if profile_count == 0 then
    profile = tenant.default
  end
  if profile_count > 1 or not tenant.profiles[profile] then
    handle:respond({ [":status"] = "403", ["content-type"] = "text/plain" },
      "Egress profile forbidden\n")
    return
  end
  if headers:get(":method") ~= "CONNECT" and headers:get(":scheme") ~= "http" then
    handle:respond({ [":status"] = "400", ["content-type"] = "text/plain" },
      "Use CONNECT for HTTPS\n")
    return
  end

  handle:clearRouteCache()
  handle:streamInfo():dynamicMetadata():set("datum.auth", "profile", profile)
end
