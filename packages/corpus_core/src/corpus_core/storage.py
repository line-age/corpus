"""The raw data lake: write-once storage for files exactly as Internet Archive served them."""

import os
import uuid
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


class RawStore(Protocol):
    def put(self, key: str, data: bytes) -> bool:
        """Store data under key unless the key exists. True if written, False if it existed."""
        ...

    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


def raw_key(collection: str, identifier: str, filename: str) -> str:
    return f"raw/{collection}/{identifier}/{filename}"


class LocalStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"key escapes store: {key}")
        return p

    def put(self, key: str, data: bytes) -> bool:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.part")
        tmp.write_bytes(data)
        try:
            os.link(tmp, p)  # fails if p exists: concurrent writers cannot overwrite
        except FileExistsError:
            return False
        finally:
            tmp.unlink(missing_ok=True)
        return True

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Store:
    def __init__(self, bucket: str, prefix: str = "", endpoint_url: str | None = None) -> None:
        import boto3

        self.bucket, self.prefix = bucket, prefix.strip("/")
        self._s3 = boto3.client("s3", endpoint_url=endpoint_url)

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> bool:
        from botocore.exceptions import ClientError

        try:  # conditional write: refused if the key already exists
            self._s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, IfNoneMatch="*")
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in {"PreconditionFailed", "412"}:
                return False
            raise
        return True

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise


def open_store(url: str, s3_endpoint_url: str | None = None) -> RawStore:
    u = urlparse(url)
    if u.scheme == "file":
        return LocalStore(u.path)
    if u.scheme == "s3":
        return S3Store(u.netloc, u.path, endpoint_url=s3_endpoint_url)
    raise ValueError(f"unsupported raw store: {url}")
