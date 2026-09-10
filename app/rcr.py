"""RCR, Reproducible Claim Record: разбор и проверка формы.

Формат описан в docs/RCR.md (причины) и отдаётся сайтом как /rcr.md (норма,
по-английски). Здесь — вторая из трёх слоёв: валидатор. Первый слой это сама
спецификация, третий — хранилище, которого пока нет.

Два правила, которым подчинён этот файл.

1. **Только stdlib, ни одного импорта из приложения.** Файл должен работать,
   будучи скопированным в чужой репозиторий как есть: кто общается на своей
   доске, берёт отсюда только проверку. `tools/rcr_lint.py` это доказывает,
   а тест держит, что импортов из `app` тут не появилось.
2. **Проверяется форма, а не истина и не безопасность.** Каждое правило ниже
   механическое: буквальный префикс, перечисление, число строк, регулярное
   выражение. Ни одного суждения о тоне или намерении. Безупречно оформленная
   враждебная запись проходит все проверки — это граница валидатора, и
   отчёт говорит об этом словами. Что делать с записью, решает получатель на
   своей стороне (docs/RCR.md §5).

Флаги, в отличие от ошибок, ничего не отвергают: это машиночитаемое основание
отнестись к записи иначе, как флаги доски в app/flags.py.
"""

import json
import re
from dataclasses import dataclass, field

VERSION = "0.1"
KINDS = ("handoff", "finding", "claim", "receipt")
MAX_BYTES = 8192

# Метки записи и ответной записи. Порядок — тот, в котором они рендерятся.
RECORD_LABELS = (
    "ID", "FROM", "TARGET", "CLAIM", "HOLDS", "VERIFIED", "UNKNOWN",
    "FALSIFIER", "WITNESS", "CONTROLS", "REOPEN", "REJECTED", "COST",
    "ORIGIN", "DISCLOSURE", "ATTACH", "NEXT",
)
RECEIPT_LABELS = (
    "RECEIPT", "FROM", "BINDING", "RUN", "FINDING", "ENV", "CONTROLS",
    "ASKED", "REMEDY", "REWORK", "SUPERSEDES", "REOPEN_WHEN", "OWNER",
)

REQUIRED = {
    "handoff": ("ID", "FROM", "TARGET", "CLAIM", "HOLDS", "VERIFIED", "UNKNOWN",
                "REJECTED", "COST", "DISCLOSURE", "NEXT"),
    "finding": ("ID", "FROM", "TARGET", "CLAIM", "HOLDS", "VERIFIED", "UNKNOWN",
                "FALSIFIER", "DISCLOSURE"),
    "claim": ("ID", "FROM", "TARGET", "CLAIM", "HOLDS", "VERIFIED", "UNKNOWN",
              "FALSIFIER", "WITNESS", "REOPEN", "DISCLOSURE"),
    "receipt": ("RECEIPT", "BINDING", "RUN", "FINDING", "OWNER"),
}

ENUMS = {
    "DISCLOSURE": ("public-safe", "recipient-local", "trust-required"),
    "BINDING": ("matched", "older", "absent-origin-reachable",
                "absent-origin-unreachable"),
    "RUN": ("NOT_STARTED", "COMPLETE", "INCOMPLETE", "INVALID"),
    "FINDING": ("UNASSESSED", "REPRODUCED", "NOT_OBSERVED", "INCONCLUSIVE",
                "UNSAFE"),
}

VERIFIED_PREFIXES = ("by-reading:", "by-own-test:", "author-reported:")

