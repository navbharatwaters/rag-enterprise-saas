"""Extract structured metadata from parsed document content."""

import re
from dataclasses import dataclass, field


@dataclass
class DocumentMetadata:
    title: str | None = None
    author: str | None = None
    language: str | None = None
    summary: str | None = None       # first 500 chars of meaningful content
    word_count: int = 0
    has_tables: bool = False
    has_code: bool = False
    detected_topics: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# Simple heuristics — no heavy ML dependencies
_CODE_PATTERN = re.compile(
    r"(def |class |import |from .+ import|function |const |var |let |#include|public static)",
    re.M,
)
_TABLE_PATTERN = re.compile(r"\|.+\|.+\|", re.M)

# Very common topic keywords (extend as needed)
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "finance": ["revenue", "profit", "loss", "balance sheet", "cash flow", "ebitda"],
    "legal": ["agreement", "clause", "liability", "indemnif", "jurisdiction", "plaintiff"],
    "technical": ["api", "algorithm", "database", "server", "cloud", "deployment"],
    "medical": ["patient", "diagnosis", "treatment", "dosage", "clinical", "symptom"],
    "hr": ["employee", "performance", "salary", "onboarding", "recruitment", "compensation"],
}

_LANG_PATTERNS: dict[str, list[str]] = {
    "hi": ["है", "में", "का", "की", "के", "और", "को", "से", "पर"],
    "es": [" es ", " en ", " de ", " que ", " del ", " con "],
    "fr": [" est ", " les ", " des ", " avec ", " pour ", " dans "],
    "de": [" ist ", " und ", " der ", " die ", " das ", " von "],
}


class MetadataExtractor:
    """Extracts metadata from document text without heavy ML models."""

    def extract(self, content: str, filename: str, file_type: str) -> DocumentMetadata:
        lines = content.splitlines()
        stripped = content.strip()

        title = self._extract_title(lines, filename)
        author = self._extract_author(lines)
        language = self._detect_language(stripped)
        summary = self._make_summary(stripped)
        word_count = len(re.findall(r"\b\w+\b", content))
        has_tables = bool(_TABLE_PATTERN.search(content))
        has_code = bool(_CODE_PATTERN.search(content))
        topics = self._detect_topics(content.lower())

        return DocumentMetadata(
            title=title,
            author=author,
            language=language,
            summary=summary,
            word_count=word_count,
            has_tables=has_tables,
            has_code=has_code,
            detected_topics=topics,
            extra={"file_type": file_type},
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _extract_title(self, lines: list[str], filename: str) -> str | None:
        """Use first non-empty line if it looks title-like; fall back to filename."""
        for line in lines[:10]:
            stripped = line.strip()
            # Markdown heading
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip() or None
            # Short first line (≤ 120 chars, no sentence-ending punctuation)
            if 3 < len(stripped) <= 120 and not stripped.endswith((".", ":", ";")):
                return stripped
        # Fall back to filename without extension
        base = filename.rsplit(".", 1)[0] if "." in filename else filename
        return base.replace("_", " ").replace("-", " ").strip() or None

    def _extract_author(self, lines: list[str]) -> str | None:
        """Look for 'Author:', 'By:', 'Written by:' patterns in first 20 lines."""
        pattern = re.compile(r"^(?:author|by|written by)[:\s]+(.+)$", re.I)
        for line in lines[:20]:
            m = pattern.match(line.strip())
            if m:
                return m.group(1).strip()[:200]
        return None

    def _detect_language(self, text: str) -> str:
        """Simple frequency-based language detection (English default)."""
        sample = text[:2000].lower()
        best_lang = "en"
        best_count = 0
        for lang, markers in _LANG_PATTERNS.items():
            count = sum(sample.count(m) for m in markers)
            if count > best_count and count >= 3:
                best_count = count
                best_lang = lang
        return best_lang

    def _make_summary(self, text: str) -> str | None:
        """Return first ~500 chars of meaningful content."""
        # Skip very short header lines to get to body text
        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) > 40:
                return stripped[:500]
        return text[:500] if text else None

    def _detect_topics(self, lower_content: str) -> list[str]:
        hits: list[str] = []
        for topic, keywords in _TOPIC_KEYWORDS.items():
            if any(kw in lower_content for kw in keywords):
                hits.append(topic)
        return hits
