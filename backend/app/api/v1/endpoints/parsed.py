import io
import json
import logging
import zipfile
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_storage import ObjectNotFoundError, get_storage

from app.core.database import get_db
from app.deps import DocumentAccess, require_completed, require_read_access
from app.schemas.parsed import DocumentTree
from app.services.document_tree import load_document_tree
from app.services.export import content_disposition, display_stem, safe_stem
from app.services.markdown import (
    RenderOptions,
    render_csv,
    render_markdown,
    render_plain_text,
)


class ExportFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    TEXT = "text"
    CSV = "csv"


# Only these formats can carry references to the image paths in the archive.
REFERENCE_FORMATS = {ExportFormat.JSON, ExportFormat.MARKDOWN}


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/{document_id}/parsed", response_model=DocumentTree)
async def get_parsed_document(
    page: int | None = Query(
        default=None,
        ge=1,
        description=(
            "Restrict to one 1-based page. Tables are still returned whole, "
            "including parts on other pages."
        ),
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
        RenderOptions(
            include_artifacts=include_artifacts, page_separators=page is None
        ),
    )
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


def _create_zip(files: dict[str, bytes]) -> bytes:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for filename, data in files.items():
            zip_file.writestr(filename, data)
    return zip_buffer.getvalue()


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
    """Download the extracted content as JSON, Markdown, plain text, or CSV"""
    require_completed(access)

    tree = await load_document_tree(
        db, access, page=page, include_warnings=export_format is ExportFormat.JSON
    )

    # Images are included for every format, but only reference-capable formats
    # have their serialized content updated to use the archive paths.
    images = await _collect_images(
        tree, rewrite_references=export_format in REFERENCE_FORMATS
    )

    fallback = f"document-{access.document.id}"
    extension, media_type, body = _serialize(tree, export_format, include_artifacts)
    filename = f"{safe_stem(access.document.filename, fallback)}.{extension}"
    unicode_filename = f"{display_stem(access.document.filename, fallback)}.{extension}"

    if images:
        files = {filename: body.encode("utf-8"), **images}
        filename = f"{safe_stem(access.document.filename, fallback)}.zip"
        unicode_filename = f"{display_stem(access.document.filename, fallback)}.zip"
        body, media_type = (
            await run_in_threadpool(_create_zip, files),
            "application/zip",
        )

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


async def _collect_images(
    tree: DocumentTree, *, rewrite_references: bool
) -> dict[str, bytes]:
    """Fetch figure images and optionally point their regions at archive paths."""
    regions = [
        region for page in tree.pages for region in page.regions if region.image_key
    ]
    paths: dict[str, str | None] = {}
    files: dict[str, bytes] = {}
    for region in regions:
        key = region.image_key
        if key not in paths:
            try:
                data = await run_in_threadpool(get_storage().download_bytes, key)
            except ObjectNotFoundError:
                logger.warning(
                    f"Figure image {key} is missing from storage; left out of the export"
                )
                paths[key] = None
            else:
                paths[key] = f"images/figure-{len(files) + 1}.{key.rsplit('.', 1)[-1]}"
                files[paths[key]] = data
        if rewrite_references:
            region.image_key = paths[key]
    return files


def _serialize(
    tree: DocumentTree, export_format: ExportFormat, include_artifacts: bool
) -> tuple[str, str, str]:
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
    if export_format is ExportFormat.CSV:
        return "csv", "text/csv; charset=utf-8", render_csv(tree)
    return "txt", "text/plain; charset=utf-8", render_plain_text(tree, options)
