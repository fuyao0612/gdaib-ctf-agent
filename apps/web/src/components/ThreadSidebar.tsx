/** 可搜索的任务历史，包含归档、重命名与删除意图入口。 */
import { useState } from "react";
import type { Thread } from "../types";

interface Props {
  threads: Thread[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onRename: (thread: Thread) => void;
  onToggleArchive: (thread: Thread) => void;
  onDelete: (thread: Thread) => void;
}

/** 任务历史只处理筛选和用户意图，数据变更仍由工作台统一协调。 */
export default function ThreadSidebar({
  threads,
  selectedId,
  onSelect,
  onRename,
  onToggleArchive,
  onDelete,
}: Props) {
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const visible = threads.filter(
    (item) =>
      (showArchived || !item.archived) &&
      item.title.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <>
      <input
        className="thread-search"
        aria-label="搜索任务"
        placeholder="搜索任务…"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <label className="archive-toggle">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(event) => setShowArchived(event.target.checked)}
        />
        显示已归档
      </label>
      <nav className="thread-list" aria-label="任务历史">
        {visible.map((thread) => (
          <div
            key={thread.id}
            className={`thread-row ${selectedId === thread.id ? "selected" : ""}`}
          >
            <button className="thread-item" onClick={() => onSelect(thread.id)}>
              <span>{thread.title}</span>
              <small>
                {thread.archived
                  ? "已归档"
                  : `更新于 ${new Date(thread.updated_at).toLocaleDateString()}`}
              </small>
            </button>
            <div className="thread-actions">
              <button
                aria-label={`重命名 ${thread.title}`}
                onClick={() => onRename(thread)}
              >
                ✎
              </button>
              <button
                aria-label={`${thread.archived ? "恢复" : "归档"} ${thread.title}`}
                onClick={() => onToggleArchive(thread)}
              >
                {thread.archived ? "↥" : "⌁"}
              </button>
              <button
                aria-label={`删除 ${thread.title}`}
                onClick={() => onDelete(thread)}
              >
                ×
              </button>
            </div>
          </div>
        ))}
        {visible.length === 0 && (
          <p className="thread-empty">
            {query ? "没有匹配的任务。" : "暂无任务，点击上方按钮创建。"}
          </p>
        )}
      </nav>
    </>
  );
}
