"""S08.2 - descriptive results stored beside deterministic objective scoring (issue #33).

Test Plan cases are named after their IDs: ST_15 (the descriptive score and feedback
are stored and shown in the attempt in Quiz History) and ST_16 (in a mixed quiz the MCQ
marks stay deterministic at 100 percent and the total is a clear combination of both).
Each runs with the LLM faked (``FakeLlm``) and with no LLM (concept matching).
"""

import pytest
from sqlalchemy import select

from app.modules.llm.models import LlmRequest
from app.modules.llm.provider import LlmError
from app.modules.questions import service as questions
from app.modules.questions.models import Difficulty, QuestionType
from app.modules.quizzes import service as quizzes
from app.modules.quizzes.models import AttemptAnswer, QuizAttempt, QuizQuestion
from tests.test_descriptive_evaluation import (
    ACID,
    FULL_ANSWER,
    PARTIAL_ANSWER,
    FakeLlm,
    llm_reply,
    use_llm,
)
from tests.test_quiz_attempts import API, auth, make_quiz, make_user, mcqs, start, submit

WEAK_ANSWER = "Atomicity means a transaction is all or nothing."


@pytest.fixture()
def author(db):
    return make_user(db, "mixed_author")


@pytest.fixture()
def student(client, db):
    make_user(db, "mixed_student")
    return auth(client, "mixed_student")


def descriptive(db, author_id: int, concepts=ACID) -> int:
    return questions.create_question(
        db,
        author_id=author_id,
        subject="DBMS",
        topic="Transactions",
        difficulty=Difficulty.MEDIUM,
        question_type=QuestionType.DESCRIPTIVE,
        text="Explain ACID properties",
        correct_answer="Atomicity, Consistency, Isolation and Durability ...",
        expected_concepts=list(concepts),
    ).id


@pytest.fixture()
def mixed(db, author):
    """Three MCQs (answer "A") and one descriptive ACID question, one point each."""
    mcq_ids = mcqs(db, author.id, 3)
    essay = descriptive(db, author.id)
    return make_quiz(db, author.id, [*mcq_ids, essay]), mcq_ids, essay


def take(client, headers, quiz_id, answers) -> int:
    attempt_id = start(client, headers, quiz_id).json()["id"]
    response = submit(client, headers, attempt_id, answers, confirm=True)
    assert response.status_code == 200, response.text
    return attempt_id


def evaluate(client, headers, attempt_id):
    return client.post(f"{API}/attempts/{attempt_id}/evaluate", headers=headers)


def read(client, headers, attempt_id) -> dict:
    return client.get(f"{API}/attempts/{attempt_id}", headers=headers).json()


def history(client, headers) -> dict:
    return client.get(f"{API}/attempts", headers=headers).json()


def objective_rows(db, attempt_id, mcq_ids):
    rows = db.scalars(
        select(AttemptAnswer).where(
            AttemptAnswer.attempt_id == attempt_id, AttemptAnswer.question_id.in_(mcq_ids)
        )
    )
    return sorted((r.question_id, r.response, r.is_correct, r.score) for r in rows)


# --------------------------------------------------------------------------- #
# ST_15 - the descriptive score and feedback are stored and shown in Quiz History
# --------------------------------------------------------------------------- #


def _assert_stored_in_history(client, student, attempt_id, essay):
    listed = history(client, student)
    entry = next(e for e in listed["attempts"] if e["attempt_id"] == attempt_id)
    assert entry["breakdown"]["pending_evaluation"] == 0
    opened = read(client, student, attempt_id)
    answer = next(a for a in opened["result"]["answers"] if a["question_id"] == essay)
    assert answer["status"] == "evaluated"
    assert answer["question_type"] == "descriptive"
    return entry, opened, answer


