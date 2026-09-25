"""Object store providers, shared by the backend (writes) and worker (reads)."""

import os
from abc import ABC, abstractmethod
from functools import lru_cache
from io import BytesIO
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import storage_settings

# get_object returns these for a key that is not there. Which one depends on
# whether the caller may also ListBucket, so both have to be handled.
_MISSING_OBJECT_CODES = {"NoSuchKey", "404", "NoSuchBucket"}


class ObjectNotFoundError(Exception):
    """The key does not exist.

    Distinct from every other storage failure because it is permanent: callers
    retry a timeout or a 503, but a missing object never becomes present, and
    retrying one only delays the failure the user is waiting on. Raising a
    provider-level exception keeps botocore error codes out of the callers.
    """


class StorageProvider(ABC):
    @abstractmethod
    def upload_file(self, file: BinaryIO, key: str) -> None:
        pass

    @abstractmethod
    def download_file(self, key: str) -> BinaryIO:
        pass

    @abstractmethod
    def download_bytes(self, key: str) -> bytes:
        """Read a whole object into memory.

        The ingestion pipeline is written against bytes throughout --
        filetype.guess, fitz.open(stream=...), PIL -- so streaming would only
        be re-buffered by the first thing that touched it.
        """

    @abstractmethod
    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        pass

    @abstractmethod
    def delete_file(self, key: str) -> None:
        pass


class S3StorageProvider(StorageProvider):
    def __init__(self) -> None:
        self.bucket_name = storage_settings.S3_BUCKET_NAME
        self.s3_client = boto3.client(
            "s3",
            # `or None` so that S3_ENDPOINT="" from an env file is treated as
            # unset rather than as an empty endpoint, which boto3 rejects.
            endpoint_url=storage_settings.S3_ENDPOINT or None,
            aws_access_key_id=storage_settings.S3_ACCESS_KEY,
            aws_secret_access_key=storage_settings.S3_SECRET_KEY,
            region_name=storage_settings.S3_REGION,
            config=Config(
                # MinIO serves buckets as a path segment and rejects the
                # virtual-host style boto3 prefers against AWS; AWS accepts
                # path style, so one setting covers both.
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def upload_file(self, file: BinaryIO, key: str) -> None:
        self.s3_client.upload_fileobj(file, self.bucket_name, key)

    def download_file(self, key: str) -> BinaryIO:
        return BytesIO(self.download_bytes(key))

    def download_bytes(self, key: str) -> bytes:
        try:
            obj = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_OBJECT_CODES:
                raise ObjectNotFoundError(f"s3://{self.bucket_name}/{key}") from exc
            raise
        return obj["Body"].read()

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        return self.s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete_file(self, key: str) -> None:
        self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)


class LocalStorageProvider(StorageProvider):
    """Disk-backed provider for running a single service without MinIO.

    Only usable when every service that touches a key shares the directory --
    in compose they do not, so USE_LOCAL_STORAGE is for local runs outside it.
    """

    def __init__(self) -> None:
        self.storage_dir = os.path.abspath(storage_settings.LOCAL_STORAGE_DIR)
        os.makedirs(self.storage_dir, exist_ok=True)

    def _get_path(self, key: str) -> str:
        # Resolve before use: a key is derived from a user-supplied filename,
        # and "../" in one would otherwise write outside storage_dir.
        path = os.path.abspath(os.path.join(self.storage_dir, key))
        if os.path.commonpath([path, self.storage_dir]) != self.storage_dir:
            raise ValueError(f"Key escapes the storage directory: {key}")
        return path

    def upload_file(self, file: BinaryIO, key: str) -> None:
        path = self._get_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(file.read())

    def download_file(self, key: str) -> BinaryIO:
        try:
            return open(self._get_path(key), "rb")
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc

    def download_bytes(self, key: str) -> bytes:
        with self.download_file(key) as handle:
            return handle.read()

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        # Local storage has no signing; the backend serves these itself.
        return f"/api/v1/documents/local/{key}"

    def delete_file(self, key: str) -> None:
        path = self._get_path(key)
        if os.path.exists(path):
            os.remove(path)


@lru_cache(maxsize=1)
def get_storage() -> StorageProvider:
    """The process-wide provider.

    Cached because the worker fans pages out with asyncio.to_thread and would
    otherwise build a client per call. boto3 clients are thread-safe to share
    (resources are not); this returns a client, so sharing one is correct.
    """
    if storage_settings.USE_LOCAL_STORAGE:
        return LocalStorageProvider()
    return S3StorageProvider()
