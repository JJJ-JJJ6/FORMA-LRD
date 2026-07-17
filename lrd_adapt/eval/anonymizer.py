"""
Anonymization boundary for the evaluation harness (CLAUDE.md Anonymization
section).

⚠ THIS MODULE MUST NEVER BE IMPORTED BY ANY AGENT-FACING CODE PATH — not
the converter, not the hypothesis provider, not harness/tools.py, not any
skill-loading code. It reads mapping.csv, which holds the SRC-code <-> real
J-name/coordinate correspondence. arXiv:2607.00084 is public; if the
mapping ever became reachable from an LLM tool call, the anonymization
scheme would be worthless. This module is for human/orchestration-level
scripts only (setting up a LOO run, registering a new source) — the FORMA
pipeline itself only ever sees SRC codes, never this file.

See lrd_adapt/eval/test_anonymizer_isolation.py for the regression check
that enforces this boundary (mapping.csv is not in harness/tools.py's
_GREP_FILES registry, and the per-source loaders in lrd_adapt/hypothesis
and lrd_adapt/evidence never return more than one source's data).
"""
from __future__ import annotations

import csv
import os
import re

_MAPPING_PATH = os.path.join(os.path.dirname(__file__), "mapping.csv")
_SRC_CODE_RE = re.compile(r"^SRC(\d+)$")


def mapping_path():
    return os.getenv("LRD_MAPPING_TABLE") or _MAPPING_PATH


def load_mapping(path=None):
    """Returns {src_code: {"real_id": str, "notes": str}}. [] if the file doesn't exist yet."""
    path = path or mapping_path()
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {
            row["src_code"]: {"real_id": row["real_id"], "notes": row.get("notes", "")}
            for row in csv.DictReader(f)
        }


def real_id_for(src_code, path=None):
    """Look up the real identity for a SRC code. For human/orchestration use only."""
    return load_mapping(path).get(src_code, {}).get("real_id")


def src_code_for(real_id, path=None):
    """Reverse lookup: does this real identity already have a SRC code assigned?"""
    for code, entry in load_mapping(path).items():
        if entry["real_id"] == real_id:
            return code
    return None


def _next_src_code(existing_codes):
    numbers = [int(m.group(1)) for c in existing_codes if (m := _SRC_CODE_RE.match(c))]
    return f"SRC{(max(numbers) + 1) if numbers else 1:02d}"


def register_source(real_id, notes="", path=None):
    """
    Assign a SRC code to a real identity, appending to mapping.csv.
    Idempotent: re-registering the same real_id returns its existing code.

    For human/orchestration use only when setting up a new evaluation run —
    never call this from agent-facing code.
    """
    path = path or mapping_path()
    mapping = load_mapping(path)

    existing = src_code_for(real_id, path)
    if existing is not None:
        return existing

    code = _next_src_code(mapping.keys())
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["src_code", "real_id", "notes"])
        writer.writerow([code, real_id, notes])
    return code


def registered_src_codes(path=None):
    """All SRC codes currently registered, sorted."""
    return sorted(load_mapping(path).keys())
