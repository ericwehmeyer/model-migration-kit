"""Exception hierarchy for model-migration-kit.

Everything that can stop a migration decision gets its own type, because the CLI
turns these into distinct exit codes and a CI system needs to tell "the tool
broke" apart from "the migration is unsafe".
"""

from __future__ import annotations


class MigrationKitError(Exception):
    """Base class for every error raised by model-migration-kit."""


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


class HeaderlessArtifactError(ArtifactError):
    """Raised when an artifact file exists but records no header at all.

    A subclass rather than just a message, because this is the one artifact
    failure a caller can act on without an operator. The writer appends the
    header before anything else, so a file with no header is a process killed
    before its first record landed: it holds nothing that was ever attributable
    to a model or a golden set, and nothing anybody paid for.
    :func:`~model_migration_kit.runner.run_goldenset` therefore restarts over it,
    exactly as it already did over a zero-byte file.

    Everything else ``ArtifactError`` covers is the opposite -- evidence that
    something is wrong with content that may have cost real money -- which is why
    this stays narrow rather than becoming a general "unreadable" case. Anyone
    catching ``ArtifactError`` still catches this.
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


class DependencyContractError(MigrationKitError):
    """Raised when opik-rigor's output no longer has the shape this build reads.

    Not a defensive flourish. model-migration-kit consumes rigor's report dictionaries
    by string key, and one of those keys -- ``underpowered`` on a failed pass-rate
    gate -- is what separates "this model demonstrably missed the bar" (NO-GO)
    from "this sample is too small to say" (REVIEW). If the key ever disappears,
    a `.get(..., False)` would read as *powered*, and a REVIEW would silently
    become a NO-GO: a blocked migration, a wrong verdict, and nothing raised
    anywhere to say so.

    So the rule is that a missing key stops the comparison and names what is
    missing. A tool whose entire claim is that its verdicts are traceable cannot
    issue one by guessing at its instrument's output.
    """


class ReportError(MigrationKitError):
    """Raised when a report cannot be rendered from the evidence it was given.

    Added deliberately after this module was frozen, rather than by reusing the
    base class, because the CLI maps exception types to exit codes and "the
    evidence log does not contain a verdict" is a different thing from "the tool
    broke". The alternative on the table was raising ``MigrationKitError``
    directly, which would have made every unrelated failure indistinguishable
    from a report failure at the one place that matters.

    Note what this must never be used for: a report that is *partial* is not a
    report that failed. A run killed halfway still renders, showing observed
    counts against expected ones -- that is the whole point of rendering from the
    evidence log.

    Narrowed after the fact, and the narrowing is worth recording. This docstring
    originally claimed the two cases "the evidence file is missing" and "the log
    holds no comparison record", but the frozen Session 3 contract assigns both to
    ``ArtifactError`` and names it in a test. An unreadable or wrong-shaped
    artifact is an artifact problem wherever it is encountered, and having two
    types for it by which module noticed would be a distinction no caller could
    use -- both map to exit 3 regardless. So this type keeps the case the contract
    left to it and nothing else: a report that was built but cannot honestly be
    written, which today means a self-containment violation.
    """
