from __future__ import annotations

import json
from typing import Optional
from urllib import error as urlerror
from urllib import request as urlrequest

from .settings import settings


class LLMClient:
    """
    Мини-клиент для OpenAI-совместимых `/v1/chat/completions` эндпоинтов.

    Подходит для:
    - облачных провайдеров (OpenAI-совместимые API),
    - локальных серверов (LM Studio / Ollama и т.п.), если они эмулируют этот API.

    Контракт:
    - `is_enabled()` сообщает, можно ли делать запросы (включено + есть api_base + model).
    - `chat(prompt)` отправляет один промпт и возвращает текст ответа.
      При любой проблеме (конфиг/сеть/ответ) бросает RuntimeError.
    """

    def __init__(
        self,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: Optional[int] = None,
    ) -> None:
        # Важно: rstrip("/") чтобы не получить двойной слэш при сборке URL.
        self.api_base = (api_base or settings.llm_api_base or "").rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout_sec = timeout_sec or settings.llm_timeout_sec

    def is_enabled(self) -> bool:
        """
        Возвращает True, если клиент в принципе готов ходить в LLM.

        Используется внешним кодом (например DiagramAI), чтобы:
        - не пытаться делать HTTP, если llm выключен;
        - не ловить исключения там, где можно просто выбрать fallback.
        """
        return bool(settings.llm_enabled and self.api_base and self.model)

    def chat(self, prompt: str) -> str:
        """
        Отправляет один промпт в chat/completions и возвращает `message.content`.

        Ошибки:
        - RuntimeError: LLM выключен/не настроен; сеть/таймаут; не-JSON; неожиданный формат ответа.
        """
        if not self.is_enabled():
            raise RuntimeError("LLM is disabled or not configured (llm_enabled/api_base/model).")

        url = f"{self.api_base}/v1/chat/completions"

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an assistant that helps analyze and refactor UML class diagrams for Python projects."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        data = json.dumps(payload).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urlrequest.Request(url, data=data, headers=headers, method="POST")

        try:
            with urlrequest.urlopen(req, timeout=self.timeout_sec) as resp:
                # Читаем ответ целиком. (Поведение как было: decode utf-8.)
                resp_body = resp.read().decode("utf-8")
        except urlerror.URLError as e:
            # URLError включает и таймауты, и ошибки соединения.
            raise RuntimeError(f"LLM HTTP error: {e}") from e

        try:
            parsed = json.loads(resp_body)
        except json.JSONDecodeError as e:
            # Сохраняем прежний смысл: показать сырой ответ (repr), чтобы было что дебажить.
            raise RuntimeError(f"LLM returned non-JSON response: {resp_body!r}") from e

        try:
            return parsed["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Unexpected LLM response format: {parsed!r}") from e
