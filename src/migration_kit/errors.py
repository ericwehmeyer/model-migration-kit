"""Exception hierarchy for migration-kit.

Everything that can stop a migration decision gets its own type, because the CLI
turns these into distinct exit codes and a CI system needs to tell "the tool
broke" apart from "the migration is unsafe".
"""

from __future__ import annotations


class MigrationKitError(Exception):
    """Base class for every error raised by migration-kit."""


class GoldenSetError(MigrationKitError):
    """Raised when a golden set is malformed, duplicated, or unreadable.

    Validation is strict on purpose. A golden set with two items sharing an id
    produces a comparison whose per-item flip list cannot be trusted, and a
    warning would be read by nobody. It is an error at load time or it is a
    silent wrong answer later.
    """


class ArtifactError(MigrationKitError):
    """Raised when a run artifact is missing, malformed, or does not match.

    Also raised when two artifacts are compared that were produced against
    different golden sets -- comparing model A on one set to model B on another
    is not a migration decision, it is two unrelated numbers side by side.
    """


class JudgeConfigError(MigrationKitError):
    """Raised when the judge configuration is invalid or has changed.

    The same judges must grade both sides of a comparison. If the judge config
    hash differs between two artifacts, the scores were produced by different
    instruments and are not comparable -- the same argument rigor makes about
    rubric drift, one level up.
    """


class JudgeReliabilityError(MigrationKitError):
    """Raised when a judge fails to parse on too many items.

    An unreliable judge does not produce a cautious verdict; it produces a
    meaningless one. Aborting and saying which judge failed on how many items is
    the product working, not the product breaking.
    """

    def __init__(self, judge_name: str, failures: int, total: int, tolerance: float) -> None:
        self.judge_name = judge_name
        self.failures = failures
        self.total = total
        self.tolerance = tolerance
        rate = failures / total if total else 0.0
        super().__init__(
            f"judge {judge_name!r} failed to parse {failures} of {total} responses "
            f"({rate:.1%}), over the {tolerance:.1%} tolerance. The comparison is "
            f"aborted: an unreliable judge invalidates every number downstream of it. "
            f"Inspect the recorded raw responses in the evidence log, fix the rubric "
            f"or the model pin, and re-run."
        )


class ConfigError(MigrationKitError):
    """Raised when the TOML configuration is invalid or a threshold is nonsense."""
