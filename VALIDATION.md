# Validation status

Portfolio preparation used Python 3.12 in an isolated environment without provider credentials.

- Python source syntax checks passed.
- `pytest -q tests`: **691 passed, 55 failed**; one dependency deprecation warning. The failed regressions remain unresolved in this snapshot.
- Failures include conference/inbox behavior, source-level configuration assertions, orchestration state and campaign/provider behavior. See the tests for the intended contracts.
- Live-provider diagnostics and deployment repair utilities are excluded from the portfolio snapshot.
- No live campaign, real recipient list or production service connection was used for validation.

This snapshot supports implementation review. It requires further regression work before deployment.
