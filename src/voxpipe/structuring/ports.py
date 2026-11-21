"""Provider-neutral structured extraction port."""

from __future__ import annotations

from typing import Protocol

from voxpipe.structuring.service import StructuredExtractionRequest
from voxpipe.structuring.contracts import StructuredIntelligence


class StructuredExtractionPort(Protocol):
    """An extractor returns claims; the application boundary validates all claims."""

    @property
    def provider_name(self) -> str: ...

    def extract(self, request: StructuredExtractionRequest) -> StructuredIntelligence:
        """Extract claims from source text treated as untrusted data."""
        ...
