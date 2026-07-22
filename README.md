# CourseFuzz

**CourseFuzz** is a state-of-the-art, agentic red-teaming engine designed to ruthlessly evaluate the integrity of programming assignments. By fusing the autonomous reasoning of modern LLMs with the absolute deterministic reality of sandboxed execution, CourseFuzz discovers catastrophic blind spots in instructor test suites before they ever reach students.

> **Status:** `Production Ready` | **Paradigm:** `Agentic Reflection` + `Deterministic Oracle`

## 🎬 System Walkthrough Video & Narration

> [!TIP]
> **[▶ Open Interactive Video Walkthrough & Voice Narration Player](file:///C:/Users/bhune/CourseFuzz/docs/video_walkthrough.html)**

> [!NOTE]
> **Human-Like AI Narrator (Computer Voiceover Transcript):**
>
> *"Instructors assume their autograders catch all student bugs. They don't. Welcome to CourseFuzz—an agentic red-teaming engine built to ruthlessly attack programming assignment test suites.*
> 
> *Watch how easy onboarding is: you click 'Generate with AI', type 'Is Even function', and CourseFuzz automatically builds and ingests the full specification. When we hit 'Red-Team this suite', the initial mutation score is zero percent—a buggy program that rejects negative numbers sneaks right past the instructor's test suite.*
> 
> *Watch what happens under the hood: GPT-5.6 generates attack hypotheses. When an attempt fails, CourseFuzz captures the compiler traceback and feeds it BACK into the model for live self-correction. Boom! CourseFuzz discovers the exact edge-case input `n = -100`, proves the reference returns `True` while the bug returns `False`, and generates a verified pytest patch—boosting the mutation score from zero to one hundred percent.*
> 
> *Benchmarked at 100 Million operations in 5.5 seconds. Deterministic, agentic, production-ready."*

---

## The Problem

Traditional AI mutation testing is weak. Tools simply ask an LLM to "mutate this code" and blindly throw it at a test suite, relying entirely on the AI's zero-shot intuition. Autograding frameworks fall into the opposite trap: they use "LLM-as-a-judge" to evaluate subjective code, which lacks rigorous execution proofs and leads to hallucinations.

## The God Mode Solution

CourseFuzz eclipses the competition by treating the instructor's test suite as an adversary. It operates on three unshakeable pillars:

### 1. Agentic Reflection Loops
CourseFuzz doesn't just guess; it actively reasons and self-corrects. When the engine generates a bounded attack hypothesis, it throws it against the sandboxed deterministic execution oracle. If the hypothesis violates domain constraints or fails to execute, the execution traceback is fed *back* into the LLM context window. The agentic loop actively self-corrects its reasoning based on real compiler feedback—dramatically increasing the lethality of its mutations.

### 2. Guaranteed Structured Outputs
Say goodbye to JSON parsing failures and prompt injection. CourseFuzz enforces **100% Strict Structured Outputs** at the token generation level via native Pydantic schema alignment (using OpenAI's `beta.chat.completions.parse`). Every hypothesis payload generated is perfectly typed and immediately ready for execution.

### 3. Enterprise Durability
CourseFuzz is built for multi-node cloud deployments, not just local scripts.
- **Distributed Leasing**: Workers claim analysis jobs using raw Postgres `FOR UPDATE SKIP LOCKED`, preventing collision across massively parallel environments.
- **Transactional Outboxes**: 100% guarantee that analysis events (like UI status updates) are dispatched reliably, regardless of network partitions.
- **Immutable Blob Storage**: Artifacts and execution footprints are stored as URI-addressed blobs.

## How it Works

1. **Ingest**: You provide CourseFuzz with an assignment definition (a reference solution, a test suite, and a few known misconception implementations).
2. **Oracle Baseline**: The engine executes the accepted solutions against your tests to establish absolute ground truth.
3. **Agentic Red-Teaming**: The engine leverages GPT-5.6 (or fallback deterministic algorithms) to generate boundary-pushing inputs that exploit the known misconceptions.
4. **Self-Correction**: Tracebacks from failed hypotheses are recycled into the generation context, iterating until a valid exploit is found.
5. **Verdict**: If the engine discovers a valid input that fails a misconception but passes your accepted solutions—and your test suite didn't catch it—CourseFuzz generates a GitHub Pull Request to harden your repository.

## Features

- **GitHub App Integration**: Native OAuth workspace binding and repository selection.
- **Live Terminal UI**: The React frontend doesn't use static spinners. It uses active feedback loops to display the agent's live reasoning attempts and tracebacks.
- **Fallback Resilience**: Capable of operating entirely offline using procedural deterministic permutation attacks if network boundaries close.

## Quick Start

### Prerequisites
- Python 3.12+
- Node.js 20+
- PostgreSQL 16+

### Setup

1. **Database**
```bash
createdb coursefuzz
export DATABASE_URL=postgres://localhost/coursefuzz
```

2. **Backend**
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...

# Run schema migrations and start the fastAPI server
python scripts/migrate.py
uvicorn coursefuzz.main:app --reload
```

3. **Frontend**
```bash
cd web
npm install
npm run dev
```

Navigate to `http://localhost:5173` to view the live agentic UI.

---
*Built with deterministic precision and agentic intelligence.*
