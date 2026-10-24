# Tekar Hosting

Panel quản lý hosting trên Docker: ứng dụng compose, dịch vụ nền tảng dùng chung, reverse proxy tự động,
Cloudflare Tunnel + wildcard, backup/restore, quét bảo mật, giám sát tài nguyên, phân quyền, web terminal
và trợ lý Claude.

## Kiến trúc

```
Internet ─► Cloudflare edge (TLS, WAF) ─► th-cloudflared ─► th-caddy:80 ─► container app (mạng th-edge)
                                                      ▲
                     th-api (FastAPI + SPA) ──────────┤ Admin API (unix socket, chỉ th-api/th-worker)
                     th-worker (job queue, cron, metrics, health check, cảnh báo)
                     th-db (Postgres riêng của panel)
                     th-claude (tuỳ chọn: Claude Code console, nối MCP về th-api)
```

| Thành phần | Vai trò |
|---|---|
| `th-api` | REST + WebSocket (terminal, logs, chat Claude), MCP server tại `/mcp`, phục vụ giao diện web |
| `th-worker` | Chạy job (deploy, backup, restore, scan…), lịch cron, thu thập CPU/RAM (đọc cgroup v2), health check domain, cảnh báo Telegram/webhook |
| `th-caddy` | Reverse proxy; cấu hình sinh từ DB và đẩy qua Admin API; cert tự động qua DNS-01 Cloudflare (kể cả wildcard) |
| `th-cloudflared` | Connector của tunnel do panel tạo (remote-managed), tự tạo khi thiết lập tunnel |
| mạng `th-edge` | Mạng chung giữa Caddy và các service web của app (chỉ service được chọn mới gắn vào) |

## Cài đặt

```bash
cd /data/application/tekar-hosting
cp .env.example .env            # sửa TH_SECRET_KEY, TH_DB_PASSWORD, TH_ADMIN_*
docker network create th-edge
# build image từ mã nguồn (panel, caddy, Claude console, devbox) — docker-compose.yml chỉ khai báo image
docker compose -f docker-compose.yml -f docker-compose.ci.yml --profile claude --profile devbox build
docker compose up -d
# tuỳ chọn: Claude Code console
docker compose --profile claude up -d th-claude
```

Cập nhật code: build lại như trên rồi `docker compose up -d th-api th-worker` (thêm `th-caddy` nếu sửa `caddy/`).
Chỉ dùng `-f docker-compose.ci.yml` cho bước build: `up` kèm `-f` sẽ không nạp `docker-compose.override.yml`
(cổng publish của th-caddy do panel sinh). CI không cần `.env`; tag image theo `TH_VERSION` (mặc định `latest`).

Panel: `http://<server>:9900` (đổi bằng `TH_PANEL_PORT`). Nên đưa panel ra ngoài qua chính Caddy/Tunnel
(tạo route `panel.<domain>` → `th-api:8000`) rồi đặt `TH_PANEL_BIND=127.0.0.1`.

`TH_TRUSTED_PROXIES`: dải IP của reverse proxy đứng trước panel (mạng docker `th-edge` của Caddy, mạng của NPM nếu
còn dùng; mặc định `172.16.0.0/12`). Chỉ các nguồn này được tin `X-Forwarded-For` — IP trong audit log và bộ đếm
chống dò mật khẩu lấy từ đó. Xem subnet bằng `docker network inspect th-edge`. Panel từ chối khởi động nếu
`TH_SECRET_KEY` để mặc định hoặc ngắn hơn 16 ký tự. Swagger `/api/docs` tắt trừ khi `TH_API_DOCS=true`.

Cùng tồn tại với hạ tầng cũ: Nginx Proxy Manager vẫn giữ cổng 80/443; Caddy chạy cổng 8880/8843 và nhận traffic
public qua Cloudflare Tunnel. Khi đã chuyển hết host khỏi NPM (trang Proxy → Import từ NPM), có thể đổi
`TH_CADDY_HTTP_PORT=80`, `TH_CADDY_HTTPS_PORT=443` và tắt NPM.

