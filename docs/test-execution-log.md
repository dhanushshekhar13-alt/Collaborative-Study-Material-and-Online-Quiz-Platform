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

## S05.2 (#25) — Quiz assembly and publishing

Test Plan cases are automated in `tests/test_quiz_building.py` under their case IDs,
run against the endpoints the UI (UI-2 #63) will call (`/api/v1/quizzes`, see
`app/modules/quizzes/router.py`). A quiz is saved as a draft and published in a separate
step, as in the Create Quiz use case.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `IT_10` | A quiz is generated with the requested count, difficulty and timer | `test_IT_10_quiz_is_built_with_requested_count_difficulty_and_timer`, `test_preview_picks_matching_questions_without_saving`, `test_reviewed_question_set_is_saved_in_the_given_order`, `test_reviewed_set_must_match_the_requested_count`, `test_count_duration_and_difficulty_are_validated` | Pass |
| `ST_10` | A newly contributed question is immediately selectable when building a quiz | `test_ST_10_new_question_is_immediately_selectable_for_a_quiz`, `test_unpublished_llm_drafts_and_unknown_questions_cannot_be_used` | Pass |
| — | The request fails gracefully when the bank has too few matching questions | `test_too_few_matching_questions_fails_gracefully`, `test_empty_bank_reports_zero_available` | Pass |
| `ST_09` | A published quiz appears under the channel's Quizzes tab | `test_ST_09_published_quiz_appears_in_channel_quizzes_tab_for_all_members`, `test_published_general_quiz_is_listed_for_everyone`, `test_a_quiz_is_published_once_and_only_by_its_author` | Pass |
| `ST_09` | It is visible to all channel members | `test_ST_09_published_quiz_appears_in_channel_quizzes_tab_for_all_members`, `test_members_do_not_see_the_answer_key`, `test_non_members_cannot_see_a_channels_quizzes`, `test_publishing_to_a_channel_needs_membership`, `test_only_authorized_users_publish_to_authorized_channels`, `test_channel_bank_questions_stay_in_their_channel` | Pass |
| — | An unpublished quiz remains private to its author | `test_unpublished_quiz_is_private_to_its_author`, `test_profile_counts_only_published_quizzes` | Pass |

No schema change: `quizzes` and `quiz_questions` already exist (migration `0001`).

## S06.1 (#28) — Quiz attempt with automatic scoring and timer

Test Plan cases are automated in `tests/test_quiz_attempts.py` under their case IDs,
against the attempt endpoints. Quizzes are built with the quizzes service until S05.2
(#25) adds the quiz-building endpoints. The timer is tested by moving the attempt
clock forward rather than waiting. The NFR-01 target for this path is 3 seconds for
submission with result display; it is checked on a 50-question quiz.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_11` | A four-of-five attempt is scored 4/5 (80 percent) and displayed immediately | `test_UT_11_four_of_five_is_scored_80_percent_and_shown_immediately` | Pass |
| — | Objective questions are scored deterministically without the LLM (NFR-06) | `test_objective_scoring_is_deterministic_and_never_uses_the_llm`, `test_true_false_and_short_answer_are_scored_too`, `test_descriptive_answers_are_stored_for_evaluation_not_scored` | Pass |
| — | Submission and result generation complete within the response-time target (NFR-01) | `test_submission_and_result_stay_inside_the_response_time_target` | Pass |
| `UT_12` | A warning names the number of unanswered questions before final submission | `test_UT_12_warning_names_the_unanswered_count_before_final_submission`, `test_the_warning_is_also_shown_while_answering`, `test_blank_answers_count_as_unanswered` | Pass |
| `UT_12` | The configured rule decides whether submission is blocked or merely warned | `test_UT_12_warning_names_the_unanswered_count_before_final_submission` (warn), `test_UT_12_block_rule_refuses_submission_until_all_are_answered` (block), `test_a_complete_attempt_submits_without_any_warning` | Pass |
| `ST_11` | The quiz auto-submits when the timer reaches zero | `test_ST_11_quiz_auto_submits_at_timeout_scoring_only_answered_questions`, `test_saving_after_the_limit_auto_submits`, `test_late_answers_are_not_counted_after_the_timer_ends`, `test_a_submit_arriving_just_after_zero_still_counts`, `test_the_time_limit_is_fixed_when_the_attempt_starts` | Pass |
| `ST_11` | Only answered questions are scored; the rest are marked unattempted | `test_ST_11_quiz_auto_submits_at_timeout_scoring_only_answered_questions`, `test_UT_12_warning_names_the_unanswered_count_before_final_submission` | Pass |
| — | The remaining time is visible throughout the attempt | `test_remaining_time_is_visible_throughout_the_attempt`, `test_reopening_a_running_quiz_resumes_the_same_timer` | Pass |
| `IT_12` | An official quiz is attempted and scored identically to a peer-created quiz | `test_IT_12_official_quiz_is_attempted_and_scored_like_a_peer_quiz` | Pass |
| — | Non-members cannot attempt a channel-scoped quiz | `test_non_members_cannot_attempt_a_channel_quiz`, `test_a_member_who_leaves_cannot_start_again`, `test_a_quiz_in_a_hidden_channel_is_not_revealed` | Pass |

Migration `0006` (attempt timer) upgrades and downgrades cleanly and matches the
models (`tests/test_migrations.py`). Manually verified on a migrated, seeded SQLite
database: the seeded student and faculty start "CPU scheduling basics" with 15 minutes
on the clock, get "2 questions are unanswered" when submitting one answer, and see
`1/3 (33.3%)` after confirming; the admin, not a member of that channel, is refused.

## UI-1 (#62) — Web UI foundation: auth, profile and channel screens

The UI is static HTML/CSS/JS served at `/app/`. Each acceptance criterion is checked in
a real browser (Playwright + Chromium) against a live server with the seeded data, in
`tests/test_web_ui.py::test_UI_1_acceptance_criteria_in_the_browser`. That test runs
locally and is skipped in CI, which has no browser. The other tests in the file run
everywhere.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_01`, `UT_02` | Register creates an account and shows "Registration successful"; an invalid email shows "Enter a valid email address" | browser test, register steps | Pass (local) |
| `IT_02` | Login signs the user in; wrong credentials show "Invalid username or password" | browser test, login steps | Pass (local) |
| `ST_01` | Profile shows the user's details and saves an edited bio | browser test, profile steps | Pass (local) |
| `UT_03`, `UT_04` | Create Channel makes a Normal channel; Authorized is disabled for users without the role (and enabled for an authorized user) | browser test, create-channel and faculty steps | Pass (local) |
| `IT_03`, `IT_04` | Channels can be searched, joined and left; My Channels is shown | browser test, search/join/My Channels/leave steps | Pass (local) |
| `ST_04` | Authorized and Normal labels in search results, lists and the channel page | browser test, label assertions on all three screens | Pass (local) |
| — | Pages load only their own files under a strict CSP; server text is never parsed as HTML | `test_ui_files_are_served_with_a_strict_content_security_policy`, `test_the_page_loads_only_its_own_files`, `test_server_text_is_never_inserted_as_html` | Pass |
| — | Every endpoint the UI calls exists in the API | `test_every_endpoint_the_ui_calls_exists` | Pass |
| — | Usable at phone width (no horizontal scroll at 375 px) | browser test, phone step | Pass (local) |

No API or schema change: `docs/api/openapi-v1.json` is unchanged, since the UI routes are
excluded from the contract.

## S08.1 (#32) — Descriptive answer evaluation with concept feedback

Executed 2026-10-22 with `python -m pytest tests/test_descriptive_evaluation.py`. The
LLM is a scripted fake (`FakeLlm`). Each case is also run with no LLM configured, using
the deterministic `concept-match` evaluator.

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `UT_15` | A complete, accurate answer receives a high score with feedback confirming concept coverage | `test_UT_15_complete_answer_scores_high_with_all_concepts_confirmed`, `test_UT_15_concept_match_scores_a_complete_answer_10_of_10` | Pass |
| `UT_16` | A partial answer receives a proportionally lower score | `test_UT_16_partial_answer_scores_lower_and_names_isolation_and_durability`, `test_UT_16_concept_match_scores_two_of_four_as_5`, `test_scores_are_proportional_to_concept_coverage` | Pass |
| — | Scores are returned on a consistent, documented scale (0–10, steps of 0.5) | `test_llm_scores_are_kept_on_the_0_to_10_scale` | Pass |
| `IT_16` | Feedback lists at least one strength and one weakness, not only a score | `test_IT_16_feedback_lists_clear_definitions_and_missing_examples`, `test_IT_16_concept_match_spots_definitions_without_examples`, `test_feedback_always_has_a_strength_and_a_weakness` | Pass |
| `UT_16` | Missing concepts are named individually (Isolation and Durability) | `test_UT_16_partial_answer_scores_lower_and_names_isolation_and_durability`, `test_UT_16_concept_match_scores_two_of_four_as_5` | Pass |
| `IT_15` | A blank submission is blocked with "Answer cannot be empty" | `test_IT_15_blank_answer_is_blocked_and_nothing_is_sent_to_the_llm`, `test_an_answer_addressing_no_concept_scores_zero` | Pass |
| `IT_15` | No empty request is sent to the LLM | `test_IT_15_blank_answer_is_blocked_and_nothing_is_sent_to_the_llm`, `test_IT_15_the_service_also_refuses_blank_answers_before_calling_the_llm` | Pass |
| — (NFR-06) | LLM output is validated; failures fall back to deterministic scoring and are audited | `test_an_llm_failure_falls_back_to_concept_matching`, `test_the_answer_is_sent_as_delimited_data_with_the_expected_concepts` | Pass |
| — | With the offline `fake` provider, evaluation uses concept matching and records nothing | `test_the_offline_fake_provider_evaluates_by_concept_matching` | Pass |
| — | Only descriptive questions the caller can read are evaluated; sign-in required | `test_objective_questions_are_not_evaluated_this_way`, `test_a_question_without_concepts_needs_the_llm`, `test_a_question_in_a_hidden_channel_is_not_revealed`, `test_evaluation_needs_a_signed_in_user` | Pass |

No real provider was called. A run against a live LLM needs `LLM_PROVIDER`,
`LLM_MODEL` and `LLM_API_KEY`, and is still to be recorded. No schema change.
`POST /api/v1/evaluations/descriptive` is new, and `docs/api/openapi-v1.json` is
regenerated.

## S06.2 (#29) — Attempt persistence and quiz history

Executed 2026-10-22 with `python -m pytest tests/test_quiz_history.py` (SQLite; CI also
runs the suite on PostgreSQL).

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `IT_11` | A submitted attempt is listed in My Quiz History and opens with an accurate breakdown | `test_IT_11_submitted_attempt_is_listed_and_opens_with_an_accurate_breakdown`, `test_history_lists_attempts_newest_first`, `test_an_attempt_whose_timer_ran_out_appears_auto_submitted` | Pass |
| `ST_12` | Aggregate accuracy is computed correctly: 82.5 percent over 60, 80, 100 and 90 | `test_ST_12_average_accuracy_updates_to_82_5_after_a_fourth_attempt`, `test_each_attempt_counts_once_whatever_the_quiz_length`, `test_average_is_rounded_to_one_decimal` | Pass |
| — (NFR-04) | No committed attempt is lost during a simulated transient failure | `test_NFR_04_no_committed_attempt_is_lost_when_a_submission_fails_midway`, `test_NFR_04_a_failed_commit_keeps_earlier_attempts_and_can_be_retried` | Pass |
| — | History is private to its owner, excludes running attempts, filters by quiz and needs sign-in | `test_history_shows_only_the_callers_own_attempts`, `test_attempts_still_running_are_not_listed`, `test_history_can_be_filtered_to_one_quiz`, `test_history_needs_a_signed_in_user` | Pass |

No schema change: attempts were already stored with score, timestamps and answers by
S06.1. The new route is `GET /api/v1/attempts`; `docs/api/openapi-v1.json` is regenerated.

## S03.1 (#26) — Official content management in Authorized Channels

Test Plan cases are automated in `tests/test_official_content.py` under their case
IDs, run against the endpoints the UI will call. "Manage Official Content" is shown
when the channel response's `can_manage_official_content` is true (and per material,
`can_manage`); the API applies the same rule, so a direct request is refused whatever
the UI shows.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_05` | Published material is tagged 'Official/Verified' within the channel | `test_UT_05_authorized_user_publishes_official_material`, `test_everything_published_in_an_authorized_channel_is_official`, `test_official_cannot_be_claimed_outside_an_authorized_channel` | Pass |
| `ST_06` | An authorized user can edit and remove official content | `test_ST_06_privileges_differ_between_authorized_and_normal_channels`, `test_authorized_user_can_edit_official_content`, `test_editing_keeps_required_tags`, `test_authorized_user_can_remove_official_content`, `test_admins_can_manage_official_content`, `test_official_flag_survives_edits` | Pass |
| — | Official content is visually distinguished from peer content | `test_official_content_is_distinguished_from_peer_content` (`content_label` / `content_style`, `official` filter) | Pass |
| `UT_06` | The Manage Official Content option is hidden or disabled for a regular member | `test_UT_06_regular_member_cannot_manage_official_content`, `test_manage_option_is_shown_only_to_authorized_users`, `test_authorized_user_outside_the_channel_cannot_manage_it` | Pass |
| `UT_06` | A direct request is rejected with 'Insufficient permissions' | `test_UT_06_regular_member_cannot_manage_official_content`, `test_peer_material_is_managed_by_its_uploader_only` | Pass |
| `ST_06` | The same regular member can still contribute normally in a Normal Channel | `test_ST_06_privileges_differ_between_authorized_and_normal_channels`, `test_regular_member_still_contributes_in_a_normal_channel` | Pass |

No schema change: `study_materials.is_official` already exists (migration `0001`).
Manually verified on a migrated, seeded SQLite database: the seeded authorized user
publishes, edits and removes official material in the seeded Authorized Channel, and
the seeded student sees `can_manage_official_content: false` and gets
`Insufficient permissions` on edit.

## S07.1 (#30) — LLM question generation with mandatory review

Test Plan cases are automated in `tests/test_llm_generation.py` under their case IDs,
run against the `/api/v1/ai/generations` endpoints the UI will call. Every test uses
the deterministic `FakeProvider` (`LLM_PROVIDER=fake`) or a scripted provider, so no
key or network is needed; with `LLM_PROVIDER=none` the endpoints answer 503.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_13` | Five to ten questions are returned, each with options, a marked correct answer and an explanation | `test_UT_13_generates_questions_with_options_answer_and_explanation`, `test_the_requested_number_of_questions_is_returned`, `test_counts_outside_five_to_ten_are_refused` | Pass |
| `UT_13` | Generated content matches the selected subject, topic and difficulty | `test_generated_content_matches_selected_subject_topic_and_difficulty`, `test_validate_item_rejects_off_topic_and_incomplete_items` | Pass |
| — | An LLM failure or timeout surfaces a clear error rather than a blank screen | `test_a_provider_failure_returns_a_clear_error_and_is_logged`, `test_a_provider_timeout_returns_a_clear_error`, `test_unusable_output_after_every_retry_is_a_clear_error`, `test_with_no_provider_configured_the_feature_says_so`, `test_the_http_provider_maps_timeouts_and_errors` | Pass |
| `UT_14` | Add to Quiz is unavailable until each question is approved or rejected in the review screen | `test_UT_14_add_to_quiz_is_unavailable_until_every_question_is_reviewed`, `test_nothing_approved_means_no_quiz` | Pass |
| `IT_13` | A flawed question can be edited and saved with the correction, or rejected and discarded | `test_IT_13_a_flawed_question_can_be_edited_and_saved_with_the_correction`, `test_a_reviewed_question_cannot_be_reviewed_or_edited_again`, `test_an_edit_must_keep_the_question_answerable` | Pass |
| `IT_13` | Rejected questions never appear in the bank or the quiz | `test_IT_13_a_rejected_question_is_discarded_and_never_reaches_bank_or_quiz` | Pass |
| — | Batches are private; channel banks need membership | `test_a_batch_is_private_to_the_user_who_generated_it`, `test_a_question_from_another_batch_is_not_found`, `test_generating_into_a_channel_bank_needs_membership` | Pass |