def test_ST_15_descriptive_score_and_feedback_are_shown_in_the_stored_attempt(
    client, db, student, mixed
):
    quiz_id, mcq_ids, essay = mixed
    fake = use_llm(
        client,
        FakeLlm(
            llm_reply(
                5,
                ["Atomicity", "Consistency"],
                strengths=["clear definitions"],
                weaknesses=["missing examples"],
                summary="Half of ACID is explained.",
            )
        ),
    )
    attempt_id = take(
        client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: PARTIAL_ANSWER}
    )
    assert evaluate(client, student, attempt_id).status_code == 200
    assert len(fake.calls) == 1

    entry, opened, answer = _assert_stored_in_history(client, student, attempt_id, essay)
    feedback = answer["evaluation"]
    assert feedback["score"] == 5 and feedback["max_score"] == 10
    assert feedback["display"] == "5/10"
    assert feedback["covered_concepts"] == ["Atomicity", "Consistency"]
    assert feedback["missing_concepts"] == ["Isolation", "Durability"]
    assert feedback["strengths"] == ["clear definitions"]
    assert feedback["weaknesses"] == ["missing examples"]
    assert feedback["summary"] == "Half of ACID is explained."
    assert feedback["evaluator"] == "llm" and feedback["evaluated_at"]
    assert answer["score"] == 0.5 and answer["points"] == 1
    assert answer["response"] == PARTIAL_ANSWER
    assert opened["result"]["display"] == "3.5/4 (87.5%)"
    assert entry["display"] == "3.5/4 (87.5%)"
    assert entry["breakdown"]["descriptive"]["display"] == "0.5/1 (50%)"


def test_ST_15_without_an_llm_the_concept_match_result_is_stored(client, db, student, mixed):
    quiz_id, mcq_ids, essay = mixed
    use_llm(client, None)
    attempt_id = take(client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: FULL_ANSWER})
    body = evaluate(client, student, attempt_id).json()
    assert [e["question_id"] for e in body["evaluated"]] == [essay]

    _, opened, answer = _assert_stored_in_history(client, student, attempt_id, essay)
    assert answer["evaluation"]["evaluator"] == "concept-match"
    assert answer["evaluation"]["display"] == "10/10"
    assert answer["evaluation"]["missing_concepts"] == []
    assert opened["result"]["display"] == "4/4 (100%)"


def test_the_stored_feedback_does_not_change_or_re_call_the_llm_when_reopened(
    client, db, student, mixed
):
    quiz_id, mcq_ids, essay = mixed
    fake = use_llm(client, FakeLlm(llm_reply(6, ["Atomicity", "Consistency", "Isolation"])))
    attempt_id = take(
        client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: PARTIAL_ANSWER}
    )
    evaluate(client, student, attempt_id)
    first = read(client, student, attempt_id)

    fake.reply = llm_reply(10, ACID)  # a different verdict, should anyone ask again
    again = evaluate(client, student, attempt_id).json()
    assert again["evaluated"] == [] and again["not_evaluated"] == []
    assert len(fake.calls) == 1
    assert read(client, student, attempt_id) == first
    history(client, student)
    assert len(fake.calls) == 1  # reading history never calls the LLM


# --------------------------------------------------------------------------- #
# ST_16 - MCQ marks stay deterministic; the total combines both parts clearly
# --------------------------------------------------------------------------- #


def test_ST_16_mcq_score_stays_100_percent_and_the_total_is_a_clear_combination(
    client, db, student, mixed
):
    quiz_id, mcq_ids, essay = mixed
    use_llm(client, FakeLlm(llm_reply(2, ["Atomicity"], summary="Only atomicity is covered.")))
    answers = {**dict.fromkeys(mcq_ids, "A"), essay: WEAK_ANSWER}
    attempt_id = take(client, student, quiz_id, answers)
    before = objective_rows(db, attempt_id, mcq_ids)

    body = evaluate(client, student, attempt_id).json()
    [result] = body["evaluated"]
    assert result["score"] == 2 and result["points_earned"] == 0.2

    breakdown = read(client, student, attempt_id)["result"]["breakdown"]
    assert breakdown["objective"] == {
        "score": 3.0,
        "max_score": 3.0,
        "percentage": 100.0,
        "display": "3/3 (100%)",
    }
    assert breakdown["descriptive"]["display"] == "0.2/1 (20%)"
    assert breakdown["total"]["display"] == "3.2/4 (80%)"
    assert breakdown["explanation"] == (
        "Total 3.2/4 = objective 3/3 (marked automatically against the answer key) + "
        "descriptive 0.2/1 (each written answer's 0-10 evaluation scaled to the "
        "question's points)."
    )
    assert body["breakdown"] == breakdown
    assert objective_rows(db, attempt_id, mcq_ids) == before


