/** 可搜索的项目任务列表，包含归档、重命名与删除意图入口。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Archive,
  ArchiveRestore,
  FileText,
  MoreHorizontal,
  Pencil,
  Search,
  Trash2,
  X,
} from "lucide-react";
import type { Thread } from "../types";
import IconButton from "./IconButton";

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
  const [searchOpen, setSearchOpen] = useState(false);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState<{ top: number; left: number } | null>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const visible = threads.filter(
    (item) =>
      (showArchived || !item.archived) &&
      item.title.toLowerCase().includes(query.toLowerCase()),
  );

  useEffect(() => {
    if (!openMenuId) return undefined;
    const closeMenu = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || menuTriggerRef.current?.contains(target)) return;
      setOpenMenuId(null);
      setMenuPosition(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // 菜单是当前最上层交互。捕获并消费 Esc，避免同一次按键继续关闭
      // 底层的新建任务、手机侧栏或其他工作台界面。
      event.preventDefault();
      event.stopPropagation();
      setOpenMenuId(null);
      setMenuPosition(null);
      menuTriggerRef.current?.focus();
    };
    const closeOnViewportChange = () => {
      setOpenMenuId(null);
      setMenuPosition(null);
    };
    document.addEventListener("pointerdown", closeMenu);
    window.addEventListener("keydown", closeOnEscape, true);
    window.addEventListener("resize", closeOnViewportChange);
    document.addEventListener("scroll", closeOnViewportChange, true);
    requestAnimationFrame(() => menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus());
    return () => {
      document.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("keydown", closeOnEscape, true);
      window.removeEventListener("resize", closeOnViewportChange);
      document.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [openMenuId]);

  const menuThread = threads.find((thread) => thread.id === openMenuId);

  function closeActions() {
    setOpenMenuId(null);
    setMenuPosition(null);
  }

  return (
    <section ref={sectionRef} className="thread-section" aria-labelledby="thread-section-title">
      <header className="thread-section-header">
        <span id="thread-section-title">任务</span>
        <div>
          <IconButton
            icon={Search}
            label="搜索任务"
            aria-pressed={searchOpen}
            onClick={() => {
              if (searchOpen) setQuery("");
              setSearchOpen((value) => !value);
            }}
          />
          <IconButton
            icon={showArchived ? ArchiveRestore : Archive}
            label={showArchived ? "隐藏已归档任务" : "显示已归档任务"}
            aria-pressed={showArchived}
            onClick={() => setShowArchived((value) => !value)}
          />
        </div>
      </header>

      {searchOpen && (
        <div className="thread-search-wrap">
          <Search size={15} aria-hidden="true" />
          <input
            autoFocus
            className="thread-search"
            aria-label="搜索任务"
            placeholder="搜索项目中的任务…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {(query || searchOpen) && (
            <IconButton
              icon={X}
              label="关闭任务搜索"
              size={15}
              onClick={() => {
                setQuery("");
                setSearchOpen(false);
              }}
            />
          )}
        </div>
      )}

      <nav className="thread-list" aria-label="项目任务">
        {visible.map((thread) => (
          <div
            key={thread.id}
            className={`thread-row ${selectedId === thread.id ? "selected" : ""}`}
          >
            <button
              className="thread-item"
              aria-current={selectedId === thread.id ? "page" : undefined}
              onClick={() => onSelect(thread.id)}
            >
              <FileText size={16} aria-hidden="true" />
              <span className="thread-copy">
                <span>{thread.title}</span>
                <small>
                  {thread.archived
                    ? "已归档"
                    : `更新于 ${Number.isNaN(new Date(thread.updated_at).getTime()) ? "最近" : new Date(thread.updated_at).toLocaleDateString()}`}
                </small>
              </span>
            </button>
            <IconButton
              icon={MoreHorizontal}
              className="thread-more"
              label={`管理任务 ${thread.title}`}
              aria-expanded={openMenuId === thread.id}
              aria-controls={openMenuId === thread.id ? "thread-action-popover" : undefined}
              onClick={(event) => {
                if (openMenuId === thread.id) {
                  closeActions();
                  return;
                }
                const rect = event.currentTarget.getBoundingClientRect();
                const menuWidth = 154;
                const menuHeight = 126;
                menuTriggerRef.current = event.currentTarget;
                setMenuPosition({
                  top: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - menuHeight - 8)),
                  left: Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8)),
                });
                setOpenMenuId(thread.id);
              }}
            />
          </div>
        ))}
        {visible.length === 0 && (
          <p className="thread-empty">
            {query ? "没有匹配的任务。" : "暂无任务，点击上方按钮创建。"}
          </p>
        )}
      </nav>
      {menuThread && menuPosition && createPortal(
        <div
          ref={menuRef}
          id="thread-action-popover"
          className="thread-menu"
          role="group"
          aria-label={`${menuThread.title} 操作`}
          style={menuPosition}
        >
          <button type="button" onClick={() => { closeActions(); onRename(menuThread); }}>
            <Pencil size={15} aria-hidden="true" />
            重命名
          </button>
          <button type="button" onClick={() => { closeActions(); onToggleArchive(menuThread); }}>
            {menuThread.archived ? <ArchiveRestore size={15} aria-hidden="true" /> : <Archive size={15} aria-hidden="true" />}
            {menuThread.archived ? "恢复任务" : "归档任务"}
          </button>
          <button type="button" className="danger" onClick={() => { closeActions(); onDelete(menuThread); }}>
            <Trash2 size={15} aria-hidden="true" />
            删除任务
          </button>
        </div>,
        document.body,
      )}
    </section>
  );
}
