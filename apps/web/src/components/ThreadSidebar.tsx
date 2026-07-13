/** 可搜索的对话导航，包含归档、重命名与删除意图入口。 */
import { useState } from 'react'
import type { Thread } from '../types'

interface Props {
  threads: Thread[]
  selectedId?: string
  onSelect: (id: string) => void
  onRename: (thread: Thread) => void
  onToggleArchive: (thread: Thread) => void
  onDelete: (thread: Thread) => void
}

/** 对话导航只处理筛选和用户意图，数据变更仍由工作台统一协调。 */
export default function ThreadSidebar({ threads, selectedId, onSelect, onRename, onToggleArchive, onDelete }: Props) {
  const [query, setQuery] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const visible = threads.filter(item =>
    (showArchived || !item.archived) && item.title.toLowerCase().includes(query.toLowerCase()),
  )

  return <>
    <input className="thread-search" aria-label="搜索对话" placeholder="搜索对话…" value={query} onChange={event => setQuery(event.target.value)} />
    <label className="archive-toggle"><input type="checkbox" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />显示已归档</label>
    <nav className="thread-list">{visible.map(thread => <div key={thread.id} className={`thread-row ${selectedId === thread.id ? 'selected' : ''}`}>
      <button className="thread-item" onClick={() => onSelect(thread.id)}><span>{thread.title}</span><small>{thread.archived ? '已归档' : thread.mode}</small></button>
      <div className="thread-actions"><button aria-label={`重命名 ${thread.title}`} onClick={() => onRename(thread)}>✎</button><button aria-label={`${thread.archived ? '恢复' : '归档'} ${thread.title}`} onClick={() => onToggleArchive(thread)}>{thread.archived ? '↥' : '⌁'}</button><button aria-label={`删除 ${thread.title}`} onClick={() => onDelete(thread)}>×</button></div>
    </div>)}</nav>
  </>
}
