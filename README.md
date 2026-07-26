<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="Deep Research Pipeline — evidence-first research with traceable sources, quotes, and verdicts">
</p>

Deep Research Pipeline — экспериментальный self-hosted workflow на Python для
исследований, результаты которых можно проверить. Planner декомпозирует вопрос,
параллельные Researchers собирают источники, независимые Verifiers проверяют
утверждения, а Writer формирует цитируемый отчёт только из допустимых claims.

Каждый вывод можно проследить до неизменяемого snapshot источника, точной цитаты,
её координат и сохранённого verdict. Перед публикацией отчёт проходит human
approval gate.

## Как устроен pipeline

<p align="center">
  <img src="./assets/readme/pipeline.svg" width="100%" alt="От вопроса через параллельный поиск, immutable evidence и независимую верификацию к цитируемому отчёту">
</p>

```text
Question → Plan → Parallel research → Immutable evidence
         → Independent verification → Cited report → Human approval
```

| Инвариант | Что обеспечивает проект |
|---|---|
| Источник воспроизводим | Claim ссылается на immutable `SourceSnapshot` с hash и размером |
| Цитата проверяема | Сохраняются точный текст, locator, `quote_start` и `quote_end` |
| Агент не проверяет себя | Каждый claim проходит отдельного Verifier |
| Публикация контролируема | Human review хранит решение, причину, reviewer и время |

## Быстрый старт

Нужны Python 3.11+, Docker с Compose, ключ OpenAI и ключ Tavily.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d
```

Заполните в `.env` как минимум:

```dotenv
OPENAI_API_KEY=...
TAVILY_API_KEY=...
RESEARCH_MODEL=...
WORKER_MODEL=...
```

`WORKER_MODEL` можно оставить пустым: тогда Researchers используют
`RESEARCH_MODEL`. `WRITER_MODEL` последовательно наследует `WORKER_MODEL` и
`RESEARCH_MODEL`. `.env` содержит секреты, игнорируется Git и не должен попадать
в коммиты.

Подготовьте application-схему и checkpoint-таблицы LangGraph:

```bash
python -m app.db.migrate
python -m app.checkpoint
```

Запустите первое исследование:

```bash
python -m app.main "Как LangGraph организует параллельное выполнение агентов?"
```

Команда печатает `Run ID` и финальный отчёт. Артефакты сохраняются в
`data/runs/<run-id>/`:

```text
data/runs/<run-id>/
├── report.md
├── report.json
└── sources/
```

Метаданные, provenance и запись `research_reports` сохраняются в PostgreSQL.

## Что проверяется

Researcher не может записать произвольную ссылку или приблизительную цитату.
До сохранения claim проект проверяет URL, hash, размер snapshot и точное
совпадение quote. Затем Verifier отдельно оценивает смысл, область утверждения
и заявленные числа.

Поддерживаемые verdict:

- `supported`;
- `partially_supported`;
- `contradicted`;
- `citation_mismatch`;
- `source_unavailable`;
- `insufficient_evidence`;
- `out_of_scope`.

Writer получает только claims с разрешёнными verdict, проверяет inline
citations и не может незаметно добавить число, отсутствующее в evidence.
Финальный JSON сохраняет структурированный результат вместе с ограничениями,
противоречиями и unanswered questions.

## Восстановление после ошибок

Внешние вызовы используют retry с exponential backoff. Падение одного worker
не уничтожает результаты остальных, а run получает один из трёх статусов:
`completed`, `completed_with_errors` или `failed`.

Повторить только незавершённые задачи существующего run:

```bash
python -m app.main --resume <run-id>
```

Успешные tasks и уже сохранённые verifications повторно не выполняются.
Ожидаемые ошибки OpenAI, конфигурации и БД выводятся без traceback и завершают
команду с кодом `2`; неожиданные ошибки программирования сохраняют traceback.

## Human review и публикация

Создайте первую admin identity и рабочие роли:

```bash
python -m app.ops reviewer-add admin "Operations Admin" --role admin
python -m app.ops reviewer-add reviewer "Research Reviewer" \
  --role reviewer --actor admin
python -m app.ops reviewer-add release "Release Operator" \
  --role publisher --actor admin
```

<details>
<summary><strong>Просмотр provenance и принятие решений</strong></summary>

Просмотреть runs, tasks, claims, verifications и отчёт:

```bash
python -m app.ops runs --reviewer reviewer
python -m app.ops run <run-id> --reviewer reviewer
python -m app.ops tasks <run-id> --reviewer reviewer
python -m app.ops claims <run-id> --reviewer reviewer
python -m app.ops verifications <run-id> --reviewer reviewer
python -m app.ops report <run-id> --reviewer reviewer
```

Одобрить, отклонить claim или запросить дополнительное исследование:

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

</details>

Approval отчёта возможен только после approve всех claims, на которые он
ссылается. Публикация без human approval блокируется:

```bash
python -m app.ops review-report approve <run-id> \
  --reason "Все использованные claims проверены" --reviewer reviewer
python -m app.ops publish <run-id> \
  --reason "Готово к публикации" --reviewer release
