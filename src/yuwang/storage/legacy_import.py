"""One-time migration from the Windows bind mount into the Docker data volume."""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from yuwang.storage.sqlite import SQLiteRepository

_MARKER = ".legacy-host-import-v1"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _tables(connection: sqlite3.Connection, schema: str = "main") -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            f"SELECT name FROM {_quote(schema)}.sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _columns(connection: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA {_quote(schema)}.table_info({_quote(table)})")]


def import_legacy_data(
    legacy_database: Path,
    volume_database: Path,
    legacy_artifacts: Path,
    volume_artifacts: Path,
) -> bool:
    """Merge an old bind-mounted database into a managed Docker volume once.

    Rows use their existing primary keys, so both a pre-existing volume and the
    legacy database remain readable.  The caller must run while the old API is
    stopped, otherwise copying a SQLite WAL database would not be a stable snapshot.
    """

    marker = volume_database.parent / _MARKER
    if marker.exists() or not legacy_database.is_file():
        return False

    volume_database.parent.mkdir(parents=True, exist_ok=True)
    volume_artifacts.mkdir(parents=True, exist_ok=True)
    staging_database = volume_database.parent / ".legacy-host-import.db"
    for path in (staging_database, *(
        staging_database.with_name(staging_database.name + suffix) for suffix in ("-wal", "-shm")
    )):
        path.unlink(missing_ok=True)
    # A read-only bind mount cannot provide SQLite's lock files.  Copy the stopped
    # host DB and its WAL sidecars into the managed volume before attaching it.
    for suffix in ("", "-wal", "-shm"):
        source = legacy_database.with_name(legacy_database.name + suffix)
        if source.is_file():
            shutil.copy2(source, staging_database.with_name(staging_database.name + suffix))
    # Bring an old volume up to the current schema before attaching the host database.
    SQLiteRepository(volume_database)
    volume_uri = volume_database.resolve().as_uri() + "?mode=rwc"
    try:
        with sqlite3.connect(volume_uri, uri=True) as destination:
            destination.execute("PRAGMA foreign_keys=OFF")
            destination.execute("ATTACH DATABASE ? AS legacy", (str(staging_database),))
            for table in _tables(destination, "legacy"):
                if table not in _tables(destination):
                    row = destination.execute(
                        "SELECT sql FROM legacy.sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                    if not row or not row[0]:
                        raise RuntimeError(f"无法迁移未知数据表：{table}")
                    destination.execute(str(row[0]))
                destination_columns = set(_columns(destination, table))
                columns = [column for column in _columns(destination, table, "legacy") if column in destination_columns]
                if not columns:
                    continue
                field_list = ", ".join(_quote(column) for column in columns)
                try:
                    destination.execute(
                        f"INSERT OR REPLACE INTO {_quote(table)} ({field_list}) "
                        f"SELECT {field_list} FROM legacy.{_quote(table)}"
                    )
                    # Commit per table: large checkpoint snapshots can exceed SQLite's
                    # practical WAL transaction size on Docker Desktop filesystems.
                    destination.commit()
                except sqlite3.Error as error:
                    raise RuntimeError(f"迁移数据表 {table} 失败：{error}") from error
            # SQLite cannot detach an attached database with an active write transaction.
            destination.execute("DETACH DATABASE legacy")

        if legacy_artifacts.is_dir():
            shutil.copytree(legacy_artifacts, volume_artifacts, dirs_exist_ok=True, copy_function=shutil.copy2)
        marker.write_text("legacy bind mount merged\n", encoding="utf-8")
    finally:
        for path in (staging_database, *(
            staging_database.with_name(staging_database.name + suffix) for suffix in ("-wal", "-shm")
        )):
            path.unlink(missing_ok=True)
    return True


def set_volume_ownership(root: Path, uid: int = 10001, gid: int = 10001) -> None:
    """The init container is root; the API container intentionally is not."""

    chown = getattr(os, "chown", None)
    if not callable(chown):  # Windows test environments do not expose POSIX ownership APIs.
        return
    for directory, _, files in os.walk(root):
        chown(directory, uid, gid)
        for filename in files:
            chown(Path(directory) / filename, uid, gid)
