# main.py
from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Python Project Analyzer", version="0.1.0")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """
    Simple health endpoint to verify that FastAPI service is running.
    """
    return {"status": "ok"}


# For local `python main.py` debug (optional)
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
