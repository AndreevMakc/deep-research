# Deep Research Pipeline

<p align="center">
  <a href="https://github.com/AndreevMakc/deep-research/actions/workflows/quality.yml"><img src="https://github.com/AndreevMakc/deep-research/actions/workflows/quality.yml/badge.svg" alt="Quality checks"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-252B31?logo=python&logoColor=61D6B2" alt="Python 3.11 or newer">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-252B31?logoColor=61D6B2" alt="MIT License"></a>
</p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Deep Research Pipeline связывает каждый вывод с неизменяемым источником, точной цитатой и независимой проверкой">
</p>

<p align="center">
  <strong>Self-hosted pipeline для исследований, которые можно проверить.</strong><br>
  Planner декомпозирует вопрос, Researchers параллельно собирают evidence,
  Verifiers независимо проверяют claims, а Writer формирует отчёт только из
  допустимых выводов.
</p>

<p align="center">
  <a href="#быстрый-старт">Быстрый старт</a> ·
  <a href="#как-устроен-pipeline">Архитектура</a> ·
  <a href="#выбор-llm-провайдера">LLM-провайдеры</a> ·
  <a href="#human-review-и-публикация">Human review</a> ·
  <a href="#multi-user-api">API</a>
</p>

> [!IMPORTANT]
> Проект экспериментальный. Перед применением в критичных процессах
> проверьте модели, thresholds, роли, лимиты и восстановление на своих данных.

## Что делает результат проверяемым

Проект не просит доверять финальному тексту «на слово». Для каждого вывода он
сохраняет цепочку происхождения:

| Контроль | Что фиксируется |
|---|---|
| **Immutable source** | Нормализованный URL, snapshot, SHA-256 и размер |
| **Exact quote** | Дословная цитата, locator, `quote_start` и `quote_end` |
| **Independent verdict** | Отдельный Verifier оценивает смысл, область и числа |
| **Human gate** | Решение reviewer, причина, автор и время до публикации |

Researcher не может сохранить произвольную ссылку или приблизительную цитату:
URL должен присутствовать в source packet, hash и размер snapshot должны
совпасть, а quote — дословно находиться в сохранённом тексте. Writer получает
все verdict для контекста, но строит основные разделы только из допустимых
claims и повторно проверяет inline citations и числовые значения.

Результат одного run хранится как переносимый набор артефактов:

```text
data/runs/<run-id>/
├── report.md          # читаемый отчёт с inline citations
├── report.json        # разделы, источники, ограничения и unanswered questions
└── sources/           # неизменяемые snapshots источников
```

Метаданные, provenance, checkpoints и review state сохраняются в PostgreSQL.

## Как устроен pipeline

<p align="center">
  <img src="./assets/readme/pipeline.svg" width="100%" alt="Вопрос проходит через планирование, параллельный поиск, реестр доказательств, независимую проверку, синтез и human approval">
</p>

```text
Вопрос → План → Параллельный поиск → Evidence ledger
       → Независимая проверка → Отчёт → Human approval → Публикация
```

- **Planner** формирует scope, подзадачи и критерии завершения.
- **Researchers × N** ищут, загружают и сохраняют источники параллельно.
- **Evidence ledger** связывает snapshot, quote и claim до вызова Verifier.
- **Verifiers × N** независимо присваивают verdict каждому claim.
- **Writer** синтезирует Markdown и JSON только из допустимых claims.
- **Reviewer** принимает окончательное решение о публикации.

Параллелизм ограничивается семафорами, прогресс сохраняется через LangGraph
checkpoints, а успешные tasks и verifications не выполняются повторно при
resume.

## Быстрый старт

Понадобятся Python 3.11+, Docker с Compose, ключ выбранного LLM-провайдера и
ключ Tavily.

```bash
git clone https://github.com/AndreevMakc/deep-research.git
cd deep-research

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt

cp .env.example .env
docker compose up -d
```

Заполните минимальную конфигурацию:

```dotenv
LLM_PROVIDER=openai
LLM_API_KEY=...
TAVILY_API_KEY=...

RESEARCH_MODEL=<planner-model>
WORKER_MODEL=<researcher-model>
VERIFIER_MODEL=<verifier-model>
WRITER_MODEL=<writer-model>
```

Подготовьте application-схему и checkpoint-таблицы:

