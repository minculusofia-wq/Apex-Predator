"""
Paper Executor - Apex Predator v8.0

Exécuteur de trades paper avec interface compatible avec OrderExecutor.
Permet le swap transparent entre mode réel et paper.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Dict, Any, List, TYPE_CHECKING

if TYPE_CHECKING:
    from core.local_orderbook import OrderbookManager
    from core.analyzer import Opportunity

from .config import PaperConfig, get_paper_config
from .fill_simulator import FillSimulator, FillResult, FillType
from .capital_manager import PaperCapitalManager
from .trade_store import PaperTradeStore, PaperTrade, PaperOrder


class PaperExecutorState(Enum):
    """États de l'exécuteur paper."""
    STOPPED = "stopped"
    READY = "ready"
    EXECUTING = "executing"
    PAUSED = "paused"


@dataclass(slots=True)
class PaperTradeResult:
    """Résultat d'un trade paper."""
    opportunity_id: str
    success: bool
    order_yes_id: Optional[str] = None
    order_no_id: Optional[str] = None
    trade_id: Optional[str] = None
    error_message: Optional[str] = None
    executed_at: datetime = field(default_factory=datetime.now)

    @property
    def is_partial(self) -> bool:
        """Vérifie si un seul ordre a réussi."""
        return (self.order_yes_id is not None) != (self.order_no_id is not None)


