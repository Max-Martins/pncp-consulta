"""pncp-consulta: cliente leve para a API pública do Portal Nacional de Contratações Públicas."""

from .client import IdCompra, PNCPClient, PNCPError
from .modalidades import MODALIDADES, UFS

__all__ = ["IdCompra", "PNCPClient", "PNCPError", "MODALIDADES", "UFS"]
__version__ = "0.1.0"
