"""V0.2 固定分析流水线。

按固定顺序执行确定性工具，产出 AnalysisBundle 作为 LLM 报告生成的输入。
不做任何 LLM 自主规划；阶段顺序由本模块硬编码。
"""
from dataclasses import dataclass


@dataclass
class AnalysisBundle:
    scope: dict
    overall: dict
    themes: dict
    sources: dict
    samples: list[dict]
    stats: list[dict]

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "overall": self.overall,
            "themes": self.themes,
            "sources": self.sources,
            "samples": self.samples,
            "stats": self.stats,
        }


class BaselinePipeline:
    """固定顺序：①范围 ②总体 ③主题 ④来源 ⑤抽样 ⑥下钻 ⑦比较。

    数据集（datasource）+ 日志快照（snapshot）+ 证据库（evidence_store）。
    toolset 可注入（测试用 stub）；默认用 app.analysis.tools 的 8 个工具。
    """

    def __init__(self, datasource, snapshot, evidence_store, toolset=None):
        self.datasource = datasource
        self.snapshot = snapshot
        self.evidence_store = evidence_store
        if toolset is None:
            from app.analysis import tools
            self._tools = tools
        else:
            self._tools = toolset

    def run(self) -> AnalysisBundle:
        t = self._tools
        ds, snap, store = self.datasource, self.snapshot, self.evidence_store

        coverage = t.data_coverage(ds, snap, store)
        trend = t.volume_trend(ds, snap, store)
        comparison = t.period_comparison(ds, snap, store)
        theme_res = t.topic_frequency_tool(ds, snap, store)
        source_res = t.top_sources(ds, snap, store)
        sample_res = t.sample_comments(ds, snap, store)
        drill_res = t.drill_evidence(ds, snap, store)
        compare_res = t.object_compare(ds, snap, store)

        stats = [coverage, trend, comparison, theme_res, source_res, sample_res,
                 drill_res, compare_res]

        scope = {
            "comment_count": coverage["result"].get("comment_count"),
            "time_range": coverage.get("time_range"),
            "datasource": coverage["result"].get("datasource"),
            "bias_note": coverage.get("bias_note"),
        }
        overall = {
            "current": trend["result"].get("total"),
            "previous": comparison["result"].get("previous"),
            "change_rate": comparison["result"].get("change_rate"),
            "bias_note": trend.get("bias_note"),
        }
        themes = {
            "topics": theme_res["result"].get("topics", []),
            "bias_note": theme_res.get("bias_note"),
        }
        sources = {
            "videos": source_res["result"].get("videos", []),
            "bias_note": source_res.get("bias_note"),
        }
        samples = sample_res["result"].get("comments", [])

        return AnalysisBundle(
            scope=scope,
            overall=overall,
            themes=themes,
            sources=sources,
            samples=samples,
            stats=stats,
        )
