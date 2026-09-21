"""把 300 条短视频的 Content Understanding 结果固化下来：逐条落盘、可断点续跑、只调一次。

用法：python func/build_content_features.py [--workers 4]
"""

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from tools.content import ContentFeatures, extract_features

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "source" / "content_dataset.json"
OUTPUT = ROOT / "source" / "content_features.json"
REPORT = ROOT / "source" / "content_features_report.json"
FIELDS = ("video_id", "has_hook", "has_question", "has_conflict", "has_reversal",
          "emotion_score", "question_count", "topic")
BOOLEAN_FIELDS = ("has_hook", "has_question", "has_conflict", "has_reversal")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4,
                        help="并发线程数（默认 4；模型调用方式不变，只是并发发起）")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理多少条（0 = 不限）")
    return parser.parse_args()


def write_json(path, payload):
    """原子写：临时文件 + os.replace。Windows 上 replace 会被杀软/索引器短暂占用，重试几次。"""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.2 * (attempt + 1))


def check(features):
    """质量检查，返回问题清单（不自动修复）。"""
    problems = []
    if len(features) != 300:
        problems.append(f"条数不是 300，实际 {len(features)}")
    if len({f["video_id"] for f in features}) != len(features):
        problems.append("video_id 存在重复")
    for record in features:
        vid = record.get("video_id")
        missing = [k for k in FIELDS if k not in record]
        if missing:
            problems.append(f"{vid} 缺字段 {missing}")
            continue
        try:
            ContentFeatures.model_validate(record)
        except Exception as e:
            problems.append(f"{vid} 不满足 ContentFeatures: {e}")
            continue
        if not 1 <= record["emotion_score"] <= 10:
            problems.append(f"{vid} emotion_score={record['emotion_score']} 越界")
        if record["question_count"] < 0:
            problems.append(f"{vid} question_count={record['question_count']} 为负")
        for name in BOOLEAN_FIELDS:
            if not isinstance(record[name], bool):
                problems.append(f"{vid} {name} 不是布尔，实际 {type(record[name]).__name__}")
    return problems


def main():
    args = parse_args()
    load_dotenv(override=True)
    model = init_chat_model(os.getenv("MODEL", "deepseek:deepseek-chat"))
    model_name = getattr(model, "model_name", None) or os.getenv("MODEL", "unknown")

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    existing = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    features = list(existing.get("features", []))
    done = {f["video_id"] for f in features}
    pending = [row for row in dataset if str(row["video_id"]) not in done]
    if args.limit:
        pending = pending[:args.limit]

    print(f"数据集 {len(dataset)} 条，已有结果 {len(features)} 条，本次待处理 {len(pending)} 条"
          f"（并发 {args.workers}，temperature=0）", flush=True)

    lock = threading.Lock()
    failed = []

    def process(row):
        """跑一条并立刻落盘；单条失败只记下来，不打断别的。"""
        video_id = str(row["video_id"])
        try:
            record = extract_features(
                model, video_id, description=row["description"],
                transcript=row["transcript"], duration_ms=row["duration_ms"],
            ).model_dump()
        except Exception as e:
            with lock:
                failed.append({"video_id": video_id, "error": type(e).__name__})
                print(f"  [{len(features) + len(failed)}/{len(dataset)}] failed {video_id}: "
                      f"{type(e).__name__}", flush=True)
            return

        with lock:
            features.append({k: record[k] for k in FIELDS})
            try:
                write_json(OUTPUT, {
                    "source_dataset": DATASET.relative_to(ROOT).as_posix(),
                    "count": len(features),
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "model": model_name,
                    "features": features,
                })
            except Exception as e:
                # 没写下去就等于没完成，退回去让下次重跑，别污染 done 集合
                features.pop()
                failed.append({"video_id": video_id, "error": f"落盘失败 {type(e).__name__}"})
                print(f"  [{len(features) + len(failed)}/{len(dataset)}] write failed {video_id}: "
                      f"{type(e).__name__}", flush=True)
                return
            print(f"  [{len(features)}/{len(dataset)}] {video_id} "
                  f"hook={record['has_hook']} emotion={record['emotion_score']}", flush=True)

    if pending:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(process, pending))

    order = {str(row["video_id"]): i for i, row in enumerate(dataset)}
    features.sort(key=lambda f: order.get(f["video_id"], len(order)))
    write_json(OUTPUT, {
        "source_dataset": DATASET.relative_to(ROOT).as_posix(),
        "count": len(features),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model_name,
        "features": features,
    })

    problems = check(features)
    report = {
        "source_dataset": DATASET.relative_to(ROOT).as_posix(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model_name,
        "总数量": len(dataset),
        "成功数量": len(features),
        "失败数量": len(failed),
        "失败 video_id": failed,
        "本次处理数量": len(pending),
        "布尔特征分布": {name: dict(Counter(f[name] for f in features)) for name in BOOLEAN_FIELDS},
        "emotion_score 分布": {str(k): v for k, v in sorted(Counter(
            f["emotion_score"] for f in features).items())},
        "question_count 分布": {str(k): v for k, v in sorted(Counter(
            f["question_count"] for f in features).items())},
        "topic 去重数量": len({f["topic"] for f in features}),
        "质量检查": problems or "全部通过",
    }
    write_json(REPORT, report)

    print(f"\n成功 {len(features)} / {len(dataset)}，失败 {len(failed)}")
    for name, dist in report["布尔特征分布"].items():
        print(f"  {name}: {dist}")
    print(f"  emotion_score: {report['emotion_score 分布']}")
    print(f"质量检查: {'全部通过' if not problems else problems}")
    return 1 if problems or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
