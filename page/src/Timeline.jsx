import { useState, useEffect } from 'react'
import './Timeline.css'

function Timeline({ taskId }) {
  const [timeline, setTimeline] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!taskId) return
    fetchTimeline()
  }, [taskId])

  async function fetchTimeline() {
    try {
      const res = await fetch(`/api/tasks/${taskId}/timeline`)
      if (!res.ok) {
        setError(`Failed to fetch timeline: ${res.status}`)
        return
      }
      const data = await res.json()
      setTimeline(data)
    } catch (e) {
      setError(String(e))
    }
  }

  if (error) {
    return <div className="timeline-error">Timeline 加载失败: {error}</div>
  }

  if (!timeline || !timeline.events || timeline.events.length === 0) {
    return <div className="timeline-empty">暂无执行过程记录</div>
  }

  const stages = buildStages(timeline.events)

  return (
    <div className="timeline">
      <h3>执行过程</h3>
      {stages.map((stage, i) => (
        <div key={i} className={`stage stage-${stage.status}`}>
          <div className="stage-header">
            <span className="stage-name">{stage.name}</span>
            <span className={`stage-status status-${stage.status}`}>
              {statusLabel(stage.status)}
            </span>
          </div>
          {stage.tools.length > 0 && (
            <ul className="tool-list">
              {stage.tools.map((tool, j) => (
                <li key={j}>
                  {tool.name} ({tool.sample_size} 样本)
                </li>
              ))}
            </ul>
          )}
          {stage.error && <p className="error">{stage.error}</p>}
        </div>
      ))}
    </div>
  )
}

function buildStages(events) {
  const stages = []
  let currentStage = null

  for (const ev of events) {
    if (ev.event_type === 'stage_start') {
      currentStage = { name: ev.payload.stage, tools: [], status: 'running' }
      stages.push(currentStage)
    } else if (ev.event_type === 'tool_call' && currentStage) {
      currentStage.tools.push({
        name: ev.payload.tool,
        sample_size: ev.payload.sample_size || 0,
      })
    } else if (ev.event_type === 'stage_done' && currentStage) {
      currentStage.status = 'done'
    } else if (ev.event_type === 'tool_unauthorized' && currentStage) {
      currentStage.status = 'failed'
      currentStage.error = `未授权工具: ${ev.payload.tool}`
    } else if (ev.event_type === 'stage_failed' && currentStage) {
      currentStage.status = 'failed'
      currentStage.error = ev.payload.error || '执行失败'
    }
  }

  return stages
}

function statusLabel(status) {
  const labels = {
    running: '运行中',
    done: '已完成',
    failed: '失败',
  }
  return labels[status] || status
}

export default Timeline
