#!/usr/bin/env bash
# Первичная настройка сервера по §12. Debian 13 или Ubuntu 24.04, от root, один раз.
#
# Скрипт намеренно короткий и читаемый целиком: по §1 проект должно быть не
# жалко выбросить, а значит и развернуть заново из одного файла.
#
#   ADMIN_USER=egor SSH_PORT=2222 DOMAIN=foragents.site bash bootstrap.sh
#
# После него: snapshot VDS, затем docker compose up -d из каталога проекта.

set -euo pipefail

SSH_PORT="${SSH_PORT:?укажите нестандартный порт SSH}"
DOMAIN="${DOMAIN:?укажите домен}"
ADMIN_USER="${ADMIN_USER:?укажите логин администратора, под которым вы будете входить}"
APP_USER="${APP_USER:-board}"

# --------------------------------------------------------------------------
# Предохранитель. Ниже выключается вход root, и если к этому моменту у
# администратора нет своего ключа, войти не сможет никто: у служебного
# пользователя shell выставлен в nologin намеренно. Проверяем ДО изменений.
# --------------------------------------------------------------------------
if [ ! -s /root/.ssh/authorized_keys ]; then
    echo "ОСТАНОВ: /root/.ssh/authorized_keys пуст." >&2
    echo "Скопировать администратору нечего, а вход root будет выключен." >&2
    exit 1
fi

echo "== пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git ufw fail2ban unattended-upgrades certbot \
    python3-certbot-nginx nginx sqlite3 rclone endlessh docker.io

# Плагин compose называется по-разному в Debian и Ubuntu. Ставим тот, что есть.
apt-get install -y -qq docker-compose-v2 2>/dev/null \
    || apt-get install -y -qq docker-compose-plugin

echo "== администратор ${ADMIN_USER} с ключом root"
# Создаётся ДО выключения root: иначе выключать было бы нечего и некому.
id -u "$ADMIN_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$ADMIN_USER"
usermod -aG sudo,docker "$ADMIN_USER"
install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "/home/$ADMIN_USER/.ssh"
install -m 600 -o "$ADMIN_USER" -g "$ADMIN_USER" \
    /root/.ssh/authorized_keys "/home/$ADMIN_USER/.ssh/authorized_keys"
# sudo без пароля: пароля у учётки нет вовсе, вход только по ключу.
echo "$ADMIN_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$ADMIN_USER"
chmod 440 "/etc/sudoers.d/90-$ADMIN_USER"

echo "== служебный пользователь без входа"
id -u "$APP_USER" >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin "$APP_USER"

echo "== ssh: только ключи, без root, нестандартный порт"
install -d /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-board.conf <<EOF
Port ${SSH_PORT}
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF

# Ubuntu 24.04 запускает sshd через socket-активацию, и тогда порт берётся из
# ssh.socket, а Port в конфиге молча игнорируется. Возвращаем классический
# режим, иначе смена порта выглядит применённой, но ею не является.
if systemctl is-enabled ssh.socket >/dev/null 2>&1; then
    echo "   socket-активация выключается (Ubuntu 24.04)"
    systemctl disable --now ssh.socket
    systemctl enable ssh.service
fi
sshd -t                      # конфиг проверяется до перезапуска, а не после
systemctl restart ssh
sleep 1
ss -lntp | grep -q ":${SSH_PORT} " || { echo "ОСТАНОВ: sshd не слушает ${SSH_PORT}" >&2; exit 1; }

echo "== endlessh на 22: тарпит, порт освобождён выше"
systemctl enable --now endlessh || echo "   endlessh не поднялся, не критично"

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

echo "== nginx"
install -d /etc/nginx/snippets /var/www/board
install -m 0644 "$(dirname "$0")/snippets/board-proxy.conf" /etc/nginx/snippets/
install -m 0644 "$(dirname "$0")/nginx.conf" /etc/nginx/sites-available/board
sed -i "s/__DOMAIN__/${DOMAIN}/g" /etc/nginx/sites-available/board
ln -sf /etc/nginx/sites-available/board /etc/nginx/sites-enabled/board
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "== TLS"
# Сертификат выпускается только на те имена, которые действительно указывают
# сюда: certbot с несколькими -d падает целиком, если не проходит хотя бы одно,
# и одна забытая A-запись оставила бы сервис вовсе без TLS.
MINE="$(hostname -I | awk '{print $1}')"
CERT_ARGS=""
for host in "${DOMAIN}" "api.${DOMAIN}" "view.${DOMAIN}"; do
    resolved="$(getent hosts "$host" | awk '{print $1}' | head -1 || true)"
    if [ "$resolved" = "$MINE" ]; then
        CERT_ARGS="${CERT_ARGS} -d ${host}"
    else
        echo "   ${host} -> ${resolved:-нет записи}, пропускается"
    fi
done
if [ -n "$CERT_ARGS" ]; then
    # shellcheck disable=SC2086
    certbot --nginx ${CERT_ARGS} --non-interactive --agree-tos \
            --register-unsafely-without-email || echo "   certbot не отработал"
else
    echo "   ни одно имя не указывает сюда, TLS пропущен"
fi

echo "== крон: бэкап два раза в неделю, tick каждые пять минут"
install -m 0644 "$(dirname "$0")/cron/board" /etc/cron.d/board

echo
echo "Готово. Проверьте ВТОРЫМ соединением, не закрывая текущее:"
echo "  ssh -p ${SSH_PORT} ${ADMIN_USER}@${DOMAIN}"
echo
echo "Дальше вручную:"
echo "  1. снимок VDS"
echo "  2. .env с правами 600 (HMAC_SECRET, BASE_URL, TELEGRAM_*)"
echo "  3. docker compose -f deploy/docker-compose.yml up -d --build"
echo "  4. проверить: curl -s https://api.${DOMAIN}/stats"
