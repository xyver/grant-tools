"""
Grant Analyzer - thin, country-keyed coordinator.

This file used to contain the entire US-specific implementation. As of
2026-06-29 it's a coordinator over two real backends with genuinely
different capabilities:

    us_backend.py  - full: discovery, precedent, readiness scoring,
                      live opportunity checks, writing templates
    can_backend.py - partial: discovery and precedent only (see its
                      CAPABILITIES dict) - no Canadian Grant Readiness
                      Score, writing template, or live-opportunity-check
                      exists yet

Why the split: an external architecture review (2026-06-29) flagged that
this module was accumulating US-specific assumptions (CFDA-keyed lookups,
USA-XX loc_id parsing) while nominally being country-agnostic, with no
second real backend to check that assumption against. Splitting now,
before a third country, keeps each backend's real capabilities honest
and visible instead of letting grant_analyzer.py grow a conditional limb
per country (the international/USAID branch already inside
us_backend.score_and_rank_programs was a live example of exactly that
pattern starting to happen).

No formal CountryGrantBackend interface exists yet - deliberately. With
one full implementation and one partial one, the real shared method
signatures aren't visible yet. Revisit once can_backend gets a real
scoring function (a second full implementation) - that's when the actual
shared shape between two complete backends becomes visible, instead of
guessed.

Usage:
    import grant_analyzer as ga
    ga.CAPABILITIES["CAN"]              # {'discovery': True, 'precedent': True, 'readiness_scoring': False, ...}
    backend = ga.get_backend("US")      # the us_backend module
    backend.match_grant_programs(...)

    # or, just as commonly, import the backend you need directly:
    import us_backend
    import can_backend
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import us_backend  # noqa: E402
import can_backend  # noqa: E402

BACKENDS = {
    "US": us_backend,
    "CAN": can_backend,
}

CAPABILITIES = {country: backend.CAPABILITIES for country, backend in BACKENDS.items()}


def get_backend(country_code):
    """Look up a country's backend module by its 2-3 letter code (e.g.
    "US", "CAN"). Raises rather than returning None for an unknown
    country - a missing backend should be a loud error, not a silent
    fallback to whichever backend happened to be imported first."""
    backend = BACKENDS.get(country_code.upper())
    if backend is None:
        raise ValueError(f"No grant backend for country '{country_code}' - available: {sorted(BACKENDS)}")
    return backend


def supports(country_code, capability):
    """Check one capability for one country without raising - e.g.
    supports("CAN", "readiness_scoring") -> False. Use this to branch
    cleanly instead of try/except NotImplementedError for control flow."""
    backend = get_backend(country_code)
    return bool(backend.CAPABILITIES.get(capability, False))


if __name__ == "__main__":
    print("=" * 70)
    print("Grant Analyzer - country capability summary")
    print("=" * 70)
    for country, caps in CAPABILITIES.items():
        print(f"\n{country}:")
        for capability, supported in caps.items():
            print(f"  {capability}: {'YES' if supported else 'no'}")

    print("\n" + "=" * 70)
    print("Run us_backend.py or can_backend.py directly for each backend's own")
    print("real-world validation block (Woodlands Mission / BC wildfire case).")
    print("=" * 70)
