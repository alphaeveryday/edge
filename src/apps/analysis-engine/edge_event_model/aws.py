"""S3 / Secrets Manager helpers for cloud inference.

Model weights + FinBERT embedding cache live in S3 (``models/current/``); the
OpenAI key lives in Secrets Manager. All helpers degrade gracefully (return /
no-op) when boto3 or the resource is unavailable, so local runs are unaffected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config

MODEL_FILES = ("news_nn.pt", "spread_model.joblib", "factor_latest.parquet", "meta.json")


def _download(key_name: str, dest: Path) -> None:
    """Download ``{ARTIFACT_S3_PREFIX}/{key_name}`` -> ``dest`` (boto3, aws-cli fallback)."""
    bucket, prefix = config.ARTIFACT_S3_BUCKET, config.ARTIFACT_S3_PREFIX
    key = f"{prefix}/{key_name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import boto3

        boto3.client("s3", region_name=config.AWS_REGION).download_file(bucket, key, str(dest))
    except Exception:
        import subprocess

        subprocess.run(
            ["aws", "s3", "cp", f"s3://{bucket}/{key}", str(dest), "--region", config.AWS_REGION],
            check=True,
        )


def ensure_artifacts(dest_dir: str | Path) -> Path:
    """Ensure the 4 model files exist locally in ``dest_dir`` (pull missing from S3)."""
    d = Path(dest_dir)
    for name in MODEL_FILES:
        if not (d / name).exists():
            _download(name, d / name)
    return d


def ensure_embed_cache(dest_path: str | Path = config.EMBED_CACHE) -> Path:
    """Ensure the FinBERT embedding cache parquet exists locally (pull from S3 if missing)."""
    p = Path(dest_path)
    if not p.exists():
        _download(p.name, p)
    return p


def openai_key() -> str | None:
    """Fetch the OpenAI key from Secrets Manager (``{"apikey": ...}``); None on failure."""
    try:
        import boto3

        raw = boto3.client("secretsmanager", region_name=config.AWS_REGION).get_secret_value(
            SecretId=config.OPENAI_SECRET_ID
        )["SecretString"]
        return json.loads(raw).get("apikey") if raw.strip().startswith("{") else raw.strip()
    except Exception:
        return None
