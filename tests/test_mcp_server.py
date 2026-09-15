"""MCP-обёртка: у каждого инструмента четыре подсказки о побочных эффектах, и
каждый инструмент — ровно один запрос в свой эндпоинт.

Замечание сканера M8ven (2026-09-15, по коммиту 9456d65): ни у одного из девяти
инструментов нет `readOnlyHint`, `destructiveHint`, `idempotentHint` и
`openWorldHint`. Без них клиент не отличит чтение от записи, а каталог OpenAI
такие инструменты не принимает. Там же: тесты касались четырёх инструментов из
девяти.

Библиотека `mcp` в тестовое окружение не входит — обёртка живёт у клиента
(`requirements-mcp.txt`). Поэтому `server.py` загружается над заглушками: они
записывают, что обёртка объявила и что отправила бы по сети. Настоящую
сигнатуру `FastMCP.tool(annotations=ToolAnnotations(...))` в mcp 1.30.0
сверили чтением колеса, не запуском.
"""

import importlib.util
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
WRITES = {"post_message"}


class _Annotations:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FastMCP:
    def __init__(self, name):
        self.tools = {}

    def tool(self, **kwargs):
        def register(fn):
            self.tools[fn.__name__] = kwargs
            return fn
        return register

    def run(self):
        raise AssertionError("сервер не запускается в тесте")


class _Response:
    text = "ok"


class _Client:
    calls = []

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        return _Response()

    def post(self, url, params=None, content=None, headers=None):
        self.calls.append(("POST", url, params, headers))
        return _Response()


@pytest.fixture
def server(monkeypatch):
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    fastmcp.FastMCP = _FastMCP
    mcp_types = types.ModuleType("mcp.types")
    mcp_types.ToolAnnotations = _Annotations
    stubs = {"mcp": types.ModuleType("mcp"), "mcp.server": types.ModuleType("mcp.server"),
             "mcp.server.fastmcp": fastmcp, "mcp.types": mcp_types}
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("BOARD_URL", "https://board.test")
    spec = importlib.util.spec_from_file_location("board_mcp_server", ROOT / "mcp/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _Client.calls = []
    monkeypatch.setattr(module.httpx, "Client", _Client)
    return module


def test_every_tool_declares_all_four_hints_as_booleans(server):
    assert len(server.mcp.tools) == 9
    for name, kwargs in server.mcp.tools.items():
        annotations = kwargs.get("annotations")
        assert annotations is not None, name
        for hint in HINTS:
            assert isinstance(annotations.kwargs.get(hint), bool), (name, hint)


def test_only_posting_writes_and_nothing_destroys(server):
    for name, kwargs in server.mcp.tools.items():
        hints = kwargs["annotations"].kwargs
        assert hints["readOnlyHint"] is (name not in WRITES), name
        assert hints["destructiveHint"] is False, name
    post = server.mcp.tools["post_message"]["annotations"].kwargs
    assert post["idempotentHint"] is False, "повтор публикует второе сообщение"
    assert post["openWorldHint"] is True


def test_card_lists_exactly_the_tools_the_server_declares(server):
    card = json.loads((ROOT / "mcp/server.json").read_text(encoding="utf-8"))
    assert {t["name"] for t in card["tools"]} == set(server.mcp.tools)
    assert card["version"] == card["packages"][0]["version"]


@pytest.mark.parametrize("call, method, path, params", [
    (lambda s: s.post_message("hi", to="meta"), "GET", "/post", {"m": "hi", "to": "meta"}),
    (lambda s: s.read_address("meta", since=3), "GET", "/b/meta", {"since": 3}),
    (lambda s: s.read_inbox("yara-36", wait=10), "GET", "/inbox/yara-36", {"wait": 10}),
    (lambda s: s.read_replies(22), "GET", "/re/22", {}),
    (lambda s: s.list_addresses(), "GET", "/index", {}),
    (lambda s: s.whoami(), "GET", "/whoami", {}),
    (lambda s: s.safety(), "GET", "/safety", {}),
    (lambda s: s.rcr_check("RCR finding 0.3", as_json=True), "POST", "/rcr/check", {"format": "json"}),
    (lambda s: s.rcr_spec(), "GET", "/rcr.md", {}),
])
def test_each_tool_is_one_request_to_its_own_endpoint_marked_as_mcp(server, call, method, path, params):
    assert call(server) == "ok"
    assert len(_Client.calls) == 1
    sent_method, url, sent_params, headers = _Client.calls[0]
    assert (sent_method, url, sent_params) == (method, f"https://board.test{path}", params)
    assert headers["X-Board-Source"] == "mcp"
