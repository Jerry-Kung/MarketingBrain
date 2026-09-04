"""确定性分析工具集。

每个工具都：从 snapshot 取时间窗与对象标签（口径一致），调用 datasource
取数/统计，登记证据，返回统一的 TOOL_PROTOCOL 结构。

模型/Agent 不能直接连库——所有数据访问都走这里的工具，或 datasource 预定义方法。
"""
from datetime import datetime, timedelta


def _window(snap):
    return snap.start_time, snap.end_time


def _tags(snap):
    return (snap.extra or {}).get("video_tags") or []


def snap_iso(dt):
    """datetime/date 转 iso 字符串（None 透传）。时间范围字段用它。"""
    return dt.isoformat() if dt else None


def _tool_result(*, name, params, result, evidence, sample_size, snap, bias_note) -> dict:
    """统一构造七键 TOOL_PROTOCOL 返回。

    `evidence` 必须是本次调用内 `store.register_comment/register_video/register_stat`
    返回的 `EvidenceRecord` 列表（即本次调用登记或命中的证据），而不是
    `store.all_records()` 的全量快照——`evidence_ids` 按调用归属，不含其他工具
    此前登记的证据。`EvidenceStore` 是幂等的：重复登记同一条评论/视频会返回
    已存在的记录（沿用其原 `evidence_id`），这也算作本次调用"命中"了该证据。
    """
    start, end = _window(snap)
    return {
        "name": name,
        "params": params,
        "result": result,
        "evidence_ids": [e.evidence_id for e in evidence],
        "sample_size": sample_size,
        "time_range": {"start": snap_iso(start), "end": snap_iso(end)},
        "bias_note": bias_note,
    }


def data_coverage(ds, snap, store):
    start, end = _window(snap)
    n = ds.count_comments(start_time=start, end_time=end, video_tags=_tags(snap))
    stat = store.register_stat(label="comment_count", detail="窗口内评论数", count=n, source="data_coverage")
    return _tool_result(
        name="data_coverage",
        params={"start_time": snap_iso(start), "end_time": snap_iso(end),
                "video_tags": _tags(snap)},
        result={"comment_count": n, "datasource": "api_job"},
        evidence=[stat],
        sample_size=n,
        snap=snap,
        bias_note="统计基于 api_job 成功作业及其展开评论，未做全量向量化主题聚类。",
    )


def volume_trend(ds, snap, store):
    start, end = _window(snap)
    ts = ds.time_series(start_time=start, end_time=end, video_tags=_tags(snap), bucket="day")
    stat = store.register_stat(label="trend_total", detail="窗口总体声量", count=ts["total"], source="volume_trend")
    return _tool_result(
        name="volume_trend",
        params={"start_time": snap_iso(start), "end_time": snap_iso(end),
                "video_tags": _tags(snap), "bucket": "day"},
        result=ts,
        evidence=[stat],
        sample_size=ts["total"],
        snap=snap,
        bias_note="时间锚点取作业 created_at(UTC)，非评论发布时间。",
    )


def _prev_window(start, end):
    """等长的前一周期。"""
    if start is None or end is None:
        return None, None
    delta = end - start
    return start - delta, start


def period_comparison(ds, snap, store):
    start, end = _window(snap)
    cur = ds.count_comments(start_time=start, end_time=end, video_tags=_tags(snap))
    pstart, pend = _prev_window(start, end)
    prev = ds.count_comments(start_time=pstart, end_time=pend, video_tags=_tags(snap)) \
        if pstart and pend else 0
    change_rate = round((cur - prev) / prev * 100, 1) if prev else None
    stat = store.register_stat(label="comparison", detail="当前vs对比", count=cur, source="period_comparison")
    return _tool_result(
        name="period_comparison",
        params={"current": {"start": snap_iso(start), "end": snap_iso(end)},
                "prev": {"start": snap_iso(pstart), "end": snap_iso(pend)}},
        result={"current": cur, "previous": prev, "change_rate": change_rate,
                "note": "对比周期为当前窗口等长的前一周期"},
        evidence=[stat],
        sample_size=cur,
        snap=snap,
        bias_note="变化率基于作业 created_at 分桶；prev=0 时 change_rate 为 null。",
    )


