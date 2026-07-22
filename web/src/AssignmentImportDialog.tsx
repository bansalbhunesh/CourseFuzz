import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

type AssignmentImportDialogProps = {
  open: boolean;
  onClose: () => void;
  onImported: (assignmentId: string) => Promise<void>;
};

const exampleManifest = JSON.stringify(
  {
    title: "Absolute value",
    summary: "Return the non-negative magnitude of one bounded integer input.",
    entrypoint: "absolute_value",
    input_names: ["n"],
    domain_min: -3,
    domain_max: 3,
    reference: {
      title: "Instructor reference",
      source: "def absolute_value(n):\n    if n < 0:\n        return -n\n    return n\n",
    },
    accepted_solutions: [
      {
        title: "Accepted independent solution",
        source: "def absolute_value(n):\n    if n >= 0:\n        return n\n    return 0 - n\n",
      },
    ],
    misconception_programs: [
      {
        title: "Always negates",
        misconception: "Assumes absolute value always means negation.",
        source: "def absolute_value(n):\n    return -n\n",
      },
      {
        title: "Returns input unchanged",
        misconception: "Assumes the input is already a magnitude.",
        source: "def absolute_value(n):\n    return n\n",
      },
    ],
    instructor_tests: [
      { inputs: [-2], expected: 2, label: "negative" },
      { inputs: [0], expected: 0, label: "zero" },
    ],
    destination: {
      kind: "local_artifact",
      test_directory: "verified_tests",
    },
  },
  null,
  2,
);

export function AssignmentImportDialog({
  open,
  onClose,
  onImported,
}: AssignmentImportDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [manifest, setManifest] = useState(exampleManifest);
  const [prompt, setPrompt] = useState("");
  const [generating, setGenerating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      textareaRef.current?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload = JSON.parse(manifest) as unknown;
      const response = await fetch("/api/assignments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) {
        const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        throw new Error(detail || "The assignment manifest was rejected.");
      }
      await onImported(String(body.id));
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The assignment manifest is invalid.");
    } finally {
      setBusy(false);
    }
  }

  async function loadFile(file: File | undefined) {
    if (!file) return;
    setManifest(await file.text());
    setError(null);
  }

  async function generateWithAI() {
    if (!prompt.trim()) return;
    setGenerating(true);
    setError(null);
    try {
      const response = await fetch("/api/assignments/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt.trim() }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error("AI manifest generation failed");
      }
      setManifest(JSON.stringify(body, null, 2));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to generate AI manifest.");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <dialog
      className="import-dialog"
      ref={dialogRef}
      onClose={onClose}
      onCancel={onClose}
      aria-labelledby="import-title"
    >
      <form onSubmit={submit}>
        <header className="import-header">
          <div>
            <span className="section-number">NEW ASSIGNMENT</span>
            <h2 id="import-title">Lock an executable snapshot</h2>
          </div>
          <button className="icon-action" type="button" onClick={onClose} aria-label="Close import dialog">×</button>
        </header>
        <p className="import-guidance">
          Import a bounded Python function, two independently authored accepted controls,
          realistic misconception programs, instructor tests, and an exact write destination.
          CourseFuzz validates every control before preserving the snapshot.
        </p>
        <div className="manifest-toolbar">
          <span>JSON MANIFEST</span>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <input
              type="text"
              placeholder="e.g. Factorial or Fibonacci function..."
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              style={{ padding: "6px 10px", fontSize: "0.8rem", borderRadius: "4px", border: "1px solid var(--rule)" }}
              aria-label="AI Prompt"
            />
            <button
              type="button"
              className="text-action"
              onClick={() => void generateWithAI()}
              disabled={generating || !prompt.trim()}
              style={{ fontSize: "0.75rem", whiteSpace: "nowrap" }}
            >
              {generating ? "Generating..." : "⚡ Generate with AI"}
            </button>
            <label className="file-action">
              <span>Load file</span>
              <input
                type="file"
                accept="application/json,.json"
                onChange={(event) => void loadFile(event.target.files?.[0])}
              />
            </label>
          </div>
        </div>
        <textarea
          className="manifest-input"
          ref={textareaRef}
          value={manifest}
          onChange={(event) => setManifest(event.target.value)}
          spellCheck={false}
          aria-label="Assignment JSON manifest"
        />
        {error && <p className="import-error" role="alert">{error}</p>}
        <footer className="dialog-actions">
          <button className="text-action" type="button" onClick={onClose}>Cancel</button>
          <button className="primary-action" type="submit" disabled={busy}>
            {busy ? "Validating snapshot…" : "Validate and import"}<span aria-hidden="true">→</span>
          </button>
        </footer>
      </form>
    </dialog>
  );
}