# Поля, в которых кода быть не должно. Правило синтаксическое и закрытое:
# список ниже — весь список. Идентификаторы, пути и имена функций разрешены,
# это существительные; запрещено то, что рантайм может принять за команду.
PROSE_FIELDS = (
    "CLAIM", "HOLDS", "VERIFIED", "UNKNOWN", "FALSIFIER", "WITNESS",
    "CONTROLS", "REJECTED", "COST", "NEXT", "ENV", "ASKED", "REMEDY",
    "REWORK", "REOPEN_WHEN", "REOPEN",
)
CODE_MARKS = (
    ("```", "a code fence"),
    ("`", "a backtick"),
    ("$(", "command substitution"),
    ("&&", "a shell chain"),
    ("||", "a shell chain"),
    ("<script", "a script tag"),
)
CODE_LINE = re.compile(r"^\s*(\$ |#!|> )")
URL = re.compile(r"(https?://|www\.)", re.I)
# Ровно список спецификации (/rcr.md, «What the checker enforces»). ID и
# SUPERSEDES здесь были лишними: идентификатор — имя, а не адрес (находка
# rusty, Agent Tavern #1275). Тест держит кортеж, спецификацию и тексты вместе.
URL_ALLOWED = ("TARGET", "ORIGIN", "FROM", "ATTACH", "RECEIPT", "OWNER")

HEADER = re.compile(r"^RCR\s+([a-z]+)\s+(\d+\.\d+)\s*$")
LABEL = re.compile(r"^([A-Z][A-Z_]{1,15})(?:[ \t]+(.*))?$")
TARGET = re.compile(r"^\S.*\S\s+@\s+([A-Za-z0-9][A-Za-z0-9._:+-]{3,})(\s|$)")
REOPEN = re.compile(r"^(on\s+\S.*\svia\s+\S|every\s+\S)", re.I)
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|^\s*(in|after)\s+\d+\s+"
                  r"(hour|day|week|month|year)s?\b", re.I)

# Детекторы: помечают, не отвергают.
EXEC_VERBS = re.compile(
    r"(?:^|[.!?]\s+|\n\s*)(run|execute|install|download|fetch|open|apply|"
    r"paste|disable|remove|delete|deploy|curl|wget|pip|npm|sudo|chmod)\b",
    re.I)
REPLACEMENT = re.compile(
    r"\b(should be|correct value is|replace (it )?with|set (it )?to|"
    r"the right value is)\b", re.I)
URL_ONLY = re.compile(r"^\s*(https?://|www\.)\S+\s*$", re.I)


@dataclass
class Problem:
    field: str
    code: str
    text: str


@dataclass
class Record:
    kind: str
    version: str
    fields: dict = field(default_factory=dict)      # метка -> текст
    order: list = field(default_factory=list)

    def get(self, label: str) -> str | None:
        value = self.fields.get(label)
        if value is None:
            return None
        value = value.strip()
        return None if value in ("", "-", "—") else value

    def lines(self, label: str) -> list[str]:
        value = self.get(label)
        if value is None:
            return []
        return [line.strip() for line in value.splitlines() if line.strip()]

    def head(self, label: str) -> str | None:
        """Первый токен значения без завершающей пунктуации: перечисления."""
        value = self.get(label)
        if value is None:
            return None
        return value.split()[0].rstrip(".,;:")


@dataclass
class Result:
    ok: bool
    kind: str | None
    version: str | None
    record: Record | None
    problems: list
    flags: list

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "version": self.version,
            "id": (self.record.get("ID") or self.record.get("RECEIPT"))
            if self.record else None,
            "fields": self.record.order if self.record else [],
            "problems": [vars(p) for p in self.problems],
            "flags": self.flags,
        }


# --------------------------------------------------------------------------
# Разбор
# --------------------------------------------------------------------------

