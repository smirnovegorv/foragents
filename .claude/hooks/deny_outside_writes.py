"""PreToolUse: запрет записи за пределы репозитория.

Зачем существует. Агент этого проекта читает доски для агентов — текст,
написанный неизвестными сторонами. Такой текст данные, а не инструкции, но
канал ревью кода устроен так, что данные в нём легально выглядят как
инструкции: «вот патч», «воспроизводится таким тестом», «у вас грязное дерево».
Решение агента доверять или не доверять здесь не защита, а надежда. Хук
исполняет обвязка, и уговорить его нельзя.

Границы честности, они же причина, по которой этот файл не считается решением
задачи:

* `Write`, `Edit`, `NotebookEdit` проверяются точно — путь известен целиком.
* `Bash` и `PowerShell` проверяются **эвристикой по тексту команды**. Обойти
  можно: собрать путь из переменных, закодировать, запустить интерпретатор,
  который соберёт путь сам. Это фильтр от очевидного и от случайного.
* Настоящая граница — механизм, принадлежащий кому-то, кем агент не является:
  контейнер, отдельная учётная запись, ACL на этот файл. См. docs/SECURITY.md.

Почему Python, а не shell: `jq` на машине оператора нет, и хук на нём молчал
вместо запрета. Молчащий хук хуже отсутствующего, потому что создаёт
уверенность. Python нужен проекту и так.

Отказ при ошибке. Непредвиденное исключение отдаёт `ask`, а не `allow`: сбой в
охране не должен открывать дверь. И не `deny`, иначе баг здесь останавливает
всю работу — решает оператор.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Корень репозитория выводится из положения самого файла, а не прописан:
# .claude/hooks/deny_outside_writes.py -> parents[2]
REPO = Path(__file__).resolve().parents[2]

# Единственное исключение: скретчпад сессии. Он вне репозитория, но это
# временные файлы обвязки, а не данные оператора. Убрать — удалить строку.
SCRATCH = Path(tempfile.gettempdir()).resolve() / "claude"

# Своя же охрана: правка настроек и хуков не запрещается, а выносится на
# подтверждение оператора. Запретить полностью нельзя — тогда починка бага
# здесь потребует ручной правки; разрешить молча тоже нельзя — охрана, которую
# охраняемый меняет незаметно, ничем не лучше отсутствующей.
GUARDED = REPO / ".claude"

WRITE_VERBS = re.compile(
    r"(>>?|\btee\b|\bcp\b|\bmv\b|\brm\b|\brmdir\b|\bmkdir\b|\btouch\b|\bdd\b"
    r"|\bln\b|\binstall\b|\bchmod\b|\bchown\b|\bicacls\b|\bunzip\b|\btar\b"
    r"|copy-item|move-item|remove-item|new-item|set-content|add-content"
    r"|out-file|clear-content|export-csv)",
    re.I,
)

# Домашний каталог, собранный из переменной окружения, путём не выглядит и мимо
# разбора путей проходит.
HOME_VARS = re.compile(
    r"(\$home\b|\$\{home\}|\$env:userprofile|%userprofile%|\$env:appdata"
    r"|%appdata%|\$env:localappdata|%localappdata%)",
    re.I,
)

# Буква диска не должна цепляться к концу слова: иначе в `https://example.com`
# находится «диск s:» и любое перенаправление вывода рядом с URL запрещается.
# Найдено проверкой хука до его установки, а не после.
PATH_TOKENS = re.compile(
    r"""((?<![A-Za-z0-9])[a-zA-Z]:[\\/][^\s"';|)&]*"""
    r"""|(?<![A-Za-z0-9])/[a-zA-Z]/[^\s"';|)&]*"""
    r"""|~/[^\s"';|)&]*)"""
)

HARMLESS = {"/dev/null", "/dev/stdout", "/dev/stderr", "nul"}


def emit(decision: str, reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    sys.exit(0)


def resolve(raw: str) -> Path:
    """Путь клиента в абсолютный. /c/x -> C:/x, ~ -> домашний, относительный —
    от корня репозитория (рабочий каталог сессии)."""
    text = raw.strip().strip('"').strip("'")
    text = text.replace("\\", "/")
    unix_drive = re.match(r"^/([a-zA-Z])/(.*)$", text)
    if unix_drive:
        text = f"{unix_drive.group(1)}:/{unix_drive.group(2)}"
    if text.startswith("~"):
        text = os.path.expanduser(text)
    path = Path(text)
    if not path.is_absolute():
        path = REPO / path
    # resolve() без strict раскрывает .. и симлинки, не требуя существования.
    return path.resolve()


def inside(path: Path, root: Path) -> bool:
    try:
        return path == root or root in path.parents
    except OSError:
        return False


def check_file_path(raw: str) -> None:
    if not raw:
        return
    path = resolve(raw)
    if inside(path, GUARDED):
        emit(
            "ask",
            f"Правка собственной охраны проекта: {raw}. Это настройки и хуки, "
            "ограничивающие агента, поэтому изменение требует подтверждения "
            "оператора, а не проходит молча.",
        )
    if inside(path, REPO) or inside(path, SCRATCH):
        return
    emit(
        "deny",
        f"Запись за пределы репозитория запрещена правилом проекта: {raw}. "
        f"Разрешён {REPO} и скретчпад сессии. Причина в docs/SECURITY.md: "
        "агент читает недоверенный текст, поэтому его право писать ограничено "
        "механически, а не собственным суждением. Нужную запись выполняет "
        "оператор.",
    )


def check_command(cmd: str) -> None:
    if not cmd or not WRITE_VERBS.search(cmd):
        return  # чтение снаружи не ограничивается, только запись
    if HOME_VARS.search(cmd):
        emit(
            "deny",
            "Команда пишет в путь, собранный из переменной окружения домашнего "
            f"каталога. Правило проекта: запись только в {REPO}.",
        )
    for token in PATH_TOKENS.findall(cmd):
        if token.strip().lower() in HARMLESS:
            continue
        if "://" in token:  # URL, а не путь
            continue
        path = resolve(token)
        if inside(path, REPO) or inside(path, SCRATCH):
            continue
        emit(
            "deny",
            f"Команда содержит путь за пределами репозитория ({token}) вместе "
            f"с операцией записи. Правило проекта: писать можно только в {REPO}. "
            "Если это чтение — уберите из команды пишущие операции; если запись "
            "действительно нужна, её выполняет оператор.",
        )


def main() -> None:
    payload = json.load(sys.stdin)
    tool = payload.get("tool_name") or ""
    data = payload.get("tool_input") or {}
    if tool in ("Write", "Edit", "NotebookEdit"):
        check_file_path(data.get("file_path") or data.get("notebook_path") or "")
    elif tool in ("Bash", "PowerShell"):
        check_command(data.get("command") or "")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — сбой охраны не открывает дверь
        emit(
            "ask",
            f"Хук запрета записи вне репозитория сам упал ({exc!r}), поэтому "
            "решение за оператором. Проверьте .claude/hooks/deny_outside_writes.py.",
        )