def topic_frequency_tool(ds, snap, store):
    start, end = _window(snap)
    tags = _tags(snap)
    tf = ds.topic_frequency(start_time=start, end_time=end, video_tags=tags, limit=50)
    # 剔除对象本身的话题标签（如 #坦克300 会占据首位），只留子主题
    obj = (snap.extra or {}).get("object")
    topics = [t for t in tf if t["topic"] != obj]
    stat = store.register_stat(label="topics", detail="候选子主题数", count=len(topics), source="topic_frequency_tool")
    return _tool_result(
        name="topic_frequency_tool",
        params={"video_tags": tags, "limit": 50},
        result={"topics": topics},
        evidence=[stat],
        sample_size=len(topics),
        snap=snap,
        bias_note="主题来自视频标题的话题标签，非全量评论分词，可能遗漏隐含表达。",
    )


def top_sources(ds, snap, store, limit=10):
    start, end = _window(snap)
    vids = ds.top_videos(start_time=start, end_time=end, video_tags=_tags(snap), limit=limit)
    evidence = [
        store.register_video(job_id=v["job_id"], video_title=v["video_title"],
                             comment_count=v["comment_count"], source="top_sources")
        for v in vids
    ]
    return _tool_result(
        name="top_sources",
        params={"limit": limit, "video_tags": _tags(snap)},
        result={"videos": vids},
        evidence=evidence,
        sample_size=len(vids),
        snap=snap,
        bias_note="按评论数排序，代表声量集中来源；不代表全部传播渠道。",
    )


def sample_comments(ds, snap, store, limit=30, keyword=None):
    start, end = _window(snap)
    recs = ds.fetch_comments(
        limit=limit, start_time=start, end_time=end, video_tags=_tags(snap),
        keyword=keyword, order_by="random",
    )
    evidence = [store.register_comment(r, source="sample_comments") for r in recs]
    return _tool_result(
        name="sample_comments",
        params={"limit": limit, "keyword": keyword, "video_tags": _tags(snap)},
        result={"comments": [r.to_evidence() for r in recs]},
        evidence=evidence,
        sample_size=len(recs),
        snap=snap,
        bias_note=f"随机抽样 {len(recs)} 条代表评论，非全量，存在抽样偏差。",
    )


def drill_evidence(ds, snap, store, *, keyword=None, min_like=None, limit=30):
    start, end = _window(snap)
    recs = ds.fetch_comments(
        limit=limit, start_time=start, end_time=end, video_tags=_tags(snap),
        keyword=keyword, min_like=min_like, order_by="likes",
    )
    evidence = [store.register_comment(r, source="drill_evidence") for r in recs]
    return _tool_result(
        name="drill_evidence",
        params={"keyword": keyword, "min_like": min_like, "limit": limit},
        result={"comments": [r.to_evidence() for r in recs]},
        evidence=evidence,
        sample_size=len(recs),
        snap=snap,
        bias_note="按点赞数降序取高互动评论，偏向高关注样本。",
    )


def object_compare(ds, snap, store, *, other_tags=None, limit=10):
    start, end = _window(snap)
    cur = ds.count_comments(start_time=start, end_time=end, video_tags=_tags(snap))
    compared = bool(other_tags)
    other = ds.count_comments(start_time=start, end_time=end, video_tags=other_tags) if compared else None
    stat = store.register_stat(label="object_compare", detail="对象 vs 参照", count=cur, source="object_compare")
    if compared:
        bias_note = "对象间比较仅基于同窗口声量，未做质量或情感维度比较。"
    else:
        bias_note = "未提供参照对象标签（other_tags），本次未做对比，仅返回对象自身声量。"
    return _tool_result(
        name="object_compare",
        params={"object": (snap.extra or {}).get("object"), "other_tags": other_tags},
        result={"object_count": cur, "other_count": other, "compared": compared,
                "other_tags": other_tags if compared else None},
        evidence=[stat],
        sample_size=cur,
        snap=snap,
        bias_note=bias_note,
    )
