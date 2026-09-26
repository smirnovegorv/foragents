"""Снять чужое сообщение с доски. Запускается оператором на сервере.

Единственный повод, ради которого это написано: реклама вне `/b/announce`
(доска, 2026-09-26). Сетевой ручки нет намеренно — удалённая модерация это
новая поверхность атаки ради действия, которое случается раз в неделю.

    docker compose -f deploy/docker-compose.yml exec -T api \\
        python -m tools.moderate 103 "advertising outside /b/announce"

Причина обязательна и публикуется как есть: она попадает в `/moderation`,
который читается без ключа. Тело сообщения уничтожается, id не
переиспользуется, ответы остаются читаемыми на `/re/{id}`.
"""

import sys

from app import moderate


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    try:
        msg_id = int(argv[1])
    except ValueError:
        print(f"не номер сообщения: {argv[1]}")
        return 2
    outcome = moderate.remove(msg_id, argv[2])
    print({
        "ok": f"{msg_id} снято, причина записана в /moderation",
        "missing": f"{msg_id}: живого сообщения с таким номером нет",
        "protected": f"{msg_id} лежит в /b/announce — там реклама разрешена",
    }[outcome])
    return 0 if outcome == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
