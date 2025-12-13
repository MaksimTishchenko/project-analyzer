# Project Analyzer (Python)

Сервис, который анализирует Python-проект и возвращает:

* структуру проекта (модули → классы → методы, функции, импорты),
* стек технологий (по импортам и dependency-файлам),
* диаграмму классов в формате **PlantUML** или **Mermaid**,
* удобный REST API для локальных проектов и (опционально) GitHub-репозиториев.

## Возможности

### Анализ локального проекта

* Рекурсивное сканирование (с игнорированием служебных папок и мусора).
* AST-парсинг Python-кода.
* Генерация диаграммы классов (PlantUML/Mermaid).
* “Top-N” диаграмма через `diagram_max_classes`, чтобы не получить “ковёр” на больших кодовых базах.

### Анализ GitHub (опционально)

* Эндпоинт `/analyze/github` и `/analyze/github/diagram`.
* Клонирование может быть выключено флагом (безопасный режим демо).

---

## Требования

* Python 3.10+ (рекомендуется 3.11+)
* Poetry

Для предпросмотра PlantUML в IDE:

* PyCharm + плагин **PlantUML Integration**
* Java (JDK 17+ рекомендуется)
* Graphviz (опционально, но полезно)

---

## Установка

```bash
poetry install
```

---

## Запуск API

```bash
poetry run uvicorn main:app --reload --port 8081
```

Документация Swagger:

* `http://127.0.0.1:8081/docs`

---

## Эндпоинты

### Health-check

`GET /health`

Ответ:

```json
{"status":"ok"}
```

---

## Анализ локального проекта

### 1) Полный JSON-результат

`POST /analyze/local`

Пример запроса (PowerShell):

```powershell
$body = @{
  path = "D:\Home_works\project-analyzer"
  include_tech_stack = $true
  diagram_format = "plantuml"       # "plantuml" | "mermaid"
  diagram_max_classes = 15          # top-N classes for diagram
  diagram_group_by_module = $true
  diagram_public_only = $false
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8081/analyze/local" `
  -ContentType "application/json" `
  -Body $body
```

В ответе есть ключи (упрощённо):

* `meta` — время генерации и опции,
* `scan` — статистика сканирования, dependency-файлы,
* `summary` — счётчики (modules/classes/functions/methods/imports),
* `tech_stack` — стек (если включено),
* `diagram` — `{format, text}`,
* `project_model` — JSON-структура проекта.

---

### 2) Только диаграмма как текст (удобно копировать)

`POST /analyze/local/diagram`

Пример (PlantUML → `.puml`):

```powershell
$body = @{
  path = "D:\Home_works\project-analyzer"
  diagram_format = "plantuml"
  diagram_max_classes = 15
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8081/analyze/local/diagram" `
  -ContentType "application/json" `
  -Body $body `
  | Out-File diagram_small.puml -Encoding utf8
