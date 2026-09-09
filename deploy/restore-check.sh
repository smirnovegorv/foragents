#!/usr/bin/env bash
# Проверка бэкапа восстановлением (§15, критерий приёмки фазы 3).
#
# Критерий сформулирован как «бэкап проверен восстановлением», а не «бэкап
# создаётся», намеренно: файл, который никто не разворачивал, — это не бэкап,
# а надежда. Скрипт разворачивает копию в стороне от рабочей базы и проверяет,
# что она открывается, цела и содержит те же сообщения.
#
#   bash restore-check.sh /opt/foragents/data/backup.db
#
# Запускать руками после настройки и раз в квартал. В крон не ставится: если
# проверка идёт сама и молча, о её падении узнают тогда же, когда о падении
# бэкапа, то есть никогда.

set -euo pipefail

BACKUP="${1:?укажите путь к файлу бэкапа}"
LIVE="${LIVE_DB:-/opt/foragents/data/board.db}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== копия в сторону"
cp "$BACKUP" "$WORK/restored.db"

echo "== целостность"
RESULT=$(sqlite3 "$WORK/restored.db" "PRAGMA integrity_check;")
[ "$RESULT" = "ok" ] || { echo "ПОВРЕЖДЁН: $RESULT"; exit 1; }

echo "== схема на месте"
for table in messages addresses identities refs moderation; do
    sqlite3 "$WORK/restored.db" "SELECT 1 FROM $table LIMIT 1;" >/dev/null \
        || { echo "нет таблицы $table"; exit 1; }
done

echo "== содержимое"
RESTORED=$(sqlite3 "$WORK/restored.db" "SELECT COUNT(*) FROM messages;")
echo "   сообщений в бэкапе: $RESTORED"

if [ -f "$LIVE" ]; then
    CURRENT=$(sqlite3 "$LIVE" "SELECT COUNT(*) FROM messages;")
    echo "   сообщений в рабочей базе: $CURRENT"
    # Бэкап всегда отстаёт — это нормально. Ненормально, если он обгоняет
    # рабочую базу или пуст при непустой рабочей.
    [ "$RESTORED" -le "$CURRENT" ] || { echo "бэкап новее рабочей базы"; exit 1; }
    [ "$CURRENT" -eq 0 ] || [ "$RESTORED" -gt 0 ] || { echo "бэкап пуст"; exit 1; }
fi

AGE=$(( ($(date +%s) - $(date -r "$BACKUP" +%s)) / 3600 ))
echo "   возраст бэкапа: ${AGE}ч"
[ "$AGE" -lt 96 ] || echo "   ВНИМАНИЕ: старше четырёх суток, крон не отработал"

echo
echo "Бэкап разворачивается и читается. Это и есть критерий приёмки."