```bash
python -m app.db.migrate
python -m app.checkpoint
```

Запустите первое исследование:

```bash
python -m app.main "Как LangGraph организует параллельное выполнение агентов?"
```

Команда напечатает `Run ID`, итоговый статус и финальный отчёт.

## Выбор LLM-провайдера

Один интерфейс конфигурации работает с облачными и локальными моделями:

| `LLM_PROVIDER` | Интеграция | API key |
|---|---|---|
| `openai` | `langchain-openai` | Требуется |
| `openrouter` | `langchain-openrouter` | Требуется |
| `groq` | `langchain-groq` | Требуется |
| `google` | `langchain-google-genai` | Требуется |
| `ollama` | OpenAI-compatible local endpoint | Не требуется |
| Любое другое имя | OpenAI-compatible `LLM_BASE_URL` | Зависит от endpoint |

`LLM_BASE_URL` всегда переопределяет встроенный URL провайдера.
`OPENAI_API_KEY` поддерживается как legacy fallback для `LLM_API_KEY`.

### Модели по ролям

| Роль | Переменная | Практичный профиль |
|---|---|---|
| Planner | `RESEARCH_MODEL` | Сильное планирование и декомпозиция |
| Researchers | `WORKER_MODEL` | Быстрая и недорогая массовая обработка |
| Verifier | `VERIFIER_MODEL` | Точная проверка цитат, scope и чисел |
| Writer | `WRITER_MODEL` | Стабильный структурированный синтез |

Если `WORKER_MODEL` пуст, используется `RESEARCH_MODEL`.
`VERIFIER_MODEL` наследует `WORKER_MODEL`, затем `RESEARCH_MODEL`.
`WRITER_MODEL` использует ту же цепочку fallback.

<details>
<summary><strong>Примеры конфигурации провайдеров</strong></summary>

```dotenv
# OpenAI
LLM_PROVIDER=openai
LLM_API_KEY=...
RESEARCH_MODEL=<planner-model>
WORKER_MODEL=<worker-model>
VERIFIER_MODEL=<verifier-model>
WRITER_MODEL=<writer-model>

# OpenRouter
LLM_PROVIDER=openrouter
LLM_API_KEY=...
RESEARCH_MODEL=<provider/model>

# Groq
LLM_PROVIDER=groq
LLM_API_KEY=...
RESEARCH_MODEL=<model>

# Google Gemini
LLM_PROVIDER=google
LLM_API_KEY=...
RESEARCH_MODEL=<gemini-model>

# Локальный Ollama
LLM_PROVIDER=ollama
LLM_API_KEY=
RESEARCH_MODEL=qwen3
```

</details>

> [!CAUTION]
> `.env` содержит секреты, игнорируется Git и не должен попадать в коммиты.

## Verdicts и ограничения Writer

Verifier сохраняет один из семи verdict:

- `supported`;
- `partially_supported`;
- `contradicted`;
- `citation_mismatch`;
- `source_unavailable`;
- `insufficient_evidence`;
- `out_of_scope`.

`partially_supported` требует явной оговорки в отчёте. Rejected claims не
могут незаметно попасть в основной текст, а число без подтверждающего evidence
останавливает сборку draft. Финальный JSON отдельно сохраняет ограничения,
противоречия и unanswered questions.

## Восстановление после ошибок

Внешние вызовы используют retry с exponential backoff. Падение одного worker
не уничтожает результаты остальных, а run получает статус `completed`,
`completed_with_errors` или `failed`.

Повторить только незавершённые задачи:

```bash
python -m app.main --resume <run-id>
```

Успешные tasks и уже сохранённые verifications будут переиспользованы.
Ожидаемые ошибки провайдера, конфигурации и БД выводятся без traceback с кодом
возврата `2`; неожиданные программные ошибки сохраняют traceback.

## Human review и публикация

Публикация заблокирована, пока отчёт и все использованные claims не получили
необходимое одобрение.

```bash
python -m app.ops reviewer-add admin "Operations Admin" --role admin
python -m app.ops reviewer-add reviewer "Research Reviewer" \
  --role reviewer --actor admin
python -m app.ops reviewer-add release "Release Operator" \
  --role publisher --actor admin
```

<details>
<summary><strong>Полный review workflow</strong></summary>

Просмотреть provenance:

