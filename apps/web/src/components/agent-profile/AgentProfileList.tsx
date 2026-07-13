/** 只负责展示 Agent 配置及触发列表操作，不持有远端数据。 */
import type { AgentProfile } from "../../types";

interface Props {
  profiles: AgentProfile[];
  onEdit: (profile: AgentProfile) => void;
  onShowVersions: (profile: AgentProfile) => void;
  onCopy: (profile: AgentProfile) => void;
  onMakeDefault: (profile: AgentProfile) => void;
  onRemove: (profile: AgentProfile) => void;
}

export default function AgentProfileList({
  profiles,
  onEdit,
  onShowVersions,
  onCopy,
  onMakeDefault,
  onRemove,
}: Props) {
  return (
    <div className="provider-table">
      {profiles.map((profile) => (
        <article
          className={`provider-row ${profile.is_default ? "default" : ""}`}
          key={profile.profile_id}
        >
          <div>
            <strong>{profile.name}</strong>
            <small>
              v{profile.version} · {profile.completion_mode} · {profile.run_mode}
            </small>
            <small>{profile.description || "无说明"}</small>
          </div>
          <div className="provider-flags">
            {profile.is_default && <span>默认</span>}
            {!profile.enabled && <span>停用</span>}
          </div>
          <div>
            <button onClick={() => onEdit(profile)}>编辑</button>
            <button onClick={() => onShowVersions(profile)}>版本</button>
            <button onClick={() => onCopy(profile)}>复制</button>
            <button
              disabled={profile.is_default}
              onClick={() => onMakeDefault(profile)}
            >
              设为默认
            </button>
            <button
              className="danger"
              disabled={profile.is_default}
              onClick={() => onRemove(profile)}
            >
              删除
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}
