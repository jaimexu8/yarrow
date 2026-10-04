from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
