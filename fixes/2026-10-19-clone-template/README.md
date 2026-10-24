# Bản vá: clone / template app — tiêm env, stack, network, dịch vụ nền tảng

Ngày kiểm tra: 2026-10-19. Phương pháp: chạy thật end-to-end qua API của panel (tạo app `qa-src` có
web + worker, gắn Postgres/Redis/S3 dùng chung, domain, cổng publish, `container_name`, volume), clone
sang `qa-clone` có chép dữ liệu, rồi soi `.env`, override, mạng, DNS và route. App test đã xoá sạch sau đó.

## Cách áp

```bash
cd /data/application/tekar-hosting/fixes/2026-10-19-clone-template
./apply.sh            # dry-run + áp patch + kiểm tra cú pháp
./apply.sh --deploy   # + build lại image, restart th-api/th-worker
./apply.sh --revert   # gỡ bản vá
```

Sau khi deploy: **deploy lại các app đang có dịch vụ nền tảng** (hiện có `demo-static`) để chúng nhận mạng
`th-svc-<slug>` và biến `COMPOSE_PROJECT_NAME`.

File bị sửa: `backend/app/routers/apps.py`, `backend/app/services/compose.py`,
`backend/app/services/app_jobs.py`, `backend/app/services/docker_svc.py`.

## Kết quả kiểm tra

| Hạng mục | Trạng thái trước vá |
|---|---|
| Tiêm env Postgres/Redis/S3 (`DATABASE_URL`, `PG*`, `REDIS_URL`, `S3_*`) | ✅ đúng, mỗi app có user/db/prefix/bucket riêng |
| Clone cấp tài nguyên nền tảng mới + chép dữ liệu DB/volume | ✅ đúng (`qa_src` → `qa_clone`, volume chép đủ) |
| Clone bỏ `ports` và `container_name` | ✅ đúng |
| `APP_DATA_DIR`, thư mục volume | ✅ đúng |
| Stack name (`-p th-<slug>`) | ✅ panel đúng, ❌ không có trong `.env` (FIX-5) |
| **Service không nằm trong edge truy cập DB/Redis/S3 dùng chung** | ❌ **lỗi nặng** (FIX-1) |
| Route proxy khi compose có `container_name` | ❌ lỗi 502 (FIX-2) |
| Clone gắn domain mới → biến `DOMAIN` | ❌ vẫn giữ domain cũ (FIX-3) |
| Biến template nhập `${random}` / bỏ trống | ❌ ghi nguyên chữ vào `.env` (FIX-4) |
| Tạo app từ template qua API/MCP không truyền `services` | ❌ không cấp dịch vụ, `${PGHOST}` rỗng (FIX-6) |
| Trùng cổng publish khi tạo app | ⚠ không kiểm tra (chưa vá, xem "Tồn đọng") |
| Trùng alias DNS trên mạng `th-edge` | ⚠ rủi ro kiến trúc (chưa vá, xem "Tồn đọng") |

## Chi tiết từng lỗi và cách vá

### FIX-1 — Service không ở edge không tới được dịch vụ nền tảng (nặng)
**Hiện tượng:** container Postgres/Redis/MinIO dùng chung chỉ nằm trên mạng `th-edge`, còn override chỉ gắn
`th-edge` cho service có domain. Vì vậy worker, cron hay backend nội bộ nhận `PGHOST=th-postgres-shared-pg`
nhưng **không resolve được** tên này. Đã kiểm chứng: `th-qa-src-worker-1` báo `no response`, còn `qa-src-web`
(nằm trên edge) thì kết nối được.

**Không vá bằng cách gắn mọi service vào `th-edge`.** Đã kiểm chứng rằng trên mạng chung, tên service bị
trộn giữa các app: từ `qa-src-web`, `getent web` trả về IP của **qa-clone và demo-static**. Nếu làm vậy,
các tên như `db` hay `redis` sẽ trỏ nhầm sang app khác.

**Cách vá:** mỗi app có binding được cấp mạng riêng `th-svc-<slug>` (external, nên `compose down` không xoá).
- `app_jobs.connect_platform()`: tạo mạng này rồi gắn container dịch vụ nền tảng (nếu là container cục bộ)
  vào đó.
- `compose.write_override(app, svc_network)`: gắn **mọi service** của app (trừ `network_mode`) vào mạng đó.
- `job_delete`: gỡ và xoá mạng (`docker_svc.remove_network`).

Đã kiểm chứng trên mạng riêng: `pg_isready` ⇒ `accepting connections`, và `web` của app khác không còn thấy.

### FIX-2 — Route proxy sai upstream khi có `container_name`
`_add_route` luôn dùng `<project>-<service>-1`. Với `container_name: qa-src-web`, route trỏ tới
`th-qa-src-web-1:80`, là container không tồn tại, nên trả 502. Bản vá thêm `_upstream_host()`, dùng
`container_name` nếu có (bỏ qua khi tên chứa `${...}`).

### FIX-3 — Clone sang domain mới nhưng `DOMAIN` vẫn là domain cũ
`DOMAIN=` được ghi vào phần env người dùng lúc tạo app, nên clone chép nguyên giá trị cũ. Hậu quả: n8n
(`WEBHOOK_URL`, `N8N_HOST`) và các app dùng `${DOMAIN}` chạy trên domain của app gốc. Bản vá thay dòng
`DOMAIN=` (hoặc thêm vào nếu chưa có) khi clone có `domain`.

