#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.request

from source_guard import validate_proposal

HERE = Path(__file__).resolve().parent
URL = "https://opencode.ai/inference/openai/v1/chat/completions"
MODELS = ["nemotron-3-ultra-free", "big-pickle", "laguna-s-2.1-free"]
TARGETS = {
    "epistemic.py": HERE / "epistemic.py",
    "test_epistemic.py": HERE / "test_epistemic.py",
}
STATE_PATH = HERE / "AUTOWORK_STATE.json"
OBJECTIVE_PATH = HERE / "AUTOWORK_OBJECTIVE.md"
MAX_EVENTS = 40


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_verifier() -> tuple[int, str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "-v",
            "test_epistemic_frozen.py",
            "test_epistemic.py",
            "test_source_guard_frozen.py",
        ],
        cwd=HERE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return proc.returncode, proc.stdout[-16000:]


def load_state() -> dict:
    try:
        state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    except Exception:
        state = {}
    state.setdefault("schema_version", 2)
    state.setdefault("objective", OBJECTIVE_PATH.read_text().strip())
    state.setdefault("events", [])
    state.setdefault("accepted_edits", 0)
    state.setdefault("rejected_edits", 0)
    state.setdefault("inconclusive_ticks", 0)
    return state


def save_state(state: dict) -> None:
    state["schema_version"] = 2
    state["updated_at"] = now()
    state["events"] = state.get("events", [])[-MAX_EVENTS:]
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def event(state: dict, decision: str, **fields) -> None:
    state["events"].append({"time": now(), "decision": decision, **fields})
    if decision == "ACCEPT":
        state["accepted_edits"] += 1
    elif decision == "REJECT":
        state["rejected_edits"] += 1
    elif decision == "INCONCLUSIVE":
        state["inconclusive_ticks"] += 1
    save_state(state)


def extract_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"no JSON object found in content={text[:1600]!r}")


def call_one(model: str, prompt: str) -> tuple[dict, str]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return a single JSON object only. First character { and last character }. "
                    "No markdown, preface, or commentary. You are proposing at most one bounded code change; "
                    "deterministic external tests, not you, decide whether it is accepted."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 6500,
        "temperature": 0,
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "validated-agent-autowork/0.3"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=75) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise ValueError(f"HTTP {exc.code}: {body[:1200]}") from exc
    envelope = json.loads(raw)
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"unexpected envelope={raw[:1600]}")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    reasoning = message.get("reasoning") or ""
    if not content.strip():
        raise ValueError(f"empty content; reasoning={reasoning[:1200]!r}")
    return extract_object(content), reasoning[:1600]


def call_model(prompt: str) -> tuple[str, dict, list[str]]:
    failures: list[str] = []
    for model in MODELS:
        try:
            proposal, _reasoning = call_one(model, prompt)
            return model, proposal, failures
        except Exception as exc:
            failures.append(f"{model}: {type(exc).__name__}: {exc}")
    raise ValueError(" | ".join(failures))


def build_prompt(state: dict, verifier_output: str) -> str:
    recent = state.get("events", [])[-6:]
    return f'''OBJECTIVE:
{OBJECTIVE_PATH.read_text().strip()}

VERIFIED CURRENT STATE:
The frozen+editable verifier suite currently passes.
Latest verifier output:
{verifier_output[-7000:]}

CURRENT epistemic.py:
{TARGETS["epistemic.py"].read_text()}

CURRENT test_epistemic.py:
{TARGETS["test_epistemic.py"].read_text()}

RECENT DURABLE EVENTS:
{json.dumps(recent, indent=2)[:5000]}

Choose ONE safe useful next step. Prefer an adversarial test that exposes a real missing invariant; otherwise improve epistemic.py. If no justified improvement exists, choose no_change.

Your exact JSON schema is:
{{"action":"write_file|no_change","path":"epistemic.py|test_epistemic.py|null","content":"complete replacement file or empty string","hypothesis":"short falsifiable claim","expected_observation":"what verifier should establish","note":"short rationale"}}

Rules:
- one file only;
- test_epistemic.py changes MUST be append-only: reproduce the entire existing file byte-for-byte, then append one or more new unittest test methods/classes;
- never delete, rename, rewrite, or weaken an existing test;
- epistemic.py may only use its existing safe import families (dataclasses, enum, typing);
- test_epistemic.py may only import unittest and epistemic;
- no filesystem, network, shell, subprocess, dynamic import, eval, exec, credential, or process-control capability;
- do not edit the harness, frozen tests, objective, state, workflow, or source_guard;
- passing your own proposed test is not authority; the external verifier decides.
'''