```

Экспортировать только approved материалы в Obsidian vault:

```bash
python -m app.ops export-obsidian <run-id> /path/to/vault \
  --reviewer release
```

Любое изменение review-статуса claim сбрасывает approval текущего отчёта.

## Multi-user API

API изолирует данные по tenant, использует hashed bearer tokens и требует
`Idempotency-Key` для каждого mutating endpoint. Durable PostgreSQL queue
поддерживает leases, heartbeats, cancellation и повторный захват потерянной
задачи другим worker.

Создайте tenant и выпустите первый admin token:

```bash
python -m app.tenants create acme "Acme Research"
python -m app.tenants issue-token acme admin --role admin
```

Запустите API и независимо масштабируемые workers:

```bash
uvicorn app.api:app --host 0.0.0.0 --port 8000
python -m app.worker
python -m app.webhooks
```

Создайте idempotent run:

```bash
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Authorization: Bearer <token>" \
  -H "Idempotency-Key: create-run-001" \
  -H "Content-Type: application/json" \
  -d '{"question":"Какие доказательства доступны?"}'
```

После запуска доступны:

- OpenAPI — `http://localhost:8000/docs`;
- review dashboard — `http://localhost:8000/dashboard`;
- liveness — `http://localhost:8000/health/live`;
- readiness — `http://localhost:8000/health/ready`.

Webhook subscriptions создаются через `POST /api/v1/webhooks`. Delivery
подписывается заголовком `X-Deep-Research-Signature` в формате
`sha256=<HMAC>` и повторяется с exponential backoff.

Полный production-контур запускается отдельно:

```bash
docker compose -f compose.production.yml up --build
```

## Качество и эксплуатация

Offline evaluation прогоняет 14 контрольных сценариев без OpenAI и Tavily:

```bash
python -m app.evaluation
```

Он измеряет verdict accuracy, citation coverage, supported claim rate, exact
quote rate, обнаружение ошибочных fixtures, recovery, дубликаты источников,
длительность, внешние запросы и стоимость. Результаты записываются в
`data/evaluations/<timestamp>/`; нарушение `evals/thresholds.json` завершает
команду с ненулевым кодом.

Сравнить запуск с baseline:

```bash
python -m app.evaluation \
  --baseline data/evaluations/<baseline>/evaluation.json
```

Проверить health, SLO, alerts и трассу run:

```bash
python -m app.health live
python -m app.health ready
python -m app.health metrics --window-minutes 60
python -m app.health alerts --window-minutes 60
python -m app.ops events <run-id> --reviewer reviewer
```

Процедуры backup, retention, rollback и production deployment описаны в
[Operations](docs/operations.md) и
[Deployment runbook](docs/deployment-runbook.md).

## Проверки для разработки

```bash
python -m unittest discover -s tests
ruff check .
python -m pip check
alembic check
```

Database smoke tests:

```bash
python -m tests.test_migrations_smoke
python -m tests.test_source_claim_smoke
python -m tests.test_verification_smoke
python -m tests.test_partial_failure_smoke
python -m tests.test_human_review_smoke
python -m tests.test_observability_smoke
python -m tests.test_api_smoke
```

GitHub Actions применяет миграции, запускает static analysis, unit/database
smoke tests, SLO checks, offline quality thresholds и собирает production image.

<details>
<summary><strong>Конфигурация</strong></summary>

| Переменная | Назначение | Значение по умолчанию |
|---|---|---|
| `OPENAI_API_KEY` | Ключ OpenAI API | — |
| `TAVILY_API_KEY` | Ключ Tavily API | — |
| `RESEARCH_MODEL` | Модель Planner | — |
| `WORKER_MODEL` | Модель Researchers | `RESEARCH_MODEL` |
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
| `MAX_RUN_SECONDS` | Длительность run | `3600` |
| `ESTIMATED_INPUT_COST_PER_1M_TOKENS_USD` | Оценка стоимости 1M входных токенов | `0` |
| `SLO_MIN_RUN_SUCCESS_RATE` | Минимальная доля успешных runs | `0.95` |
| `SLO_MAX_EXTERNAL_P95_MS` | Максимальный p95 внешнего вызова | `30000` |
| `SLO_MAX_RETRY_RATE` | Максимальная доля retries | `0.10` |
| `TELEMETRY_RETENTION_DAYS` | Retention operational events | `30` |
| `LOG_LEVEL` | Уровень логирования | `INFO` |

</details>

## Статус проекта

Все этапы текущего [research pipeline roadmap](docs/research-pipeline-roadmap.md)
реализованы. Проект остаётся экспериментальным: перед использованием в
критичных процессах проверьте модели, thresholds, роли, лимиты и процедуры
восстановления на собственных данных.

## Поддержка и лицензия

Текущую ветку `main` поддерживает
[@AndreevMakc](https://github.com/AndreevMakc). Ошибки и предложения можно
оформлять через GitHub Issues. Уязвимости следует отправлять приватно согласно
[Security policy](SECURITY.md).

Исходный код распространяется по [MIT License](LICENSE).
