from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.core.config import settings


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
