import base64
import io

from PIL import Image

from app.pipeline.file_loaders.file_loader import FileLoader


class GIFLoader(FileLoader):
    def count_pages(self, file_data: bytes, **kwargs) -> int:
        with Image.open(io.BytesIO(file_data)) as gif:
            return getattr(gif, "n_frames", 1)

    def process_file(self, file_data: bytes, **kwargs) -> list[str]:
        return self.process_pages(
            file_data, list(range(1, self.count_pages(file_data, **kwargs) + 1)), **kwargs
        )

    def process_pages(
        self, file_data: bytes, page_numbers: list[int], **kwargs
    ) -> list[str]:
        selected = set(page_numbers)
        output: list[str] = []

        with Image.open(io.BytesIO(file_data)) as gif:
            frame_count = getattr(gif, "n_frames", 1)
            for frame_number in range(frame_count):
                if frame_number + 1 not in selected:
                    output.append("")
                    continue
                gif.seek(frame_number)

                # Convert to RGB/RGBA so all GIF modes are supported
                frame = gif.convert("RGBA")

                # Write frame as PNG into memory
                image_buffer = io.BytesIO()
                frame.save(image_buffer, format="PNG")

                output.append(
                    base64.b64encode(image_buffer.getvalue()).decode("utf-8")
                )

        return output
