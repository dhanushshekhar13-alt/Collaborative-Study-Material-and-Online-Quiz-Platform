"""S12.2 - recommendations targeting weak topics (issue #41, FR-12, UC-13).

Test Plan cases are named after their IDs: IT_24 (a newly weak topic's materials and
quizzes appear near the top), ST_23 (recommendations shift away once the topic
improves) and ST_24 (top items are tagged with the weakest topic). Attempts go through
the real ``/attempts`` endpoints; recommendations are read from
``GET /api/v1/analytics/recommendations``.
"""

import pytest

from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialType
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType
from app.modules.quizzes import attempts
from tests.test_analytics import make_topic_quiz, take
from tests.test_quiz_attempts import API, OPTIONS, Clock, auth, make_user

DBMS = "DBMS"


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(attempts, "utcnow", fake)
    return fake


@pytest.fixture()
def author(db):
    return make_user(db, "content_author")


@pytest.fixture()
def learner(db):
    return make_user(db, "learner")


@pytest.fixture()
def headers(client, learner):
    return auth(client, "learner")


def recommendations(client, headers, **params) -> dict:
    response = client.get(f"{API}/analytics/recommendations", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def share_material(db, author_id: int, topic: str, title: str | None = None, **extra) -> int:
    material = materials.create_material(
        db,
        uploader_id=author_id,
        title=title or f"{topic} notes",
        material_type=MaterialType.NOTES,
        subject=extra.pop("subject", DBMS),
        topic=topic,
        url="https://example.com/notes",
        **extra,
    )
    db.commit()
    return material.id


def share_question(db, author_id: int, topic: str, text: str | None = None, **extra) -> int:
    qid = questions.create_question(
        db,
        author_id=author_id,
        subject=extra.pop("subject", DBMS),
        topic=topic,
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.MULTIPLE_CHOICE,
        text=text or f"A {topic} practice question?",
        options=OPTIONS,
        correct_answer="A",
        **extra,
    ).id
    db.commit()
    return qid


def topics_of(items: list[dict]) -> set[str]:
    return {i["topic"] for i in items}


def fresh(body: dict) -> list[dict]:
    return [i for i in body["items"] if not i["consumed"]]


@pytest.fixture()
def library(db, author):
    """Peer-shared content on three DBMS topics, plus a quiz per topic."""
    content = {}
    for topic in ("Concurrency Control", "Indexing", "Normalization", "Transaction Management"):
        content[topic] = {
            "quiz": make_topic_quiz(db, author.id, DBMS, topic),
            "practice_quiz": make_topic_quiz(db, author.id, DBMS, topic),
            "material": share_material(db, author.id, topic),
            "question": share_question(db, author.id, topic),
        }
    return content


# --------------------------------------------------------------------------- #
# IT_24 - a newly weak topic is recommended near the top
# --------------------------------------------------------------------------- #


def test_IT_24_a_30_percent_concurrency_quiz_puts_its_materials_and_quizzes_on_top(
    client, headers, library, clock
):
    before = recommendations(client, headers)
    assert before["mode"] == "general"

    take(client, headers, library["Concurrency Control"]["quiz"], right=3, clock=clock)
    after = recommendations(client, headers)

    assert after["mode"] == "targeted"
    assert [f["topic"] for f in after["focus_topics"]] == ["Concurrency Control"]
    assert after["focus_topics"][0]["accuracy"] == 30.0
    top = after["items"][:3]
    assert topics_of(top) == {"Concurrency Control"}
    assert {i["item_type"] for i in top} == {"quiz", "material", "question"}
    ids = {(i["item_type"], i["item_id"]) for i in fresh(after)}
    cc = library["Concurrency Control"]
    assert ("material", cc["material"]) in ids
    assert ("quiz", cc["practice_quiz"][0]) in ids
    assert ("question", cc["question"]) in ids


# --------------------------------------------------------------------------- #
# ST_23 - recommendations shift away once the topic improves
# --------------------------------------------------------------------------- #


def test_ST_23_after_two_85_percent_attempts_concurrency_gives_way_to_the_next_weakest(
    client, headers, library, clock
):
    cc, idx = library["Concurrency Control"]["quiz"], library["Indexing"]["quiz"]
    take(client, headers, cc, right=3, clock=clock)
    take(client, headers, idx, right=4, clock=clock)
    take(client, headers, idx, right=4, clock=clock)
    before = recommendations(client, headers)
    assert [f["topic"] for f in before["focus_topics"]] == ["Concurrency Control", "Indexing"]
    assert before["items"][0]["topic"] == "Concurrency Control"

    take(client, headers, cc, right=9, clock=clock)
    take(client, headers, cc, right=9, clock=clock)
    after = recommendations(client, headers)

    assert [f["topic"] for f in after["focus_topics"]] == ["Indexing"]
    assert after["items"][0]["topic"] == "Indexing"
    assert "Concurrency Control" not in topics_of(after["items"])


# --------------------------------------------------------------------------- #
# ST_24 - the top items are tagged with the weakest topic
# --------------------------------------------------------------------------- #


def test_ST_24_top_recommendations_are_tagged_with_the_weakest_topic(
    client, headers, library, clock
):
    take(client, headers, library["Transaction Management"]["quiz"], right=2, clock=clock)
    take(client, headers, library["Transaction Management"]["quiz"], right=2, clock=clock)
    take(client, headers, library["Indexing"]["quiz"], right=4, clock=clock)
    take(client, headers, library["Indexing"]["quiz"], right=5, clock=clock)
    take(client, headers, library["Normalization"]["quiz"], right=9, clock=clock)

    board = client.get(f"{API}/analytics/dashboard", headers=headers).json()
    weakest = board["weak_areas"][0]["topic"]
    body = recommendations(client, headers)

    assert weakest == "Transaction Management"
    assert body["focus_topics"][0]["topic"] == weakest
    for item in fresh(body)[:3]:
        assert (item["subject"], item["topic"]) == (DBMS, weakest)
        assert item["reason"] == "Weak area: 20% over 2 attempts"


# --------------------------------------------------------------------------- #
# Recommended items map to weak topics
# --------------------------------------------------------------------------- #


def test_every_targeted_item_maps_to_a_weak_topic(client, headers, library, clock):
    take(client, headers, library["Indexing"]["quiz"], right=2, clock=clock)
    take(client, headers, library["Normalization"]["quiz"], right=10, clock=clock)
    take(client, headers, library["Normalization"]["quiz"], right=10, clock=clock)

    body = recommendations(client, headers, limit=50)

    focus = {(f["subject"], f["topic"]) for f in body["focus_topics"]}
    assert focus == {(DBMS, "Indexing")}
    assert body["items"] and {(i["subject"], i["topic"]) for i in body["items"]} <= focus


def test_a_developing_topic_is_not_targeted(client, headers, library, clock):
    take(client, headers, library["Indexing"]["quiz"], right=6, clock=clock)
    take(client, headers, library["Indexing"]["quiz"], right=6, clock=clock)

    body = recommendations(client, headers)

    assert body["mode"] == "general" and body["focus_topics"] == []
    assert body["message"] == "No weak topics right now. Here are popular picks to keep you going."


def test_a_weak_topic_with_nothing_shared_falls_back_to_popular_picks(
    client, db, headers, learner, author, clock
):
    # The only Routing content is the learner's own practice quiz.
    quiz = make_topic_quiz(db, learner.id, "Networks", "Routing")
    take(client, headers, quiz, right=1, clock=clock)
    share_material(db, author.id, "Normalization")

    body = recommendations(client, headers)

    assert body["mode"] == "general"
    assert [f["topic"] for f in body["focus_topics"]] == ["Routing"]
    assert "Nothing has been shared yet for your weak topics (Routing)" in body["message"]
    assert any(i["topic"] == "Normalization" for i in body["items"])


def test_a_new_user_gets_popular_picks_with_a_clear_message(client, headers, library):
    body = recommendations(client, headers)

    assert body["mode"] == "general" and body["focus_topics"] == []
    assert body["message"] == (
        "Attempt a quiz so we can find your weak topics. Meanwhile, here are popular picks."
    )
    assert body["items"]
    assert all(i["reason"] == "Popular with other learners" for i in body["items"])


# --------------------------------------------------------------------------- #
# Already-consumed items are de-prioritised
# --------------------------------------------------------------------------- #


def test_consumed_items_are_listed_after_fresh_ones_not_hidden(
    client, db, headers, learner, library, clock
):
    cc = library["Concurrency Control"]
    take(client, headers, cc["quiz"], right=3, clock=clock)
    body = recommendations(client, headers, limit=50)

    flags = [i["consumed"] for i in body["items"]]
    assert flags == sorted(flags)  # every fresh item before every consumed one
    consumed = {(i["item_type"], i["item_id"]) for i in body["items"] if i["consumed"]}
    assert ("quiz", cc["quiz"][0]) in consumed  # attempted
    assert ("question", cc["quiz"][1][-1]) in consumed  # answered in that quiz


def test_opening_a_recommendation_moves_it_behind_fresh_items(client, headers, library, clock):
    cc = library["Concurrency Control"]
    take(client, headers, cc["quiz"], right=3, clock=clock)
    first = recommendations(client, headers)["items"][0]
    assert first["consumed"] is False

    opened = client.post(
        f"{API}/analytics/recommendations/opened",
        json={"item_type": first["item_type"], "item_id": first["item_id"]},
        headers=headers,
    )
    assert opened.status_code == 204
    # Opening twice is fine (idempotent).
    client.post(
        f"{API}/analytics/recommendations/opened",
        json={"item_type": first["item_type"], "item_id": first["item_id"]},
        headers=headers,
    )

    body = recommendations(client, headers, limit=50)
    keys = [(i["item_type"], i["item_id"]) for i in body["items"]]
    entry = body["items"][keys.index((first["item_type"], first["item_id"]))]
    assert entry["consumed"] is True
    assert body["items"][0]["item_id"] != first["item_id"] or (
        body["items"][0]["item_type"] != first["item_type"]
    )


def test_a_rated_material_counts_as_consumed(client, db, headers, learner, library, clock):
    cc = library["Concurrency Control"]
    take(client, headers, cc["quiz"], right=3, clock=clock)
    materials.rate_material(db, cc["material"], learner.id, 4)
    db.commit()

    body = recommendations(client, headers, limit=50)

    entry = next(
        i for i in body["items"] if (i["item_type"], i["item_id"]) == ("material", cc["material"])
    )
    assert entry["consumed"] is True


def test_better_rated_materials_rank_first_within_a_topic(
    client, db, headers, author, library, clock
):
    cc = library["Concurrency Control"]
    best = share_material(db, author.id, "Concurrency Control", title="Locking cheat sheet")
    for name in ("rater_a", "rater_b"):
        rater = make_user(db, name)
        materials.rate_material(db, best, rater.id, 5)
        materials.rate_material(db, cc["material"], rater.id, 2)
    db.commit()
    take(client, headers, cc["quiz"], right=3, clock=clock)

    body = recommendations(client, headers)

    shown = [i["item_id"] for i in body["items"] if i["item_type"] == "material"]
    assert shown.index(best) < shown.index(cc["material"])
    top = next(i for i in body["items"] if i["item_id"] == best and i["item_type"] == "material")
    assert (top["rating_average"], top["rating_count"]) == (5.0, 2)


# --------------------------------------------------------------------------- #
# Scope: what the user may see, never their own work
# --------------------------------------------------------------------------- #


def test_own_contributions_are_never_recommended(client, db, headers, learner, library, clock):
    mine = share_material(db, learner.id, "Concurrency Control", title="My own notes")
    take(client, headers, library["Concurrency Control"]["quiz"], right=3, clock=clock)

    body = recommendations(client, headers, limit=50)

    assert ("material", mine) not in {(i["item_type"], i["item_id"]) for i in body["items"]}


def test_channel_content_is_recommended_only_to_its_members(
    client, db, headers, learner, author, library, clock
):
    channel = channels.create_channel(db, name="DB study group", created_by=author.id)
    db.commit()
    private = share_material(
        db, author.id, "Concurrency Control", title="Group notes", channel_id=channel.id
    )
    take(client, headers, library["Concurrency Control"]["quiz"], right=3, clock=clock)

    def listed() -> set[tuple[str, int]]:
        return {
            (i["item_type"], i["item_id"])
            for i in recommendations(client, headers, limit=50)["items"]
        }

    assert ("material", private) not in listed()
    channels.add_member(db, channel.id, learner.id)
    db.commit()
    assert ("material", private) in listed()


# --------------------------------------------------------------------------- #
# Access and validation
# --------------------------------------------------------------------------- #


def test_recommendations_need_a_signed_in_user(client):
    assert client.get(f"{API}/analytics/recommendations").status_code == 401
    opened = client.post(
        f"{API}/analytics/recommendations/opened", json={"item_type": "quiz", "item_id": 1}
    )
    assert opened.status_code == 401


def test_an_unknown_item_type_is_refused(client, headers):
    response = client.post(
        f"{API}/analytics/recommendations/opened",
        json={"item_type": "video", "item_id": 1},
        headers=headers,
    )
    assert response.status_code == 422


def test_an_admin_is_scoped_like_anyone_else(client, db, library, clock):
    make_user(db, "admin_learner", Role.ADMIN)
    admin = auth(client, "admin_learner")
    take(client, admin, library["Indexing"]["quiz"], right=1, clock=clock)
    body = recommendations(client, admin)
    assert body["focus_topics"][0]["topic"] == "Indexing"
