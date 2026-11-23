from abc import ABC, abstractmethod


class FileLoader(ABC):
    @abstractmethod
    def process_file(self, file_data: bytes, **kwargs):
        pass

    def count_pages(self, file_data: bytes, **kwargs) -> int:
        return 1

    def process_pages(
        self, file_data: bytes, page_numbers: list[int], **kwargs
    ) -> list[str]:
        if 1 in page_numbers:
            return self.process_file(file_data, **kwargs)
        return ["" for _ in range(self.count_pages(file_data, **kwargs))]
