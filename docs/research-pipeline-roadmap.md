# Deep Research Pipeline Roadmap

Текущий контур проекта:

```text
Planner
→ параллельные Researchers
→ параллельные Verifiers claims
→ доказательный Writer
→ human review / publication gate
→ telemetry / SLO / operations
→ tenant API / distributed workers / webhooks
```

Researcher производит проверяемые утверждения, связанные с
неизменяемыми snapshots. Независимый Verifier проверяет каждый claim,
сохраняет verdict и обновляет его статус. Оба worker-этапа устойчивы к
частичным ошибкам и имеют независимые лимиты параллелизма. Writer
синтезирует итог только по проверенным claims и сохраняет Markdown/JSON.
Offline evaluation измеряет качество на контрольных fixtures и блокирует
регрессии через CI thresholds. Эксплуатационный CLI, human review,
publication gate, лимиты, telemetry, SLO и production runbooks уже
реализованы вместе с multi-user API и распределённым выполнением.

## Этап 1. Доказательная база Researcher

**Приоритет:** P0

**Статус:** реализован

До реализации этого этапа Researcher получал только поисковые
сниппеты, которых недостаточно для надёжной проверки утверждений.

### Доработки

1. Добавить загрузку полного содержимого найденных страниц.
2. Нормализовать URL и исключать дубликаты.
3. Сохранять источники в таблицу `sources`:
   - URL и canonical URL;
   - заголовок;
   - дату загрузки;
   - hash содержимого;
   - локальный путь или полный текст;
   - метаданные.
4. Расширить `ResearchFinding`:
   - добавить дословную `evidence_quote`;
   - добавить `locator` — заголовок раздела, абзац или fragment;
   - добавить `scope`;
   - использовать `source_id`, а не только URL.
5. Проверять программно, что цитата действительно присутствует
   в сохранённом тексте.
6. Сохранять findings в таблицу `claims`.

### Definition of Done

- Каждый claim связан с сохранённым источником.
- Каждая цитата буквально найдена в содержимом источника.
- Выдуманный URL или цитата отклоняются до записи в БД.
- Недостаток доказательств возвращает пустой список claims,
  а не вынужденный вывод.

## Этап 1.1. Неизменяемые доказательства

**Приоритет:** P0

**Статус:** реализован

Этап усиливает provenance перед добавлением Verifier: claim должен
всегда ссылаться на конкретную версию текста, которая не меняется
при повторной загрузке URL.

### Доработки

1. Добавить Alembic и baseline существующей application-схемы.
2. Разделить `Source` и неизменяемый `SourceSnapshot`.
3. Хранить в snapshot:
   - `content_hash`;
   - `local_path`;
   - `final_url`;
   - MIME type и HTTP status;
   - размер содержимого;
   - время и метаданные загрузки.
4. Связать `Claim` с `source_snapshot_id`.
5. Добавить явный `research_task_id`.
6. Сохранять `quote_start` и `quote_end`.
7. Проверять hash и размер snapshot при каждом чтении.
8. Не перезаписывать старый snapshot при изменении страницы.

### Definition of Done

- Новая версия страницы создаёт новый snapshot.
- Старый claim продолжает ссылаться на прежний текст.
- Claim воспроизводится по snapshot и координатам цитаты.
- Повреждение локального файла обнаруживается при чтении.
- Чистая и legacy-схемы обновляются через Alembic.

## Этап 2. Verifier-agent

**Приоритет:** P0

**Статус:** реализован

Verifier реализован как отдельный агент: Researcher не проверяет
собственные выводы.

### Реализовано

1. Создать `VerificationResult` и prompt Verifier.
2. Передавать Verifier:
   - один claim;
   - сохранённый источник;
   - точную цитату;
   - контекст вокруг цитаты.
3. Поддержать verdict:
   - `supported`;
   - `partially_supported`;
   - `contradicted`;
   - `citation_mismatch`;
   - `source_unavailable`;
   - `insufficient_evidence`;
   - `out_of_scope`.
4. Проверять отдельно:
   - существует ли цитата;
   - соответствует ли она утверждению;
   - присутствуют ли заявленные числа;
   - не шире ли claim области источника.
5. Сохранять результат в `verifications`.
6. Обновлять `Claim.status`.
7. Не удалять весь проект или источник из-за одного
   неподтверждённого claim.

### Definition of Done

- Verifier работает независимо от Researcher.
- Неверная цитата получает `citation_mismatch`.
- Неподтверждённое число не может получить `supported`.
- Все решения сохраняются с причиной и confidence.

## Этап 3. Устойчивый LangGraph workflow

**Приоритет:** P1

**Статус:** реализован

Целевой граф:

```text
Planner
   ↓
Researchers
   ↓
Claim extraction
   ↓
Verifiers
   ↓
Judge / triage
   ↓
Writer
```

### Реализовано

1. Добавить второй fan-out: один Verifier на claim.
2. Ограничить параллелизм отдельно для поиска и верификации.
3. Добавить retry/backoff для Tavily, загрузки страниц и LLM.
4. Не завершать весь run из-за одной неудачной задачи.
5. Поддержать итоговые состояния:
   - `completed`;
   - `completed_with_errors`;
   - `failed`.
6. Сохранять ошибку в `ResearchTask.output_data`.
7. Добавить повторный запуск только failed-задач.
8. Обеспечить идемпотентность при восстановлении из checkpoint.

### Definition of Done

- Падение одного Researcher не уничтожает результаты остальных.
- Run можно продолжить после quota или network failure.
- Повторный запуск не создаёт дубликаты sources и claims.

## Этап 4. Полноценный Writer

**Приоритет:** P1

**Статус:** реализован