No schema change: generated questions are `questions` rows with `source=llm` and
`status=draft` (already in the model), and each batch is its `llm_requests` audit row.

## S07.2 (#31) — Adaptive difficulty constrained to the selected topic

Test Plan cases are automated in `tests/test_adaptive_quiz.py` under their case IDs,
run against the `/api/v1/ai/adaptive-quizzes` endpoints with the deterministic
`FakeProvider` (or a wrapper that answers off topic first). The rule itself is a pure
function, `app.modules.llm.adaptive.level_after`, tested in isolation.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `IT_14` | Three consecutive correct answers raise the next question's difficulty | `test_IT_14_three_correct_answers_raise_the_next_questions_difficulty`, `test_level_after_follows_the_adaptation_rule` | Pass |
| `ST_13` | Two consecutive incorrect answers lower the next question's difficulty | `test_ST_13_two_incorrect_answers_lower_the_next_questions_difficulty`, `test_level_after_follows_the_adaptation_rule`, `test_streaks_restart_after_a_change` | Pass |
| — | The current difficulty level is discoverable by the user | `test_the_current_difficulty_is_discoverable` (`current_difficulty`, `current_difficulty_label`, per-answer "Difficulty raised to Hard" message) | Pass |
| `ST_14` | All questions across a full adaptive attempt carry the selected topic tag | `test_ST_14_every_question_in_a_full_adaptive_quiz_keeps_the_topic` | Pass |
| — | A generated question outside the topic is rejected and regenerated | `test_an_off_topic_generated_question_is_rejected_and_regenerated` | Pass |
| `ST_14` | Topic containment holds at every difficulty level reached | `test_topic_containment_holds_at_every_difficulty_reached` | Pass |
| — | Bank first, AI drafts never reach the bank; answers survive provider failures | `test_bank_questions_are_used_first_and_ai_drafts_never_reach_the_bank`, `test_without_an_llm_the_bank_alone_runs_the_quiz_until_it_runs_out`, `test_a_failed_next_question_never_loses_the_answer` | Pass |

Migration `0007` (`adaptive_sessions`) upgrades and downgrades cleanly and matches the
models (`tests/test_migrations.py`).

## S03.2 (#27) — Peer contribution, rating and reporting in Normal Channels

