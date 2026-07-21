"""
Tests for the debiased hypothesis provider: candidates must be generated,
scored, and redshift-computed identically whether or not a registered claim
exists, so a source with no prior claim (blind search) is handled the same
way as one with a known claim to audit.

No pytest dependency -- run directly:
    python lrd_adapt/hypothesis/test_lrd_hypothesis_provider.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lrd_adapt.hypothesis.lrd_hypothesis_provider import (  # noqa: E402
    generate_lrd_hypotheses,
    generate_lrd_hypotheses_for_state,
)

# SRC04's real CWT-detected peak (see project history: 36030.0 A, matches
# the paper's He I 10833 claim at z=2.328 to within ~1 pixel).
SRC04_OBSERVED_PEAK_ANG = 36030.0


def test_blind_call_needs_no_registered_claim():
    """No primary_line/z_spec at all -- must not raise, must still enumerate
    every physically plausible candidate."""
    result = generate_lrd_hypotheses(observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG)
    assert result["hypotheses"], "blind call produced no candidates at all"
    for h in result["hypotheses"]:
        assert h["_lrd_provenance"]["matches_registered_claim"] is False
        assert h["_lrd_provenance"]["registered_z_spec"] is None


def test_blind_call_all_six_candidates_are_genuinely_close_for_one_peak():
    """Honest finding, not a bug: with only ONE detected peak and no other
    information, the window-centering score should NOT strongly discriminate
    among the six candidates -- that's a real information-theoretic limit,
    not something a scoring formula can paper over. All six line up within
    a few points of each other for SRC04's real peak (matches the actual
    full-pipeline LLM run's independent conclusion: LineIDAmbiguous,
    six-way degeneracy, needs corroborating lines/external evidence to
    break). If this ever starts strongly favoring one candidate from a
    single peak alone, that's a red flag that scoring picked up a bias
    again, not that it got smarter."""
    result = generate_lrd_hypotheses(observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG)
    scores = [h["score"] for h in result["hypotheses"]]
    assert len(scores) == 6, f"expected all 6 lines to be physically plausible here, got {len(scores)}"
    assert max(scores) - min(scores) < 5.0, (
        f"scores span {max(scores) - min(scores):.1f} points -- single-peak scoring "
        "should stay near-degenerate across all plausible candidates, see docstring"
    )
    lines = {h["_lrd_provenance"]["line"] for h in result["hypotheses"]}
    assert "HeI_Pagamma" in lines, "the registered claim's line should still appear as a candidate"


def test_registered_claim_is_provenance_only_not_a_score_boost():
    """Same peak, called with vs without the registered claim, must produce
    IDENTICAL scores and redshifts for every candidate -- the claim may only
    change the `matches_registered_claim`/`registered_z_spec` tags."""
    blind = generate_lrd_hypotheses(observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG)
    with_claim = generate_lrd_hypotheses(
        observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG,
        primary_line="HeI_Pagamma", z_spec=2.328,
    )

    blind_by_line = {h["_lrd_provenance"]["line"]: h for h in blind["hypotheses"]}
    claim_by_line = {h["_lrd_provenance"]["line"]: h for h in with_claim["hypotheses"]}
    assert set(blind_by_line) == set(claim_by_line), "registering a claim changed which candidates are generated"

    for line, h_blind in blind_by_line.items():
        h_claim = claim_by_line[line]
        assert h_blind["score"] == h_claim["score"], f"{line}: score changed by registering a claim"
        assert h_blind["z_representative"] == h_claim["z_representative"], (
            f"{line}: computed redshift changed by registering a claim"
        )

    # Only the tag differs, and only on the matching candidate.
    he1_claim = claim_by_line["HeI_Pagamma"]
    assert he1_claim["_lrd_provenance"]["matches_registered_claim"] is True
    assert he1_claim["_lrd_provenance"]["registered_z_spec"] == 2.328
    for line, h in claim_by_line.items():
        if line != "HeI_Pagamma":
            assert h["_lrd_provenance"]["matches_registered_claim"] is False


def test_registered_z_spec_never_overrides_data_derived_redshift():
    """A deliberately WRONG registered z_spec must not change the computed
    redshift for the matching candidate -- it's data-derived only."""
    result = generate_lrd_hypotheses(
        observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG,
        primary_line="HeI_Pagamma", z_spec=99.0,  # deliberately wrong
    )
    he1 = next(h for h in result["hypotheses"] if h["_lrd_provenance"]["line"] == "HeI_Pagamma")
    assert abs(he1["z_representative"] - 2.3259) < 1e-3, (
        "redshift should come from the observed peak, not the (wrong) registered z_spec"
    )
    assert he1["_lrd_provenance"]["registered_z_spec"] == 99.0, "the wrong claim should still be tagged, just inert"


