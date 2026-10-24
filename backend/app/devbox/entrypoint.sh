#!/bin/bash
# Tạo user SSH + thư mục làm việc rồi chạy sshd. Biến:
#   DEV_USER, DEV_PASSWORD (rỗng = chỉ dùng key), DEV_SUDO (true/false), DEV_UID
#   /etc/th/authorized_keys.src (mount từ <thư mục app>/authorized_keys, sửa xong restart app)
# Mọi biến môi trường khác của container (biến dịch vụ dùng chung DATABASE_URL, REDIS_URL, S3_*… và .env của app)
# được ghi vào ~/.ssh/environment (sshd nạp cho mọi phiên: shell, lệnh, sftp, VS Code Remote) và ~/services.env.
set -euo pipefail
U="${DEV_USER:-dev}"
UID_="${DEV_UID:-1000}"
H="/home/$U"

if id "$U" >/dev/null 2>&1; then
  [ "$(id -u "$U")" = "$UID_" ] || { echo "devbox: user $U trùng user hệ thống" >&2; exit 1; }
else
  getent group "$UID_" >/dev/null || groupadd -g "$UID_" "$U"
  useradd -u "$UID_" -g "$UID_" -d "$H" -s /bin/bash -M "$U"
fi
mkdir -p "$H/workspace" "$H/.ssh"
[ -f "$H/.bashrc" ] || cp /etc/skel/.bashrc /etc/skel/.profile "$H/" 2>/dev/null || true
chown "$UID_:$UID_" "$H" "$H/workspace" "$H/.ssh"
[ -f "$H/.bashrc" ] && chown "$UID_:$UID_" "$H/.bashrc" "$H/.profile" 2>/dev/null || true
chmod 700 "$H/.ssh"

if [ -n "${DEV_PASSWORD:-}" ]; then
  echo "$U:$DEV_PASSWORD" | chpasswd
  PWAUTH=yes
else
  passwd -l "$U" >/dev/null
  PWAUTH=no
fi
if [ "${DEV_SUDO:-false}" = "true" ]; then
  echo "$U ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/devbox
  chmod 440 /etc/sudoers.d/devbox
else
  rm -f /etc/sudoers.d/devbox
fi

# biến môi trường cho phiên SSH
{
  env | grep -Ev '^(DEV_PASSWORD|DEV_SUDO|DEV_UID|HOME|HOSTNAME|PATH|PWD|SHLVL|TERM|_|LANG|TZ|DEBIAN_FRONTEND)=' | grep -E '^[A-Za-z_][A-Za-z0-9_]*=' || true
  echo "PATH=$H/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
} > "$H/.ssh/environment"
grep -v '^PATH=' "$H/.ssh/environment" | grep -v '^DEV_USER=' > "$H/services.env"
chown "$UID_:$UID_" "$H/.ssh/environment" "$H/services.env"
chmod 600 "$H/.ssh/environment" "$H/services.env"

# key do panel quản lý: chép sang file thuộc root (sshd StrictModes không nhận file của uid trên host)
install -m 644 -o root -g root /dev/null /etc/th/authorized_keys
[ -f /etc/th/authorized_keys.src ] && grep -E '^(ssh-|ecdsa-|sk-)' /etc/th/authorized_keys.src > /etc/th/authorized_keys || true

# host key giữ qua các lần tạo lại container (nằm trong home, chỉ root đọc)
KD=/home/.ssh-host-keys
mkdir -p "$KD"; chmod 700 "$KD"
[ -f "$KD/ssh_host_ed25519_key" ] || ssh-keygen -q -t ed25519 -N '' -f "$KD/ssh_host_ed25519_key"
[ -f "$KD/ssh_host_rsa_key" ] || ssh-keygen -q -t rsa -b 3072 -N '' -f "$KD/ssh_host_rsa_key"

cat > /etc/ssh/sshd_config.d/devbox.conf <<CONF
HostKey $KD/ssh_host_ed25519_key
HostKey $KD/ssh_host_rsa_key
PermitRootLogin no
AllowUsers $U
PasswordAuthentication $PWAUTH
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys /etc/th/authorized_keys
PermitUserEnvironment yes
AllowTcpForwarding yes
X11Forwarding no
ClientAliveInterval 60
CONF

echo "devbox: user=$U sudo=${DEV_SUDO:-false} password_auth=$PWAUTH"
exec /usr/sbin/sshd -D -e
