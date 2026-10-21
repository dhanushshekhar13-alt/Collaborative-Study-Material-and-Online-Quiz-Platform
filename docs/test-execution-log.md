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

## S02.2 (#21) — Channel membership and member management

Test Plan cases are automated in `tests/test_channel_membership.py` under their case
IDs, run against the endpoints the UI (UI-1 #62) will call. The member list is the
channel-restricted content that exists today; materials and chat gate on the same
`is_member` check. The UI's confirm step is mirrored by `confirm=true` on removal.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `IT_03` | A keyword search returns matching channels | `test_IT_03_search_and_join_adds_the_user_at_once`, `test_keyword_search_returns_only_matching_channels` | Pass |
| `IT_03` | Joining adds the user to the member list immediately and grants access to channel content | `test_IT_03_search_and_join_adds_the_user_at_once`, `test_joining_grants_access_to_channel_content` | Pass |
| `IT_03` | A joined channel appears under My Channels | `test_a_joined_channel_appears_under_my_channels` | Pass |
| `IT_04` | Leaving removes the user from the member list | `test_IT_04_leaving_removes_the_user_and_the_channel_from_my_channels` | Pass |
| `IT_04` | The channel no longer appears under My Channels | Same test | Pass |
| `IT_04` | Channel-restricted content is no longer accessible | `test_after_leaving_channel_content_is_closed` | Pass |
| `ST_03` | A creator or moderator can remove a member after confirming | `test_ST_03_creator_removes_a_member_who_loses_access`, `test_a_moderator_can_remove_a_member`, `test_removal_needs_confirmation` | Pass |
| `ST_03` | The removed member loses access to channel-restricted content | `test_ST_03_creator_removes_a_member_who_loses_access` | Pass |
| — | A regular member cannot remove anyone | `test_a_regular_member_cannot_remove_anyone`, `test_a_non_member_cannot_remove_anyone`, `test_a_moderator_cannot_remove_the_creator` | Pass |

Manually verified on a migrated, seeded SQLite database: search, join, My Channels,
member list, removal by the owner (and refusal for a plain member) and leave behave as
above on the seeded "DBMS study group".

## S05.1 (#24) — Question bank contribution and discovery

Test Plan cases are automated in `tests/test_questions.py` under their case IDs, run
against the endpoints the UI (UI-2 #63) will call. A report's "Incorrect answer" is the
`incorrect` reason of the moderation module's report.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_09` | A fully specified question is saved with all tags and appears in the bank list | `test_UT_09_question_is_saved_with_all_tags_and_appears_in_the_bank`, `test_every_question_type_can_be_contributed`, `test_text_and_tags_are_required`, `test_difficulty_and_type_tags_are_required` | Pass |
| `UT_10` | Submission blocked with "Please select the correct answer" when no correct answer is chosen | `test_UT_10_submission_without_a_correct_answer_is_blocked`, `test_UT_10_every_question_type_needs_a_correct_answer`, `test_correct_answer_must_be_one_of_the_options` | Pass |
| — | The question can be scoped to a channel bank or the general bank | `test_question_can_be_scoped_to_a_channel_bank`, `test_question_without_a_channel_goes_to_the_general_bank`, `test_only_channel_members_can_add_to_a_channel_bank`, `test_a_hidden_channel_bank_is_not_revealed` | Pass |
| `IT_09` | A keyword search returns matching questions | `test_IT_09_search_rate_and_report` (step 1), `test_keyword_search_ignores_case_and_matches_tags`, `test_search_filters_by_tags` | Pass |
| `IT_09` | A rating is saved against the question | `test_IT_09_search_rate_and_report` (steps 2–3), `test_rating_again_replaces_the_earlier_rating`, `test_rating_must_be_one_to_five_stars` | Pass |
| `IT_09` | A report such as "Incorrect answer" is logged for moderation | `test_IT_09_search_rate_and_report` (step 4), `test_reporting_twice_does_not_file_a_duplicate`, `test_a_channel_question_report_is_scoped_to_its_channel` | Pass |

No schema change: `questions`, `question_ratings` and `reports` already exist (migration
`0001`). Unreviewed LLM drafts stay out of the bank
(`test_unreviewed_llm_drafts_stay_out_of_the_bank`).

## S04.1 (#22) — Upload, search and download study materials

Test Plan cases are automated in `tests/test_materials.py` under their case IDs, run
against the endpoints the UI (UI-2 #63) will call. The NFR-01 target for this path is
written down as: search or filter answers within 1 second with 1,000 materials.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_07` | A fully tagged upload succeeds and appears in the subject and topic listing with all tags shown | `test_UT_07_tagged_upload_succeeds_and_is_listed_with_all_tags` | Pass |
| `UT_08` | Upload blocked with "Subject is required" when a mandatory field is blank | `test_UT_08_blank_subject_blocks_the_upload`, `test_every_mandatory_tag_is_required` | Pass |
| — | Supported file types and a maximum size are enforced | `test_supported_file_types_are_accepted`, `test_unsupported_file_types_are_refused`, `test_a_renamed_file_is_refused_by_its_contents`, `test_a_file_over_the_size_limit_is_refused` | Pass |
| `IT_07` | Filtering by subject and topic returns only materials matching both | `test_IT_07_subject_and_topic_return_only_materials_matching_both`, `test_filters_ignore_case_and_surrounding_spaces` | Pass |
| — | An empty result set shows a helpful empty state | `test_an_empty_result_carries_a_helpful_empty_state` | Pass |
| — | Search and filter complete within the agreed response-time target (NFR-01) | `test_search_and_filter_stay_inside_the_response_time_target` | Pass |
| `IT_08` | Material preview loads correctly | `test_IT_08_a_peer_material_previews_and_downloads`, `test_non_previewable_files_download_but_do_not_preview` | Pass |
| `IT_08` | The file downloads successfully to the device | `test_IT_08_a_peer_material_previews_and_downloads`, `test_unicode_file_names_download_under_their_name` | Pass |
| — | Access respects channel membership where the material is channel-scoped | `test_channel_materials_are_for_members_only`, `test_joining_opens_channel_materials_and_leaving_closes_them`, `test_only_members_can_upload_to_a_channel` | Pass |

Migration `0004` (material files) upgrades and downgrades cleanly and matches the
models (`tests/test_migrations.py`). Manually verified on a migrated, seeded SQLite
database: UT_07, UT_08, IT_07 and IT_08 behave as above alongside the seeded materials.

## S04.2 (#23) — Rate, review and report materials

Test Plan cases are automated in `tests/test_material_feedback.py` under their case
IDs, run against the endpoints the UI (UI-2 #63) will call. ST_07's precondition
(an average of 4.0 from two ratings) is set up through the API; the plan's
"Duplicate content" reason is `reason: duplicate`.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `ST_07` | Submitting a rating updates the material's average rating correctly (4.0 from 2 ratings + 5 stars → 4.3) | `test_ST_07_rating_updates_the_average_and_helpful_counts_once`, `test_submitting_a_rating_updates_the_average_correctly`, `test_rating_again_replaces_the_earlier_rating`, `test_a_rating_from_1_to_5_is_required` | Pass |
| `ST_07` | The helpful count increments by one per user and cannot be inflated by repeating | `test_ST_07_rating_updates_the_average_and_helpful_counts_once`, `test_the_helpful_count_cannot_be_inflated_by_repeating`, `test_marking_helpful_does_not_need_or_add_a_rating`, `test_uploaders_cannot_rate_or_mark_their_own_material` | Pass |
| `ST_07` | Review text is displayed with its author | `test_review_text_is_displayed_with_its_author` | Pass |
| `ST_08` | The report is recorded with timestamp and reporter id | `test_ST_08_report_is_recorded_and_appears_in_the_moderation_queue` | Pass |
| `ST_08` | The report appears in the moderation queue for review | `test_ST_08_report_is_recorded_and_appears_in_the_moderation_queue`, `test_channel_material_reports_go_to_that_channels_queue`, `test_reporting_again_while_open_does_not_duplicate` | Pass |
| — | A reason must be selected before submitting | `test_a_reason_must_be_selected_before_submitting`, `test_an_unknown_reason_is_refused` | Pass |

Migration `0005` (optional review rating) upgrades and downgrades cleanly and matches
the models (`tests/test_migrations.py`). Manually verified on a migrated, seeded SQLite
database: rating, review listing, helpful marking and reporting behave as above on the
seeded materials.
