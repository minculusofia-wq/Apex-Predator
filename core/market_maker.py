"""
Market Maker - Strategy de market making bidirectionnel

Fonctionnalités:
1. Placer des ordres des deux côtés (YES et NO)
2. Capturer le spread bid/ask
3. Gestion du risque avec inventaire max
4. Rééquilibrage automatique des positions
5. Ajustement dynamique des prix selon la volatilité

Stratégie:
- On place un ordre BUY sur YES et un ordre BUY sur NO
- Quand un ordre est rempli, on gagne le spread
- Le profit = Spread - Frais

Documentation: https://docs.polymarket.com
"""

from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import asyncio

from api.private import PolymarketPrivateClient
from core.scanner import MarketData


class MMStatus(Enum):
    """États du market maker."""
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class MMSide(Enum):
    """Côté du market maker."""
    YES = "yes"
    NO = "no"
    BOTH = "both"


@dataclass
class MMOrder:
    """Représente un ordre de market making."""
    order_id: str
    token_id: str
    side: str  # BUY or SELL
    price: float
    size: float
    filled: float = 0.0
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def is_filled(self) -> bool:
        return self.filled >= self.size

    @property
    def remaining(self) -> float:
        return self.size - self.filled


@dataclass
class MMPosition:
    """Position actuelle sur un marché."""
    market_id: str
    yes_shares: float = 0.0
    no_shares: float = 0.0
    avg_yes_price: float = 0.0
    avg_no_price: float = 0.0
    total_cost: float = 0.0
    realized_pnl: float = 0.0

    @property
    def net_position(self) -> float:
        """Position nette (positif = long YES, négatif = long NO)."""
        return self.yes_shares - self.no_shares

    @property
    def is_balanced(self) -> bool:
        """Vérifie si la position est équilibrée."""
        return abs(self.net_position) < 10  # Tolérance de 10 shares

    @property
    def unrealized_pnl(self, yes_price: float = 0.5, no_price: float = 0.5) -> float:
        """P&L non réalisé."""
        yes_value = self.yes_shares * yes_price
        no_value = self.no_shares * no_price
        return yes_value + no_value - self.total_cost


@dataclass
class MMConfig:
    """Configuration du market maker."""
    # Spread cible
    target_spread: float = 0.04          # 4 cents de spread cible
    min_spread: float = 0.02             # Spread minimum acceptable
    max_spread: float = 0.10             # Spread maximum

    # Taille des ordres
    order_size: float = 50.0             # Taille de chaque ordre en $
    max_position: float = 500.0          # Position max par côté

    # Décalage des prix
    price_offset: float = 0.01           # Décalage par rapport au mid
    aggressive_offset: float = 0.005     # Décalage plus agressif si inventaire déséquilibré

    # Timing
    refresh_interval: float = 2.0        # Intervalle de rafraîchissement (secondes)
    order_timeout: int = 60              # Timeout des ordres (secondes)

    # Gestion du risque
    max_inventory_imbalance: float = 200.0  # Déséquilibre max avant rééquilibrage
    stop_loss_pct: float = 0.10          # Stop loss à 10%


