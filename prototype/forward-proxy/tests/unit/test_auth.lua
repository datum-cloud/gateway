dofile(arg[1] or "prototype/forward-proxy/runtime/auth.lua")

local methods = {}
local counts = setmetatable({}, { __mode = "k" })
function methods:get(name)
  return rawget(self, name)
end
function methods:getNumValues(name)
  return counts[self][name] or (rawget(self, name) and 1 or 0)
end
function methods:remove(name)
  self[name] = nil
end

local passed = 0
local function check(name, supplied, expected_status, expected_profile, duplicates)
  local headers = { [":method"] = "GET", [":scheme"] = "http" }
  for key, value in pairs(supplied) do
    headers[string.lower(key)] = value
  end
  setmetatable(headers, { __index = methods })
  counts[headers] = duplicates or {}
  local state = {}
  local metadata = {}
  function metadata:set(namespace, key, value)
    assert(namespace == "datum.auth" and key == "profile", name)
    state.profile = value
  end
  local handle = {}
  function handle:headers() return headers end
  function handle:respond(response, body)
    state.status = tonumber(response[":status"])
    state.challenge = response["proxy-authenticate"]
    assert(not string.find(body, "dGVuYW50", 1, true), name)
  end
  function handle:clearRouteCache() state.cleared = true end
  function handle:streamInfo()
    return { dynamicMetadata = function() return metadata end }
  end
  envoy_on_request(handle)
  assert(state.status == expected_status, name .. ": unexpected status")
  assert(state.profile == expected_profile, name .. ": unexpected profile")
  assert(headers:get("proxy-authorization") == nil, name)
  assert(headers:get("proxy-connection") == nil, name)
  for key, value in pairs(headers) do
    assert(string.sub(key, 1, 8) ~= "x-datum-", name)
    assert(string.sub(key, 1, 8) ~= "x-envoy-", name)
  end
  if expected_status == 407 then
    assert(state.challenge == 'Basic realm="datum-forward-proxy"', name)
  end
  if expected_profile then assert(state.cleared, name) end
  assert(headers:get("authorization") == supplied.authorization, name)
  passed = passed + 1
end

local auth_a = "Basic dGVuYW50LWE6ZGVtby1h"
local auth_b = "Basic dGVuYW50LWI6ZGVtby1i"
for _, method in ipairs({ "GET", "CONNECT" }) do
  check(method .. " missing auth", { [":method"] = method }, 407)
  check(method .. " bad auth", { [":method"] = method, ["proxy-authorization"] = "Basic bad" }, 407)
  check(method .. " spoof without auth", {
    [":method"] = method, ["x-datum-upstream-cluster"] = "exit-a",
    ["x-datum-egress-profile"] = "exit-a", ["x-envoy-original-dst-host"] = "127.0.0.1:9901"
  }, 407)
  for _, credential in ipairs({ auth_a, auth_b }) do
    local default = credential == auth_a and "exit-a" or "exit-b"
    check(method .. " default", { [":method"] = method, ["proxy-authorization"] = credential }, nil, default)
    check(method .. " explicit b", {
      [":method"] = method, ["proxy-authorization"] = credential, ["x-datum-egress-profile"] = "exit-b"
    }, nil, "exit-b")
    check(method .. " explicit a", {
      [":method"] = method, ["proxy-authorization"] = credential, ["x-datum-egress-profile"] = "exit-a"
    }, credential == auth_b and 403 or nil, credential == auth_a and "exit-a" or nil)
    for _, profile in ipairs({ "unknown", "", "exit-a,exit-b", "EXIT-B" }) do
      check(method .. " invalid profile", {
        [":method"] = method, ["proxy-authorization"] = credential, ["x-datum-egress-profile"] = profile
      }, 403)
    end
    check(method .. " internal spoof ignored", {
      [":method"] = method, ["proxy-authorization"] = credential,
      ["x-datum-upstream-cluster"] = "exit-a", ["x-datum-internal-profile"] = "exit-a",
      ["x-envoy-retry-on"] = "5xx", ["x-envoy-max-retries"] = "10",
      ["proxy-connection"] = "keep-alive", authorization = "Bearer origin-only"
    }, nil, default)
  end
end
for _, authorization in ipairs({ "", "Bearer token", auth_a .. "," .. auth_b, auth_a .. " junk" }) do
  check("malformed auth", { ["proxy-authorization"] = authorization }, 407)
end
check("scheme case", { ["proxy-authorization"] = "bAsIc\tdGVuYW50LWE6ZGVtby1h" }, nil, "exit-a")
check("duplicate auth", { ["proxy-authorization"] = auth_a }, 407, nil, { ["proxy-authorization"] = 2 })
check("duplicate profile", {
  ["proxy-authorization"] = auth_a, ["x-datum-egress-profile"] = "exit-a"
}, 403, nil, { ["x-datum-egress-profile"] = 2 })
check("auth before profile", { ["proxy-authorization"] = "bad", ["x-datum-egress-profile"] = "unknown" }, 407)
check("https absolute URI is not plaintext", { ["proxy-authorization"] = auth_a, [":scheme"] = "https" }, 400)
print(string.format("%d Lua authentication cases passed", passed))
