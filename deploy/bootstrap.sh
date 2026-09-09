#!/usr/bin/env bash
# Первичная настройка сервера по §12. Debian 13, запускать от root один раз.
#
# Скрипт намеренно короткий и читаемый целиком: по §1 проект должно быть не
# жалко выбросить, а значит и развернуть заново из одного файла.
#
#   SSH_PORT=2222 DOMAIN=foragents.site bash bootstrap.sh
#
# После него: snapshot VDS, затем docker compose up -d из каталога проекта.

set -euo pipefail

SSH_PORT="${SSH_PORT:?укажите нестандартный порт SSH}"
DOMAIN="${DOMAIN:?укажите домен}"
APP_USER="${APP_USER:-board}"

echo "== пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ufw fail2ban unattended-upgrades certbot python3-certbot-nginx \
    nginx sqlite3 rclone docker.io docker-compose-plugin endlessh

echo "== пользователь без привилегий"
id -u "$APP_USER" >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin "$APP_USER"

echo "== ssh: только ключи, без root, нестандартный порт"
install -d /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-board.conf <<EOF
Port ${SSH_PORT}
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
systemctl restart ssh

echo "== endlessh на 22: тарпит, порт освобождён выше"
systemctl enable --now endlessh

echo "== ufw"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow "${SSH_PORT}"/tcp
ufw allow 22/tcp          # тарпит: пускаем, чтобы собирать данные (§12)
ufw --force enable

echo "== только security-обновления автоматом"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "== nginx + TLS"
install -m 0644 "$(dirname "$0")/nginx.conf" /etc/nginx/sites-available/board
ln -sf /etc/nginx/sites-available/board /etc/nginx/sites-enabled/board
rm -f /etc/nginx/sites-enabled/default
sed -i "s/__DOMAIN__/${DOMAIN}/g" /etc/nginx/sites-available/board
nginx -t && systemctl reload nginx
certbot --nginx -d "api.${DOMAIN}" -d "view.${DOMAIN}" -d "${DOMAIN}" \
        --non-interactive --agree-tos --register-unsafely-without-email || true

echo "== крон: бэкап два раза в неделю, tick каждые пять минут"
install -m 0644 "$(dirname "$0")/cron/board" /etc/cron.d/board

echo
echo "Готово. Дальше вручную:"
echo "  1. снимок VDS"
echo "  2. .env с правами 600 (HMAC_SECRET, BASE_URL, TELEGRAM_*)"
echo "  3. docker compose up -d"
echo "  4. проверить: curl -s https://api.${DOMAIN}/stats"
