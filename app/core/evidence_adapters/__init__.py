"""V1.7 – Evidence source adapters package.

Adapters convert external events to normalised Evidence records.
Each adapter implements the EvidenceAdapter protocol.
"""
from app.core.evidence_adapters.base import EvidenceAdapter, NormalisedEvidence
from app.core.evidence_adapters.webhook import GenericWebhookAdapter

__all__ = ["EvidenceAdapter", "NormalisedEvidence", "GenericWebhookAdapter"]