def test_implausible_registered_line_is_not_grandfathered_in():
    """A registered claim whose implied redshift falls outside its own
    line's plausible window must NOT be force-included -- no candidate gets
    a free pass just because a paper claims it."""
    # At this peak, Halpha's implied z is ~4.49 -- outside its own window
    # only if the peak doesn't correspond to it; use an observed wavelength
    # where Paalpha's implied z falls outside Paalpha's own window.
    obs_wl_outside_paalpha_window = 18756.0 * (1 + 5.0)  # z=5.0, Paalpha window is (0.68, 1.10)
    result = generate_lrd_hypotheses(
        observed_wavelength_ang=obs_wl_outside_paalpha_window,
        primary_line="Paalpha", z_spec=5.0,
    )
    lines_present = {h["_lrd_provenance"]["line"] for h in result["hypotheses"]}
    assert "Paalpha" not in lines_present, (
        "a registered claim outside its own line's redshift window should not be force-included"
    )


# -----------------------------------------------------------------------
# External redshift prior (e.g. grizli's own automated z-fit, once a real
# product exists to parse -- see lrd_hypothesis_provider.py's docstring).
# He I+Pagamma's data-implied z at SRC04's real peak is ~2.3259.
# -----------------------------------------------------------------------

HEI_IMPLIED_Z_AT_SRC04_PEAK = 36030.0 / 10833.0 - 1.0  # ~2.3259


def test_omitting_prior_args_is_unchanged_from_no_prior_support_at_all():
    """Regression guard: callers that don't know about the prior mechanism
    (every existing call site) must see byte-identical scores to before it
    existed."""
    with_defaults = generate_lrd_hypotheses(observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG)
    explicit_none = generate_lrd_hypotheses(
        observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG,
        external_z_prior=None, external_z_prior_sigma=None,
    )
    for h1, h2 in zip(with_defaults["hypotheses"], explicit_none["hypotheses"]):
        assert h1["score"] == h2["score"]
    for h in with_defaults["hypotheses"]:
        assert h["_lrd_provenance"]["prior_bonus"] == 0.0


def test_tight_prior_near_hei_breaks_the_degeneracy():
    """A confident external prior (small sigma) close to He I's data-implied
    z should make He I clearly win, unlike the near-degenerate no-prior
    case -- this is the mechanism that would let a real grizli multi-line
    fit (sharp zgrid peak) actually resolve what one peak alone cannot."""
    result = generate_lrd_hypotheses(
        observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG,
        external_z_prior=HEI_IMPLIED_Z_AT_SRC04_PEAK,
        external_z_prior_sigma=0.01,
        external_z_prior_source="test_grizli_zfit",
    )
    top = result["hypotheses"][0]
    assert top["_lrd_provenance"]["line"] == "HeI_Pagamma", (
        f"expected a tight prior to make HeI_Pagamma win, got {top['_lrd_provenance']['line']!r}"
    )
    runner_up_score = result["hypotheses"][1]["score"]
    assert top["score"] - runner_up_score > 20.0, "tight prior should clearly separate the winner, not just nudge it"
    assert top["_lrd_provenance"]["external_z_prior_source"] == "test_grizli_zfit"


