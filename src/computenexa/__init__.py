"""ComputeNexa: private GPUs for your business, from Python.

    from computenexa import Client

    cnx = Client()                                    # reads CNX_API_KEY
    with cnx.gpu("rtx-4090", app="datascience", hours=2) as machine:
        print(machine.ssh_command)

Guide: https://app.computenexa.com/help/api · Reference: https://app.computenexa.com/help/api/reference
"""

__version__ = "0.2.0"

from .client import Client
from .errors import (
    APIConnectionError,
    AuthenticationError,
    ComputeNexaError,
    ConflictError,
    GpuLimitReachedError,
    InsufficientCreditsError,
    InvalidRequestError,
    LaunchFailedError,
    NoCapacityError,
    NotFoundError,
    PermissionDeniedError,
    ProjectInUseError,
    RateLimitError,
    ServerError,
    WaitTimeoutError,
)
from .models import (
    Account,
    App,
    Catalog,
    Gpu,
    Instance,
    LedgerEntry,
    ModelApiAddress,
    Organization,
    Project,
    Receipt,
    Schedule,
    ScheduleRun,
    SshKey,
    Usage,
    window,
)

__all__ = [
    "APIConnectionError",
    "Account",
    "App",
    "AuthenticationError",
    "Catalog",
    "Client",
    "ComputeNexaError",
    "ConflictError",
    "Gpu",
    "GpuLimitReachedError",
    "Instance",
    "InsufficientCreditsError",
    "InvalidRequestError",
    "LaunchFailedError",
    "LedgerEntry",
    "ModelApiAddress",
    "NoCapacityError",
    "NotFoundError",
    "Organization",
    "PermissionDeniedError",
    "Project",
    "ProjectInUseError",
    "RateLimitError",
    "Receipt",
    "Schedule",
    "ScheduleRun",
    "ServerError",
    "SshKey",
    "Usage",
    "WaitTimeoutError",
    "window",
]
