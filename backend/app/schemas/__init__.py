"""Request and response models for the v1 API.

Re-exported flat so that ``from app.schemas import DocumentOut`` keeps working
for the endpoints written against the original single-module layout.
"""

from .auth import (
    MessageResponse,
    NewPassword,
    NormalizedEmail,
    PasswordResetConfirm,
    PasswordResetRequest,
    ResendVerificationRequest,
    Token,
    UserCreate,
    UserOut,
    VerifyEmailRequest,
    normalize_email,
)
from .common import BBox, StrictModel
from .document import (
    DocumentDetail,
    DocumentOut,
    DocumentRename,
    JobOut,
    PageOut,
    ReprocessInfo,
    UploadAccepted,
    UploadRejected,
    UploadResponse,
)
from .parsed import (
    SCHEMA_VERSION,
    DocumentStats,
    DocumentTree,
    PageNode,
    RegionNode,
    WarningNode,
)
from .table import (
    MergeCandidate,
    MergeCandidatesOut,
    MergeTablesRequest,
    RowSource,
    TableCellNode,
    TableMutationResult,
    TableNode,
    TablePart,
)

__all__ = [
    "SCHEMA_VERSION",
    "BBox",
    "DocumentDetail",
    "DocumentOut",
    "DocumentRename",
    "DocumentStats",
    "DocumentTree",
    "JobOut",
    "MergeCandidate",
    "MergeCandidatesOut",
    "MergeTablesRequest",
    "MessageResponse",
    "NewPassword",
    "NormalizedEmail",
    "PageNode",
    "PageOut",
    "PasswordResetConfirm",
    "PasswordResetRequest",
    "RegionNode",
    "ReprocessInfo",
    "ResendVerificationRequest",
    "RowSource",
    "StrictModel",
    "TableCellNode",
    "TableMutationResult",
    "TableNode",
    "TablePart",
    "Token",
    "UploadAccepted",
    "UploadRejected",
    "UploadResponse",
    "UserCreate",
    "UserOut",
    "VerifyEmailRequest",
    "WarningNode",
    "normalize_email",
]
