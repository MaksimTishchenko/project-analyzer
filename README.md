# Project Analyzer (Python)

Сервис, который **статически анализирует Python-проект** и возвращает:

* структуру проекта: `модули → классы → методы`, функции, импорты,
* **tech stack** (по импортам и dependency-файлам),
* **диаграмму классов** в формате **PlantUML** или **Mermaid**,
* REST API на **FastAPI** для анализа **локальных проектов** и (опционально) **GitHub** репозиториев.

---

## Что уже сделано (по проекту)

### 1) Сканирование файлов (FileScanner)

* Рекурсивный обход проекта.
* Игнор “мусорных” директорий (`.git`, `__pycache__`, `.venv`, `node_modules`, `.idea`, и т.п.).
* Опциональная поддержка **.gitignore** (через `pathspec`, если установлен; иначе — fallback).
* Пропуск:

  * явных бинарных расширений (`.png`, `.zip`, `.exe`, `.pdf`, …),
  * симлинков (по умолчанию),
  * слишком больших файлов (лимит в конфиге).
* На выходе: список `.py` + обнаруженные dependency-файлы (`requirements.txt`, `pyproject.toml`, `setup.cfg`) + статистика.

### 2) AST-парсер (CodeParser)

* Парсит `.py` через `ast`.
* Достаёт:

  * классы, базовые классы (наследование),
  * методы (включая декораторы и `lineno`),
  * top-level функции (включая `async`),
  * импорты,
  * атрибуты (включая `self.x = ...`) + простые композиции/агрегации по эвристикам.

### 3) TechStackAnalyzer

* Собирает зависимости из:

  * импортов (из AST),
  * `requirements.txt`,
  * `pyproject.toml` (Poetry deps / groups).
* Выдаёт структурированный JSON: пакеты, категории, “сигналы”, оценка типа проекта (web/ml/cli/scientific).

### 4) DiagramGenerator

* Генерирует диаграмму классов:

  * PlantUML **или** Mermaid
  * наследование,
  * композиция/агрегация (если извлечено).
* Поддерживает **Top-N** классов через `diagram_max_classes`, чтобы не получить “ковёр” на больших репозиториях.

### 5) FastAPI слой

* `GET /health`
* `POST /analyze/local` и `POST /analyze/local/diagram`
* `POST /analyze/github` и `POST /analyze/github/diagram` (клонирование по умолчанию выключено настройками)

---

## Требования (локальный запуск без Docker)

* Python **3.12** (проект в Poetry заявлен на 3.12)
* Poetry

Установка:

```bash
poetry install
```

Запуск API:

```bash
poetry run uvicorn main:app --reload --port 8081
```

Swagger:

