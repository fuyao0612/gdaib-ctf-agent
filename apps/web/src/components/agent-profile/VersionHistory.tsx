/** 展示不可变历史版本，并把回滚动作交给上层数据协调器。 */
import type { AgentProfile } from "../../types";

interface Props {
  versions: AgentProfile[];
  onRollback: (version: number) => void;
}

export default function VersionHistory({ versions, onRollback }: Props) {
  if (versions.length === 0) return null;

  return (
    <section className="version-history">
      <h4>版本历史与对比</h4>
      {versions.map((profile) => (
        <details key={profile.version}>
          <summary>
            v{profile.version} · {profile.created_at}
            <button onClick={() => onRollback(profile.version)}>
              回滚到此版本
            </button>
          </summary>
          <pre>{JSON.stringify(profile, null, 2)}</pre>
        </details>
      ))}
    </section>
  );
}
