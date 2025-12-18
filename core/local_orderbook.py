"""
Local Orderbook Mirror - Miroir local de l'orderbook maintenu via WebSocket

Optimisation HFT: Au lieu de poll l'API pour les orderbooks, maintenir
un miroir local qui est mis à jour via WebSocket en temps réel.

Avantages:
- Réaction instantanée aux changements (0ms vs ~100ms polling)
- Moins de requêtes API (réduit risque de rate limiting)
- Best bid/ask toujours à jour

Usage:
    orderbook = LocalOrderbook("token_id_123")

    # Appliquer un snapshot initial
    orderbook.apply_snapshot(bids, asks)

    # Appliquer les deltas WebSocket
    orderbook.apply_delta(book_update)

    # Accéder aux données
    print(f"Best bid: {orderbook.best_bid}, Best ask: {orderbook.best_ask}")
"""

import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# Utiliser sortedcontainers si disponible pour O(log n) operations
try:
    from sortedcontainers import SortedDict
    _HAS_SORTED = True
except ImportError:
    _HAS_SORTED = False
    SortedDict = dict  # Fallback


@dataclass(slots=True)
class OrderbookLevel:
    """Niveau de prix dans l'orderbook."""
    price: float
    size: float
    order_count: int = 1


class LocalOrderbook:
    """
    Miroir local de l'orderbook maintenu via WebSocket.

    Utilise SortedDict pour des opérations O(log n) sur les prix.
    Fallback sur dict standard si sortedcontainers non installé.
    """

    def __init__(self, token_id: str, max_levels: int = 50):
        """
        Initialise l'orderbook local.

        Args:
            token_id: ID du token Polymarket
            max_levels: Nombre max de niveaux à garder (pour limiter mémoire)
        """
        self.token_id = token_id
        self._max_levels = max_levels

        # Bids: prix décroissant (meilleur bid = plus haut prix)
        # Asks: prix croissant (meilleur ask = plus bas prix)
        if _HAS_SORTED:
            self._bids: SortedDict = SortedDict(lambda x: -x)  # Reverse sort
            self._asks: SortedDict = SortedDict()
        else:
            self._bids: Dict[float, float] = {}
            self._asks: Dict[float, float] = {}

        self._last_update: float = 0
        self._update_count: int = 0
        self._is_initialized: bool = False

    @property
    def best_bid(self) -> Optional[float]:
        """Retourne le meilleur bid (plus haut prix d'achat)."""
        if not self._bids:
            return None
        if _HAS_SORTED:
            return self._bids.peekitem(0)[0]  # Premier = plus haut avec reverse sort
        return max(self._bids.keys())

    @property
    def best_ask(self) -> Optional[float]:
        """Retourne le meilleur ask (plus bas prix de vente)."""
        if not self._asks:
            return None
        if _HAS_SORTED:
            return self._asks.peekitem(0)[0]  # Premier = plus bas
        return min(self._asks.keys())

    @property
    def best_bid_size(self) -> float:
        """Retourne la taille au meilleur bid."""
        bid = self.best_bid
        return self._bids.get(bid, 0) if bid else 0

    @property
    def best_ask_size(self) -> float:
        """Retourne la taille au meilleur ask."""
        ask = self.best_ask
        return self._asks.get(ask, 0) if ask else 0

    @property
    def spread(self) -> Optional[float]:
        """Retourne le spread (ask - bid)."""
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid

    @property
    def spread_percent(self) -> Optional[float]:
        """Retourne le spread en pourcentage du mid."""
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None or (bid + ask) == 0:
            return None
        mid = (bid + ask) / 2
        return (ask - bid) / mid * 100

    @property
    def mid_price(self) -> Optional[float]:
        """Retourne le mid price."""
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2

    @property
    def is_stale(self) -> bool:
        """Vérifie si les données sont périmées (>5s sans update)."""
        return time.time() - self._last_update > 5.0

    @property
    def age_ms(self) -> float:
        """Âge des données en millisecondes."""
        return (time.time() - self._last_update) * 1000

    def apply_snapshot(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]]
    ) -> None:
        """
        Applique un snapshot complet de l'orderbook.

        Args:
            bids: Liste de (price, size) pour les bids
            asks: Liste de (price, size) pour les asks
        """
        self._bids.clear()
        self._asks.clear()

        for price, size in bids[:self._max_levels]:
            if size > 0:
                self._bids[price] = size

        for price, size in asks[:self._max_levels]:
            if size > 0:
                self._asks[price] = size

        self._last_update = time.time()
        self._update_count += 1
        self._is_initialized = True

    def apply_delta(
        self,
        bids: Optional[List[Tuple[float, float]]] = None,
        asks: Optional[List[Tuple[float, float]]] = None
    ) -> None:
        """
        Applique une mise à jour incrémentale (delta).

        Si size = 0, le niveau est supprimé.

        Args:
            bids: Liste de (price, size) pour les bids à mettre à jour
            asks: Liste de (price, size) pour les asks à mettre à jour
        """
        if bids:
            for price, size in bids:
                if size <= 0:
                    self._bids.pop(price, None)
                else:
                    self._bids[price] = size

        if asks:
            for price, size in asks:
                if size <= 0:
                    self._asks.pop(price, None)
                else:
                    self._asks[price] = size

        # Trimmer si trop de niveaux
        self._trim_levels()

        self._last_update = time.time()
        self._update_count += 1

    def _trim_levels(self) -> None:
        """Supprime les niveaux excédentaires."""
        if _HAS_SORTED:
            while len(self._bids) > self._max_levels:
                self._bids.popitem(-1)  # Supprimer le pire bid
            while len(self._asks) > self._max_levels:
                self._asks.popitem(-1)  # Supprimer le pire ask
        else:
            # Fallback sans sortedcontainers (moins efficace)
            if len(self._bids) > self._max_levels:
                sorted_bids = sorted(self._bids.items(), reverse=True)
                self._bids = dict(sorted_bids[:self._max_levels])

            if len(self._asks) > self._max_levels:
                sorted_asks = sorted(self._asks.items())
                self._asks = dict(sorted_asks[:self._max_levels])

    def get_depth(self, levels: int = 5) -> Dict[str, List[Tuple[float, float]]]:
        """
        Retourne la profondeur de l'orderbook.

        Args:
            levels: Nombre de niveaux à retourner

        Returns:
            Dict avec 'bids' et 'asks' comme listes de (price, size)
        """
        if _HAS_SORTED:
            bids = list(self._bids.items())[:levels]
            asks = list(self._asks.items())[:levels]
        else:
            bids = sorted(self._bids.items(), reverse=True)[:levels]
            asks = sorted(self._asks.items())[:levels]

        return {"bids": bids, "asks": asks}

    def get_volume_at_price(self, price: float, side: str) -> float:
        """Retourne le volume à un prix donné."""
        if side.upper() == "BID":
            return self._bids.get(price, 0)
        return self._asks.get(price, 0)

    def get_total_volume(self, side: str, within_spread_percent: float = 1.0) -> float:
        """
        Calcule le volume total dans une plage de spread.

        Args:
            side: "BID" ou "ASK"
            within_spread_percent: Pourcentage du mid price

        Returns:
            Volume total dans la plage
        """
        mid = self.mid_price
        if mid is None:
            return 0

        threshold = mid * (within_spread_percent / 100)
        total = 0

        if side.upper() == "BID":
            for price, size in self._bids.items():
                if mid - price <= threshold:
                    total += size
        else:
            for price, size in self._asks.items():
                if price - mid <= threshold:
                    total += size

        return total

    def to_dict(self) -> Dict:
        """Exporte l'orderbook en dict."""
        return {
            "token_id": self.token_id,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread": self.spread,
            "mid_price": self.mid_price,
            "bid_levels": len(self._bids),
            "ask_levels": len(self._asks),
            "last_update": self._last_update,
            "update_count": self._update_count,
            "is_stale": self.is_stale,
        }

    def __repr__(self) -> str:
        return (
            f"LocalOrderbook({self.token_id}, "
            f"bid={self.best_bid}, ask={self.best_ask}, "
            f"spread={self.spread})"
        )


