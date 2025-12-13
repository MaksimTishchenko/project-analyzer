from __future__ import annotations

import os
import uvicorn


def run() -> None:
    port = int(os.getenv("PORT", "8001"))  # 8001 по умолчанию (у тебя часто занят 8000)
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=True)
