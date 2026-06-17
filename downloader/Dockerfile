# Pinned by digest to the alpine:latest build this image was last built on.
FROM alpine@sha256:5b10f432ef3da1b8d4c7eb6c487f2f5a8f096bc91145e68878dd4a5019afde11
RUN apk add --no-cache wget ca-certificates
COPY download.sh /download.sh
RUN chmod +x /download.sh
ENTRYPOINT ["/download.sh"]
