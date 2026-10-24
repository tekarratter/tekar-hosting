# Image môi trường phát triển cho app loại "devbox": sshd + công cụ dev cơ bản.
# Panel tự build (tag tekar-devbox:<DEVBOX_VERSION>) lần đầu deploy một app devbox.
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 TZ=Asia/Ho_Chi_Minh
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      openssh-server sudo bash-completion ca-certificates curl wget git vim nano less htop tmux jq unzip zip rsync \
      build-essential python3 python3-pip python3-venv postgresql-client redis-tools iputils-ping netcat-openbsd \
      dnsutils procps locales tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    sed -i -E 's/^# *(en_US.UTF-8|vi_VN.UTF-8)/\1/' /etc/locale.gen; locale-gen; \
    rm -f /etc/ssh/ssh_host_*; mkdir -p /run/sshd /etc/th
COPY entrypoint.sh /usr/local/bin/devbox-entrypoint
RUN chmod 755 /usr/local/bin/devbox-entrypoint
EXPOSE 22
ENTRYPOINT ["/usr/local/bin/devbox-entrypoint"]
