"""Хук запрета записи за пределы репозитория (`.claude/hooks/`).

Почему это лежит в тестах проекта, а не в комментарии к настройке. Хук —
единственная защита машины оператора, которая работает, даже если агента
полностью убедили: его исполняет обвязка, а не суждение агента. Значит его
поломка обязана валить сборку, а не обнаруживаться в тот день, когда он
понадобился.

Первая версия хука была на shell и молча ничего не запрещала: на машине
оператора нет `jq`. Молчащий хук хуже отсутствующего — он создаёт уверенность.
Отсюда правило: проверка хука на реальных полезных нагрузках, а не на глазок.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import re

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "hooks" / "deny_outside_writes.py"
MEMORY = (Path.home().resolve() / ".claude" / "projects"
          / re.sub(r"[:\\/.]", "-", str(REPO)) / "memory")

# Экранирование в PowerShell собирается кодом: обратный слэш в литерале теста
# читается хуже, чем сам смысл проверки.
PS_HOME_WRITE = 'Set-Content -Path "$env:USERPROFILE' + chr(92) + 'x.txt" -Value 1'

CASES = [
    # (что проверяем, ожидаемое решение, полезная нагрузка)
    ("запись в репозиторий", "allow",
     {"tool_name": "Write", "tool_input": {"file_path": str(REPO / "docs" / "SECURITY.md")}}),
    ("запись в профиль оператора", "deny",
     {"tool_name": "Write", "tool_input": {"file_path": r"C:\Users\smirn\evil.txt"}}),
    ("запись в систему", "deny",
     {"tool_name": "Write", "tool_input": {"file_path": r"C:\Windows\System32\drivers\etc\hosts"}}),
    ("обход через ..", "deny",
     {"tool_name": "Write", "tool_input": {"file_path": "docs/../../escape.txt"}}),
    ("правка собственной охраны", "ask",
     {"tool_name": "Write", "tool_input": {"file_path": str(REPO / ".claude" / "settings.json")}}),
    ("перенаправление в домашний каталог", "deny",
     {"tool_name": "Bash", "tool_input": {"command": "echo x > ~/evil.txt"}}),
    ("копирование в другой проект", "deny",
     {"tool_name": "Bash", "tool_input": {"command": "cp app/main.py /c/Prog/other/main.py"}}),
    ("удаление в профиле", "deny",
     {"tool_name": "Bash", "tool_input": {"command": "rm -rf /c/Users/smirn/Documents"}}),
    ("домашний каталог из переменной окружения", "deny",
     {"tool_name": "PowerShell", "tool_input": {"command": PS_HOME_WRITE}}),
    # Разрешённое: запрет касается записи, а не чтения, и не мешает работать.
    ("чтение за пределами репозитория", "allow",
     {"tool_name": "Bash", "tool_input": {"command": "cat /c/Windows/System32/drivers/etc/hosts"}}),
    ("дозапись в документ репозитория", "allow",
     {"tool_name": "Bash", "tool_input": {"command": "cat >> docs/JOURNAL.md"}}),
    ("прогон тестов", "allow",
     {"tool_name": "Bash", "tool_input": {"command": ".venv/Scripts/python.exe -m pytest -q"}}),
    ("коммит", "allow",
     {"tool_name": "Bash", "tool_input": {"command": "git add -A && git commit -m x"}}),
    # Ложная срабатка, найденная проверкой до установки: в `https://example.com`
    # регулярка видела «диск s:» и запрещала любое перенаправление рядом с URL.
    ("URL рядом с перенаправлением", "allow",
     {"tool_name": "Bash", "tool_input": {"command": "curl -s https://getpostingboard.dev/b > /dev/null"}}),
    ("скретчпад сессии", "allow",
     {"tool_name": "Write",
      "tool_input": {"file_path": r"C:\Users\smirn\AppData\Local\Temp\claude\x\y.txt"}}),
    # Хук ограничивает запись; чтение чужих досок и файлов он не трогает.
    ("инструмент чтения не затронут", "allow",
     {"tool_name": "Read", "tool_input": {"file_path": r"C:\Users\smirn\anything.txt"}}),
    # Найдено эксплуатацией, а не разбором: `2>&1` содержит `>`, и это делало
    # пишущей любую команду, где оно стоит. Перенаправление в дескриптор
    # записью в файл не является.
    ("перенаправление дескриптора при чтении", "allow",
     {"tool_name": "Bash", "tool_input": {"command": "ls -la /c/Windows 2>&1 | head"}}),
    # Каталог памяти проекта лежит вне репозитория, и поручение оператора
    # «сохрани правило в память» без этого исключения невыполнимо.
    ("каталог памяти этого проекта", "allow",
     {"tool_name": "Write", "tool_input": {"file_path": str(MEMORY / "rule.md")}}),
    ("память чужого проекта", "deny",
     {"tool_name": "Write",
      "tool_input": {"file_path": str(MEMORY.parent.parent / "other" / "memory" / "x.md")}}),
    # Третья ложная срабатка того же класса, все найдены в работе: форма
    # `/x/...` — соглашение Git Bash о дисках, и для любой буквы она путём не
    # является. `/b/format` — адрес на нашей же доске.
    ("адрес доски рядом с записью", "allow",
     {"tool_name": "Bash",
      "tool_input": {"command": "echo 'сообщение в /b/format' >> docs/JOURNAL.md"}}),
    ("несуществующий диск в пути", "allow",
     {"tool_name": "Bash", "tool_input": {"command": "echo x > notes-/q/whatever.txt"}}),
    # Охрана обязана защищаться от всех путей, а не только от Write и Edit:
    # дверь рядом с охраной — то же, что отсутствие охраны.
    ("правка охраны через оболочку", "ask",
     {"tool_name": "Bash", "tool_input": {"command": "echo x > .claude/settings.json"}}),
]


def decide(payload: dict) -> str:
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return "allow"  # молчание хука означает «не возражаю»
    answer = json.loads(result.stdout)
    return answer["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize("name,expected,payload", CASES, ids=[c[0] for c in CASES])
def test_hook_decision(name, expected, payload):
    assert decide(payload) == expected


def test_hook_fails_closed_on_garbage():
    """Сбой в охране не открывает дверь: на неразбираемом входе хук просит
    решение оператора, а не пропускает молча."""
    result = subprocess.run(
        [sys.executable, str(HOOK)], input="not json at all",
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_hook_is_wired_into_project_settings():
    """Проверенный, но не подключённый хук не защищает ничего."""
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]
    wired = [e for e in entries
             if any("deny_outside_writes.py" in h.get("command", "") for h in e["hooks"])]
    assert wired, "хук отсутствует в .claude/settings.json"
    matcher = wired[0]["matcher"]
    for tool in ("Write", "Edit", "NotebookEdit", "Bash", "PowerShell"):
        assert tool in matcher, f"{tool} не покрыт matcher-ом: дверь рядом с охраной"
