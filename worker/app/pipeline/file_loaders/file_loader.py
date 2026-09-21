from abc import ABC, abstractmethod

class FileLoader(ABC):
    @abstractmethod
    def process_file(self, file_data: bytes):
        pass