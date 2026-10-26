"""S13.1 - reputation, badges and contributor leaderboard (issue #42, FR-13).

Test Plan cases are named after their IDs: UT_25 (a positive peer rating adds the
configured points), UT_26 (leaderboard follows updated totals) and ST_26 (crossing a
threshold through ratings awards the "Top Contributor" badge on the profile), plus one
test per acceptance criterion. The plan's steps run in the web UI; these call the
endpoints that UI uses.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.modules.accounts import service as accounts
from app.modules.materials import service as materials
from app.modules.materials.models import MaterialType
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType
from app.modules.reputation import service as reputation

API = "/api/v1"
PASSWORD = "Pass@1234"


def make_user(db, username: str) -> int:
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=username.replace("_", " ").title(),
        password=PASSWORD,
        username=username,
    )
    db.commit()
    return user.id


def auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        f"{API}/accounts/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def add_material(db, uploader_id: int, title: str = "Normal forms cheat sheet") -> int:
    material_id = materials.create_material(
        db,
        uploader_id=uploader_id,
        title=title,
        material_type=MaterialType.NOTES,
        subject="DBMS",
        url="https://example.com/notes",
    ).id
    db.commit()
    return material_id


def add_question(db, author_id: int) -> int:
    question_id = questions.create_question(
        db,
        author_id=author_id,
        subject="DBMS",
        topic="Keys",
        difficulty=Difficulty.EASY,
        question_type=QuestionType.SHORT_ANSWER,
        text="What is a foreign key?",
        correct_answer="A column that references another table's primary key",
    ).id
    db.commit()
    return question_id


def rate_material(client, headers, material_id: int, stars: int):
    response = client.put(
        f"{API}/materials/{material_id}/review", json={"rating": stars}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response


def rate_question(client, headers, question_id: int, stars: int):
    response = client.put(
        f"{API}/questions/{question_id}/rating", json={"rating": stars}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response


def profile(client, headers) -> dict:
    return client.get(f"{API}/profiles/me", headers=headers).json()


@pytest.fixture()
def people(client, db) -> dict:
    """Aditi contributes; Rahul, Meera and Kiran are peers who rate her work."""
    names = ("aditi_r", "rahul_m", "meera_k", "kiran_s")
    ids = {name: make_user(db, name) for name in names}
    return {"ids": ids, "headers": {name: auth(client, name) for name in names}}


# --------------------------------------------------------------------------- #
# Test Plan cases
# --------------------------------------------------------------------------- #


def test_UT_25_positive_peer_rating_adds_the_configured_points(client, db, people):
    aditi = people["ids"]["aditi_r"]
    reputation.award(db, user_id=aditi, points=100, reason="starting_balance")
    material = add_material(db, aditi)
    db.commit()
    assert profile(client, people["headers"]["aditi_r"])["reputation"] == 100

    rate_material(client, people["headers"]["rahul_m"], material, 5)

    assert settings.reputation_points_per_positive_rating == 5
    assert profile(client, people["headers"]["aditi_r"])["reputation"] == 105


def test_UT_26_leaderboard_reflects_updated_reputation_totals(client, db, people):
    aditi, rahul = people["ids"]["aditi_r"], people["ids"]["rahul_m"]
    reputation.award(db, user_id=aditi, points=10, reason="starting_balance")
    reputation.award(db, user_id=rahul, points=15, reason="starting_balance")
    db.commit()
    headers = people["headers"]["meera_k"]

    before = client.get(f"{API}/leaderboard", headers=headers).json()
    assert [e["user_id"] for e in before["entries"]] == [rahul, aditi]

    # Aditi earns enough to pass Rahul: two positive ratings = +10 -> 20 > 15.
    material = add_material(db, aditi)
    rate_material(client, people["headers"]["meera_k"], material, 5)
    rate_material(client, people["headers"]["kiran_s"], material, 4)

    after = client.get(f"{API}/leaderboard", headers=headers).json()
    assert [(e["user_id"], e["rank"], e["reputation"]) for e in after["entries"]] == [
        (aditi, 1, 20),
        (rahul, 2, 15),
    ]


def test_ST_26_reaching_the_threshold_through_ratings_awards_top_contributor(client, db, people):
    aditi = people["ids"]["aditi_r"]
    top = next(level for level in reputation.BADGE_LEVELS if level.code == "top-contributor")
    reputation.award(db, user_id=aditi, points=top.threshold - 10, reason="starting_balance")
    db.commit()
    assert "Top Contributor" not in [
        b["name"] for b in profile(client, people["headers"]["aditi_r"])["badges"]
    ]

    # One material rated by three peers: 3 x 5 = 15 points, crossing the threshold.
    material = add_material(db, aditi)
    for peer in ("rahul_m", "meera_k", "kiran_s"):
        rate_material(client, people["headers"][peer], material, 5)

    body = profile(client, people["headers"]["aditi_r"])
    assert body["reputation"] == top.threshold + 5
    assert "Top Contributor" in [b["name"] for b in body["badges"]]


# --------------------------------------------------------------------------- #
# Acceptance criteria
# --------------------------------------------------------------------------- #


def test_positive_question_rating_also_increases_reputation(client, db, people):
    question = add_question(db, people["ids"]["aditi_r"])
    rate_question(client, people["headers"]["rahul_m"], question, 4)
    assert reputation.total(db, people["ids"]["aditi_r"]) == 5


def test_points_follow_the_configured_amount_and_threshold(client, db, people, monkeypatch):
    monkeypatch.setattr(settings, "reputation_points_per_positive_rating", 7)
    monkeypatch.setattr(settings, "reputation_positive_rating", 5)
    material = add_material(db, people["ids"]["aditi_r"])
    rate_material(client, people["headers"]["rahul_m"], material, 4)  # not positive now
    assert reputation.total(db, people["ids"]["aditi_r"]) == 0
    rate_material(client, people["headers"]["meera_k"], material, 5)
    assert reputation.total(db, people["ids"]["aditi_r"]) == 7


def test_low_ratings_do_not_add_reputation(client, db, people):
    material = add_material(db, people["ids"]["aditi_r"])
    for peer, stars in (("rahul_m", 1), ("meera_k", 2), ("kiran_s", 3)):
        rate_material(client, people["headers"][peer], material, stars)
    assert reputation.total(db, people["ids"]["aditi_r"]) == 0


def test_rating_again_counts_once_and_lowering_it_withdraws_the_points(client, db, people):
    aditi = people["ids"]["aditi_r"]
    material = add_material(db, aditi)
    rater = people["headers"]["rahul_m"]

    rate_material(client, rater, material, 5)
    rate_material(client, rater, material, 4)  # still positive: no second award
    assert reputation.total(db, aditi) == 5

    rate_material(client, rater, material, 2)
    assert reputation.total(db, aditi) == 0
    rate_material(client, rater, material, 5)
    assert reputation.total(db, aditi) == 5


def test_reputation_events_record_their_source(client, db, people):
    aditi = people["ids"]["aditi_r"]
    material = add_material(db, aditi)
    question = add_question(db, aditi)
    rate_material(client, people["headers"]["rahul_m"], material, 5)
    rate_question(client, people["headers"]["meera_k"], question, 5)
    rate_material(client, people["headers"]["rahul_m"], material, 1)

    body = client.get(f"{API}/reputation/me", headers=people["headers"]["aditi_r"]).json()

    assert body["reputation"] == 5
    assert body["event_count"] == 3
    events = [(e["points"], e["reason"], e["source_type"]) for e in body["events"]]
    assert events == [
        (-5, "peer_rating_withdrawn", "material_review"),
        (5, "peer_rating", "question_rating"),
        (5, "peer_rating", "material_review"),
    ]
    assert all(e["source_id"] is not None and e["created_at"] for e in body["events"])
    # The audit trail adds up to the total shown everywhere else.
    assert sum(e["points"] for e in body["events"]) == body["reputation"]
    assert profile(client, people["headers"]["aditi_r"])["reputation"] == body["reputation"]


def test_self_rating_does_not_change_reputation(client, db, people):
    aditi = people["ids"]["aditi_r"]
    question = add_question(db, aditi)
    material = add_material(db, aditi)

    # Questions accept a self-rating (it counts towards the average) but earns nothing.
    rate_question(client, people["headers"]["aditi_r"], question, 5)
    # Materials refuse a self-rating outright.
    response = client.put(
        f"{API}/materials/{material}/review",
        json={"rating": 5},
        headers=people["headers"]["aditi_r"],
    )
    assert response.status_code == 403
    # Even a direct service call cannot award self-rating points.
    assert (
        reputation.record_peer_rating(
            db,
            contributor_id=aditi,
            rater_id=aditi,
            rating=5,
            source_type="material_review",
            source_id=1,
        )
        == 0
    )

    assert reputation.total(db, aditi) == 0
    assert (
        client.get(f"{API}/reputation/me", headers=people["headers"]["aditi_r"]).json()["events"]
        == []
    )


def test_crossing_each_threshold_awards_its_badge_once(db, people):
    aditi = people["ids"]["aditi_r"]
    levels = reputation.BADGE_LEVELS
    reputation.award(db, user_id=aditi, points=levels[0].threshold - 1, reason="seed")
    assert reputation.badges_for(db, aditi) == []

    reputation.award(db, user_id=aditi, points=1, reason="seed")
    assert [b.code for b in reputation.badges_for(db, aditi)] == [levels[0].code]

    # A jump past several thresholds awards every badge crossed, each once.
    reputation.award(db, user_id=aditi, points=levels[-1].threshold, reason="seed")
    reputation.award(db, user_id=aditi, points=5, reason="seed")
    codes = sorted(b.code for b in reputation.badges_for(db, aditi))
    assert codes == sorted(level.code for level in levels)


def test_badges_are_kept_when_reputation_later_drops(db, people):
    aditi = people["ids"]["aditi_r"]
    threshold = reputation.BADGE_LEVELS[0].threshold
    reputation.award(db, user_id=aditi, points=threshold, reason="seed")
    reputation.award(db, user_id=aditi, points=-10, reason="peer_rating_withdrawn")
    assert [b.code for b in reputation.badges_for(db, aditi)] == [reputation.BADGE_LEVELS[0].code]


def test_leaderboard_order_matches_current_totals_with_shared_ranks(client, db, people):
    ids = people["ids"]
    for name, points in (("aditi_r", 30), ("rahul_m", 50), ("meera_k", 30)):
        reputation.award(db, user_id=ids[name], points=points, reason="seed")
    db.commit()

    body = client.get(f"{API}/leaderboard", headers=people["headers"]["kiran_s"]).json()

    rows = [(e["rank"], e["user_id"], e["reputation"]) for e in body["entries"]]
    assert rows == [
        (1, ids["rahul_m"], 50),
        (2, ids["aditi_r"], 30),
        (2, ids["meera_k"], 30),
    ]
    assert body["total"] == 3
    # Kiran has no reputation yet, so is not ranked.
    assert body["me"] == {"rank": None, "reputation": 0}
    for entry in body["entries"]:
        assert entry["reputation"] == reputation.total(db, entry["user_id"])


def test_leaderboard_pages_and_shows_the_callers_rank(client, db, people):
    ids = people["ids"]
    for points, name in enumerate(("aditi_r", "rahul_m", "meera_k", "kiran_s"), start=1):
        reputation.award(db, user_id=ids[name], points=points * 10, reason="seed")
    db.commit()
    headers = people["headers"]["rahul_m"]

    page = client.get(f"{API}/leaderboard?limit=2&offset=1", headers=headers).json()

    assert [(e["rank"], e["user_id"]) for e in page["entries"]] == [
        (2, ids["meera_k"]),
        (3, ids["rahul_m"]),
    ]
    assert page["total"] == 4
    assert page["me"] == {"rank": 3, "reputation": 20}


def test_leaderboard_lists_badges_and_needs_sign_in(client, db, people):
    aditi = people["ids"]["aditi_r"]
    reputation.award(db, user_id=aditi, points=reputation.BADGE_LEVELS[0].threshold, reason="s")
    db.commit()

    assert client.get(f"{API}/leaderboard").status_code == 401
    body = client.get(f"{API}/leaderboard", headers=people["headers"]["rahul_m"]).json()
    assert body["entries"][0]["badges"] == [reputation.BADGE_LEVELS[0].code]
    assert body["entries"][0]["display_name"] == "Aditi R"


def test_badge_awards_appear_on_the_users_profile(client, db, people):
    aditi = people["ids"]["aditi_r"]
    first = reputation.BADGE_LEVELS[0]
    reputation.award(db, user_id=aditi, points=first.threshold, reason="seed")
    db.commit()

    own = profile(client, people["headers"]["aditi_r"])
    public = client.get(f"{API}/profiles/{aditi}", headers=people["headers"]["rahul_m"]).json()

    for body in (own, public):
        assert [b["name"] for b in body["badges"]] == [first.name]
    catalogue = client.get(f"{API}/badges", headers=people["headers"]["aditi_r"]).json()
    assert [(b["code"], b["earned"]) for b in catalogue] == [
        (level.code, level is first) for level in reputation.BADGE_LEVELS
    ]
    assert [b["threshold"] for b in catalogue] == sorted(b["threshold"] for b in catalogue)