* [http://127.0.0.1:8081/docs](http://127.0.0.1:8081/docs)

Тесты:

```bash
poetry run pytest
```

---

## Docker (рекомендуемый способ)

### 1) Запуск через Docker Compose

```bash
docker compose up --build
```

После старта API будет доступен на порту, который проброшен в `docker-compose.yml`
(часто это `http://127.0.0.1:8000` или `http://127.0.0.1:8081` — смотри compose).

### Важно про пути внутри контейнера (твоя ситуация с пустым `/workspace`)

Если ты вызываешь `/analyze/local` с `path="/workspace"`, но **не примонтировал туда код**, FileScanner реально увидит “пустую” папку → `python_files: []`.

Правильная схема:

* примонтировать свой проект в контейнер, например в `/workspace`,
* в запросе указывать **путь внутри контейнера**, например `/workspace/mini_project` или `/workspace`.

Пример (если в `docker-compose.yml` сделано что-то вроде):

```yaml
volumes:
  - ./:/workspace
```

Тогда запросы должны выглядеть так:

* `path: "/workspace"` — чтобы анализировать **весь** примонтированный репозиторий
* или `path: "/workspace/mini_project"` — чтобы анализировать конкретную папку

---

## API

### Health-check

`GET /health`

Ответ:

```json
{"status":"ok"}
```

---

## Анализ локального проекта

### 1) Полный JSON

`POST /analyze/local`

Тело запроса:

```json
{
  "path": "/workspace/mini_project",
  "use_llm": false,
  "include_tech_stack": true,
  "diagram_group_by_module": true,
  "diagram_public_only": false,
  "diagram_format": "plantuml",
  "diagram_max_classes": 40
}
```

### 2) Только диаграмма как текст

`POST /analyze/local/diagram`

То же тело запроса, но ответ будет **PlainText**:

* PlantUML: `text/vnd.plantuml`
* Mermaid: `text/markdown`

---

## Анализ GitHub (опционально)

### 1) Полный JSON

`POST /analyze/github`

```json
{
  "repo_url": "https://github.com/psf/requests",
  "ref": null,
  "include_tech_stack": true,
  "diagram_format": "plantuml",
  "diagram_max_classes": 25,
  "diagram_group_by_module": true,
  "diagram_public_only": false
}
```

### 2) Только диаграмма

`POST /analyze/github/diagram`

> ⚠️ Важно: клонирование GitHub **по умолчанию выключено** настройками и вернёт 501 (“не реализовано”), пока не включишь флаг.

Настройки см. `app/settings.py`:

* `github_fetcher_allow_clone` (по умолчанию `False`)
* `github_fetcher_workspace_dir` (кэш)
* `github_fetcher_timeout_sec`
* `github_fetcher_cache_ttl_hours`

---

## Настройки (ENV / .env)

Проект использует `pydantic-settings`, читается `.env` (если есть).

### GitHub fetcher

* `GITHUB_FETCHER_ALLOW_CLONE=true|false`
* `GITHUB_FETCHER_WORKSPACE_DIR=.cache/repos`
* `GITHUB_FETCHER_TIMEOUT_SEC=180`
* `GITHUB_FETCHER_CACHE_TTL_HOURS=72`

### LLM 

По умолчанию LLM выключен

* `LLM_ENABLED=true|false`
* `LLM_API_BASE=http://localhost:1234` **или** `https://api.openai.com`
* `LLM_API_KEY=...` (для локальных обычно не нужен)
* `LLM_MODEL=gpt-4.1-mini` (или любое имя, которое понимает backend)
* `LLM_TIMEOUT_SEC=120`

---

## Просмотр диаграмм

### PlantUML в PyCharm

1. Плагин **PlantUML Integration**
2. Java:

```powershell
java -version
```

3.  Graphviz:

```powershell
dot -V
```

4. Сохрани результат в файл `diagram.puml` → открой Preview.

### Mermaid

Можно вставлять в Markdown-рендереры (GitHub, Mermaid Live Editor и т.п.).

---

## Рекомендованные режимы диаграмм

**Чтобы не было ковра:**

* `diagram_max_classes = 15..25`
* `diagram_public_only = true`
* `diagram_group_by_module = false` (если “пакеты” мешают читать)

**Для анализа (внутреннего):**

* `diagram_max_classes = 40` (или больше)
* `diagram_group_by_module = true`

---

## CI (GitHub Actions)

Рекомендуемый пайплайн:

* поднять Python,
* установить Poetry,
* закэшировать Poetry/pip,
* прогнать:

  * `poetry run pytest`
  * `poetry run black --check .`
  * `poetry run isort --check-only .`

---

## Структура проекта (кратко)

* `app/file_scanner.py` — поиск файлов проекта + статистика
* `app/code_parser.py` — AST-парсер → `ProjectModel`
* `app/tech_stack_analyzer.py` — зависимости/импорты → tech stack
* `app/diagram_generator.py` — PlantUML диаграмма
* `app/diagram_generator_mermaid.py` — Mermaid диаграмма
* `app/github_fetcher.py` — клон GitHub (по флагу, кэш + TTL)
* `app/service.py` — пайплайн анализа (local/github)
* `app/settings.py` — настройки через env/.env
* `main.py` — FastAPI API

---

## Быстрый чек-лист “работает ли всё”

1. `docker compose up --build`
2. Swagger: `/docs`
3. `POST /analyze/local` с `path="/workspace/..."` (именно путь внутри контейнера)
4. В ответе:

   * `summary.modules/classes/...` не нули (для тестового проекта),
   * `diagram.text` содержит `@startuml` … `@enduml` (для PlantUML)
   * `tech_stack` не `null` (если `include_tech_stack=true`)
