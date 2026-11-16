import base64

import fitz

from app.pipeline.file_loaders.file_loader import FileLoader


class PDFLoader(FileLoader):
    def process_file(self, file_data: bytes, **kwargs) -> list[str]:
        output: list[str] = []
        password = kwargs.get("password")

        with fitz.open(stream=file_data, filetype="pdf") as doc:
            if doc.needs_pass:
                if password is None:
                    raise ValueError("PDF is encrypted. Password is required.")

                if not doc.authenticate(password):
                    raise ValueError("Incorrect PDF password.")

            for page in doc:
                pix = page.get_pixmap(dpi=150)
                image_bytes = pix.tobytes("png")

                image_data = base64.b64encode(image_bytes).decode("utf-8")
                output.append(image_data)

        return output
