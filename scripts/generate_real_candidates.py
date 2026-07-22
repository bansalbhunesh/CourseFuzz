from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path

from pydantic import BaseModel

from evaluations.real_corpus import load_manifest
from evaluations.real_scoring import Candidate, PublicTask, seal_candidates

ROOT = Path(__file__).resolve().parents[1]
REAL_DIR = ROOT / "evaluations" / "real"
MANIFEST = REAL_DIR / "selection_manifest.json"
PUBLIC_BUNDLE = REAL_DIR / "public.jsonl"
CANDIDATES = REAL_DIR / "candidates.jsonl"
RECEIPT = REAL_DIR / "receipt.json"

class HypothesisBatch(BaseModel):
    inputs: list[str]
    rationales: list[str]

def generate_random(task: PublicTask, budget: int, seed: int) -> list[Candidate]:
    rng = random.Random(f"{seed}-{task.task_id}")
    candidates = []
    
    bases = []
    if "input" in task.public_tests:
        bases.extend(task.public_tests["input"])
    
    if not bases:
        bases.append("1\n")
        
    for i in range(budget):
        base = rng.choice(bases)
        if rng.random() > 0.5:
            mutated = base + "\n"
        else:
            mutated = base + base
            
        mutated = mutated[:1_000_000]
        
        candidates.append(
            Candidate(
                task_id=task.task_id,
                generator="frozen-random",
                ordinal=i + 1,
                stdin=mutated,
                rationale="random mutation of public tests",
            )
        )
    return candidates

def generate_llm(task: PublicTask, budget: int) -> list[Candidate]:
    from openai import OpenAI
    client = OpenAI(timeout=30.0, max_retries=2)
    model = os.getenv("COURSEFUZZ_MODEL", "gpt-4o")
    
    task_context = {
        "title": task.upstream_name,
        "description": task.description,
        "public_tests": task.public_tests,
    }
    
    response = client.beta.chat.completions.parse(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are generating test inputs to find logic bugs in competitive programming submissions. "
                           f"Generate {budget} distinct inputs that probe edge cases. "
                           "Return exact strings for stdin.",
            },
            {
                "role": "user",
                "content": json.dumps(task_context, indent=2),
            },
        ],
        response_format=HypothesisBatch,
    )
    
    parsed = response.choices[0].message.parsed
    if not parsed or len(parsed.inputs) != budget:
        return generate_random(task, budget, 42)
        
    candidates = []
    for i in range(budget):
        candidates.append(
            Candidate(
                task_id=task.task_id,
                generator="gpt-one-shot",
                ordinal=i + 1,
                stdin=parsed.inputs[i][:1_000_000],
                rationale=parsed.rationales[i] if i < len(parsed.rationales) else "",
            )
        )
    return candidates

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate candidates for the real corpus")
    parser.add_argument("--budget", type=int, default=8)
    parser.add_argument("--generator", choices=["random", "llm", "both"], default="both")
    args = parser.parse_args()
    
    if not PUBLIC_BUNDLE.exists():
        print("Missing public.jsonl - run 'python scripts/prepare_real_evaluation.py bundle' first.")
        return 1
        
    manifest = load_manifest(MANIFEST)
    tasks: list[PublicTask] = []
    for line in PUBLIC_BUNDLE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            tasks.append(PublicTask.model_validate_json(line))
            
    all_candidates: list[Candidate] = []
    
    for task in tasks:
        print(f"Generating for {task.task_id}...")
        if args.generator in ("random", "both"):
            all_candidates.extend(generate_random(task, args.budget, 42))
        if args.generator in ("llm", "both"):
            if not os.getenv("OPENAI_API_KEY"):
                print("Skipping LLM generation: OPENAI_API_KEY not set")
            else:
                all_candidates.extend(generate_llm(task, args.budget))
                
    from evaluations.real_corpus import canonical_json
    with CANDIDATES.open("w", encoding="utf-8") as f:
        for c in all_candidates:
            f.write(canonical_json(c.model_dump(mode="json")) + "\n")
            
    print(f"Wrote {len(all_candidates)} candidates to {CANDIDATES}")
    
    receipt = seal_candidates(
        manifest=manifest,
        public_bundle_path=PUBLIC_BUNDLE,
        candidates_path=CANDIDATES,
        receipt_path=RECEIPT,
        budget_per_task=args.budget,
    )
    print(f"Sealed {receipt.candidate_count} candidates into {RECEIPT}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
