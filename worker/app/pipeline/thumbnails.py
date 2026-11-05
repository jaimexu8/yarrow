import base64
import io

from PIL import Image

from app.core.config import settings


def build_thumbnail(page_b64: str) -> bytes:
    with Image.open(io.BytesIO(base64.b64decode(page_b64))) as image:
        image.load()
        has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
        converted = image.convert("RGBA" if has_alpha else "RGB")
    width = settings.THUMBNAIL_WIDTH_PX
    height = max(1, round(converted.height * width / converted.width))
    buffer = io.BytesIO()
    converted.resize((width, height), Image.LANCZOS).save(buffer, format="WEBP", quality=settings.THUMBNAIL_QUALITY)
    return buffer.getvalue()
