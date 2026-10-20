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

## S02.1 (#20) — Channel creation and classification

Test Plan cases are automated in `tests/test_channels.py` under their case IDs, run
against the endpoints the UI (UI-1 #62) will call. Styling is delivered as a
`type_style` key on every channel response; the visual treatment is part of UI-1.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_03` | A created channel is shown with a "Normal" tag; creator auto-added as member | `test_UT_03_student_creates_a_normal_channel_and_becomes_a_member` | Pass |
| — | Channel name and subject or topic are required | `test_channel_name_is_required`, `test_subject_or_topic_is_required`, `test_a_topic_alone_is_enough` | Pass |
| `UT_04` | Authorized rejected for a Student with "Only authorized users/admins can create Authorized Channels" | `test_UT_04_student_cannot_create_an_authorized_channel`, `test_UT_04_student_cannot_approve_an_authorized_channel` | Pass |
| `UT_04` | The restriction is enforced server side | Same tests call the API directly, bypassing any UI | Pass |
| `ST_27` | An admin can approve a pending Authorized Channel request; it becomes visible platform-wide | `test_ST_27_admin_approves_a_pending_channel_and_it_becomes_visible`, `test_a_decided_request_cannot_be_decided_again`, `test_channel_requests_are_admin_only` | Pass |
| `ST_04` | Type label consistent in search results, channel lists and channel pages | `test_ST_04_type_label_is_consistent_across_list_search_and_page` | Pass |
| `ST_04` | Authorized Channels carry distinct styling | `type_style` = `channel-authorized` vs `channel-normal`, asserted in the ST_04 test | Pass (API); visual styling in UI-1 |

Manually verified on a migrated, seeded SQLite database: UT_03, UT_04, ST_27 and ST_04
behave as above alongside the seeded channels.

## S01.2 (#19) — Profile with contribution history and reputation

Test Plan cases are automated in `tests/test_profiles.py` under their case IDs, run
against the endpoints the profile page (UI-1 #62) will call. "Logging out" is the
client discarding its token; the test then signs in again for a fresh token.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `ST_01` | Edited bio and picture are saved and still correct after logout and login | `test_ST_01_edited_bio_and_picture_persist_after_logout_and_login`, `test_profile_fields_are_saved_in_the_database_not_only_returned`, `test_replacing_the_picture_changes_its_url_and_content` | Pass |
| — | Invalid or oversized uploads are rejected with a clear message | `test_an_oversized_picture_is_rejected_with_a_clear_message`, `test_a_file_that_is_not_an_image_is_rejected_whatever_its_name`, `test_svg_is_rejected_because_it_can_carry_script`, `test_an_empty_file_is_rejected`, `test_a_rejected_upload_keeps_the_previous_picture` | Pass |
| `ST_02` | Profile shows contribution count, reputation total and earned badges matching backend records | `test_ST_02_profile_shows_three_contributions_fifty_points_and_one_badge` (asserts against the owning services), `test_contribution_history_covers_materials_questions_and_quizzes`, `test_removed_drafted_and_unpublished_work_is_not_counted` | Pass |
| `ST_02` | A user with three materials, fifty points and one badge sees exactly that | `test_ST_02_profile_shows_three_contributions_fifty_points_and_one_badge` | Pass |
| — | Sections render an empty state for a brand new user | `test_a_brand_new_user_gets_empty_sections` (API returns zeros and empty lists; rendering is UI-1) | Pass (API) |

Migration `0003` (profile pictures) upgrades and downgrades cleanly and matches the
models (`tests/test_migrations.py`). Manually verified on a migrated, seeded SQLite
database with uvicorn: ST_01 via curl (bio and `profile2.jpg` intact after a second
login, picture served as `image/jpeg`), a 3 MB upload refused with the 2 MB message,
and the seeded student's profile shows its one material and 5 reputation points.
