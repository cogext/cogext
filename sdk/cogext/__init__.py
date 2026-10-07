from .client import CogextClient
from .exceptions import CogextAPIError, CogextConfigError, CogextError
from .tracker import track

Client = CogextClient

__version__ = "0.2.2"

__all__ = [
    "track",
    "CogextClient",
    "Client",
    "CogextError",
    "CogextAPIError",
    "CogextConfigError",
    "__version__",
]
