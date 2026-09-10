import { useEffect, useState } from 'react'
import './AuditWorkbench.css'

// 纯函数：从 /timeline 事件构建主子任务树。
// 旧事件（V0.4 之前）缺 result_summary/bias_note 时优雅降级（undefined），不报错。
function buildTree(events) {
  const cards = []

  // 按 card_id 取卡，没有则建占位卡。
  // Orchestrator 对「预算耗尽、从未启动」的卡只写 subtask_stop（无 subtask_start），
  // 此前 cards.find(...) 找不到就静默丢事件，这些卡在工作台里凭空消失——审计读者
  // 看不出主 Agent 原本计划了几张卡、哪几张被预算跳过。
  const cardOf = (cardId) => {
    let card = cards.find((c) => c.card_id === cardId)
    if (!card) {
      card = { card_id: cardId, title: cardId, tools: [], stop_reason: null, summary: '' }
      cards.push(card)
    }
    return card
  }

  for (const ev of events) {
    if (ev.event_type === 'subtask_start') {
      const card = cardOf(ev.payload.card_id)
      // subtask_start 才有真实标题（占位卡先用 card_id 兜底）
      if (ev.payload.title) card.title = ev.payload.title
    } else if (ev.event_type === 'subtask_tool') {
      cardOf(ev.payload.card_id).tools.push({
        tool: ev.payload.tool,
        arguments: ev.payload.arguments || {},
        sample_size: ev.payload.sample_size,
        result_summary: ev.payload.result_summary,
        bias_note: ev.payload.bias_note,
      })
    } else if (ev.event_type === 'subtask_stop') {
      const card = cardOf(ev.payload.card_id)
      card.stop_reason = ev.payload.stop_reason
      card.summary = ev.payload.summary || ''
    }
  }
  return cards
}

// 空态文案（加载失败之外的所有「没有子任务可展示」情形共用一句）
const EMPTY_TEXT = '暂无子任务记录'

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
  const cards = timeline?.events ? buildTree(timeline.events) : []
  if (cards.length === 0) return <div className="audit-empty">{EMPTY_TEXT}</div>

  return (
    <div className="audit-workbench">
      <h3>审计工作台</h3>
      {cards.map((card) => (
        <div key={card.card_id} className="audit-card">
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
                <li key={`${card.card_id}-${j}-${t.tool}`} className="audit-tool">
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
      {tool.sample_size != null && (
        <div className="audit-tool-kv"><span className="audit-kv-label">样本量</span>
          <code className="audit-kv-value">{tool.sample_size}</code></div>
      )}
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