class MarketMaker:
    """
    Market Maker bidirectionnel pour Polymarket.

    Usage:
        mm = MarketMaker(private_client, market_data)
        await mm.start()

    Stratégie:
        1. Calcule le mid-price depuis l'orderbook
        2. Place un ordre BUY YES à (mid - offset)
        3. Place un ordre BUY NO à (1 - mid - offset)
        4. Gère les fills et rééquilibre l'inventaire
    """

    def __init__(
        self,
        private_client: Optional[PolymarketPrivateClient],
        config: Optional[MMConfig] = None
    ):
        self.private_client = private_client
        self.config = config or MMConfig()
        self._status = MMStatus.STOPPED
        self._task: Optional[asyncio.Task] = None

        # État interne
        self._positions: Dict[str, MMPosition] = {}
        self._active_orders: Dict[str, MMOrder] = {}
        self._markets: Dict[str, MarketData] = {}

        # Métriques
        self._total_trades = 0
        self._total_volume = 0.0
        self._total_pnl = 0.0
        self._start_time: Optional[datetime] = None

    @property
    def status(self) -> MMStatus:
        return self._status

    @property
    def is_running(self) -> bool:
        return self._status == MMStatus.RUNNING

    @property
    def stats(self) -> dict:
        """Statistiques du market maker."""
        uptime = 0
        if self._start_time:
            uptime = (datetime.now() - self._start_time).total_seconds()

        return {
            "status": self._status.value,
            "uptime_seconds": int(uptime),
            "total_trades": self._total_trades,
            "total_volume": round(self._total_volume, 2),
            "total_pnl": round(self._total_pnl, 2),
            "active_orders": len(self._active_orders),
            "active_positions": len([p for p in self._positions.values() if p.yes_shares + p.no_shares > 0]),
        }

    async def start(self, markets: Dict[str, MarketData]) -> None:
        """Démarre le market maker sur les marchés spécifiés."""
        if self._status == MMStatus.RUNNING:
            return

        self._markets = markets
        self._status = MMStatus.RUNNING
        self._start_time = datetime.now()
        self._task = asyncio.create_task(self._run_loop())
        print(f"🏪 Market Maker démarré sur {len(markets)} marchés")

    async def stop(self) -> None:
        """Arrête le market maker et annule tous les ordres."""
        self._status = MMStatus.STOPPED

        # Annuler tous les ordres actifs
        await self._cancel_all_orders()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        print("🏪 Market Maker arrêté")

    async def pause(self) -> None:
        """Met en pause le market maker (garde les ordres)."""
        self._status = MMStatus.PAUSED
        print("🏪 Market Maker en pause")

    async def resume(self) -> None:
        """Reprend le market maker."""
        if self._status == MMStatus.PAUSED:
            self._status = MMStatus.RUNNING
            print("🏪 Market Maker repris")

    def update_markets(self, markets: Dict[str, MarketData]) -> None:
        """Met à jour les données de marché."""
        self._markets = markets

    async def _run_loop(self) -> None:
        """Boucle principale du market maker."""
        while self._status == MMStatus.RUNNING:
            try:
                for market_id, market_data in self._markets.items():
                    if not market_data.is_valid:
                        continue

                    await self._process_market(market_id, market_data)

                await asyncio.sleep(self.config.refresh_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Erreur Market Maker: {e}")
                await asyncio.sleep(5)

    async def _process_market(self, market_id: str, market_data: MarketData) -> None:
        """Traite un marché: calcule les prix et place les ordres."""
        # Calculer le mid-price
        mid_yes = self._calculate_mid_price(market_data, "yes")
        mid_no = self._calculate_mid_price(market_data, "no")

        if mid_yes is None or mid_no is None:
            return

        # Vérifier le spread
        spread = self._calculate_spread(market_data)
        if spread < self.config.min_spread:
            return  # Spread trop serré, pas rentable

        # Obtenir ou créer la position
        position = self._get_or_create_position(market_id)

        # Calculer les prix d'ordres
        buy_yes_price, buy_no_price = self._calculate_order_prices(
            mid_yes, mid_no, position
        )

        # Placer les ordres
        await self._place_orders(
            market_data=market_data,
            buy_yes_price=buy_yes_price,
            buy_no_price=buy_no_price,
            position=position
        )

    def _calculate_mid_price(self, market_data: MarketData, side: str) -> Optional[float]:
        """Calcule le mid-price pour un côté."""
        if side == "yes":
            bid = market_data.best_bid_yes
            ask = market_data.best_ask_yes
        else:
            bid = market_data.best_bid_no
            ask = market_data.best_ask_no

        if bid is None or ask is None:
            return None

        return (bid + ask) / 2

    def _calculate_spread(self, market_data: MarketData) -> float:
        """Calcule le spread effectif."""
        return market_data.effective_spread

    def _get_or_create_position(self, market_id: str) -> MMPosition:
        """Obtient ou crée une position pour un marché."""
        if market_id not in self._positions:
            self._positions[market_id] = MMPosition(market_id=market_id)
        return self._positions[market_id]

    def _calculate_order_prices(
        self,
        mid_yes: float,
        mid_no: float,
        position: MMPosition
    ) -> Tuple[float, float]:
        """
        Calcule les prix d'ordres en fonction de l'inventaire.

        Si on est long YES, on offre un meilleur prix sur NO pour rééquilibrer.
        """
        offset = self.config.price_offset

        # Ajuster l'offset selon le déséquilibre d'inventaire
        if abs(position.net_position) > self.config.max_inventory_imbalance / 2:
            if position.net_position > 0:  # Long YES, plus agressif sur NO
                offset_no = self.config.aggressive_offset
                offset_yes = self.config.price_offset * 1.5
            else:  # Long NO, plus agressif sur YES
                offset_yes = self.config.aggressive_offset
                offset_no = self.config.price_offset * 1.5
        else:
            offset_yes = offset
            offset_no = offset

        # Prix d'achat = mid - offset
        buy_yes_price = mid_yes - offset_yes
        buy_no_price = mid_no - offset_no

        # S'assurer que les prix sont valides
        buy_yes_price = max(0.01, min(0.99, buy_yes_price))
        buy_no_price = max(0.01, min(0.99, buy_no_price))

        return buy_yes_price, buy_no_price

    async def _place_orders(
        self,
        market_data: MarketData,
        buy_yes_price: float,
        buy_no_price: float,
        position: MMPosition
    ) -> None:
        """Place les ordres de market making."""
        if not self.private_client:
            return

        market = market_data.market

        # Vérifier les limites de position
        if position.yes_shares >= self.config.max_position:
            buy_yes_price = 0  # Ne pas acheter plus de YES

        if position.no_shares >= self.config.max_position:
            buy_no_price = 0  # Ne pas acheter plus de NO

        # Calculer la taille en shares
        size_yes = self.config.order_size / buy_yes_price if buy_yes_price > 0 else 0
        size_no = self.config.order_size / buy_no_price if buy_no_price > 0 else 0

        # Placer l'ordre YES
        if size_yes > 0:
            try:
                result = await self.private_client.create_limit_order(
                    token_id=market.token_yes_id,
                    side="BUY",
                    price=buy_yes_price,
                    size=size_yes
                )
                if result.get("orderID"):
                    self._active_orders[result["orderID"]] = MMOrder(
                        order_id=result["orderID"],
                        token_id=market.token_yes_id,
                        side="BUY",
                        price=buy_yes_price,
                        size=size_yes
                    )
                    print(f"📗 MM Order YES: BUY {size_yes:.0f} @ ${buy_yes_price:.3f}")
            except Exception as e:
                print(f"⚠️ Erreur ordre YES: {e}")

        # Placer l'ordre NO
        if size_no > 0:
            try:
                result = await self.private_client.create_limit_order(
                    token_id=market.token_no_id,
                    side="BUY",
                    price=buy_no_price,
                    size=size_no
                )
                if result.get("orderID"):
                    self._active_orders[result["orderID"]] = MMOrder(
                        order_id=result["orderID"],
                        token_id=market.token_no_id,
                        side="BUY",
                        price=buy_no_price,
                        size=size_no
                    )
                    print(f"📕 MM Order NO: BUY {size_no:.0f} @ ${buy_no_price:.3f}")
            except Exception as e:
                print(f"⚠️ Erreur ordre NO: {e}")

    async def _cancel_all_orders(self) -> None:
        """Annule tous les ordres actifs."""
        if not self.private_client:
            return

        try:
            await self.private_client.cancel_all_orders()
            self._active_orders.clear()
            print("🗑️ Tous les ordres MM annulés")
        except Exception as e:
            print(f"⚠️ Erreur annulation ordres: {e}")

    async def _cancel_stale_orders(self) -> None:
        """Annule les ordres trop vieux."""
        now = datetime.now()
        stale_orders = []

        for order_id, order in self._active_orders.items():
            age = (now - order.created_at).total_seconds()
            if age > self.config.order_timeout:
                stale_orders.append(order_id)

        for order_id in stale_orders:
            if self.private_client:
                await self.private_client.cancel_order(order_id)
            del self._active_orders[order_id]

    def get_position(self, market_id: str) -> Optional[MMPosition]:
        """Récupère la position pour un marché."""
        return self._positions.get(market_id)

    def get_all_positions(self) -> List[MMPosition]:
        """Récupère toutes les positions."""
        return list(self._positions.values())

    def calculate_total_pnl(self) -> float:
        """Calcule le P&L total de toutes les positions."""
        total = 0.0
        for position in self._positions.values():
            total += position.realized_pnl
        return total
