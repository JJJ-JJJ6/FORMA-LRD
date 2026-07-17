"""
Negative-control scaffolding (CLAUDE.md build order C: "negative controls,
~100 random catalog sources").

## Where candidates actually come from (resolved 2026-07-17)

The upstream MAST/grizli extraction pipeline lives in a separate, untracked
working directory (~/eiger_specviz locally, deployed to the eor1 server at
/data2/eiger_specviz/ -- NOT part of this git repo, no version control of
its own currently). Its make_catalog.py stage already produces a full
per-field photometric catalog ({field}/Prep/{field}_phot.fits) with every
detected source, not just the 19 known targets; match_ids.py just throws
almost all of them away. select_negative_controls.py (in that separate
directory) samples a candidate batch from the rest, excluding anything
within 0.3 arcsec of a known target, and writes {field}_negative_candidates.csv
in the same id/ra/dec/grizli_id shape match_ids.py's own output uses, ready
to feed into a grism extraction the same way the 19 real sources were.

## The part that was genuinely unresolved, and how (worked out 2026-07-17)

"Not one of our 19 targets" is a much weaker guarantee than "confirmed not
a broad-line source" -- Kapoor+26 started from 54 broad-line candidates
before narrowing to 19, so an unclaimed catalog source could still be a
real broad-line emitter nobody's final table happened to include. A batch
of such candidates is NOT valid negative controls on its own; there's no
ground truth to score the pipeline's output against.

The fix: ground truth doesn't need to come from knowing the object's true
astrophysical classification (which isn't available). It comes from
independently confirming what's actually observable in THAT specific
extracted spectrum -- via visual inspection in specvizitor (the same tool
already used for the real 19 sources) or the CWT feature-finder as a quick
automated screen. Only candidates independently confirmed to show no
significant broad emission line become real negative controls; the ones
that DO show something get set aside (they might be genuine sources the
paper's search just didn't happen to catch). This tests a specific,
checkable claim -- "does the pipeline hallucinate a detection where an
independent look found nothing" -- rather than the harder, currently
unanswerable "does the pipeline get this object's true classification
right."

## Still unresolved: the hypothesis-provider design gap

lrd_adapt/hypothesis/lrd_hypothesis_provider.py's generate_lrd_hypotheses()
takes `primary_line` and `z_spec` as REQUIRED arguments -- by design,
because for the 19 real sources there always IS a paper claim to audit
(that's the whole point of the "primary hypothesis = paper's claim" design
from A1). A negative control -- even a properly screened one -- still has
no such claim; calling generate_lrd_hypotheses_for_state() on one will
raise (`No primary hypothesis registered for ...`), not gracefully return
"no claim to test." This still needs a real design decision:

- Option A: extend the hypothesis provider with a mode that, given only an
  observed wavelength (if the screening step above found one after all, or
  for a candidate deliberately kept despite showing something), enumerates
  the six candidate identities with NO designated "primary"/paper one, and
  scores whether the pipeline stays appropriately uncertain rather than
  confidently confirming any of them.
- Option B: for candidates the screening step confirms show NO line at
  all, test that the pipeline correctly returns Unknown when the
  converter/CWT stage finds nothing -- simpler, and now the more directly
  applicable of the two given how screening works, since a properly
  screened negative control is by construction a "no line" case.

Manifest format (once real, screened sources are available):
    lrd_adapt/eval/negative_controls_manifest.json
    {"NEG01": {"fits_source": "<how to obtain/convert it>",
               "screening_status": "<confirmed_no_line | flagged_has_signal | unscreened>",
               "notes": "..."}, ...}
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
