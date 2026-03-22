"""S3 connector — AWS credential-based file sync."""

import asyncio
import logging
import mimetypes
from datetime import datetime
from typing import AsyncIterator

import boto3
from botocore.exceptions import ClientError

from src.connectors.base import BaseConnector, ExternalFile
from src.connectors.registry import register_connector

logger = logging.getLogger(__name__)


@register_connector
class S3Connector(BaseConnector):
    """Connector for Amazon S3 buckets."""

    connector_type = "s3"

    def _get_client(self):
        """Create an S3 client from stored credentials."""
        return boto3.client(
            "s3",
            aws_access_key_id=self.credentials["access_key"],
            aws_secret_access_key=self.credentials["secret_key"],
            region_name=self.config.get("region", "us-east-1"),
        )

    async def validate_credentials(self) -> bool:
        """Check that we can access the configured bucket."""
        try:
            client = self._get_client()
            await asyncio.to_thread(
                client.head_bucket, Bucket=self.config["bucket"]
            )
            return True
        except (ClientError, KeyError, Exception):
            logger.debug("S3 credential validation failed", exc_info=True)
            return False

    async def list_files(
        self,
        since: datetime | None = None,
    ) -> AsyncIterator[ExternalFile]:
        """List objects in the bucket, optionally filtered by prefix and modified time."""
        client = self._get_client()
        paginator = client.get_paginator("list_objects_v2")

        params = {
            "Bucket": self.config["bucket"],
            "Prefix": self.config.get("prefix", ""),
        }

        for page in await asyncio.to_thread(
            lambda: list(paginator.paginate(**params))
        ):
            for obj in page.get("Contents", []):
                # Skip directories
                if obj["Key"].endswith("/"):
                    continue

                modified = obj["LastModified"].replace(tzinfo=None)
                if since and modified < since.replace(tzinfo=None):
                    continue

                yield ExternalFile(
                    external_id=obj["Key"],
                    name=obj["Key"].rsplit("/", 1)[-1],
                    path=f"s3://{self.config['bucket']}/{obj['Key']}",
                    mime_type=self._guess_mime(obj["Key"]),
                    size_bytes=obj["Size"],
                    modified_at=modified,
                    hash=obj["ETag"].strip('"'),
                )

    async def download_file(self, file: ExternalFile) -> tuple[bytes, str]:
        """Download an object from S3."""
        client = self._get_client()
        response = await asyncio.to_thread(
            client.get_object,
            Bucket=self.config["bucket"],
            Key=file.external_id,
        )
        content = await asyncio.to_thread(response["Body"].read)
        return content, file.name

    @staticmethod
    def _guess_mime(key: str) -> str:
        """Guess MIME type from the S3 key (filename)."""
        mime, _ = mimetypes.guess_type(key)
        return mime or "application/octet-stream"
