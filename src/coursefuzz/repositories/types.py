from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactRecord:
    filename: str
    sha256: str
    content: bytes
