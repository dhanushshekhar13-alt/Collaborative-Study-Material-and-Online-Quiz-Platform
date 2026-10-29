"""NFR-06 - LLM reliability: review gate, reporting and deterministic scoring (issue #49).

The NFR is verified by UT_14, IT_13 and ST_16 plus a review of the scoring code path.
Those cases run here under their IDs across every route LLM output can take, beside
their feature tests (``test_llm_generation.py``, ``test_descriptive_results.py``):

1. LLM-generated questions need review and approval before publication, whichever
   feature generated them (question generation, Ask AI, Ask AI in a channel).
2. Reports on AI-generated content are captured and routed to moderation: an AI
   response (explanation, generated batch, topic quiz, evaluation) is reported at
   ``POST /ai/responses/{id}/reports``; an AI question in a bank through the usual
   question report; both reach the moderation queue marked ``ai_generated``.
3. Objective questions are scored deterministically, never by the LLM: scoring runs
   with a provider that fails on any call, and no module on the scoring path can
   reach the LLM module.

Every test uses the offline fake provider or a scripted one; nothing calls a real model.
"""

import ast
from pathlib import Path

import pytest
from sqlalchemy import select

from app.modules import ALLOWED_DEPENDENCIES
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.service import MemberRole
from app.modules.llm.models import LlmRequest
from app.modules.llm.provider import FakeProvider
from app.modules.moderation.models import Report
from tests.test_descriptive_evaluation import ACID, FakeLlm, llm_reply, use_llm
from tests.test_descriptive_results import descriptive
from tests.test_llm_generation import DBMS, auth, make_user, use_provider
from tests.test_quiz_attempts import make_quiz, mcqs, start, submit

API = "/api/v1"
GEN = f"{API}/ai/generations"
ASK = f"{API}/ai/ask"
QUEUE = f"{API}/moderation/queue"


def report_ai(client, headers, response_id, reason="incorrect", details=None):
    return client.post(
        f"{API}/ai/responses/{response_id}/reports",
        json={"reason": reason, "details": details},
        headers=headers,
    )


def queue_item(client, headers, target_type, target_id) -> dict:
    response = client.get(QUEUE, headers=headers)
    assert response.status_code == 200, response.text
    return next(
        i for i in response.json() if (i["target_type"], i["target_id"]) == (target_type, target_id)
    )


@pytest.fixture()
def people(client, db) -> dict:
    """A student, an admin, and a Normal Channel the student is in with its owner."""
    student = make_user(db, "nfr6_student")
    owner = make_user(db, "nfr6_owner")
    make_user(db, "nfr6_other")
    make_user(db, "nfr6_admin", Role.ADMIN)
    channel = channels.create_channel(db, name="DBMS circle", subject="DBMS", created_by=owner.id)
    channels.add_member(db, channel.id, student.id, MemberRole.MEMBER)
    db.commit()
    use_provider(client, FakeProvider())
    return {
        "student": auth(client, "nfr6_student"),
        "owner": auth(client, "nfr6_owner"),
        "other": auth(client, "nfr6_other"),
        "admin": auth(client, "nfr6_admin"),
        "student_id": student.id,
        "channel": channel.id,
    }


# --------------------------------------------------------------------------- #
# 1. Review gate before publication (UT_14, IT_13)
# --------------------------------------------------------------------------- #


def _bank_ids(client, headers, **filters) -> set[int]:
    response = client.get(f"{API}/questions", params=filters, headers=headers)
    assert response.status_code == 200, response.text
    return {q["id"] for q in response.json()}


def test_UT_14_generated_questions_reach_no_bank_or_quiz_before_review(client, people):
    h = people["student"]
    batch = client.post(GEN, json=DBMS, headers=h).json()
    ids = [q["id"] for q in batch["questions"]]
    assert {q["review_status"] for q in batch["questions"]} == {"pending"}

    # Not in the bank, not in a quiz picked from it, not usable by id, no Add to Quiz.
    assert _bank_ids(client, h, topic="Normalization").isdisjoint(ids)
    preview = client.post(
        f"{API}/quizzes/preview",
        json={"topic": "Normalization", "difficulty": "medium", "question_count": 1},
        headers=h,
    )
    assert preview.status_code != 200 or not {q["id"] for q in preview.json()["questions"]} & set(
        ids
    )
    direct = client.post(
        f"{API}/quizzes",
        json={
            "title": "Sneaky",
            "difficulty": "medium",
            "duration_minutes": 10,
            "question_ids": ids,
        },
        headers=h,
    )
    assert direct.status_code >= 400
    assert client.post(f"{GEN}/{batch['id']}/quiz", json={}, headers=h).status_code == 409


