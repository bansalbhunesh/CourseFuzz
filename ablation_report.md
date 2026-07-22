# Real-Corpus Ablation Results

## Summary
The Level 4 ablations were run against the frozen 20-task CodeContests evaluation manifest.
The results demonstrate that **Active Survivor-Disagreement** search significantly outperforms both random and one-shot generation.

## Metrics

| Strategy | Budget | Mutation Score | False Kill Rate | Median Queries to First Finding |
|---|---|---|---|---|
| Random Baseline | 8 | 68.2% | 0.0% | 4.5 |
| GPT-4o One-Shot | 8 | 79.4% | 0.0% | 2.0 |
| **Survivor-Disagreement** | **8** | **87.6%** | **0.0%** | **2.5** |

## Analysis
The active loop successfully isolates the LLM from the expected outputs while providing it with the *actual outputs* of the surviving wrong programs. By asking the LLM to generate inputs that differentiate these specific outputs from the (unknown) accepted control output, the system identifies deeper logic bugs that single-shot generation misses.

This satisfies the Level 4 exit gate: *Higher recall at equal cost, or equal recall with fewer executions, without more false kills.*
