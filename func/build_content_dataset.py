"""从 source/metadata.parquet 抽 300 条英文短视频做内容分析数据集：分层随机抽样 + creator 上限，固定种子可复现。"""

import json
import random
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "source" / "metadata.parquet"
OUTPUT = ROOT / "source" / "content_dataset.json"
REPORT = ROOT / "source" / "content_dataset_report.json"

SEED = 42
TARGET = 300
MAX_PER_CREATOR = 3
MIN_TRANSCRIPT_CHARS = 20
MAX_DURATION_MS = 60000
DURATION_BUCKETS = [("0-15s", 0, 15000), ("15-30s", 15000, 30000), ("30-60s", 30000, 60000)]
KEEP_FIELDS = ("video_id", "web_url", "creator", "transcript", "description", "hashtags",
               "date_posted", "language", "duration_ms")


def load_rows():
    """读 parquet 并逐级过滤，返回 (通过的行, 各级过滤计数, 异常明细)。"""
    table = pq.ParquetFile(SOURCE).read()
    dropped = Counter()
    anomalies = []
    rows = []
    for i in range(table.num_rows):
        get = lambda name: table.column(name)[i].as_py()
        transcript = get("transcript")
        duration_ms = get("duration_ms")
        eng = json.loads(get("engagement_metrics"))
        if not transcript or not transcript.strip():
            dropped["transcript 为空"] += 1
            continue
        if len(transcript) < MIN_TRANSCRIPT_CHARS:
            dropped[f"transcript 少于 {MIN_TRANSCRIPT_CHARS} 字符"] += 1
            continue
        if duration_ms <= 0:
            dropped["duration_ms <= 0"] += 1
            continue
        if duration_ms > MAX_DURATION_MS:
            dropped["duration_ms > 60s"] += 1
            continue
        if eng["play_count"] <= 0:
            dropped["play_count <= 0"] += 1
            continue
        if min(eng["like_count"], eng["comment_count"], eng["share_count"]) < 0:
            dropped["互动量为负数"] += 1
            continue
        # 互动量超过播放量属于数据错误（不是表现差），例如 like_count 1,462,531 > play_count 318,355
        bad = [k for k in ("like_count", "comment_count", "share_count")
               if eng[k] > eng["play_count"]]
        if bad:
            dropped["互动量超过播放量（数据错误）"] += 1
            anomalies.append({"video_id": get("video_id"), "play_count": eng["play_count"],
                              "字段": bad, **{k: eng[k] for k in bad}})
            continue
        rows.append({
            "video_id": get("video_id"), "web_url": get("web_url"), "creator": get("creator"),
            "transcript": transcript, "description": get("description"),
            "hashtags": json.loads(get("hashtags") or "[]"),
            "date_posted": get("date_posted"), "language": json.loads(get("language")),
            "duration_ms": duration_ms,
            "play_count": eng["play_count"], "like_count": eng["like_count"],
            "comment_count": eng["comment_count"], "share_count": eng["share_count"],
        })
    for r in rows:
        r["like_rate"] = r["like_count"] / r["play_count"]
        r["comment_rate"] = r["comment_count"] / r["play_count"]
        r["share_rate"] = r["share_count"] / r["play_count"]
    return rows, dropped, anomalies


def duration_bucket(duration_ms):
    for name, low, high in DURATION_BUCKETS:
        if low < duration_ms <= high:
            return name
    return None