class OrderbookManager:
    """
    Gestionnaire de plusieurs orderbooks locaux.

    Maintient un orderbook par token_id et expose des méthodes
    pour les mettre à jour via les événements WebSocket.
    """

    def __init__(self, max_levels: int = 50):
        """
        Initialise le gestionnaire.

        Args:
            max_levels: Niveaux max par orderbook
        """
        self._orderbooks: Dict[str, LocalOrderbook] = {}
        self._max_levels = max_levels

    def get_or_create(self, token_id: str) -> LocalOrderbook:
        """Récupère ou crée un orderbook pour un token."""
        if token_id not in self._orderbooks:
            self._orderbooks[token_id] = LocalOrderbook(
                token_id=token_id,
                max_levels=self._max_levels
            )
        return self._orderbooks[token_id]

    def get(self, token_id: str) -> Optional[LocalOrderbook]:
        """Récupère un orderbook s'il existe."""
        return self._orderbooks.get(token_id)

    def apply_snapshot(
        self,
        token_id: str,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]]
    ) -> None:
        """Applique un snapshot à un orderbook."""
        ob = self.get_or_create(token_id)
        ob.apply_snapshot(bids, asks)

    def apply_delta(
        self,
        token_id: str,
        bids: Optional[List[Tuple[float, float]]] = None,
        asks: Optional[List[Tuple[float, float]]] = None
    ) -> None:
        """Applique un delta à un orderbook."""
        ob = self.get_or_create(token_id)
        ob.apply_delta(bids, asks)

    def remove(self, token_id: str) -> None:
        """Supprime un orderbook."""
        self._orderbooks.pop(token_id, None)

    def clear(self) -> None:
        """Supprime tous les orderbooks."""
        self._orderbooks.clear()

    def get_all_spreads(self) -> Dict[str, Optional[float]]:
        """Retourne les spreads de tous les orderbooks."""
        return {
            token_id: ob.spread
            for token_id, ob in self._orderbooks.items()
        }

    def get_stale_orderbooks(self) -> List[str]:
        """Retourne les token_ids des orderbooks périmés."""
        return [
            token_id
            for token_id, ob in self._orderbooks.items()
            if ob.is_stale
        ]

    @property
    def count(self) -> int:
        """Nombre d'orderbooks gérés."""
        return len(self._orderbooks)

    @property
    def stats(self) -> Dict:
        """Statistiques du gestionnaire."""
        return {
            "orderbook_count": len(self._orderbooks),
            "stale_count": len(self.get_stale_orderbooks()),
            "total_updates": sum(
                ob._update_count for ob in self._orderbooks.values()
            ),
        }