def test_IT_13_only_approved_questions_are_published_and_rejected_ones_never_are(client, people):
    h = people["student"]
    batch = client.post(GEN, json=DBMS, headers=h).json()
    first, *rest = [q["id"] for q in batch["questions"]]
    client.post(f"{GEN}/{batch['id']}/questions/{first}/reject", headers=h)
    for qid in rest:
        client.post(f"{GEN}/{batch['id']}/questions/{qid}/approve", headers=h)

    bank = _bank_ids(client, h, topic="Normalization")
    assert set(rest) <= bank and first not in bank


def test_UT_14_an_ask_ai_topic_quiz_cannot_be_published_until_its_questions_are_approved(
    client, people
):
    h = people["student"]
    made = client.post(ASK, json={"query": "Give me a quiz on Binary Search Trees"}, headers=h)
    assert made.status_code == 200, made.text
    quiz = made.json()["quiz"]
    assert quiz["review_required"] is True

    refused = client.post(f"{API}/quizzes/{quiz['quiz_id']}/publish", json={}, headers=h)
    assert refused.status_code == 422, refused.text
    assert "not in the question bank" in refused.json()["detail"]

    batch = client.get(f"{GEN}/{quiz['generation_id']}", headers=h).json()
    for q in batch["questions"]:
        client.post(f"{GEN}/{batch['id']}/questions/{q['id']}/approve", headers=h)
    published = client.post(f"{API}/quizzes/{quiz['quiz_id']}/publish", json={}, headers=h)
    assert published.status_code == 200, published.text


def test_UT_14_channel_ask_ai_questions_stay_out_of_the_channel_bank_until_approved(client, people):
    h, channel = people["student"], people["channel"]
    made = client.post(
        ASK,
        json={"query": "Give me a quiz on Normalization", "channel_id": channel},
        headers=h,
    ).json()
    generated = [i["question_id"] for i in made["quiz"]["items"] if i["source"] == "generated"]
    assert generated, made
    assert _bank_ids(client, people["owner"], channel_id=channel).isdisjoint(generated)


# --------------------------------------------------------------------------- #
# 2. Reports on AI-generated content are captured and routed to moderation
# --------------------------------------------------------------------------- #


def test_a_report_on_an_ai_explanation_is_captured_and_reaches_the_moderation_queue(
    client, db, people
):
    h = people["student"]
    answer = client.post(ASK, json={"query": "Explain B-Trees with an example"}, headers=h).json()

    filed = report_ai(client, h, answer["id"], "incorrect", "The example is not a B-Tree")

    assert filed.status_code == 201, filed.text
    body = filed.json()
    assert body["status"] == "open" and body["message"] == "Report submitted for moderation"
    stored = db.get(Report, body["report_id"])
    assert (stored.target_type.value, stored.target_id, stored.reporter_id) == (
        "ai_response",
        answer["id"],
        people["student_id"],
    )
    assert stored.details == "The example is not a B-Tree"

    item = queue_item(client, people["admin"], "ai_response", answer["id"])
    assert item["content"]["ai_generated"] is True
    assert item["content"]["title"].startswith("AI explanation")
    assert item["content"]["excerpt"]  # what the AI said, for the moderator to judge
    assert item["content"]["link"] is None  # private to the requester
    assert [r["reason"] for r in item["reports"]] == ["incorrect"]

    mine = client.get(f"{API}/moderation/reports/mine", headers=h).json()
    assert mine[0]["target_type"] == "ai_response" and mine[0]["status"] == "open"


def test_an_ai_report_in_a_channel_is_routed_to_that_channels_moderators(client, people):
    h = people["student"]
    answer = client.post(
        ASK,
        json={"query": "Explain normalization", "channel_id": people["channel"]},
        headers=h,
    ).json()
    report_ai(client, h, answer["id"], "inappropriate")

    # The channel owner moderates it; a user who moderates nothing has no queue.
    item = queue_item(client, people["owner"], "ai_response", answer["id"])
    assert item["channel_id"] == people["channel"]
    assert client.get(QUEUE, headers=people["other"]).status_code == 403


def test_an_ai_report_outside_any_channel_goes_to_admins_only(client, people):
    h = people["student"]
    answer = client.post(ASK, json={"query": "Explain hashing"}, headers=h).json()
    report_ai(client, h, answer["id"])
    owner_queue = client.get(f"{QUEUE}?channel_id={people['channel']}", headers=people["owner"])
    assert all(i["target_type"] != "ai_response" for i in owner_queue.json())
    assert queue_item(client, people["admin"], "ai_response", answer["id"])["channel_id"] is None