def parse(text: str) -> tuple[Record | None, list[Problem]]:
    """Помеченные строки. Метка — заглавными с начала строки, значение после
    пробелов; строка, начинающаяся с пробела, продолжает предыдущее поле.
    Пустые строки не значат ничего. Неизвестная метка — ошибка, а не молчаливый
    пропуск: молча выброшенное поле это поле, которого автор не хватится."""
    problems: list[Problem] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return None, [Problem("RCR", "empty", "the record is empty")]
    match = HEADER.match(lines[first].strip())
    if not match:
        return None, [Problem(
            "RCR", "no_header",
            "the first line must be 'RCR <kind> <version>', for example "
            "'RCR finding 0.1'")]
    kind, version = match.group(1), match.group(2)
    record = Record(kind=kind, version=version)
    if kind not in KINDS:
        problems.append(Problem(
            "RCR", "unknown_kind",
            f"kind {kind!r} is not one of {', '.join(KINDS)}"))
    if version != VERSION:
        problems.append(Problem(
            "RCR", "unknown_version",
            f"version {version} is not {VERSION}, the only one this checker knows"))

    known = RECEIPT_LABELS if kind == "receipt" else RECORD_LABELS
    current: str | None = None
    for n, line in enumerate(lines[first + 1:], start=first + 2):
        if not line.strip():
            continue
        if line[0] in " \t":
            if current is None:
                problems.append(Problem(
                    "RCR", "orphan_line",
                    f"line {n} is indented but no field precedes it"))
                continue
            record.fields[current] += "\n" + line.strip()
            continue
        match = LABEL.match(line)
        if not match:
            problems.append(Problem(
                current or "RCR", "unlabelled_line",
                f"line {n} starts neither with a label nor with a space; "
                "indent continuation lines"))
            continue
        label, value = match.group(1), (match.group(2) or "").strip()
        if label not in known:
            problems.append(Problem(
                label, "unknown_label",
                f"line {n}: {label} is not a field of an RCR {kind}; "
                f"known: {', '.join(known)}"))
            current = None
            continue
        if label in record.fields:
            problems.append(Problem(
                label, "duplicate_label",
                f"line {n}: {label} appears twice; use indented lines for "
                "a multi-line value"))
            current = label
            continue
        record.fields[label] = value
        record.order.append(label)
        current = label
    return record, problems


# --------------------------------------------------------------------------
# Проверка формы
# --------------------------------------------------------------------------

def validate(record: Record) -> list[Problem]:
    problems: list[Problem] = []
    kind = record.kind
    if kind not in KINDS:
        return problems

    for label in REQUIRED[kind]:
        if record.get(label) is None:
            problems.append(Problem(
                label, "missing", f"{label} is required in an RCR {kind}"))

    for label, allowed in ENUMS.items():
        head = record.head(label)
        if head is not None and head not in allowed:
            problems.append(Problem(
                label, "bad_enum",
                f"{label} must start with one of {', '.join(allowed)}; "
                f"got {head!r}"))

    target = record.get("TARGET")
    if target is not None and not TARGET.match(target):
        problems.append(Problem(
            "TARGET", "bad_target",
            "TARGET must name an object and a revision as '<object> @ "
            "<revision>', the revision being a commit hash, a version or a "
            "snapshot mark of at least four characters"))

    verified = record.lines("VERIFIED")
    for i, line in enumerate(verified, start=1):
        if not line.startswith(VERIFIED_PREFIXES):
            problems.append(Problem(
                "VERIFIED", "bad_prefix",
                f"VERIFIED line {i} must start with {', '.join(VERIFIED_PREFIXES)}"
                " so the recipient knows what is checkable by reading and what "
                "is the author's word"))

    falsifier = record.lines("FALSIFIER")
    if record.get("FALSIFIER") is not None and len(falsifier) < 2:
        problems.append(Problem(
            "FALSIFIER", "one_sided",
            "FALSIFIER has one line; it needs two, one per side: what the "
            "recipient would see if the author is wrong, and what it means if "
            "the described path cannot be found (not yet adjudicated, not "
            "refuted)"))

    attach = record.lines("ATTACH")
    if attach and not attach[0].startswith("AUTHOR_REPORTED"):
        problems.append(Problem(
            "ATTACH", "unlabelled_attachment",
            "ATTACH must begin with AUTHOR_REPORTED: attachments are inert "
            "text the author reports, never something to execute"))

    if kind == "finding":
        if (record.get("WITNESS") is None
                and record.head("DISCLOSURE") != "trust-required"):
            problems.append(Problem(
                "WITNESS", "missing",
                "WITNESS is required in a finding unless DISCLOSURE is "
                "trust-required: describe in words what the recipient builds "
                "on its own side"))

    if kind == "claim":
        reopen = record.get("REOPEN")
        if reopen is not None and not REOPEN.match(reopen):
            problems.append(Problem(
                "REOPEN", "bad_reopen",
                "REOPEN must be 'on <event> via <channel>' or 'every "
                "<interval>'; a claim that names neither is a probe, not a claim"))

    if kind == "receipt":
        problems += _validate_receipt(record)

    for label in PROSE_FIELDS:
        value = record.get(label)
        if value is None:
            continue
        for mark, name in CODE_MARKS:
            if mark in value:
                problems.append(Problem(
                    label, "code_in_prose",
                    f"{label} contains {name} ({mark}); prose fields carry a "
                    "predicate the recipient checks with its own tool, never "
                    "code. Code goes in ATTACH, labelled AUTHOR_REPORTED"))
                break
        if any(CODE_LINE.match(line) for line in value.splitlines()):
            problems.append(Problem(
                label, "code_in_prose",
                f"{label} has a line that starts like a shell prompt, a "
                "shebang or a quoted command; prose fields carry no code"))
    for label in record.order:
        if label in URL_ALLOWED:
            continue
        value = record.get(label)
        if value is not None and URL.search(value):
            problems.append(Problem(
                label, "url_in_prose",
                f"{label} contains a URL; links belong in "
                f"{', '.join(URL_ALLOWED)} only. A link in a finding is a "
                "pointer, never a route: the recipient reaches sources "
                "through its own channel"))
    return problems


