"""Compatibility exports for the application approval queue service."""

from agentos.application.approval_queue import (
    VALID_APPROVAL_MODES,
    VALID_ELEVATED_MODES,
    ApprovalQueue,
    ApprovalQueueOwnedByGatewayError,
    ApprovalSettings,
    PendingApproval,
    claim_local_approval_surface,
    get_approval_queue,
    reset_approval_queue,
)

__all__ = [
    "VALID_APPROVAL_MODES",
    "VALID_ELEVATED_MODES",
    "ApprovalQueue",
    "ApprovalQueueOwnedByGatewayError",
    "ApprovalSettings",
    "PendingApproval",
    "claim_local_approval_surface",
    "get_approval_queue",
    "reset_approval_queue",
]
