import { describe, it, expect } from 'vitest'
import { buildTree } from '../src/AuditWorkbench'

describe('AuditWorkbench buildTree', () => {
  it('按卡分组工具调用并提取停止原因', () => {
    const events = [
      { event_type: 'subtask_start', payload: { card_id: 'c1', title: '主题' } },
      { event_type: 'subtask_tool', payload: { card_id: 'c1', tool: 'topic_frequency_tool', arguments: {}, result_summary: { topic_count: 3 }, bias_note: 'b' } },
      { event_type: 'subtask_stop', payload: { card_id: 'c1', stop_reason: 'evidence_sufficient' } },
    ]
    const tree = buildTree(events)
    expect(tree).toHaveLength(1)
    expect(tree[0].tools).toHaveLength(1)
    expect(tree[0].tools[0].result_summary.topic_count).toBe(3)
    expect(tree[0].stop_reason).toBe('evidence_sufficient')
  })

  it('旧事件缺 result_summary 时优雅降级', () => {
    const events = [
      { event_type: 'subtask_start', payload: { card_id: 'c1', title: 't' } },
      { event_type: 'subtask_tool', payload: { card_id: 'c1', tool: 'sample_comments', arguments: { limit: 5 } } },
      { event_type: 'subtask_stop', payload: { card_id: 'c1', stop_reason: 'evidence_sufficient' } },
    ]
    const tree = buildTree(events)
    expect(tree[0].tools[0].result_summary).toBeUndefined()
  })
})