Test Plan cases are automated in `tests/test_peer_contribution.py` under their case
IDs, run against the endpoints the UI will call. IT_05's discussion post is the new
channel board (`/api/v1/channels/{id}/posts`); IT_06 and ST_05 use the existing
materials, questions and quizzes endpoints with `channel_id`. The plan's "Duplicate"
reason is `reason: duplicate`.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `IT_05` | A post can be created, edited and deleted by its author | `test_IT_05_creator_creates_edits_and_deletes_a_post`, `test_any_member_can_post_not_only_the_creator`, `test_only_the_author_edits_or_deletes_a_post`, `test_posts_are_for_channel_members`, `test_a_post_needs_text`, `test_a_post_from_another_channel_is_not_found` | Pass |
| `IT_05` | Deletion asks for confirmation | `test_deleting_a_post_needs_confirmation` | Pass |
| `IT_05` | Edited posts show an edited indicator | `test_IT_05_creator_creates_edits_and_deletes_a_post`, `test_edited_posts_show_an_edited_indicator` | Pass |
| `IT_06` | A non-creator member can upload a material, add a bank question and create a quiz | `test_IT_06_any_member_contributes_material_question_and_quiz` | Pass |
| `IT_06` | All three contributions are visible to other channel members | `test_IT_06_any_member_contributes_material_question_and_quiz` | Pass |
| — | Contributions are attributed to their author | `test_contributions_are_attributed_to_their_author` | Pass |
| `ST_05` | A submitted rating updates the item's average rating | `test_ST_05_peers_rate_and_report_channel_content`, `test_a_rating_updates_the_items_average` | Pass |
| `ST_05` | A report with a reason such as Duplicate is logged and routed to moderators | `test_ST_05_peers_rate_and_report_channel_content`, `test_a_duplicate_report_on_a_question_reaches_moderators` | Pass |

Migration `0008` (`channel_posts`) upgrades and downgrades cleanly and matches the
models (`tests/test_migrations.py`). Manually verified on a migrated, seeded SQLite
database (including downgrade to `0007` and back): the seeded student creates, edits
(shown as edited) and deletes a post, deletion without confirmation is refused, and
channel quizzes and questions carry their author's name.

## S08.2 (#33) — Descriptive results stored beside deterministic objective scoring

Executed 2026-10-23 with `python -m pytest tests/test_descriptive_results.py`. The LLM is
a scripted fake (`FakeLlm`); ST_15 and ST_16 are also run with no LLM configured, using
the deterministic `concept-match` evaluator.

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `ST_15` | Descriptive score and feedback are displayed inside the stored attempt in Quiz History | `test_ST_15_descriptive_score_and_feedback_are_shown_in_the_stored_attempt`, `test_ST_15_without_an_llm_the_concept_match_result_is_stored`, `test_the_stored_feedback_does_not_change_or_re_call_the_llm_when_reopened` | Pass |
| `ST_16` | MCQ scoring in a mixed quiz remains deterministic at 100 percent when all MCQs are correct | `test_ST_16_mcq_score_stays_100_percent_and_the_total_is_a_clear_combination`, `test_ST_16_without_an_llm_the_mcq_part_is_the_same`, `test_the_objective_part_is_identical_whatever_the_llm_says[0]`, `[10]` | Pass |
| `ST_16` | The total score is a clearly explained combination of both parts | `test_ST_16_mcq_score_stays_100_percent_and_the_total_is_a_clear_combination`, `test_before_evaluation_the_written_answer_is_pending_and_counts_zero`, `test_an_objective_only_quiz_explains_its_total_without_a_descriptive_part`, `test_the_evaluation_is_scaled_to_the_questions_points` | Pass |
| — (NFR-06) | The LLM module cannot write an objective answer; objective rows are unchanged by evaluation | `test_the_llm_module_cannot_write_an_objective_answer`, `test_ST_16_mcq_score_stays_100_percent_and_the_total_is_a_clear_combination` | Pass |
| — | Blank written answers are unattempted and never sent; LLM failures fall back and are audited; unevaluable answers stay pending with the reason | `test_a_blank_written_answer_is_unattempted_and_never_sent`, `test_a_failing_llm_falls_back_and_the_result_is_still_stored`, `test_an_answer_that_cannot_be_evaluated_stays_pending_with_the_reason` | Pass |
| — | Only the owner of a submitted attempt can evaluate it | `test_a_running_attempt_cannot_be_evaluated`, `test_only_the_owner_can_evaluate_an_attempt` | Pass |

Manually verified on a migrated, seeded SQLite database with `LLM_PROVIDER=none`: the
seeded student took "CPU scheduling basics" with both objective answers correct and a
written answer covering three of four concepts. Submitting showed `2/3 (66.7%)` with
"1 descriptive answer is awaiting evaluation"; `POST /attempts/2/evaluate` scored it
`7.5/10` (missing: starvation), and My Quiz History then showed `2.75/3 (91.7%)` as
"objective 2/2 + descriptive 0.75/1", with the feedback inside the opened attempt.

No schema change: the evaluation is stored in `attempt_answers.feedback` (JSON, from
migration `0001`) and its scaled points in `attempt_answers.score`. New route
`POST /api/v1/attempts/{attempt_id}/evaluate`; attempt results and history entries gain
`breakdown`. `docs/api/openapi-v1.json` is regenerated.

## S09.1 (#34) — Ask-AI explanations and topic quizzes

Executed 2026-10-23 with `python -m pytest tests/test_ask_ai.py`. The LLM is the
deterministic `FakeProvider` (wrapped as `SpyProvider` to record calls) or a scripted
fake; nothing calls a real model.

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `UT_17` | A concept query returns a relevant simplified explanation with at least one example | `test_UT_17_explain_b_trees_returns_a_simple_explanation_with_an_example`, `test_an_answer_without_an_example_is_requested_again`, `test_an_off_topic_or_unusable_answer_is_never_shown`, `test_the_concept_is_taken_from_the_query` | Pass |
| `UT_18` | A blank query shows "Please enter a question" and sends nothing to the LLM | `test_UT_18_blank_query_shows_please_enter_a_question_and_sends_nothing` (3 variants), `test_UT_18_blank_query_is_refused_even_with_no_llm_configured`, `test_the_service_refuses_a_blank_query_before_recording_it` | Pass |
| `ST_18` | Consecutive distinct queries return distinct, topic-relevant responses within the acceptable response time | `test_ST_18_consecutive_queries_are_relevant_distinct_and_timely`, `test_relevance_check_matches_topic_words` | Pass |
| `IT_17` | A natural-language quiz request generates a quiz on that topic and offers it to start | `test_IT_17_quiz_request_generates_a_bst_quiz_offered_to_start`, `test_the_topic_quiz_can_be_answered_and_scored`, `test_starting_again_resumes_the_running_attempt`, `test_quiz_options_and_counts_in_the_query_are_used`, `test_the_quiz_topic_is_taken_from_the_request` | Pass |
| — | The generated quiz respects the review gate where it would enter the bank | `test_generated_quiz_questions_stay_out_of_the_bank_until_approved`, `test_the_topic_quiz_cannot_be_published_while_questions_are_unapproved`, `test_other_users_cannot_attempt_the_private_topic_quiz` | Pass |
| — (NFR-06) | Provider failures, timeouts and no-LLM are clear errors and are audited; prompts fence the user's text | `test_provider_failures_and_timeouts_give_clear_errors_and_are_recorded`, `test_with_no_llm_configured_ask_ai_says_so`, `test_a_failed_quiz_generation_is_a_clear_error_and_creates_nothing`, `test_the_prompt_asks_for_an_example_and_fences_the_users_text` | Pass |

Manually verified on a migrated, seeded SQLite database with `LLM_PROVIDER=fake`: the
seeded student asked "Explain B-Trees with an example" (explanation plus a node-split
example), a blank query returned "Please enter a question", and "Give me a quiz on
Binary Search Trees" produced a 5-question private quiz that `POST /ai/ask/2/start`
started, while a bank search for the topic stayed empty until approval.

No schema change: the `explain` and `generate_quiz` values of `llm_task` exist since
migration `0001`. New routes `POST /api/v1/ai/ask` and `POST /api/v1/ai/ask/{ask_id}/start`;
`docs/api/openapi-v1.json` is regenerated.

## S09.2 (#35) — Channel-scoped AI blending peer and generated questions

Test Plan cases are automated in `tests/test_channel_ai.py` under their case IDs, run
against `POST /api/v1/ai/ask` with `channel_id` (the UI's "Ask AI in this channel") and
the deterministic fake provider. ST_17's precondition (5 peer DBMS questions in the
channel bank) is set up through the questions service.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `IT_18` | A channel-scoped request returns content limited to that channel's topic and bank | `test_IT_18_channel_quiz_uses_the_channels_topic_and_bank`, `test_a_named_topic_stays_within_the_channels_subject`, `test_channel_explanations_are_scoped_to_the_channels_subject`, `test_ask_ai_in_a_channel_needs_membership` | Pass |
| `IT_18` | Unrelated subjects do not appear in channel-scoped output | `test_IT_18_channel_quiz_uses_the_channels_topic_and_bank`, `test_unrelated_subjects_never_appear_in_channel_output` | Pass |
| `ST_17` | A ten-question request over a five-question bank returns those five peer questions plus five generated ones | `test_ST_17_ten_questions_over_a_five_question_bank_blend_peer_and_generated`, `test_a_full_bank_needs_no_generation`, `test_an_empty_bank_is_filled_with_generated_questions`, `test_a_bank_that_fills_the_quiz_works_without_an_llm` | Pass |
| `ST_17` | Every question is scoped to the channel's subject | `test_ST_17_ten_questions_over_a_five_question_bank_blend_peer_and_generated`, `test_unrelated_subjects_never_appear_in_channel_output` | Pass |
| — | Peer and generated questions are distinguishable to the reviewer | `test_peer_and_generated_questions_are_distinguishable`, `test_generated_drafts_stay_out_of_the_bank_until_approved`, `test_students_generated_drafts_never_join_an_authorized_channel_bank` | Pass |