def main() -> int:
    state = load_state()
    run_id = os.environ.get("GITHUB_RUN_ID", "local")

    baseline_rc, baseline_out = run_verifier()
    if baseline_rc != 0:
        event(
            state,
            "INCONCLUSIVE",
            run_id=run_id,
            reason="baseline verifier failed before model proposal",
            verifier_exit=baseline_rc,
            verifier_tail=baseline_out[-5000:],
        )
        print("AUTOWORK_V2_INCONCLUSIVE baseline verifier failed")
        return 0

    try:
        model, proposal, prior_failures = call_model(build_prompt(state, baseline_out))
    except Exception as exc:
        event(
            state,
            "INCONCLUSIVE",
            run_id=run_id,
            reason=f"all model/protocol routes failed: {type(exc).__name__}: {exc}",
        )
        print(f"AUTOWORK_V2_INCONCLUSIVE {exc}")
        return 0

    action = proposal.get("action")
    hypothesis = str(proposal.get("hypothesis", ""))[:1200]
    expected = str(proposal.get("expected_observation", ""))[:1200]
    note = str(proposal.get("note", ""))[:1200]

    if action == "no_change":
        event(
            state,
            "NO_CHANGE",
            run_id=run_id,
            model=model,
            hypothesis=hypothesis,
            expected_observation=expected,
            note=note,
            prior_model_failures=prior_failures,
        )
        print(f"AUTOWORK_V2_NO_CHANGE model={model}")
        return 0

    path = proposal.get("path")
    replacement = proposal.get("content")
    if action != "write_file" or path not in TARGETS or not isinstance(replacement, str) or not replacement.strip():
        event(
            state,
            "REJECT",
            run_id=run_id,
            model=model,
            reason="invalid action/path/content",
            action=action,
            path=path,
            prior_model_failures=prior_failures,
        )
        print("AUTOWORK_V2_REJECT invalid proposal")
        return 0

    target = TARGETS[path]
    original = target.read_text()
    if replacement == original:
        event(state, "NO_CHANGE", run_id=run_id, model=model, path=path, reason="identical replacement")
        print("AUTOWORK_V2_NO_CHANGE identical")
        return 0

    guard_ok, guard_reason = validate_proposal(path, replacement, original)
    if not guard_ok:
        event(
            state,
            "REJECT",
            run_id=run_id,
            model=model,
            path=path,
            reason=f"source guard: {guard_reason}",
            hypothesis=hypothesis,
            expected_observation=expected,
            note=note,
            prior_model_failures=prior_failures,
        )
        print(f"AUTOWORK_V2_REJECT source_guard={guard_reason}")
        return 0

    proposal_hash = hashlib.sha256(replacement.encode()).hexdigest()
    target.write_text(replacement)
    try:
        result_rc, result_out = run_verifier()
    except Exception as exc:
        result_rc, result_out = 99, f"verifier exception: {type(exc).__name__}: {exc}"

    if result_rc != 0:
        target.write_text(original)
        restore_rc, restore_out = run_verifier()
        event(
            state,
            "REJECT",
            run_id=run_id,
            model=model,
            path=path,
            proposal_sha256=proposal_hash,
            hypothesis=hypothesis,
            expected_observation=expected,
            note=note,
            verifier_exit=result_rc,
            verifier_tail=result_out[-6000:],
            rollback_verified=restore_rc == 0,
            rollback_tail=restore_out[-2000:],
            prior_model_failures=prior_failures,
        )
        print(f"AUTOWORK_V2_REJECT verifier_failed rollback_verified={restore_rc == 0}")
        return 0

    event(
        state,
        "ACCEPT",
        run_id=run_id,
        model=model,
        path=path,
        proposal_sha256=proposal_hash,
        hypothesis=hypothesis,
        expected_observation=expected,
        note=note,
        verifier_exit=result_rc,
        verifier_tail=result_out[-5000:],
        prior_model_failures=prior_failures,
    )
    print(f"AUTOWORK_V2_ACCEPT model={model} path={path} sha256={proposal_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
