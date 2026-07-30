# Operations, SLO and recovery

## Health and metrics

```bash
python -m app.health live
python -m app.health ready
python -m app.health metrics --window-minutes 60
python -m app.health alerts --window-minutes 60
```

Readiness checks PostgreSQL, the application schema and artifact storage.
The alert command exits with code `1` when an SLO is violated.

Default SLO:

- completed run success rate ≥ 95%;
- external-call p95 ≤ 30 seconds;
- retry rate ≤ 10%.

Thresholds are configured through `SLO_MIN_RUN_SUCCESS_RATE`,
`SLO_MAX_EXTERNAL_P95_MS` and `SLO_MAX_RETRY_RATE`.

Inspect the trace of a run:

```bash
python -m app.ops events <run-id> --reviewer <subject>
```

Events contain correlation ID, node/agent, task or claim ID, latency,
attempt number, error code, token estimate and estimated cost. Prompts,
page bodies, API keys and authorization values are not written to
telemetry.

## Run progress and safe control

`GET /api/v1/runs/{run_id}/progress` returns stable workflow stages
(`directions`, `sources`, `verification`, `report`), persisted counters,
elapsed wall time and the remaining configured time upper bound. It does
not expose a synthetic completion percentage. Users with provenance access
also receive a collapsible summary of task errors and retry events.

Authors and tenant administrators can control non-terminal work through
idempotent endpoints:

- `POST /api/v1/runs/{run_id}/pause` requests a cooperative stop. A leased
  worker releases the subprocess after the current heartbeat and the same
  work item becomes `paused`; the pause does not consume a retry attempt.
- `POST /api/v1/runs/{run_id}/resume` returns the paused item to the durable
  queue. LangGraph checkpoints and persisted task/verification states ensure
  completed work is not repeated.
- `POST /api/v1/runs/{run_id}/finish` stops unfinished research and starts a
  report pass from completed directions and their saved claims. The endpoint
  rejects a run that has no completed direction.

Closing the dashboard does not affect execution. Workers continue leasing
and heartbeating work from PostgreSQL, and the dashboard reconstructs the
current state from the progress endpoint after reload.

## Reviewer identities

Bootstrap the first administrator:

```bash
python -m app.ops reviewer-add admin "Operations Admin" --role admin
```

Only an administrator may create subsequent identities or enable/disable
them:

```bash
python -m app.ops reviewer-add alice "Alice" \
  --role reviewer --actor admin
python -m app.ops reviewer-add release "Release Operator" \
  --role publisher --actor admin
python -m app.ops reviewer-disable alice --actor admin
```

Roles:

- `viewer` — read provenance and telemetry;
- `reviewer` — review claims and reports;
- `publisher` — reviewer permissions plus publish/export;
- `admin` — all permissions and identity management.

## Backup

The command creates a portable PostgreSQL SQL dump, a compressed artifact
archive and a SHA-256 manifest. Restore uses `ON_ERROR_STOP`:

```bash
python -m app.maintenance backup ./backups
```

Copy the resulting directory to storage outside the deployment host.
Periodically verify that its manifest and restore procedure succeed in a
disposable environment.

## Restore

Restore overwrites the configured non-system database and matching
artifacts. Verify `DATABASE_URL` and stop research workers first:

```bash
python -m app.maintenance restore \
  ./backups/deep-research-<timestamp> \
  --confirm RESTORE
python -m app.db.migrate
python -m app.health ready
```

The command rejects PostgreSQL system databases, validates backup hashes
and checks archive paths before extraction.

## Retention

Preview removal of telemetry older than the configured window:

```bash
python -m app.maintenance retention --days 30
```

Apply telemetry retention:

```bash
python -m app.maintenance retention --days 30 --apply
```

Published run artifacts are retained by default. Removing them requires
the additional explicit flag:

```bash
python -m app.maintenance retention \
  --days 90 --include-artifacts --apply
```

Create and verify a backup before artifact retention.