No schema change: `llm_requests.channel_id` already exists. Manually verified on a
migrated, seeded SQLite database with `LLM_PROVIDER=fake`: the seeded student's
10-question channel quiz is all DBMS, starts with `POST /ai/ask/{id}/start`, and a
channel explanation succeeds.

## S10.1 (#36) — Channel messaging with persistence and history

Test Plan cases are automated in `tests/test_channel_chat.py` under their case IDs,
run against the `/api/v1/channels/{id}/messages` endpoints the UI will call. ST_19's
"log out and back in" is a fresh login token; ST_20 seeds 520 messages and pages back
through them 100 at a time with `before`.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_19` | A sent message appears with the correct sender and timestamp for all members | `test_UT_19_a_member_sends_a_message_all_members_see_with_sender_and_time`, `test_a_sent_message_carries_the_server_sender_not_one_from_the_client` | Pass |
| `UT_20` | An empty message cannot be sent; an error is shown | `test_UT_20_an_empty_message_is_refused_and_nothing_is_posted`, `test_a_message_is_stored_trimmed_and_too_long_ones_are_refused` | Pass |
| `ST_19` | Messages persist and reload in the correct order | `test_ST_19_messages_keep_their_order_and_details_after_logout_and_login`, `test_messages_persist_and_reload_in_the_order_they_were_sent` | Pass |
| `ST_20` | A large history loads progressively without missing messages | `test_ST_20_scrolling_back_through_500_messages_loads_every_one_in_order`, `test_opening_the_chat_shows_the_newest_messages_oldest_first`, `test_polling_after_the_newest_message_returns_only_new_ones`, `test_before_and_after_cannot_be_combined` | Pass |
| — | The chat is for channel members only, checked on every request | `test_only_members_read_or_send_in_the_chat`, `test_access_follows_membership_on_every_request`, `test_the_chat_needs_a_signed_in_user`, `test_a_missing_or_hidden_channel_is_not_found` | Pass |
| — | Other channels' and deleted messages never appear | `test_messages_from_other_channels_never_appear`, `test_deleted_messages_are_not_shown` | Pass |

No schema change: messages are rows in `channel_messages` (already in the model since
S00.2). The chat module now depends on accounts and channels; no module depends on
chat (`tests/test_module_boundaries.py`).
Manually verified on a migrated, seeded SQLite database: the seeded student reads the
seeded message, sends one that comes back with their name and time, a blank one is
refused with `Message cannot be empty`, the admin (not a member) is refused with
`Join the channel to view this chat`, and paging with `limit=1` then `before` walks
back through both messages in order.

## S13.1 (#42) — Reputation, badges and contributor leaderboard

Test Plan cases are automated in `tests/test_reputation.py` under their case IDs, run
against the endpoints the UI uses (`PUT /materials/{id}/review`,
`PUT /questions/{id}/rating`, `GET /profiles/me`, `GET /leaderboard`). Defaults: a
rating of 4–5 stars earns 5 points; "Top Contributor" is awarded at 100.

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `UT_25` | A positive peer rating increases the contributor's reputation by the configured amount | `test_UT_25_positive_peer_rating_adds_the_configured_points` (100 → 105), `test_positive_question_rating_also_increases_reputation`, `test_points_follow_the_configured_amount_and_threshold`, `test_low_ratings_do_not_add_reputation`, `test_rating_again_counts_once_and_lowering_it_withdraws_the_points` | Pass |
| — | Reputation events are recorded with their source for auditability | `test_reputation_events_record_their_source` | Pass |
| — | Reputation does not change on a self-rating | `test_self_rating_does_not_change_reputation` | Pass |
| `ST_26` | Crossing a reputation threshold awards the corresponding badge | `test_ST_26_reaching_the_threshold_through_ratings_awards_top_contributor` (90 + 3 × 5 → "Top Contributor"), `test_crossing_each_threshold_awards_its_badge_once`, `test_badges_are_kept_when_reputation_later_drops` | Pass |
| `UT_26` | The leaderboard order matches current reputation totals | `test_UT_26_leaderboard_reflects_updated_reputation_totals`, `test_leaderboard_order_matches_current_totals_with_shared_ranks`, `test_leaderboard_pages_and_shows_the_callers_rank`, `test_leaderboard_lists_badges_and_needs_sign_in`, `tests/test_web_ui.py::test_S13_1_leaderboard_screen_in_the_browser` | Pass |
| `ST_26` | Badge awards appear on the user's profile | `test_ST_26_reaching_the_threshold_through_ratings_awards_top_contributor`, `test_badge_awards_appear_on_the_users_profile` (own and public profile, `/badges` catalogue) | Pass |

No schema change: `reputation_events`, `badges` and `user_badges` exist since migration
`0001`, and badge rows are created on first award. New routes `GET /api/v1/leaderboard`,
`GET /api/v1/reputation/me` and `GET /api/v1/badges`; `docs/api/openapi-v1.json` is
regenerated. The existing ST_02 profile test (50 points, one badge) is unchanged.

## S12.1 (#40) — Performance tracking and analytics dashboard

Test Plan cases are automated in `tests/test_analytics.py` under their case IDs, run
against `GET /api/v1/analytics/dashboard` with attempts taken through the real
`/attempts` endpoints. The charts are checked in a browser by
`tests/test_web_ui.py::test_S12_1_performance_dashboard_in_the_browser` (Playwright +
Chromium against a live, seeded server; skipped where Chromium is not installed).

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| — | Each attempt updates the user's aggregate accuracy and attempt count | `test_each_attempt_updates_the_stored_accuracy_and_attempt_count`, `test_a_later_descriptive_evaluation_replaces_the_attempts_earlier_contribution`, `test_an_attempt_closed_by_its_timer_is_counted` | Pass |
| — | Stored figures reconcile exactly with individual attempt records | `test_stored_figures_reconcile_exactly_with_the_attempt_records`, `test_attempts_the_event_missed_or_that_were_deleted_are_reconciled_on_read`, `test_the_dashboard_repairs_figures_that_drifted_from_the_attempts` | Pass |
| — | Recomputation from raw attempts produces the same aggregates | `test_recomputation_from_raw_attempts_produces_the_same_aggregates` | Pass |
| `UT_23` | The dashboard shows performance grouped by subject and topic with trend over time | `test_UT_23_dashboard_shows_DBMS_at_80_and_OS_at_60_matching_quiz_history`, `test_each_subject_has_its_own_trend_over_time` | Pass |
| `IT_23` | The trend updates as soon as a new attempt is recorded | `test_IT_23_trend_includes_the_new_attempt_and_the_updated_line`, `test_trend_direction_needs_at_least_two_attempts` | Pass |
| — | Analytics are presented as charts or graphs, not raw numbers only | `test_S12_1_performance_dashboard_in_the_browser`, `test_the_performance_page_draws_charts_with_svg_and_calls_the_dashboard` | Pass (browser test run locally with Chromium) |
| — | A new user with no attempts sees a clear empty state | `test_a_new_user_with_no_attempts_sees_a_clear_empty_state`, `test_a_running_attempt_does_not_end_the_empty_state`, `test_S12_1_performance_dashboard_in_the_browser` | Pass |
| `UT_24` | Weak and strong topics are derived from accuracy per topic and labelled as such | `test_UT_24_normalization_at_90_is_a_strength_and_indexing_at_40_is_weak`, `test_a_topic_between_the_thresholds_is_developing`, `test_classification_thresholds` | Pass |
| — | The classification updates as new attempts are recorded | `test_the_classification_updates_as_new_attempts_are_recorded` | Pass |
| — | Topics with too little data are excluded rather than mislabelled | `test_topics_with_too_little_data_are_excluded_not_mislabelled`, `test_classification_thresholds` | Pass |
| — | Each user sees only their own performance; sign-in required | `test_each_user_sees_only_their_own_performance`, `test_the_dashboard_needs_a_signed_in_user` | Pass |

Schema change: migration `0009` adds `performance_attempts` and `performance_topics`
(analytics module). Existing attempts are folded in on the user's first dashboard read.
Analytics now depends on accounts (route guard) and quizzes; quizzes does not depend on
analytics: it publishes `quizzes.attempt_scored` through `app/core/events.py`.
Manually verified on a seeded SQLite database with ten attempts across three subjects:
the Performance page shows the tiles, subject and topic bar charts (topics coloured by
standing, with the 50% and 75% threshold lines), the overall and per-subject trend
charts with the fitted line, and the strengths and weak areas, in light and dark mode.

## S11.1 (#38) — Direct conversations with block and report

Test Plan cases are automated in `tests/test_direct_chat.py` under their case IDs. The
plan's steps run in the web UI; the tests call the endpoints that UI uses.

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `UT_21` | A direct conversation can be started with any user | `test_UT_21_start_a_private_conversation_and_send_a_message`, `test_a_conversation_can_be_started_with_any_user_and_is_reused`, `test_starting_a_conversation_with_yourself_or_nobody_is_refused` | Pass |
| `UT_21` | Messages are delivered in order and none are lost | `test_UT_21_start_a_private_conversation_and_send_a_message`, `test_messages_are_delivered_in_order_and_none_are_lost`, `test_blank_messages_are_refused_and_nothing_is_stored` | Pass |
| `IT_21` | Conversation history reloads correctly on return | `test_IT_21_twenty_plus_messages_load_in_order_before_and_after_re_login`, `test_conversation_history_reloads_correctly_on_return` | Pass |
| `UT_22` | Blocking stops further messages from that user | `test_UT_22_blocked_user_can_no_longer_send_and_a_blocked_indicator_is_shown`, `test_blocking_stops_further_messages_in_both_directions`, `test_blocking_needs_confirmation`, `test_unblocking_lets_messages_through_again` | Pass |
| `UT_22` | The block takes effect immediately and persists | `test_the_block_takes_effect_immediately_and_persists` | Pass |
| `IT_22` | A reported message reaches the moderation queue with its reason | `test_IT_22_report_an_inappropriate_message_for_moderation`, `test_a_reported_message_reaches_the_moderation_queue_with_its_reason`, `test_you_cannot_report_your_own_message_or_one_from_another_conversation` | Pass |
| — | Conversations are private to their two participants | `test_only_participants_can_read_send_or_report` | Pass |

Manually verified on a migrated, seeded SQLite database: the seeded student reopened
the seeded conversation with the faculty user (200), sent "Hey, can you share your quiz
notes?", which the faculty user saw after "Thanks!"; the faculty user reported it as
`inappropriate` ("Report submitted for moderation"); after the student blocked the
faculty user, the faculty user's next message was refused with 403 "You can no longer
send messages to this user" and the student's conversation showed `blocked_by_me`.

No schema change: the chat tables and the `direct_message` report target exist since
migration `0001`. New routes under `/api/v1/conversations` and `/api/v1/blocks`;
`docs/api/openapi-v1.json` is regenerated.

## S13.2 (#43) — Report queue and moderator actions

Test Plan cases are automated in `tests/test_moderation_queue.py` under their case IDs,
run against the endpoints the UI uses (`POST /materials/{id}/reports`,
`POST /questions/{id}/reports`, `/moderation/queue`, `/moderation/reports/...`).

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `IT_25` | A report appears in the moderation queue with content reference, reporter and reason | `test_IT_25_report_reaches_the_moderation_queue`, `test_channel_moderators_see_only_their_channels`, `test_regular_members_have_no_queue` | Pass |
| — | Duplicate reports on the same item are grouped rather than duplicated | `test_duplicate_reports_on_one_item_are_grouped`, `test_one_decision_closes_every_duplicate_report` | Pass |
| — | The reporter sees confirmation that the report was filed | `test_reporter_sees_confirmation_and_follows_the_outcome` | Pass |
| `IT_26` | Removing content hides it from the platform and sets the report status to Resolved | `test_IT_26_remove_content_hides_it_and_resolves_the_report`, `test_removal_needs_confirmation`, `test_dismiss_clears_the_report_and_keeps_the_content`, `test_a_closed_report_cannot_be_decided_again` | Pass |
| `ST_25` | A regular member cannot resolve a report in an Authorized Channel while an authorized user can | `test_ST_25_authorized_user_resolves_where_a_regular_member_cannot` | Pass |
| — | Every moderation action is recorded with actor and timestamp | `test_every_moderation_action_is_logged_with_actor_and_time` | Pass |

Schema change: migration `0010` widens the `moderation_action_type` CHECK constraint
with `dismiss_report` (verified upgrade/downgrade on SQLite and PostgreSQL 16). New
routes under `/api/v1/moderation`; `docs/api/openapi-v1.json` is regenerated.

## S10.2 (#37) — Threaded replies and membership-gated chat access

Test Plan cases are automated in `tests/test_channel_threads.py` under their case IDs,
run against the endpoints the UI calls: `POST /api/v1/channels/{id}/messages` with
`parent_id`, and `GET /api/v1/channels/{id}/messages/{message_id}/replies`.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `IT_19` | A reply is displayed nested under its parent message | `test_IT_19_a_reply_is_threaded_under_its_parent_message`, `test_a_reply_is_displayed_nested_not_in_the_main_stream`, `test_replying_to_a_reply_joins_the_same_thread`, `test_a_long_thread_pages_like_the_main_stream` | Pass |
| `IT_19` | The reply is stored with a link to its parent | `test_the_reply_is_stored_with_a_link_to_its_parent`, `test_a_reply_needs_a_visible_parent_in_the_same_channel`, `test_a_blank_reply_is_refused` | Pass |
| — | Thread reply counts are visible on the parent | `test_thread_reply_counts_are_visible_on_the_parent`, `test_deleted_replies_are_hidden_and_not_counted` | Pass |
| `IT_20` | A non-member navigating directly to the chat is denied with 'Join the channel to view this chat' | `test_IT_20_a_non_member_opening_the_chat_directly_is_denied` (main stream and thread) | Pass |
| `IT_20` | The restriction is enforced server side, not only in the UI (NFR-02) | `test_the_restriction_is_enforced_server_side`, `test_access_to_threads_follows_membership_on_every_request`, and the S10.1 access tests in `tests/test_channel_chat.py` | Pass |

No schema change: `channel_messages.parent_id` exists since migration `0001`. The main
stream now lists top-level messages only, each with `parent_id`, `reply_count` and
`last_reply_at`; `docs/api/openapi-v1.json` is regenerated. All S10.1 tests pass
unchanged.
Manually verified on a migrated, seeded SQLite database: the seeded student replies
"Yes, uploading now!" to the seeded message, the parent then shows `reply_count` 1,
the thread returns the parent and the nested reply, the admin (not a member) gets
`Join the channel to view this chat`, and replying to a missing message is
`Message not found`.

## S12.2 (#41) — Recommendations targeting weak topics

Test Plan cases are automated in `tests/test_recommendations.py` under their case IDs,
run against `GET /api/v1/analytics/recommendations` with attempts taken through the
real `/attempts` endpoints and peer content shared through the materials, questions and
quizzes services. The page is checked in a browser by
`tests/test_web_ui.py::test_S12_2_recommended_for_you_in_the_browser` (skipped where
Chromium is not installed).

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `IT_24` | Recommendations refresh as new performance data arrives (a new weak topic goes to the top) | `test_IT_24_a_30_percent_concurrency_quiz_puts_its_materials_and_quizzes_on_top` | Pass |
| `ST_23` | Recommendations refresh as new performance data arrives (an improved topic gives way to the next weakest) | `test_ST_23_after_two_85_percent_attempts_concurrency_gives_way_to_the_next_weakest` | Pass |
| `ST_24` | Recommended items map to topics identified as weak | `test_ST_24_top_recommendations_are_tagged_with_the_weakest_topic`, `test_every_targeted_item_maps_to_a_weak_topic`, `test_a_developing_topic_is_not_targeted` | Pass |
| — | Already-consumed items are de-prioritized | `test_consumed_items_are_listed_after_fresh_ones_not_hidden`, `test_opening_a_recommendation_moves_it_behind_fresh_items`, `test_a_rated_material_counts_as_consumed` | Pass |
| — | UC-13 alternate flow: no weak areas (or nothing shared for them) gives popular picks | `test_a_new_user_gets_popular_picks_with_a_clear_message`, `test_a_weak_topic_with_nothing_shared_falls_back_to_popular_picks` | Pass |
| — | Only content the user may see, never their own; better-rated items first | `test_channel_content_is_recommended_only_to_its_members`, `test_own_contributions_are_never_recommended`, `test_better_rated_materials_rank_first_within_a_topic`, `test_an_admin_is_scoped_like_anyone_else` | Pass |
| — | Sign-in required; input validated | `test_recommendations_need_a_signed_in_user`, `test_an_unknown_item_type_is_refused` | Pass |
| — | Recommended for You page (UC-13 steps 1, 4, 5) | `test_S12_2_recommended_for_you_in_the_browser`, `test_the_recommended_page_is_linked_and_records_opened_items` | Pass (browser test run locally with Chromium) |

Schema change: migration `0011` adds `recommendation_opens` (analytics module).
Analytics now also calls channels (the user's scope), materials and questions; none of
them reaches chat. The LLM is not used: the UC-13 ranking is deterministic, so it works
with `LLM_PROVIDER=none`.

## S14.1 (#44) — Notifications with user preferences

Test Plan cases are automated in `tests/test_notifications.py` under their case IDs,
run against the endpoints the notification bell and Notification Settings use
(`/api/v1/notifications`). Replies are posted through S10.2's
`POST /api/v1/channels/{id}/messages` with `parent_id`; the notification is raised in
`chat.service.post_channel_message`.

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `UT_27` | A reply or mention generates a notification linking to the relevant message | `test_UT_27_reply_mentioning_the_user_notifies_them_with_a_link`, `test_a_reply_notifies_the_author_of_the_message`, `test_a_mention_notifies_each_named_member_once`, `test_mentions_reach_channel_members_only` | Pass |
| — | Notifications show an unread count and can be marked read | `test_unread_count_and_mark_read`, `test_someone_elses_notification_cannot_be_marked` | Pass |
| — | A notification is generated for relevant quiz and channel updates | `test_quiz_and_channel_updates_generate_notifications` | Pass |
| `UT_28` | Disabling a category stops those notifications being generated | `test_UT_28_disabled_quiz_updates_stop_while_other_categories_continue`, `test_unknown_category_is_rejected` | Pass |
| `UT_28` | Other categories continue to arrive unaffected | `test_UT_28_disabled_quiz_updates_stop_while_other_categories_continue`, `test_disabling_a_category_stops_only_that_category`, `test_a_reply_that_mentions_still_arrives_when_replies_are_off` | Pass |
| — | Preferences persist across sessions | `test_preferences_persist_across_sessions` | Pass |

No schema change: `notifications` and `notification_preferences` exist since migration
`0001`. New routes under `/api/v1/notifications`; `docs/api/openapi-v1.json` is
regenerated.

## UI-2 (#63) — Web UI for materials, question bank and quizzes

Executed 2026-10-27. The screens are in `app/web/static/study.js`. Every acceptance
criterion is walked in a real browser (Playwright + Chromium) against a live, seeded
server by `tests/test_web_ui.py::test_UI_2_acceptance_criteria_in_the_browser`, which
runs the Test Plan's system-level steps for these flows through the pages and asserts
the Test Plan wording, which comes from the API. Like the UI-1 browser test it runs
locally and is skipped in CI, which has no browser; the contract tests run everywhere.
For ST_11 the test moves the running attempt's server-side time limit to three seconds
away instead of waiting a full minute; the page then counts down and the server closes
the attempt.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_07`, `UT_08` | Upload Material collects file or link, subject, topic, semester and material type; a blank subject shows "Subject is required" | browser test, upload steps (tags shown in the listing) | Pass (local) |
| `IT_07`, `IT_08` | Materials are searched and filtered by subject and topic, previewed and downloaded | browser test, search (both filters, empty state), View (PDF preview frame) and Download (original name and bytes) steps | Pass (local) |
| `ST_07`, `ST_08` | A material is rated with stars and a review, marked helpful, and reported with a reason | browser test, peer steps: average rating and helpful count update, "Report submitted for moderation"; missing rating and reason messages | Pass (local) |
| `UT_09`, `UT_10`, `IT_09` | Add Question collects text, options, correct answer, subject, topic, difficulty and type; none chosen shows "Please select the correct answer"; questions are searched, rated and reported | browser test, Add Question, keyword search, rate and report steps | Pass (local) |
| `IT_10`, `ST_10`, `ST_09` | Create Quiz takes topic, difficulty, number of questions and duration (newly added questions selectable), and the quiz is published to a channel | browser test, Create Quiz (too-few-questions message, preview, save) and Publish to "DBMS study group" steps; the member then finds it in the channel's quizzes | Pass (local) |
| `UT_12`, `ST_11` | The attempt screen shows a countdown timer, warns about unanswered questions and auto-submits when the timer expires | browser test, "1 question is unanswered" warning and confirm, then the timed-out attempt auto-submitted with only the saved answer scored | Pass (local) |
| `UT_11`, `IT_11` | The result screen shows the score immediately, and My Quiz History lists past attempts | browser test, "2/2 (100%)" on submit, history with all three attempts and their per-question breakdown | Pass (local) |
| — | Every endpoint the screens call exists; the screens are in the navigation | `test_UI_2_screens_call_the_study_workflow_endpoints`, `test_every_endpoint_the_ui_calls_exists`, `test_UI_2_screens_are_linked_from_the_navigation` | Pass |
| — | Server text never parsed as HTML; previews limited to blob images and frames | `test_server_text_is_never_inserted_as_html` (now covers `study.js`), `test_material_preview_is_allowed_only_as_blob_images_and_frames` | Pass |
| — | Usable at phone width (no horizontal scroll at 375 px) | browser test, phone step on all four screens | Pass (local) |

