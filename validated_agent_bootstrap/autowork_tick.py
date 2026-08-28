#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
URL = "https://opencode.ai/inference/openai/v1/chat/completions"
MODELS = ["nemotron-3-ultra-free", "big-pickle", "laguna-s-2.1-free"]
ALLOWED_FILES = {
    "epistemic.py": HERE / "epistemic.py",
    "test_epistemic.py": HERE / "test_epistemic.py",
}
STATE_PATH = HERE / "AUTOWORK_STATE.json"
OBJECTIVE_PATH = HERE / "AUTOWORK_OBJECTIVE.md"
MAX_EVENTS = 40
BANNED_SOURCE_TOKENS = (
    "import subprocess",
    "from subprocess",
    "import socket",
    "from socket",
    "import requests",
    "from requests",
    "import urllib",
    "from urllib",
    "os.system(",
    "os.popen(",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_tests() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "-v", "test_epistemic.py"],
        cwd=HERE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        env={**dict(__import__("os").environ), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return proc.returncode, proc.stdout[-12000:]


def test_names(text: str) -> set[str]:
    return set(re.findall(r"^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(", text, flags=re.MULTILINE))


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {
        "schema_version": 1,
        "objective": OBJECTIVE_PATH.read_text().strip(),
        "events": [],
        "accepted_edits": 0,
        "rejected_edits": 0,
        "inconclusive_ticks": 0,
    }


def save_state(state: dict) -> None:
    state["updated_at"] = utcnow()
    state["events"] = state.get("events", [])[-MAX_EVENTS:]
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError("top-level JSON must be object")
        return obj
    except Exception:
        decoder = json.JSONDecoder()
        for i, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(text[i:])
            except Exception:
                continue
            if isinstance(obj, dict):
                return obj
        raise ValueError("no JSON object found")


def call_one_model(model: str, prompt: str) -> tuple[str, str]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a bounded coding worker. Return exactly one JSON object and no markdown. "
                    "You may propose one full-file replacement only. Never claim tests ran unless the supplied observation says so."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 5000,
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "validated-agent-autowork/0.2"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=75) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise ValueError(f"HTTP {exc.code}: {body[:1000]}") from exc

    obj = json.loads(raw)
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"unexpected response envelope: {raw[:1200]}")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    reasoning = msg.get("reasoning") or ""
    if not content:
        raise ValueError(f"model returned no content; reasoning={reasoning[:1200]}; raw={raw[:1600]}")
    return content, reasoning


def call_model(prompt: str) -> tuple[str, str, str]:
    failures: list[str] = []
    for model in MODELS:
        try:
            content, reasoning = call_one_model(model, prompt)
            return model, content, reasoning
        except Exception as exc:
            failures.append(f"{model}: {type(exc).__name__}: {exc}")
    raise ValueError("all model routes failed | " + " | ".join(failures))


def build_prompt(state: dict, test_rc: int, test_output: str) -> str:
    files = {name: path.read_text() for name, path in ALLOWED_FILES.items()}
    recent = state.get("events", [])[-8:]
    return f"""OBJECTIVE
{OBJECTIVE_PATH.read_text().strip()}

CURRENT VERIFIED TEST OBSERVATION
exit_code={test_rc}
{test_output}

CURRENT FILES
--- epistemic.py ---
{files['epistemic.py']}
--- test_epistemic.py ---
{files['test_epistemic.py']}

RECENT DURABLE EVENTS
{json.dumps(recent, indent=2)[:6000]}

Choose exactly one action:
1. Improve the implementation or adversarial tests in a way that advances the objective.
2. If no safe useful change is justified, choose no_change.

Return exactly one JSON object:
{{
  "action": "write_file" | "no_change",
  "path": "epistemic.py" | "test_epistemic.py" | null,
  "content": "COMPLETE replacement file content when action=write_file, else empty string",
  "hypothesis": "brief causal/design hypothesis being tested",
  "expected_observation": "what the deterministic tests should establish",
  "note": "brief explanation"
}}

Constraints:
- one file per tick;
- preserve every existing test; adding stronger tests is preferred;
- do not weaken/delete tests merely to obtain green;
- do not add network, credential, GitHub API, subprocess, shell, or process-control code;
- do not alter files outside the two allowed files;
- do not treat model output as verifier authority.
"""


def reject_event(state: dict, **fields) -> None:
    state["events"].append({"time": utcnow(), "decision": "REJECT", **fields})
    state["rejected_edits"] += 1
    save_state(state)


