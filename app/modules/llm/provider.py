"""LLM providers (S07.1, NFR-06).

A provider turns a prompt into text. Everything that decides whether that text is
usable (JSON shape, topic and difficulty tags, answer keys) lives in
``generation.py``, so a misbehaving model can never bypass validation.

``LLM_PROVIDER`` picks the provider:

* ``none``: AI features are switched off and report that clearly.
* ``fake``: a deterministic, offline generator of questions and Ask-AI explanations.
  It needs no key and never touches the network, so tests and demos behave the same
  on every run.
* ``openai``: any OpenAI-compatible chat completions API (``LLM_BASE_URL`` to override).
* ``anthropic``: the Anthropic Messages API.

Keys are read from configuration only and never logged or echoed in errors.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from app.core.config import Settings
from app.modules.llm.models import LlmTask


class LlmError(Exception):
    """The provider failed or returned something unusable."""


class LlmTimeoutError(LlmError):
    """The provider did not answer within ``LLM_TIMEOUT_SECONDS``."""


class LlmProvider(Protocol):
    name: str

    def complete(self, task: LlmTask, prompt: str, params: dict[str, Any]) -> str:
        """Return the model's raw text answer to ``prompt``.

        ``params`` repeats the structured request (subject, topic, difficulty, count,
        ...) for providers, like the fake one, that do not read prose.
        """
        ...


SYSTEM_PROMPT = (
    "You write accurate exam questions for university students. "
    "Reply with JSON only, exactly in the format requested, with no commentary."
)


class FakeProvider:
    """Deterministic question generator used with ``LLM_PROVIDER=fake`` and in tests.

    The same request always yields the same questions; ``params["round"]`` and
    ``params["offset"]`` shift the numbering so retries and follow-up requests do not
    repeat earlier questions.
    """

    name = "fake"

    def complete(self, task: LlmTask, prompt: str, params: dict[str, Any]) -> str:
        if task is LlmTask.EXPLAIN:
            return json.dumps(fake_explanation(params["topic"]))
        if task not in (LlmTask.GENERATE_QUESTIONS, LlmTask.ADAPT_DIFFICULTY):
            raise LlmError(f"the fake provider does not support {task.value}")
        subject, topic = params["subject"], params["topic"]
        difficulty, count = params["difficulty"], int(params["count"])
        start = int(params.get("offset", 0)) + 100 * int(params.get("round", 0)) + 1
        items = []
        for n in range(start, start + count):
            correct = n % 4
            options = [f"{topic} statement {n}.{k + 1}" for k in range(4)]
            items.append(
                {
                    "subject": subject,
                    "topic": topic,
                    "difficulty": difficulty,
                    "question": (
                        f"[{difficulty.title()}] {subject} / {topic} practice question {n}: "
                        f"which statement about {topic} is correct?"
                    ),
                    "options": options,
                    "correct_answer": options[correct],
                    "explanation": (
                        f"{options[correct]} is the correct statement about {topic}; "
                        "the other options describe common misconceptions."
                    ),
                }
            )
        return json.dumps({"questions": items})


_FAKE_GLOSSARY: dict[str, tuple[str, str]] = {
    "b-trees": (
        "A B-Tree is a balanced search tree whose nodes hold many sorted keys, so it stays "
        "shallow and a lookup touches only a few disk pages.",
        "With up to 3 keys per node, inserting 10, 20, 30 and 40 splits the full node: 20 "
        "moves up as the new root with [10] and [30, 40] as its children.",
    ),
    "normalization": (
        "Normalization organises database tables so each fact is stored once, removing "
        "redundancy and the update, insert and delete anomalies it causes.",
        "A table Orders(order_id, customer_name, customer_city) repeats the city on every "
        "order; moving customers into their own table stores each city once (3NF).",
    ),
    "indexing": (
        "Indexing builds an extra sorted structure over a column so the database can find "
        "matching rows without scanning the whole table.",
        "An index on students(email) lets WHERE email = 'a@x.com' jump straight to one row "
        "instead of reading all 50,000 rows.",
    ),
    "binary search trees": (
        "A binary search tree keeps smaller keys in the left subtree and larger keys in the "
        "right subtree, so each comparison discards half of the remaining tree.",
        "Inserting 8, 3, 10, 1 and 6 puts 3 left of 8, 10 right of 8, then 1 left of 3 and "
        "6 right of 3; searching for 6 visits only 8, 3 and 6.",
    ),
}


def fake_explanation(topic: str) -> dict[str, Any]:
    """A deterministic, topic-specific explanation with one worked example."""
    key = " ".join(topic.lower().split())
    known = _FAKE_GLOSSARY.get(key) or _FAKE_GLOSSARY.get(key.removesuffix("s"))
    if known is None:
        known = (
            f"{topic} in simple terms: start with the problem {topic} solves, then the "
            f"idea behind how {topic} works, and finally when you would choose {topic}.",
            f"Worked example for {topic}: take one small, concrete case, apply {topic} "
            "step by step and check the result against what you expected.",
        )
    explanation, example = known
    return {"topic": topic, "explanation": explanation, "examples": [example]}


class HttpProvider:
    """A hosted model reached over HTTPS with the standard library (no extra deps)."""

    def __init__(self, kind: str, *, model: str, api_key: str, base_url: str, timeout: float):
        self.name = kind
        self._model, self._key, self._timeout = model, api_key, timeout
        default = (
            "https://api.anthropic.com/v1" if kind == "anthropic" else ("https://api.openai.com/v1")
        )
        self._base = (base_url or default).rstrip("/")

    def _request(self, prompt: str) -> urllib.request.Request:
        if self.name == "anthropic":
            body = {
                "model": self._model,
                "max_tokens": 4096,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"}
            url = f"{self._base}/messages"
        else:
            body = {
                "model": self._model,
                "temperature": 0.4,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            }
            headers = {"Authorization": f"Bearer {self._key}"}
            url = f"{self._base}/chat/completions"
        headers["Content-Type"] = "application/json"
        return urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)

    def _text(self, payload: dict[str, Any]) -> str:
        if self.name == "anthropic":
            return "".join(
                part.get("text", "") for part in payload["content"] if part.get("type") == "text"
            )
        return payload["choices"][0]["message"]["content"]

    def complete(self, task: LlmTask, prompt: str, params: dict[str, Any]) -> str:
        try:
            with urllib.request.urlopen(self._request(prompt), timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode())
        except TimeoutError as exc:
            raise LlmTimeoutError("the AI service timed out") from exc
        except urllib.error.HTTPError as exc:
            raise LlmError(f"the AI service answered with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LlmTimeoutError("the AI service timed out") from exc
            raise LlmError("the AI service could not be reached") from exc
        except (ValueError, OSError) as exc:
            raise LlmError("the AI service sent an unreadable response") from exc
        try:
            return self._text(payload)
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("the AI service sent an unexpected response") from exc


def provider_from_settings(settings: Settings) -> LlmProvider | None:
    """The configured provider, or ``None`` when AI features are switched off."""
    kind = settings.llm_provider.strip().lower()
    if kind == "fake":
        return FakeProvider()
    if kind in {"openai", "anthropic"} and settings.llm_api_key is not None:
        return HttpProvider(
            kind,
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
        )
    return None