No API or schema change: `docs/api/openapi-v1.json` is unchanged.

## S14.2 (#45) — Admin management of users, channels and content

Test Plan cases are automated in `tests/test_admin.py` under their case IDs, run against
the endpoints the admin screens call (`/api/v1/admin/...`, the channel approve/reject
routes and `/api/v1/notifications`).

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `IT_27` | Suspending sets the account status to Suspended and prevents login | `test_IT_27_admin_suspends_a_user_who_can_then_no_longer_log_in` (`spam_user01`; earlier session also refused), `test_suspending_sets_the_status_and_prevents_login`, `test_suspension_needs_confirmation_and_a_valid_target` | Pass |
| — | Suspension is reversible by an admin | `test_suspension_is_reversible_by_an_admin` | Pass |
| `IT_28` | A non-admin navigating to the admin dashboard is denied as Unauthorized and the dashboard does not render | `test_IT_28_a_student_opening_the_admin_dashboard_is_denied`, `test_every_admin_route_refuses_non_admins_on_the_server` (8 routes × student, authorized user, anonymous), `test_a_refused_admin_action_changes_nothing`, `test_the_dashboard_counts_the_work_waiting` | Pass |
| `ST_27` | Approving sets the channel status to Approved and makes it visible platform-wide as Authorized | `test_ST_27_admin_approves_a_pending_authorized_channel` ("Official CS301") | Pass |
| — | Rejecting keeps the channel out of the Authorized listing | `test_rejecting_keeps_the_channel_out_of_the_authorized_listing`, `test_a_decided_request_cannot_be_decided_again_or_logged_twice` | Pass |
| `ST_27` | Pending requests are listed for the admin with their requester | `test_pending_requests_are_listed_with_their_requester` | Pass |
| `ST_28` | Removed content is no longer visible on the platform | `test_ST_28_removing_reported_content_notifies_the_poster_and_is_logged`, `test_removed_content_is_no_longer_visible_on_the_platform`, `test_a_question_can_be_removed_and_its_author_notified`, `test_removal_needs_a_reason_and_existing_content` | Pass |
| `ST_28` | The original poster receives a notification explaining the removal | `test_ST_28_removing_reported_content_notifies_the_poster_and_is_logged`, `test_notifications_are_private_and_can_be_marked_read`, `test_channel_decisions_are_logged_and_the_requester_is_told` | Pass |
| `ST_28` | The action is written to the admin audit log with actor, target and timestamp | `test_the_audit_log_records_actor_target_and_time_newest_first`, `test_suspend_and_reinstate_are_written_to_the_audit_log`, `test_channel_decisions_are_logged_and_the_requester_is_told` | Pass |

