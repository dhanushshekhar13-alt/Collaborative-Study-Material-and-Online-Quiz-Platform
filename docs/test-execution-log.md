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
| S00.2 (#17) | Schema covers all entities in FR-01 to FR-14 | `tests/test_schema_coverage.py` (requirement → table map, both directions) | Pass |
| S00.2 (#17) | Migrations run forward and backward on an empty database | `tests/test_migrations.py::test_upgrade_then_downgrade_on_empty_database` (SQLite and PostgreSQL 16), `test_migrations_match_the_models`, `test_migrated_schema_enforces_enum_and_check_constraints` | Pass |
| S00.2 (#17) | Seed script creates test users per role plus sample content | `tests/test_seed_and_contract.py::test_seed_creates_a_user_per_role_and_sample_content`; `python -m app.seed` run on PostgreSQL | Pass |
| S00.2 (#17) | Each module exposes a documented interface and owns its data | `tests/test_module_boundaries.py` (documented `service.py`, allowed imports only), `docs/architecture.md` | Pass |
| S00.2 (#17) | A chat change needs no change to quiz scoring | `test_quiz_scoring_does_not_depend_on_chat`, `test_no_module_imports_chat`, `tests/test_services.py::test_chat_activity_does_not_change_quiz_scores` | Pass |
| S00.2 (#17) | API contract published and versioned | `docs/api/openapi-v1.json`; `test_published_api_contract_is_current`, `test_contract_is_versioned` | Pass |

## S01.1 (#18) — Registration, login and role-based access

Test Plan cases are automated in `tests/test_accounts.py` under their case IDs. The
plan writes the steps against the web UI (UI-1 #62); until it exists, each case runs
against the endpoint the UI will call and asserts the specified message verbatim. The
plan's password `Pass@123` is seven characters, below the eight-character minimum, so
the tests use `Pass@1234`.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_01` | Valid submission creates the account with a success message | `test_UT_01_valid_submission_creates_the_account` | Pass |
| `UT_02` | Invalid email rejected with "Enter a valid email address" | `test_UT_02_invalid_email_is_rejected_with_the_specified_message` | Pass |
| — | Duplicate email or username rejected with a clear message | `test_duplicate_username_is_rejected_with_a_clear_message`, `test_duplicate_email_is_rejected_with_a_clear_message`, `test_duplicates_are_caught_regardless_of_case` | Pass |
| — | Passwords stored hashed, never in plain text | `test_registration_never_stores_the_plain_password` (scrypt, `app.core.security`) | Pass |
| `IT_01` | Valid credentials log in and land on the dashboard; Student features reachable | `test_IT_01_valid_credentials_log_in_and_reach_student_features`, `test_service_guards_protect_another_modules_route` | Pass |
| `IT_02` | Wrong password rejected with "Invalid username or password"; dashboard closed | `test_IT_02_wrong_password_is_rejected_and_the_dashboard_stays_closed` | Pass |
| — | Token expiry enforced server side | `test_token_expiry_is_enforced_server_side`, `test_an_expired_token_is_refused_by_a_protected_endpoint`, `test_token_lifetime_follows_configuration` | Pass |
| — | Unauthenticated request to any protected endpoint rejected | `test_protected_endpoints_reject_unauthenticated_requests`, `test_a_tampered_token_is_rejected` | Pass |
| `IT_28` | Authorization checked server side, not only hidden in the UI | `test_IT_28_a_student_cannot_reach_admin_only_endpoints`, `test_authorization_is_enforced_server_side_not_only_hidden_in_the_ui`, `test_a_token_cannot_be_promoted_by_editing_its_role_claim` | Pass |

Migration `0002` (username) upgrades and downgrades cleanly and matches the models
(`tests/test_migrations.py`). Manually verified on a migrated, seeded SQLite database:
UT_01, UT_02, IT_01, IT_02 and IT_28 behave as above, and seeded users sign in by email.
