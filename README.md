# migration-kit

Statistically defensible go/no-go verdicts for LLM model migrations.

> **Status: v0.1 in progress.** This README is a placeholder written in Session 1
> so the package builds. The real one — with an executed quickstart — comes in
> Session 3. See [PROGRESS.md](PROGRESS.md) for where the build actually stands,
> and [docs/build-plan.md](docs/build-plan.md) for the plan it is following.

## The problem

Every team running LLMs in production eventually faces a forced migration: a
deprecation, a price change, a provider switch. Today most teams eyeball a
handful of outputs and ship.

`migkit` answers *"is it safe to move from model A to model B?"* with a verdict a
CI system can consume and a compliance reviewer can read: a golden set in, two
models compared under identical pinned judges, a distribution-diff report out, and
an exit code.

Three verdicts, not two:

| Verdict | Exit | Meaning |
|---|---|---|
| `GO` | 0 | No judge shows a significant regression, and B clears the configured floor |
| `NO-GO` | 1 | At least one judge shows a significant regression |
| `REVIEW` | 2 | The sample is too small to tell — collect more data |
| *(error)* | 3 | The tool could not produce a verdict |

`REVIEW` existing at all is the point. A tool that must answer GO or NO-GO will
guess when the evidence is thin, and it will guess in whichever direction its
author found comfortable.

## Built on opik-rigor

Every statistical primitive is imported from
[opik-rigor](https://pypi.org/project/opik-rigor/), none reimplemented — Wilson
intervals for pass rates, Mann-Whitney for regressions, pinned judges with hashed
rubrics, and the append-only evidence log the report is rendered from.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
