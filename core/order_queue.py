"""
Order Queue - File d'attente asynchrone pour ordres non-bloquants

Optimisation 4.1: Traitement parallèle des ordres sans bloquer le thread principal.

Features:
- Queue prioritaire (urgent > high > normal)
- Traitement parallèle configurable (max 3 par défaut)
- Callbacks sur completion/failure
- Retry automatique avec backoff
- Tracking du statut des ordres
"""

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any, List, Deque
from datetime import datetime
from enum import Enum


class QueueOrderStatus(Enum):
    """Statut d'un ordre dans la queue (distinct de OrderStatus de order_manager)."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OrderPriority(Enum):
    """Priorité d'un ordre."""
    NORMAL = 0
    HIGH = 1
    URGENT = 2


@dataclass(slots=True)
class QueuedOrder:
    """Ordre en attente dans la queue (slots=True pour performance HFT)."""
    token_id: str
    side: str  # BUY/SELL
    price: float
    size: float
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    order_type: str = "GTC"  # GTC, FOK
    priority: OrderPriority = OrderPriority.NORMAL
    created_at: datetime = field(default_factory=datetime.now)
    status: QueueOrderStatus = QueueOrderStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retries: int = 0
    market_id: Optional[str] = None  # Pour tracking
    metadata: Dict[str, Any] = field(default_factory=dict) # Pour données additionnelles (ex: side=YES)

    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire."""
        return {
            "id": self.id,
            "token_id": self.token_id,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "order_type": self.order_type,
            "priority": self.priority.name,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "error": self.error,
            "retries": self.retries,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class QueueStats:
    """Statistiques de la queue (slots=True pour performance HFT)."""
    total_enqueued: int = 0
    total_completed: int = 0
    total_failed: int = 0
    total_retried: int = 0
    avg_processing_time_ms: float = 0.0
    current_queue_size: int = 0
    current_processing: int = 0


class OrderQueue:
    """
    File d'attente asynchrone pour ordres non-bloquants.

    Usage:
        queue = OrderQueue(private_client)
        await queue.start()

        # Enqueue un ordre (non-bloquant)
        order_id = await queue.enqueue(QueuedOrder(
            token_id="0x...",
            side="BUY",
            price=0.55,
            size=100
        ))

        # Vérifier le statut
        status = queue.get_order_status(order_id)

        # Arrêter la queue
        await queue.stop()
    """

    def __init__(
        self,
        private_client,
        max_concurrent: int = 3,
        max_retries: int = 2,
        retry_delay: float = 0.05  # HFT: réduit de 0.5s à 50ms
    ):
        """
        Initialise la queue d'ordres.

        Args:
            private_client: Client API privé Polymarket
            max_concurrent: Nombre max d'ordres en parallèle
            max_retries: Nombre max de retries par ordre
            retry_delay: Délai entre retries (secondes)
        """
        self._client = private_client
        self._max_concurrent = max_concurrent
        self._max_retries = max_retries
        self._retry_delay = retry_delay

        # Queues par priorité
        self._urgent_queue: asyncio.Queue = asyncio.Queue()
        self._high_queue: asyncio.Queue = asyncio.Queue()
        self._normal_queue: asyncio.Queue = asyncio.Queue()

        # État
        self._orders: Dict[str, QueuedOrder] = {}
        self._processing_count = 0
        self._running = False
        self._processor_task: Optional[asyncio.Task] = None

        # Semaphore pour limiter concurrence
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # HFT: Event pour notification immédiate au lieu de polling
        self._order_event = asyncio.Event()

        # Stats
        self._stats = QueueStats()
        # HFT: deque au lieu de list pour O(1) pop
        self._processing_times: Deque[float] = deque(maxlen=100)

        # Déduplication: tracking des ordres récents par clé unique
        self._recent_order_keys: Deque[str] = deque(maxlen=200)

        # Callbacks
        self.on_order_complete: Optional[Callable[[QueuedOrder], None]] = None
        self.on_order_failed: Optional[Callable[[QueuedOrder], None]] = None
        self.on_order_retry: Optional[Callable[[QueuedOrder, int], None]] = None

    @property
    def is_running(self) -> bool:
        """Vérifie si la queue est active."""
        return self._running

    @property
    def stats(self) -> QueueStats:
        """Retourne les statistiques."""
        self._stats.current_queue_size = self.queue_size
        self._stats.current_processing = self._processing_count
        if self._processing_times:
            self._stats.avg_processing_time_ms = sum(self._processing_times) / len(self._processing_times)
        return self._stats

    @property
    def queue_size(self) -> int:
        """Nombre total d'ordres en attente."""
        return (
            self._urgent_queue.qsize() +
            self._high_queue.qsize() +
            self._normal_queue.qsize()
        )

    @property
    def processing_count(self) -> int:
        """Nombre d'ordres en cours de traitement."""
        return self._processing_count

    async def start(self) -> None:
        """Démarre le processeur de queue."""
        if self._running:
            return

        self._running = True
        self._processor_task = asyncio.create_task(self._process_loop())
        print("📬 [OrderQueue] Démarré (max concurrent: {})".format(self._max_concurrent))

    async def stop(self) -> None:
        """Arrête le processeur proprement."""
        self._running = False

        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass

        print("📭 [OrderQueue] Arrêté")

    def _make_order_key(self, order: QueuedOrder) -> str:
        """Crée une clé unique pour déduplication."""
        return f"{order.token_id}:{order.side}:{order.price}:{order.size}"

    async def enqueue(self, order: QueuedOrder) -> str:
        """
        Ajoute un ordre à la queue (non-bloquant).

        Args:
            order: L'ordre à ajouter

        Returns:
            ID de l'ordre (ou ID existant si doublon)
        """
        # HFT: Déduplication - éviter les ordres identiques
        order_key = self._make_order_key(order)
        if order_key in self._recent_order_keys:
            # Ordre identique récent trouvé, skip
            return order.id

        # Ajouter à la liste des clés récentes
        self._recent_order_keys.append(order_key)

        self._orders[order.id] = order
        self._stats.total_enqueued += 1

        # Choisir la queue selon priorité
        if order.priority == OrderPriority.URGENT:
            await self._urgent_queue.put(order.id)
        elif order.priority == OrderPriority.HIGH:
            await self._high_queue.put(order.id)
        else:
            await self._normal_queue.put(order.id)

        # HFT: Signaler qu'un ordre est disponible (réveille _process_loop)
        self._order_event.set()

        return order.id

    async def enqueue_batch(self, orders: List[QueuedOrder]) -> List[str]:
        """
        Ajoute plusieurs ordres à la queue.

        Args:
            orders: Liste d'ordres

        Returns:
            Liste des IDs
        """
        ids = []
        for order in orders:
            order_id = await self.enqueue(order)
            ids.append(order_id)
        return ids

    def get_order(self, order_id: str) -> Optional[QueuedOrder]:
        """Récupère un ordre par ID."""
        return self._orders.get(order_id)

    def get_order_status(self, order_id: str) -> Optional[QueueOrderStatus]:
        """Récupère le statut d'un ordre."""
        order = self._orders.get(order_id)
        return order.status if order else None

    def get_pending_orders(self) -> List[QueuedOrder]:
        """Récupère les ordres en attente."""
        return [o for o in self._orders.values() if o.status == QueueOrderStatus.PENDING]

    def get_completed_orders(self) -> List[QueuedOrder]:
        """Récupère les ordres terminés."""
        return [o for o in self._orders.values() if o.status == QueueOrderStatus.COMPLETED]

    def get_failed_orders(self) -> List[QueuedOrder]:
        """Récupère les ordres échoués."""
        return [o for o in self._orders.values() if o.status == QueueOrderStatus.FAILED]

    async def cancel_order(self, order_id: str) -> bool:
        """
        Annule un ordre en attente.

        Args:
            order_id: ID de l'ordre

        Returns:
            True si annulé
        """
        order = self._orders.get(order_id)
        if order and order.status == QueueOrderStatus.PENDING:
            order.status = QueueOrderStatus.CANCELLED
            return True
        return False

    def clear_completed(self) -> int:
        """
        Nettoie les ordres terminés de la mémoire.

        Returns:
            Nombre d'ordres nettoyés
        """
        to_remove = [
            oid for oid, o in self._orders.items()
            if o.status in (QueueOrderStatus.COMPLETED, QueueOrderStatus.FAILED, QueueOrderStatus.CANCELLED)
        ]
        for oid in to_remove:
            del self._orders[oid]
        return len(to_remove)

    async def _process_loop(self) -> None:
        """Boucle principale de traitement (HFT: event-based, pas de polling)."""
        while self._running:
            try:
                # Récupérer le prochain ordre par priorité
                order_id = await self._get_next_order()

                if order_id:
                    # Lancer le traitement en parallèle (limité par semaphore)
                    asyncio.create_task(self._process_order(order_id))
                else:
                    # HFT: Attendre un signal au lieu de polling (latence 0 vs 10ms)
                    self._order_event.clear()
                    try:
                        await asyncio.wait_for(self._order_event.wait(), timeout=0.1)
                    except asyncio.TimeoutError:
                        pass  # Timeout OK, juste re-vérifier

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [OrderQueue] Erreur boucle: {e}")
                await asyncio.sleep(0.05)  # Réduit de 100ms à 50ms

    async def _get_next_order(self) -> Optional[str]:
        """Récupère le prochain ordre par priorité."""
        # Vérifier dans l'ordre: urgent > high > normal
        for queue in [self._urgent_queue, self._high_queue, self._normal_queue]:
            try:
                return queue.get_nowait()
            except asyncio.QueueEmpty:
                continue
        return None

    async def _process_order(self, order_id: str) -> None:
        """Traite un ordre avec gestion de concurrence."""
        async with self._semaphore:
            order = self._orders.get(order_id)

            # Vérifier que l'ordre est toujours valide
            if not order or order.status != QueueOrderStatus.PENDING:
                return

            order.status = QueueOrderStatus.PROCESSING
            self._processing_count += 1
            start_time = asyncio.get_event_loop().time()

            try:
                # Exécuter l'ordre via le client API
                result = await self._execute_order(order)

                # Vérifier le résultat
                if result.get("error"):
                    raise Exception(result["error"])

                # Succès
                order.status = QueueOrderStatus.COMPLETED
                order.result = result
                self._stats.total_completed += 1

                # Callback
                if self.on_order_complete:
                    try:
                        self.on_order_complete(order)
                    except Exception:
                        pass

            except Exception as e:
                # Gérer l'échec avec retry potentiel
                await self._handle_failure(order, e)

            finally:
                self._processing_count -= 1

                # Enregistrer le temps de traitement (deque maxlen=100 gère auto)
                elapsed = (asyncio.get_event_loop().time() - start_time) * 1000
                self._processing_times.append(elapsed)

    async def _execute_order(self, order: QueuedOrder) -> Dict[str, Any]:
        """Exécute l'ordre via le client API."""
        if not self._client:
            return {"error": "Client non initialisé"}

        if order.order_type == "MARKET":
            return await self._client.create_market_order(
                token_id=order.token_id,
                side=order.side,
                amount=order.size # NOTE: Check if size is shares or amount (USDC)
                # Polymarket create_market_order arg 'amount':
                # BUY: Amount in USDC
                # SELL: Amount in Shares
            )

        return await self._client.create_limit_order(
            token_id=order.token_id,
            side=order.side,
            price=order.price,
            size=order.size,
            time_in_force=order.order_type
        )

    async def _handle_failure(self, order: QueuedOrder, error: Exception) -> None:
        """Gère un échec avec retry potentiel."""
        order.error = str(error)

        # Vérifier si on peut retry
        if order.retries < self._max_retries:
            order.retries += 1
            order.status = QueueOrderStatus.PENDING
            self._stats.total_retried += 1

            # Callback retry
            if self.on_order_retry:
                try:
                    self.on_order_retry(order, order.retries)
                except Exception:
                    pass

            # Attendre avant retry
            await asyncio.sleep(self._retry_delay * order.retries)

            # Re-enqueue
            await self.enqueue(order)

        else:
            # Max retries atteint
            order.status = QueueOrderStatus.FAILED
            self._stats.total_failed += 1

            # Callback failure
            if self.on_order_failed:
                try:
                    self.on_order_failed(order)
                except Exception:
                    pass


