from app.pipeline.file_loaders.file_loader import FileLoader
from typing import List
import base64
import io

from PIL import Image


class GIFLoader(FileLoader):
    def process_file(self, file_data: bytes) -> List[str]:
        output: List[str] = []

        gif = Image.open(io.BytesIO(file_data))

        try:
            frame_count = gif.n_frames
        except AttributeError:
            frame_count = 1

        for frame_num in range(frame_count):
            gif.seek(frame_num)

            # Convert to RGB/RGBA so all GIF modes are supported
            frame = gif.convert("RGBA")

            # Write frame as PNG into memory
            image_buffer = io.BytesIO()
            frame.save(image_buffer, format="PNG")

            # Encode PNG as base64
            image_data = base64.b64encode(
                image_buffer.getvalue()
            ).decode("utf-8")

            output.append(image_data)

        return output