def test_ST_16_without_an_llm_the_mcq_part_is_the_same(client, db, student, mixed):
    quiz_id, mcq_ids, essay = mixed
    use_llm(client, None)
    attempt_id = take(client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: WEAK_ANSWER})
    evaluate(client, student, attempt_id)
    breakdown = read(client, student, attempt_id)["result"]["breakdown"]
    assert breakdown["objective"]["display"] == "3/3 (100%)"
    assert breakdown["descriptive"]["display"] == "0.25/1 (25%)"  # 1 of 4 concepts
    assert breakdown["total"]["display"] == "3.25/4 (81.2%)"


@pytest.mark.parametrize("verdict", [0, 10])
def test_the_objective_part_is_identical_whatever_the_llm_says(client, db, student, mixed, verdict):
    quiz_id, mcq_ids, essay = mixed
    use_llm(client, FakeLlm(llm_reply(verdict, ACID if verdict else ["Atomicity"])))
    answers = {mcq_ids[0]: "A", mcq_ids[1]: "B", mcq_ids[2]: "A", essay: PARTIAL_ANSWER}
    attempt_id = take(client, student, quiz_id, answers)
    submitted = read(client, student, attempt_id)["result"]["breakdown"]["objective"]
    evaluate(client, student, attempt_id)
    after = read(client, student, attempt_id)["result"]
    assert after["breakdown"]["objective"] == submitted
    assert submitted["display"] == "2/3 (66.7%)"
    assert (after["correct"], after["incorrect"]) == (2, 1)


def test_the_llm_module_cannot_write_an_objective_answer(client, db, student, mixed):
    quiz_id, mcq_ids, essay = mixed
    attempt_id = take(client, student, quiz_id, {**dict.fromkeys(mcq_ids, "B"), essay: WEAK_ANSWER})
    with pytest.raises(ValueError, match="deterministically"):
        quizzes.record_descriptive_evaluation(
            db, attempt_id, mcq_ids[0], score=10, max_score=10, feedback={}
        )
    with pytest.raises(ValueError):
        quizzes.record_descriptive_evaluation(
            db, attempt_id, essay, score=11, max_score=10, feedback={}
        )


# --------------------------------------------------------------------------- #
# The total before evaluation, scaling, and the edges of the evaluate route
# --------------------------------------------------------------------------- #


def test_before_evaluation_the_written_answer_is_pending_and_counts_zero(
    client, db, student, mixed
):
    quiz_id, mcq_ids, essay = mixed
    attempt_id = take(client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: WEAK_ANSWER})
    result = read(client, student, attempt_id)["result"]
    assert result["pending_evaluation"] == 1 and result["evaluated"] == 0
    assert result["display"] == "3/4 (75%)"
    assert result["breakdown"]["explanation"].endswith(
        "1 descriptive answer is awaiting evaluation and counting 0 until then."
    )
    entry = history(client, student)["attempts"][0]
    assert entry["breakdown"]["pending_evaluation"] == 1


