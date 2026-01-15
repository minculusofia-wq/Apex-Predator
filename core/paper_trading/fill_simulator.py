"""
Fill Simulator - Apex Predator v8.0

Simule l'exécution réaliste des ordres basée sur:
- Profondeur du carnet d'ordres réel
- Probabilités de fill configurables
- Modèle de slippage basé sur la taille
- Délais de latence réalistes
"""

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.local_orderbook import OrderbookManager

from .config import PaperConfig, get_paper_config


class FillType(Enum):
    """Types de résultat de fill."""
    FULL = "full"           # Ordre complètement rempli
    PARTIAL = "partial"     # Ordre partiellement rempli
    REJECTED = "rejected"   # Ordre rejeté
    TIMEOUT = "timeout"     # Timeout sans fill complet


@dataclass
class FillResult:
    """Résultat d'une simulation de fill."""
    fill_type: FillType
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    fill_delay_ms: int = 0
    slippage_bps: float = 0.0
    available_liquidity: float = 0.0
    rejection_reason: Optional[str] = None
    simulated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_successful(self) -> bool:
        """True si l'ordre a été au moins partiellement rempli."""
        return self.fill_type in (FillType.FULL, FillType.PARTIAL)

    @property
    def fill_rate(self) -> float:
        """Pourcentage de l'ordre rempli (0.0 à 1.0)."""
        return 1.0 if self.fill_type == FillType.FULL else (
            self.filled_size / max(self.filled_size, 0.001)
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire pour sérialisation."""
        return {
            "fill_type": self.fill_type.value,
            "filled_size": self.filled_size,
            "avg_fill_price": self.avg_fill_price,
            "fill_delay_ms": self.fill_delay_ms,
            "slippage_bps": self.slippage_bps,
            "available_liquidity": self.available_liquidity,
            "rejection_reason": self.rejection_reason,
            "simulated_at": self.simulated_at.isoformat(),
        }


class FillSimulator:
    """
    Simule l'exécution réaliste des ordres.

    Utilise les données du carnet d'ordres réel pour déterminer:
    - La probabilité et le timing du fill
    - Le slippage basé sur la taille de l'ordre
    - Les fills partiels en cas de liquidité insuffisante

    Usage:
        simulator = FillSimulator(orderbook_manager)
        result = await simulator.simulate_order(token_id, "BUY", 0.45, 100.0)
    """

    def __init__(
        self,
        orderbook_manager: Optional["OrderbookManager"] = None,
        config: Optional[PaperConfig] = None
    ):
        self.orderbooks = orderbook_manager
        self.config = config or get_paper_config()
        self._order_counter = 0

    async def simulate_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        market_id: Optional[str] = None
    ) -> FillResult:
        """
        Simule l'exécution d'un ordre.

        Args:
            token_id: ID du token (YES ou NO)
            side: "BUY" ou "SELL"
            price: Prix limite de l'ordre
            size: Taille en nombre de shares

        Returns:
            FillResult avec les détails de la simulation
        """
        # 1. Vérifier l'état du carnet d'ordres
        orderbook_check = self._check_orderbook(token_id)
        if orderbook_check is not None:
            return orderbook_check

        # 2. Obtenir la liquidité disponible
        available_depth = self._get_available_depth(token_id, side, price)
        current_best_price = self._get_best_price(token_id, side)

        # 3. Vérifier le price drift
        if current_best_price is not None:
            drift_check = self._check_price_drift(price, current_best_price, side)
            if drift_check is not None:
                return drift_check

        # 4. Déterminer la catégorie de fill
        fill_category, delay_ms = self._determine_fill_timing()

        # 5. Calculer le slippage
        order_value = size * price
        slippage_bps = self._calculate_slippage(order_value, available_depth)

        # 6. Déterminer la taille du fill
        filled_size, fill_type, rejection_reason = self._determine_fill_size(
            size, available_depth, fill_category
        )

        # 7. Si rejeté, retourner immédiatement
        if fill_type == FillType.REJECTED:
            return FillResult(
                fill_type=fill_type,
                rejection_reason=rejection_reason,
                available_liquidity=available_depth,
            )

        # 8. Calculer le prix de fill avec slippage
        fill_price = self._apply_slippage(price, slippage_bps, side)

        # 9. Simuler le délai
        await asyncio.sleep(delay_ms / 1000.0)

        return FillResult(
            fill_type=fill_type,
            filled_size=filled_size,
            avg_fill_price=fill_price,
            fill_delay_ms=delay_ms,
            slippage_bps=slippage_bps,
            available_liquidity=available_depth,
        )

    def _check_orderbook(self, token_id: str) -> Optional[FillResult]:
        """Vérifie si le carnet d'ordres est disponible et frais."""
        if self.orderbooks is None:
            # Mode sans orderbook - simulation simplifiée
            return None

        orderbook = self.orderbooks.get(token_id)
        if orderbook is None:
            return FillResult(
                fill_type=FillType.REJECTED,
                rejection_reason="Orderbook not available",
            )

        # Vérifier si stale
        if hasattr(orderbook, 'is_stale') and orderbook.is_stale:
            return FillResult(
                fill_type=FillType.REJECTED,
                rejection_reason=f"Orderbook stale (>{self.config.stale_orderbook_seconds}s)",
            )

        return None

    def _get_available_depth(
        self,
        token_id: str,
        side: str,
        price: float
    ) -> float:
        """Obtient la profondeur disponible au prix demandé."""
        if self.orderbooks is None:
            # Sans orderbook, simuler une profondeur raisonnable
            return self.config.min_depth_for_full_fill * 2

        orderbook = self.orderbooks.get(token_id)
        if orderbook is None:
            return self.config.min_depth_for_full_fill

        # Pour un BUY, on regarde les asks (vendeurs)
        # Pour un SELL, on regarde les bids (acheteurs)
        if side.upper() == "BUY":
            if hasattr(orderbook, 'get_ask_depth'):
                return orderbook.get_ask_depth(price)
            elif hasattr(orderbook, 'best_ask'):
                # Estimation basée sur le meilleur ask
                return self.config.min_depth_for_full_fill
        else:
            if hasattr(orderbook, 'get_bid_depth'):
                return orderbook.get_bid_depth(price)
            elif hasattr(orderbook, 'best_bid'):
                return self.config.min_depth_for_full_fill

        return self.config.min_depth_for_full_fill

    def _get_best_price(self, token_id: str, side: str) -> Optional[float]:
        """Obtient le meilleur prix actuel."""
        if self.orderbooks is None:
            return None

        orderbook = self.orderbooks.get(token_id)
        if orderbook is None:
            return None

        if side.upper() == "BUY":
            return getattr(orderbook, 'best_ask', None)
        else:
            return getattr(orderbook, 'best_bid', None)

    def _check_price_drift(
        self,
        order_price: float,
        current_price: float,
        side: str
    ) -> Optional[FillResult]:
        """Vérifie si le prix a trop dévié depuis le placement de l'ordre."""
        if current_price <= 0:
            return None

        drift_bps = abs(current_price - order_price) / order_price * 10000

        if drift_bps > self.config.price_drift_rejection_bps:
            return FillResult(
                fill_type=FillType.REJECTED,
                rejection_reason=f"Price drift too high: {drift_bps:.1f} bps",
            )

        return None

    def _determine_fill_timing(self) -> tuple[FillType, int]:
        """
        Détermine le type et le timing du fill basé sur les probabilités.

        Returns:
            (fill_category, delay_ms)
        """
        roll = random.random() * 100  # 0-100

        immediate_threshold = self.config.immediate_fill_pct
        delayed_threshold = immediate_threshold + self.config.delayed_fill_pct

        if roll < immediate_threshold:
            # Fill immédiat (70% par défaut)
            delay = random.randint(
                self.config.min_fill_delay_ms,
                self.config.max_immediate_delay_ms
            )
            return FillType.FULL, delay

        elif roll < delayed_threshold:
            # Fill différé (20% par défaut)
            delay = random.randint(
                self.config.max_immediate_delay_ms,
                self.config.max_delayed_delay_ms
            )
            return FillType.FULL, delay

        else:
            # Timeout ou partiel (10% par défaut)
            delay = int(self.config.fill_timeout_seconds * 1000)
            return FillType.TIMEOUT, delay

    def _calculate_slippage(
        self,
        order_value: float,
        available_depth: float
    ) -> float:
        """
        Calcule le slippage basé sur l'impact de l'ordre.

        Formule: slippage = base + (order_value / depth) × size_factor
        Plafonné à max_slippage_bps
        """
        base_slippage = self.config.base_slippage_bps

        # Impact de la taille
        if available_depth > 0:
            impact_ratio = order_value / available_depth
            size_impact = impact_ratio * self.config.size_impact_factor * 100  # Convert to bps
        else:
            size_impact = self.config.max_slippage_bps

        total_slippage = base_slippage + size_impact

        # Plafonner
        return min(total_slippage, self.config.max_slippage_bps)

    def _determine_fill_size(
        self,
        requested_size: float,
        available_depth: float,
        fill_category: FillType
    ) -> tuple[float, FillType, Optional[str]]:
        """
        Détermine la taille du fill basée sur la liquidité.

        Returns:
            (filled_size, fill_type, rejection_reason)
        """
        # Si timeout, retourner un fill partiel aléatoire
        if fill_category == FillType.TIMEOUT:
            partial_rate = random.uniform(0.3, 0.8)
            filled = requested_size * partial_rate
            return filled, FillType.PARTIAL, None

        # Vérifier la liquidité
        if available_depth >= requested_size:
            # Assez de liquidité pour un fill complet
            return requested_size, FillType.FULL, None

        elif available_depth >= requested_size * self.config.partial_fill_threshold:
            # Liquidité suffisante pour un fill partiel
            partial_rate = random.uniform(0.7, 0.95)
            filled = min(available_depth * partial_rate, requested_size)
            return filled, FillType.PARTIAL, None

        else:
            # Pas assez de liquidité
            return 0.0, FillType.REJECTED, "Insufficient liquidity"

    def _apply_slippage(
        self,
        price: float,
        slippage_bps: float,
        side: str
    ) -> float:
        """Applique le slippage au prix selon le côté de l'ordre."""
        slippage_factor = slippage_bps / 10000  # Convert bps to decimal

        if side.upper() == "BUY":
            # Achat = prix plus élevé (défavorable)
            return price * (1 + slippage_factor)
        else:
            # Vente = prix plus bas (défavorable)
            return price * (1 - slippage_factor)

    def generate_order_id(self) -> str:
        """Génère un ID unique pour les ordres paper."""
        self._order_counter += 1
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"paper_{timestamp}_{self._order_counter:06d}"
