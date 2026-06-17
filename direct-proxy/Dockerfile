# Minimal HTTP/HTTPS forward proxy for the direct-exit pool: the domains listed
# in DIRECT_DOMAINS bypass Tor and exit straight to the internet through here.
# Built from alpine + tinyproxy rather than a third-party image so the egress
# path is reproducible and from a known base (same pin as downloader/Dockerfile).
FROM alpine@sha256:5b10f432ef3da1b8d4c7eb6c487f2f5a8f096bc91145e68878dd4a5019afde11
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
EXPOSE 8118
ENTRYPOINT ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
