"""
Performance Optimizations - Module d'optimisation HFT

Optimisations incluses:
1. uvloop - Event loop 2-4x plus rapide que asyncio par défaut
2. orjson - Sérialisation JSON 10x plus rapide
3. TTLCache - Cache en mémoire avec expiration automatique
"""

import sys
import asyncio
from typing import Any, Optional
from functools import lru_cache

# ═══════════════════════════════════════════════════════════════
# UVLOOP - Event Loop Optimisé
# ═══════════════════════════════════════════════════════════════

_uvloop_installed = False

def setup_uvloop() -> bool:
    """
    Configure uvloop comme event loop par défaut.
    Doit être appelé AVANT toute création d'event loop.

    Returns:
        True si uvloop est activé, False sinon
    """
    global _uvloop_installed

    if sys.platform == "win32":
        print("⚠️ uvloop non disponible sur Windows")
        return False

    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        _uvloop_installed = True
        print("⚡ uvloop activé - Event loop optimisé")
        return True
    except ImportError:
        print("⚠️ uvloop non installé - pip install uvloop")
        return False
    except Exception as e:
        print(f"⚠️ Erreur uvloop: {e}")
        return False


def is_uvloop_active() -> bool:
    """Vérifie si uvloop est actif (vérifie le type de l'event loop actuel)."""
    if _uvloop_installed:
        return True
    # Vérification dynamique de l'event loop actuel
    try:
        import uvloop
        loop = asyncio.get_event_loop()
        return isinstance(loop, uvloop.Loop)
    except Exception:
        return False


def is_uvloop_available() -> bool:
    """Vérifie si uvloop est disponible (installé)."""
    try:
        import uvloop
        return True
    except ImportError:
        return False


# ═══════════════════════════════════════════════════════════════
# ORJSON - Sérialisation JSON Rapide
# ═══════════════════════════════════════════════════════════════

try:
    import orjson
    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False
    import json as _json


def json_dumps(obj: Any) -> str:
    """
    Sérialise un objet en JSON (utilise orjson si disponible).

    ~10x plus rapide que json.dumps standard.
    """
    if _HAS_ORJSON:
        return orjson.dumps(obj).decode("utf-8")
    return _json.dumps(obj)


def json_dumps_bytes(obj: Any) -> bytes:
    """
    Sérialise un objet en JSON bytes (utilise orjson si disponible).

    Optimal pour les réponses HTTP directes.
    """
    if _HAS_ORJSON:
        return orjson.dumps(obj)
    return _json.dumps(obj).encode("utf-8")


def json_loads(data: str | bytes) -> Any:
    """
    Parse du JSON (utilise orjson si disponible).

    ~3x plus rapide que json.loads standard.
    """
    if _HAS_ORJSON:
        return orjson.loads(data)
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return _json.loads(data)


# ═══════════════════════════════════════════════════════════════
# CACHE EN MÉMOIRE
# ═══════════════════════════════════════════════════════════════

try:
    from cachetools import TTLCache
    _HAS_CACHETOOLS = True
except ImportError:
    _HAS_CACHETOOLS = False


class MarketCache:
    """
    Cache en mémoire pour les données de marché avec TTL.

    Réduit les appels API redondants et améliore la latence.
    """

    def __init__(self, maxsize: int = 1000, ttl: float = 5.0):
        """
        Args:
            maxsize: Nombre max d'entrées en cache
            ttl: Durée de vie en secondes
        """
        if _HAS_CACHETOOLS:
            self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        else:
            self._cache = {}
            self._ttl = ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Récupère une valeur du cache."""
        try:
            value = self._cache.get(key)
            if value is not None:
                self._hits += 1
            else:
                self._misses += 1
            return value
        except Exception:
            self._misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        """Stocke une valeur dans le cache."""
        try:
            self._cache[key] = value
        except Exception:
            pass

    def delete(self, key: str) -> None:
        """Supprime une entrée du cache."""
        try:
            del self._cache[key]
        except KeyError:
            pass

    def clear(self) -> None:
        """Vide le cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        """Retourne les statistiques du cache."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 1),
            "size": len(self._cache),
        }


# ═══════════════════════════════════════════════════════════════
# INSTANCE GLOBALE DU CACHE
# ═══════════════════════════════════════════════════════════════

# Cache pour les orderbooks (TTL ultra-court pour HFT - 100ms)
orderbook_cache = MarketCache(maxsize=500, ttl=0.1)

# Cache pour les marchés (TTL plus long car moins volatil)
market_cache = MarketCache(maxsize=200, ttl=30.0)


# ═══════════════════════════════════════════════════════════════
# DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════

def get_performance_status() -> dict:
    """Retourne le statut des optimisations."""
    return {
        "uvloop": is_uvloop_active() or is_uvloop_available(),
        "uvloop_active": is_uvloop_active(),
        "uvloop_available": is_uvloop_available(),
        "orjson": _HAS_ORJSON,
        "cachetools": _HAS_CACHETOOLS,
        "orderbook_cache": orderbook_cache.stats,
        "market_cache": market_cache.stats,
    }


def print_performance_status():
    """Affiche le statut des optimisations."""
    status = get_performance_status()
    print("\n⚡ Performance HFT Status:")
    uvloop_str = "✅ Disponible" if status['uvloop'] else "❌ Non installé"
    if status.get('uvloop_active'):
        uvloop_str = "✅ Actif"
    print(f"   uvloop:     {uvloop_str}")
    print(f"   orjson:     {'✅ Actif' if status['orjson'] else '❌ Inactif'}")
    print(f"   cachetools: {'✅ Actif' if status['cachetools'] else '❌ Inactif'}")
    print(f"   Orderbook Cache: {status['orderbook_cache']}")
    print(f"   Market Cache:    {status['market_cache']}")
