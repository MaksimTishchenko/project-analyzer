from __future__ import annotations

import os

import uvicorn


def run() -> None:
    """
    Запускает приложение в dev-режиме через Uvicorn.

    Что делает:
    - читает порт из переменной окружения `PORT` (если не задана — использует 8001);
    - стартует ASGI-приложение `main:app` на localhost (127.0.0.1);
    - включает `reload=True`, чтобы сервер автоматически перезапускался при изменениях кода.

    Важно:
    - функция предназначена для локальной разработки и не нацелена на production-запуск.
    """
    port_str = os.getenv("PORT", "8001")
    port = int(port_str)

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=port,
        reload=True,
    )