def main() -> int:
    state = load_state()
    before_rc, before_output = run_tests()
    if before_rc != 0:
        state["events"].append({
            "time": utcnow(),
            "decision": "INCONCLUSIVE",
            "reason": "baseline tests already failing; autonomous edits blocked",
            "test_exit": before_rc,
            "test_tail": before_output[-3000:],
        })
        state["inconclusive_ticks"] += 1
        save_state(state)
        print("AUTOWORK_INCONCLUSIVE baseline tests failing")
        return 0

    try:
        selected_model, content, reasoning = call_model(build_prompt(state, before_rc, before_output))
        proposal = extract_json(content)
    except Exception as exc:
        state["events"].append({
            "time": utcnow(),
            "decision": "INCONCLUSIVE",
            "reason": f"model/protocol failure: {type(exc).__name__}: {exc}",
        })
        state["inconclusive_ticks"] += 1
        save_state(state)
        print(f"AUTOWORK_INCONCLUSIVE {type(exc).__name__}: {exc}")
        return 0

    action = proposal.get("action")
    hypothesis = str(proposal.get("hypothesis", ""))[:1000]
    expected = str(proposal.get("expected_observation", ""))[:1000]
    note = str(proposal.get("note", ""))[:1000]

    if action == "no_change":
        state["events"].append({
            "time": utcnow(),
            "decision": "NO_CHANGE",
            "model": selected_model,
            "hypothesis": hypothesis,
            "expected_observation": expected,
            "note": note,
        })
        save_state(state)
        print(f"AUTOWORK_NO_CHANGE model={selected_model}")
        return 0

    if action != "write_file":
        reject_event(state, reason=f"unsupported action {action!r}", model=selected_model)
        print("AUTOWORK_REJECT unsupported action")
        return 0

    rel = proposal.get("path")
    replacement = proposal.get("content")
    if rel not in ALLOWED_FILES or not isinstance(replacement, str) or not replacement.strip():
        reject_event(state, reason="invalid path or empty replacement", path=rel, model=selected_model)
        print("AUTOWORK_REJECT invalid proposal")
        return 0

    if any(token in replacement for token in BANNED_SOURCE_TOKENS):
        reject_event(state, reason="proposal contains banned process/network capability", path=rel, model=selected_model)
        print("AUTOWORK_REJECT banned capability")
        return 0

    target = ALLOWED_FILES[rel]
    original = target.read_text()
    if replacement == original:
        state["events"].append({
            "time": utcnow(),
            "decision": "NO_CHANGE",
            "reason": "replacement identical to current file",
            "path": rel,
            "model": selected_model,
        })
        save_state(state)
        print("AUTOWORK_NO_CHANGE identical")
        return 0

    baseline_tests = test_names(ALLOWED_FILES["test_epistemic.py"].read_text())
    if rel == "test_epistemic.py":
        proposed_tests = test_names(replacement)
        missing = sorted(baseline_tests - proposed_tests)
        if missing:
            reject_event(
                state,
                reason="proposal deletes or renames existing tests",
                path=rel,
                missing_tests=missing,
                model=selected_model,
            )
            print(f"AUTOWORK_REJECT missing_tests={missing}")
            return 0

    proposal_hash = hashlib.sha256(replacement.encode()).hexdigest()
    target.write_text(replacement)
    try:
        after_rc, after_output = run_tests()
    except Exception as exc:
        after_rc, after_output = 99, f"validator exception: {type(exc).__name__}: {exc}"

    if after_rc != 0:
        target.write_text(original)
        restore_rc, restore_output = run_tests()
        reject_event(
            state,
            path=rel,
            model=selected_model,
            proposal_sha256=proposal_hash,
            hypothesis=hypothesis,
            expected_observation=expected,
            note=note,
            validator_exit=after_rc,
            validator_tail=after_output[-4000:],
            rollback_verified=restore_rc == 0,
            rollback_test_tail=restore_output[-1200:],
        )
        print(f"AUTOWORK_REJECT path={rel} rollback_verified={restore_rc == 0}")
        return 0

    state["events"].append({
        "time": utcnow(),
        "decision": "ACCEPT",
        "path": rel,
        "model": selected_model,
        "proposal_sha256": proposal_hash,
        "hypothesis": hypothesis,
        "expected_observation": expected,
        "note": note,
        "validator_exit": after_rc,
        "validator_tail": after_output[-3000:],
    })
    state["accepted_edits"] += 1
    save_state(state)
    print(f"AUTOWORK_ACCEPT model={selected_model} path={rel} sha256={proposal_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
