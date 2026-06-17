# Pinned to the exact node 22 patch this image was last built on.
FROM node:22.22.3-bookworm-slim

WORKDIR /playwright-mcp

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned to @playwright/mcp 0.0.75. The chromium build pulled on the next line
# follows from this package's playwright-core dependency.
RUN npm install @playwright/mcp@0.0.75
RUN ./node_modules/.bin/playwright install --with-deps chromium

EXPOSE 3000

CMD ["npx", "@playwright/mcp@0.0.75", \
     "--port", "3000", \
     "--host", "0.0.0.0", \
     "--allowed-hosts", "*", \
     "--no-sandbox", \
     "--headless", \
     "--isolated", \
     "--browser", "chromium", \
     "--user-agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36", \
     "--proxy-server", "http://haproxy:8118", \
     "--proxy-bypass", "searxng,llama,haproxy,playwright,localhost,127.0.0.1"]
