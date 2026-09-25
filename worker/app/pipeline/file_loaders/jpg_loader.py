import base64

from app.pipeline.file_loaders.file_loader import FileLoader


class JPGLoader(FileLoader):
    def process_file(self, file_data: bytes) -> list[str]:
        return [base64.b64encode(file_data).decode("utf-8")]