## Tính năng

### Vendor-free (không khoá vào panel)
- Mỗi app managed là **một thư mục compose chuẩn**: `docker-compose.yml`, `.env`, `docker-compose.override.yml` (panel sinh: label, giới hạn CPU/RAM, mạng edge, volume dạng thư mục), `README.tekar.md`. Chạy tay được: `cd <thư mục> && docker compose -p <project> up -d`.
- Ổ đĩa là nguồn sự thật: sửa file trực tiếp trên host hoặc trong tab **Thư mục** của app (trình soạn thảo, có bản `.bak`); deploy luôn đọc lại file.
- File thuộc user trên host (theo thư mục cha, hoặc `TH_STORAGE_OWNER=uid:gid`).
- **Lưu trữ & Backup → Thư mục**: tự chọn nơi đặt thư mục app, backup, dữ liệu volume (volume của app mới lưu thành thư mục `<dir>/<slug>/<volume>`), dữ liệu mount (biến `APP_DATA_DIR`). Các đường dẫn phải nằm dưới `TH_STORAGE_ROOT` (mặc định `/data`).
- **Export** thư mục app (kèm/không kèm .env, dữ liệu) để mang đi nơi khác; **Gỡ khỏi panel** giữ nguyên container + thư mục.

### Stack trên host (Dockhand, Portainer…)
- Trang **Stack trên host** liệt kê mọi project compose kèm badge công cụ đang quản lý: Dockhand, Portainer, Dockge…, compose CLI, panel.
- Xem compose + env (giá trị secret được che; admin có thể hiện). Dockhand: đọc đường dẫn thật từ DB của Dockhand và `.env.dockhand`. Portainer: đọc file trong volume của Portainer qua Docker API; biến môi trường khôi phục từ container đang chạy.
- **Quản lý tại chỗ** (adopt — vẫn để công cụ cũ quản lý) hoặc **Chuyển về ứng dụng**: tạo thư mục app vendor-free, **giữ nguyên tên project** nên volume/network/dữ liệu giữ nguyên; đường dẫn bind tương đối được đổi thành tuyệt đối (dữ liệu ở chỗ cũ) hoặc chép cả thư mục. Sau khi chuyển: xoá stack khỏi Dockhand/Portainer **không xoá volume**.

### Ứng dụng
- **Managed**: panel lưu compose + `.env` (mã hoá trong DB). Tạo app từ:
  - **GitHub / Git**: clone repo (repo private qua kết nối token GitHub/GitLab/Gitea — Tài khoản → Kết nối Git) vào `<thư mục app>/src`; panel đọc repo, gợi ý chạy bằng compose trong repo (đường dẫn tương đối đổi sang `./src/…`) hoặc build từ `Dockerfile`, điền `.env` từ `.env.example`. Tab **Git** của app: kéo code & deploy lại (`up -d --build`), tuỳ chọn đồng bộ compose theo repo, tự kéo khi có commit mới (5 phút/lần), webhook push của GitHub/GitLab (bỏ qua nhánh khác). Token chỉ gửi qua header HTTP tới đúng host, không ghi vào `.git/config`.
  - **Image Docker**: image + cổng + thư mục dữ liệu → compose sinh sẵn, sửa được.
  - **Compose & .env**: dán hoặc tải file lên.
  - **Template**: có sẵn (WordPress, n8n, Metabase, Uptime Kuma, static, image generic) hoặc template tự lưu từ app bất kỳ (Cài đặt → Lưu làm template, tuỳ chọn bỏ giá trị biến, chia sẻ cho mọi người).
  - **Môi trường phát triển (SSH)**: container Debian (image `tekar-devbox`, panel tự build từ `backend/app/devbox/` lần deploy đầu) với user SSH riêng (mật khẩu và/hoặc public key, tuỳ chọn sudo), thư mục làm việc `/home/<user>/workspace` nằm ở `<thư mục app>/home/<user>` trên host, cổng SSH riêng cấp tự động trong dải `dev_ssh_ports` (Cài đặt → Môi trường phát triển, kèm host hiển thị trong lệnh `ssh`). Biến dịch vụ dùng chung (`DATABASE_URL`, `REDIS_URL`, `S3_*`…) có sẵn trong mọi phiên SSH (`~/.ssh/environment`) và file `~/services.env`; container nằm trên mạng `th-svc-<slug>` nên tới thẳng postgres/redis/s3. Tab Tổng quan có thẻ **Truy cập SSH**: lệnh `ssh`, cấu hình `~/.ssh/config`, hiện/đổi mật khẩu, sửa key, bật/tắt sudo.
  - **Clone app đang chạy** (nút Clone trên trang app): app managed mới với cùng compose/env; tuỳ chọn chép thư mục app, dữ liệu volume / `APP_DATA_DIR`, database/redis/bucket trên dịch vụ nền tảng (cấp tài nguyên riêng), tạm dừng app gốc khi chép, bỏ cổng publish và `container_name` để không trùng, gắn domain mới.
