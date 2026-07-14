"""可管理设置存储：Provider、平台预算与版本化 AgentProfile。"""

from __future__ import annotations

from uuid import UUID

from yuwang.settings.models import AgentDefaults, ProviderConfig
from yuwang.settings.profiles import AgentProfileVersion
from yuwang.storage.sqlite_common import SQLiteStore


class SQLiteSettingsStore(SQLiteStore):
    def save_provider_config(self, value: ProviderConfig) -> ProviderConfig:
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO provider_configs VALUES(?,?,?)",
                (str(value.id), value.model_dump_json(), value.created_at),
            )
        return value

    def get_provider_config(self, provider_id: UUID | str) -> ProviderConfig | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM provider_configs WHERE id=?", (str(provider_id),)
            ).fetchone()
        return ProviderConfig.model_validate_json(row["data"]) if row else None

    def list_provider_configs(self) -> list[ProviderConfig]:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM provider_configs ORDER BY created_at").fetchall()
        return [ProviderConfig.model_validate_json(row["data"]) for row in rows]

    def set_default_provider(self, provider_id: UUID) -> None:
        with self._lock:
            values = self.list_provider_configs()
            if not any(value.id == provider_id for value in values):
                raise KeyError("Provider 配置不存在")
            for value in values:
                desired = value.id == provider_id
                if value.is_default != desired:
                    value.is_default = desired
                    self.save_provider_config(value)

    def delete_provider_config(self, provider_id: UUID) -> None:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM provider_configs WHERE id=?", (str(provider_id),))
            if cursor.rowcount == 0:
                raise KeyError("Provider 配置不存在")

    def get_agent_defaults(self) -> AgentDefaults:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM app_settings WHERE key='agent_defaults'"
            ).fetchone()
        return AgentDefaults.model_validate_json(row["data"]) if row else AgentDefaults()

    def save_agent_defaults(self, value: AgentDefaults) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO app_settings(key,data) VALUES('agent_defaults',?)",
                (value.model_dump_json(),),
            )

    def save_agent_profile_version(self, value: AgentProfileVersion) -> None:
        with self.connect() as db:
            existing = db.execute(
                "SELECT data FROM agent_profile_versions WHERE profile_id=? AND version=?",
                (str(value.profile_id), value.version),
            ).fetchone()
            serialized = value.model_dump_json()
            if existing and existing["data"] != serialized:
                raise ValueError("AgentProfile 历史版本不可变")
            db.execute(
                "INSERT OR IGNORE INTO agent_profile_versions VALUES(?,?,?,?)",
                (str(value.profile_id), value.version, serialized, value.created_at),
            )

    def get_agent_profile(
        self, profile_id: UUID, version: int | None = None
    ) -> AgentProfileVersion | None:
        with self.connect() as db:
            if version is None:
                row = db.execute(
                    "SELECT data FROM agent_profile_versions WHERE profile_id=? ORDER BY version DESC LIMIT 1",
                    (str(profile_id),),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT data FROM agent_profile_versions WHERE profile_id=? AND version=?",
                    (str(profile_id), version),
                ).fetchone()
        return AgentProfileVersion.model_validate_json(row["data"]) if row else None

    def list_agent_profile_versions(self, profile_id: UUID) -> list[AgentProfileVersion]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM agent_profile_versions WHERE profile_id=? ORDER BY version",
                (str(profile_id),),
            ).fetchall()
        return [AgentProfileVersion.model_validate_json(row["data"]) for row in rows]

    def list_agent_profiles(self) -> list[AgentProfileVersion]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT versions.data FROM agent_profile_versions AS versions
                JOIN (
                    SELECT profile_id, MAX(version) AS latest
                    FROM agent_profile_versions GROUP BY profile_id
                ) AS current
                ON versions.profile_id=current.profile_id AND versions.version=current.latest
                ORDER BY versions.created_at
                """
            ).fetchall()
        return [AgentProfileVersion.model_validate_json(row["data"]) for row in rows]

    def delete_agent_profile(self, profile_id: UUID) -> None:
        with self.connect() as db:
            cursor = db.execute(
                "DELETE FROM agent_profile_versions WHERE profile_id=?", (str(profile_id),)
            )
            if cursor.rowcount == 0:
                raise KeyError("Agent 配置不存在")
