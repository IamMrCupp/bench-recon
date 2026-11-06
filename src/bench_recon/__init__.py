"""bench-recon — self-hosted engagement/target system of record.

Records hardware/automotive/IoT pentesting engagements: an engagement (scope,
authorization, client ref, dates, status) holds targets, which accumulate an
append-only findings log and on-disk artifacts. This service is where sensitive
recon data lives; benchhud is only ever handed a ``ref`` and a ref-safe
projection, never the sensitive detail.
"""

__version__ = "0.1.0"  # x-release-please-version

__all__ = ["__version__"]