### FIX-4 — `_fill` với giá trị `${random}` hoặc bỏ trống
Giao diện gợi ý "`${random}` = sinh ngẫu nhiên", nhưng `re.sub` không xử lý lại chuỗi thay thế, nên `.env`
chứa nguyên chữ `${random}`, và compose nội suy thành rỗng. Biến bỏ trống thì ghi `KEY=${KEY}`. Template
lưu từ app với `blank_env` bị lỗi này với **mọi secret**. Bản vá: giá trị `${random}` thì sinh ngẫu nhiên,
bỏ trống thì để rỗng.

### FIX-5 — Tiêm stack name
Khối env tự sinh thêm `COMPOSE_PROJECT_NAME=th-<slug>` và `APP_SLUG=<slug>`. Trước đây, nếu chạy tay
`docker compose up -d` trong thư mục (đúng như README "vendor-free" quảng cáo) mà quên `-p`, compose lấy tên
thư mục `<slug>` và **dựng stack thứ hai trùng lặp**.

### FIX-6 — Template qua API/MCP không tự cấp dịch vụ
Frontend tự tick dịch vụ theo `template.services`, nhưng backend thì không. Bản vá: nếu có template mà
`services` rỗng thì backend tự cấp dịch vụ đầu tiên đúng loại.

## Tồn đọng (chưa vá — cần quyết định thiết kế)

1. **Trùng alias DNS trên `th-edge`:** mọi service có domain đều mang alias tên service (`web`, `api`…)
   trên mạng chung. Nếu một service có domain gọi service cùng app bằng tên ngắn, nó có thể trúng app khác.
   Hướng sửa gốc là **cho `th-caddy` join mạng `default` của từng app** thay vì kéo app vào `th-edge`.
   Đây là refactor lớn (proxy + override + cleanup mạng).
2. **Không kiểm tra trùng cổng publish** khi tạo app (Image/Compose/Template). Nên đối chiếu với
   `docker ps` hoặc `caddy_ports` trước khi deploy và báo lỗi sớm.
3. **Clone volume/network có `name:` cố định** trong compose: bản clone dùng **chung** volume với app gốc
   (và `_copy_volumes` chép vào nhầm chỗ). Nên bỏ `name:` khi clone, hoặc cảnh báo trong `clone-info`.
4. **Route cũ** tạo trước bản vá cho app có `container_name` cần sửa upstream tay (hiện không có app nào
   bị ảnh hưởng).
5. Đổi slug hoặc xoá binding cuối cùng không gỡ mạng `th-svc-<slug>`. Việc này vô hại, chỉ để thừa mạng.

## Đã kiểm chứng bản vá
- `patch --dry-run` sạch trên mã hiện tại; `py_compile` OK.
- Chạy logic đã vá trong container tạm từ image `tekar-hosting:latest`: `_fill`, `_upstream_host`, `write_env`
  (có `COMPOSE_PROJECT_NAME`, `APP_SLUG`), `write_override` (web: `default`+`th-edge`+`th-svc-qa`;
  worker: `default`+`th-svc-qa`; service `network_mode: host` không bị đụng).
- **Chưa** chạy end-to-end sau khi build lại panel. Người áp nên lặp lại kịch bản: tạo app web + worker có
  Postgres/Redis/S3, kiểm tra `docker exec <worker> pg_isready -h $PGHOST`, rồi clone sang domain mới và
  kiểm tra `DOMAIN`.

## Đã áp — 2026-10-19 (ghi chú của người áp)
Đã áp lên panel và chạy lại kịch bản end-to-end ở trên. Cả 6 lỗi xác nhận đã hết: worker `pg_isready` ⇒
`accepting connections`, kết nối được Redis/S3, không thấy container app khác; route `container_name` trả 200;
clone ⇒ `DOMAIN` mới; `${random}`/bỏ trống đúng; template qua API tự cấp Postgres. App/template test đã xoá,
không còn mạng `th-svc-*` thừa. `demo-static` đã deploy lại và nằm trên `th-svc-demo-static`.

Chỉnh thêm so với bản vá gốc (`fix.patch` đã được tạo lại để khớp code hiện tại; bản gốc không còn giữ):
- `connect_platform` dùng `container_name` / `public_host` của dịch vụ (host app thật sự kết nối) thay vì
  `service.host` (host của control plane), và bỏ qua lỗi khi host là IP/tên miền ngoài.
- **`job_limits` (đổi giới hạn tài nguyên) gọi `write_override(app)` không có mạng ⇒ override mất `th-svc-<slug>`**,
  lần recreate sau worker lại mất DB. Đã tách `platform_bindings()` dùng chung cho deploy và limits.
- `job_delete`: lỗi xoá mạng chỉ ghi log, không làm hỏng job (DB đã xoá bản ghi trước đó).
- `apply.sh --deploy` dùng `docker compose up --build`, nhưng image `tekar-hosting` **không có `build:`
  trong compose** ⇒ không build lại gì. Đã đổi sang `docker build -t tekar-hosting:latest`.

Tồn đọng thêm: container dịch vụ nền tảng bị tạo lại (vd nâng cấp postgres dùng chung) sẽ rời các mạng
`th-svc-*` cho tới khi từng app deploy lại.