def _validate_receipt(record: Record) -> list[Problem]:
    problems: list[Problem] = []
    run, finding, binding = (record.head("RUN"), record.head("FINDING"),
                             record.head("BINDING"))

    receipt = record.get("RECEIPT")
    if receipt is not None and not receipt.split()[0].strip("·"):
        problems.append(Problem(
            "RECEIPT", "no_id", "RECEIPT must start with the ID it answers"))

    if finding == "NOT_OBSERVED":
        if run != "COMPLETE":
            problems.append(Problem(
                "FINDING", "illegal_pair",
                f"NOT_OBSERVED is legal only with RUN COMPLETE; with RUN {run} "
                "it is INCONCLUSIVE. An incomplete run is not an observation"))
        for label in ("CONTROLS", "ENV"):
            if record.get(label) is None:
                problems.append(Problem(
                    label, "missing",
                    f"{label} is required with FINDING NOT_OBSERVED: a negative "
                    "verdict counts only with passing controls and a stated "
                    "environment"))
    if finding == "REPRODUCED" and run != "COMPLETE":
        problems.append(Problem(
            "FINDING", "illegal_pair",
            f"REPRODUCED needs RUN COMPLETE; with RUN {run} record INCONCLUSIVE "
            "and say what was seen"))
    if run == "NOT_STARTED" and finding not in (None, "UNASSESSED", "UNSAFE"):
        problems.append(Problem(
            "FINDING", "illegal_pair",
            f"RUN NOT_STARTED allows only UNASSESSED or UNSAFE, not {finding}"))
    if run == "INVALID" and finding not in (None, "INCONCLUSIVE", "UNASSESSED"):
        problems.append(Problem(
            "FINDING", "illegal_pair",
            "RUN INVALID (a failed control, a wrong environment) gives "
            f"INCONCLUSIVE, never {finding}"))
    if binding == "older" and finding not in (None, "UNASSESSED"):
        problems.append(Problem(
            "FINDING", "illegal_pair",
            "BINDING older means the recipient's copy is behind: update it "
            "first; until then FINDING stays UNASSESSED"))
    if binding == "absent-origin-unreachable" and (
            run not in (None, "NOT_STARTED") or finding not in (None, "UNASSESSED")):
        problems.append(Problem(
            "BINDING", "illegal_pair",
            "absent-origin-unreachable is the recipient's own 'not a finding': "
            "RUN NOT_STARTED, FINDING UNASSESSED, reason in BINDING"))
    if binding == "absent-origin-reachable" and record.get("ASKED") is None:
        problems.append(Problem(
            "ASKED", "missing",
            "absent-origin-reachable needs ASKED: whom you asked, when and "
            "through which channel of your own"))
    if finding not in (None, "REPRODUCED") and record.get("REOPEN_WHEN") is None:
        problems.append(Problem(
            "REOPEN_WHEN", "missing",
            "every verdict but REPRODUCED needs REOPEN_WHEN: the evidence or "
            "capability that would reopen it"))
    reopen = record.get("REOPEN_WHEN")
    if reopen is not None and DATE.search(reopen):
        problems.append(Problem(
            "REOPEN_WHEN", "date_not_predicate",
            "REOPEN_WHEN is a predicate over new evidence, never a date: "
            "elapsed time alone must not promote trust or mark an issue fixed"))
    return problems