class OrderQueueManager:
    """
    Gestionnaire de queue d'ordres pour intégration facile.

    Fournit une interface simplifiée pour utiliser OrderQueue.
    """

    _instance: Optional['OrderQueueManager'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._queue: Optional[OrderQueue] = None
        self._initialized = True

    def initialize(self, private_client, **kwargs) -> None:
        """Initialise le manager avec un client."""
        self._queue = OrderQueue(private_client, **kwargs)

    @property
    def queue(self) -> Optional[OrderQueue]:
        """Accès à la queue."""
        return self._queue

    async def start(self) -> None:
        """Démarre la queue."""
        if self._queue:
            await self._queue.start()

    async def stop(self) -> None:
        """Arrête la queue."""
        if self._queue:
            await self._queue.stop()

    async def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        priority: OrderPriority = OrderPriority.NORMAL,
        order_type: str = "GTC",
        market_id: Optional[str] = None
    ) -> str:
        """
        Place un ordre dans la queue.

        Returns:
            ID de l'ordre
        """
        if not self._queue:
            raise RuntimeError("Queue non initialisée")

        order = QueuedOrder(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            priority=priority,
            order_type=order_type,
            market_id=market_id
        )

        return await self._queue.enqueue(order)


# Singleton global
order_queue_manager = OrderQueueManager()
