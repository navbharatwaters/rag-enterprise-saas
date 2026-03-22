"""Document chunking with token-based splitting.

Uses LangChain's RecursiveCharacterTextSplitter with tiktoken
for accurate token counting. Generates content hashes for deduplication.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from uuid import UUID

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

CHUNK_CONFIG = {
    "chunk_size": 512,
    "chunk_overlap": 50,
    "min_chunk_size": 100,
    "separators": [
        "\n\n",  # Paragraph break
        "\n",    # Line break
        ". ",    # Sentence end
        "! ",
        "? ",
        "; ",
        ", ",
        " ",     # Word break (last resort)
    ],
}

# Tokenizer for accurate token counting (cl100k_base is used by many models)
_tokenizer: tiktoken.Encoding | None = None


def _get_tokenizer() -> tiktoken.Encoding:
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = tiktoken.get_encoding("cl100k_base")
    return _tokenizer


def count_tokens(text: str) -> int:
    """Count tokens in text using cl100k_base encoding."""
    return len(_get_tokenizer().encode(text))


def content_hash(text: str) -> str:
    """Generate SHA-256 hash of text content."""
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class ChunkData:
    """Intermediate chunk representation before database insertion."""

    document_id: UUID
    tenant_id: UUID
    content: str
    content_hash: str
    chunk_index: int
    page_number: int | None
    start_char: int | None
    end_char: int | None
    token_count: int
    metadata: dict = field(default_factory=dict)


def create_chunks(
    document_id: UUID,
    tenant_id: UUID,
    content: str,
    pages: list[dict] | None = None,
    metadata: dict | None = None,
) -> list[ChunkData]:
    """Split document content into chunks with metadata.

    Args:
        document_id: Document UUID.
        tenant_id: Tenant UUID.
        content: Full document text.
        pages: Page info from parser (optional, for page number mapping).
        metadata: Custom metadata to include in each chunk.

    Returns:
        List of ChunkData ready for embedding and storage.
    """
    if not content or not content.strip():
        return []

    tokenizer = _get_tokenizer()

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_CONFIG["chunk_size"],
        chunk_overlap=CHUNK_CONFIG["chunk_overlap"],
        separators=CHUNK_CONFIG["separators"],
        length_function=lambda t: len(tokenizer.encode(t)),
    )

    texts = splitter.split_text(content)

    chunks: list[ChunkData] = []
    search_from = 0

    for i, text in enumerate(texts):
        # Find character position in original content
        start_char = content.find(text, search_from)
        if start_char == -1:
            # Fallback: text was modified by splitter (trimmed, etc.)
            start_char = None
            end_char = None
        else:
            end_char = start_char + len(text)
            search_from = start_char + 1

        # Map to page number
        page_number = _find_page_for_position(pages or [], start_char) if start_char is not None else None

        token_count = len(tokenizer.encode(text))

        # Skip chunks below minimum size
        if token_count < CHUNK_CONFIG["min_chunk_size"] and i < len(texts) - 1:
            # Only skip if not the last chunk (last chunk can be small)
            continue

        chunk = ChunkData(
            document_id=document_id,
            tenant_id=tenant_id,
            content=text,
            content_hash=content_hash(text),
            chunk_index=len(chunks),  # Re-index after potential skips
            page_number=page_number,
            start_char=start_char,
            end_char=end_char,
            token_count=token_count,
            metadata={
                **(metadata or {}),
                "chunk_index": len(chunks),
                "total_chunks": len(texts),  # Approximate, may change after filtering
            },
        )
        chunks.append(chunk)

    # Fix total_chunks in metadata
    for chunk in chunks:
        chunk.metadata["total_chunks"] = len(chunks)

    logger.info(
        "Created %d chunks from %d chars (%d tokens)",
        len(chunks),
        len(content),
        sum(c.token_count for c in chunks),
    )
    return chunks


def _find_page_for_position(pages: list[dict], char_position: int) -> int | None:
    """Find which page a character position belongs to.

    Pages are expected to have a 'text' field with page content.
    Returns 1-indexed page number.
    """
    if not pages:
        return None

    cumulative = 0
    for i, page in enumerate(pages):
        page_length = len(page.get("text", ""))
        if cumulative + page_length > char_position:
            return i + 1
        cumulative += page_length

    return None
