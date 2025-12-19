"""
Order Executor - Exécute les trades automatiquement

Fonctionnalités:
1. Reçoit les opportunités de l'analyzer
2. Vérifie les conditions de trading
3. Place les ordres bilatéraux (YES + NO)
4. Monitore l'exécution
5. Gère les erreurs et retries

Optimisation 4.1: Intégration OrderQueue pour exécution non-bloquante
"""

import asyncio
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from core.analyzer import Opportunity, OpportunityAction
from core.order_manager import OrderManager, ActiveOrder
from core.order_queue import OrderQueue, QueuedOrder, OrderPriority, QueueOrderStatus
from core.fill_manager import FillManager
from api.private import PolymarketPrivateClient, PolymarketCredentials
from api.private.polymarket_private import OrderSide
from config import get_settings, get_trading_params, TradingParams


class ExecutorState(Enum):
    """États de l'executor."""
    STOPPED = "stopped"
    READY = "ready"
    EXECUTING = "executing"
    PAUSED = "paused"


@dataclass(slots=True)
class TradeResult:
    """Résultat d'un trade (slots=True pour performance HFT)."""
    opportunity_id: str
    success: bool
    order_yes_id: Optional[str] = None
    order_no_id: Optional[str] = None
    error_message: Optional[str] = None
    executed_at: datetime = field(default_factory=datetime.now)
    
    @property
    def is_partial(self) -> bool:
        """Vérifie si un seul ordre a réussi."""
        return (self.order_yes_id is not None) != (self.order_no_id is not None)


