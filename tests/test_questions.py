"""S05.1 - question bank contribution and discovery (issue #24, FR-05).

Test Plan cases are named after their IDs: UT_09, UT_10, IT_09.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.moderation.models import Report, ReportReason, ReportStatus, ReportTarget
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionRating, QuestionSource, QuestionType

API = "/api/v1/questions"
PASSWORD = "Pass@1234"


def make_user(db, username: str, role: Role = Role.STUDENT):
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=username.title(),
        password=PASSWORD,
        role=role,
        username=username,
    )
    db.commit()
    return user


def auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/accounts/login", json={"username": username, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def student(client, db):
    make_user(db, "student_a")
    return auth(client, "student_a")


@pytest.fixture()
def other_student(client, db):
    make_user(db, "student_b")
    return auth(client, "student_b")


@pytest.fixture()
def admin(client, db):
    make_user(db, "admin_a", Role.ADMIN)
    return auth(client, "admin_a")


def mcq(**overrides):
    body = {
        "text": "What is normalization?",
        "subject": "DBMS",
        "topic": "Normalization",
        "difficulty": "easy",
        "question_type": "multiple_choice",
        "options": [
            "Organising data to reduce redundancy",
            "Encrypting a table",
            "Backing up a database",
            "Indexing every column",
        ],
        "correct_answer": "Organising data to reduce redundancy",
        "explanation": "Normalization removes redundancy and update anomalies.",
    }
    return {**body, **overrides}


def contribute(client, headers, **overrides):
    return client.post(API, json=mcq(**overrides), headers=headers)


def new_channel(client, headers, name="DBMS Study Group"):
    response = client.post(
        "/api/v1/channels", json={"name": name, "subject": "DBMS"}, headers=headers
    )
    assert response.status_code == 201
    return response.json()["id"]


# --------------------------------------------------------------------------- #
# UT_09 - a fully specified question is saved with all tags and listed
# --------------------------------------------------------------------------- #


def test_UT_09_question_is_saved_with_all_tags_and_appears_in_the_bank(client, student):
    response = contribute(client, student)

    assert response.status_code == 201
    body = response.json()
    assert body["text"] == "What is normalization?"
    assert body["subject"] == "DBMS"
    assert body["topic"] == "Normalization"
    assert body["difficulty"] == "easy"
    assert body["question_type"] == "multiple_choice"
    assert body["correct_answer"] == "Organising data to reduce redundancy"
    assert body["bank"] == "general"
    assert body["source"] == "peer"
    assert body["status"] == "published"

    listed = client.get(API, headers=student).json()
    assert [q["id"] for q in listed] == [body["id"]]
    assert listed[0]["subject"] == "DBMS" and listed[0]["difficulty"] == "easy"


@pytest.mark.parametrize(
    ("question_type", "extra"),
    [
        ("true_false", {"options": None, "correct_answer": "false"}),
        ("short_answer", {"options": None, "correct_answer": "Third normal form"}),
        ("descriptive", {"options": None, "correct_answer": "A model answer"}),
        ("descriptive", {"options": None, "correct_answer": None, "expected_concepts": ["1NF"]}),
    ],
)
def test_every_question_type_can_be_contributed(client, student, question_type, extra):
    response = contribute(client, student, question_type=question_type, **extra)

    assert response.status_code == 201, response.json()
    assert response.json()["question_type"] == question_type


def test_true_false_answers_are_normalised(client, student):
    body = contribute(
        client, student, question_type="true_false", options=None, correct_answer="false"
    ).json()

    assert body["options"] == ["True", "False"]
    assert body["correct_answer"] == "False"


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("text", "Enter the question text"),
        ("subject", "Select a subject"),
        ("topic", "Select a topic"),
    ],
)
def test_text_and_tags_are_required(client, student, field, message):
    response = contribute(client, student, **{field: "   "})

    assert response.status_code == 422
    assert response.json()["detail"] == message


def test_difficulty_and_type_tags_are_required(client, student):
    body = mcq()
    del body["difficulty"]
    assert client.post(API, json=body, headers=student).status_code == 422

    body = mcq()
    del body["question_type"]
    assert client.post(API, json=body, headers=student).status_code == 422


def test_multiple_choice_needs_two_distinct_options(client, student):
    response = contribute(client, student, options=["Only one"], correct_answer="Only one")

    assert response.status_code == 422
    assert response.json()["detail"] == "Enter at least two different options"


def test_correct_answer_must_be_one_of_the_options(client, student):
    response = contribute(client, student, correct_answer="Something else")

    assert response.status_code == 422
    assert response.json()["detail"] == "The correct answer must be one of the options"


def test_contributing_needs_a_signed_in_user(client):
    assert contribute(client, {}).status_code == 401


# --------------------------------------------------------------------------- #
# UT_10 - submission blocked without a correct answer
# --------------------------------------------------------------------------- #


def test_UT_10_submission_without_a_correct_answer_is_blocked(client, student, db):
    response = contribute(client, student, text="What is a primary key?", correct_answer=None)

    assert response.status_code == 422
    assert response.json()["detail"] == "Please select the correct answer"
    assert client.get(API, headers=student).json() == []


@pytest.mark.parametrize(
    ("question_type", "extra"),
    [
        ("multiple_choice", {"correct_answer": "  "}),
        ("true_false", {"options": None, "correct_answer": None}),
        ("short_answer", {"options": None, "correct_answer": ""}),
        ("descriptive", {"options": None, "correct_answer": None, "expected_concepts": []}),
    ],
)
def test_UT_10_every_question_type_needs_a_correct_answer(client, student, question_type, extra):
    response = contribute(client, student, question_type=question_type, **extra)

    assert response.status_code == 422
    assert response.json()["detail"] == "Please select the correct answer"


# --------------------------------------------------------------------------- #
# Scope - channel bank or general bank
# --------------------------------------------------------------------------- #


def test_question_can_be_scoped_to_a_channel_bank(client, student):
    channel_id = new_channel(client, student)

    body = contribute(client, student, channel_id=channel_id).json()

    assert body["channel_id"] == channel_id
    assert body["bank"] == "channel"
    channel_bank = client.get(API, params={"channel_id": channel_id}, headers=student).json()
    assert [q["id"] for q in channel_bank] == [body["id"]]
    assert client.get(API, headers=student).json() == []


def test_question_without_a_channel_goes_to_the_general_bank(client, student):
    channel_id = new_channel(client, student)
    general = contribute(client, student).json()

    assert general["channel_id"] is None
    assert [q["id"] for q in client.get(API, headers=student).json()] == [general["id"]]
    assert client.get(API, params={"channel_id": channel_id}, headers=student).json() == []


def test_only_channel_members_can_add_to_a_channel_bank(client, student, other_student, admin):
    channel_id = new_channel(client, student)

    refused = contribute(client, other_student, channel_id=channel_id)
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Join the channel to add questions to its bank"

    assert contribute(client, admin, channel_id=channel_id).status_code == 201


def test_a_visible_channel_bank_can_be_browsed_by_non_members(client, student, other_student):
    channel_id = new_channel(client, student)
    contribute(client, student, channel_id=channel_id)

    found = client.get(API, params={"channel_id": channel_id}, headers=other_student).json()

    assert len(found) == 1


def test_a_hidden_channel_bank_is_not_revealed(client, db, other_student):
    faculty = make_user(db, "faculty_a", Role.AUTHORIZED_USER)
    faculty_headers = auth(client, "faculty_a")
    response = client.post(
        "/api/v1/channels",
        json={"name": "Official DBMS", "subject": "DBMS", "channel_type": "authorized"},
        headers=faculty_headers,
    )
    channel_id = response.json()["id"]  # pending approval: hidden from other students
    question = questions.create_question(
        db,
        author_id=faculty.id,
        subject="DBMS",
        topic="Keys",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.SHORT_ANSWER,
        text="What is a primary key?",
        correct_answer="A unique row identifier",
        channel_id=channel_id,
    )
    db.commit()

    assert (
        client.post(API, json=mcq(channel_id=channel_id), headers=other_student).status_code == 404
    )
    assert (
        client.get(API, params={"channel_id": channel_id}, headers=other_student).status_code == 404
    )
    assert client.get(f"{API}/{question.id}", headers=other_student).status_code == 404


def test_unknown_channel_is_not_found(client, student):
    assert contribute(client, student, channel_id=9999).status_code == 404


# --------------------------------------------------------------------------- #
# IT_09 - search, rate and report
# --------------------------------------------------------------------------- #


@pytest.fixture()
def dbms_bank(client, student):
    """Question bank contains multiple DBMS questions (IT_09 pre-condition)."""
    ids = {}
    ids["normalization"] = contribute(client, student).json()["id"]
    ids["bcnf"] = contribute(
        client,
        student,
        text="Which normal form removes every non-trivial functional dependency anomaly?",
        topic="Normal forms",
        difficulty="hard",
        options=["1NF", "2NF", "BCNF"],
        correct_answer="BCNF",
        explanation=None,
    ).json()["id"]
    ids["primary_key"] = contribute(
        client,
        student,
        text="A primary key can contain NULL values.",
        topic="Keys",
        question_type="true_false",
        options=None,
        correct_answer="False",
        explanation=None,
    ).json()["id"]
    return ids


def test_IT_09_search_rate_and_report(client, db, student, other_student, dbms_bank):
    # 1. Search with keyword "normalization": matching questions only.
    results = client.get(API, params={"q": "normalization"}, headers=other_student).json()
    assert {q["id"] for q in results} == {dbms_bank["normalization"]}

    # 2-3. Open a result and rate it 4 stars: the rating is saved.
    opened = client.get(f"{API}/{results[0]['id']}", headers=other_student)
    assert opened.status_code == 200
    rated = client.put(
        f"{API}/{results[0]['id']}/rating", json={"rating": 4}, headers=other_student
    )
    assert rated.status_code == 200
    assert rated.json()["my_rating"] == 4
    assert rated.json()["rating_average"] == 4.0
    assert rated.json()["rating_count"] == 1
    assert db.query(QuestionRating).filter_by(question_id=results[0]["id"]).one().rating == 4

    # 4. Report a different question as "Incorrect answer": logged for moderation.
    reported = client.post(
        f"{API}/{dbms_bank['primary_key']}/reports",
        json={"reason": "incorrect", "details": "Incorrect answer"},
        headers=other_student,
    )
    assert reported.status_code == 201
    assert reported.json()["status"] == "open"
    assert reported.json()["message"] == "Report submitted for moderation"
    report = db.get(Report, reported.json()["report_id"])
    assert report.target_type is ReportTarget.QUESTION
    assert report.target_id == dbms_bank["primary_key"]
    assert report.reason is ReportReason.INCORRECT
    assert report.status is ReportStatus.OPEN
    assert report.details == "Incorrect answer"


def test_keyword_search_ignores_case_and_matches_tags(client, student, dbms_bank):
    by_text = client.get(API, params={"q": "NORMAL FORM"}, headers=student).json()
    assert {q["id"] for q in by_text} == {dbms_bank["bcnf"]}

    by_topic = client.get(API, params={"q": "keys"}, headers=student).json()
    assert {q["id"] for q in by_topic} == {dbms_bank["primary_key"]}

    assert client.get(API, params={"q": "photosynthesis"}, headers=student).json() == []


def test_search_filters_by_tags(client, student, dbms_bank):
    hard = client.get(API, params={"difficulty": "hard"}, headers=student).json()
    assert [q["id"] for q in hard] == [dbms_bank["bcnf"]]

    true_false = client.get(API, params={"type": "true_false"}, headers=student).json()
    assert [q["id"] for q in true_false] == [dbms_bank["primary_key"]]

    by_subject = client.get(API, params={"subject": "dbms", "topic": "Keys"}, headers=student)
    assert [q["id"] for q in by_subject.json()] == [dbms_bank["primary_key"]]


def test_rating_again_replaces_the_earlier_rating(client, student, other_student, dbms_bank):
    url = f"{API}/{dbms_bank['normalization']}/rating"
    client.put(url, json={"rating": 2}, headers=student)
    client.put(url, json={"rating": 4}, headers=other_student)
    again = client.put(url, json={"rating": 5}, headers=other_student).json()

    assert again["rating_count"] == 2
    assert again["rating_average"] == 3.5
    assert again["my_rating"] == 5
    listed = client.get(API, params={"q": "normalization"}, headers=student).json()[0]
    assert listed["my_rating"] == 2 and listed["rating_average"] == 3.5


@pytest.mark.parametrize("rating", [0, 6])
def test_rating_must_be_one_to_five_stars(client, student, dbms_bank, rating):
    url = f"{API}/{dbms_bank['normalization']}/rating"
    assert client.put(url, json={"rating": rating}, headers=student).status_code == 422


def test_rating_or_reporting_an_unknown_question_is_not_found(client, student):
    assert client.put(f"{API}/999/rating", json={"rating": 3}, headers=student).status_code == 404
    response = client.post(f"{API}/999/reports", json={"reason": "incorrect"}, headers=student)
    assert response.status_code == 404


def test_reporting_twice_does_not_file_a_duplicate(client, db, other_student, dbms_bank):
    url = f"{API}/{dbms_bank['bcnf']}/reports"
    first = client.post(url, json={"reason": "incorrect"}, headers=other_student).json()
    second = client.post(url, json={"reason": "incorrect"}, headers=other_student).json()

    assert first["report_id"] == second["report_id"]
    assert db.query(Report).count() == 1


def test_a_channel_question_report_is_scoped_to_its_channel(client, db, student, other_student):
    channel_id = new_channel(client, student)
    question_id = contribute(client, student, channel_id=channel_id).json()["id"]

    report_id = client.post(
        f"{API}/{question_id}/reports", json={"reason": "duplicate"}, headers=other_student
    ).json()["report_id"]

    assert db.get(Report, report_id).channel_id == channel_id


def test_unreviewed_llm_drafts_stay_out_of_the_bank(client, db, student):
    author = accounts.get_user_by_email(db, "student_a@test.com")
    draft = questions.create_question(
        db,
        author_id=author.id,
        subject="DBMS",
        topic="Normalization",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.SHORT_ANSWER,
        text="Define normalization.",
        correct_answer="Reducing redundancy",
        source=QuestionSource.LLM,
    )
    db.commit()
    make_user(db, "student_c")
    other = auth(client, "student_c")

    assert client.get(API, params={"q": "normalization"}, headers=other).json() == []
    assert client.get(f"{API}/{draft.id}", headers=other).status_code == 404
    assert client.get(f"{API}/{draft.id}", headers=student).json()["status"] == "draft"
