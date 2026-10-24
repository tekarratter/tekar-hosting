FROM caddy:2.11-builder-alpine AS builder
# cloudflare: DNS-01 (wildcard) | caddy-l4: stream TCP/UDP như NPM
# Ghim phiên bản để build lặp lại được (CI); nâng cấp: đổi tag ở đây
RUN xcaddy build v2.11.7 \
    --with github.com/caddy-dns/cloudflare@v0.2.4 \
    --with github.com/mholt/caddy-l4@v0.1.2

FROM caddy:2.11-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
