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
    UserDelete,
    UserOut,
    UserUpdate,
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
from .integrations import (
    CloudConnectionOut,
    OAuthAuthorizeResponse,
    OAuthCallbackRequest,
    SyncResponse,
    SyncStatusOut,
)
from .parsed import (
    SCHEMA_VERSION,
    DocumentStats,
    DocumentTree,
    PageNode,
    RegionNode,
    WarningNode,
)
from .sharing import (
    DocumentShareOut,
    ShareDocumentRequest,
    UpdateShareRequest,
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
    "CloudConnectionOut",
    "DocumentDetail",
    "DocumentOut",
    "DocumentRename",
    "DocumentShareOut",
    "DocumentStats",
    "DocumentTree",
    "JobOut",
    "MergeCandidate",
    "MergeCandidatesOut",
    "MergeTablesRequest",
    "MessageResponse",
    "NewPassword",
    "NormalizedEmail",
    "OAuthAuthorizeResponse",
    "OAuthCallbackRequest",
    "PageNode",
    "PageOut",
    "PasswordResetConfirm",
    "PasswordResetRequest",
    "RegionNode",
    "ReprocessInfo",
    "ResendVerificationRequest",
    "RowSource",
    "ShareDocumentRequest",
    "StrictModel",
    "SyncResponse",
    "SyncStatusOut",
    "TableCellNode",
    "TableMutationResult",
    "TableNode",
    "TablePart",
    "Token",
    "UpdateShareRequest",
    "UploadAccepted",
    "UploadRejected",
    "UploadResponse",
    "UserCreate",
    "UserDelete",
    "UserOut",
    "UserUpdate",
    "VerifyEmailRequest",
    "WarningNode",
    "normalize_email",
]
