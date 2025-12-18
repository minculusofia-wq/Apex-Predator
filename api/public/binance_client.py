"""
Binance Public Client - Vérification prix et volatilité en temps réel

Endpoints utilisés (publics, sans API key):
- GET /api/v3/ticker/price : Prix spot
- GET /api/v3/ticker/24hr : Stats 24h avec volatilité
"""

import httpx
from typing import Optional, Dict
from dataclasses import dataclass
import asyncio


@dataclass
class CryptoPrice:
    """Prix et volatilité d'un asset crypto."""
    symbol: str           # BTCUSDT, ETHUSDT, etc.
    price: float          # Prix spot actuel
    change_1h: float      # % change 1h (approximé)
    change_24h: float     # % change 24h
    volume_24h: float     # Volume 24h en USDT
    high_24h: float       # Plus haut 24h
    low_24h: float        # Plus bas 24h
    
    @property
    def volatility_score(self) -> float:
        """Score de volatilité 0-100 basé sur le range 24h."""
        if self.price == 0:
            return 0
        range_pct = ((self.high_24h - self.low_24h) / self.price) * 100
        # Normaliser sur 100 (20% range = score 100)
        return min(100, range_pct * 5)


class BinanceClient:
    """
    Client pour l'API publique Binance.
    
    Usage:
        async with BinanceClient() as client:
            btc = await client.get_price("BTC")
            prices = await client.get_all_prices()
    """
    
    BASE_URL = "https://api.binance.com"
    
    # Mapping asset -> symbole Binance
    SYMBOLS = {
        "BTC": "BTCUSDT",
        "ETH": "ETHUSDT",
        "SOL": "SOLUSDT",
        "XRP": "XRPUSDT",
        "DOGE": "DOGEUSDT",
        "PEPE": "PEPEUSDT",
        "TON": "TONUSDT",
        "BNB": "BNBUSDT",
    }
    
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._cache: Dict[str, CryptoPrice] = {}
        self._cache_ttl = 10  # seconds
        self._last_update = 0
    
    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=10,
            headers={"Accept": "application/json"}
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
    
    async def get_price(self, asset: str) -> Optional[CryptoPrice]:
        """
        Récupère le prix et la volatilité d'un asset.
        
        Args:
            asset: Symbole court (BTC, ETH, SOL, etc.)
            
        Returns:
            CryptoPrice ou None si non trouvé
        """
        symbol = self.SYMBOLS.get(asset.upper())
        if not symbol:
            return None
        
        try:
            response = await self._client.get(
                "/api/v3/ticker/24hr",
                params={"symbol": symbol}
            )
            response.raise_for_status()
            data = response.json()
            
            price = float(data.get("lastPrice", 0))
            open_price = float(data.get("openPrice", 0))
            
            # Calculer change 1h approximé (1/24 du change 24h avec facteur)
            change_24h = float(data.get("priceChangePercent", 0))
            change_1h = change_24h / 6  # Approximation simple
            
            return CryptoPrice(
                symbol=symbol,
                price=price,
                change_1h=round(change_1h, 2),
                change_24h=round(change_24h, 2),
                volume_24h=float(data.get("quoteVolume", 0)),
                high_24h=float(data.get("highPrice", 0)),
                low_24h=float(data.get("lowPrice", 0))
            )
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 451:
                # Geo-restriction (USA/France etc.) - Silence error to avoid log spam
                return None
            print(f"Erreur Binance {asset}: {e}")
            return None
        except Exception as e:
            print(f"Erreur Binance {asset}: {e}")
            return None
    
    async def get_all_prices(self) -> Dict[str, CryptoPrice]:
        """
        Récupère les prix de tous les assets supportés.
        
        Returns:
            Dict {asset: CryptoPrice}
        """
        prices = {}
        
        # Paralléliser les requêtes
        tasks = {
            asset: self.get_price(asset) 
            for asset in self.SYMBOLS.keys()
        }
        
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        for asset, result in zip(tasks.keys(), results):
            if isinstance(result, CryptoPrice):
                prices[asset] = result
        
        return prices
    
    async def get_volatility_ranking(self) -> list[tuple[str, float]]:
        """
        Retourne les assets triés par volatilité (desc).
        
        Returns:
            Liste de tuples (asset, volatility_score)
        """
        prices = await self.get_all_prices()
        
        ranking = [
            (asset, price.volatility_score)
            for asset, price in prices.items()
        ]
        
        return sorted(ranking, key=lambda x: x[1], reverse=True)