class OrderExecutor:
    """
    Exécute les trades automatiquement quand les opportunités sont détectées.
    
    Usage:
        executor = OrderExecutor(credentials)
        await executor.start()
        result = await executor.execute_opportunity(opportunity)
    """
    
    def __init__(
        self,
        credentials: Optional[PolymarketCredentials] = None,
        order_manager: Optional[OrderManager] = None
    ):
        self.settings = get_settings()
        self._params: TradingParams = get_trading_params()
        self._credentials = credentials
        self._order_manager = order_manager or OrderManager()
        
        self._state = ExecutorState.STOPPED
        self._client: Optional[PolymarketPrivateClient] = None

        # Stats
        self._trades_today = 0
        self._successful_trades = 0
        self._failed_trades = 0
        self._last_trade_time: Optional[datetime] = None

        # 6.1: Risk Management - Circuit Breaker
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5  # Seuil avant pause
        self._pause_duration_seconds = 60   # Durée de la pause

        # 5.2: Locks par marché (au lieu d'un lock global)
        # Permet des trades parallèles sur différents marchés
        self._market_locks: Dict[str, asyncio.Lock] = {}

        # 4.1: Order Queue pour exécution non-bloquante
        self._order_queue: Optional[OrderQueue] = None
        self._use_queue: bool = True  # Activer par défaut

        # 7.0: Fill Manager for reconciliation
        self.fill_manager: Optional[FillManager] = None

        # HFT: Connection warming task
        self._warmup_task: Optional[asyncio.Task] = None
        self._warmup_interval: float = 30.0  # Secondes entre chaque warmup

        # Callbacks
        self.on_trade_start: Optional[Callable[[Opportunity], None]] = None
        self.on_trade_success: Optional[Callable[[TradeResult], None]] = None
        self.on_trade_failure: Optional[Callable[[TradeResult], None]] = None
        self.on_state_change: Optional[Callable[[ExecutorState], None]] = None
        self.on_fill: Optional[Callable[[str, str, float, float], None]] = None
        self.on_order_end: Optional[Callable[[str, str, float], None]] = None

    @property
    def state(self) -> ExecutorState:
        """État actuel de l'executor."""
        return self._state
    
    @property
    def is_ready(self) -> bool:
        """Vérifie si l'executor est prêt à trader."""
        return self._state == ExecutorState.READY and self._credentials is not None
    
    @property
    def stats(self) -> dict:
        """Statistiques de trading."""
        base_stats = {
            "trades_today": self._trades_today,
            "successful": self._successful_trades,
            "failed": self._failed_trades,
            "win_rate": self._successful_trades / max(1, self._trades_today) * 100,
            "last_trade": self._last_trade_time.isoformat() if self._last_trade_time else None,
        }

        # 4.1: Ajouter stats de la queue si disponible
        if self._order_queue:
            queue_stats = self._order_queue.stats
            base_stats["queue"] = {
                "size": queue_stats.current_queue_size,
                "processing": queue_stats.current_processing,
                "total_completed": queue_stats.total_completed,
                "total_failed": queue_stats.total_failed,
                "avg_time_ms": round(queue_stats.avg_processing_time_ms, 2),
            }

        return base_stats
    
    def set_credentials(self, credentials: PolymarketCredentials) -> None:
        """Configure les credentials."""
        self._credentials = credentials
    
    def update_params(self, params: TradingParams) -> None:
        """Met à jour les paramètres de trading."""
        self._params = params
    
    def _set_state(self, state: ExecutorState) -> None:
        """Change l'état."""
        self._state = state
        if self.on_state_change:
            self.on_state_change(state)
    
    async def start(self) -> bool:
        """
        Démarre l'executor.

        Returns:
            True si démarré, False si erreur
        """
        if not self._credentials or not self._credentials.is_valid:
            print("❌ [Executor] Credentials invalides ou manquantes")
            return False

        try:
            self._client = PolymarketPrivateClient(self._credentials)

            # HFT: Pré-chauffer les connexions TLS (gain ~50-150ms sur premier ordre)
            await self._client.warm_connections()

            # 4.1: Démarrer la queue d'ordres
            if self._use_queue and self._client:
                self._order_queue = OrderQueue(
                    private_client=self._client,
                    max_concurrent=3,
                    max_retries=2
                )
                # Configurer les callbacks
                self._order_queue.on_order_complete = self._on_queue_order_complete
                self._order_queue.on_order_failed = self._on_queue_order_failed
                await self._order_queue.start()

            # 7.0: Démarrer Fill Manager
            if self._client:
                self.fill_manager = FillManager(self._client)
                self.fill_manager.on_fill = self._on_fill_callback
                self.fill_manager.on_order_end = self._on_order_end_callback
                await self.fill_manager.start()

            # HFT: Démarrer la tâche de connection warming périodique
            if self._client:
                self._warmup_task = asyncio.create_task(self._connection_warmup_loop())

            self._set_state(ExecutorState.READY)
            return True
        except Exception as e:
            print(f"❌ [Executor] Erreur démarrage: {e}")
            self._set_state(ExecutorState.STOPPED)
            return False
    
    async def stop(self) -> None:
        """Arrête l'executor."""
        # HFT: Arrêter la tâche de connection warming
        if self._warmup_task:
            self._warmup_task.cancel()
            try:
                await self._warmup_task
            except asyncio.CancelledError:
                pass
            self._warmup_task = None

        # 7.0: Stop Fill Manager
        if self.fill_manager:
            await self.fill_manager.stop()
            self.fill_manager = None

        # 4.1: Arrêter la queue d'ordres
        if self._order_queue:
            await self._order_queue.stop()
            self._order_queue = None

        if self._client:
            self._client = None

        # Cleanup: Libérer les locks de marché (évite memory leak)
        self._market_locks.clear()

        self._set_state(ExecutorState.STOPPED)
    
    def pause(self) -> None:
        """Met l'executor en pause."""
        if self._state == ExecutorState.READY:
            self._set_state(ExecutorState.PAUSED)
    
    def resume(self) -> None:
        """Reprend l'execution."""
        if self._state == ExecutorState.PAUSED:
            # Si la pause était due au circuit breaker, on reset le compteur
            if self._consecutive_failures >= self._max_consecutive_failures:
                print("Circuit Breaker: Reprise manuelle, reset du compteur d'échecs.")
                self._consecutive_failures = 0

            self._set_state(ExecutorState.READY)
    
    async def can_trade(self) -> tuple[bool, str]:
        """
        Vérifie si on peut trader maintenant.
        
        Returns:
            Tuple (peut_trader, raison si non)
        """
        # Vérifier l'état
        if self._state != ExecutorState.READY:
            return False, f"Executor non prêt (état: {self._state.value})"
        
        # Vérifier le trading automatique
        if not self._params.auto_trading_enabled:
            return False, "Trading automatique désactivé"
        
        # Vérifier le délai entre trades
        if self._last_trade_time:
            elapsed = (datetime.now() - self._last_trade_time).total_seconds()
            if elapsed < self._params.min_time_between_trades:
                remaining = self._params.min_time_between_trades - elapsed
                return False, f"Attendre {remaining:.0f}s avant prochain trade"
        
        # Vérifier le nombre de positions ouvertes
        open_positions = self._order_manager.open_positions_count
        if open_positions >= self._params.max_open_positions:
            return False, f"Limite de positions atteinte ({open_positions}/{self._params.max_open_positions})"
        
        # Vérifier l'exposition totale
        current_exposure = self._order_manager.total_exposure
        if current_exposure + self._params.capital_per_trade > self._params.max_total_exposure:
            return False, f"Exposition max atteinte (${current_exposure:.2f}/${self._params.max_total_exposure:.2f})"
        
        return True, ""
    
    def _get_market_lock(self, market_id: str) -> asyncio.Lock:
        """5.2: Récupère ou crée un lock pour un marché spécifique."""
        if market_id not in self._market_locks:
            self._market_locks[market_id] = asyncio.Lock()
        return self._market_locks[market_id]

    async def execute_opportunity(self, opportunity: Opportunity) -> TradeResult:
        """
        Exécute un trade sur une opportunité.

        Args:
            opportunity: L'opportunité à trader

        Returns:
            TradeResult avec les détails du trade
        """
        # 5.2: Lock par marché - permet des trades parallèles sur différents marchés
        market_lock = self._get_market_lock(opportunity.market_id)
        async with market_lock:
            self._set_state(ExecutorState.EXECUTING)
            
            try:
                # Vérifier si on peut trader
                can_trade, reason = await self.can_trade()
                if not can_trade:
                    # Log seulement si auto-trading désactivé (une fois par opportunité)
                    if "automatique désactivé" in reason:
                        print(f"⏸️ [Auto Trading OFF] Opportunité ignorée: {opportunity.symbol}")
                    return TradeResult(
                        opportunity_id=opportunity.id,
                        success=False,
                        error_message=reason
                    )
                
                # Vérifier l'opportunité
                if opportunity.action != OpportunityAction.TRADE:
                    return TradeResult(
                        opportunity_id=opportunity.id,
                        success=False,
                        error_message="Opportunité non éligible au trading"
                    )
                
                # 6.1: Risk Management - Vérification de slippage juste avant le trade
                current_cost = opportunity.recommended_price_yes + opportunity.recommended_price_no
                if current_cost > self._params.max_pair_cost_slippage_check:
                    self._failed_trades += 1 # Compte comme un échec
                    return TradeResult(
                        opportunity_id=opportunity.id,
                        success=False,
                        error_message=(
                            f"Slippage détecté. "
                            f"Coût: ${current_cost:.4f} > "
                            f"Seuil: ${self._params.max_pair_cost_slippage_check:.4f}"
                        )
                    )

                # Callback de début
                if self.on_trade_start:
                    self.on_trade_start(opportunity)
                
                # Calculer la taille des ordres
                size = self._calculate_order_size(opportunity)
                
                # Placer les ordres
                result = await self._place_bilateral_orders(opportunity, size)
                
                # 6.2: Wait for fills to confirm execution
                if result.success:
                    await self._wait_for_fills(result)

                # Mettre à jour les stats
                self._trades_today += 1
                self._last_trade_time = datetime.now()
                
                if result.success:
                    self._successful_trades += 1
                    self._consecutive_failures = 0  # Reset en cas de succès
                    if self.on_trade_success:
                        self.on_trade_success(result)
                else:
                    self._failed_trades += 1
                    self._consecutive_failures += 1

                    # 6.1: Risk Management - Déclencher le circuit breaker
                    if self._consecutive_failures >= self._max_consecutive_failures:
                        print(f"CIRCUIT BREAKER: {self._consecutive_failures} échecs consécutifs. Pause de {self._pause_duration_seconds}s.")
                        self.pause()
                        asyncio.create_task(self._auto_resume())

                    if self.on_trade_failure:
                        self.on_trade_failure(result)
                
                return result
                
            finally:
                self._set_state(ExecutorState.READY)

    async def _wait_for_fills(self, result: TradeResult, timeout: float = 5.0) -> None:
        """
        6.2: Vérifie si les ordres sont remplis.
        
        Args:
            result: Resultat du trade contenant les IDs des ordres
            timeout: Temps max à attendre (secondes)
        """
        if not self._client or not result.success:
            return
            
        # IDs à vérifier
        order_ids = []
        if result.order_yes_id: order_ids.append(result.order_yes_id)
        if result.order_no_id: order_ids.append(result.order_no_id)
        
        if not order_ids:
            return

        start_time = datetime.now()
        
        while (datetime.now() - start_time).total_seconds() < timeout:
            all_filled = True
            
            for order_id in order_ids:
                try:
                    # Fetch order status from API (simulate via client if needed)
                    # Note: Private client needs get_order method
                    order_data = await self._client.get_order(order_id)
                    
                    if order_data:
                        status = order_data.get("status", "open")
                        filled = float(order_data.get("sizeMatched", 0.0))
                        
                        # Update OrderManager
                        self._order_manager.update_order_status(
                            order_id=order_id,
                            status=status,
                            filled_size=filled
                        )
                        
                        # Estimer fees: 2% taker/maker (simplifié si API ne donne pas fee)
                        # Polymarket fee model: 2% on net winnings? or spread?
                        # Ici on assume un coût de transaction pour le scoring
                        
                        if status != "FILLED" and status != "CANCELED":
                            all_filled = False
                            
                except Exception as e:
                    print(f"⚠️ Error checking fill for {order_id}: {e}")
                    all_filled = False
            
            if all_filled:
                break
                
            await asyncio.sleep(0.5)

    async def _auto_resume(self):
        """Reprend automatiquement après la durée de pause du circuit breaker."""
        await asyncio.sleep(self._pause_duration_seconds)
        print("CIRCUIT BREAKER: Reprise automatique du trading.")
        self._consecutive_failures = 0 # Reset du compteur avant reprise
        self.resume()
    
    def _calculate_order_size(self, opportunity: Opportunity) -> float:
        """
        Calcule la taille optimale des ordres.
        
        Basé sur:
        - Capital alloué par trade
        - Prix des tokens
        - Spread disponible
        """
        # 6.1: Risk Management - Allocation dynamique du capital
        base_capital = self._params.capital_per_trade
        score_multiplier = 1.0  # Par défaut

        if opportunity.score == 5: # 5 étoiles
            score_multiplier = self._params.capital_multiplier_score_5
        elif opportunity.score == 4: # 4 étoiles
            score_multiplier = self._params.capital_multiplier_score_4

        capital = base_capital * score_multiplier
        
        # Calculer le nombre de shares basé sur le prix moyen
        avg_price = (opportunity.recommended_price_yes + opportunity.recommended_price_no) / 2
        
        # On divise le capital entre YES et NO
        capital_per_side = capital / 2
        
        # Nombre de shares par côté
        shares = capital_per_side / avg_price
        
        return round(shares, 2)
    
    async def _place_bilateral_orders(
        self,
        opportunity: Opportunity,
        size: float
    ) -> TradeResult:
        """
        Place les ordres bilatéraux (YES + NO).
        
        Args:
            opportunity: Opportunité à trader
            size: Taille des ordres
            
        Returns:
            TradeResult
        """
        if not self._client:
            return TradeResult(
                opportunity_id=opportunity.id,
                success=False,
                error_message="Client non initialisé"
            )
        
        order_yes_id = None
        order_no_id = None

        try:
            # 5.1: Exécution PARALLÈLE des ordres YES et NO (50% latence gagnée)
            results = await asyncio.gather(
                self._client.place_order(
                    token_id=opportunity.token_yes_id,
                    side=OrderSide.BUY,
                    price=opportunity.recommended_price_yes,
                    size=size
                ),
                self._client.place_order(
                    token_id=opportunity.token_no_id,
                    side=OrderSide.BUY,
                    price=opportunity.recommended_price_no,
                    size=size
                ),
                return_exceptions=True  # Capturer les erreurs individuellement
            )

            order_yes, order_no = results

            # Traiter résultat YES
            if isinstance(order_yes, Exception):
                raise order_yes  # Propager l'erreur
            order_yes_id = order_yes.get("id")

            # Traiter résultat NO
            if isinstance(order_no, Exception):
                raise order_no  # Propager l'erreur
            order_no_id = order_no.get("id")
            
            # Enregistrer dans l'order manager
            if order_yes_id:
                self._order_manager.add_order(ActiveOrder(
                    id=order_yes_id,
                    opportunity_id=opportunity.id,
                    market_id=opportunity.market_id,
                    token_id=opportunity.token_yes_id,
                    side="YES",
                    price=opportunity.recommended_price_yes,
                    size=size,
                    status="open"
                ))
            
            if order_no_id:
                self._order_manager.add_order(ActiveOrder(
                    id=order_no_id,
                    opportunity_id=opportunity.id,
                    market_id=opportunity.market_id,
                    token_id=opportunity.token_no_id,
                    side="NO",
                    price=opportunity.recommended_price_no,
                    size=size,
                    status="open"
                ))
            
            return TradeResult(
                opportunity_id=opportunity.id,
                success=True,
                order_yes_id=order_yes_id,
                order_no_id=order_no_id
            )
            
        except Exception as e:
            # 6.1: Risk Management - Logique améliorée pour ordres partiels
            # Si un seul des deux ordres a été placé, on tente de l'annuler
            # pour éviter une position directionnelle non désirée.
            if order_yes_id and not order_no_id:
                try:
                    await self._client.cancel_order(order_yes_id)
                    print(f"⚠️ [RISK] Ordre NO échoué. Annulation de l'ordre YES {order_yes_id} réussie.")
                except Exception as cancel_e:
                    print(f"🚨 [RISK] CRITIQUE: Ordre NO échoué ET annulation de l'ordre YES {order_yes_id} échouée. Position ouverte non couverte! Erreur: {cancel_e}")
            elif order_no_id and not order_yes_id:
                 # Ce cas est moins probable avec asyncio.gather mais possible
                try:
                    await self._client.cancel_order(order_no_id)
                    print(f"⚠️ [RISK] Ordre YES échoué. Annulation de l'ordre NO {order_no_id} réussie.")
                except Exception: # Pas besoin de log critique ici, car le premier ordre a déjà échoué
                    pass
            
            return TradeResult(
                opportunity_id=opportunity.id,
                success=False,
                order_yes_id=order_yes_id,
                order_no_id=order_no_id,
                error_message=str(e)
            )
    
    async def cancel_all_orders(self) -> int:
        """
        Annule tous les ordres actifs.

        Returns:
            Nombre d'ordres annulés
        """
        if not self._client:
            return 0

        try:
            count = await self._client.cancel_all_orders()
            self._order_manager.clear()
            return count
        except Exception:
            return 0

    # ═══════════════════════════════════════════════════════════════════
    # 4.1: Méthodes pour Order Queue (exécution non-bloquante)
    # ═══════════════════════════════════════════════════════════════════

    async def queue_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        priority: OrderPriority = OrderPriority.NORMAL,
        order_type: str = "GTC",
        market_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Ajoute un ordre à la queue (non-bloquant).

        Args:
            token_id: ID du token
            side: "BUY" ou "SELL"
            price: Prix de l'ordre
            size: Taille en shares
            priority: Priorité (NORMAL, HIGH, URGENT)
            order_type: Type d'ordre (GTC, FOK)
            market_id: ID du marché (optionnel, pour tracking)

        Returns:
            ID de l'ordre dans la queue, ou None si queue non disponible
        """
        if not self._order_queue:
            return None

        order = QueuedOrder(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            priority=priority,
            order_type=order_type,
            market_id=market_id,
            metadata=metadata or {}
        )

        return await self._order_queue.enqueue(order)

    async def queue_bilateral_orders(
        self,
        opportunity: Opportunity,
        priority: OrderPriority = OrderPriority.NORMAL
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Ajoute une paire d'ordres YES/NO à la queue.

        Args:
            opportunity: L'opportunité
            priority: Priorité des ordres

        Returns:
            Tuple (order_yes_id, order_no_id)
        """
        if not self._order_queue:
            return None, None

        size = self._calculate_order_size(opportunity)

        # Enqueue les deux ordres EN PARALLÈLE (optimisation HFT)
        order_yes_id, order_no_id = await asyncio.gather(
            self.queue_order(
                token_id=opportunity.token_yes_id,
                side="BUY",
                price=opportunity.recommended_price_yes,
                size=size,
                priority=priority,
                market_id=opportunity.market_id
            ),
            self.queue_order(
                token_id=opportunity.token_no_id,
                side="BUY",
                price=opportunity.recommended_price_no,
                size=size,
                priority=priority,
                market_id=opportunity.market_id
            )
        )

        return order_yes_id, order_no_id

    def get_queue_order_status(self, order_id: str) -> Optional[QueueOrderStatus]:
        """Récupère le statut d'un ordre dans la queue."""
        if not self._order_queue:
            return None
        return self._order_queue.get_order_status(order_id)

    def _on_queue_order_complete(self, order: QueuedOrder) -> None:
        """Callback quand un ordre de la queue est complété."""
        self._successful_trades += 1
        self._last_trade_time = datetime.now()

        # Enregistrer dans l'order manager si on a le résultat
        if order.result and order.result.get("orderID"):
            order_id = order.result["orderID"]
            
            self._order_manager.add_order(ActiveOrder(
                id=order_id,
                opportunity_id=order.id,
                market_id=order.market_id or "",
                token_id=order.token_id,
                side=order.side,
                price=order.price,
                size=order.size,
                status="open"
            ))

            # 7.0: Track order for fills
            if self.fill_manager:
                # Tenter de récupérer le side (YES/NO) des métadonnées
                side = order.metadata.get("side", "UNKNOWN")
                
                self.fill_manager.track_order(
                    order_id=order_id,
                    market_id=order.market_id or "",
                    side=side,
                    qty=order.size
                )

    async def _on_fill_callback(self, market_id: str, side: str, filled_qty: float, price: float):
        """Callback appelé par FillManager quand un fill est détecté."""
        if self.on_fill:
            if asyncio.iscoroutinefunction(self.on_fill):
                await self.on_fill(market_id, side, filled_qty, price)
            else:
                self.on_fill(market_id, side, filled_qty, price)

    async def _on_order_end_callback(self, market_id: str, side: str, remaining_qty: float):
        """Callback appelé par FillManager quand un ordre se termine (cancel/expired)."""
        if self.on_order_end:
            if asyncio.iscoroutinefunction(self.on_order_end):
                await self.on_order_end(market_id, side, remaining_qty)
            else:
                self.on_order_end(market_id, side, remaining_qty)

    def _handle_failure(self, error_msg: str):
        """Gestion centralisée des échecs pour le Circuit Breaker."""
        self._failed_trades += 1
        self._consecutive_failures += 1

        if self._consecutive_failures >= self._max_consecutive_failures:
            print(f"🚨 CIRCUIT BREAKER: {self._consecutive_failures} échecs consécutifs. Pause de {self._pause_duration_seconds}s.")
            self.pause()
            asyncio.create_task(self._auto_resume())
    
    def _on_queue_order_failed(self, order: QueuedOrder) -> None:
        """Callback quand un ordre de la queue échoue."""
        print(f"❌ [Executor] Ordre échoué: {order.id} - {order.error}")
        self._handle_failure(str(order.error))

    async def _connection_warmup_loop(self) -> None:
        """
        HFT: Maintient les connexions chaudes en envoyant des requêtes légères.

        Évite les cold starts après inactivité qui ajouteraient ~50-150ms
        de latence sur le premier ordre.
        """
        while True:
            try:
                await asyncio.sleep(self._warmup_interval)

                if self._client and self._state in (ExecutorState.READY, ExecutorState.PAUSED):
                    await self._client.warm_connections()

            except asyncio.CancelledError:
                break
            except Exception:
                pass  # Ignorer les erreurs de warmup silencieusement
