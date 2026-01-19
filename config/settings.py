"""
Settings - Configuration générale du Bot HFT

Contient les endpoints API, configuration réseau, et paramètres système.
"""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Configuration générale du bot."""
    
    # ═══════════════════════════════════════════════════════════════
    # APIs POLYMARKET - PUBLIQUES
    # ═══════════════════════════════════════════════════════════════
    polymarket_api_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    
    # ═══════════════════════════════════════════════════════════════
    # APIs POLYMARKET - PRIVÉES (à remplir)
    # ═══════════════════════════════════════════════════════════════
    polymarket_api_key: str = Field(default="", env="POLYMARKET_API_KEY")
    polymarket_api_secret: str = Field(default="", env="POLYMARKET_API_SECRET")
    polymarket_passphrase: str = Field(default="", env="POLYMARKET_PASSPHRASE")
    polymarket_private_key: str = Field(default="", env="POLYMARKET_PRIVATE_KEY")
    
    # ═══════════════════════════════════════════════════════════════
    # WALLET POLYGON
    # ═══════════════════════════════════════════════════════════════
    wallet_address: str = Field(default="", env="WALLET_ADDRESS")
    polygon_rpc_url: str = Field(default="https://polygon-rpc.com", env="POLYGON_RPC_URL")
    chain_id: int = 137  # Polygon Mainnet
    
    # ═══════════════════════════════════════════════════════════════
    # MARCHÉS CIBLES - HFT CRYPTO
    # ═══════════════════════════════════════════════════════════════
    target_keywords: list[str] = [
        # Top volatilité crypto
        "BTC", "Bitcoin",
        "ETH", "Ethereum", 
        "SOL", "Solana",
        "XRP", "Ripple",
        "DOGE", "Dogecoin",
        "PEPE",
        "TON", "Telegram",
        "BNB", "Binance"
    ]
    
    market_types: list[str] = [
        # Types de marchés prix/prédiction
        "Up", "Down", "Above", "Below",
        "Price", "Hit", "Reach", "Touch",
        "High", "Low", "Dip", "Surge", "Moon",
        "Hour", "Today", "EOD", "Tomorrow",
        "$", "%" , "?"
    ]
    
    # ═══════════════════════════════════════════════════════════════
    # PARAMÈTRES SYSTÈME (Optimisés HFT - Ultra-rapide)
    # ═══════════════════════════════════════════════════════════════
    scan_interval_seconds: float = 0.3  # 300ms entre scans (très rapide)
    request_timeout: int = 10  # Timeout 10s (increased to prevent startup hang)
    max_retries: int = 1  # 1 seul retry (rapidité > fiabilité pour HFT)
    
    # ═══════════════════════════════════════════════════════════════
    # GESTION DU RISQUE (Phase 3)
    # ═══════════════════════════════════════════════════════════════
    enable_kelly_sizing: bool = False  # SAFETY SWITCH (Default OFF)
    kelly_min_bet: float = 5.0   # Mise minimum si Kelly activé
    kelly_max_bet: float = 50.0  # Mise maximum si Kelly activé
    
    # ═══════════════════════════════════════════════════════════════
    # CHEMINS FICHIERS
    # ═══════════════════════════════════════════════════════════════
    data_dir: str = "data"
    trades_file: str = "data/trades.json"
    opportunities_file: str = "data/opportunities.json"
    trading_params_file: str = "config/trading_params.json"
    wallet_encrypted_file: str = "wallet.enc"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
    
    @property
    def has_api_credentials(self) -> bool:
        """Vérifie si les credentials API sont configurées."""
        return bool(self.polymarket_api_key and self.polymarket_api_secret and self.polymarket_passphrase)
    
    @property
    def has_private_key(self) -> bool:
        """Vérifie si la clé privée est configurée."""
        return bool(self.polymarket_private_key)
    
    @property
    def has_wallet(self) -> bool:
        """Vérifie si une adresse wallet est configurée."""
        return bool(self.wallet_address)


@lru_cache()
def get_settings() -> Settings:
    """Retourne l'instance singleton des settings."""
    return Settings()
