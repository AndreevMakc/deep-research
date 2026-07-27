# Production deployment and rollback runbook

> [!IMPORTANT]
> Этот документ является справочным черновиком. Production environment и
> deployment workflow пока не настроены и не авторизованы. CI не выполняет
> команды из этого runbook. Любой первый deployment требует отдельного
> решения и pull request.

## Pre-deployment

1. Create a backup with `python -m app.maintenance backup ./backups`.
2. Run `ruff check .`, unit tests, migration smoke and offline evaluation.
3. Build the immutable image:

   ```bash
   docker build -t deep-research:<version> .
   ```

4. Record the image tag, Git commit and migration head in the change log.

## Deployment

1. Start PostgreSQL and apply migrations with the new image:

   ```bash
   docker compose \
     -f docker-compose.yml \
     -f compose.production.yml \
     up -d postgres
   docker compose \
     -f docker-compose.yml \
     -f compose.production.yml \
     run --rm app python -m app.db.migrate
   ```

2. Verify readiness:

   ```bash
   docker compose \
     -f docker-compose.yml \
     -f compose.production.yml \
     run --rm app python -m app.health ready
   ```

3. Start the API, research workers and webhook workers:

   ```bash
   docker compose \
     -f docker-compose.yml \
     -f compose.production.yml \
     up -d api worker webhook-worker
   ```

4. Verify `/health/ready`, `/docs` and the review dashboard.
5. Run one canary research and inspect its `events`, report provenance and
   SLO metrics.
6. Register operational alerts using `python -m app.health alerts`.

## Rollback

Application rollback is safe while the new database schema remains
backward compatible:

1. Stop new workers.
2. Switch jobs to the previous immutable image tag.
3. Run readiness and a read-only provenance command.

If the previous application cannot operate with the migrated schema:

1. Stop all writers.
2. Confirm that the pre-deployment backup belongs to the target database.
3. Restore it with `--confirm RESTORE`.
4. Start the previous image.
5. Run readiness and a canary.

Do not run an Alembic downgrade against production data as an improvised
rollback. Use the verified backup when schema rollback is required.

## Incident checklist

1. Capture the run ID and correlation ID.
2. Export `app.ops events`, health metrics and active alerts.
3. Stop only affected workers unless integrity is at risk.
4. Preserve PostgreSQL and artifacts before repair.
5. Record cause, affected runs, recovery action and prevention follow-up.