def test_a_generated_batch_and_a_topic_quiz_can_be_reported(client, people):
    h = people["student"]
    batch = client.post(GEN, json=DBMS, headers=h).json()
    quiz = client.post(ASK, json={"query": "Give me a quiz on Heaps"}, headers=h).json()

    for response_id in (batch["id"], quiz["id"]):
        assert report_ai(client, h, response_id).status_code == 201
    batch_item = queue_item(client, people["admin"], "ai_response", batch["id"])
    assert batch_item["content"]["title"] == "AI-generated questions: Normalization"
    assert "Normalization" in batch_item["content"]["excerpt"]
    assert queue_item(client, people["admin"], "ai_response", quiz["id"])["content"][
        "title"
    ].startswith("AI topic quiz")


def test_an_llm_evaluation_can_be_reported_but_concept_matching_has_nothing_to_report(
    client, db, people
):
    author = make_user(db, "nfr6_author")
    essay = descriptive(db, author.id)
    db.commit()
    h = people["student"]
    use_llm(client, FakeLlm(llm_reply(4, ["Atomicity"], summary="Only atomicity is covered.")))
    scored = client.post(
        f"{API}/evaluations/descriptive",
        json={"question_id": essay, "answer": "Atomicity is all or nothing."},
        headers=h,
    ).json()
    assert scored["evaluator"] == "llm" and scored["ai_response_id"]
    assert report_ai(client, h, scored["ai_response_id"], "incorrect").status_code == 201
    item = queue_item(client, people["admin"], "ai_response", scored["ai_response_id"])
    assert item["content"]["excerpt"] == "Only atomicity is covered."

    use_llm(client, None)
    matched = client.post(
        f"{API}/evaluations/descriptive",
        json={"question_id": essay, "answer": "Atomicity is all or nothing."},
        headers=h,
    ).json()
    assert matched["evaluator"] == "concept-match" and matched["ai_response_id"] is None


def test_reporting_the_same_ai_response_twice_returns_the_open_report(client, db, people):
    h = people["student"]
    answer = client.post(ASK, json={"query": "Explain tries"}, headers=h).json()
    first = report_ai(client, h, answer["id"]).json()["report_id"]
    again = report_ai(client, h, answer["id"]).json()["report_id"]
    assert first == again
    assert len(db.scalars(select(Report)).all()) == 1


def test_only_the_person_the_ai_answered_can_report_it(client, people):
    answer = client.post(ASK, json={"query": "Explain AVL trees"}, headers=people["student"]).json()
    assert report_ai(client, people["other"], answer["id"]).status_code == 404
    assert report_ai(client, people["student"], 999_999).status_code == 404
    assert client.post(f"{API}/ai/responses/{answer['id']}/reports", json={}).status_code == 401
    bad = client.post(
        f"{API}/ai/responses/{answer['id']}/reports",
        json={"reason": "not-a-reason"},
        headers=people["student"],
    )
    assert bad.status_code == 422


def test_upholding_a_report_withdraws_the_ai_output(client, db, people):
    h, admin = people["student"], people["admin"]
    quiz = client.post(ASK, json={"query": "Give me a quiz on Graphs"}, headers=h).json()
    report_id = report_ai(client, h, quiz["id"], "inappropriate").json()["report_id"]

    removed = client.post(
        f"{API}/moderation/reports/{report_id}/remove",
        json={"confirm": True, "note": "Answer key is wrong"},
        headers=admin,
    )

    assert removed.status_code == 200, removed.text
    detail = removed.json()
    assert detail["status"] == "resolved" and detail["content"]["available"] is False
    assert [a["action"] for a in detail["history"]] == ["remove_content"]
    assert db.get(LlmRequest, quiz["id"]).withdrawn is True
    assert client.post(f"{ASK}/{quiz['id']}/start", headers=h).status_code == 404
    review = client.get(f"{GEN}/{quiz['quiz']['generation_id']}", headers=h)
    assert review.status_code == 200  # the batch itself was not reported

    batch = client.post(GEN, json=DBMS, headers=h).json()
    batch_report = report_ai(client, h, batch["id"]).json()["report_id"]
    client.post(
        f"{API}/moderation/reports/{batch_report}/remove", json={"confirm": True}, headers=admin
    )
    closed = client.get(f"{GEN}/{batch['id']}", headers=h)
    assert closed.status_code == 404
    assert closed.json()["detail"] == "This generated question set was withdrawn after a report"


