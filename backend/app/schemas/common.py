from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Rejects unknown fields"""

    model_config = ConfigDict(extra="forbid")


class BBox(StrictModel):
    """Bounding box in page-pixel space, origin top-left"""

    x0: float
    y0: float
    x1: float
    y1: float
