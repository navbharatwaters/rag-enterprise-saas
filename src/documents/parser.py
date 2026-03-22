"""Document parsing via Docling service with text file fallback.

Docling handles PDF, DOCX, HTML. Plain text and markdown files
are parsed directly without Docling.
"""

import logging
from dataclasses import dataclass, field

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

# File types that don't need Docling
TEXT_TYPES = {"txt", "md"}

# File types that require Docling
DOCLING_TYPES = {"pdf", "docx", "html"}


@dataclass
class ParsedDocument:
    """Result of parsing a document."""

    content: str
    pages: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    page_count: int = 0
    word_count: int = 0


class DoclingParser:
    """Client for the Docling document parsing service."""

    def __init__(self, base_url: str | None = None, timeout: float = 300.0):
        self.base_url = (base_url or settings.DOCLING_URL).rstrip("/")
        self.timeout = timeout

    async def parse(
        self, file_bytes: bytes, file_type: str, filename: str = "document"
    ) -> ParsedDocument:
        """Parse a document and extract text content.

        For text/markdown files, parses directly without Docling.
        For PDF/DOCX/HTML, sends to Docling service.

        Args:
            file_bytes: Raw file contents.
            file_type: File extension without dot (e.g. "pdf", "txt").
            filename: Original filename for Docling.

        Returns:
            ParsedDocument with extracted content.
        """
        if file_type in TEXT_TYPES:
            return self._parse_text(file_bytes, file_type)

        return await self._parse_with_docling(file_bytes, file_type, filename)

    def _parse_text(self, file_bytes: bytes, file_type: str) -> ParsedDocument:
        """Parse plain text or markdown files directly."""
        content = file_bytes.decode("utf-8", errors="replace")
        return ParsedDocument(
            content=content,
            page_count=1,
            word_count=len(content.split()),
        )

    async def _parse_with_docling(
        self, file_bytes: bytes, file_type: str, filename: str
    ) -> ParsedDocument:
        """Send document to Docling service for parsing."""
        ext = f".{file_type}" if not file_type.startswith(".") else file_type
        upload_filename = f"{filename}{ext}" if not filename.endswith(ext) else filename

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1alpha/convert/file",
                files={"files": (upload_filename, file_bytes)},
            )
            response.raise_for_status()
            result = response.json()

        # Docling v1alpha response: {"document": {"md_content": ..., "text_content": ...}, ...}
        doc = result.get("document", {}) if isinstance(result, dict) else {}
        if not doc:
            logger.warning("Unexpected Docling response format: %s", type(result))
            return self._parse_text(file_bytes, file_type)

        # Prefer markdown, fall back to plain text
        content = doc.get("md_content") or doc.get("text_content") or ""
        pages = []
        tables = []
        metadata = {}

        return ParsedDocument(
            content=content,
            pages=pages,
            tables=tables,
            metadata=metadata,
            page_count=len(pages) if pages else max(1, content.count("\f") + 1),
            word_count=len(content.split()),
        )

    async def health(self) -> bool:
        """Check if Docling service is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False
