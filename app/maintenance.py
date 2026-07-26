from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.engine import URL, make_url

from app.config import get_settings
from app.db.models import (
    OperationalEvent,
    ReportReviewStatus,
    ResearchReport,
    ResearchRun,
)
from app.db.session import SessionFactory
from app.source_store import RUNS_DIRECTORY


def _connection_args(url: URL) -> tuple[list[str], dict]:
    database = url.database or ""

    if database in {
        "",
        "postgres",
        "template0",
        "template1",
    }:
        raise RuntimeError(
            "Maintenance refused to target a system database"
        )

    arguments = [
        "--host",
        url.host or "localhost",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "",
        "--dbname",
        database,
    ]
    environment = dict(os.environ)

    if url.password:
        environment["PGPASSWORD"] = url.password

    return arguments, environment


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def create_backup(output_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    backup_directory = (
        output_root.resolve()
        / f"deep-research-{timestamp}"
    )
    backup_directory.mkdir(parents=True, exist_ok=False)
    database_path = backup_directory / "database.sql"
    raw_database_path = backup_directory / "database.sql.raw"
    artifacts_path = backup_directory / "artifacts.tar.gz"
    arguments, environment = _connection_args(
        make_url(get_settings().database_url)
    )
    subprocess.run(
        [
            "pg_dump",
            *arguments,
            "--format=plain",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(raw_database_path),
        ],
        check=True,
        env=environment,
    )

    with raw_database_path.open(
        "r",
        encoding="utf-8",
    ) as source, database_path.open(
        "w",
        encoding="utf-8",
    ) as target:
        for line in source:
            if line.strip() == "SET transaction_timeout = 0;":
                continue

            target.write(line)

    raw_database_path.unlink()

    with tarfile.open(
        artifacts_path,
        "w:gz",
    ) as archive:
        if RUNS_DIRECTORY.exists():
            archive.add(
                RUNS_DIRECTORY,
                arcname="runs",
            )

    manifest = {
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "database": {
            "file": database_path.name,
            "sha256": _sha256(database_path),
        },
        "artifacts": {
            "file": artifacts_path.name,
            "sha256": _sha256(artifacts_path),
        },
    }
    (backup_directory / "manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return backup_directory


def _verify_backup(backup_directory: Path) -> dict:
    manifest_path = backup_directory / "manifest.json"
    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    for key in ("database", "artifacts"):
        item = manifest[key]
        path = backup_directory / item["file"]

        if _sha256(path) != item["sha256"]:
            raise RuntimeError(
                f"Backup checksum mismatch: {path}"
            )

    return manifest


def _safe_extract(
    archive_path: Path,
    destination: Path,
) -> None:
    destination = destination.resolve()

    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(
                    "Links are not allowed in artifact archive"
                )

            target = (
                destination / member.name
            ).resolve()

            if destination not in target.parents:
                raise RuntimeError(
                    "Unsafe path in artifact archive"
                )

        archive.extractall(destination)


def restore_backup(
    backup_directory: Path,
    *,
    confirmation: str,
) -> None:
    if confirmation != "RESTORE":
        raise RuntimeError(
            "Restore requires --confirm RESTORE"
        )

    backup_directory = backup_directory.resolve()
    manifest = _verify_backup(backup_directory)
    database_path = (
        backup_directory
        / manifest["database"]["file"]
    )
    artifacts_path = (
        backup_directory
        / manifest["artifacts"]["file"]
    )
    arguments, environment = _connection_args(
        make_url(get_settings().database_url)
    )
    subprocess.run(
        [
            "psql",
            *arguments,
            "--set",
            "ON_ERROR_STOP=1",
            "--file",
            str(database_path),
        ],
        check=True,
        env=environment,
    )
    extraction_root = RUNS_DIRECTORY.parent
    temporary_root = extraction_root / (
        ".restore-" + uuid.uuid4().hex
    )
    temporary_root.mkdir(parents=True, exist_ok=False)

    try:
        _safe_extract(artifacts_path, temporary_root)
        restored_runs = temporary_root / "runs"

        if restored_runs.exists():
            RUNS_DIRECTORY.mkdir(
                parents=True,
                exist_ok=True,
            )

            for item in restored_runs.iterdir():
                target = RUNS_DIRECTORY / item.name

                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()

                item.replace(target)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def apply_retention(
    *,
    days: int,
    apply: bool,
    include_artifacts: bool,
) -> dict:
    if days < 1:
        raise ValueError("Retention days must be positive")

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=days
    )

    with SessionFactory() as session:
        event_ids = list(
            session.scalars(
                select(OperationalEvent.id).where(
                    OperationalEvent.created_at < cutoff
                )
            ).all()
        )
        artifact_run_ids = (
            list(
                session.scalars(
                    select(ResearchRun.id)
                    .join(
                        ResearchReport,
                        ResearchReport.run_id
                        == ResearchRun.id,
                    )
                    .where(
                        ResearchRun.created_at < cutoff,
                        ResearchReport.review_status
                        == ReportReviewStatus.PUBLISHED,
                    )
                ).all()
            )
            if include_artifacts
            else []
        )

        if apply and event_ids:
            session.execute(
                delete(OperationalEvent).where(
                    OperationalEvent.id.in_(event_ids)
                )
            )
            session.commit()

    removed_artifacts: list[str] = []

    if apply:
        root = RUNS_DIRECTORY.resolve()

        for run_id in artifact_run_ids:
            target = (root / str(run_id)).resolve()

            if (
                target.parent != root
                or target.name != str(run_id)
            ):
                raise RuntimeError(
                    "Unsafe artifact retention target"
                )

            if target.exists():
                shutil.rmtree(target)
                removed_artifacts.append(str(run_id))

    return {
        "mode": "apply" if apply else "dry_run",
        "cutoff": cutoff.isoformat(),
        "telemetry_events": len(event_ids),
        "artifact_runs": [
            str(value)
            for value in artifact_run_ids
        ],
        "removed_artifacts": removed_artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backup, restore, and retention operations."
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )
    backup = subparsers.add_parser("backup")
    backup.add_argument("output_root", type=Path)
    restore = subparsers.add_parser("restore")
    restore.add_argument("backup_directory", type=Path)
    restore.add_argument("--confirm", required=True)
    retention = subparsers.add_parser("retention")
    retention.add_argument(
        "--days",
        type=int,
        default=get_settings().telemetry_retention_days,
    )
    retention.add_argument(
        "--apply",
        action="store_true",
    )
    retention.add_argument(
        "--include-artifacts",
        action="store_true",
    )
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "backup":
            result = {
                "backup_directory": str(
                    create_backup(arguments.output_root)
                )
            }
        elif arguments.command == "restore":
            restore_backup(
                arguments.backup_directory,
                confirmation=arguments.confirm,
            )
            result = {"status": "restored"}
        else:
            result = apply_retention(
                days=arguments.days,
                apply=arguments.apply,
                include_artifacts=(
                    arguments.include_artifacts
                ),
            )
    except (
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
        ValueError,
    ) as error:
        print(f"Error: {error}")
        return 2

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