```

Пример (Mermaid → `.mmd`):

```powershell
$body = @{
  path = "D:\Home_works\project-analyzer"
  diagram_format = "mermaid"
  diagram_max_classes = 15
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8081/analyze/local/diagram" `
  -ContentType "application/json" `
  -Body $body `
  | Out-File diagram_small.mmd -Encoding utf8
```

---

## Анализ GitHub 

### 1) Полный JSON-результат

`POST /analyze/github`

```powershell
$body = @{
  repo_url = "https://github.com/psf/requests"
  ref = $null                      # optional: branch/tag/commit
  include_tech_stack = $true
  diagram_format = "plantuml"
  diagram_max_classes = 25
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8081/analyze/github" `
  -ContentType "application/json" `
  -Body $body
```

### 2) Только диаграмма как текст

`POST /analyze/github/diagram`

```powershell
$body = @{
  repo_url = "https://github.com/psf/requests"
  diagram_format = "plantuml"
  diagram_max_classes = 25
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8081/analyze/github/diagram" `
  -ContentType "application/json" `
  -Body $body
```

> Примечание: клонирование/кэширование может быть ограничено настройками (см. `app/settings.py`).

---

## Просмотр PlantUML в PyCharm

1. Установи плагин **PlantUML Integration**:

   * `Settings → Plugins → Marketplace → PlantUML Integration`
2. Убедись, что Java доступна:

   ```powershell
   java -version
   ```
3. (Опционально) Установи Graphviz и проверь:

   ```powershell
   dot -V
   ```
4. Сгенерируй файл:

   * `diagram_small.puml`
5. Открой файл в PyCharm → вкладка/панель **Preview**.

---

## Рекомендованные режимы диаграмм

  * `diagram_max_classes = 8..15`
  * `diagram_public_only = true`
  * `diagram_group_by_module = false` (если “пакеты” мешают читать)

* Для анализа (внутреннего):

  * `diagram_max_classes = 40..0` (0 = без лимита, если поддержано)
  * `diagram_group_by_module = true`

---

## Тесты

```bash
poetry run pytest
```

---

## Структура проекта (кратко)

* `app/file_scanner.py` — поиск файлов проекта + статистика
* `app/code_parser.py` — AST-парсер → ProjectModel
* `app/tech_stack_analyzer.py` — зависимости/импорты → tech stack
* `app/diagram_generator.py` — PlantUML диаграмма
* `app/diagram_generator_mermaid.py` — Mermaid диаграмма
* `app/service.py` — пайплайн анализа (local/github)
* `main.py` — FastAPI API

---
## Архитектура и идеология проекта

### Зачем нужен этот инструмент

Проект Analyzer создавался как **инженерный инструмент анализа Python-кода**, а не как генератор документации “ради картинки”.

Основная цель — **понять структуру проекта**:

* какие модули и классы в нём есть,
* как они связаны между собой,
* какие технологии и библиотеки используются,
* где находятся ключевые точки ответственности.

---

### Детерминированный анализ (AST-first подход)

В основе анализатора лежит **статический анализ AST (Abstract Syntax Tree)**.

Это означает, что:

* код **не исполняется**;
* результат анализа **детерминирован** и воспроизводим;
* отсутствуют побочные эффекты и риски запуска чужого кода;
* анализ одинаково работает для локальных проектов и GitHub-репозиториев.

AST используется для:

* извлечения классов, функций и методов;
* определения наследования;
* анализа импортов;
* построения базовой модели проекта (`ProjectModel`).

Такой подход делает инструмент:

* безопасным,
* предсказуемым,
* расширяемым.

---

### Диаграммы как средство анализа, а не самоцель

Полная диаграмма классов для реального проекта почти всегда **нечитаема**.
Поэтому в проекте используется принцип **top-N архитектурных диаграмм**.

Идея проста:

* вместо отображения *всего* проекта,
* отображаются **наиболее значимые классы**.

Значимость класса определяется эвристикой:

* количество методов,
* участие в наследовании,
* наличие связей (композиция/агрегация).

Это позволяет:

* получить читаемую диаграмму даже для больших проектов;
* быстро понять архитектуру;
* использовать диаграмму для презентаций и защиты проекта.

---

### Форматы диаграмм

Поддерживаются два текстовых формата:

* **PlantUML** — для классических UML-диаграмм и IDE-просмотра;
* **Mermaid** — для Markdown и документации.

Генерация происходит **без LLM**, строго на основе модели проекта.
Это гарантирует:

* воспроизводимость,
* контроль над результатом,
* отсутствие “галлюцинаций”.

---

### Роль LLM 

Интеграция LLM рассматривается **как надстройка**, а не как ядро системы.

Планируемые сценарии:

* генерация текстовых архитектурных описаний;
* пояснение связей между модулями;
* summarization результатов анализа.

При этом:

* LLM **не заменяет** AST-анализ;
* LLM работает **только поверх уже построенной структуры**.

Такой подход сохраняет инженерную строгость системы.

---

### Общий пайплайн

Анализ проекта выполняется в несколько чётко разделённых этапов:

1. **FileScanner** — поиск и фильтрация файлов проекта
2. **CodeParser** — AST-анализ и построение `ProjectModel`
3. **TechStackAnalyzer** — определение используемых технологий
4. **DiagramGenerator** — генерация диаграмм (PlantUML / Mermaid)
5. **API слой (FastAPI)** — доступ к анализу через REST

Каждый этап изолирован и может быть:

* расширен,
* протестирован,
* заменён без переписывания всего проекта.

---

### Почему это важно

Такая архитектура:

* отражает реальные инженерные практики;
* масштабируется на большие проекты;
* подходит как для обучения, так и для практического использования;
* демонстрирует понимание архитектуры, а не только синтаксиса Python.
