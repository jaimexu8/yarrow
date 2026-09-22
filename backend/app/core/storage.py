import os
from abc import ABC, abstractmethod
from typing import BinaryIO

import boto3


class StorageProvider(ABC):
    @abstractmethod
    def upload_file(self, file: BinaryIO, key: str) -> None:
        pass

    @abstractmethod
    def download_file(self, key: str) -> BinaryIO:
        pass

    @abstractmethod
    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        pass

    @abstractmethod
    def delete_file(self, key: str) -> None:
        pass

class S3StorageProvider(StorageProvider):
    def __init__(self):
        self.endpoint_url = os.getenv("S3_ENDPOINT", "http://rustfs:9000")
        self.access_key = os.getenv("S3_ACCESS_KEY", "rustfsadmin")
        self.secret_key = os.getenv("S3_SECRET_KEY", "rustfsadmin")
        self.bucket_name = os.getenv("S3_BUCKET_NAME", "yarrow-documents")
        
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key
        )

    def upload_file(self, file: BinaryIO, key: str) -> None:
        self.s3_client.upload_fileobj(file, self.bucket_name, key)

    def download_file(self, key: str) -> BinaryIO:
        from io import BytesIO
        obj = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
        return BytesIO(obj['Body'].read())

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket_name, 'Key': key},
            ExpiresIn=expires_in
        )

    def delete_file(self, key: str) -> None:
        self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)

class LocalStorageProvider(StorageProvider):
    def __init__(self):
        self.storage_dir = os.path.join(os.getcwd(), "storage")
        os.makedirs(self.storage_dir, exist_ok=True)

    def _get_path(self, key: str) -> str:
        return os.path.join(self.storage_dir, key)

    def upload_file(self, file: BinaryIO, key: str) -> None:
        path = self._get_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(file.read())

    def download_file(self, key: str) -> BinaryIO:
        path = self._get_path(key)
        return open(path, "rb")

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        # Local storage doesn't support actual presigned URLs, returning a mock format
        return f"http://localhost:8000/api/v1/documents/local/{key}"

    def delete_file(self, key: str) -> None:
        path = self._get_path(key)
        if os.path.exists(path):
            os.remove(path)

