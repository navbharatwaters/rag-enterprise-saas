"""Context assembly for RAG generation pipeline."""

import tiktoken

from src.generation.schemas import Source
from src.retrieval.schemas import SearchResult


class ContextAssembler:
    """Assemble context from search results within a token budget."""

    def __init__(
        self,
        max_context_tokens: int = 6000,
        reserved_for_answer: int = 1500,
    ):
        self.max_context_tokens = max_context_tokens
        self.reserved = reserved_for_answer
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return len(self._encoding.encode(text))

    def assemble(
        self,
        results: list[SearchResult],
        question: str,
    ) -> tuple[str, list[Source]]:
        """Assemble context string and source list within token budget.

        Args:
            results: Ranked search results (already reranked or in search order)
            question: The user's question (for token budget accounting)

        Returns:
            context: Formatted context string with citation markers
            sources: List of Source models for included chunks
        """
        question_tokens = self.count_tokens(question)
        available_tokens = self.max_context_tokens - self.reserved - question_tokens

        if available_tokens <= 0:
            return "", []

        context_parts: list[str] = []
        sources: list[Source] = []
        used_tokens = 0
        separator = "\n\n---\n\n"
        separator_tokens = self.count_tokens(separator)

        for i, result in enumerate(results):
            citation_id = i + 1
            chunk_text = self._format_chunk(result, citation_id)
            chunk_tokens = self.count_tokens(chunk_text)

            # Account for separator between chunks
            extra = separator_tokens if context_parts else 0

            if used_tokens + chunk_tokens + extra > available_tokens:
                # Try truncating the chunk to fit remaining space
                remaining = available_tokens - used_tokens - extra
                if remaining > 50:  # Only include if meaningful amount fits
                    truncated = self._truncate_chunk(
                        result, citation_id, remaining
                    )
                    if truncated:
                        context_parts.append(truncated)
                        sources.append(self._result_to_source(result, citation_id))
                break

            context_parts.append(chunk_text)
            sources.append(self._result_to_source(result, citation_id))
            used_tokens += chunk_tokens + extra

        context = separator.join(context_parts)
        return context, sources

    def _format_chunk(self, result: SearchResult, citation_id: int) -> str:
        """Format a search result as a context chunk with citation marker."""
        header = f"[Source {citation_id}]"
        if result.document_title:
            header += f" {result.document_title}"
        if result.page_number is not None:
            header += f" (Page {result.page_number})"

        content = result.content or ""
        return f"{header}\n\n{content}"

    def _truncate_chunk(
        self, result: SearchResult, citation_id: int, max_tokens: int
    ) -> str | None:
        """Truncate chunk content to fit within max_tokens."""
        header = f"[Source {citation_id}]"
        if result.document_title:
            header += f" {result.document_title}"
        if result.page_number is not None:
            header += f" (Page {result.page_number})"

        header_with_newlines = f"{header}\n\n"
        header_tokens = self.count_tokens(header_with_newlines)

        content_budget = max_tokens - header_tokens
        if content_budget <= 10:
            return None

        content = result.content or ""
        tokens = self._encoding.encode(content)
        if len(tokens) > content_budget:
            tokens = tokens[:content_budget]
            content = self._encoding.decode(tokens).rstrip()
            content += "..."

        return f"{header_with_newlines}{content}"

    def _result_to_source(self, result: SearchResult, citation_id: int) -> Source:
        """Convert a SearchResult to a Source model."""
        snippet = (result.content or "")[:200]
        if len(result.content or "") > 200:
            snippet += "..."

        return Source(
            citation_id=citation_id,
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            document_title=result.document_title or "",
            filename=result.filename or "",
            page_number=result.page_number,
            content_snippet=snippet,
            relevance_score=result.score,
        )
