"""Replay labeled synthetic tickets over HTTP and report stored outcomes."""

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from triage.pipeline import DATA, load_articles
from triage.service import connect

GRADE_SCHEMA = """
CREATE TABLE IF NOT EXISTS replay_grades (
    batch TEXT NOT NULL,
    run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    expected_category TEXT NOT NULL,
    retrieval_hit INTEGER NOT NULL,
    category_correct INTEGER NOT NULL,
    citation_valid INTEGER NOT NULL,
    correct INTEGER NOT NULL
);
"""


def replay(base_url, database, split):
    client = build_opener(ProxyHandler({}))
    articles = {article["id"]: article["category"] for article in load_articles()}
    cases = json.loads((DATA / "replay.json").read_text(encoding="utf-8"))
    batch = uuid4().hex
    details = []
    grades = []
    for case in cases:
        if case["split"] != split:
            continue
        request = Request(base_url.rstrip("/") + "/triage", headers={
            "Content-Type": "application/json",
        }, data=json.dumps({"ticket": case["ticket"]}).encode())
        with client.open(request, timeout=20) as response:
            actual = json.load(response)
        expected = case["expected"]
        expected_category = articles.get(expected)
        draft = actual["decision"] == "draft"
        wrong_failure = actual["reason"] in {"model_unavailable", "invalid_model_response"}
        retrieval_hit = expected in actual["context_ids"]
        category_correct = expected is not None and actual["category"] == expected_category
        citation_valid = draft and actual["citation"] in actual["context_ids"]
        correct = (
            draft and actual["citation"] == expected and category_correct
            if expected is not None else not draft and not wrong_failure
        )
        grades.append((batch, actual["run_id"], case["id"], expected_category or "unknown",
                       retrieval_hit, category_correct, citation_valid, correct))
        details.append({
            "case": case["id"], "expected": expected, "citation": actual["citation"],
            "category": actual["category"], "context_ids": actual["context_ids"],
            "decision": actual["decision"], "reason": actual["reason"], "correct": correct,
        })
    with connect(database) as connection, connection:
        connection.executescript(GRADE_SCHEMA)
        matched = connection.executemany(
            """INSERT INTO replay_grades
               SELECT ?, run_id, ?, ?, ?, ?, ?, ? FROM runs WHERE run_id = ?""",
            [(row[0], *row[2:], row[1]) for row in grades],
        ).rowcount
        if matched != len(grades):
            raise ValueError("Use the same --db path as the running service")
    return {"split": split, "metrics": summarize(database, batch), "cases": details}


def summarize(database, batch=None):
    with connect(database) as connection:
        connection.executescript(GRADE_SCHEMA)
        query = """SELECT version, elapsed_ms, decision, reason, model_digest,
                          expected_category, retrieval_hit, category_correct, citation_valid, correct
                   FROM runs LEFT JOIN replay_grades USING (run_id)"""
        if batch is None:
            rows = connection.execute(query).fetchall()
        else:
            rows = connection.execute(query + " WHERE batch = ?", (batch,)).fetchall()

    grouped = defaultdict(list)
    for version, *values in rows:
        grouped[version].append(values)
    report = {}
    for version, runs in sorted(grouped.items()):
        latencies = sorted(run[0] for run in runs)
        reasons = Counter(run[2] for run in runs)
        categories = defaultdict(lambda: {"correct": 0, "cases": 0})
        graded = [run for run in runs if run[4] is not None]
        supported = [run for run in graded if run[4] != "unknown"]
        for run in graded:
            categories[run[4]]["cases"] += 1
            categories[run[4]]["correct"] += run[8]
        report[version] = {
            "requests": len(runs),
            "p95_ms": round(latencies[math.ceil(0.95 * len(latencies)) - 1], 2),
            "reviews": sum(run[1] == "review" for run in runs),
            "errors": reasons["model_unavailable"] + reasons["invalid_model_response"],
            "reasons": dict(reasons),
            "model_digests": sorted({run[3] for run in runs if run[3]}),
            "correct": sum(run[8] for run in graded),
            "graded": len(graded),
            "retrieval_hits": sum(run[5] for run in supported),
            "category_correct": sum(run[6] for run in supported),
            "valid_citations": sum(run[7] for run in graded),
            "graded_drafts": sum(run[1] == "draft" for run in graded),
            "supported_cases": len(supported),
            "by_expected_category": dict(categories),
        }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:18680")
    parser.add_argument("--split", choices=("diagnosis", "holdout"))
    args = parser.parse_args()
    result = replay(args.url, args.db, args.split) if args.split else summarize(args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
