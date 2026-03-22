"""Query decomposition for complex multi-part questions."""

import json
import logging
import re

from litellm import acompletion

from src.core.config import settings

logger = logging.getLogger(__name__)


class QueryDecomposer:
    """Decompose complex queries into simpler sub-queries via LLM."""

    # Keywords that suggest a complex query needing decomposition
    COMPLEXITY_INDICATORS = [
        "compare", "difference", "between", "versus", "vs",
        "and also", "as well as", "in addition",
        "why", "how", "explain",
        "before and after", "over time", "trend",
        "multiple", "several", "various",
        "pros and cons", "advantages and disadvantages",
        "first", "second", "then", "finally",
    ]

    _PROMPT = (
        "You are a query decomposition assistant. Break down complex questions "
        "into simpler sub-queries that can be searched independently.\n\n"
        "Rules:\n"
        "1. Only decompose if the question has multiple parts or requires "
        "multiple pieces of information.\n"
        "2. Each sub-query should be self-contained and searchable.\n"
        "3. Keep sub-queries concise (under 15 words each).\n"
        "4. Return 2-5 sub-queries maximum.\n"
        "5. If the question is already simple, return it as-is in a "
        "single-item list.\n\n"
        "Question: {question}\n\n"
        "Respond ONLY with a JSON array of strings, no explanation:\n"
        '["sub-query 1", "sub-query 2", ...]'
    )

    def needs_decomposition(self, question: str) -> bool:
        """Quick heuristic check — avoids an LLM call for obviously simple queries."""
        q = question.lower()

        for indicator in self.COMPLEXITY_INDICATORS:
            if indicator in q:
                return True

        if question.count("?") > 1:
            return True

        if len(question.split()) > 20:
            return True

        # Two or more coordinating conjunctions → multiple parts
        if re.search(r"\b(and|or|but|also|plus)\b.*\b(and|or|but|also|plus)\b", q):
            return True

        return False

    async def decompose(self, question: str) -> list[str]:
        """Return a list of sub-queries for *question*.

        Falls back to ``[question]`` on any error or when the question is
        considered simple by the heuristic.
        """
        if not self.needs_decomposition(question):
            return [question]

        try:
            response = await acompletion(
                model=settings.DECOMPOSITION_MODEL,
                messages=[{"role": "user", "content": self._PROMPT.format(question=question)}],
                temperature=0,
                max_tokens=300,
                api_base=settings.LITELLM_PROXY_URL,
                api_key=settings.LITELLM_MASTER_KEY,
            )
            content = response.choices[0].message.content.strip()

            # Strip markdown code fences if present
            if content.startswith("```"):
                content = re.sub(r"^```[a-z]*\n?", "", content)
                content = re.sub(r"\n?```$", "", content)

            sub_queries: list = json.loads(content)
            if isinstance(sub_queries, list) and sub_queries:
                cleaned = [q.strip() for q in sub_queries if isinstance(q, str) and q.strip()]
                return cleaned[:5] or [question]

        except Exception as exc:
            logger.warning("Query decomposition failed: %s", exc)

        return [question]

    async def decompose_with_reasoning(self, question: str) -> dict:
        """Decompose and return a debug-friendly dict."""
        sub_queries = await self.decompose(question)
        return {
            "original": question,
            "was_decomposed": len(sub_queries) > 1,
            "sub_queries": sub_queries,
            "count": len(sub_queries),
        }


# Module-level singleton — stateless so safe to share across requests
query_decomposer = QueryDecomposer()
