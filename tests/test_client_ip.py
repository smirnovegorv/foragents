"""Чей адрес считается адресом клиента.

`X-Forwarded-For` клиент присылает сам, и первый элемент в нём — его слова,
а не наблюдение. Настоящий адрес дописывает наш nginx в конец. Приложение
читало первый элемент, поэтому один заголовок менял псевдоним, считал новый
бакет лимита и давал свежую личность для правила «три из двадцати»: один
процесс становился сколькими угодно авторами.

Тест держит именно это: подставленный элемент не должен становиться личностью.
"""


def _name(response) -> str:
    for line in response.text.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(response.text)


def test_forwarded_for_reads_the_hop_our_proxy_added(client):
    honest = _name(client.get("/whoami", headers={"X-Forwarded-For": "198.51.100.7"}))
    forged = _name(client.get("/whoami",
                              headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7"}))
    assert forged == honest, "подставленный первый элемент X-Forwarded-For сменил личность"


def test_a_forged_x_real_ip_does_not_beat_the_chain(client):
    honest = _name(client.get("/whoami", headers={"X-Forwarded-For": "198.51.100.8"}))
    forged = _name(client.get("/whoami",
                              headers={"X-Forwarded-For": "198.51.100.8",
                                       "X-Real-IP": "203.0.113.9"}))
    assert forged == honest, "X-Real-IP, присланный клиентом, перебил адрес из цепочки"
