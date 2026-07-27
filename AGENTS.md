# Repository workflow rules

These rules apply to every human and automated contributor.

## Branches

- Never implement changes directly on `main`.
- Create a new branch from an up-to-date `main` for every feature, fix,
  documentation update, CI change, or refactor.
- Use lowercase kebab-case names in the form
  `<type>/<issue>-<short-description>` when a GitHub issue exists, or
  `<type>/<short-description>` otherwise.
- Allowed types: `feat`, `fix`, `docs`, `refactor`, `test`, `ci`, `chore`,
  and `hotfix`.
- Keep one logical change per branch and delete the branch after merge.

Examples:

- `feat/3-research-library`
- `fix/27-resume-checkpoint`
- `chore/repository-guardrails`

## Pull requests

- Every change to `main`, including documentation and workflow changes, must
  arrive through a GitHub pull request.
- Direct pushes, force pushes, and branch deletion on `main` are forbidden.
- A pull request must explain the change, list verification performed, and
  link the relevant issue when one exists.
- Required CI checks must pass before merge.
- Keep pull requests small enough to review and revert independently.

## CI and deployments

- CI is validation-only. It may lint, test, validate migrations and
  configuration, and build a local container image without publishing it.
- Do not add registry pushes, release publication, GitHub Environments,
  infrastructure changes, cloud credentials, or deployment commands.
- Deployment is out of scope until the repository owner approves a separate
  deployment design and pull request.
- Do not weaken or bypass a failing required check to merge a change.