```bash
python -m app.ops runs --reviewer reviewer
python -m app.ops run <run-id> --reviewer reviewer
python -m app.ops tasks <run-id> --reviewer reviewer
python -m app.ops claims <run-id> --reviewer reviewer
python -m app.ops verifications <run-id> --reviewer reviewer
python -m app.ops report <run-id> --reviewer reviewer
```

Принять, отклонить claim или запросить дополнительное исследование:

```bash
python -m app.ops review-claim approve <claim-id> \
  --reason "Цитата и область утверждения проверены" --reviewer reviewer
python -m app.ops review-claim reject <claim-id> \
  --reason "Источник не подтверждает вывод" --reviewer reviewer
python -m app.ops review-claim research <claim-id> \
  --reason "Нужен независимый первичный источник" --reviewer reviewer
```

После reject пересоберите отчёт. После follow-up task сначала возобновите run:

```bash
python -m app.main --resume <run-id>
python -m app.ops rebuild-report <run-id> --reviewer reviewer
```

Одобрить и опубликовать отчёт:

```bash
python -m app.ops review-report approve <run-id> \
  --reason "Все использованные claims проверены" --reviewer reviewer
python -m app.ops publish <run-id> \
  --reason "Готово к публикации" --reviewer release
```

Экспортировать approved материалы в Obsidian:

```bash
python -m app.ops export-obsidian <run-id> /path/to/vault \
  --reviewer release
```

Любое изменение review-статуса claim сбрасывает approval текущего отчёта.

</details>

## Multi-user API

FastAPI-контур изолирует данные по tenant и поддерживает два способа входа:
hashed bearer tokens для программных клиентов и серверные cookie-сессии для
пользовательского интерфейса. Пароли хешируются через `scrypt`, cookie имеют
`HttpOnly`, `SameSite=Strict` и configurable `Secure`, а mutating запросы из
браузера дополнительно проверяют CSRF token. Durable PostgreSQL queue
поддерживает leases, heartbeats, cancellation и повторный захват потерянных
задач.

```bash
python -m app.tenants create acme "Acme Research"
# Первый пользователь tenant обязан быть admin. Пароль читается без echo.
python -m app.tenants create-user acme admin --role admin

# Только для локального HTTP. В production оставьте true.
export SESSION_COOKIE_SECURE=false
uvicorn app.api:app --host 0.0.0.0 --port 8000
python -m app.worker
python -m app.webhooks
```

Если сначала нужен программный admin, выпустите bearer token вместо
`create-user`, а затем создайте отдельный браузерный аккаунт с
`--actor-token <token>`:

```bash
python -m app.tenants issue-token acme automation --role admin
python -m app.tenants create-user acme admin --role admin \
  --actor-token <token>
```

Создать idempotent run:

```bash
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Authorization: Bearer <token>" \
  -H "Idempotency-Key: create-run-001" \
  -H "Content-Type: application/json" \
  -d '{"question":"Какие доказательства доступны?"}'
```

После запуска доступны:

- OpenAPI — `http://localhost:8000/docs`;
- dashboard с обычной формой входа — `http://localhost:8000/dashboard`;
- liveness — `http://localhost:8000/health/live`;
- readiness — `http://localhost:8000/health/ready`.

Dashboard показывает общую библиотеку активных, готовых и архивных
исследований. Новый вопрос сначала сохраняется как черновик с охватом,
периодом, допущениями и оценкой времени; рабочий run создаётся только после
явного подтверждения. Незавершённый черновик восстанавливается после
обновления страницы. Из dashboard также можно изменить автоматически
созданный заголовок, перенести собственное исследование в архив и увидеть
новый результат; unread-состояние хранится отдельно для каждого пользователя.

Webhook delivery подписывается `X-Deep-Research-Signature` в формате
`sha256=<HMAC>` и повторяется с exponential backoff.

Локальная проверка production-подобного Compose-контура:

```bash
docker compose -f compose.production.yml up --build
```

Это локальный запуск. Production environment и автоматический deployment в
проекте не настроены; CI ничего не публикует и никуда не развёртывает.

## Качество и эксплуатация

Offline evaluation прогоняет 14 контрольных сценариев без LLM API и Tavily:

```bash
python -m app.evaluation
```