def test_loose_prior_only_mildly_nudges_not_dominates():
    """A coarse prior (sigma large relative to the ~3.6-wide spread of
    candidate redshifts here, e.g. a rough photo-z) should leave the
    SPREAD between candidate scores close to the no-prior baseline --
    everyone gets a similar bonus, so it doesn't newly sharpen the
    discrimination the way a tight prior does. sigma=2.0 turns out to
    still be fairly discriminating at this z scale (candidates span
    z~0.9-4.5) -- sigma=8 is the genuinely loose case."""
    no_prior = generate_lrd_hypotheses(observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG)
    with_loose_prior = generate_lrd_hypotheses(
        observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG,
        external_z_prior=HEI_IMPLIED_Z_AT_SRC04_PEAK,
        external_z_prior_sigma=8.0,
    )
    no_prior_scores = [h["score"] for h in no_prior["hypotheses"]]
    loose_scores = [h["score"] for h in with_loose_prior["hypotheses"]]
    no_prior_spread = max(no_prior_scores) - min(no_prior_scores)
    loose_spread = max(loose_scores) - min(loose_scores)
    assert loose_spread < no_prior_spread + 5.0, (
        f"a loose (sigma=8) prior widened the score spread from {no_prior_spread:.1f} to "
        f"{loose_spread:.1f} -- expected it to stay close to the no-prior baseline"
    )


def test_prior_far_from_every_candidate_barely_helps_anyone():
    """An external prior that doesn't match ANY candidate's implied redshift
    should leave scores close to the no-prior baseline for everyone -- it
    shouldn't manufacture a false winner out of a bad prior."""
    result = generate_lrd_hypotheses(
        observed_wavelength_ang=SRC04_OBSERVED_PEAK_ANG,
        external_z_prior=50.0,  # nowhere near any of the ~0.9-4.5 candidates
        external_z_prior_sigma=0.05,
    )
    for h in result["hypotheses"]:
        assert h["_lrd_provenance"]["prior_bonus"] < 1.0


def test_state_wiring_passes_prior_through_when_present():
    """generate_lrd_hypotheses_for_state should pick up an external prior
    from state if some upstream step ever sets one -- this is the hook a
    future grizli-zfit-reading converter would plug into."""
    state = {
        "file_name": "SRC_NOT_REGISTERED_AT_ALL",
        "peaks": [{"wavelength": SRC04_OBSERVED_PEAK_ANG, "amplitude": 1.0}],
        "external_z_prior": HEI_IMPLIED_Z_AT_SRC04_PEAK,
        "external_z_prior_sigma": 0.01,
        "external_z_prior_source": "test_state_wiring",
    }
    result = generate_lrd_hypotheses_for_state(state, params=None)
    top = result["hypotheses"][0]
    assert top["_lrd_provenance"]["line"] == "HeI_Pagamma"
    assert top["_lrd_provenance"]["external_z_prior_source"] == "test_state_wiring"


def test_state_wiring_defaults_to_no_prior_when_absent():
    """Existing state dicts (no external_z_prior key at all) must behave
    exactly as before this feature existed."""
    state = {
        "file_name": "SRC_NOT_REGISTERED_AT_ALL",
        "peaks": [{"wavelength": SRC04_OBSERVED_PEAK_ANG, "amplitude": 1.0}],
    }
    result = generate_lrd_hypotheses_for_state(state, params=None)
    for h in result["hypotheses"]:
        assert h["_lrd_provenance"]["prior_bonus"] == 0.0


if __name__ == "__main__":
    test_blind_call_needs_no_registered_claim()
    test_blind_call_all_six_candidates_are_genuinely_close_for_one_peak()
    test_registered_claim_is_provenance_only_not_a_score_boost()
    test_registered_z_spec_never_overrides_data_derived_redshift()
    test_implausible_registered_line_is_not_grandfathered_in()
    test_omitting_prior_args_is_unchanged_from_no_prior_support_at_all()
    test_tight_prior_near_hei_breaks_the_degeneracy()
    test_loose_prior_only_mildly_nudges_not_dominates()
    test_prior_far_from_every_candidate_barely_helps_anyone()
    test_state_wiring_passes_prior_through_when_present()
    test_state_wiring_defaults_to_no_prior_when_absent()
    print("OK -- debiased hypothesis provider + external-prior tests passed.")
