FROM envoyproxy/envoy@sha256:8de97e654644827d62afdbf7e1d3dc9fee01a8690d410dd1c63a30dc6b6cecbb

COPY prototype/forward-proxy/runtime/auth.lua /etc/envoy/auth.lua
COPY prototype/forward-proxy/runtime/envoy.json /etc/envoy/envoy.json

EXPOSE 3128
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/envoy"]
CMD ["-c", "/etc/envoy/envoy.json", "--log-level", "warning", "--concurrency", "2"]