Он измеряет verdict accuracy, citation coverage, supported claim rate, exact
quote rate, recovery, дубликаты источников, длительность, внешние запросы и
стоимость. Threshold failure завершает команду с ненулевым кодом.

```bash
# Unit и static checks
python -m unittest discover -s tests
ruff check .
python -m pip check
alembic check

# Health, SLO и trace
python -m app.health live
python -m app.health ready
python -m app.health alerts --window-minutes 60
python -m app.ops events <run-id> --reviewer reviewer
```

GitHub Actions применяет миграции, запускает static analysis, unit/database
smoke tests, offline quality thresholds, SLO checks и проверяет сборку Docker
image без публикации и деплоя.

Подробные процедуры:

- [Правила разработки и pull request](CONTRIBUTING.md)
- [Operations guide](docs/operations.md)
- [Deployment runbook](docs/deployment-runbook.md)
- [Research pipeline roadmap](docs/research-pipeline-roadmap.md)
- [Security policy](SECURITY.md)

<details>
<summary><strong>Все переменные конфигурации</strong></summary>

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `LLM_PROVIDER` | OpenAI, OpenRouter, Groq, Google, Ollama или custom | `openai` |
| `LLM_API_KEY` | Ключ выбранного LLM-провайдера | `OPENAI_API_KEY` |
| `LLM_BASE_URL` | Переопределение OpenAI-compatible API URL | URL провайдера |
| `OPENAI_API_KEY` | Legacy fallback для `LLM_API_KEY` | — |
| `TAVILY_API_KEY` | Ключ Tavily API | — |
| `RESEARCH_MODEL` | Модель Planner | — |
| `WORKER_MODEL` | Модель Researchers | `RESEARCH_MODEL` |
| `VERIFIER_MODEL` | Модель Verifier | `WORKER_MODEL` |
| `WRITER_MODEL` | Модель Writer | `WORKER_MODEL` |
| `DATABASE_URL` | Подключение к PostgreSQL | `postgresql://research:research@localhost:54321/research` |
| `MAX_PARALLEL_RESEARCHERS` | Параллелизм Researchers | `3` |
| `MAX_PARALLEL_VERIFIERS` | Параллелизм Verifiers | `5` |
| `EXTERNAL_MAX_ATTEMPTS` | Попытки внешнего вызова | `3` |
| `RETRY_MIN_WAIT_SECONDS` | Начальная задержка retry | `1` |
| `RETRY_MAX_WAIT_SECONDS` | Максимальная задержка retry | `10` |
| `MAX_EXTERNAL_REQUESTS` | Внешние вызовы на run | `100` |
| `MAX_SOURCES` | Уникальные sources на run | `50` |
| `MAX_CLAIMS` | Claims на run | `100` |
| `MAX_TOKENS` | Оценка входных токенов на run | `200000` |
| `MAX_RUN_SECONDS` | Максимальная длительность run | `3600` |
| `ESTIMATED_INPUT_COST_PER_1M_TOKENS_USD` | Оценка стоимости 1M input tokens | `0` |
| `SLO_MIN_RUN_SUCCESS_RATE` | Минимальная доля успешных runs | `0.95` |
| `SLO_MAX_EXTERNAL_P95_MS` | Максимальный p95 внешнего вызова | `30000` |
| `SLO_MAX_RETRY_RATE` | Максимальная доля retries | `0.10` |
| `TELEMETRY_RETENTION_DAYS` | Retention operational events | `30` |
| `LOG_LEVEL` | Уровень логирования | `INFO` |
| `SESSION_COOKIE_NAME` | Имя HttpOnly session cookie | `dr_session` |
| `CSRF_COOKIE_NAME` | Имя CSRF cookie | `dr_csrf` |
| `SESSION_LIFETIME_DAYS` | Срок сессии доверенного устройства | `30` |
| `SESSION_COOKIE_SECURE` | Отправлять session cookies только по HTTPS | `true` |

</details>

## Статус, поддержка и лицензия

Все этапы текущего roadmap реализованы, но проект сохраняет
**experimental**-статус. Текущую ветку `main` поддерживает
[@AndreevMakc](https://github.com/AndreevMakc). Ошибки и предложения можно
оформлять через GitHub Issues; уязвимости следует отправлять приватно согласно
[Security policy](SECURITY.md).

Исходный код распространяется по [MIT License](LICENSE).
