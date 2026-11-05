from uuid import UUID


def thumbnail_key(document_id: UUID | str, page_number: int) -> str:
    return f"documents/{document_id}/thumbnails/{page_number}.webp"