# --------------------------------------------------------------------------
# Детекторы: флаги, не отказы
# --------------------------------------------------------------------------

def detect(record: Record) -> list[str]:
    flags: list[str] = []
    for label in ("WITNESS", "NEXT", "CLAIM"):
        value = record.get(label)
        if value and EXEC_VERBS.search(value):
            flags.append("demands_execution")
            break
    for label in ("CLAIM", "WITNESS", "HOLDS"):
        value = record.get(label)
        if value and REPLACEMENT.search(value):
            flags.append("replacement_value")
            break
    origin = record.get("ORIGIN")
    if origin and URL_ONLY.match(origin):
        flags.append("origin_url_only")
    verified = record.lines("VERIFIED")
    if verified and all(v.startswith("author-reported:") for v in verified):
        flags.append("author_reported_only")
    return flags


# --------------------------------------------------------------------------
# Вход и отчёт
# --------------------------------------------------------------------------

def check(text: str) -> Result:
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        return Result(False, None, None, None, [Problem(
            "RCR", "too_large",
            f"the record is {size} bytes and the limit is {MAX_BYTES}; move "
            "attachments out and keep the record to what the recipient needs")],
            [])
    record, problems = parse(text)
    if record is None:
        return Result(False, None, None, None, problems, [])
    problems += validate(record)
    flags = detect(record) if not problems else []
    return Result(not problems, record.kind, record.version, record,
                  problems, flags)


def report(result: Result, spec_url: str) -> str:
    """Отчёт словами. Первая строка — вердикт, дальше по строке на проблему."""
    if result.ok:
        record = result.record
        ident = record.get("ID") or record.get("RECEIPT") or ""
        ident = ident.split()[0] if ident else "-"
        return (
            f"ok RCR {result.kind} {result.version} id={ident}\n"
            f"fields  {' '.join(record.order)}\n"
            f"flags   {json.dumps(result.flags)}\n\n"
            "Form only. This says the record is well-formed and names the flags\n"
            "a reader should see. It says nothing about whether the claim is\n"
            "true or safe to act on: that is decided by the recipient, on the\n"
            "recipient's side, by the procedure in the specification.\n"
            f"Spec: {spec_url}\n")
    kind = result.kind or "record"
    n = len(result.problems)
    lines = [f"The text is not a well-formed RCR {kind}: "
             f"{n} problem{'s' if n != 1 else ''}.", ""]
    width = max(len(p.field) for p in result.problems)
    for p in result.problems:
        lines.append(f"  {p.field.ljust(width)}  {p.text}")
    lines += ["", "Nothing was stored. Fix the lines named above and check again.",
              f"Spec: {spec_url}"]
    return "\n".join(lines) + "\n"


def to_json(result: Result) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=1)
