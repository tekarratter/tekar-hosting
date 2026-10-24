# Claude Code console: đăng nhập bằng tài khoản Claude (Pro/Max/Team) qua web terminal của panel.
# Kết nối MCP tới panel (http://th-api:8000/mcp) để đọc trạng thái, báo cáo, cấu hình container.
FROM node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates jq less procps \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && usermod -l claude -d /home/claude -m node && groupmod -n claude node
COPY CLAUDE.md /opt/th/CLAUDE.md
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENV HOME=/home/claude
WORKDIR /home/claude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
