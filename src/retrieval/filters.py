"""SQL filter clause builder for search queries.

Converts SearchFilters into parameterized SQLAlchemy WHERE clauses.
All filters combine with AND. Parameters are always bound (never interpolated)
to prevent SQL injection.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, text
from sqlalchemy.sql import ClauseElement

from src.retrieval.schemas import SearchFilters


def build_filter_clauses(
    filters: SearchFilters | None,
    *,
    chunk_alias: str = "c",
    doc_alias: str = "d",
) -> tuple[list[ClauseElement], dict]:
    """Build WHERE clause fragments and bind parameters from SearchFilters.

    Args:
        filters: Optional search filters to apply.
        chunk_alias: SQL alias for chunks table (default "c").
        doc_alias: SQL alias for documents table (default "d").

    Returns:
        Tuple of (list of SQL text clauses, dict of bind parameters).
        Empty list and dict if no filters provided.
    """
    if filters is None:
        return [], {}

    clauses: list[ClauseElement] = []
    params: dict = {}

    if filters.document_ids:
        clauses.append(text(f"{chunk_alias}.document_id = ANY(:filter_doc_ids)"))
        params["filter_doc_ids"] = [str(uid) for uid in filters.document_ids]

    if filters.file_types:
        clauses.append(text(f"{doc_alias}.file_type = ANY(:filter_file_types)"))
        params["filter_file_types"] = filters.file_types

    if filters.created_after is not None:
        clauses.append(text(f"{doc_alias}.created_at >= :filter_created_after"))
        params["filter_created_after"] = filters.created_after

    if filters.created_before is not None:
        clauses.append(text(f"{doc_alias}.created_at <= :filter_created_before"))
        params["filter_created_before"] = filters.created_before

    if filters.metadata:
        clauses.append(text(f"{chunk_alias}.metadata @> :filter_metadata::jsonb"))
        params["filter_metadata"] = _serialize_jsonb(filters.metadata)

    if filters.category:
        clauses.append(text(f"{doc_alias}.user_metadata @> :filter_category::jsonb"))
        params["filter_category"] = _serialize_jsonb({"category": filters.category})

    if filters.confidentiality:
        clauses.append(text(f"{doc_alias}.user_metadata @> :filter_confidentiality::jsonb"))
        params["filter_confidentiality"] = _serialize_jsonb({"confidentiality": filters.confidentiality})

    if filters.tags:
        # All requested tags must be present in the document's tags array
        for i, tag in enumerate(filters.tags):
            param_key = f"filter_tag_{i}"
            clauses.append(text(f"{doc_alias}.user_metadata -> 'tags' @> :{param_key}::jsonb"))
            params[param_key] = _serialize_jsonb([tag])

    if filters.document_date_from:
        clauses.append(text(f"{doc_alias}.user_metadata ->> 'document_date' >= :filter_doc_date_from"))
        params["filter_doc_date_from"] = filters.document_date_from

    if filters.document_date_to:
        clauses.append(text(f"{doc_alias}.user_metadata ->> 'document_date' <= :filter_doc_date_to"))
        params["filter_doc_date_to"] = filters.document_date_to

    return clauses, params


def _serialize_jsonb(metadata: dict) -> str:
    """Serialize dict to JSON string for JSONB containment query."""
    import json

    return json.dumps(metadata)