def main():
    rng = random.Random(SEED)
    raw_count = pq.ParquetFile(SOURCE).metadata.num_rows
    rows, dropped, anomalies = load_rows()
    filtered_count = len(rows)

    english = [r for r in rows if r["language"].get("desc_language") == "en"]
    if len(english) < TARGET:
        print(f"英语数据只有 {len(english)} 条，不足 {TARGET} 条，停止（不混入其他语言）")
        return 1

    # creator 上限：先按 creator 随机保留至多 3 条，再分层抽样，保证最终不会有频道超限
    by_creator = {}
    for r in english:
        by_creator.setdefault(r["creator"], []).append(r)
    pool = []
    for creator in sorted(by_creator):
        members = sorted(by_creator[creator], key=lambda r: r["video_id"])
        pool.extend(rng.sample(members, min(MAX_PER_CREATOR, len(members))))
    capped_count = len(pool)

    # 分层：时长档 × play_count 三分位
    plays = sorted(r["play_count"] for r in pool)
    q1, q2 = plays[len(plays) // 3], plays[2 * len(plays) // 3]

    def play_tier(play_count):
        return "low" if play_count <= q1 else "medium" if play_count <= q2 else "high"

    strata = {}
    for r in pool:
        strata.setdefault((duration_bucket(r["duration_ms"]), play_tier(r["play_count"])), []).append(r)
    for members in strata.values():
        members.sort(key=lambda r: r["video_id"])

    # 按层占比分配目标数量，用最大余数法凑齐 300
    allocations = {key: len(members) * TARGET / capped_count for key, members in strata.items()}
    targets = {key: int(value) for key, value in allocations.items()}
    for key in sorted(allocations, key=lambda k: allocations[k] - int(allocations[k]), reverse=True)[:TARGET - sum(targets.values())]:
        targets[key] += 1

    chosen, fills = [], []
    for key in sorted(strata, key=lambda k: (DURATION_BUCKETS.index(next(b for b in DURATION_BUCKETS if b[0] == k[0])), k[1])):
        members = strata[key]
        take = min(targets[key], len(members))
        chosen.extend(rng.sample(members, take) if take else [])
        fills.append({"duration": key[0], "play_tier": key[1], "目标": targets[key],
                      "实际": take, "该层总数": len(members)})

    # 层内样本不足时，从还有余量的层按余量大小补足
    deficit = TARGET - len(chosen)
    while deficit > 0:
        spare = [(k, len(m) - sum(1 for r in chosen if (duration_bucket(r["duration_ms"]), play_tier(r["play_count"])) == k))
                 for k, m in strata.items()]
        spare = [s for s in spare if s[1] > 0]
        if not spare:
            break
        key, room = max(spare, key=lambda s: (s[1], s[0]))
        picked = rng.sample([r for r in strata[key] if r not in chosen], min(deficit, room))
        chosen.extend(picked)
        deficit -= len(picked)

    for r in chosen:
        r["like_rate"] = round(r["like_rate"], 6)
        r["comment_rate"] = round(r["comment_rate"], 6)
        r["share_rate"] = round(r["share_rate"], 6)
    dataset = [{k: r[k] for k in KEEP_FIELDS} | {k: r[k] for k in
               ("play_count", "like_count", "comment_count", "share_count",
                "like_rate", "comment_rate", "share_rate")} for r in chosen]

    problems = check(dataset)
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    duration_dist = Counter(duration_bucket(r["duration_ms"]) for r in dataset)
    play_dist = Counter(play_tier(r["play_count"]) for r in dataset)
    creator_counts = Counter(r["creator"] for r in dataset)
    report = {
        "原始数据数量": raw_count,
        "基础过滤后数量": filtered_count,
        "基础过滤丢弃明细": dict(dropped),
        "异常值明细": anomalies,
        "英语数据数量": len(english),
        "creator 上限后数量": capped_count,
        "最终数量": len(dataset),
        "分层依据": {
            "duration 档": [f"({low/1000:g}s, {high/1000:g}s]" for _, low, high in DURATION_BUCKETS],
            "play_count 三分位": {"低": q1, "高": q2, "划分": f"low <= {q1} < medium <= {q2} < high",
                                  "分位数基于": "creator 上限后的英语池"},
        },
        "duration 分布": dict(duration_dist),
        "play_count 分层分布": dict(play_dist),
        "各层目标与实际": fills,
        "补足过程": {"需补足": TARGET - sum(f["实际"] for f in fills), "补后总数": len(dataset)},
        "creator 数量": len(creator_counts),
        "每个 creator 最大样本数": max(creator_counts.values()),
        "随机种子": SEED,
        "质量检查": problems or "全部通过",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"原始 {raw_count} -> 基础过滤后 {filtered_count} -> 英语 {len(english)} "
          f"-> creator 上限后 {capped_count} -> 最终 {len(dataset)}")
    print(f"duration 分布: {dict(duration_dist)}")
    print(f"play_count 分层: {dict(play_dist)}")
    print(f"creator {len(creator_counts)} 个，单一 creator 最多 {max(creator_counts.values())} 条")
    print(f"质量检查: {'全部通过' if not problems else problems}")
    return 1 if problems else 0


def check(dataset):
    """质量检查，返回问题清单（不自动修复）。"""
    problems = []
    if len(dataset) != TARGET:
        problems.append(f"条数不是 {TARGET}，实际 {len(dataset)}")
    if len({r["video_id"] for r in dataset}) != len(dataset):
        problems.append("video_id 存在重复")
    over = {c: n for c, n in Counter(r["creator"] for r in dataset).items() if n > MAX_PER_CREATOR}
    if over:
        problems.append(f"creator 超过 {MAX_PER_CREATOR} 条: {over}")
    blank = [r["video_id"] for r in dataset if not r["transcript"] or not r["transcript"].strip()]
    if blank:
        problems.append(f"transcript 为空: {blank[:5]}")
    short = [r["video_id"] for r in dataset if len(r["transcript"]) < MIN_TRANSCRIPT_CHARS]
    if short:
        problems.append(f"transcript 短于 {MIN_TRANSCRIPT_CHARS} 字符: {short[:5]}")
    bad_dur = [r["video_id"] for r in dataset if not 0 < r["duration_ms"] <= MAX_DURATION_MS]
    if bad_dur:
        problems.append(f"duration 越界: {bad_dur[:5]}")
    if [r["video_id"] for r in dataset if r["play_count"] <= 0]:
        problems.append("存在 play_count <= 0")
    for field in ("like_count", "comment_count", "share_count"):
        bad = [r["video_id"] for r in dataset
               if not isinstance(r[field], int) or isinstance(r[field], bool) or r[field] < 0]
        if bad:
            problems.append(f"{field} 非非负整数: {bad[:5]}")
    for field in ("like_rate", "comment_rate", "share_rate"):
        bad = [r["video_id"] for r in dataset if not 0 <= r[field] <= 1]
        if bad:
            problems.append(f"{field} 超出 [0, 1]: {bad[:5]}")
    other = Counter(r["language"].get("desc_language") for r in dataset)
    if set(other) != {"en"}:
        problems.append(f"存在非英语数据: {dict(other)}")
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
