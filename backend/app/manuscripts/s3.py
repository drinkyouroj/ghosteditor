import boto3
from botocore.exceptions import ClientError

from app.config import settings

_s3_client = None


def get_s3_client():
    """Return a lazily-initialized, cached S3 client."""
    global _s3_client
    if _s3_client is None:
        kwargs = {
            "aws_access_key_id": settings.aws_access_key_id,
            "aws_secret_access_key": settings.aws_secret_access_key,
            "region_name": settings.aws_region,
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def ensure_bucket_exists() -> None:
    """Create the bucket if it doesn't exist (for MinIO local dev)."""
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket_name)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket_name)


def upload_to_s3(content: bytes, s3_key: str) -> None:
    """Upload file bytes to S3."""
    client = get_s3_client()
    client.put_object(
        Bucket=settings.s3_bucket_name,
        Key=s3_key,
        Body=content,
    )


def download_from_s3(s3_key: str) -> bytes:
    """Download file bytes from S3."""
    client = get_s3_client()
    response = client.get_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    return response["Body"].read()


def delete_from_s3(s3_key: str) -> None:
    """Delete a file from S3. Silently ignores if the key doesn't exist."""
    client = get_s3_client()
    try:
        client.delete_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    except ClientError:
        pass
