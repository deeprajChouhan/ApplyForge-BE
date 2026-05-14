from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.core.config import settings


class LocalStorageService:
    """File-system backed storage — used when ENV=dev and no S3 is available."""

    def __init__(self) -> None:
        self.base = Path(settings.upload_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def upload_bytes(self, *, content: bytes, key_prefix: str, filename: str, content_type: str | None) -> str:
        dest = self.base / key_prefix
        dest.mkdir(parents=True, exist_ok=True)
        key = f"{uuid4().hex}_{filename}"
        (dest / key).write_bytes(content)
        return f"local://{key_prefix}/{key}"

    def download_bytes(self, uri: str) -> bytes:
        _, _, tail = uri.partition("local://")
        return (self.base / tail).read_bytes()


class S3StorageService:
    def __init__(self) -> None:
        self.bucket = settings.s3_bucket
        self.client: BaseClient = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(s3={"addressing_style": "path"}),
        )

    def upload_bytes(self, *, content: bytes, key_prefix: str, filename: str, content_type: str | None) -> str:
        key = f"{key_prefix}/{uuid4().hex}_{filename}"
        extra_args: dict[str, str] = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self.client.put_object(Bucket=self.bucket, Key=key, Body=BytesIO(content), **extra_args)
        return f"s3://{self.bucket}/{key}"

    def download_bytes(self, s3_uri: str) -> bytes:
        _, _, tail = s3_uri.partition("s3://")
        bucket, _, key = tail.partition("/")
        response = self.client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()


def get_storage_service() -> S3StorageService | LocalStorageService:
    """Return local storage in dev (when S3 isn't reachable), S3 otherwise."""
    if settings.env == "dev" and settings.storage_backend == "local":
        return LocalStorageService()
    return S3StorageService()
