/** Agent 配置数据协调器：远端操作留在这里，展示和表单拆到 agent-profile/。 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type {
  AgentProfile,
  AgentProfileInput,
  ProviderConfig,
  SettingsMode,
  ToolSpec,
} from "../types";
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
  mode: SettingsMode;
}

export default function AgentProfileCenter({
  csrf,
  providers,
  onChanged,
  mode,
}: Props) {
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [tools, setTools] = useState<ToolSpec[]>([]);
  const [form, setForm] = useState<AgentProfileInput>(createEmptyProfile);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [versions, setVersions] = useState<AgentProfile[]>([]);
  const [wizardStep, setWizardStep] = useState(1);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState("");
  const [schemaText, setSchemaText] = useState("");
  const [formOpen, setFormOpen] = useState(false);

  async function load() {
    const [profileItems, toolItems] = await Promise.all([
      api.adminProfiles(csrf),
      api.tools(),
    ]);
    setProfiles(profileItems);
    setTools(Array.isArray(toolItems) ? toolItems : []);
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
    setFormOpen(true);
  }

  function reset() {
    setEditingId(null);
    setForm(createEmptyProfile());
    setSchemaText("");
    setVersions([]);
    setWizardStep(1);
    setPreview("");
    setFormOpen(true);
  }

  async function save() {
    setError("");
    try {
      const payload = buildProfilePayload(form, schemaText);
      if (editingId) await api.updateProfile(csrf, editingId, payload);
      else await api.createProfile(csrf, payload);
      await load();
      reset();
      setFormOpen(false);
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

  const recommended = profiles.find((profile) => profile.is_default);
  const workflowLabel = recommended?.workflow.preset === "direct"
    ? "直接执行并验证"
    : recommended?.workflow.preset === "planned"
      ? "先规划后执行"
      : "规划、验证并自动调整";
  const completionLabel = recommended?.completion_mode === "evidence"
    ? "证据验证"
    : recommended?.completion_mode === "structured"
      ? "结构化输出"
      : "建议回答";

  if (mode === "beginner") {
    return (
      <section className="profile-center" data-testid="agent-profile-center">
        <div className="settings-title">
          <div>
            <h3>推荐 Agent 配置</h3>
            <small>来自服务端正式默认配置，并非演示数据。</small>
          </div>
        </div>
        {recommended ? (
          <article className="recommended-agent">
            <div>
              <strong>{recommended.name}</strong>
              <span>v{recommended.version}</span>
            </div>
            <p>{recommended.description || "使用安全预算、动态规划与证据验证。"}</p>
            <dl>
              <div>
                <dt>工作流</dt>
                <dd>{workflowLabel}</dd>
              </div>
              <div>
                <dt>完成标准</dt>
                <dd>{completionLabel}</dd>
              </div>
              <div>
                <dt>模型来源</dt>
                <dd>
                  {recommended.default_provider_id
                    ? providers.find(
                        (provider) => provider.id === recommended.default_provider_id,
                      )?.name ?? "指定 Provider"
                    : "跟随默认 Provider"}
                </dd>
              </div>
              <div>
                <dt>运行余量</dt>
                <dd>{recommended.budget.max_duration_seconds} 秒 · {recommended.budget.max_tool_calls} 次工具调用</dd>
              </div>
            </dl>
            <small>
              如需修改预算、记忆、提示词或验证方式，请切换到高级模式。
            </small>
          </article>
        ) : (
          <p className="settings-notice">正在读取默认 Agent 配置…</p>
        )}
      </section>
    );
  }

  return (
    <section className="profile-center" data-testid="agent-profile-center">
      <div className="settings-title">
        <h3>Agent 配置</h3>
        <div>
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
      {formOpen && <div className="editor-surface">
      <div className="editor-header"><div><h4>{editingId ? "编辑 Agent 配置" : "新建 Agent 配置"}</h4><small>每次只显示一个步骤，切换步骤会保留未提交内容。</small></div><button type="button" onClick={() => { setFormOpen(false); setEditingId(null); setVersions([]); }}>取消</button></div>
      <AgentProfileForm
        form={form}
        providers={providers}
        tools={tools}
        expert={false}
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
      </div>}
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
