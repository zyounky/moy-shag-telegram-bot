"""Reproducible local retrieval comparison; optional bounded paid LLM pairs.

The baseline must be a trusted local copy of knowledge.py. Environment settings
and the existing tester/expiry/request limits remain in force. Reports contain
source text: write them only to ignored data/, never commit the catalog/report.
"""

import argparse
import importlib.util
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assistant_bot.knowledge import Catalog
from assistant_bot.llm import generator_from_env


def load_baseline(path):
    spec = importlib.util.spec_from_file_location("context_baseline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Catalog


def summarize(records):
    summary = {}
    for variant in ("before", "after"):
        rows = [r for r in records if r["variant"] == variant]
        if not rows:
            continue
        summary[variant] = {
            "questions": len(rows),
            "expected_top1": sum(r["top1"] for r in rows if r["outcome"] == "evidence"),
            "expected_top3": sum(r["top3"] for r in rows if r["outcome"] == "evidence"),
            "evidence_questions": sum(r["outcome"] == "evidence" for r in rows),
            "missing_or_clarification_correct": sum(r["outcome_correct"] for r in rows if r["outcome"] != "evidence"),
            "median_context_chars": statistics.median(r["context_chars"] for r in rows),
        }
        live = [r for r in rows if "generation" in r]
        if live:
            times = sorted(r["generation"]["wall_seconds"] for r in live)
            first = [r["generation"]["metrics"]["first_answer_seconds"] for r in live
                     if "first_answer_seconds" in r["generation"]["metrics"]]
            summary[variant].update(
                live_questions=len(live),
                validated_answers=sum(r["generation"]["status"] == "answered" for r in live),
                median_seconds=round(statistics.median(times), 3),
                p90_seconds=times[max(0, math.ceil(.9 * len(times)) - 1)],
                median_first_text_seconds=round(statistics.median(first), 3) if first else None,
                median_input_tokens=statistics.median(r["generation"]["metrics"].get("input_tokens") or 0 for r in live),
            )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.jsonl")
    parser.add_argument("--baseline-module", type=Path, required=True)
    parser.add_argument("--questions", type=Path, default=ROOT / "evaluation/questions.json")
    parser.add_argument("--report", type=Path, default=ROOT / "data/context-evaluation.json")
    parser.add_argument("--live", action="store_true", help="paid API calls, at most 30 total")
    parser.add_argument("--resume", action="store_true", help="reuse unchanged completed pairs from the report")
    parser.add_argument("--spent-requests", type=int, default=0, help="include earlier/interrupted calls in the same 30-call budget")
    parser.add_argument("--test-chat-file", type=Path, default=ROOT / "runtime/tester_chat_id")
    args = parser.parse_args()
    previous = json.loads(args.report.read_text(encoding="utf-8")) if args.resume else {}
    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    live_count = 2 * sum(q.get("live", False) for q in questions)
    generator = generator_from_env(os.environ) if args.live else None
    if args.live and (not generator or live_count > min(30, generator.remaining_requests)):
        parser.error("Live evaluation requires a configured generator and sufficient budget (maximum 30).")
    tester = int(args.test_chat_file.read_text()) if args.live else None
    spent = max(args.spent_requests, previous.get("api_attempts", 0),
                sum("generation" in r for r in previous.get("records", [])))
    if generator:
        generator.remaining_requests = max(0, min(30, generator.remaining_requests) - spent)
    catalogs = {"before": load_baseline(args.baseline_module)(args.catalog), "after": Catalog(args.catalog)}
    records = previous.get("records", [])
    report = {"summary": {}, "records": records,
              "api_attempts": spent, "superseded": previous.get("superseded", []),
              "method": "Paired sequential requests; alternating order; same LLM/prompt/effort; no cache; no Telegram delivery. Title matches are retrieval proxies, not factual quality scores."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    for index, case in enumerate(questions):
        # Alternate to reduce systematic advantage from server load/cache warmup.
        order = ("before", "after") if index % 2 == 0 else ("after", "before")
        for variant in order:
            started = perf_counter()
            result = catalogs[variant].search(case["question"], case.get("context"))
            search_ms = round((perf_counter() - started) * 1000, 3)
            titles = [e.title.replace("\xa0", " ") for e in result.evidence]
            hits = [bool(re.search(case["expected"], t, re.I)) for t in titles] if case["expected"] else []
            record = {**case, "variant": variant, "search_ms": search_ms, "titles": titles,
                      "top1": bool(hits and hits[0]), "top3": any(hits),
                      "outcome_correct": (result.needs_clarification if case["outcome"] == "clarification"
                                          else not result.evidence if case["outcome"] == "missing" else any(hits)),
                      "context_chars": sum(len(e.passage) for e in result.evidence),
                      "evidence": [{"title": e.title, "passage": e.passage, "scope": e.content_scope} for e in result.evidence]}
            if args.live and case.get("live"):
                old = next((r for r in records if r["id"] == case["id"] and r["variant"] == variant), None)
                if old and old.get("generation") and old["evidence"] == record["evidence"]:
                    continue
                if generator.remaining_requests <= 0:
                    raise SystemExit("The 30-call budget is exhausted; no additional API call was sent.")
                if old and old.get("generation"):
                    report["superseded"].append(old)
                generator._cache.clear()  # Measure real generations, not repeated local cache hits.
                report["api_attempts"] += 1
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                started = perf_counter()
                generated = generator.generate(case["question"], case.get("profile", {}), result.evidence, chat_id=tester)
                record["generation"] = {"status": generator.last_status,
                                        "metrics": dict(generator.last_metrics),
                                        "wall_seconds": round(perf_counter() - started, 3)}
                if generated:
                    advice = generated[1]
                    record["generation"].update(answer=advice.answer, action=advice.action,
                                                completion=advice.completion, sources=advice.sources,
                                                quote=advice.passage)
                print(json.dumps({"id": case["id"], "variant": variant, **record["generation"]["metrics"],
                                  "status": generator.last_status}, ensure_ascii=False), flush=True)
            records[:] = [r for r in records if (r["id"], r["variant"]) != (case["id"], variant)]
            records.append(record)
            report["summary"] = summarize(records)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
