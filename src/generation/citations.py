"""Citation extraction and validation for RAG responses."""

import re

from src.generation.schemas import Source

CITATION_PATTERN = re.compile(r"\[(\d+)\]")


def extract_citations(text: str) -> list[int]:
    """Extract citation numbers from text like [1], [2][3].

    Returns sorted unique citation IDs.
    """
    matches = CITATION_PATTERN.findall(text)
    return sorted(set(int(m) for m in matches))


def validate_citations(
    text: str,
    available_sources: list[Source],
) -> tuple[str, list[int]]:
    """Validate citations and remove invalid ones.

    Args:
        text: LLM response text with citation markers
        available_sources: Sources that were provided in context

    Returns:
        cleaned_text: Text with invalid citations removed
        valid_citations: List of valid citation IDs found
    """
    cited = extract_citations(text)
    valid_ids = {s.citation_id for s in available_sources}

    valid = [c for c in cited if c in valid_ids]
    invalid = [c for c in cited if c not in valid_ids]

    cleaned = text
    for inv in invalid:
        cleaned = re.sub(rf"\[{inv}\]", "", cleaned)

    # Clean up multiple spaces
    cleaned = re.sub(r"  +", " ", cleaned)
    # Clean up space before punctuation
    cleaned = re.sub(r" ([.,;:!?])", r"\1", cleaned)
    cleaned = cleaned.strip()

    return cleaned, valid


def filter_used_sources(
    sources: list[Source],
    valid_citations: list[int],
) -> list[Source]:
    """Filter sources to only those actually cited in the response.

    Args:
        sources: All available sources
        valid_citations: Citation IDs found in the response

    Returns:
        List of sources that were actually cited
    """
    cited_set = set(valid_citations)
    return [s for s in sources if s.citation_id in cited_set]
