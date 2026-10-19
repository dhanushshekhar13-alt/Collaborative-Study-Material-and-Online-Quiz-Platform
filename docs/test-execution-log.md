# Test execution log

The Test Plan (`Documents/Test Plan Document.docx`) defines the functional test cases.
This log records, per story, which automated tests prove its acceptance criteria and
the result of the CI run that merged it. Foundation stories (S00.x) have no functional
Test Plan cases, so their evidence is the automated checks listed below.

| Story | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| S00.1 (#16) | main protected, requires passing CI | Branch protection on `main` requires the `lint-and-test` check | Configured |
| S00.1 (#16) | CI runs lint and unit tests on every PR | `.github/workflows/ci.yml` (`ruff check`, `ruff format --check`, `pytest`) | Pass |
| S00.1 (#16) | README setup in under ten commands | `tests/test_repository_hygiene.py::test_readme_setup_fits_in_ten_commands` | Pass |
| S00.1 (#16) | Boots from environment, no secrets committed | `tests/test_config.py`, `tests/test_repository_hygiene.py::test_no_secret_values_are_committed` | Pass |
| S00.1 (#16) | Single documented deploy command | `scripts/deploy.sh` + `docker-compose.yml`, README "Deployment" | Documented |
| S00.1 (#16) | LLM keys read from configuration | `tests/test_config.py::test_llm_key_comes_from_configuration_and_is_masked` | Pass |
