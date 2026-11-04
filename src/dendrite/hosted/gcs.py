"""Signed Google Cloud Storage URLs (V4) for the blob broker.

The server signs with a service-account key (``gcs_signing_key`` in server.toml) or with its
own credentials. On Cloud Run or GKE those come from the metadata server and sign through the
IAM ``signBlob`` API, so the service account needs ``roles/iam.serviceAccountTokenCreator`` on
itself. Laptops never hold Google credentials: they get these URLs.
"""

from __future__ import annotations

import datetime
import threading
from typing import Any


class GcsSigner:
    def __init__(self, key_file: str | None = None, endpoint: str | None = None):
        import google.auth
        from google.cloud import storage
        from google.oauth2 import service_account

        if key_file:
            self.creds = service_account.Credentials.from_service_account_file(
                key_file, scopes=["https://www.googleapis.com/auth/devstorage.read_write"]
            )
            project = self.creds.project_id
        else:
            self.creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.endpoint = endpoint
        options = {"api_endpoint": endpoint} if endpoint else None
        self.client = storage.Client(credentials=self.creds, project=project or "dendrite", client_options=options)
        self._lock = threading.Lock()

    def _signing_args(self) -> dict[str, Any]:
        """A key signs locally; metadata-server credentials sign through IAM with a fresh token."""
        if hasattr(self.creds, "sign_bytes") and getattr(self.creds, "signer", None) is not None:
            return {}
        import google.auth.transport.requests

        with self._lock:
            if not self.creds.valid:
                self.creds.refresh(google.auth.transport.requests.Request())
        return {"service_account_email": self.creds.service_account_email, "access_token": self.creds.token}

    def sign(
        self,
        path: str,
        method: str,
        expires: int,
        headers: dict[str, str] | None = None,
        filename: str | None = None,
    ) -> str:
        bucket, _, key = path.partition("/")
        blob = self.client.bucket(bucket).blob(key)
        kw = self._signing_args()
        if self.endpoint:
            kw["api_access_endpoint"] = self.endpoint
        if filename:
            from ..store import content_disposition

            kw["response_disposition"] = content_disposition(filename)
        return blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(seconds=expires),
            method=method,
            headers=dict(headers) if headers else None,  # it adds Host to the dict it's given
            **kw,
        )


def crc32c_b64(path) -> str:
    """CRC32C of a file in GCS's encoding (base64 of the big-endian 32-bit value)."""
    import base64

    import google_crc32c

    c = google_crc32c.Checksum()
    with open(path, "rb") as f:
        while chunk := f.read(8 * 1024**2):
            c.update(chunk)
    return base64.b64encode(c.digest()).decode()
