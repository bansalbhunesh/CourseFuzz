from __future__ import annotations

from pathlib import Path
from typing import Protocol


class BlobStorage(Protocol):
    def put(self, key: str, data: bytes) -> str:
        """Stores data and returns its URI."""
        ...

    def get(self, uri: str) -> bytes | None:
        """Retrieves data by URI, returning None if not found."""
        ...


class LocalBlobStorage:
    """A file-system backed blob storage for local and testing environments."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        # Prevent path traversal
        safe_key = key.replace("..", "").lstrip("/")
        filepath = self.base_dir / safe_key
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(data)

        # In a real environment, this might be a file:// or s3:// URI
        # For simplicity, we just return a prefixed string
        return f"file://{filepath.as_posix()}"

    def get(self, uri: str) -> bytes | None:
        if not uri.startswith("file://"):
            return None
        filepath = Path(uri[7:])

        if not filepath.exists() or not filepath.is_file():
            return None

        return filepath.read_bytes()
