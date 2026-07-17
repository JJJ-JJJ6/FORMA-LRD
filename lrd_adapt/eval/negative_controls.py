"""
Negative-control scaffolding (CLAUDE.md build order C: "negative controls,
~100 random catalog sources").

Two things block this from being more than scaffolding right now:

1. **No catalog access.** ~100 random EIGER (or similar) catalog sources
   need to come from real archival data -- this machine has neither Docker
   nor a connection to the eor1 server's grizli products (see CLAUDE.md
   Environment notes). This module defines the manifest format and loader;
   populating it with real entries is an external-data task, not a code task.

2. **An unresolved design gap in the hypothesis provider.** lrd_adapt/
   hypothesis/lrd_hypothesis_provider.py's generate_lrd_hypotheses() takes
   `primary_line` and `z_spec` as REQUIRED arguments -- by design, because
   for the 19 real sources there always IS a paper claim to audit (that's
   the whole point of the "primary hypothesis = paper's claim" design from
   A1). A negative control has no such claim: it's a random source that
   was never proposed as an LRD/AGN broad-line source by anyone. Calling
   generate_lrd_hypotheses_for_state() on one will raise
   (`No primary hypothesis registered for ...`), not gracefully return "no
   claim to test."

   Fixing this needs a real design decision, not a guess:
   - Option A: extend the hypothesis provider with a mode that, given only
     an observed wavelength, enumerates the six candidate identities with
     NO designated "primary"/paper one, and scores whether the pipeline
     stays appropriately uncertain rather than confidently confirming any
     of them.
   - Option B: treat negative controls as "no line detected at all" cases
     and test that the pipeline correctly returns Unknown when the
     converter/CWT stage finds nothing -- a different (and much simpler)
     kind of negative control than "a real line that isn't actually an
     LRD/AGN broad line."
   Both are legitimate negative-control designs testing different failure
   modes; this hasn't been decided, so this module doesn't implement
   either yet.

Manifest format (once real sources are available):
    lrd_adapt/eval/negative_controls_manifest.json
    {"NEG01": {"fits_source": "<how to obtain/convert it>", "notes": "..."}, ...}
Anonymized the same way as the 19 real sources (see anonymizer.py) -- a
negative control's real identity is just as much CLAUDE.md's Anonymization
concern as a real LRD candidate's.
"""
from __future__ import annotations

import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "negative_controls_manifest.json")


def load_negative_controls(path=None):
    """Returns {neg_code: {...}}. Empty until real catalog sources are registered."""
    path = path or _MANIFEST_PATH
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
