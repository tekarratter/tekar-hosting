"""Thư viện template ứng dụng (1-click). Biến ${VAR} được điền từ form / sinh ngẫu nhiên.

`services`: dịch vụ nền tảng cần cấp (postgres/redis/s3) — env được tiêm tự động.
`web`: service + port để tạo proxy route.
"""

TEMPLATES: list[dict] = [
    {
        "id": "static-nginx",
        "name": "Static site (Nginx)",
        "description": "Web tĩnh, mount thư mục html",
        "web": {"service": "web", "port": 80},
        "services": [],
        "compose": """services:
  web:
    image: nginx:1.27-alpine
    restart: unless-stopped
    volumes:
      - ./html:/usr/share/nginx/html:ro
""",
        "env": "",
    },
    {
        "id": "wordpress",
        "name": "WordPress",
        "description": "WordPress + MariaDB riêng",
        "web": {"service": "wordpress", "port": 80},
        "services": [],
        "compose": """services:
  wordpress:
    image: wordpress:6.8-php8.3-apache
    restart: unless-stopped
    environment:
      WORDPRESS_DB_HOST: db
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: ${DB_PASSWORD}
      WORDPRESS_DB_NAME: wp
    volumes:
      - wp-data:/var/www/html
    depends_on: [db]
  db:
    image: mariadb:11.4
    restart: unless-stopped
    environment:
      MARIADB_DATABASE: wp
      MARIADB_USER: wp
      MARIADB_PASSWORD: ${DB_PASSWORD}
      MARIADB_ROOT_PASSWORD: ${DB_ROOT_PASSWORD}
    volumes:
      - db-data:/var/lib/mysql
volumes:
  wp-data:
  db-data:
""",
        "env": "DB_PASSWORD=${random}\nDB_ROOT_PASSWORD=${random}\n",
    },
    {
        "id": "n8n",
        "name": "n8n",
        "description": "Workflow automation, dùng Postgres dùng chung",
        "web": {"service": "n8n", "port": 5678},
        "services": ["postgres"],
        "compose": """services:
  n8n:
    image: n8nio/n8n:1.95.3
    restart: unless-stopped
    environment:
      DB_TYPE: postgresdb
      DB_POSTGRESDB_HOST: ${PGHOST}
      DB_POSTGRESDB_PORT: ${PGPORT}
      DB_POSTGRESDB_DATABASE: ${PGDATABASE}
      DB_POSTGRESDB_USER: ${PGUSER}
      DB_POSTGRESDB_PASSWORD: ${PGPASSWORD}
      N8N_HOST: ${DOMAIN}
      N8N_PROTOCOL: https
      WEBHOOK_URL: https://${DOMAIN}/
      N8N_ENCRYPTION_KEY: ${ENCRYPTION_KEY}
      GENERIC_TIMEZONE: Asia/Ho_Chi_Minh
    volumes:
      - n8n-data:/home/node/.n8n
volumes:
  n8n-data:
""",
        "env": "ENCRYPTION_KEY=${random}\n",
    },
    {
        "id": "uptime-kuma",
        "name": "Uptime Kuma",
        "description": "Giám sát uptime",
        "web": {"service": "kuma", "port": 3001},
        "services": [],
        "compose": """services:
  kuma:
    image: louislam/uptime-kuma:1.23.16
    restart: unless-stopped
    volumes:
      - kuma-data:/app/data
volumes:
  kuma-data:
""",
        "env": "",
    },
    {
        "id": "metabase",
        "name": "Metabase",
        "description": "BI dashboard, lưu metadata trên Postgres dùng chung",
        "web": {"service": "metabase", "port": 3000},
        "services": ["postgres"],
        "compose": """services:
  metabase:
    image: metabase/metabase:v0.50.20
    restart: unless-stopped
    environment:
      MB_DB_TYPE: postgres
      MB_DB_HOST: ${PGHOST}
      MB_DB_PORT: ${PGPORT}
      MB_DB_DBNAME: ${PGDATABASE}
      MB_DB_USER: ${PGUSER}
      MB_DB_PASS: ${PGPASSWORD}
""",
        "env": "",
    },
    {
        "id": "node-app",
        "name": "Ứng dụng từ image (generic)",
        "description": "Chạy 1 image bất kỳ, tiêm sẵn Postgres + Redis + S3",
        "web": {"service": "app", "port": 3000},
        "services": ["postgres", "redis", "s3"],
        "compose": """services:
  app:
    image: ${IMAGE}
    restart: unless-stopped
    env_file: .env
""",
        "env": "IMAGE=nginx:alpine\n",
    },
]


def get_template(tid: str) -> dict | None:
    return next((t for t in TEMPLATES if t["id"] == tid), None)
