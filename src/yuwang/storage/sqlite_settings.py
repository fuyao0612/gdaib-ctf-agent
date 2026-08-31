"""可管理设置存储：Provider、平台预算与版本化 AgentProfile。"""

from __future__ import annotations

import json
from uuid import UUID

from yuwang.settings.models import AgentDefaults, ProviderConfig
from yuwang.settings.profiles import AgentProfileVersion
from yuwang.settings.skills import SkillDefinition
from yuwang.storage.sqlite_common import SQLiteStore

MIN_CONTEXT_TOKEN_BUDGET = 32_768


class SQLiteSettingsStore(SQLiteStore):
    def save_skill(self, value: SkillDefinition) -> SkillDefinition:
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO skills VALUES(?,?,?)",
                (str(value.id), value.model_dump_json(), value.created_at.isoformat()),
            )
        return value

    def get_skill(self, skill_id: UUID | str) -> SkillDefinition | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM skills WHERE id=?", (str(skill_id),)).fetchone()
        return SkillDefinition.model_validate_json(row["data"]) if row else None

    def list_skills(self) -> list[SkillDefinition]:
        with self.connect() as db:
            rows = db.execute("SELECT data FROM skills ORDER BY created_at").fetchall()
        return [SkillDefinition.model_validate_json(row["data"]) for row in rows]

    def delete_skill_with_thread_cleanup(self, skill_id: UUID) -> int:
        """删除设置后同步清理 Thread 选择；历史 Run 继续使用 TaskSpec 快照。"""

        affected = 0
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,data FROM threads").fetchall()
            for row in rows:
                data = json.loads(row["data"])
                selected = data.get("skill_ids", [])
                remaining = [item for item in selected if item != str(skill_id)]
                if len(remaining) == len(selected):
                    continue
                data["skill_ids"] = remaining
                db.execute(
                    "UPDATE threads SET data=? WHERE id=?",
                    (json.dumps(data, ensure_ascii=False), row["id"]),
                )
                affected += 1
            cursor = db.execute("DELETE FROM skills WHERE id=?", (str(skill_id),))
            if cursor.rowcount == 0:
                raise KeyError("Skill 不存在")
        return affected

    def save_provider_config(
        self, value: ProviderConfig, *, set_default: bool = False
    ) -> ProviderConfig:
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if set_default:
                rows = db.execute("SELECT id,data FROM provider_configs").fetchall()
                for row in rows:
                    if row["id"] == str(value.id):
                        continue
                    current = ProviderConfig.model_validate_json(row["data"])
                    if current.is_default:
                        current.is_default = False
                        db.execute(
                            "UPDATE provider_configs SET data=? WHERE id=?",
                            (current.model_dump_json(), row["id"]),
                        )
                value.is_default = True
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
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,data FROM provider_configs").fetchall()
            if not any(row["id"] == str(provider_id) for row in rows):
                raise KeyError("Provider 配置不存在")
            for row in rows:
                value = ProviderConfig.model_validate_json(row["data"])
                desired = value.id == provider_id
                if value.is_default != desired:
                    value.is_default = desired
                    db.execute(
                        "UPDATE provider_configs SET data=? WHERE id=?",
                        (value.model_dump_json(), row["id"]),
                    )

    def delete_provider_config(self, provider_id: UUID) -> None:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM provider_configs WHERE id=?", (str(provider_id),))
            if cursor.rowcount == 0:
                raise KeyError("Provider 配置不存在")

    def delete_provider_with_thread_fallback(
        self,
        provider_id: UUID,
        fallback_provider_id: UUID | None,
        notice: str,
    ) -> int:
        """事务内回退会话选择并删除 Provider，避免留下引用已删除配置的 Thread。"""

        affected = 0
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,data FROM threads").fetchall()
            for row in rows:
                data = json.loads(row["data"])
                if data.get("provider_config_id") != str(provider_id):
                    continue
                data["provider_config_id"] = (
                    str(fallback_provider_id) if fallback_provider_id else None
                )
                data["provider_fallback_notice"] = notice
                db.execute(
                    "UPDATE threads SET data=? WHERE id=?",
                    (json.dumps(data, ensure_ascii=False), row["id"]),
                )
                affected += 1
            cursor = db.execute("DELETE FROM provider_configs WHERE id=?", (str(provider_id),))
            if cursor.rowcount == 0:
                raise KeyError("Provider 配置不存在")
        return affected

    def get_agent_defaults(self) -> AgentDefaults:
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM app_settings WHERE key='agent_defaults'"
            ).fetchone()
        if not row:
            return AgentDefaults()
        data = json.loads(row["data"])
        # 仅迁移 v0.5.0 明确保存的 32000。缺失字段必须交给当前模型默认值
        # 262144，不能在升级时被误写为 32K；其他无效值仍由模型校验报告。
        if data.get("context_token_budget") == 32_000:
            data["context_token_budget"] = MIN_CONTEXT_TOKEN_BUDGET
            with self.connect() as writable:
                writable.execute(
                    "INSERT OR REPLACE INTO app_settings(key,data) VALUES('agent_defaults',?)",
                    (json.dumps(data, ensure_ascii=False),),
                )
        return AgentDefaults.model_validate(data)

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

    def replace_agent_profile_references(
        self, source_id: UUID, target: AgentProfileVersion
    ) -> int:
        """将历史线程绑定迁移到保留的 Profile，避免去重后新 Run 无法解析配置。"""

        source = str(source_id)
        target_id = str(target.profile_id)
        changed = 0
        with self.connect() as db:
            rows = db.execute("SELECT id, data FROM threads").fetchall()
            for row in rows:
                data = json.loads(row["data"])
                if data.get("agent_profile_id") != source:
                    continue
                data["agent_profile_id"] = target_id
                data["agent_profile_version"] = target.version
                db.execute(
                    "UPDATE threads SET data=? WHERE id=?",
                    (json.dumps(data, ensure_ascii=False), row["id"]),
                )
                changed += 1
        return changed
