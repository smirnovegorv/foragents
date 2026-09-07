# Один контейнер, непривилегированный пользователь, read-only корень (§12).
FROM python:3.12-slim

RUN useradd -u 10001 -m board
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# tick.py появляется в фазе 3 (§15) и добавляется сюда тогда же.

# git-хэш версии кода пишется в каждое сообщение (§1). В образе git нет,
# поэтому значение передаётся сборкой.
ARG CODE_REV=unknown
ENV CODE_REV=${CODE_REV}

USER board
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