def test_an_objective_only_quiz_explains_its_total_without_a_descriptive_part(
    client, db, author, student
):
    quiz_id = make_quiz(db, author.id, mcqs(db, author.id, 2))
    attempt_id = start(client, student, quiz_id).json()["id"]
    ids = [q["question_id"] for q in read(client, student, attempt_id)["questions"]]
    submit(client, student, attempt_id, dict.fromkeys(ids, "A"))
    breakdown = read(client, student, attempt_id)["result"]["breakdown"]
    assert breakdown["explanation"] == (
        "Total 2/2 = objective 2/2 (marked automatically against the answer key)."
    )
    assert breakdown["descriptive"]["max_score"] == 0


def test_the_evaluation_is_scaled_to_the_questions_points(client, db, author, student, mixed):
    quiz_id, mcq_ids, essay = mixed
    link = db.scalar(
        select(QuizQuestion).where(
            QuizQuestion.quiz_id == quiz_id, QuizQuestion.question_id == essay
        )
    )
    link.points = 5
    db.commit()
    use_llm(client, FakeLlm(llm_reply(7.5, ACID[:3])))
    attempt_id = take(
        client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: PARTIAL_ANSWER}
    )
    evaluate(client, student, attempt_id)
    result = read(client, student, attempt_id)["result"]
    assert result["breakdown"]["descriptive"]["display"] == "3.75/5 (75%)"
    assert result["display"] == "6.75/8 (84.4%)"
    assert db.get(QuizAttempt, attempt_id).score == 6.75


def test_a_blank_written_answer_is_unattempted_and_never_sent(client, db, student, mixed):
    quiz_id, mcq_ids, essay = mixed
    fake = use_llm(client, FakeLlm(llm_reply(10, ACID)))
    attempt_id = take(client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: "   "})
    body = evaluate(client, student, attempt_id).json()
    assert body["evaluated"] == [] and fake.calls == []
    result = read(client, student, attempt_id)["result"]
    essay_answer = next(a for a in result["answers"] if a["question_id"] == essay)
    assert essay_answer["status"] == "unattempted" and essay_answer["evaluation"] is None
    assert result["breakdown"]["pending_evaluation"] == 0


def test_a_failing_llm_falls_back_and_the_result_is_still_stored(client, db, student, mixed):
    quiz_id, mcq_ids, essay = mixed
    use_llm(client, FakeLlm(error=LlmError("provider down")))
    attempt_id = take(
        client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: PARTIAL_ANSWER}
    )
    [result] = evaluate(client, student, attempt_id).json()["evaluated"]
    assert result["evaluator"] == "concept-match" and result["display"] == "5/10"
    assert len(list(db.scalars(select(LlmRequest)))) == 1  # the failed call is audited


def test_an_answer_that_cannot_be_evaluated_stays_pending_with_the_reason(
    client, db, author, student
):
    [mcq] = mcqs(db, author.id, 1)
    essay = descriptive(db, author.id, concepts=())
    quiz_id = make_quiz(db, author.id, [mcq, essay])
    use_llm(client, None)
    attempt_id = take(client, student, quiz_id, {mcq: "A", essay: WEAK_ANSWER})
    body = evaluate(client, student, attempt_id).json()
    assert body["not_evaluated"] == [
        {
            "question_id": essay,
            "reason": "This question has no expected concepts to evaluate against",
        }
    ]
    assert body["breakdown"]["pending_evaluation"] == 1


def test_a_running_attempt_cannot_be_evaluated(client, db, student, mixed):
    quiz_id, _, _ = mixed
    attempt_id = start(client, student, quiz_id).json()["id"]
    response = evaluate(client, student, attempt_id)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Submit the attempt before its written answers are evaluated"
    )


def test_only_the_owner_can_evaluate_an_attempt(client, db, student, mixed):
    quiz_id, mcq_ids, essay = mixed
    attempt_id = take(client, student, quiz_id, {**dict.fromkeys(mcq_ids, "A"), essay: WEAK_ANSWER})
    make_user(db, "someone_else")
    other = auth(client, "someone_else")
    assert evaluate(client, other, attempt_id).status_code == 404
    assert evaluate(client, student, 9999).status_code == 404
    assert client.post(f"{API}/attempts/{attempt_id}/evaluate").status_code == 401
