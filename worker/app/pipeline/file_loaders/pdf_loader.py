import base64

import fitz

from app.pipeline.file_loaders.file_loader import FileLoader


class PDFLoader(FileLoader):
    @staticmethod
    def _open(file_data: bytes, password: str | None = None):
        doc = fitz.open(stream=file_data, filetype="pdf")
        if doc.needs_pass:
            if password is None:
                doc.close()
                raise ValueError("PDF is encrypted. Password is required.")
            if not doc.authenticate(password):
                doc.close()
                raise ValueError("Incorrect PDF password.")
        return doc

    def count_pages(self, file_data: bytes, **kwargs) -> int:
        with self._open(file_data, kwargs.get("password")) as doc:
            return doc.page_count

    def process_file(self, file_data: bytes, **kwargs) -> list[str]:
        return self.process_pages(
            file_data,
            list(range(1, self.count_pages(file_data, **kwargs) + 1)),
            **kwargs,
        )

    def process_pages(
        self, file_data: bytes, page_numbers: list[int], **kwargs
    ) -> list[str]:
        selected = set(page_numbers)
        output: list[str] = []

        with self._open(file_data, kwargs.get("password")) as doc:
            for index, page in enumerate(doc):
                if index + 1 not in selected:
                    output.append("")
                    continue
                pix = page.get_pixmap(dpi=150)
                output.append(
                    base64.b64encode(pix.tobytes("png")).decode("utf-8")
                )

        return output
