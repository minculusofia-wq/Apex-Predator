"""
Paper Capital Manager - Apex Predator v8.0

Gère le capital virtuel pour le paper trading:
- Balance disponible
- Capital alloué aux positions ouvertes
- P&L réalisé et non-réalisé
- Application des frais Polymarket
"""

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Optional, Any

from .config import PaperConfig, get_paper_config


@dataclass
class CapitalSnapshot:
    """Snapshot de l'état du capital à un instant T."""
    timestamp: datetime
    balance: float
    allocated: float
    realized_pnl: float
    unrealized_pnl: float
    total_fees_paid: float
    total_slippage_cost: float

    @property
    def total_equity(self) -> float:
        """Equity totale = balance + allocated + unrealized P&L."""
        return self.balance + self.allocated + self.unrealized_pnl

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "balance": self.balance,
            "allocated": self.allocated,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "total_fees_paid": self.total_fees_paid,
            "total_slippage_cost": self.total_slippage_cost,
            "total_equity": self.total_equity,
        }


@dataclass
class DailyStats:
    """Statistiques journalières du capital."""
    date: str
    starting_balance: float
    ending_balance: float
    realized_pnl: float
    trades_count: int
    fees_paid: float
    slippage_cost: float

    @property
    def net_pnl(self) -> float:
        return self.realized_pnl - self.fees_paid - self.slippage_cost

    @property
    def return_pct(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return (self.ending_balance - self.starting_balance) / self.starting_balance * 100


class PaperCapitalManager:
    """
    Gère le capital virtuel pour le paper trading.

    Fonctionnalités:
    - Allocation/release de capital pour les positions
    - Calcul de P&L avec frais et slippage
    - Snapshots pour tracking de performance
    - Persistence JSON

    Usage:
        manager = PaperCapitalManager()
        await manager.start()

        # Allouer capital pour un trade
        success = manager.allocate(50.0, "market_123")

        # Fermer position avec P&L
        manager.release("market_123", pnl=5.0, fees=0.10, slippage=0.05)
    """

    def __init__(self, config: Optional[PaperConfig] = None):
        self.config = config or get_paper_config()

        # État du capital global
        self._starting_capital = self.config.starting_capital
        self._balance = self.config.starting_capital  # Disponible (legacy)
        self._allocated: Dict[str, float] = {}  # market_id -> montant alloué (legacy)
        self._unrealized_pnl: Dict[str, float] = {}  # market_id -> P&L non réalisé

        # Tracking cumulatif global
        self._realized_pnl = 0.0
        self._total_fees_paid = 0.0
        self._total_slippage_cost = 0.0
        self._trades_count = 0

        # ═══════════════════════════════════════════════════════════════
        # CAPITAL PAR STRATÉGIE (v8.2)
        # ═══════════════════════════════════════════════════════════════
        self._strategy_mode = self.config.strategy_mode

        # Calculer allocation selon le mode
        if self._strategy_mode == "gabagool":
            gabagool_pct, smart_ape_pct = 100.0, 0.0
        elif self._strategy_mode == "smart_ape":
            gabagool_pct, smart_ape_pct = 0.0, 100.0
        else:
            gabagool_pct = self.config.gabagool_capital_pct
            smart_ape_pct = self.config.smart_ape_capital_pct

        # Balances initiales par stratégie
        self._gabagool_starting = self._starting_capital * gabagool_pct / 100
        self._smart_ape_starting = self._starting_capital * smart_ape_pct / 100
        self._gabagool_balance = self._gabagool_starting
        self._smart_ape_balance = self._smart_ape_starting

        # Allocation par stratégie (market_id -> montant)
        self._gabagool_allocated: Dict[str, float] = {}
        self._smart_ape_allocated: Dict[str, float] = {}

        # Unrealized P&L par stratégie
        self._gabagool_unrealized: Dict[str, float] = {}
        self._smart_ape_unrealized: Dict[str, float] = {}

        # P&L réalisé par stratégie
        self._gabagool_realized_pnl = 0.0
        self._smart_ape_realized_pnl = 0.0

        # Frais et slippage par stratégie
        self._gabagool_fees = 0.0
        self._smart_ape_fees = 0.0
        self._gabagool_slippage = 0.0
        self._smart_ape_slippage = 0.0

        # Trades par stratégie
        self._gabagool_trades_count = 0
        self._smart_ape_trades_count = 0

        # Historique
        self._snapshots: list[CapitalSnapshot] = []
        self._daily_stats: Dict[str, DailyStats] = {}

        # Lock pour opérations thread-safe
        self._lock = asyncio.Lock()

        # Persistence
        self._data_file = Path(self.config.stats_file)

    async def start(self) -> None:
        """Initialise le manager et charge les données persistées."""
        await self._load_state()

    async def stop(self) -> None:
        """Sauvegarde l'état avant arrêt."""
        await self._save_state()

    # ═══════════════════════════════════════════════════════════════
    # ALLOCATION / RELEASE
    # ═══════════════════════════════════════════════════════════════

    def allocate(self, amount: float, market_id: str, strategy: str = "gabagool") -> bool:
        """
        Alloue du capital pour une nouvelle position depuis le pool de la stratégie.

        Args:
            amount: Montant à allouer en USDC
            market_id: ID du marché
            strategy: "gabagool" ou "smart_ape"

        Returns:
            True si allocation réussie, False si balance insuffisante ou stratégie désactivée
        """
        if amount <= 0:
            return False

        # Vérifier si la stratégie est activée
        if not self.is_strategy_enabled(strategy):
            return False

        # Allouer depuis le pool de la stratégie
        if strategy == "gabagool":
            if amount > self._gabagool_balance:
                return False
            self._gabagool_balance -= amount
            if market_id in self._gabagool_allocated:
                self._gabagool_allocated[market_id] += amount
            else:
                self._gabagool_allocated[market_id] = amount
        else:  # smart_ape
            if amount > self._smart_ape_balance:
                return False
            self._smart_ape_balance -= amount
            if market_id in self._smart_ape_allocated:
                self._smart_ape_allocated[market_id] += amount
            else:
                self._smart_ape_allocated[market_id] = amount

        # Mettre à jour aussi les totaux legacy pour compatibilité
        self._balance = self._gabagool_balance + self._smart_ape_balance
        self._allocated[market_id] = self._allocated.get(market_id, 0.0) + amount

        return True

    def release(
        self,
        market_id: str,
        pnl: float = 0.0,
        fees: float = 0.0,
        slippage: float = 0.0,
        strategy: str = "gabagool"
    ) -> float:
        """
        Libère le capital alloué et enregistre le P&L pour la stratégie.

        Args:
            market_id: ID du marché
            pnl: Profit/perte brut
            fees: Frais payés (Polymarket 2%)
            slippage: Coût du slippage
            strategy: "gabagool" ou "smart_ape"

        Returns:
            Le montant net retourné à la balance
        """
        # Calculer le net
        net_pnl = pnl - fees - slippage

        # Libérer depuis le pool de la stratégie
        if strategy == "gabagool":
            allocated = self._gabagool_allocated.pop(market_id, 0.0)
            self._gabagool_unrealized.pop(market_id, None)
            returned = allocated + net_pnl
            self._gabagool_balance += returned
            self._gabagool_realized_pnl += net_pnl
            self._gabagool_fees += fees
            self._gabagool_slippage += slippage
            self._gabagool_trades_count += 1
        else:  # smart_ape
            allocated = self._smart_ape_allocated.pop(market_id, 0.0)
            self._smart_ape_unrealized.pop(market_id, None)
            returned = allocated + net_pnl
            self._smart_ape_balance += returned
            self._smart_ape_realized_pnl += net_pnl
            self._smart_ape_fees += fees
            self._smart_ape_slippage += slippage
            self._smart_ape_trades_count += 1

        # Mettre à jour les totaux legacy
        self._allocated.pop(market_id, None)
        self._unrealized_pnl.pop(market_id, None)
        self._balance = self._gabagool_balance + self._smart_ape_balance
        self._realized_pnl = self._gabagool_realized_pnl + self._smart_ape_realized_pnl
        self._total_fees_paid = self._gabagool_fees + self._smart_ape_fees
        self._total_slippage_cost = self._gabagool_slippage + self._smart_ape_slippage
        self._trades_count = self._gabagool_trades_count + self._smart_ape_trades_count

        # Snapshot
        self._take_snapshot()

        return returned

    def update_unrealized_pnl(self, market_id: str, pnl: float) -> None:
        """Met à jour le P&L non réalisé pour une position."""
        self._unrealized_pnl[market_id] = pnl

    # ═══════════════════════════════════════════════════════════════
    # CALCUL DES FRAIS
    # ═══════════════════════════════════════════════════════════════

    def calculate_fee(self, pnl: float) -> float:
        """
        Calcule les frais Polymarket (2% sur les gains uniquement).

        Args:
            pnl: Profit/perte brut

        Returns:
            Frais à payer (0 si perte)
        """
        if pnl <= 0:
            return 0.0
        return pnl * self.config.polymarket_fee_rate

    # ═══════════════════════════════════════════════════════════════
    # GETTERS
    # ═══════════════════════════════════════════════════════════════

    @property
    def balance(self) -> float:
        """Balance disponible (non allouée)."""
        return self._balance

    @property
    def allocated(self) -> float:
        """Total du capital alloué aux positions."""
        return sum(self._allocated.values())

    @property
    def unrealized_pnl(self) -> float:
        """P&L non réalisé total."""
        return sum(self._unrealized_pnl.values())

    @property
    def realized_pnl(self) -> float:
        """P&L réalisé cumulatif."""
        return self._realized_pnl

    @property
    def total_equity(self) -> float:
        """Equity totale (balance + allocated + unrealized)."""
        return self._balance + self.allocated + self.unrealized_pnl

    @property
    def total_return(self) -> float:
        """Rendement total en %."""
        if self._starting_capital <= 0:
            return 0.0
        return (self.total_equity - self._starting_capital) / self._starting_capital * 100

    @property
    def net_pnl(self) -> float:
        """P&L net (réalisé - frais - slippage)."""
        return self._realized_pnl

    @property
    def gross_pnl(self) -> float:
        """P&L brut (avant frais et slippage)."""
        return self._realized_pnl + self._total_fees_paid + self._total_slippage_cost

    def get_position_allocation(self, market_id: str) -> float:
        """Retourne le capital alloué à un marché spécifique."""
        return self._allocated.get(market_id, 0.0)

    def has_capacity(self, amount: float) -> bool:
        """Vérifie si le capital disponible est suffisant."""
        return self._balance >= amount

    # ═══════════════════════════════════════════════════════════════
    # SNAPSHOTS & STATISTIQUES
    # ═══════════════════════════════════════════════════════════════

    def _take_snapshot(self) -> None:
        """Prend un snapshot de l'état actuel."""
        snapshot = CapitalSnapshot(
            timestamp=datetime.now(),
            balance=self._balance,
            allocated=self.allocated,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            total_fees_paid=self._total_fees_paid,
            total_slippage_cost=self._total_slippage_cost,
        )
        self._snapshots.append(snapshot)

        # Limiter à 1000 snapshots
        if len(self._snapshots) > 1000:
            self._snapshots = self._snapshots[-1000:]

    def get_current_snapshot(self) -> CapitalSnapshot:
        """Retourne le snapshot actuel."""
        return CapitalSnapshot(
            timestamp=datetime.now(),
            balance=self._balance,
            allocated=self.allocated,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            total_fees_paid=self._total_fees_paid,
            total_slippage_cost=self._total_slippage_cost,
        )

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques complètes."""
        return {
            "starting_capital": self._starting_capital,
            "current_balance": self._balance,
            "allocated": self.allocated,
            "total_equity": self.total_equity,
            "realized_pnl": self._realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "total_fees_paid": self._total_fees_paid,
            "total_slippage_cost": self._total_slippage_cost,
            "total_return_pct": self.total_return,
            "trades_count": self._trades_count,
            "open_positions": len(self._allocated),
            "strategy_mode": self._strategy_mode,
        }

    # ═══════════════════════════════════════════════════════════════
    # MÉTHODES PAR STRATÉGIE (v8.2)
    # ═══════════════════════════════════════════════════════════════

    @property
    def strategy_mode(self) -> str:
        """Retourne le mode de stratégie actif."""
        return self._strategy_mode

    def is_strategy_enabled(self, strategy: str) -> bool:
        """Vérifie si une stratégie est activée selon le mode."""
        if self._strategy_mode == "both":
            return True
        return self._strategy_mode == strategy

    def get_strategy_balance(self, strategy: str) -> float:
        """Retourne la balance disponible d'une stratégie."""
        if strategy == "gabagool":
            return self._gabagool_balance
        return self._smart_ape_balance

    def get_strategy_allocated(self, strategy: str) -> float:
        """Retourne le capital alloué d'une stratégie."""
        if strategy == "gabagool":
            return sum(self._gabagool_allocated.values())
        return sum(self._smart_ape_allocated.values())

    def get_strategy_equity(self, strategy: str) -> float:
        """Retourne l'equity totale d'une stratégie (balance + allocated + unrealized)."""
        if strategy == "gabagool":
            unrealized = sum(self._gabagool_unrealized.values())
            return self._gabagool_balance + sum(self._gabagool_allocated.values()) + unrealized
        unrealized = sum(self._smart_ape_unrealized.values())
        return self._smart_ape_balance + sum(self._smart_ape_allocated.values()) + unrealized

    def get_strategy_stats(self, strategy: str) -> Dict[str, Any]:
        """Retourne les statistiques complètes d'une stratégie."""
        if strategy == "gabagool":
            starting = self._gabagool_starting
            balance = self._gabagool_balance
            allocated = sum(self._gabagool_allocated.values())
            unrealized = sum(self._gabagool_unrealized.values())
            realized_pnl = self._gabagool_realized_pnl
            fees = self._gabagool_fees
            slippage = self._gabagool_slippage
            trades = self._gabagool_trades_count
            positions = len(self._gabagool_allocated)
        else:
            starting = self._smart_ape_starting
            balance = self._smart_ape_balance
            allocated = sum(self._smart_ape_allocated.values())
            unrealized = sum(self._smart_ape_unrealized.values())
            realized_pnl = self._smart_ape_realized_pnl
            fees = self._smart_ape_fees
            slippage = self._smart_ape_slippage
            trades = self._smart_ape_trades_count
            positions = len(self._smart_ape_allocated)

        total_equity = balance + allocated + unrealized
        return_pct = ((total_equity - starting) / starting * 100) if starting > 0 else 0.0

        return {
            "strategy": strategy,
            "enabled": self.is_strategy_enabled(strategy),
            "starting_capital": starting,
            "balance": balance,
            "allocated": allocated,
            "unrealized_pnl": unrealized,
            "total_equity": total_equity,
            "realized_pnl": realized_pnl,
            "total_fees": fees,
            "total_slippage": slippage,
            "return_pct": return_pct,
            "trades_count": trades,
            "open_positions": positions,
        }

    def has_strategy_capacity(self, strategy: str, amount: float) -> bool:
        """Vérifie si la stratégie a assez de capital disponible."""
        if not self.is_strategy_enabled(strategy):
            return False
        return self.get_strategy_balance(strategy) >= amount

    def update_strategy_unrealized_pnl(self, market_id: str, strategy: str, pnl: float) -> None:
        """Met à jour le P&L non réalisé pour une position d'une stratégie."""
        if strategy == "gabagool":
            self._gabagool_unrealized[market_id] = pnl
        else:
            self._smart_ape_unrealized[market_id] = pnl
        # Mettre à jour aussi le total legacy
        self._unrealized_pnl[market_id] = pnl

    # ═══════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════════

    async def _save_state(self) -> None:
        """Sauvegarde l'état dans un fichier JSON."""
        state = {
            "starting_capital": self._starting_capital,
            "balance": self._balance,
            "allocated": self._allocated,
            "unrealized_pnl": self._unrealized_pnl,
            "realized_pnl": self._realized_pnl,
            "total_fees_paid": self._total_fees_paid,
            "total_slippage_cost": self._total_slippage_cost,
            "trades_count": self._trades_count,
            "last_updated": datetime.now().isoformat(),
            # Données par stratégie (v8.2)
            "strategy_mode": self._strategy_mode,
            "gabagool": {
                "starting": self._gabagool_starting,
                "balance": self._gabagool_balance,
                "allocated": self._gabagool_allocated,
                "unrealized": self._gabagool_unrealized,
                "realized_pnl": self._gabagool_realized_pnl,
                "fees": self._gabagool_fees,
                "slippage": self._gabagool_slippage,
                "trades_count": self._gabagool_trades_count,
            },
            "smart_ape": {
                "starting": self._smart_ape_starting,
                "balance": self._smart_ape_balance,
                "allocated": self._smart_ape_allocated,
                "unrealized": self._smart_ape_unrealized,
                "realized_pnl": self._smart_ape_realized_pnl,
                "fees": self._smart_ape_fees,
                "slippage": self._smart_ape_slippage,
                "trades_count": self._smart_ape_trades_count,
            },
        }

        self._data_file.parent.mkdir(parents=True, exist_ok=True)

        def write_sync():
            with open(self._data_file, "w") as f:
                json.dump(state, f, indent=2)

        await asyncio.to_thread(write_sync)

    async def _load_state(self) -> None:
        """Charge l'état depuis le fichier JSON."""
        if not self._data_file.exists():
            return

        def read_sync():
            with open(self._data_file, "r") as f:
                return json.load(f)

        try:
            state = await asyncio.to_thread(read_sync)

            # Données globales legacy
            self._starting_capital = state.get("starting_capital", self.config.starting_capital)
            self._balance = state.get("balance", self._starting_capital)
            self._allocated = state.get("allocated", {})
            self._unrealized_pnl = state.get("unrealized_pnl", {})
            self._realized_pnl = state.get("realized_pnl", 0.0)
            self._total_fees_paid = state.get("total_fees_paid", 0.0)
            self._total_slippage_cost = state.get("total_slippage_cost", 0.0)
            self._trades_count = state.get("trades_count", 0)

            # Données Gabagool (v8.2)
            if "gabagool" in state:
                gab = state["gabagool"]
                self._gabagool_starting = gab.get("starting", self._gabagool_starting)
                self._gabagool_balance = gab.get("balance", self._gabagool_balance)
                self._gabagool_allocated = gab.get("allocated", {})
                self._gabagool_unrealized = gab.get("unrealized", {})
                self._gabagool_realized_pnl = gab.get("realized_pnl", 0.0)
                self._gabagool_fees = gab.get("fees", 0.0)
                self._gabagool_slippage = gab.get("slippage", 0.0)
                self._gabagool_trades_count = gab.get("trades_count", 0)

            # Données Smart Ape (v8.2)
            if "smart_ape" in state:
                sa = state["smart_ape"]
                self._smart_ape_starting = sa.get("starting", self._smart_ape_starting)
                self._smart_ape_balance = sa.get("balance", self._smart_ape_balance)
                self._smart_ape_allocated = sa.get("allocated", {})
                self._smart_ape_unrealized = sa.get("unrealized", {})
                self._smart_ape_realized_pnl = sa.get("realized_pnl", 0.0)
                self._smart_ape_fees = sa.get("fees", 0.0)
                self._smart_ape_slippage = sa.get("slippage", 0.0)
                self._smart_ape_trades_count = sa.get("trades_count", 0)

        except (json.JSONDecodeError, KeyError) as e:
            # En cas d'erreur, partir d'un état frais
            print(f"[PaperCapitalManager] Error loading state: {e}, starting fresh")

    def reset(self) -> None:
        """Remet le capital à son état initial."""
        # Reset global legacy
        self._balance = self._starting_capital
        self._allocated.clear()
        self._unrealized_pnl.clear()
        self._realized_pnl = 0.0
        self._total_fees_paid = 0.0
        self._total_slippage_cost = 0.0
        self._trades_count = 0
        self._snapshots.clear()

        # Reset Gabagool
        self._gabagool_balance = self._gabagool_starting
        self._gabagool_allocated.clear()
        self._gabagool_unrealized.clear()
        self._gabagool_realized_pnl = 0.0
        self._gabagool_fees = 0.0
        self._gabagool_slippage = 0.0
        self._gabagool_trades_count = 0

        # Reset Smart Ape
        self._smart_ape_balance = self._smart_ape_starting
        self._smart_ape_allocated.clear()
        self._smart_ape_unrealized.clear()
        self._smart_ape_realized_pnl = 0.0
        self._smart_ape_fees = 0.0
        self._smart_ape_slippage = 0.0
        self._smart_ape_trades_count = 0