- **Adopted**: nhận quản lý dự án compose đang chạy (Phát hiện dự án). Stack của Dockhand/Portainer được dò đường dẫn thật trên host; nếu không đọc được file compose thì vẫn có start/stop/restart, logs, terminal, metrics, giới hạn tài nguyên, backup volume.
- File `compose.th.yml` sinh tự động: label `th.app`, giới hạn CPU/RAM theo service, gắn mạng edge.
- Deploy webhook cho CI: `POST /api/hooks/deploy/<token>` (pull image + up -d; app từ git: kéo code + build). Tự động cập nhật image hằng đêm (tuỳ chọn).

### Dịch vụ nền tảng dùng chung
- Postgres / Redis / S3 (MinIO hoặc S3 khác): đăng ký server có sẵn hoặc để panel tạo container mới.
- Mỗi app được cấp: database + role riêng (REVOKE PUBLIC), Redis ACL user chỉ truy cập `prefix:*` (cấm lệnh admin/dangerous), bucket + user + policy riêng.
- Biến môi trường (`DATABASE_URL`, `PG*`, `REDIS_URL`, `S3_*`, có thể thêm prefix) được tiêm vào `.env` khi deploy app managed.
- Lưu ý Redis external: nếu redis không chạy với `aclfile`, ACL user sẽ mất khi redis restart (panel cảnh báo).

### Reverse proxy (Caddy) — thay thế hoàn toàn Nginx Proxy Manager

| NPM | Tekar Hosting |
|---|---|
| Proxy host, websocket, custom location | Route `proxy` + custom location (path → upstream khác, kèm Caddyfile riêng) |
| Redirection host (mã, scheme, giữ path) | Route `redirect` |
| 404 host | Route `dead` (mã + nội dung tuỳ chọn) |
| Stream TCP/UDP | Stream (caddy-l4), panel tự publish cổng cho th-caddy |
| Access list (user + IP allow/deny, satisfy any/all, pass auth) | Access list dùng chung, rule IP theo thứ tự |
| Let's Encrypt HTTP-01 / DNS | HTTP-01 + TLS-ALPN (khi Caddy giữ 80/443), DNS-01 Cloudflare, wildcard, tự gia hạn |
| Custom certificate | Tải lên PEM (kiểm tra khớp key, cảnh báo sắp hết hạn) |
| Force SSL, HSTS (+subdomains), HTTP/2 | Có (+ preload), HTTP/2 + HTTP/3 mặc định; force SSL hoạt động cả khi qua Tunnel |
| Block common exploits, cache assets | Có |
| Advanced config (nginx) | Đoạn Caddyfile (kiểm tra cú pháp trước khi lưu) + handler JSON |
| Default site | 404 / trang chào / redirect / HTML / đóng kết nối |
| Access log theo host | Access log JSON, lọc theo domain/status, thống kê request/4xx/5xx |

Thêm: nhiều upstream (load balance + health check), Basic Auth/IP allowlist nhanh theo route, CORS, header,
trang bảo trì, giới hạn body, timeout, chống dò IP thật qua `CF-Connecting-IP`, health check gọi thẳng upstream + cảnh báo.

