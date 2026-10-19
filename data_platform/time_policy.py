"""Separate business freshness, ML development and explicit test release."""

from datetime import date

BUSINESS_CUTOFF = date(2026, 6, 30)
DEVELOPMENT_CUTOFF = date(2026, 5, 31)
TEST_START = date(2026, 6, 1)
FEATURE_PURPOSES = ("development", "scoring")


def validate_feature_cutoff(as_of, purpose="development"):
    if purpose not in FEATURE_PURPOSES or type(as_of) is not date:
        raise ValueError("Explicit feature purpose/date required: development or scoring")
    if as_of > BUSINESS_CUTOFF:
        raise ValueError("Feature date exceeds the available business data end, 2026-06-30")
    if purpose == "development" and as_of > DEVELOPMENT_CUTOFF:
        raise ValueError("June model-development outcomes are locked; development ends 2026-05-31")

