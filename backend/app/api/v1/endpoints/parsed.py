import json
import logging
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps import DocumentAccess, require_completed, require_read_access
from app.schemas.parsed import DocumentTree
from app.services.document_tree import load_document_tree
from app.services.export import content_disposition, display_stem, safe_stem
from app.services.markdown import RenderOptions, render_markdown, render_plain_text


class ExportFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{document_id}/parsed", response_model=DocumentTree)
async def get_parsed_document(
    page: int | None = Query(
        default=None,
        ge=1,
        description=("Restrict to one 1-based page. Tables are still returned whole, " "including parts on other pages."),
    ),
    include_cells: bool = Query(default=True),
    include_warnings: bool = Query(default=True),
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """The document's pages, regions and logical tables.

    Returns 200 for a document that has not been processed too, with empty pages.
    """
    if page is not None and page > (access.document.page_count or 0):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document has no page {page}",
        )

    return await load_document_tree(
        db,
        access,
        page=page,
        include_cells=include_cells,
        include_warnings=include_warnings,
    )


@router.get(
    "/{document_id}/markdown",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/markdown": {}}}},
)
async def get_document_markdown(
    page: int | None = Query(default=None, ge=1),
    include_artifacts: bool = Query(
        default=True,
        description="Headers, footers and page numbers. On for the viewer, off for exports.",
    ),
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Render the document as markdown, based on the tree of parsed objects"""

    tree = await load_document_tree(db, access, page=page, include_warnings=False)
    text = render_markdown(
        tree,
        RenderOptions(include_artifacts=include_artifacts, page_separators=page is None),
    )
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.get("/{document_id}/export")
async def export_document(
    export_format: ExportFormat = Query(
        alias="format",
        description="No default: an unlabeled export is a support ticket.",
    ),
    page: int | None = Query(default=None, ge=1),
    include_artifacts: bool = Query(
        default=False,
        description="Off by default here: repeated running heads make an export unreadable.",
    ),
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Download the extracted content as JSON, Markdown or plain text"""
    require_completed(access)

    tree = await load_document_tree(db, access, page=page, include_warnings=export_format is ExportFormat.JSON)
    fallback = f"document-{access.document.id}"
    extension, media_type, body = _serialize(tree, export_format, include_artifacts)
    filename = f"{safe_stem(access.document.filename, fallback)}.{extension}"
    unicode_filename = f"{display_stem(access.document.filename, fallback)}.{extension}"

    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(
                filename,
                disposition="attachment",
                unicode_filename=unicode_filename,
            )
        },
    )


def _serialize(tree: DocumentTree, export_format: ExportFormat, include_artifacts: bool) -> tuple[str, str, str]:
    """Return (extension, media type, body) for one export format."""
    options = RenderOptions(include_artifacts=include_artifacts)
    if export_format is ExportFormat.JSON:
        return (
            "json",
            "application/json",
            json.dumps(tree.model_dump(mode="json"), indent=2, ensure_ascii=False),
        )
    if export_format is ExportFormat.MARKDOWN:
        return "md", "text/markdown; charset=utf-8", render_markdown(tree, options)
    return "txt", "text/plain; charset=utf-8", render_plain_text(tree, options)
