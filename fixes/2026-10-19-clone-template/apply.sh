#!/usr/bin/env bash
# Áp bản vá clone/template/mạng dịch vụ nền tảng cho Tekar Hosting.
#   ./apply.sh            kiểm tra + áp bản vá + kiểm tra cú pháp (chưa build lại)
#   ./apply.sh --deploy   như trên + build lại image, khởi động lại th-api/th-worker
#   ./apply.sh --revert   gỡ bản vá
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PATCH="$HERE/fix.patch"
cd "$REPO"

files=(backend/app/routers/apps.py backend/app/services/compose.py backend/app/services/app_jobs.py backend/app/services/docker_svc.py)

if [[ "${1:-}" == "--revert" ]]; then
  patch -p1 -R --dry-run < "$PATCH" >/dev/null && patch -p1 -R < "$PATCH"
  echo "Đã gỡ bản vá. Chạy: docker build -t tekar-hosting:latest . && docker compose up -d th-api th-worker"
  exit 0
fi

if patch -p1 -R --dry-run -f < "$PATCH" >/dev/null 2>&1; then
  echo "Bản vá đã được áp trước đó — bỏ qua bước patch."
else
  echo "== Kiểm tra bản vá (dry-run)"
  patch -p1 --dry-run < "$PATCH"
  echo "== Áp bản vá"
  patch -p1 --no-backup-if-mismatch < "$PATCH"
fi

echo "== Kiểm tra cú pháp Python"
python3 -m py_compile "${files[@]}"
echo "OK"

if [[ "${1:-}" == "--deploy" ]]; then
  echo "== Build lại & khởi động lại th-api, th-worker"
  docker build -t tekar-hosting:latest "$REPO" && docker compose up -d th-api th-worker
  echo
  echo "Xong. Deploy lại các app có dịch vụ nền tảng (vd demo-static) để nhận mạng th-svc-<slug> và COMPOSE_PROJECT_NAME."
else
  echo
  echo "Đã áp code. Chạy lại với --deploy để build + khởi động lại panel."
fi
