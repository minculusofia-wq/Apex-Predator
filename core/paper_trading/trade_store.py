"""
Paper Trade Store - Apex Predator v8.0

Persistence des trades paper et positions.
Stocke l'historique complet pour analyse de performance.
"""

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from .config import PaperConfig, get_paper_config
from .fill_simulator import FillResult, FillType


@dataclass
class PaperOrder:
    """Un ordre paper individuel."""
    id: str
    token_id: str
    market_id: str
    side: str  # "BUY" ou "SELL"
    price: float
    size: float
    order_type: str = "GTC"
    status: str = "pending"  # pending, filled, partial, rejected, cancelled
    created_at: datetime = field(default_factory=datetime.now)

    # Résultats de simulation (après fill)
    fill_type: Optional[str] = None
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    fill_delay_ms: int = 0
    slippage_bps: float = 0.0
    fees_paid: float = 0.0
    rejection_reason: Optional[str] = None
    filled_at: Optional[datetime] = None

    def apply_fill_result(self, result: FillResult) -> None:
        """Applique le résultat de simulation à l'ordre."""
        self.fill_type = result.fill_type.value
        self.filled_size = result.filled_size
        self.avg_fill_price = result.avg_fill_price
        self.fill_delay_ms = result.fill_delay_ms
        self.slippage_bps = result.slippage_bps
        self.rejection_reason = result.rejection_reason
        self.filled_at = datetime.now()

        if result.fill_type == FillType.FULL:
            self.status = "filled"
        elif result.fill_type == FillType.PARTIAL:
            self.status = "partial"
        elif result.fill_type == FillType.REJECTED:
            self.status = "rejected"
        else:
            self.status = "timeout"

    @property
    def fill_value(self) -> float:
        """Valeur totale du fill (size × price)."""
        return self.filled_size * self.avg_fill_price

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "token_id": self.token_id,
            "market_id": self.market_id,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "order_type": self.order_type,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "fill_type": self.fill_type,
            "filled_size": self.filled_size,
            "avg_fill_price": self.avg_fill_price,
            "fill_delay_ms": self.fill_delay_ms,
            "slippage_bps": self.slippage_bps,
            "fees_paid": self.fees_paid,
            "rejection_reason": self.rejection_reason,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperOrder":
        order = cls(
            id=data["id"],
            token_id=data["token_id"],
            market_id=data["market_id"],
            side=data["side"],
            price=data["price"],
            size=data["size"],
            order_type=data.get("order_type", "GTC"),
            status=data.get("status", "pending"),
        )
        order.created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now()
        order.fill_type = data.get("fill_type")
        order.filled_size = data.get("filled_size", 0.0)
        order.avg_fill_price = data.get("avg_fill_price", 0.0)
        order.fill_delay_ms = data.get("fill_delay_ms", 0)
        order.slippage_bps = data.get("slippage_bps", 0.0)
        order.fees_paid = data.get("fees_paid", 0.0)
        order.rejection_reason = data.get("rejection_reason")
        if data.get("filled_at"):
            order.filled_at = datetime.fromisoformat(data["filled_at"])
        return order


