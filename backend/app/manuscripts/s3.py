import boto3
from botocore.exceptions import ClientError

from app.config import settings


def get_s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )


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
