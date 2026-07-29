"""Compose data-init entry point.  It never deletes either source or volume data."""

from __future__ import annotations

from pathlib import Path

from yuwang.storage.legacy_import import import_legacy_data, set_volume_ownership


def main() -> None:
    data_root = Path("/data")
    import_legacy_data(
        legacy_database=Path("/legacy/yuwang.db"),
        volume_database=data_root / "yuwang.db",
        legacy_artifacts=Path("/legacy/artifacts"),
        volume_artifacts=data_root / "artifacts",
    )
    (data_root / "artifacts").mkdir(parents=True, exist_ok=True)
    set_volume_ownership(data_root)


if __name__ == "__main__":
    main()
