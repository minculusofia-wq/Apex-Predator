"""
CoinGecko Public Client - Alternative à Binance pour data volatilité

Avec cache intégré pour respecter le rate limit CoinGecko (30 calls/min).
"""

import httpx
import time
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import asyncio


@dataclass
class CryptoPrice:
    """Prix et volatilité d'un asset crypto."""
    symbol: str           # BTC, ETH, etc.
    price: float          # Prix spot actuel
    change_24h: float     # % change 24h
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


class CoinGeckoClient:
    """
    Client pour l'API publique CoinGecko (Free Tier).
    Rate limit: ~30 calls/minute

    Cache intégré (TTL 60s) pour éviter les 429 errors.
    """

    BASE_URL = "https://api.coingecko.com/api/v3"
    CACHE_TTL = 60  # Cache 60 secondes (respecte rate limit)

    # Mapping Symbol -> CoinGecko ID
    ASSETS = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "SOL": "solana",
        "XRP": "ripple",
        "DOGE": "dogecoin",
        "PEPE": "pepe",
        "TON": "the-open-network",
        "BNB": "binancecoin",
    }

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        # Cache pour éviter rate limiting
        self._cache: Dict[str, tuple[float, Any]] = {}  # key -> (timestamp, data)
        self._last_request_time: float = 0

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

    def _get_cached(self, key: str) -> Optional[Any]:
        """Récupère une valeur du cache si non expirée."""
        if key in self._cache:
            timestamp, data = self._cache[key]
            if time.time() - timestamp < self.CACHE_TTL:
                return data
            del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        """Stocke une valeur dans le cache."""
        self._cache[key] = (time.time(), data)

    async def get_volatility_ranking(self) -> List[tuple[str, float]]:
        """
        Récupère les données et retourne le ranking de volatilité.
        Utilise le cache pour éviter les 429 (rate limit).
        """
        cache_key = "volatility_ranking"

        # Vérifier le cache d'abord
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        if not self._client:
            return []

        # Rate limiting: attendre minimum 2s entre requêtes
        elapsed = time.time() - self._last_request_time
        if elapsed < 2.0:
            await asyncio.sleep(2.0 - elapsed)

        try:
            ids = ",".join(self.ASSETS.values())
            self._last_request_time = time.time()

            response = await self._client.get(
                "/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ids,
                    "order": "market_cap_desc",
                    "per_page": 20,
                    "page": 1,
                    "sparkline": "false"
                }
            )
            response.raise_for_status()
            data = response.json()

            # Map ID back to Symbol for output
            id_to_symbol = {v: k for k, v in self.ASSETS.items()}

            ranking = []
            for item in data:
                cg_id = item['id']
                if cg_id in id_to_symbol:
                    symbol = id_to_symbol[cg_id]
                    price_obj = CryptoPrice(
                        symbol=symbol,
                        price=float(item.get('current_price', 0) or 0),
                        change_24h=float(item.get('price_change_percentage_24h', 0) or 0),
                        high_24h=float(item.get('high_24h', 0) or 0),
                        low_24h=float(item.get('low_24h', 0) or 0)
                    )
                    ranking.append((symbol, price_obj.volatility_score))

            # Sort by volatility desc
            result = sorted(ranking, key=lambda x: x[1], reverse=True)

            # Mettre en cache
            self._set_cached(cache_key, result)

            return result

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                # Rate limited - retourner le cache même expiré si disponible
                if cache_key in self._cache:
                    _, data = self._cache[cache_key]
                    print("⚠️ CoinGecko rate limited - utilisation du cache")
                    return data
                print("⚠️ CoinGecko rate limited - aucune donnée cache")
            else:
                print(f"⚠️ Erreur CoinGecko HTTP: {e}")
            return []
        except Exception as e:
            print(f"⚠️ Erreur CoinGecko: {e}")
            return []
