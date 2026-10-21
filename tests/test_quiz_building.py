"""S05.2 - quiz assembly and publishing (issue #25, FR-05).

Test Plan cases are named after their IDs: IT_10, ST_09, ST_10.
"""

import random

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionSource, QuestionType
from app.modules.quizzes import building

API = "/api/v1/quizzes"
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
def author(client, db):
    make_user(db, "author_a")
    return auth(client, "author_a")


@pytest.fixture()
def member(client, db):
    make_user(db, "member_b")
    return auth(client, "member_b")


@pytest.fixture()
def outsider(client, db):
    make_user(db, "outsider_c")
    return auth(client, "outsider_c")


def seed_bank(db, *, subject="DBMS", topic="Normalization", difficulty=Difficulty.MEDIUM, n=10):
    owner = accounts.get_user_by_email(db, "seeder@test.com") or make_user(db, "seeder")
    ids = [
        questions.create_question(
            db,
            author_id=owner.id,
            subject=subject,
            topic=topic,
            difficulty=difficulty,
            question_type=QuestionType.TRUE_FALSE,
            text=f"{subject} {difficulty.value} statement {i}",
            options=["True", "False"],
            correct_answer="True",
        ).id
        for i in range(n)
    ]
    db.commit()
    return ids


def new_channel(client, headers, name="DBMS Study Group"):
    response = client.post(
        "/api/v1/channels", json={"name": name, "subject": "DBMS"}, headers=headers
    )
    assert response.status_code == 201
    return response.json()["id"]


def join(client, headers, channel_id):
    response = client.post(f"/api/v1/channels/{channel_id}/join", headers=headers)
    assert response.status_code in (200, 201), response.json()


def criteria(**overrides):
    body = {
        "subject": "DBMS",
        "difficulty": "medium",
        "question_count": 10,
        "duration_minutes": 15,
    }
    return {**body, **overrides}


def build(client, headers, **overrides):
    return client.post(API, json=criteria(**overrides), headers=headers)


# --------------------------------------------------------------------------- #
# IT_10 - a quiz is generated with the requested count, difficulty and timer
# --------------------------------------------------------------------------- #


def test_IT_10_quiz_is_built_with_requested_count_difficulty_and_timer(client, db, author):
    seed_bank(db, difficulty=Difficulty.MEDIUM, n=12)
    seed_bank(db, difficulty=Difficulty.EASY, n=5)
    seed_bank(db, subject="Operating Systems", n=5)

    response = build(client, author, title="DBMS Quick Quiz")

    assert response.status_code == 201, response.json()
    quiz = response.json()
    assert quiz["title"] == "DBMS Quick Quiz"
    assert quiz["question_count"] == 10 and len(quiz["questions"]) == 10
    assert quiz["duration_minutes"] == 15
    assert quiz["difficulty"] == "medium"
    assert {q["difficulty"] for q in quiz["questions"]} == {"medium"}
    assert {q["subject"] for q in quiz["questions"]} == {"DBMS"}
    assert len({q["id"] for q in quiz["questions"]}) == 10
    assert [q["position"] for q in quiz["questions"]] == list(range(1, 11))
    assert quiz["status"] == "draft"


def test_preview_picks_matching_questions_without_saving(client, db, author):
    seed_bank(db, n=12)

    response = client.post(f"{API}/preview", json=criteria(), headers=author)

    assert response.status_code == 200
    body = response.json()
    assert len(body["questions"]) == 10 and body["duration_minutes"] == 15
    assert client.get(API, params={"mine": True}, headers=author).json() == []


def test_reviewed_question_set_is_saved_in_the_given_order(client, db, author):
    ids = seed_bank(db, n=6)
    chosen = [ids[4], ids[0], ids[2]]

    response = build(client, author, question_count=3, question_ids=chosen)

    assert response.status_code == 201
    assert [q["id"] for q in response.json()["questions"]] == chosen


