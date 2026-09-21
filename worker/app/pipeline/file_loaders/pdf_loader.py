from app.pipeline.file_loaders.file_loader import FileLoader
from typing import List
import base64
import fitz

class PDFLoader(FileLoader):
    def process_file(self,file_data: bytes) -> List[str]:
        output: List[str] = []
        
        doc = fitz.open(stream=file_data, filetype="pdf")
        
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            image_bytes = pix.tobytes("png")

            image_data = base64.b64encode(image_bytes).decode("utf-8")
            output.append(image_data)
            
        return output