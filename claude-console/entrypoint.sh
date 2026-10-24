#!/bin/sh
# Volume home có thể được tạo rỗng (owner root) → sửa quyền rồi hạ xuống user claude
chown claude:claude /home/claude
[ -f /home/claude/CLAUDE.md ] || install -o claude -g claude -m 644 /opt/th/CLAUDE.md /home/claude/CLAUDE.md
[ -f /home/claude/.mcp.json ] && chown claude:claude /home/claude/.mcp.json
exec setpriv --reuid=claude --regid=claude --init-groups "$@"