No schema change: `users.is_suspended`, `reports`, `moderation_actions` and
`notifications` exist since migration `0001`. New module `admin` (no tables); new
capability `admin:dashboard`; channels may now call moderation and notifications
(`docs/architecture.md`). The notifications inbox routes come from S14.1. `docs/api/openapi-v1.json` is
regenerated.

Manually verified on a migrated, seeded SQLite database: the student gets
`Unauthorized` from `/admin/dashboard`; the faculty user's "Official CS301" request is
listed with its requester and approved; a reported material appears in the review queue,
is removed (404 afterwards) and its uploader's inbox holds the explanation; the student
is suspended (login 401) and reinstated; the audit log lists all four actions with the
admin as actor.

## S11.2 (#39) — In-chat sharing of materials, questions and quizzes

Test Plan cases are automated in `tests/test_in_chat_sharing.py` under their case IDs,
run against the endpoints the chat screens call (`/api/v1/conversations/{id}/messages`
and `/api/v1/channels/{id}/messages`), then following the card's `api_path`.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `ST_21` | A shared resource renders as a preview card; opening it navigates to the original | `test_ST_21_shared_material_stays_linked_and_opens_the_original[direct]`, `[channel]` ("OS_Scheduling_Notes.pdf") | Pass |
| `ST_22` | Opening a shared quiz goes to its start screen and it can be attempted | `test_ST_22_shared_quiz_opens_the_start_screen_and_can_be_attempted` | Pass |
| — | A shared resource renders as a preview card in the chat | `test_a_shared_resource_renders_as_a_preview_card`, `test_sharing_with_a_comment_keeps_both`, `test_a_share_can_be_a_thread_reply`, `test_the_conversation_list_shows_a_shared_last_message` | Pass |
| — | Opening it navigates to the original content | `test_opening_the_card_navigates_to_the_original_content` (material, question, quiz) | Pass |
| — | The link keeps working after the content is edited | `test_the_link_keeps_working_after_the_content_is_edited`, `test_the_message_stores_a_reference_not_a_copy` | Pass |
| — | Access and safety rules | `test_a_removed_resource_shows_an_unavailable_card`, `test_the_card_respects_what_the_reader_may_open`, `test_you_cannot_share_what_does_not_exist[...]`, `test_you_cannot_share_what_you_may_not_open`, `test_an_adaptive_run_cannot_be_shared`, `test_a_question_card_never_shows_the_answer_key`, `test_a_blank_message_without_a_share_is_still_refused`, `test_an_unknown_resource_type_is_refused`, `test_blocking_still_stops_shares` | Pass |