def test_reviewed_set_must_match_the_requested_count(client, db, author):
    ids = seed_bank(db, n=6)

    short = build(client, author, question_count=3, question_ids=ids[:2])
    duplicated = build(client, author, question_count=2, question_ids=[ids[0], ids[0]])

    assert short.status_code == 422
    assert short.json()["detail"] == "Choose exactly 3 different questions"
    assert duplicated.status_code == 422


def test_topic_matches_ignoring_case_and_a_topic_or_subject_is_required(client, db, author):
    seed_bank(db, topic="Normalization", n=3)

    by_topic = build(client, author, subject=None, topic="  normalization ", question_count=3)
    neither = build(client, author, subject=" ", topic=None)

    assert by_topic.status_code == 201
    assert by_topic.json()["topic"] == "normalization"
    assert neither.status_code == 422 and neither.json()["detail"] == "Select a topic"


@pytest.mark.parametrize(
    "field", [{"question_count": 0}, {"duration_minutes": 0}, {"difficulty": "extreme"}]
)
def test_count_duration_and_difficulty_are_validated(client, db, author, field):
    assert build(client, author, **field).status_code == 422


def test_selection_is_random_but_always_from_matching_questions(db):
    ids = seed_bank(db, n=20)
    picks = {
        tuple(
            item.question.id
            for item in building.select_questions(
                db,
                subject="DBMS",
                topic=None,
                difficulty=Difficulty.MEDIUM,
                count=5,
                rng=random.Random(seed),
            )
        )
        for seed in range(5)
    }

    assert len(picks) > 1
    assert all(set(pick) <= set(ids) for pick in picks)


def test_building_needs_sign_in(client):
    assert build(client, {}).status_code == 401


# --------------------------------------------------------------------------- #
# ST_10 - a newly contributed question is immediately selectable
# --------------------------------------------------------------------------- #


def test_ST_10_new_question_is_immediately_selectable_for_a_quiz(client, db, author):
    seed_bank(db, difficulty=Difficulty.EASY, n=2)
    contributed = client.post(
        "/api/v1/questions",
        json={
            "text": "What is a candidate key?",
            "subject": "DBMS",
            "topic": "Keys",
            "difficulty": "easy",
            "question_type": "short_answer",
            "correct_answer": "A minimal superkey",
        },
        headers=author,
    )
    assert contributed.status_code == 201
    new_id = contributed.json()["id"]

    preview = client.post(
        f"{API}/preview", json=criteria(difficulty="easy", question_count=3), headers=author
    )
    assert new_id in [q["id"] for q in preview.json()["questions"]]

    quiz = build(client, author, difficulty="easy", question_count=1, question_ids=[new_id])
    assert quiz.status_code == 201
    assert quiz.json()["questions"][0]["text"] == "What is a candidate key?"


def test_unpublished_llm_drafts_and_unknown_questions_cannot_be_used(client, db, author):
    owner = make_user(db, "llm_owner")
    draft = questions.create_question(
        db,
        author_id=owner.id,
        subject="DBMS",
        topic="Keys",
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.SHORT_ANSWER,
        text="Draft from the LLM",
        correct_answer="x",
        source=QuestionSource.LLM,
    )
    db.commit()

    with_draft = build(client, author, question_count=1, question_ids=[draft.id])
    unknown = build(client, author, question_count=1, question_ids=[999_999])

    assert with_draft.status_code == 422
    assert with_draft.json()["detail"] == f"Question {draft.id} is not in the question bank"
    assert unknown.status_code == 422
    assert (
        client.post(f"{API}/preview", json=criteria(question_count=1), headers=author).status_code
        == 409
    )


# --------------------------------------------------------------------------- #
# Too few matching questions fails gracefully
# --------------------------------------------------------------------------- #


def test_too_few_matching_questions_fails_gracefully(client, db, author):
    seed_bank(db, n=3)

    for response in (
        build(client, author),
        client.post(f"{API}/preview", json=criteria(), headers=author),
    ):
        assert response.status_code == 409
        assert response.json() == {
            "detail": "Only 3 matching questions are available (10 requested). "
            "Broaden the criteria or ask for fewer questions.",
            "available": 3,
            "requested": 10,
        }
    assert client.get(API, params={"mine": True}, headers=author).json() == []


