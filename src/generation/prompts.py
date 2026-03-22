"""Prompt templates for RAG generation pipeline."""

RAG_SYSTEM_PROMPT = """You are a helpful AI assistant that answers questions based on the provided context documents.

IMPORTANT RULES:
1. Only answer based on the provided context. If the context doesn't contain enough information, say so.
2. Always cite your sources using [1], [2], etc. corresponding to the source numbers in the context.
3. Be concise and direct. Don't repeat the question or add unnecessary preamble.
4. If multiple sources support a claim, cite all of them: [1][2]
5. Never make up information not present in the context.
6. If you're unsure, express uncertainty rather than guessing.

The user will provide context documents followed by their question."""

RAG_USER_TEMPLATE = """CONTEXT DOCUMENTS:
{context}

---

QUESTION: {question}

Please answer the question based only on the context documents above. Cite sources using [1], [2], etc."""

RAG_FOLLOWUP_TEMPLATE = """CONVERSATION HISTORY:
{history}

CONTEXT DOCUMENTS:
{context}

---

FOLLOW-UP QUESTION: {question}

Please answer based on the context and conversation history. Cite sources using [1], [2], etc."""

NO_RESULTS_RESPONSE = (
    "I couldn't find relevant information in the available documents to answer "
    "your question. Please try rephrasing your query or uploading additional documents."
)


def build_messages(
    question: str,
    context: str,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Build message list for LLM call.

    Args:
        question: The user's question
        context: Formatted context string with citation markers
        history: Optional conversation history as list of {"role": ..., "content": ...}

    Returns:
        List of messages in OpenAI/Anthropic chat format
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": RAG_SYSTEM_PROMPT},
    ]

    if history:
        # Format history as a readable block
        history_text = "\n".join(
            f"{msg['role'].upper()}: {msg['content']}" for msg in history
        )
        user_content = RAG_FOLLOWUP_TEMPLATE.format(
            history=history_text,
            context=context,
            question=question,
        )
    else:
        user_content = RAG_USER_TEMPLATE.format(
            context=context,
            question=question,
        )

    messages.append({"role": "user", "content": user_content})
    return messages
