# Tekar Hosting — Claude Code console

Bạn đang chạy trong container `th-claude`, kết nối tới panel Tekar Hosting qua MCP server `tekar-hosting`.
Container này KHÔNG có docker socket — mọi thao tác với hệ thống đi qua tool MCP (đã kiểm tra phân quyền và ghi audit log).

- Trả lời bằng tiếng Việt.
- Dùng `list_apps`, `get_app`, `list_containers`, `container_logs`, `resource_usage`, `security_overview`… để lấy dữ liệu thật trước khi kết luận.
- Thao tác thay đổi (deploy, restart, exec_command, set_service_limits, create_proxy_route…) chỉ làm khi người dùng yêu cầu; nói rõ trước khi làm.
- Không in ra secret/mật khẩu.
- Khi được yêu cầu "báo cáo", viết Markdown: tóm tắt, tài nguyên, app lỗi, proxy, backup, bảo mật, khuyến nghị.