def test_empty_bank_reports_zero_available(client, author):
    response = build(client, author, subject="Compilers")

    assert response.status_code == 409 and response.json()["available"] == 0


# --------------------------------------------------------------------------- #
# ST_09 - publishing to a channel: Quizzes tab, visible to all members
# --------------------------------------------------------------------------- #


def test_ST_09_published_quiz_appears_in_channel_quizzes_tab_for_all_members(
    client, db, author, member, outsider
):
    seed_bank(db, n=10)
    channel_id = new_channel(client, author)
    join(client, member, channel_id)
    quiz_id = build(client, author, title="DBMS Quick Quiz").json()["id"]

    published = client.post(
        f"{API}/{quiz_id}/publish", json={"channel_id": channel_id}, headers=author
    )

    assert published.status_code == 200, published.json()
    assert published.json()["status"] == "published"
    assert published.json()["channel_id"] == channel_id
    for viewer in (author, member):
        tab = client.get(API, params={"channel_id": channel_id}, headers=viewer)
        assert tab.status_code == 200
        assert [(q["id"], q["title"]) for q in tab.json()] == [(quiz_id, "DBMS Quick Quiz")]
        opened = client.get(f"{API}/{quiz_id}", headers=viewer)
        assert opened.status_code == 200 and len(opened.json()["questions"]) == 10


def test_members_do_not_see_the_answer_key(client, db, author, member):
    seed_bank(db, n=2)
    channel_id = new_channel(client, author)
    join(client, member, channel_id)
    quiz_id = build(client, author, question_count=2).json()["id"]
    client.post(f"{API}/{quiz_id}/publish", json={"channel_id": channel_id}, headers=author)

    as_member = client.get(f"{API}/{quiz_id}", headers=member).json()["questions"]
    as_author = client.get(f"{API}/{quiz_id}", headers=author).json()["questions"]

    assert {q["correct_answer"] for q in as_member} == {None}
    assert {q["correct_answer"] for q in as_author} == {"True"}


def test_non_members_cannot_see_a_channels_quizzes(client, db, author, outsider):
    seed_bank(db, n=2)
    channel_id = new_channel(client, author)
    quiz_id = build(client, author, question_count=2).json()["id"]
    client.post(f"{API}/{quiz_id}/publish", json={"channel_id": channel_id}, headers=author)

    tab = client.get(API, params={"channel_id": channel_id}, headers=outsider)
    opened = client.get(f"{API}/{quiz_id}", headers=outsider)

    assert tab.status_code == 403 and tab.json()["detail"] == "Join the channel to see its quizzes"
    assert opened.status_code == 403


