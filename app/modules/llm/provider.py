"""LLM providers (S07.1, NFR-06).

A provider turns a prompt into text. Everything that decides whether that text is
usable (JSON shape, topic and difficulty tags, answer keys) lives in
``generation.py``, so a misbehaving model can never bypass validation.

``LLM_PROVIDER`` picks the provider:

* ``none``: AI features are switched off and report that clearly.
* ``fake``: a deterministic, offline generator. It needs no key and never touches the
  network, so tests and demos behave the same on every run.
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