class PaperExecutor:
    """
    Exécuteur paper trading avec interface compatible OrderExecutor.

    Caractéristiques:
    - Utilise données de marché réelles
    - Simule fills réalistes (slippage, délais, partiels)
    - Gère capital virtuel
    - Persiste historique des trades

    Usage:
        executor = PaperExecutor(orderbook_manager)
        await executor.start()

        result = await executor.execute_opportunity(opportunity)
    """

    def __init__(
        self,
        orderbook_manager: Optional["OrderbookManager"] = None,
        config: Optional[PaperConfig] = None,
        capital_manager: Optional[PaperCapitalManager] = None,
        trade_store: Optional[PaperTradeStore] = None,
    ):
        self.config = config or get_paper_config()

        # Composants
        self.fill_simulator = FillSimulator(orderbook_manager, self.config)
        self.capital_manager = capital_manager or PaperCapitalManager(self.config)
        self.trade_store = trade_store or PaperTradeStore(self.config)

        # État
        self._state = PaperExecutorState.STOPPED
        self._is_paper_mode = True  # Toujours True pour identification

        # Stats
        self._trades_today = 0
        self._successful_trades = 0
        self._failed_trades = 0
        self._last_trade_time: Optional[datetime] = None

        # Ordres actifs
        self._active_orders: Dict[str, PaperOrder] = {}
        self._active_trades: Dict[str, PaperTrade] = {}

        # Callbacks (compatibilité avec OrderExecutor)
        self.on_trade_start: Optional[Callable] = None
        self.on_trade_success: Optional[Callable] = None
        self.on_trade_failure: Optional[Callable] = None
        self.on_fill: Optional[Callable] = None

        # Lock pour exécution séquentielle
        self._execution_lock = asyncio.Lock()

    # ═══════════════════════════════════════════════════════════════
    # PROPRIÉTÉS
    # ═══════════════════════════════════════════════════════════════

    @property
    def state(self) -> PaperExecutorState:
        """État actuel de l'exécuteur."""
        return self._state

    @property
    def is_paper_mode(self) -> bool:
        """Indique que c'est le mode paper."""
        return True

    @property
    def strategy_mode(self) -> str:
        """Retourne le mode de stratégie actif."""
        return self.capital_manager.strategy_mode

    def _is_strategy_allowed(self, strategy: str) -> bool:
        """Vérifie si la stratégie est autorisée selon le mode."""
        return self.capital_manager.is_strategy_enabled(strategy)

    async def start(self) -> None:
        """Démarre l'exécuteur paper."""
        await self.capital_manager.start()
        await self.trade_store.load()
        self._state = PaperExecutorState.READY
        print("📝 [PaperExecutor] Started in PAPER TRADING mode")
        print(f"📝 [PaperExecutor] Starting capital: ${self.capital_manager.balance:.2f}")

    async def stop(self) -> None:
        """Arrête l'exécuteur paper."""
        self._state = PaperExecutorState.STOPPED
        await self.capital_manager.stop()
        await self.trade_store.save()
        print("📝 [PaperExecutor] Stopped")

    def pause(self) -> None:
        """Met en pause l'exécuteur."""
        if self._state == PaperExecutorState.READY:
            self._state = PaperExecutorState.PAUSED

    def resume(self) -> None:
        """Reprend l'exécuteur."""
        if self._state == PaperExecutorState.PAUSED:
            self._state = PaperExecutorState.READY

    # ═══════════════════════════════════════════════════════════════
    # EXÉCUTION DES TRADES
    # ═══════════════════════════════════════════════════════════════

    async def execute_opportunity(
        self,
        opportunity: "Opportunity",
        strategy: str = "gabagool"
    ) -> PaperTradeResult:
        """
        Exécute une opportunité en mode paper.

        Args:
            opportunity: L'opportunité à exécuter
            strategy: "gabagool" ou "smart_ape"

        Returns:
            PaperTradeResult avec les détails du trade
        """
        async with self._execution_lock:
            self._state = PaperExecutorState.EXECUTING

            try:
                # 0. Vérifier si la stratégie est autorisée (v8.2)
                if not self._is_strategy_allowed(strategy):
                    return PaperTradeResult(
                        opportunity_id=opportunity.id,
                        success=False,
                        error_message=f"Strategy '{strategy}' disabled in mode '{self.strategy_mode}'",
                    )

                # 1. Vérifier si on peut trader
                can_trade, reason = self._can_trade(opportunity, strategy)
                if not can_trade:
                    return PaperTradeResult(
                        opportunity_id=opportunity.id,
                        success=False,
                        error_message=reason,
                    )

                # 2. Callback de début
                if self.on_trade_start:
                    self.on_trade_start(opportunity)

                # 3. Créer le trade
                trade = self.trade_store.create_trade(
                    strategy=strategy,
                    market_id=opportunity.market_id,
                    market_question=getattr(opportunity, 'question', ''),
                )

                # 4. Calculer la taille de l'ordre
                order_size = self._calculate_order_size(opportunity, strategy)

                # 5. Allouer le capital depuis le pool de la stratégie (v8.2)
                total_cost = order_size * (opportunity.recommended_price_yes + opportunity.recommended_price_no)
                if not self.capital_manager.allocate(total_cost, opportunity.market_id, strategy=strategy):
                    strat_balance = self.capital_manager.get_strategy_balance(strategy)
                    return PaperTradeResult(
                        opportunity_id=opportunity.id,
                        success=False,
                        error_message=f"Insufficient {strategy} capital: need ${total_cost:.2f}, have ${strat_balance:.2f}",
                    )

                # 6. Placer les ordres bilatéraux (YES + NO)
                result = await self._place_bilateral_orders(
                    trade=trade,
                    opportunity=opportunity,
                    size=order_size,
                )

                # 7. Finaliser
                if result.success:
                    self._successful_trades += 1
                    self._trades_today += 1
                    self._last_trade_time = datetime.now()

                    if self.on_trade_success:
                        self.on_trade_success(result)
                else:
                    self._failed_trades += 1
                    # Libérer le capital si échec (v8.2: passer la stratégie)
                    self.capital_manager.release(opportunity.market_id, strategy=strategy)

                    if self.on_trade_failure:
                        self.on_trade_failure(result)

                await self.trade_store.save()
                return result

            finally:
                self._state = PaperExecutorState.READY

    async def _place_bilateral_orders(
        self,
        trade: PaperTrade,
        opportunity: "Opportunity",
        size: float
    ) -> PaperTradeResult:
        """Place les ordres YES et NO en parallèle."""

        # Créer les ordres
        order_yes = self.trade_store.create_order(
            token_id=opportunity.token_yes_id,
            market_id=opportunity.market_id,
            side="BUY",
            price=opportunity.recommended_price_yes,
            size=size,
        )

        order_no = self.trade_store.create_order(
            token_id=opportunity.token_no_id,
            market_id=opportunity.market_id,
            side="BUY",
            price=opportunity.recommended_price_no,
            size=size,
        )

        # Simuler les fills en parallèle
        results = await asyncio.gather(
            self.fill_simulator.simulate_order(
                token_id=opportunity.token_yes_id,
                side="BUY",
                price=opportunity.recommended_price_yes,
                size=size,
                market_id=opportunity.market_id,
            ),
            self.fill_simulator.simulate_order(
                token_id=opportunity.token_no_id,
                side="BUY",
                price=opportunity.recommended_price_no,
                size=size,
                market_id=opportunity.market_id,
            ),
            return_exceptions=True,
        )

        fill_yes, fill_no = results

        # Gérer les exceptions
        if isinstance(fill_yes, Exception):
            fill_yes = FillResult(fill_type=FillType.REJECTED, rejection_reason=str(fill_yes))
        if isinstance(fill_no, Exception):
            fill_no = FillResult(fill_type=FillType.REJECTED, rejection_reason=str(fill_no))

        # Appliquer les résultats aux ordres
        order_yes.apply_fill_result(fill_yes)
        order_no.apply_fill_result(fill_no)

        # Ajouter au trade
        trade.add_entry_order(order_yes)
        trade.add_entry_order(order_no)

        # Vérifier le succès
        yes_success = fill_yes.is_successful
        no_success = fill_no.is_successful

        if yes_success and no_success:
            # Succès complet - calculer le P&L potentiel
            self._active_trades[trade.id] = trade

            return PaperTradeResult(
                opportunity_id=opportunity.id,
                success=True,
                order_yes_id=order_yes.id,
                order_no_id=order_no.id,
                trade_id=trade.id,
            )

        elif yes_success or no_success:
            # Succès partiel - position non hedgée
            trade.status = "partial"
            error = "Partial fill: " + (
                f"YES filled, NO rejected ({fill_no.rejection_reason})"
                if yes_success
                else f"NO filled, YES rejected ({fill_yes.rejection_reason})"
            )

            return PaperTradeResult(
                opportunity_id=opportunity.id,
                success=False,
                order_yes_id=order_yes.id if yes_success else None,
                order_no_id=order_no.id if no_success else None,
                trade_id=trade.id,
                error_message=error,
            )

        else:
            # Échec total
            trade.status = "cancelled"
            return PaperTradeResult(
                opportunity_id=opportunity.id,
                success=False,
                error_message=f"Both orders rejected: YES={fill_yes.rejection_reason}, NO={fill_no.rejection_reason}",
            )

    def _can_trade(self, opportunity: "Opportunity", strategy: str = "gabagool") -> tuple[bool, str]:
        """Vérifie si on peut exécuter le trade pour une stratégie."""
        if self._state != PaperExecutorState.READY:
            return False, f"Executor not ready: {self._state.value}"

        # Vérifier le capital disponible pour la stratégie (v8.2)
        required = self._calculate_order_size(opportunity, strategy) * 2  # YES + NO
        if not self.capital_manager.has_strategy_capacity(strategy, required):
            strat_balance = self.capital_manager.get_strategy_balance(strategy)
            return False, f"Insufficient {strategy} capital: need ${required:.2f}, have ${strat_balance:.2f}"

        return True, ""

    def _calculate_order_size(
        self,
        opportunity: "Opportunity",
        strategy: str = "gabagool"
    ) -> float:
        """Calcule la taille de l'ordre basée sur la configuration."""
        # Utiliser la taille par défaut du config
        from config import get_trading_params
        params = get_trading_params()

        if strategy == "gabagool":
            return params.get_gabagool_trade_size()
        elif strategy == "smart_ape":
            return params.get_smart_ape_trade_size()
        else:
            return params.capital_per_trade

    # ═══════════════════════════════════════════════════════════════
    # INTERFACE COMPATIBLE AVEC OrderExecutor
    # ═══════════════════════════════════════════════════════════════

    async def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        time_in_force: str = "GTC",
        market_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Place un ordre individuel (compatible avec PolymarketPrivateClient).

        Returns:
            Dict avec id, status, etc.
        """
        order = self.trade_store.create_order(
            token_id=token_id,
            market_id=market_id or "unknown",
            side=side,
            price=price,
            size=size,
        )

        result = await self.fill_simulator.simulate_order(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            market_id=market_id,
        )

        order.apply_fill_result(result)
        self._active_orders[order.id] = order

        if self.on_fill and result.is_successful:
            self.on_fill(market_id, side, result.filled_size, result.avg_fill_price)

        return {
            "id": order.id,
            "status": "LIVE" if result.is_successful else "REJECTED",
            "size": size,
            "sizeMatched": result.filled_size,
            "price": result.avg_fill_price,
            "side": side,
            "is_paper": True,
        }

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """Récupère les détails d'un ordre."""
        order = self.trade_store.get_order(order_id)
        if not order:
            return {"error": "Order not found"}

        return {
            "id": order.id,
            "status": order.status,
            "size": order.size,
            "sizeMatched": order.filled_size,
            "price": order.price,
            "avgPrice": order.avg_fill_price,
            "side": order.side,
            "is_paper": True,
        }

    async def cancel_order(self, order_id: str) -> bool:
        """Annule un ordre (toujours réussi en paper mode)."""
        order = self.trade_store.get_order(order_id)
        if order and order.status == "pending":
            order.status = "cancelled"
            return True
        return False

    async def get_balance(self) -> Dict[str, float]:
        """Retourne la balance paper."""
        return {
            "USDC": self.capital_manager.balance,
            "allocated": self.capital_manager.allocated,
            "total_equity": self.capital_manager.total_equity,
            "is_paper": True,
        }

    # ═══════════════════════════════════════════════════════════════
    # STATISTIQUES
    # ═══════════════════════════════════════════════════════════════

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques de l'exécuteur."""
        capital_stats = self.capital_manager.get_stats()
        trade_summary = self.trade_store.get_summary()

        return {
            "mode": "PAPER",
            "state": self._state.value,
            "trades_today": self._trades_today,
            "successful_trades": self._successful_trades,
            "failed_trades": self._failed_trades,
            "last_trade_time": self._last_trade_time.isoformat() if self._last_trade_time else None,
            "capital": capital_stats,
            "trades": trade_summary,
        }

    async def close_position(
        self,
        market_id: str,
        pnl: float = 0.0,
        reason: str = "manual",
        strategy: str = None
    ) -> bool:
        """Ferme une position paper et enregistre le P&L."""
        # Trouver le trade
        for trade in self._active_trades.values():
            if trade.market_id == market_id:
                # Utiliser la stratégie du trade si non spécifiée
                strat = strategy or trade.strategy

                # Calculer les frais
                fees = self.capital_manager.calculate_fee(pnl)

                # Fermer le trade
                trade.close(exit_value=trade.entry_cost + pnl, fees=fees)

                # Libérer le capital vers le pool de la stratégie (v8.2)
                self.capital_manager.release(market_id, pnl=pnl, fees=fees, strategy=strat)

                # Retirer des trades actifs
                del self._active_trades[trade.id]

                await self.trade_store.save()
                return True

        return False
