"""
Wallet Module - Gestion sécurisée du wallet
"""

from .secure_wallet import SecureWallet
from .encryption import WalletEncryption

__all__ = [
    "SecureWallet",
    "WalletEncryption",
]