No schema change: `shared_resource_type` / `shared_resource_id` exist on
`channel_messages` and `direct_messages` since migration `0001`. Chat may now call
materials, questions and quizzes (`docs/architecture.md`); quizzes gains
`describe_quiz`. `docs/api/openapi-v1.json` is regenerated. The chat screens that
render the cards are UI-3 (#64).

## NFR-02 (#47) — Security: credentials and role-based authorization

Automated in `tests/test_security.py`. The authorization matrix is built from the
published API contract and the route guards, not a hand-kept list, so routes added later
are covered automatically: 107 operations, of which 102 require a token, each called
anonymously and with nine kinds of bad token (1,020 refusals), plus every role-guarded
route called by every role that lacks its capability. The Test Plan cases the NFR maps
to (IT_28, UT_06, IT_20) run here under their IDs, alongside their feature tests.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| — | Credentials are stored hashed using an accepted algorithm, never plain text | `test_registered_password_is_stored_as_a_salted_scrypt_hash`, `test_the_same_password_never_produces_the_same_stored_value`, `test_no_stored_credential_is_plain_text`, `test_a_tampered_or_foreign_hash_never_verifies` | Pass |
| — | Credentials never leave the server | `test_no_response_schema_can_carry_a_password_or_its_hash`, `test_account_responses_never_include_the_password_or_hash`, `test_a_wrong_password_is_refused_without_saying_which_part_was_wrong` | Pass |
| — | Every tested unauthorized access attempt is denied (no or bad credentials) | `test_every_operation_outside_the_public_list_requires_a_token`, `test_every_protected_operation_refuses_every_bad_credential` (no header, empty, wrong scheme, malformed, foreign key, edited role claim, `alg: none`, expired, suspended, deleted), `test_a_refused_request_does_not_reach_the_data` | Pass |
| `IT_28` | Every tested unauthorized access attempt is denied (insufficient role) | `test_every_role_guarded_route_refuses_each_role_without_the_capability`, `test_roles_with_the_capability_pass_the_guard`, `test_IT_28_non_admin_roles_are_denied_every_admin_route` (12 admin-only routes × student, authorized user), `test_a_role_change_takes_effect_on_the_next_request` | Pass |
| `UT_06` | A user can modify only resources they have permission for (official content) | `test_UT_06_a_regular_member_cannot_manage_official_content` ("Insufficient permissions") | Pass |
| `IT_20` | A user can access only resources they have permission for (channel chat) | `test_IT_20_a_non_member_is_denied_the_channel_chat` | Pass |
| — | A user can modify only resources they have permission for (other users' data) | `test_another_users_material_cannot_be_edited_or_removed`, `test_another_users_channel_posts_and_members_cannot_be_changed`, `test_another_users_quiz_draft_and_attempt_cannot_be_touched`, `test_another_users_ai_work_cannot_be_reviewed_or_continued`, `test_another_users_conversation_and_notifications_stay_private`, `test_reports_and_the_moderation_queue_are_limited_to_who_may_see_them`, `test_profile_changes_apply_only_to_the_caller` | Pass |

No schema or route change; `docs/api/openapi-v1.json` is unchanged. The security rules
are written up in `docs/architecture.md` ("Security (NFR-02)").

## NFR-06 (#49) — LLM reliability: review gate, reporting and deterministic scoring

Automated in `tests/test_llm_reliability.py`, with the offline fake provider or a
scripted one. The NFR's verification cases (UT_14, IT_13, ST_16) run there under their
IDs across every path LLM output can take, alongside their feature tests in
`tests/test_llm_generation.py` and `tests/test_descriptive_results.py`; the scoring
code-path review is kept as an automated check.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_14` | LLM-generated questions require review and approval before publication | `test_UT_14_generated_questions_reach_no_bank_or_quiz_before_review`, `test_UT_14_an_ask_ai_topic_quiz_cannot_be_published_until_its_questions_are_approved`, `test_UT_14_channel_ask_ai_questions_stay_out_of_the_channel_bank_until_approved`; `tests/test_llm_generation.py::test_UT_14_add_to_quiz_is_unavailable_until_every_question_is_reviewed` | Pass |
| `IT_13` | Only reviewed questions are published; edited ones keep the correction, rejected ones never are | `test_IT_13_only_approved_questions_are_published_and_rejected_ones_never_are`; `tests/test_llm_generation.py::test_IT_13_a_flawed_question_can_be_edited_and_saved_with_the_correction`, `test_IT_13_a_rejected_question_is_discarded_and_never_reaches_bank_or_quiz` | Pass |
| — | Reports on AI-generated content are captured | `test_a_report_on_an_ai_explanation_is_captured_and_reaches_the_moderation_queue`, `test_a_generated_batch_and_a_topic_quiz_can_be_reported`, `test_an_llm_evaluation_can_be_reported_but_concept_matching_has_nothing_to_report`, `test_reporting_the_same_ai_response_twice_returns_the_open_report`, `test_only_the_person_the_ai_answered_can_report_it` | Pass |
| — | Reports on AI-generated content are routed to moderation | `test_an_ai_report_in_a_channel_is_routed_to_that_channels_moderators`, `test_an_ai_report_outside_any_channel_goes_to_admins_only`, `test_upholding_a_report_withdraws_the_ai_output`, `test_a_dismissed_ai_report_keeps_the_output`, `test_a_reported_ai_question_in_the_bank_is_marked_ai_generated` | Pass |
| `ST_16` | Objective questions are scored deterministically, never by the LLM alone | `test_ST_16_objective_answers_are_scored_with_the_llm_unreachable`, `test_ST_16_an_llm_verdict_never_changes_the_objective_part`, `test_the_scoring_code_path_cannot_reach_the_llm_module`; `tests/test_descriptive_results.py::test_ST_16_*`, `tests/test_quiz_attempts.py::test_objective_scoring_is_deterministic_and_never_uses_the_llm` | Pass |

New route `POST /api/v1/ai/responses/{id}/reports`; descriptive evaluations now return
`ai_response_id`, and moderation queue entries carry `content.ai_generated` and
`content.excerpt`. Migration `0012` adds `ai_response` to the report and action target
CHECK constraints and `llm_requests.withdrawn`; it upgrades and downgrades cleanly and
matches the models (`tests/test_migrations.py`). llm may now call moderation and
moderation_queue may call llm (`docs/architecture.md`, "LLM reliability (NFR-06)").
`docs/api/openapi-v1.json` is regenerated.

## NFR-04 (#48) — Reliability: never lose a committed attempt or upload

Verified by fault injection in `tests/test_reliability.py`, as the NFR specifies: a
child process running the real app over a file database is killed (`os._exit`) after
its writes are flushed but before they commit, mid-submission and mid-upload, and
transient database failures are injected part-way through requests. The database is
then reopened from a new engine, as a restarted server would see it. The NFR maps to no
Test Plan case ID. The earlier submission fault-injection tests are in
`tests/test_quiz_history.py`.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| — | No committed quiz attempt or uploaded material is lost during a simulated transient failure | `test_killing_the_service_mid_submission_loses_no_committed_attempt`, `test_killing_the_service_mid_upload_leaves_no_partial_material`, `test_committed_attempts_and_uploads_survive_a_restart`, `test_a_transient_failure_mid_upload_saves_nothing_and_can_be_retried[while storing the file]`, `[at commit]`, `test_a_failing_reaction_never_costs_the_user_their_submission`; `tests/test_quiz_history.py::test_NFR_04_*` | Pass |
| — | The system recovers to a consistent state after the failure | Same tests: nothing half-written after the kill (no half-scored answers, no material without its file, analytics rows only for committed attempts, SQLite `integrity_check` ok) and the same request succeeds on retry; `test_a_database_failure_inside_a_reaction_still_rolls_the_request_back`, `test_a_damaged_file_is_refused_rather_than_served`, `test_migration_0013_backfills_checksums_for_existing_files` | Pass |
| — | Uploads and attempt writes are transactional | `test_an_upload_is_one_transaction`, `test_a_submission_is_one_transaction` (one commit each, analytics figures included), `test_handlers_are_isolated_from_each_other` | Pass |

Event handlers now run in savepoints (`app/core/events.py`), so a failing subscriber
cannot fail the submission or upload that triggered it; a database failure inside one
still rolls the request back with a 503. Material files keep a SHA-256 checksum
(migration `0013`, backfilled; upgrades, downgrades and matches the models), returned as
`file.sha256` and checked on every preview and download. `docs/api/openapi-v1.json` is
regenerated. Rules are written up in `docs/architecture.md` ("Reliability (NFR-04)").

## NFR-03 (#50) — Usability: intuitive navigation and visual analytics

Executed 2026-10-28. The Test Plan has no case of its own for NFR-03; `IT_01` expects a
signed-in user to land on a dashboard, which this story adds (`#/dashboard`, see
`app/web/README.md`). The browser walk-through runs a brand-new user through every core
flow from the dashboard in a real browser (Playwright + Chromium) against a live, seeded
server, counting clicks. Like the other browser tests it runs locally and is skipped in
CI, which has no browser; the static and API tests run everywhere.

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| `IT_01` | Log in lands on the dashboard | `test_signed_in_users_land_on_the_dashboard`; browser test, log-in step (Dashboard heading, Dashboard active in the nav) | Pass |
| — | A new user can find a channel, upload material and attempt a quiz unaided | `test_NFR_03_new_user_walkthrough_in_the_browser`: the Get started checklist goes 0 → 1 → 2 of 3 and disappears; join, upload and a submitted quiz with its result; `test_the_dashboard_endpoints_give_a_new_user_a_clean_start` | Pass (local) |
| — | Analytics are presented as charts or graphs rather than raw numbers only | `test_the_dashboard_shows_progress_as_charts`; browser test, dashboard progress (subject bars and score trend as SVG `role=img`) and Performance | Pass (local) |
| — | Core flows are reachable within three clicks from the dashboard | `test_every_core_flow_starts_from_a_dashboard_link`; browser test counts: join a channel 2, upload form 1 (upload 2), start a quiz 3, Performance 1 | Pass (local) |
| — | The dashboard fits a phone (no horizontal scroll at 375 px) | browser test, phone step | Pass (local) |

Moderated walkthrough with first-time users (the issue's "How it is verified") is for
the team to run on the deployed app; this log covers the automated part.

No API or schema change: `docs/api/openapi-v1.json` is unchanged. The UI-1, S12.1, S12.2,
S13.1 and UI-2 browser tests now expect the dashboard after log in.

## UI-3 (#64) — Web UI for chat, AI help, analytics, notifications, moderation and admin

Executed 2026-10-29. The screens are in `app/web/static/community.js` (plus small hooks
in `app.js`: the chat on the channel page, the Recommended for You panel on the
Performance page, capability-gated nav links and the bell). Every acceptance criterion
is walked in a real browser (Playwright + Chromium) against a live, seeded server by
`tests/test_web_ui_community.py::test_UI_3_acceptance_criteria_in_the_browser`, with
three signed-in users at once (student, faculty, a newly registered `spam_user01`) and
then the admin, using the offline `fake` LLM provider. Like the UI-1/UI-2 browser tests
it runs locally and is skipped in CI, which has no browser; the contract tests run
everywhere.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| `UT_19`, `UT_20` | The channel chat shows messages with sender and time and lets a member send | browser test: Send disabled while the box is blank or spaces; the sent message shows "Sample Student" and a time, and another member sees it with the same sender | Pass (local) |
| `IT_19` | A member replies in a thread | browser test: Reply → "Yes, uploading now!" nested under the parent, attributed to "Sample Faculty"; the parent shows "1 reply" | Pass (local) |
| `IT_20` | A non-member is refused with "Join the channel to view this chat" | browser test: `spam_user01` opens the channel URL; the chat shows the API's message and no composer | Pass (local) |
| `UT_21` | Direct Messages: start a conversation and read its history | browser test: member list → profile → **Message** → message sent; the other user opens it from Messages | Pass (local) |
| `UT_22` | Block a user; the blocked indicator is shown | browser test: Block User (confirmed) → "You have blocked …", composer hidden, Unblock offered; the other side sees "Blocked" and cannot send | Pass (local) |
| `IT_22` | Report a message with a reason | browser test: no reason → "Select a reason for reporting this message"; "Inappropriate content" → report filed | Pass (local) |
| `UT_18` | Ask AI shows "Please enter a question" for an empty query | browser test: the message is shown and no `/ai/ask` request is sent; `test_UI_3_blank_question_is_stopped_in_the_page_with_the_test_plan_wording` | Pass |
| `UT_17`, `IT_17` | Ask AI explains a concept and offers a topic quiz to start | browser test: "Explain B-Trees with an example" → explanation with examples; "Give me a quiz on Binary Search Trees" → Start quiz opens the timed attempt | Pass (local) |
| — | A review screen lets the user approve, edit or reject each generated question before it is added | browser test: Generate (DBMS › Indexing, 5) → approve one, reject one, edit one, approve the rest → Create quiz | Pass (local) |
| `UT_23`, `UT_24` | Performance shows subject and topic charts with strong/weak topics, an invitation to attempt a quiz when there is no history, and Recommended for You | browser test: charts (S12.1) and the Recommended for You panel for the student; the empty state "… Attempt a quiz to see your performance here." with the panel for a new user | Pass (local) |
| `UT_27` | The bell lists replies and mentions | browser test: the bell shows 1 unread; the reply notification opens the channel at that message with its thread open, and the count clears | Pass (local) |
| `UT_28` | A category can be switched off | browser test: Notification settings → Quiz updates off; the API then reports `quiz_update` off and replies still on | Pass (local) |
| `IT_25`, `IT_26` | Moderation lists pending reports with content, reporter and reason; a moderator removes content or dismisses | browser test: the reported material shows "Sample Faculty — Inappropriate content"; Remove Content → "Status: resolved". A student without channels to moderate sees "Only moderators can review reports" and no nav link | Pass (local) |
| `IT_27` | Admin suspends a user | browser test: User management → `spam_user01` → Suspend (confirmed) → "Suspended"; that user's login is then refused | Pass (local) |
| `ST_27` | Admin approves a pending Authorized Channel request | browser test: "Official CS301" → Approve → "is now approved"; it is then listed platform-wide as an Authorized Channel | Pass (local) |
| `IT_28` | Anyone else sees "Unauthorized" | browser test: a student opening `#/admin` sees "Unauthorized", no dashboard, and no Admin nav link | Pass (local) |
| — | Every endpoint the screens call exists; the screens are linked | `test_every_endpoint_the_ui_calls_exists` (now covers `community.js`), `test_UI_3_pages_are_registered_and_linked` | Pass |
| — | Server text never parsed as HTML; files served with the CSP | `test_server_text_is_never_inserted_as_html`, `test_ui_files_are_served_with_a_strict_content_security_policy` (both now cover `community.js`) | Pass |
| — | Usable at phone width (no horizontal scroll at 375 px) | browser test, phone step on chat, messages, Ask AI, notifications, moderation and admin | Pass (local) |

No API or schema change: `docs/api/openapi-v1.json` is unchanged.

## NFR-05 (#51) — Scalability: hold performance as users and content grow

Executed 2026-10-29. The Test Plan has no case of its own for NFR-05; the issue's "How
it is verified" (seed a large dataset, re-run the performance suite, review schema
indexes) is automated in `tests/test_scalability.py`. `scripts/seed_scale.py` seeds the
tested range: factor 1 (50 users, 10 channels, 500 materials, 1,100 channel messages,
1,000 direct messages, 1,000 notifications, 200 quiz attempts, 50 reports) to factor 10
(ten times each). Everything runs in CI on in-memory SQLite; the new migration was also
checked on PostgreSQL 16.

| Test case | Acceptance criterion | Test | Result |
| --- | --- | --- | --- |
| — | Performance stays acceptable as users and content volume increase across the tested range | `test_NFR_05_hot_paths_stay_inside_the_target_across_the_tested_range[1]` and `[10]` (28 hot paths, median of 3, each under 1 s), `test_NFR_05_starting_and_submitting_a_quiz_stays_inside_the_target[1]` and `[10]` | Pass |
| — | (same) the work per request does not grow with the data | `test_NFR_05_queries_per_request_do_not_grow_with_the_data` (factor 1 vs 10, same query count on every hot path except recommendations, which follow the user's topics), `test_NFR_05_lists_cost_a_fixed_number_of_queries` | Pass |
| — | Hot query paths are indexed | `test_NFR_05_hot_paths_never_scan_a_growing_table` (every query of every hot path through SQLite's query planner; two intended scans listed with reasons), `test_NFR_05_every_foreign_key_is_indexed`, `test_NFR_05_the_moderation_queue_and_report_lookups_have_their_indexes`; migration `0014` checked by `tests/test_migrations.py` | Pass |
| — | A new domain or feature can be added without redesigning the core architecture | `test_NFR_05_module_dependencies_form_layers_a_new_module_can_sit_on` with the existing `tests/test_module_boundaries.py`; checklist in `docs/architecture.md` ("Adding a domain") | Pass |
| — | The seed covers the tested range | `test_the_scale_seed_covers_the_tested_range` | Pass |

What the suite found at factor 10 before the fixes, and after (time in-process, queries
per request):

| Hot path | Before | After |
| --- | --- | --- |
| Moderation queue (500 open reports) | 316 ms, 1,620 queries | 39 ms, 6 |
| Channel member list (busy channel, 500 members) | 87 ms, 505 | 18 ms, 6 |
| Conversation list (50 conversations) | 77 ms, 352 | 12 ms, 5 |
| Channel directory (100 channels) | 41 ms, 202 | 12 ms, 4 |
| General quiz list (50 quizzes) | 652 queries | 26 ms, 6 |
| Performance dashboard (100 attempts) | one query per attempt on every view | 15 ms, 8 |
| Admin dashboard | loaded every account to count them | 20 ms, 6 (counts in SQL) |

Known limits, written up in `docs/architecture.md`: the admin user list and the quiz
list return every row (a fixed couple of queries, but the payload grows); they need
paging before tens of thousands of accounts or quizzes.

Migration `0014` adds indexes only (no route or response change);

## NFR-01 (#46) — Performance: keep common operations inside response-time targets

Executed 2026-10-30. The Test Plan has no NFR-01 case, so tests are named after the
NFR. Targets, method and full results are in [`performance.md`](performance.md). The
load test (`scripts/load_test.py`) drives 50 concurrent students through 14 page
loads, 4 searches/filters and quiz submission, against SQLite and, in CI,
PostgreSQL 16.

| Test case | Acceptance criterion | Evidence | Result |
| --- | --- | --- | --- |
| — | Targets are written down as explicit numbers before testing begins | `test_NFR_01_targets_are_written_down_as_explicit_numbers` (2 s pages, 2 s search, 3 s quiz submission; p95; 50 users), `test_NFR_01_the_documented_targets_match_the_ones_tested` (`docs/performance.md`) | Pass |
| — | Page loads, quiz submission and search or filter complete within the agreed target under normal load | `test_NFR_01_common_operations_stay_inside_their_targets_under_normal_load[sqlite]` and `[postgresql]`; `test_NFR_01_the_load_covers_dashboard_quiz_submission_and_material_search` | Pass |
| — | No common operation regularly exceeds its target under normal load | The same load test checks the p95 of every operation, not only each category, and fails on any error response. `python -m scripts.load_test` (3 rounds, 2,850 operations): page load p95 0.67 s (SQLite) and 1.11 s (PostgreSQL), search 0.55 s and 0.99 s, quiz submission 1.16 s and 1.51 s | Pass |
| — | A burst larger than the connection pool does not fail requests | `test_NFR_01_a_burst_larger_than_the_pool_is_served_without_errors`, `test_NFR_01_the_server_admits_one_request_per_database_connection`, `test_NFR_01_static_pages_are_not_held_back_by_the_api_limit` | Pass |
| — | Percentile computed correctly | `test_NFR_01_p95_is_the_nearest_rank_percentile` | Pass |

Before the fixes, the same load test failed on `main`. The Quizzes page had a p95 of
5.7 s (`GET /quizzes` ran 531 queries), and all page loads had a p95 of 4.4 s. A
no-pause burst also exhausted the connection pool. No API or schema change:
`docs/api/openapi-v1.json` is unchanged.
