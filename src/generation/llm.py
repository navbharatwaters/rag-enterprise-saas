"""LLM client using LiteLLM for unified API access."""

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from litellm import acompletion

from src.core.config import settings
from src.generation.schemas import TokenUsage

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Non-streaming LLM response."""

    content: str
    model: str
    usage: TokenUsage


class LLMClient:
    """LLM client using LiteLLM for unified API."""

    def __init__(
        self,
        default_model: str = "",
    ):
        self.default_model = default_model or settings.DEFAULT_LLM_MODEL

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        stream: bool = False,
        metadata: dict | None = None,
    ) -> LLMResponse | AsyncGenerator[str, None]:
        """Generate LLM completion.

        Args:
            messages: Chat messages
            model: Override default model
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            stream: Whether to stream the response
            metadata: Metadata for cost tracking (e.g. tenant_id)

        Returns:
            LLMResponse for non-streaming, AsyncGenerator[str] for streaming
        """
        response = await acompletion(
            model=model or self.default_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            metadata=metadata,
            api_base=settings.LITELLM_PROXY_URL,
            api_key=settings.LITELLM_MASTER_KEY,
        )

        if stream:
            return self._stream_response(response)

        return LLMResponse(
            content=response.choices[0].message.content,
            model=response.model,
            usage=TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            ),
        )

    async def _stream_response(
        self, response: object
    ) -> AsyncGenerator[str, None]:
        """Yield streaming tokens."""
        async for chunk in response:  # type: ignore[union-attr]
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Get singleton LLMClient instance."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