**Chuyển từ NPM (Proxy → Import NPM rồi Cài đặt & chuyển đổi):**
1. Import: proxy host, redirection, 404, stream, access list (mật khẩu được băm lại), default site. `advanced_config` nginx được giữ lại để tham khảo, cần viết lại bằng Caddyfile.
2. Kiểm tra: trang chuyển đổi liệt kê xung đột cổng, host NPM chưa import, route chưa bật HTTPS.
3. Chuyển: dừng container NPM → Caddy nhận 80/443 → cert Let's Encrypt cấp qua HTTP-01 trong vài giây. Lỗi giữa chừng → tự hoàn tác.
4. Hoàn tác bất cứ lúc nào: nút **Trả lại cho NPM** (Caddy về cổng phụ, bật lại NPM). Dữ liệu NPM không bị xoá.

### Cloudflare
- Xác thực token → chọn account/zone → đặt domain gốc.
- Tạo tunnel (remote-managed) + chạy connector; ingress và CNAME DNS được đồng bộ tự động mỗi khi đổi route.
- Wildcard: `*.domain` → tunnel → Caddy; Caddy cấp cert wildcard qua DNS-01 nếu cần HTTPS trực tiếp.
- Token cần quyền: **Account › Cloudflare Tunnel: Edit**, **Zone › DNS: Edit**, **Zone › Zone: Read**.

### Backup / restore
- Theo cron từng app, giữ N bản. Gồm: volume, thư mục bind, dump logic DB nằm trong app (postgres/mysql), database/key/bucket trên dịch vụ dùng chung, compose + .env.
- **Đích backup**: thư mục local khác (ổ gắn ngoài, NFS), **S3-compatible** (AWS S3, Cloudflare R2, Backblaze B2, Wasabi, MinIO), **SFTP** (mật khẩu hoặc private key). Có nút kiểm tra kết nối; retention áp dụng cả bản trên đích; tuỳ chọn không giữ bản cục bộ.
- Restore chọn từng mục, tự tải từ đích nếu bản cục bộ không còn. Sau khi cài lại panel: duyệt file trên đích → **Nhập để khôi phục**.
- **Sao lưu chính panel** (Cài đặt → Sao lưu panel): `pg_dump` DB của panel + thư mục cert/cấu hình Caddy + `.env`, đóng gói
  `<backups>/_panel/panel-<stamp>.tar.gz` (chmod 600), theo cron, giữ N bản, tuỳ chọn đẩy lên một đích backup.
  Cách khôi phục ghi ở đầu `backend/app/services/panel_backup.py`.

### Bảo mật
- Kiểm tra cấu hình: privileged, docker.sock, host network/pid, DB mở 0.0.0.0, chạy root, thiếu giới hạn RAM, tag latest, secret trong env, restart loop, trang quản trị public không bảo vệ.
- Quét lỗ hổng image bằng Trivy theo lịch; cảnh báo khi có CRITICAL.

### Giám sát & cấp phát
- **Dung lượng theo stack** (Lưu trữ & Backup): mỗi stack (app panel, Dockhand, Portainer, compose tay, container lẻ) tách theo thư mục compose, bind mount, named volume, lớp ghi container, backup cục bộ; image hiển thị riêng (dùng chung, không cộng tổng). Thư mục stack con lồng bên trong (vd dữ liệu Dockhand) được trừ ra; thư mục quá rộng/hệ thống bỏ qua. Worker tính lại mỗi 6 giờ hoặc bấm *Tính lại*.
- CPU/RAM/IO/network từng container (cgroup v2), host CPU/RAM/disk, lịch sử 72h.
- Hạn mức theo app (CPU, RAM, disk) và giới hạn theo service; so sánh cấp phát / sử dụng / dung lượng host.
- Cảnh báo: RAM/disk/CPU host, container down/unhealthy, gần chạm giới hạn RAM, vượt hạn mức disk, domain không phản hồi, job lỗi.
- Cảnh báo ổ đĩa qua Telegram kèm nút **Xoá image cũ** (Cài đặt → *Cảnh báo ổ đĩa qua Telegram*, mặc định tắt, ngưỡng 80%, image cũ hơn 14 ngày). Nút xoá các image không container nào dùng (kể cả container đang dừng), dùng một lần, chỉ trong chat đã cấu hình, có ghi nhật ký. Worker nhận lượt bấm qua `getUpdates`, nên cần bot riêng cho panel (không webhook, không dùng chung).