def test_a_dismissed_ai_report_keeps_the_output(client, db, people):
    h = people["student"]
    answer = client.post(ASK, json={"query": "Explain stacks"}, headers=h).json()
    report_id = report_ai(client, h, answer["id"]).json()["report_id"]
    dismissed = client.post(
        f"{API}/moderation/reports/{report_id}/dismiss", json={}, headers=people["admin"]
    )
    assert dismissed.json()["status"] == "dismissed"
    assert db.get(LlmRequest, answer["id"]).withdrawn is False


def test_a_reported_ai_question_in_the_bank_is_marked_ai_generated(client, people):
    h = people["student"]
    batch = client.post(GEN, json=DBMS, headers=h).json()
    for q in batch["questions"]:
        client.post(f"{GEN}/{batch['id']}/questions/{q['id']}/approve", headers=h)
    qid = batch["questions"][0]["id"]

    filed = client.post(
        f"{API}/questions/{qid}/reports", json={"reason": "incorrect"}, headers=people["other"]
    )

    assert filed.status_code == 201, filed.text
    item = queue_item(client, people["admin"], "question", qid)
    assert item["content"]["ai_generated"] is True


# --------------------------------------------------------------------------- #
# 3. Objective questions are scored deterministically, never by the LLM (ST_16)
# --------------------------------------------------------------------------- #


class ExplodingProvider:
    """Fails the test if anything asks the LLM."""

    name = "exploding"

    def complete(self, task, prompt, params):
        raise AssertionError(f"objective scoring called the LLM ({task})")


def test_ST_16_objective_answers_are_scored_with_the_llm_unreachable(client, db, people):
    author = make_user(db, "nfr6_quiz_author")
    ids = mcqs(db, author.id, 3)
    essay = descriptive(db, author.id)
    quiz_id = make_quiz(db, author.id, [*ids, essay])
    use_provider(client, ExplodingProvider())
    h = people["student"]

    scores = []
    for _ in range(2):
        attempt = start(client, h, quiz_id).json()["id"]
        answers = {ids[0]: "A", ids[1]: " a ", ids[2]: "B", essay: "Atomicity only."}
        result = submit(client, h, attempt, answers, confirm=True).json()["result"]
        scores.append((result["breakdown"]["objective"]["display"], result["correct"]))

    assert scores == [("2/3 (66.7%)", 2)] * 2


def test_ST_16_an_llm_verdict_never_changes_the_objective_part(client, db, people):
    author = make_user(db, "nfr6_mixed_author")
    ids = mcqs(db, author.id, 3)
    essay = descriptive(db, author.id)
    quiz_id = make_quiz(db, author.id, [*ids, essay])
    h = people["student"]
    attempt = start(client, h, quiz_id).json()["id"]
    submit(client, h, attempt, {**dict.fromkeys(ids, "A"), essay: "weak"}, confirm=True)
    before = client.get(f"{API}/attempts/{attempt}", headers=h).json()["result"]["breakdown"]

    use_llm(client, FakeLlm(llm_reply(10, ACID, summary="Perfect")))
    client.post(f"{API}/attempts/{attempt}/evaluate", headers=h)

    after = client.get(f"{API}/attempts/{attempt}", headers=h).json()["result"]["breakdown"]
    assert (
        after["objective"]
        == before["objective"]
        == {
            "score": 3.0,
            "max_score": 3.0,
            "percentage": 100.0,
            "display": "3/3 (100%)",
        }
    )
    assert after["descriptive"]["display"] == "1/1 (100%)"


def _reachable(module: str) -> set[str]:
    seen, todo = set(), [module]
    while todo:
        for dep in ALLOWED_DEPENDENCIES[todo.pop()]:
            if dep not in seen:
                seen.add(dep)
                todo.append(dep)
    return seen


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    found |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    return found


def test_the_scoring_code_path_cannot_reach_the_llm_module():
    """The code-path review the NFR asks for, kept as a check: quizzes owns scoring, and
    neither quizzes nor anything it may call can import the LLM module."""
    assert "llm" not in _reachable("quizzes")
    for path in Path("app/modules/quizzes").glob("*.py"):
        assert not any(m.startswith("app.modules.llm") for m in _imports(path)), path
    # Evaluation refuses objective questions, so the LLM can never write their marks.
    source = Path("app/modules/quizzes/attempts.py").read_text()
    assert "objective answers are scored deterministically, never evaluated" in source