Writer формирует доказательный отчёт только из claims с разрешёнными
verdict и программно проверяемыми citations.

### Реализовано

1. Передавать Writer только проверенные claims.
2. Запретить добавлять новые факты во время синтеза.
3. Генерировать:
   - краткий ответ;
   - основной отчёт;
   - ограничения;
   - противоречия;
   - unanswered questions;
   - список источников.
4. Добавить inline citations.
5. Явно обозначать уровень уверенности.
6. Экспортировать результат в Markdown и JSON.

### Definition of Done

- Каждое значимое утверждение отчёта связано с verified claim.
- Writer не использует rejected claims.
- Цитаты можно проследить до сохранённого текста источника.

## Этап 5. Тестирование и оценка качества

**Приоритет:** P1

**Статус:** реализован

### Реализовано

- Добавлены тесты нормализации и canonicalization URL.
- Добавлен тест точного поиска цитаты.
- Добавлен тест сохранения `Source → Claim → Verification`.
- Добавлены тесты частичного падения worker-задач и восстановления из checkpoint.
- Добавлен интеграционный контур без внешних API.
- Подготовлен набор из 14 контрольных сценариев для Verifier и Writer.
- Добавлены заведомо некорректные fixtures:
  неверные числа, URL, цитаты и недоступные источники.
- Реализован offline evaluation runner с JSON-отчётом и сравнением с baseline.
- Добавлены пороги качества и GitHub Actions quality gate.

### Метрики

- Доля claims с точной цитатой.
- Citation mismatch rate.
- Доля поддержанных claims.
- Число дубликатов источников.
- Стоимость и длительность run.
- Число внешних запросов.
- Доля успешно восстановленных runs.

## Этап 6. Эксплуатация и human review

**Приоритет:** P2

**Статус:** реализован

### Реализовано

1. Добавлены CLI-команды просмотра runs, tasks, claims, verifications,
   reports и полного журнала provenance.
2. Добавлены review status и неизменяемый журнал human-решений с причиной,
   reviewer и временем.
3. Реализованы approve/reject claim и создание follow-up research task.
4. Rejected claim исключается из следующей сборки Writer.
5. Добавлены approval gate отчёта и отдельная операция публикации.
6. Добавлен экспорт approved claims в Markdown/JSON для Obsidian.
7. Добавлены фиксируемые на уровне run лимиты:
   - максимальное число запросов;
   - максимальное число источников;
   - максимальное число claims;
   - максимальное оценочное число токенов;
   - максимальное время выполнения.
8. Добавлен сквозной smoke-тест review, publication gate и экспорта.

### Definition of Done

- Пользователь может просмотреть весь provenance run из CLI.
- Каждое human-решение сохраняется с причиной и временем.
- Неодобренный отчёт невозможно пометить опубликованным.
- Rejected claim исключается из следующей версии отчёта.
- Экспорт содержит только approved материалы.

## Этап 7. Наблюдаемость и production hardening

**Приоритет:** P2

**Статус:** реализован

### Реализовано

1. Добавлены correlation IDs и structured JSON events для graph nodes,
   внешних вызовов, retries, lifecycle run и budget consumption.
2. Добавлена durable telemetry в `operational_events`:
   latency, attempts, task/claim/agent attribution, error codes, token
   estimates и configurable cost estimates.
3. Добавлены CLI trace, health/readiness, SLO metrics и alert evaluation.
4. Добавлена минимальная RBAC-модель reviewer identities:
   viewer, reviewer, publisher и admin.
5. Добавлены backup/restore с checksum manifest и safe archive extraction.
6. Добавлена dry-run retention policy для telemetry и published artifacts.
7. Добавлены production container, deployment и rollback runbooks.
8. CI проверяет readiness, alerts, telemetry/RBAC smoke и сборку image.

### Definition of Done

- Любой внешний вызов прослеживается до run, task и agent.
- Для времени, ошибок и расхода бюджета заданы измеримые SLO.
- Состояние БД и artifacts восстанавливается по проверенному runbook.
- Production deployment имеет health checks и безопасный rollback.

## Этап 8. Multi-user API и распределённое выполнение

**Приоритет:** P3

**Статус:** реализован

### Реализовано

1. Добавлен authenticated versioned `/api/v1` для runs, provenance,
   reviews, reports, publication и webhook subscriptions.
2. Добавлены tenants и hashed bearer tokens с tenant-scoped API roles.
3. Все API-запросы к данным фильтруются по `tenant_id`; чужой ресурс
   возвращается как `404`.
4. Добавлена PostgreSQL durable queue с `SKIP LOCKED`, leases, heartbeats,
   expired-lease recovery, retry attempts и отдельным worker process.
5. Добавлены cancellation queued/running work и terminal cancellation state.
6. Mutating endpoints защищены tenant-scoped idempotency keys и
   transaction-level advisory locks.
7. Добавлен review dashboard поверх прежнего RBAC, audit trail и
   publication gate.
8. Добавлены HMAC-SHA256 signed webhooks, delivery queue, exponential retry
   и защита webhook URL от private/loopback targets.
9. Production Compose запускает независимо API, research workers и webhook
   delivery workers.
10. Добавлен сквозной TestClient smoke двух tenants, idempotency, leases,
    cancellation, dashboard и webhook signature.

### Definition of Done

- Два tenant не могут читать или изменять данные друг друга.
- Worker можно безопасно остановить и заменить без потери задачи.
- Все mutating API operations идемпотентны и авторизованы.
- Review и publication доступны через API/UI с прежним audit trail.

## Статус roadmap

Все запланированные этапы реализованы. Следующие продуктовые направления
следует добавлять как новый roadmap после проверки production usage,
нагрузочного профиля и требований внешнего identity provider.