def test_publishing_to_a_channel_needs_membership(client, db, author, outsider):
    seed_bank(db, n=2)
    channel_id = new_channel(client, author)
    quiz_id = build(client, outsider, question_count=2).json()["id"]

    response = client.post(
        f"{API}/{quiz_id}/publish", json={"channel_id": channel_id}, headers=outsider
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Join the channel to publish quizzes to it"


def test_published_general_quiz_is_listed_for_everyone(client, db, author, outsider):
    seed_bank(db, n=2)
    quiz_id = build(client, author, question_count=2).json()["id"]

    client.post(f"{API}/{quiz_id}/publish", json={}, headers=author)

    assert [q["id"] for q in client.get(API, headers=outsider).json()] == [quiz_id]
    assert client.get(f"{API}/{quiz_id}", headers=outsider).status_code == 200


def test_a_quiz_is_published_once_and_only_by_its_author(client, db, author, member):
    seed_bank(db, n=2)
    quiz_id = build(client, author, question_count=2).json()["id"]

    by_other = client.post(f"{API}/{quiz_id}/publish", json={}, headers=member)
    first = client.post(f"{API}/{quiz_id}/publish", json={}, headers=author)
    again = client.post(f"{API}/{quiz_id}/publish", json={}, headers=author)

    assert by_other.status_code == 404
    assert first.status_code == 200
    assert again.status_code == 409 and again.json()["detail"] == "This quiz is already published"


def test_channel_bank_questions_stay_in_their_channel(client, db, author, member):
    channel_a = new_channel(client, author, "Channel A")
    channel_b = new_channel(client, author, "Channel B")
    private = client.post(
        "/api/v1/questions",
        json={
            "text": "Channel A only",
            "subject": "DBMS",
            "topic": "Keys",
            "difficulty": "medium",
            "question_type": "true_false",
            "correct_answer": "true",
            "channel_id": channel_a,
        },
        headers=author,
    ).json()["id"]

    general_preview = client.post(f"{API}/preview", json=criteria(question_count=1), headers=author)
    channel_quiz = build(
        client, author, question_count=1, question_ids=[private], channel_id=channel_a
    )
    wrong_channel = client.post(
        f"{API}/{channel_quiz.json()['id']}/publish",
        json={"channel_id": channel_b},
        headers=author,
    )
    outside_bank = build(client, member, question_count=1, channel_id=channel_a)

    assert general_preview.status_code == 409
    assert channel_quiz.status_code == 201
    assert wrong_channel.status_code == 422
    assert outside_bank.status_code == 403
    assert outside_bank.json()["detail"] == "Join the channel to use its question bank"


def test_only_authorized_users_publish_to_authorized_channels(client, db, author):
    make_user(db, "admin_z", Role.ADMIN)
    admin = auth(client, "admin_z")
    seed_bank(db, n=2)
    created = client.post(
        "/api/v1/channels",
        json={"name": "Official DBMS", "subject": "DBMS", "channel_type": "authorized"},
        headers=admin,
    )
    assert created.status_code == 201, created.json()
    channel_id = created.json()["id"]
    join(client, author, channel_id)
    student_quiz = build(client, author, question_count=2).json()["id"]
    admin_quiz = build(client, admin, question_count=2).json()["id"]

    refused = client.post(
        f"{API}/{student_quiz}/publish", json={"channel_id": channel_id}, headers=author
    )
    allowed = client.post(
        f"{API}/{admin_quiz}/publish", json={"channel_id": channel_id}, headers=admin
    )

    assert refused.status_code == 403
    assert (
        refused.json()["detail"] == "Only authorized users can add content to Authorized Channels"
    )
    assert allowed.status_code == 200


# --------------------------------------------------------------------------- #
# An unpublished quiz remains private to its author
# --------------------------------------------------------------------------- #


def test_unpublished_quiz_is_private_to_its_author(client, db, author, member):
    seed_bank(db, n=2)
    channel_id = new_channel(client, author)
    join(client, member, channel_id)
    quiz_id = build(client, author, question_count=2, channel_id=channel_id).json()["id"]

    assert client.get(f"{API}/{quiz_id}", headers=author).status_code == 200
    assert [q["id"] for q in client.get(API, params={"mine": True}, headers=author).json()] == [
        quiz_id
    ]
    hidden = client.get(f"{API}/{quiz_id}", headers=member)
    assert hidden.status_code == 404 and hidden.json()["detail"] == "Quiz not found"
    assert client.get(API, params={"channel_id": channel_id}, headers=member).json() == []
    assert client.get(API, headers=member).json() == []
    assert client.get(API, params={"mine": True}, headers=member).json() == []


def test_profile_counts_only_published_quizzes(client, db, author):
    from app.modules.quizzes import service as quizzes

    seed_bank(db, n=2)
    user = accounts.get_user_by_email(db, "author_a@test.com")
    quiz_id = build(client, author, question_count=2).json()["id"]
    assert quizzes.count_created_by(db, user.id) == 0

    client.post(f"{API}/{quiz_id}/publish", json={}, headers=author)

    assert quizzes.count_created_by(db, user.id) == 1
