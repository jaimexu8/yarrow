from app.pipeline.file_loaders.file_loader import FileLoader
from typing import List
import base64

class PNGLoader(FileLoader):
    def process_file(self, file_data: bytes) -> List[str]:
        return [base64.b64encode(file_data).decode('utf-8')]