@dataclass
class PaperTrade:
    """Un trade paper complet (peut contenir plusieurs ordres)."""
    id: str
    strategy: str  # "gabagool" ou "smart_ape"
    market_id: str
    market_question: str = ""

    # Ordres d'entrée
    entry_orders: List[PaperOrder] = field(default_factory=list)
    entry_time: Optional[datetime] = None
    entry_cost: float = 0.0

    # Ordres de sortie
    exit_orders: List[PaperOrder] = field(default_factory=list)
    exit_time: Optional[datetime] = None
    exit_value: float = 0.0

    # P&L
    gross_pnl: float = 0.0
    fees: float = 0.0
    slippage_cost: float = 0.0

    # Status
    status: str = "open"  # open, closed, cancelled

    @property
    def net_pnl(self) -> float:
        """P&L net après frais et slippage."""
        return self.gross_pnl - self.fees - self.slippage_cost

    @property
    def is_profitable(self) -> bool:
        """True si le trade est profitable."""
        return self.net_pnl > 0

    @property
    def duration_seconds(self) -> int:
        """Durée du trade en secondes."""
        if not self.entry_time or not self.exit_time:
            return 0
        return int((self.exit_time - self.entry_time).total_seconds())

    @property
    def avg_fill_delay_ms(self) -> float:
        """Délai de fill moyen."""
        all_orders = self.entry_orders + self.exit_orders
        if not all_orders:
            return 0.0
        delays = [o.fill_delay_ms for o in all_orders if o.fill_delay_ms > 0]
        return sum(delays) / len(delays) if delays else 0.0

    @property
    def avg_slippage_bps(self) -> float:
        """Slippage moyen en bps."""
        all_orders = self.entry_orders + self.exit_orders
        if not all_orders:
            return 0.0
        slippages = [o.slippage_bps for o in all_orders]
        return sum(slippages) / len(slippages) if slippages else 0.0

    @property
    def fill_rate(self) -> float:
        """Taux de fill (% des ordres complètement remplis)."""
        all_orders = self.entry_orders + self.exit_orders
        if not all_orders:
            return 0.0
        full_fills = sum(1 for o in all_orders if o.fill_type == "full")
        return full_fills / len(all_orders)

    def add_entry_order(self, order: PaperOrder) -> None:
        """Ajoute un ordre d'entrée."""
        self.entry_orders.append(order)
        if order.status in ("filled", "partial"):
            self.entry_cost += order.fill_value
            self.slippage_cost += order.fill_value * (order.slippage_bps / 10000)
        if not self.entry_time:
            self.entry_time = order.created_at

    def add_exit_order(self, order: PaperOrder) -> None:
        """Ajoute un ordre de sortie."""
        self.exit_orders.append(order)
        if order.status in ("filled", "partial"):
            self.exit_value += order.fill_value
            self.fees += order.fees_paid
            self.slippage_cost += order.fill_value * (order.slippage_bps / 10000)
        self.exit_time = order.created_at

    def close(self, exit_value: float = 0.0, fees: float = 0.0) -> None:
        """Ferme le trade et calcule le P&L final."""
        if exit_value > 0:
            self.exit_value = exit_value
        if fees > 0:
            self.fees = fees

        self.gross_pnl = self.exit_value - self.entry_cost
        self.exit_time = datetime.now()
        self.status = "closed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "strategy": self.strategy,
            "market_id": self.market_id,
            "market_question": self.market_question,
            "entry_orders": [o.to_dict() for o in self.entry_orders],
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "entry_cost": self.entry_cost,
            "exit_orders": [o.to_dict() for o in self.exit_orders],
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_value": self.exit_value,
            "gross_pnl": self.gross_pnl,
            "fees": self.fees,
            "slippage_cost": self.slippage_cost,
            "net_pnl": self.net_pnl,
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "avg_fill_delay_ms": self.avg_fill_delay_ms,
            "avg_slippage_bps": self.avg_slippage_bps,
            "fill_rate": self.fill_rate,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperTrade":
        trade = cls(
            id=data["id"],
            strategy=data["strategy"],
            market_id=data["market_id"],
            market_question=data.get("market_question", ""),
        )
        trade.entry_orders = [PaperOrder.from_dict(o) for o in data.get("entry_orders", [])]
        trade.exit_orders = [PaperOrder.from_dict(o) for o in data.get("exit_orders", [])]
        trade.entry_time = datetime.fromisoformat(data["entry_time"]) if data.get("entry_time") else None
        trade.exit_time = datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None
        trade.entry_cost = data.get("entry_cost", 0.0)
        trade.exit_value = data.get("exit_value", 0.0)
        trade.gross_pnl = data.get("gross_pnl", 0.0)
        trade.fees = data.get("fees", 0.0)
        trade.slippage_cost = data.get("slippage_cost", 0.0)
        trade.status = data.get("status", "open")
        return trade


