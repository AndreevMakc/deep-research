# Как вносить изменения

Проект использует GitHub Flow: короткоживущие ветки, pull request и
обязательный CI перед попаданием изменений в `main`.

## Обязательный процесс

1. Обновите локальный `main`:

   ```bash
   git switch main
   git pull --ff-only origin main
   ```

2. Создайте отдельную ветку:

   ```bash
   git switch -c feat/3-research-library
   ```

3. Внесите одно логически цельное изменение.
4. Запустите локальные проверки.
5. Отправьте ветку и откройте pull request в `main`.
6. Дождитесь обязательного check `test`.
7. После merge удалите рабочую ветку.

Прямые изменения в `main` запрещены. Это относится к коду, документации,
миграциям и GitHub Actions.

## Имена веток

Формат:

```text
<type>/<issue>-<short-description>
```

Если GitHub issue отсутствует:

```text
<type>/<short-description>
```

Допустимые типы:

| Тип | Назначение |
|---|---|
| `feat` | Новая пользовательская возможность |
| `fix` | Исправление дефекта |
| `docs` | Только документация |
| `refactor` | Изменение структуры без нового поведения |
| `test` | Тесты и fixtures |
| `ci` | CI и проверки репозитория |
| `chore` | Обслуживание проекта |
| `hotfix` | Срочное исправление подтверждённой проблемы |

Правила:

- только строчные латинские буквы, цифры, `/` и `-`;
- короткое описание в kebab-case;
- номер issue добавляется, если issue существует;
- имя не содержит автора, даты и слов вроде `new`, `changes`, `temp`;
- одна ветка соответствует одному логическому изменению.

Примеры:

```text
feat/3-research-library
fix/27-resume-checkpoint
docs/contribution-rules
chore/repository-guardrails
```

## Требования к pull request

Pull request должен:

- быть направлен в `main`;
- кратко объяснять пользовательский или технический результат;
- ссылаться на GitHub issue, если он существует;
- перечислять выполненные проверки;
- отдельно отмечать изменения схемы данных и новые переменные окружения;
- не содержать несвязанных исправлений;
- пройти обязательный CI.

Для небольшого проекта отдельное одобрение другого reviewer пока не
обязательно: защитой служат PR-only flow и required check. Обсуждения в PR
должны быть разрешены до merge.

## Проверки

Основная локальная проверка:

```bash
ruff check .
python -m compileall -q app tests migrations
python -m pip check
python -m playwright install chromium
docker compose config -q
python -m app.db.migrate
alembic check
python -m unittest discover -s tests -v
python -m app.health ready
python -m app.health alerts --window-minutes 60
python -m app.evaluation --output-root data/evaluations-ci
docker build -t deep-research:ci .
```

Команды, использующие PostgreSQL, требуют локальной тестовой базы из
`docker-compose.yml`.

CI проверяет:

- синтаксис Python;
- Ruff lint;
- целостность установленных зависимостей;
- валидность Docker Compose;
- применение миграций и отсутствие migration drift;
- полный набор unit, integration и smoke-тестов;
- browser-level end-to-end тесты в Chromium;
- readiness и SLO;
- offline quality thresholds;
- сборку Docker image без публикации.

`unittest discover` уже находит smoke-тесты по шаблону `test_*.py`, поэтому CI
не запускает их повторно отдельным списком.

## Политика деплоя

Деплоев в проекте пока нет.

CI не должен:

- публиковать Docker images или пакеты;
- обращаться к registry;
- использовать GitHub Environments;
- менять облачную инфраструктуру;
- запускать удалённые команды;
- хранить deployment credentials.

Сборка локального Docker image в CI является проверкой Dockerfile и не
публикует артефакт. Любой будущий deployment workflow требует отдельного
решения и pull request.
