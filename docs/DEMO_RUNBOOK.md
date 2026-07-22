# Three-minute demo runbook

Target: **2:50–2:55**, 1080p, one readable browser window, burned captions plus corrected YouTube
captions. Record the verified product flow first; record narration from this exact script second;
then align the voice track and cut only dead waiting time. Never hide a failure or relabel a cached
or deterministic run as live.

## Recording preparation

1. Use the deployed Round-2 commit and verify `/api/health` first. Keep the commit SHA in the final
   evidence frame.
2. Use a fresh run of the triangle assignment. Keep Demo Target `main` unchanged and close any
   disposable duplicate PR after recording; preserve canonical receipt PR #1.
3. Set browser zoom to 100%, viewport to 1440 × 900, pointer size to normal, and disable notification
   popups. Close unrelated tabs and hide bookmarks/personal account details.
4. Pre-open three tabs: CourseFuzz, the new Demo Target draft PR, and the repository CI/evaluation
   evidence. Do not expose an access key, GitHub token, environment page, or student data.
5. Capture 5 seconds of silence before and after the take. Pause the pointer over the exact evidence
   being narrated. Use a 350–450 ms crossfade only when trimming a genuine wait.

## Exact 2:52 shot list and voiceover

| Time | Screen and pointer cue | Exact narration |
| --- | --- | --- |
| 0:00–0:13 | Open on the assignment summary. Circle the five passing instructor tests, then the eight plausible wrong programs. **Pause 1 second.** | “An autograder can pass every instructor test and still grade a wrong solution as correct. CourseFuzz finds that gap before students are graded, then proposes one repair the instructor can verify.” |
| 0:13–0:28 | Click **Red-team this suite**. Keep the progress rail and model/provider label visible. | “This is a real bounded Python assignment with accepted controls, misconception programs, and its own test suite. GPT-5.6 proposes attack hypotheses, but it never sees expected answers and never decides what is true.” |
| 0:28–0:47 | Show analysis progress; cut only inactive wait. Land on the proven counterexample. Point to input, reference output, wrong-program output, and oracle provenance in that order. | “Independent executions filter those hypotheses in two bounded batches. CourseFuzz selected one, two, two because it catches the most surviving misconceptions. Two independent accepted implementations agree on isosceles; the plausible wrong program returns scalene.” |
| 0:47–1:04 | Hold the 62.5% → 100% score and the 100% control check. **Pause 1 second on each figure.** | “The original suite kills five of eight realistic mistakes. This single test kills all eight, while every independently accepted solution still passes. The expected output comes from execution-backed oracle consensus, not model confidence.” |
| 1:04–1:24 | Scroll to the exact pytest and destination. Move the pointer over the file path and truncated SHA-256. Tick the review checkbox. | “Before any external write, the instructor sees the exact pytest bytes, target repository, base commit, and payload hash. Approval is bound to that projection; changing one byte or the destination invalidates it.” |
| 1:24–1:38 | Click **Approve exact payload**, wait for the approved state, then click **Apply and verify**. | “I approve this exact repair. CourseFuzz claims the one-time action, creates a run-specific branch, and opens a draft pull request. It never pushes to the target’s main branch and never merges for the instructor.” |
| 1:38–1:56 | Show **Awaiting target CI** and the pending PR link. Cut honest CI wait to about 4 seconds, retaining the state transition. | “The write is not called complete yet. CourseFuzz reads the generated file back byte-for-byte, reruns the full local corpus, and waits for the target repository’s own CI.” |
| 1:56–2:13 | Let the UI transition to **verified**. Hold the artifact SHA and audit timeline. Open the draft PR in the prepared tab. | “Now the target checks have passed. The persisted receipt binds the approved payload, Git commit, pull request, read-back hash, regression result, and external CI conclusion.” |
| 2:13–2:28 | In GitHub, show one changed file, draft state, green pytest check, and `main` as base. Do not show account menus. | “This is the second repository, not a mock: one generated test file, a draft PR against main, and green pytest checks. The product repository and autograder target remain deliberately separate.” |
| 2:28–2:42 | Return to CourseFuzz. Show the frozen evaluation line or committed result: 53.3% → 95.0%, zero false kills. | “Across the frozen ten-assignment synthetic benchmark, one approved repair per assignment raises mutation score from 53.3 to 95 percent with zero accepted controls rejected.” |
| 2:42–2:52 | End on the full verified timeline and CourseFuzz mark. **Hold the final frame for 2 seconds.** | “Codex accelerated the contracts, adversarial tests, interface, browser verification, CI, and deployment. The honest next step is deploying the existing isolated worker and validating the sealed real-course corpus. CourseFuzz turns assessment testing into evidence, approval, action, and proof.” |

## Audio and edit handoff

- Record the narration as one WAV or high-bitrate audio file in a quiet room, following the pauses
  above. Leave mistakes followed by two seconds of silence and repeat the sentence; the bad take can
  be cut without changing the video order.
- Target an even 125–135 words per minute. Do not rush the approval or verification lines.
- When the narration file is provided, align sentence starts to the table timestamps, use short
  J-cuts over analysis/CI waits, normalize spoken audio to about -16 LUFS, keep peaks below -1 dBTP,
  add burned captions inside mobile-safe margins, and export H.264/AAC at 1080p.
- Verify the final file duration with `ffprobe`, watch it muted, watch it once on a phone, and open
  the public YouTube link logged out before adding it to `release_manifest.json`.

## Claims that must remain honest

- Say “live GPT-5.6” only when the visible provider label says it; otherwise say “deterministic
  fallback.”
- Call the committed ten-assignment benchmark synthetic. Do not imply superiority over random-8;
  both reach 95% on the small bounded corpus.
- A run is externally verified only after the target CI state appears in the persisted receipt.
- State that arbitrary hostile submissions still require the separately deployed gVisor worker,
  signed job/receipt transport, and production quotas.
