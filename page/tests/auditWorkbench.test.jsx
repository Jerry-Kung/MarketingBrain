import { describe, it, expect } from 'vitest'
import { buildTree } from '../src/AuditWorkbench'
import { matchesStatus } from '../src/taskFilter'

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

  it('卡从未启动（预算跳过）时也保留：无 subtask_start 的事件不丢弃', () => {
    // Orchestrator 在 counter.subtask_exceeded() 时对未启动的卡直接写
    // subtask_stop(budget_exhausted)，不写 subtask_start。此前 cards.find(...)
    // 找不到卡就静默丢事件，这些「被预算跳过」的卡在工作台里凭空消失。
    const events = [
      { event_type: 'subtask_start', payload: { card_id: 'c1', title: '已跑的卡' } },
      { event_type: 'subtask_stop', payload: { card_id: 'c1', stop_reason: 'evidence_sufficient' } },
      { event_type: 'subtask_stop', payload: { card_id: 'c2', stop_reason: 'budget_exhausted' } },
    ]
    const tree = buildTree(events)
    expect(tree).toHaveLength(2)
    const skipped = tree.find((c) => c.card_id === 'c2')
    expect(skipped).toBeDefined()
    expect(skipped.stop_reason).toBe('budget_exhausted')
    // 占位卡缺 title 时用 card_id 兜底，保证列表可读
    expect(skipped.title).toBe('c2')
    expect(skipped.tools).toEqual([])
  })

  it('subtask_tool 先于 subtask_start 到达时也建占位卡', () => {
    const events = [
      { event_type: 'subtask_tool', payload: { card_id: 'c9', tool: 'data_coverage', arguments: {} } },
    ]
    const tree = buildTree(events)
    expect(tree).toHaveLength(1)
    expect(tree[0].card_id).toBe('c9')
    expect(tree[0].tools).toHaveLength(1)
  })
})

describe('任务状态筛选谓词', () => {
  it('all 放行全部状态，具体状态只放行同状态', () => {
    const tasks = [
      { task_id: 'a', status: 'success' },
      { task_id: 'b', status: 'failed' },
      { task_id: 'c', status: 'running' },
      { task_id: 'd', status: 'pending' },
    ]
    expect(tasks.filter((t) => matchesStatus(t, 'all'))).toHaveLength(4)
    expect(tasks.filter((t) => matchesStatus(t, 'success')).map((t) => t.task_id)).toEqual(['a'])
    expect(tasks.filter((t) => matchesStatus(t, 'failed')).map((t) => t.task_id)).toEqual(['b'])
    expect(tasks.filter((t) => matchesStatus(t, 'running')).map((t) => t.task_id)).toEqual(['c'])
  })

  it('状态缺失的任务只在 all 下出现', () => {
    const t = { task_id: 'x' }
    expect(matchesStatus(t, 'all')).toBe(true)
    expect(matchesStatus(t, 'success')).toBe(false)
  })
})
