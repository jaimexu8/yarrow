"""Request and response payloads for manual text corrections"""

from datetime import datetime

from pydantic import Field

from .common import StrictModel

MAX_CORRECTION_LENGTH = 100_000


class RegionTextEdit(StrictModel):
    """The replacement text a user typed for one region"""

    text: str = Field(max_length=MAX_CORRECTION_LENGTH)


class RegionTextMutationResult(StrictModel):
    changed: bool
    document_updated_at: datetime | None = None