class PaperTradeStore:
    """
    Stocke et persiste les trades paper.

    Usage:
        store = PaperTradeStore()
        await store.load()

        trade = store.create_trade("gabagool", "market_123")
        trade.add_entry_order(order)
        await store.save()
    """

    def __init__(self, config: Optional[PaperConfig] = None):
        self.config = config or get_paper_config()

        self._trades: Dict[str, PaperTrade] = {}
        self._orders: Dict[str, PaperOrder] = {}
        self._trade_counter = 0
        self._order_counter = 0

        self._trades_file = Path(self.config.trades_file)
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """Charge les trades depuis le fichier JSON."""
        if not self._trades_file.exists():
            return

        def read_sync():
            with open(self._trades_file, "r") as f:
                return json.load(f)

        try:
            data = await asyncio.to_thread(read_sync)
            self._trade_counter = data.get("trade_counter", 0)
            self._order_counter = data.get("order_counter", 0)

            for trade_data in data.get("trades", []):
                trade = PaperTrade.from_dict(trade_data)
                self._trades[trade.id] = trade

                # Indexer les ordres
                for order in trade.entry_orders + trade.exit_orders:
                    self._orders[order.id] = order

        except (json.JSONDecodeError, KeyError) as e:
            print(f"[PaperTradeStore] Error loading trades: {e}")

    async def save(self) -> None:
        """Sauvegarde les trades dans le fichier JSON."""
        async with self._lock:
            data = {
                "trade_counter": self._trade_counter,
                "order_counter": self._order_counter,
                "trades": [t.to_dict() for t in self._trades.values()],
                "summary": self._generate_summary(),
                "last_updated": datetime.now().isoformat(),
            }

            self._trades_file.parent.mkdir(parents=True, exist_ok=True)

            def write_sync():
                with open(self._trades_file, "w") as f:
                    json.dump(data, f, indent=2)

            await asyncio.to_thread(write_sync)

    def create_trade(
        self,
        strategy: str,
        market_id: str,
        market_question: str = ""
    ) -> PaperTrade:
        """Crée un nouveau trade paper."""
        self._trade_counter += 1
        trade_id = f"paper_trade_{self._trade_counter:06d}"

        trade = PaperTrade(
            id=trade_id,
            strategy=strategy,
            market_id=market_id,
            market_question=market_question,
        )

        self._trades[trade_id] = trade
        return trade

    def create_order(
        self,
        token_id: str,
        market_id: str,
        side: str,
        price: float,
        size: float
    ) -> PaperOrder:
        """Crée un nouvel ordre paper."""
        self._order_counter += 1
        order_id = f"paper_order_{self._order_counter:06d}"

        order = PaperOrder(
            id=order_id,
            token_id=token_id,
            market_id=market_id,
            side=side,
            price=price,
            size=size,
        )

        self._orders[order_id] = order
        return order

    def get_trade(self, trade_id: str) -> Optional[PaperTrade]:
        """Récupère un trade par ID."""
        return self._trades.get(trade_id)

    def get_order(self, order_id: str) -> Optional[PaperOrder]:
        """Récupère un ordre par ID."""
        return self._orders.get(order_id)

    def get_open_trades(self) -> List[PaperTrade]:
        """Retourne tous les trades ouverts."""
        return [t for t in self._trades.values() if t.status == "open"]

    def get_closed_trades(self) -> List[PaperTrade]:
        """Retourne tous les trades fermés."""
        return [t for t in self._trades.values() if t.status == "closed"]

    def get_trades_by_strategy(self, strategy: str) -> List[PaperTrade]:
        """Retourne les trades d'une stratégie."""
        return [t for t in self._trades.values() if t.strategy == strategy]

    def get_recent_trades(self, limit: int = 50) -> List[PaperTrade]:
        """Retourne les N derniers trades."""
        sorted_trades = sorted(
            self._trades.values(),
            key=lambda t: t.entry_time or datetime.min,
            reverse=True
        )
        return sorted_trades[:limit]

    def _generate_summary(self) -> Dict[str, Any]:
        """Génère un résumé des statistiques."""
        closed = self.get_closed_trades()

        if not closed:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "net_pnl": 0.0,
            }

        winning = [t for t in closed if t.is_profitable]
        total_pnl = sum(t.net_pnl for t in closed)
        total_fees = sum(t.fees for t in closed)
        total_slippage = sum(t.slippage_cost for t in closed)

        # Stats par stratégie
        gabagool_trades = [t for t in closed if t.strategy == "gabagool"]
        smart_ape_trades = [t for t in closed if t.strategy == "smart_ape"]

        return {
            "total_trades": len(closed),
            "winning_trades": len(winning),
            "losing_trades": len(closed) - len(winning),
            "win_rate": len(winning) / len(closed) if closed else 0.0,
            "net_pnl": total_pnl,
            "total_fees": total_fees,
            "total_slippage": total_slippage,
            "avg_pnl_per_trade": total_pnl / len(closed) if closed else 0.0,
            "gabagool": {
                "trades": len(gabagool_trades),
                "pnl": sum(t.net_pnl for t in gabagool_trades),
            },
            "smart_ape": {
                "trades": len(smart_ape_trades),
                "pnl": sum(t.net_pnl for t in smart_ape_trades),
            },
        }

    def get_summary(self) -> Dict[str, Any]:
        """Retourne le résumé actuel."""
        return self._generate_summary()

    def clear(self) -> None:
        """Efface tous les trades (reset)."""
        self._trades.clear()
        self._orders.clear()
        self._trade_counter = 0
        self._order_counter = 0
