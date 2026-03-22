"""Content highlighting for search results.

Extracts snippets around matching query terms and wraps them in <em> tags.
"""

import re


# Minimum term length to highlight (skip single-character terms)
MIN_TERM_LENGTH = 2

# Number of context characters on each side of a match
CONTEXT_WINDOW = 60

# Maximum number of highlight snippets to return
MAX_HIGHLIGHTS = 3


def highlight_content(
    content: str,
    query_terms: list[str],
    max_length: int = 200,
) -> list[str]:
    """Generate highlighted snippets around query term matches.

    Args:
        content: Full chunk text to search within.
        query_terms: Individual query words to highlight.
        max_length: Maximum length of each snippet.

    Returns:
        List of snippet strings with matches wrapped in <em> tags.
        At most MAX_HIGHLIGHTS snippets returned.
    """
    if not content or not query_terms:
        return []

    # Filter out short terms
    terms = [t for t in query_terms if len(t) >= MIN_TERM_LENGTH]
    if not terms:
        return []

    content_lower = content.lower()
    highlights: list[str] = []
    used_ranges: list[tuple[int, int]] = []

    for term in terms:
        if len(highlights) >= MAX_HIGHLIGHTS:
            break

        term_lower = term.lower()
        pos = content_lower.find(term_lower)

        if pos == -1:
            continue

        # Check for overlap with existing snippets
        half_window = max_length // 2
        start = max(0, pos - half_window)
        end = min(len(content), pos + len(term) + half_window)

        if _overlaps(start, end, used_ranges):
            continue

        used_ranges.append((start, end))

        # Extract snippet
        snippet = content[start:end]

        # Add ellipsis for truncation
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."

        # Wrap all occurrences of the term in <em> tags (case-insensitive)
        snippet = re.sub(
            f"({re.escape(term)})",
            r"<em>\1</em>",
            snippet,
            flags=re.IGNORECASE,
        )

        highlights.append(snippet)

    return highlights


def extract_query_terms(query: str) -> list[str]:
    """Split a query string into individual terms for highlighting.

    Removes common stopwords and deduplicates.
    """
    # Split on whitespace and punctuation
    words = re.findall(r"\w+", query)

    # Remove duplicates (preserve order)
    seen: set[str] = set()
    terms: list[str] = []
    for w in words:
        lower = w.lower()
        if lower not in seen:
            seen.add(lower)
            terms.append(w)

    return terms


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    """Check if a range overlaps with any existing ranges."""
    for rs, re_ in ranges:
        if start < re_ and end > rs:
            return True
    return False
