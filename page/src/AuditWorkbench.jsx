import { useEffect, useState } from 'react'
import './AuditWorkbench.css'

// 纯函数：从 /timeline 事件构建主子任务树。
// 旧事件（V0.4 之前）缺 result_summary/bias_note 时优雅降级（undefined），不报错。
function buildTree(events) {
  const cards = []
  for (const ev of events) {
    if (ev.event_type === 'subtask_start') {
      cards.push({ card_id: ev.payload.card_id, title: ev.payload.title, tools: [], stop_reason: null, summary: '' })
    } else if (ev.event_type === 'subtask_tool') {
      const card = cards.find((c) => c.card_id === ev.payload.card_id)
      if (card) card.tools.push({
        tool: ev.payload.tool,
        arguments: ev.payload.arguments || {},
        result_summary: ev.payload.result_summary,
        bias_note: ev.payload.bias_note,
      })
    } else if (ev.event_type === 'subtask_stop') {
      const card = cards.find((c) => c.card_id === ev.payload.card_id)
      if (card) {
        card.stop_reason = ev.payload.stop_reason
        card.summary = ev.payload.summary || ''
      }
    }
  }
  return cards
}

// 组件：接收 taskId，自取 /api/tasks/{taskId}/timeline（与 <Timeline taskId> 各自独立取数）。
export default function AuditWorkbench({ taskId }) {
  const [timeline, setTimeline] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!taskId) return
    let alive = true
    fetch(`/api/tasks/${taskId}/timeline`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => { if (alive) setTimeline(data) })
      .catch((e) => { if (alive) setError(String(e)) })
    return () => { alive = false }
  }, [taskId])

  if (error) return <div className="audit-empty">审计数据加载失败：{error}</div>
  if (!timeline || !timeline.events || timeline.events.length === 0) {
    return <div className="audit-empty">暂无子任务记录</div>
  }

  const cards = buildTree(timeline.events)
  if (cards.length === 0) return <div className="audit-empty">无子任务记录</div>

  return (
    <div className="audit-workbench">
      <h3>审计工作台</h3>
      {cards.map((card, i) => (
        <div key={i} className="audit-card">
          <div className="audit-card-head">
            <span className="audit-card-title">{card.title}</span>
            {card.stop_reason && (
              <span className="audit-stop">停止：{stopLabel(card.stop_reason)}</span>
            )}
          </div>
          {card.summary && <p className="audit-summary">{card.summary}</p>}
          {card.tools.length > 0 && (
            <ul className="audit-tools">
              {card.tools.map((t, j) => (
                <li key={j} className="audit-tool">
                  <span className="audit-tool-name">{t.tool}</span>
                  <ToolDetail tool={t} />
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  )
}

function ToolDetail({ tool }) {
  return (
    <div className="audit-tool-detail">
      <div className="audit-tool-kv"><span className="audit-kv-label">参数</span>
        <code className="audit-kv-value">{JSON.stringify(tool.arguments)}</code></div>
      {tool.result_summary && (
        <div className="audit-tool-kv"><span className="audit-kv-label">返回</span>
          <code className="audit-kv-value">{JSON.stringify(tool.result_summary)}</code></div>
      )}
      {tool.bias_note && <p className="audit-bias">{tool.bias_note}</p>}
    </div>
  )
}

function stopLabel(reason) {
  const map = {
    evidence_sufficient: '证据充分',
    data_insufficient: '数据不足',
    budget_exhausted: '预算耗尽',
    tool_failure: '工具失败',
    illegal_output: '输出非法',
  }
  return map[reason] || reason
}

export { buildTree }
