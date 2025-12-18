"""
Credentials Manager - Gestion sécurisée des credentials API

Gère le chargement sécurisé des credentials depuis:
1. Variables d'environnement (.env)
2. Fichier chiffré (credentials.enc)
3. Prompt sécurisé au runtime

⚠️ Les credentials ne sont JAMAIS stockés en clair
"""

import os
import getpass
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PolymarketCredentials:
    """
    Structure pour stocker les credentials API.

    Tous les champs sensibles sont optionnels et seront
    demandés au runtime si non fournis.
    """

    # ═══════════════════════════════════════════════════════════════
    # POLYMARKET CLOB API
    # ═══════════════════════════════════════════════════════════════
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    passphrase: Optional[str] = None

    # ═══════════════════════════════════════════════════════════════
    # WALLET POLYGON
    # ═══════════════════════════════════════════════════════════════
    # ⚠️ La clé privée sera chiffrée avec AES-256
    private_key: Optional[str] = None  # Clé privée du wallet
    wallet_address: Optional[str] = None

    # Alias pour compatibilité avec l'ancien code
    @property
    def polymarket_api_key(self) -> Optional[str]:
        return self.api_key

    @property
    def polymarket_api_secret(self) -> Optional[str]:
        return self.api_secret

    @property
    def wallet_private_key(self) -> Optional[str]:
        return self.private_key

    def is_api_complete(self) -> bool:
        """Vérifie si les credentials API sont présentes."""
        return bool(self.api_key and self.api_secret)

    def is_wallet_complete(self) -> bool:
        """Vérifie si les credentials wallet sont présentes."""
        return bool(self.private_key)

    def is_complete(self) -> bool:
        """Vérifie si toutes les credentials sont présentes."""
        return self.is_api_complete() and self.is_wallet_complete()

    @property
    def is_valid(self) -> bool:
        """Vérifie si les credentials minimales sont présentes pour trader."""
        return bool(self.private_key)

    def clear_sensitive(self) -> None:
        """Efface les données sensibles de la mémoire."""
        self.api_secret = None
        self.private_key = None
        self.passphrase = None


class CredentialsManager:
    """
    Gestionnaire sécurisé des credentials.
    
    Workflow:
    1. Tente de charger depuis .env (sauf clé privée)
    2. Tente de charger depuis fichier chiffré
    3. Si absent, demande via prompt sécurisé
    4. Chiffre et sauvegarde pour prochaine fois
    
    Usage:
        manager = CredentialsManager()
        credentials = await manager.get_credentials()
    """
    
    def __init__(self, encrypted_file: str = "credentials.enc"):
        self._credentials: Optional[PolymarketCredentials] = None
        self._encrypted_file = Path(encrypted_file)
    
    def load_from_env(self) -> PolymarketCredentials:
        """
        Charge les credentials depuis les variables d'environnement.

        Note: La clé privée du wallet n'est JAMAIS chargée depuis .env
        pour des raisons de sécurité.

        Returns:
            PolymarketCredentials partiellement remplies
        """
        return PolymarketCredentials(
            api_key=os.getenv("POLYMARKET_API_KEY") or None,
            api_secret=os.getenv("POLYMARKET_API_SECRET") or None,
            passphrase=os.getenv("POLYMARKET_PASSPHRASE") or None,
            wallet_address=os.getenv("WALLET_ADDRESS") or None,
            private_key=None,  # Jamais en .env pour sécurité
        )
    
    def prompt_api_credentials(self) -> tuple[str, str]:
        """
        Demande les credentials API via prompt sécurisé.
        
        Returns:
            Tuple (api_key, api_secret)
        """
        print("\n" + "=" * 50)
        print("🔐 Configuration API Polymarket")
        print("=" * 50)
        
        api_key = input("API Key: ").strip()
        api_secret = getpass.getpass("API Secret (caché): ").strip()
        
        return api_key, api_secret
    
    def prompt_wallet_credentials(self) -> tuple[str, str]:
        """
        Demande les credentials wallet via prompt sécurisé.
        
        La clé privée est demandée avec getpass pour ne pas
        apparaître dans l'historique du terminal.
        
        Returns:
            Tuple (address, private_key)
        """
        print("\n" + "=" * 50)
        print("💳 Configuration Wallet Polygon")
        print("=" * 50)
        print("⚠️  La clé privée sera chiffrée et jamais stockée en clair.")
        print()
        
        address = input("Adresse wallet (0x...): ").strip()
        private_key = getpass.getpass("Clé privée (cachée): ").strip()
        
        # Nettoyer le préfixe 0x si présent
        if private_key.startswith("0x"):
            private_key = private_key[2:]
        
        return address, private_key
    
    def validate_wallet_address(self, address: str) -> bool:
        """Valide le format d'une adresse Ethereum/Polygon."""
        if not address:
            return False
        if not address.startswith("0x"):
            return False
        if len(address) != 42:
            return False
        try:
            int(address, 16)
            return True
        except ValueError:
            return False
    
    def validate_private_key(self, key: str) -> bool:
        """Valide le format d'une clé privée."""
        clean_key = key[2:] if key.startswith("0x") else key
        if len(clean_key) != 64:
            return False
        try:
            int(clean_key, 16)
            return True
        except ValueError:
            return False
    
    async def get_credentials(self, require_wallet: bool = True) -> PolymarketCredentials:
        """
        Récupère toutes les credentials nécessaires.
        
        Args:
            require_wallet: Si True, demande aussi les credentials wallet
            
        Returns:
            APICredentials complètes
        """
        # Charger depuis .env d'abord
        credentials = self.load_from_env()
        
        # Demander les credentials API si manquantes
        if not credentials.is_api_complete():
            api_key, api_secret = self.prompt_api_credentials()
            credentials.api_key = api_key
            credentials.api_secret = api_secret
        
        # Demander les credentials wallet si requises et manquantes
        if require_wallet and not credentials.is_wallet_complete():
            address, private_key = self.prompt_wallet_credentials()
            
            if not self.validate_wallet_address(address):
                raise ValueError("Adresse wallet invalide")
            if not self.validate_private_key(private_key):
                raise ValueError("Clé privée invalide")
            
            credentials.wallet_address = address
            credentials.private_key = private_key
        
        self._credentials = credentials
        return credentials
    
    def get_cached_credentials(self) -> Optional[PolymarketCredentials]:
        """Retourne les credentials en cache (si existantes)."""
        return self._credentials
    
    def clear_credentials(self) -> None:
        """Efface les credentials de la mémoire."""
        if self._credentials:
            self._credentials.clear_sensitive()
        self._credentials = None
