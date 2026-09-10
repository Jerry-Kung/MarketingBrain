import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import ReportView, { buildRefMap } from '../src/ReportView'

// V0.5 双向反查前端覆盖：buildRefMap（证据→结论）+ evidence_refs 解析（结论→证据）。

describe('buildRefMap', () => {
  it('按 evidence_id 建立 referenced_by 映射', () => {
    const evidence = [
      { evidence_id: 'ev-1', referenced_by: ['j-1', 'j-2'] },
      { evidence_id: 'ev-2', referenced_by: [] },
      { evidence_id: 'ev-3' },
    ]
    const m = buildRefMap(evidence)
    expect(m['ev-1']).toEqual(['j-1', 'j-2'])
    expect(m['ev-2']).toEqual([])
    // referenced_by 缺失时回退空数组，不产生 undefined
    expect(m['ev-3']).toEqual([])
  })

  it('证据为空/未定义时返回空映射，不抛错', () => {
    expect(buildRefMap(undefined)).toEqual({})
    expect(buildRefMap([])).toEqual({})
  })
})

describe('ReportView 的 evidence_refs 解析（结论→证据）', () => {
  const task = { task_id: 't1', status: 'success' }

  it('themes 的 evidence_refs 按 evidence_id 展开证据文本', () => {
    const result = {
      report: { themes: [{ title: '油耗争议', evidence_refs: ['ev-1'] }] },
      evidence: [{
        evidence_id: 'ev-1', kind: 'comment', comment_id: 'c1',
        content: '这车油耗真的高', referenced_by: ['j-1'],
      }],
    }
    const { container } = render(<ReportView task={task} result={result} />)
    const list = container.querySelector('.report-evidence-list')
    expect(list).not.toBeNull()
    expect(list.textContent).toContain('这车油耗真的高')
    expect(list.textContent).toContain('ev-1')
  })

  it('risk_opportunity 的 evidence_refs 同样解析，stat 证据用 extra.label 兜底', () => {
    const result = {
      report: { risk_opportunity: [{ title: '声量下滑', type: 'risk', evidence_refs: ['ev-s'] }] },
      evidence: [{
        evidence_id: 'ev-s', kind: 'stat', content: '', video_title: '',
        extra: { label: '窗口评论数' }, referenced_by: ['j-1'],
      }],
    }
    const { container } = render(<ReportView task={task} result={result} />)
    const list = container.querySelector('.report-evidence-list')
    expect(list).not.toBeNull()
    expect(list.textContent).toContain('窗口评论数')
  })

  it('evidence_refs 指向不存在的 id 时不渲染证据块，也不抛错', () => {
    const result = {
      report: { themes: [{ title: '幽灵引用', evidence_refs: ['nope'] }] },
      evidence: [{ evidence_id: 'ev-1', kind: 'comment', content: 'x', referenced_by: [] }],
    }
    const { container } = render(<ReportView task={task} result={result} />)
    expect(container.querySelector('.report-evidence-list')).toBeNull()
  })

  it('原有 refs/comment_id/job_id 解析行为保持不变', () => {
    const result = {
      report: { sources: [{ video_title: 'v', job_id: 'j1' }], themes: [{ theme: '油耗', refs: ['c1'] }] },
      evidence: [
        { evidence_id: 'ev-1', kind: 'comment', comment_id: 'c1', content: '按 comment_id 命中', referenced_by: [] },
        { evidence_id: 'ev-2', kind: 'video', job_id: 'j1', video_title: '按 job_id 命中', referenced_by: [] },
      ],
    }
    const { container } = render(<ReportView task={task} result={result} />)
    const text = Array.from(container.querySelectorAll('.report-evidence-list'))
      .map((n) => n.textContent).join(' ')
    expect(text).toContain('按 comment_id 命中')
    expect(text).toContain('按 job_id 命中')
  })
})
