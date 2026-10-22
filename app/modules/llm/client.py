"""The LLM provider client (FR-07, FR-08, FR-09).

``LlmClient`` is the one method the LLM features need: send a system prompt and a
user prompt, get the model's text back. ``provider_client`` builds the client chosen
by ``LLM_PROVIDER`` (``anthropic`` or ``openai``), or returns ``None`` when the feature
is disabled (``LLM_PROVIDER=none`` or no ``LLM_API_KEY``). It uses only the standard
library, so no SDK is installed; tests replace the client with a fake.
"""

import json
import urllib.request
from typing import Protocol

from app.core.config import Settings

TIMEOUT_SECONDS = 20
MAX_OUTPUT_TOKENS = 1024


class LlmError(RuntimeError):
    """The provider could not be reached or answered with something unusable."""


class LlmClient(Protocol):
    def complete(self, system: str, prompt: str) -> str:
        """Return the model's text reply. Raises ``LlmError`` on any failure."""
        ...


def _post(url: str, headers: dict[str, str], body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except (OSError, ValueError) as exc:  # URLError, HTTPError and timeouts are OSErrors
        raise LlmError(f"LLM provider request failed: {exc}") from exc


class AnthropicClient:
    url = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key, self.model = api_key, model

    def complete(self, system: str, prompt: str) -> str:
        reply = _post(
            self.url,
            {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        try:
            return "".join(part["text"] for part in reply["content"] if part.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise LlmError("Unexpected reply from the LLM provider") from exc


class OpenAIClient:
    url = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key, self.model = api_key, model

    def complete(self, system: str, prompt: str) -> str:
        reply = _post(
            self.url,
            {"authorization": f"Bearer {self._api_key}"},
            {
                "model": self.model,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        try:
            return reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("Unexpected reply from the LLM provider") from exc


PROVIDERS = {"anthropic": AnthropicClient, "openai": OpenAIClient}


def provider_client(settings: Settings) -> LlmClient | None:
    """The configured provider's client, or ``None`` when the LLM is disabled."""
    if not settings.llm_enabled:
        return None
    provider = PROVIDERS.get(settings.llm_provider.lower())
    if provider is None or not settings.llm_model:
        return None
    return provider(settings.llm_api_key.get_secret_value(), settings.llm_model)