### Phân quyền
- Vai trò hệ thống: `admin` (toàn quyền), `operator` (mọi app + hạ tầng, không quản lý user/cài đặt), `member` (chỉ app được cấp).
- Vai trò theo app: `owner`, `deployer` (deploy, env, terminal, backup), `viewer`.
- Route của deployer: chỉ trỏ tới container thuộc app của mình, không chen path/wildcard vào domain app khác;
  Caddyfile nâng cao / `headers_up` chỉ admin/operator; chuỗi nhập vào (body, redirect, header) được escape placeholder.
- 2FA TOTP (tắt cần cả mật khẩu + OTP), API token (token *chỉ đọc* bị chặn mọi request ghi), audit log mọi thao tác.
  Đổi mật khẩu / reset 2FA / khoá tài khoản làm mọi phiên đăng nhập cũ hết hiệu lực.
- Lưu ý: deployer vẫn deploy được compose tuỳ ý (mount host, privileged) — chỉ cấp vai trò này cho người tin cậy.

### Claude
1. **Trợ lý trong panel**: chat có công cụ đọc trạng thái, log, tài nguyên, bảo mật…; thao tác thay đổi (restart, deploy, exec, đổi giới hạn, sửa route) luôn chờ người dùng **Duyệt**. Báo cáo tình trạng hệ thống tạo theo yêu cầu hoặc theo lịch (gửi Telegram). Chạy bằng một trong hai engine (Claude → *Gói Claude* → Engine; `auto` ưu tiên API key):
   - **API key** (Cài đặt → Claude): gọi Claude API, tính phí theo token.
   - **Gói Claude** (Pro/Max/Team): panel chạy Claude Code headless trong `th-claude` (`claude -p --output-format stream-json`), **không có tool có sẵn** (Bash/Edit…), chỉ dùng MCP của panel với token tạm cho từng lượt → cùng phân quyền, cùng cơ chế duyệt. Đăng nhập: nút *Đăng nhập* (terminal chạy `claude auth login`, mở link, dán mã) hoặc dán token dài hạn từ `claude setup-token` (mã hoá trong DB). Có nút *Kiểm tra*, chọn model (`opus`, `sonnet`…). Báo cáo chạy ở worker dùng API token chỉ-đọc tạm, xoá sau khi xong.
2. **Claude Code console** (tương tác): bật profile `claude`, Claude → *Gói Claude* → *Thiết lập MCP* → mở terminal, chạy `claude`. Console không có docker socket; mọi thao tác đi qua MCP của panel (phân quyền + audit).
3. **MCP** (Claude → *MCP*): endpoint `https://<panel>/mcp` (Streamable HTTP), tạo token **chỉ đọc** hoặc đọc + thay đổi, đoạn cấu hình sẵn cho Claude Code, Claude Desktop (`mcp-remote`), `.mcp.json`, curl; danh sách tool và nhật ký lời gọi MCP (`mcp.call` trong audit log).
   `claude mcp add --transport http tekar-hosting https://<panel>/mcp --header "Authorization: Bearer th_..."`

## Phát triển

```bash
# build lại panel (backend + giao diện) và chạy
docker compose -f docker-compose.yml -f docker-compose.ci.yml build th-api && docker compose up -d th-api th-worker
# (đừng dùng --target backend cho tag tekar-hosting:latest: image đó không có giao diện, panel trả 404)
# frontend
cd frontend && npm install && npm run dev   # proxy /api → :9900
```

API docs: `/api/docs`.
