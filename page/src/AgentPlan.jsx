import './AgentPlan.css'

// V0.4 调查计划/子任务进度/停止原因/评审/预算 视图。
// 仅在 V0.4 任务（result.agent 存在）时由 App 渲染，V0.2/V0.3 任务不受影响。
function AgentPlan({ agent }) {
  if (!agent) return null

  const cards = agent.cards || []
  const results = agent.subtask_results || []
  const review = agent.review || {}
  const budget = agent.budget || {}

  return (
    <div className="agent-plan">
      {/* 主计划（调查卡列表） */}
      <h3>调查计划</h3>
      <ul className="plan-cards">
        {cards.length === 0 && <li className="muted">无调查卡</li>}
        {cards.map((card) => (
          <li key={card.card_id} className="plan-card">
            <span className="plan-priority">P{card.priority}</span>
            <span className="plan-title">{card.title}</span>
            <span className="plan-objective">{card.objective}</span>
          </li>
        ))}
      </ul>

      {/* 子任务进度（每卡停止原因 + 摘要 + 工具调用次数） */}
      <h3>子任务进度</h3>
      <ul className="subtask-list">
        {results.length === 0 && <li className="muted">暂无子任务结果</li>}
        {results.map((r, i) => (
          <li key={i} className={`subtask-item status-${r.stop_reason}`}>
            <span className="subtask-reason">{r.stop_reason}</span>
            <span className="subtask-summary">{r.summary}</span>
            <span className="subtask-tools">工具 × {r.tool_calls_used}</span>
          </li>
        ))}
      </ul>

      {/* 评审（verdict + issues） */}
      <h3>评审</h3>
      <div className="review-box">
        <span className={`review-verdict verdict-${review.verdict}`}>{review.verdict}</span>
        {(review.issues || []).map((iss, i) => (
          <div key={i} className="review-issue">{iss.type}: {iss.detail}</div>
        ))}
      </div>

      {/* 预算（工具调用/子任务 used/limit） */}
      {budget && (
        <div className="budget-box">
          <span>工具调用 {budget.tool_calls?.used ?? 0}/{budget.tool_calls?.limit ?? '—'}</span>
          <span>子任务 {budget.subtasks?.used ?? 0}/{budget.subtasks?.limit ?? '—'}</span>
        </div>
      )}
    </div>
  )
}

export default AgentPlan
