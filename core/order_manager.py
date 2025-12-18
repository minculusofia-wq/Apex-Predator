"""
Order Manager - Gestion des ordres actifs et positions

Fonctionnalités:
1. Suivi des ordres en cours
2. Détection des remplissages (fills)
3. Calcul du PnL en temps réel
4. Fermeture automatique sur conditions
"""

import json
import asyncio
from typing import Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from enum import Enum


class OrderStatus(Enum):
    """Statuts d'un ordre."""
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class ActiveOrder:
    """Ordre actif."""
    id: str
    opportunity_id: str
    market_id: str
    token_id: str
    side: str  # "YES" ou "NO"
    price: float
    size: float
    status: str = "open"
    filled_size: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    
    # 6.2: Production Readiness - Tracking
    fees_paid: float = 0.0
    slippage: float = 0.0
    
    @property
    def is_filled(self) -> bool:
        """Vérifie si l'ordre est complètement rempli."""
        return self.filled_size >= self.size
    
    @property
    def fill_percentage(self) -> float:
        """Pourcentage de remplissage."""
        return (self.filled_size / self.size) * 100 if self.size > 0 else 0
    
    @property
    def value(self) -> float:
        """Valeur de l'ordre."""
        return self.price * self.size
    
    def to_dict(self) -> dict:
        """Convertit en dictionnaire."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        if self.filled_at:
            data["filled_at"] = self.filled_at.isoformat()
        return data


@dataclass
class Position:
    """Position ouverte sur un marché."""
    market_id: str
    question: str
    
    # Ordres associés
    order_yes_id: Optional[str] = None
    order_no_id: Optional[str] = None
    
    # Sizes
    size_yes: float = 0.0
    size_no: float = 0.0
    
    # Prix d'entrée
    entry_price_yes: float = 0.0
    entry_price_no: float = 0.0
    
    # Prix actuels
    current_price_yes: float = 0.0
    current_price_no: float = 0.0
    
    # Timing
    opened_at: datetime = field(default_factory=datetime.now)
    
    # 6.2: Settlement Status
    is_resolved: bool = False
    resolution_val: Optional[str] = None # "YES" or "NO"
    
    @property
    def total_invested(self) -> float:
        """Total investi."""
        return (self.size_yes * self.entry_price_yes) + (self.size_no * self.entry_price_no)
    
    @property
    def current_value(self) -> float:
        """Valeur actuelle."""
        return (self.size_yes * self.current_price_yes) + (self.size_no * self.current_price_no)
    
    @property
    def unrealized_pnl(self) -> float:
        """PnL non réalisé."""
        return self.current_value - self.total_invested
    
    @property
    def unrealized_pnl_percentage(self) -> float:
        """PnL en pourcentage."""
        if self.total_invested == 0:
            return 0
        return (self.unrealized_pnl / self.total_invested) * 100


@dataclass
class TradeHistory:
    """Historique d'un trade complété."""
    id: str
    market_id: str
    question: str
    
    # Ordres
    order_yes_id: str
    order_no_id: str
    
    # Résultat
    entry_value: float
    exit_value: float
    pnl: float
    pnl_percentage: float
    
    # Timing
    opened_at: datetime
    closed_at: datetime
    duration_seconds: int
    
    # 6.2: Production cost tracking
    fees_paid: float = 0.0
    slippage_cost: float = 0.0
    
    def to_dict(self) -> dict:
        """Convertit en dictionnaire."""
        return {
            "id": self.id,
            "market_id": self.market_id,
            "question": self.question,
            "pnl": self.pnl,
            "pnl_percentage": self.pnl_percentage,
            "fees_paid": self.fees_paid,
            "duration_seconds": self.duration_seconds,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
        }


