/** Agent 配置数据协调器：远端操作留在这里，展示和表单拆到 agent-profile/。 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { AgentProfile, AgentProfileInput, ProviderConfig } from "../types";
import AgentProfileForm from "./agent-profile/AgentProfileForm";
import AgentProfileList from "./agent-profile/AgentProfileList";
import VersionHistory from "./agent-profile/VersionHistory";
import {
  buildProfilePayload,
  createEmptyProfile,
  profileToInput,
} from "./agent-profile/model";

interface Props {
  csrf: string;
  providers: ProviderConfig[];
  onChanged: () => Promise<void>;
}

export default function AgentProfileCenter({
  csrf,
  providers,
  onChanged,
}: Props) {
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [form, setForm] = useState<AgentProfileInput>(createEmptyProfile);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [versions, setVersions] = useState<AgentProfile[]>([]);
  const [expert, setExpert] = useState(false);
  const [wizardStep, setWizardStep] = useState(1);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState("");
  const [schemaText, setSchemaText] = useState("");

  async function load() {
    setProfiles(await api.adminProfiles(csrf));
    await onChanged();
  }

  useEffect(() => {
    void load().catch((cause) => setError(String(cause)));
  }, [csrf]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = useMemo(
    () => profiles.find((profile) => profile.profile_id === editingId),
    [profiles, editingId],
  );

  function edit(profile: AgentProfile) {
    setEditingId(profile.profile_id);
    setForm(profileToInput(profile));
    setSchemaText(
      profile.validation_policy.json_schema
        ? JSON.stringify(profile.validation_policy.json_schema, null, 2)
        : "",
    );
    setWizardStep(1);
    setNotice("");
  }

  function reset() {
    setEditingId(null);
    setForm(createEmptyProfile());
    setSchemaText("");
    setVersions([]);
    setWizardStep(1);
    setPreview("");
  }

  async function save() {
    setError("");
    try {
      const payload = buildProfilePayload(form, schemaText);
      if (editingId) await api.updateProfile(csrf, editingId, payload);
      else await api.createProfile(csrf, payload);
      await load();
      reset();
      setNotice("Agent 配置已保存为新版本");
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function showVersions(profile: AgentProfile) {
    try {
      edit(profile);
      setVersions(await api.profileVersions(csrf, profile.profile_id));
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function rollback(version: number) {
    if (!editingId) return;
    try {
      await api.rollbackProfile(csrf, editingId, version);
      await load();
      setVersions(await api.profileVersions(csrf, editingId));
      setNotice(`已回滚并创建版本 ${version} 的后继版本`);
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function copy(profile: AgentProfile) {
    try {
      await api.copyProfile(csrf, profile.profile_id, `${profile.name} 副本`);
      await load();
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function makeDefault(profile: AgentProfile) {
    try {
      await api.defaultProfile(csrf, profile.profile_id);
      await load();
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function remove(profile: AgentProfile) {
    try {
      await api.deleteProfile(csrf, profile.profile_id);
      await load();
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function previewTemplate() {
    try {
      setPreview(
        (await api.previewTemplate(csrf, form.user_prompt_template)).rendered,
      );
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function exportConfig() {
    try {
      const bundle = await api.exportProfiles(csrf);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(bundle, null, 2)], {
          type: "application/json",
        }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "yuwang-agent-profiles.json";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(String(cause));
    }
  }

  async function importConfig(file?: File) {
    if (!file) return;
    try {
      await api.importProfiles(csrf, JSON.parse(await file.text()));
      await load();
      setNotice("无密钥配置已导入");
    } catch (cause) {
      setError(String(cause));
    }
  }

  return (
    <section className="profile-center" data-testid="agent-profile-center">
      <div className="settings-title">
        <h3>Agent 配置</h3>
        <div>
          <button onClick={() => setExpert((value) => !value)}>
            {expert ? "向导模式" : "专家模式"}
          </button>
          <button onClick={reset}>新建配置</button>
          <button onClick={() => void exportConfig()}>无密钥导出</button>
          <label className="file-button">
            导入
            <input
              type="file"
              accept="application/json"
              onChange={(event) => void importConfig(event.target.files?.[0])}
            />
          </label>
        </div>
      </div>
      <AgentProfileList
        profiles={profiles}
        onEdit={edit}
        onShowVersions={(profile) => void showVersions(profile)}
        onCopy={(profile) => void copy(profile)}
        onMakeDefault={(profile) => void makeDefault(profile)}
        onRemove={(profile) => void remove(profile)}
      />
      <AgentProfileForm
        form={form}
        providers={providers}
        expert={expert}
        wizardStep={wizardStep}
        schemaText={schemaText}
        preview={preview}
        submitLabel={
          editingId
            ? `保存新版本（当前 v${selected?.version ?? "?"}）`
            : "创建 Agent 配置"
        }
        onChange={setForm}
        onWizardStepChange={setWizardStep}
        onSchemaChange={setSchemaText}
        onPreview={() => void previewTemplate()}
        onSubmit={() => void save()}
      />
      <VersionHistory
        versions={versions}
        onRollback={(version) => void rollback(version)}
      />
      {notice && <div className="settings-notice">{notice}</div>}
      {error && (
        <div className="settings-error" role="alert">
          {error}
        </div>
      )}
    </section>
  );
}
