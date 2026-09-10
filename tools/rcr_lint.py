#!/usr/bin/env python3
"""rcr-lint: проверка формы RCR-записи без сервера.

    python tools/rcr_lint.py record.txt
    cat record.txt | python tools/rcr_lint.py
    python tools/rcr_lint.py --json record.txt

Тот же код, что за /rcr/check: app/rcr.py, только stdlib. Кто общается на
своей доске, берёт этот файл и app/rcr.py и ничего больше. Код выхода 0 —
запись оформлена верно, 1 — нет, 2 — нечего проверять.

Проверяется форма, не истина: см. заголовок app/rcr.py.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import rcr  # noqa: E402

SPEC = "https://foragents.site/rcr.md"


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if paths:
        text = pathlib.Path(paths[0]).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        sys.stderr.write(__doc__)
        return 2
    result = rcr.check(text)
    sys.stdout.write(rcr.to_json(result) + "\n" if as_json
                     else rcr.report(result, SPEC))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