class OrderManager:
    """
    Gère les ordres actifs et les positions.
    
    Usage:
        manager = OrderManager()
        manager.add_order(order)
        position = manager.get_position(market_id)
    """
    
    def __init__(self, trades_file: str = "data/trades.json"):
        self._orders: dict[str, ActiveOrder] = {}
        self._positions: dict[str, Position] = {}
        self._history: list[TradeHistory] = []
        self._trades_file = Path(trades_file)
        
        # Stats
        self._total_pnl = 0.0
        self._total_trades = 0
        self._winning_trades = 0
        
        # Charger l'historique
        self._load_history()
    
    @property
    def open_orders(self) -> list[ActiveOrder]:
        """Liste des ordres ouverts."""
        return [o for o in self._orders.values() if o.status == "open"]
    
    @property
    def open_positions_count(self) -> int:
        """Nombre de positions ouvertes."""
        return len(self._positions)
    
    @property
    def total_exposure(self) -> float:
        """Exposition totale en $."""
        return sum(p.total_invested for p in self._positions.values())
    
    @property
    def stats(self) -> dict:
        """Statistiques globales."""
        return {
            "open_orders": len(self.open_orders),
            "open_positions": self.open_positions_count,
            "total_exposure": self.total_exposure,
            "total_pnl": self._total_pnl,
            "total_trades": self._total_trades,
            "winning_trades": self._winning_trades,
            "win_rate": self._winning_trades / max(1, self._total_trades) * 100,
        }
    
    def add_order(self, order: ActiveOrder) -> None:
        """Ajoute un ordre."""
        self._orders[order.id] = order
    
    def get_order(self, order_id: str) -> Optional[ActiveOrder]:
        """Récupère un ordre par ID."""
        return self._orders.get(order_id)
    
    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_size: Optional[float] = None
    ) -> None:
        """Met à jour le statut d'un ordre."""
        if order_id in self._orders:
            self._orders[order_id].status = status
            if filled_size is not None:
                self._orders[order_id].filled_size = filled_size
            if status == "filled":
                self._orders[order_id].filled_at = datetime.now()
    
    def remove_order(self, order_id: str) -> Optional[ActiveOrder]:
        """Supprime un ordre."""
        return self._orders.pop(order_id, None)
    
    def get_position(self, market_id: str) -> Optional[Position]:
        """Récupère une position par market_id."""
        return self._positions.get(market_id)
    
    def get_all_positions(self) -> list[Position]:
        """Récupère toutes les positions."""
        return list(self._positions.values())
    
    def update_position_prices(
        self,
        market_id: str,
        price_yes: float,
        price_no: float
    ) -> None:
        """Met à jour les prix d'une position."""
        if market_id in self._positions:
            self._positions[market_id].current_price_yes = price_yes
            self._positions[market_id].current_price_no = price_no
    
    def close_position(self, market_id: str, exit_value: float) -> Optional[TradeHistory]:
        """
        Ferme une position et enregistre dans l'historique.
        
        Args:
            market_id: ID du marché
            exit_value: Valeur de sortie
            
        Returns:
            TradeHistory du trade complété
        """
        position = self._positions.pop(market_id, None)
        if not position:
            return None
        
        # Calculer le PnL
        pnl = exit_value - position.total_invested
        pnl_percentage = (pnl / position.total_invested) * 100 if position.total_invested > 0 else 0
        
        # Créer l'entrée d'historique
        now = datetime.now()
        history = TradeHistory(
            id=f"trade_{int(now.timestamp())}",
            market_id=market_id,
            question=position.question,
            order_yes_id=position.order_yes_id or "",
            order_no_id=position.order_no_id or "",
            entry_value=position.total_invested,
            exit_value=exit_value,
            pnl=pnl,
            pnl_percentage=pnl_percentage,
            opened_at=position.opened_at,
            closed_at=now,
            duration_seconds=int((now - position.opened_at).total_seconds())
        )
        
        self._history.append(history)
        
        # Mettre à jour les stats
        self._total_pnl += pnl
        self._total_trades += 1
        if pnl > 0:
            self._winning_trades += 1
        
        # Sauvegarder
        self._save_history()
        
        # Supprimer les ordres associés
        if position.order_yes_id:
            self.remove_order(position.order_yes_id)
        if position.order_no_id:
            self.remove_order(position.order_no_id)
        
        return history
    
    def clear(self) -> None:
        """Efface tous les ordres et positions."""
        self._orders.clear()
        self._positions.clear()
    
    def _load_history(self) -> None:
        """Charge l'historique depuis le fichier."""
        if self._trades_file.exists():
            try:
                with open(self._trades_file, "r") as f:
                    data = json.load(f)
                    self._total_pnl = data.get("total_pnl", 0)
                    self._total_trades = data.get("total_trades", 0)
                    self._winning_trades = data.get("winning_trades", 0)
            except Exception:
                pass
    
    def _save_history_sync(self) -> None:
        """Sauvegarde synchrone de l'historique (interne)."""
        self._trades_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_pnl": self._total_pnl,
            "total_trades": self._total_trades,
            "winning_trades": self._winning_trades,
            "history": [h.to_dict() for h in self._history[-100:]]  # Garder les 100 derniers
        }
        with open(self._trades_file, "w") as f:
            json.dump(data, f, indent=2)

    def _save_history(self) -> None:
        """5.3: Sauvegarde non-bloquante de l'historique."""
        try:
            loop = asyncio.get_running_loop()
            # Si dans un contexte async, exécuter en thread séparé
            loop.run_in_executor(None, self._save_history_sync)
        except RuntimeError:
            # Pas de loop async, exécution synchrone
            self._save_history_sync()

    async def _save_history_async(self) -> None:
        """5.3: Sauvegarde asynchrone de l'historique."""
        await asyncio.to_thread(self._save_history_sync)
    
    def get_daily_pnl(self) -> float:
        """Calcule le PnL du jour."""
        today = datetime.now().date()
        daily_pnl = sum(
            h.pnl for h in self._history
            if h.closed_at.date() == today
        )
        return daily_pnl
    
    def get_recent_trades(self, limit: int = 10) -> list[TradeHistory]:
        """Récupère les trades récents."""
        return self._history[-limit:][::-1]
