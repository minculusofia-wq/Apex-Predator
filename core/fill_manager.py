"""
Fill Manager - Gestionnaire d'exécutions et de réconciliation.

Responsabilités:
1. Suivre le statut des ordres vivants (Polling API)
2. Détecter les fills partiels ou complets
3. Notifier le moteur de stratégie (Gabagool) des mises à jour réelles
4. Gérer la réconciliation (nettoyage des ordres partiels bloqués)
"""

import asyncio
from typing import Dict, Optional, Callable, List
from dataclasses import dataclass, field
from datetime import datetime

from api.private import PolymarketPrivateClient

@dataclass(slots=True)
class TrackedOrder:
    """Ordre suivi pour réconciliation (slots=True pour performance HFT)."""
    order_id: str          # Exchange Order ID
    market_id: str         # Gabagool Market ID
    side: str              # "YES" or "NO"
    initial_qty: float
    filled_qty: float = 0.0
    status: str = "open"   # "open", "matched", "canceled"
    created_at: datetime = field(default_factory=datetime.now)
    last_check: datetime = field(default_factory=datetime.now)

class FillManager:
    def __init__(self, client: PolymarketPrivateClient, poll_interval: float = 2.0):
        self.client = client
        self.poll_interval = poll_interval
        self._tracked_orders: Dict[str, TrackedOrder] = {}
        self._is_running = False
        
        # Callbacks
        self.on_fill: Optional[Callable[[str, str, float, float], None]] = None # (market_id, side, filled_qty, price)
        self.on_order_end: Optional[Callable[[str, str, float], None]] = None # (market_id, side, remaining_qty)

    async def start(self):
        self._is_running = True
        asyncio.create_task(self._poll_loop())
        print("🕵️ Fill Manager démarré")

    async def stop(self):
        self._is_running = False

    def track_order(self, order_id: str, market_id: str, side: str, qty: float):
        """Commence le suivi d'un ordre."""
        self._tracked_orders[order_id] = TrackedOrder(
            order_id=order_id,
            market_id=market_id,
            side=side,
            initial_qty=qty
        )
        # print(f"👀 Tracking order {order_id} ({side} x {qty})")

    async def _poll_loop(self):
        """Boucle de vérification des statuts."""
        while self._is_running:
            if not self._tracked_orders:
                await asyncio.sleep(self.poll_interval)
                continue

            # Copie pour itération
            orders_to_check = list(self._tracked_orders.values())
            
            for order in orders_to_check:
                try:
                    # Appel API REST
                    order_data = await self.client.get_order(order.order_id)
                    
                    if not order_data:
                        continue

                    new_filled = float(order_data.get("sizeMatched", 0.0))
                    status = order_data.get("status", "open") # "open", "matched", "canceled"
                    avg_price = float(order_data.get("avgPrice", 0.0))

                    # Détection de nouveau fill (Delta)
                    delta_fill = new_filled - order.filled_qty
                    
                    if delta_fill > 0:
                        order.filled_qty = new_filled
                        # Trigger Callback Fill
                        if self.on_fill:
                            if asyncio.iscoroutinefunction(self.on_fill):
                                await self.on_fill(order.market_id, order.side, delta_fill, avg_price)
                            else:
                                self.on_fill(order.market_id, order.side, delta_fill, avg_price)
                        
                        print(f"💰 Fill détecté: {order.side} +{delta_fill} @ {avg_price} (Total: {new_filled}/{order.initial_qty})")

                    # Si ordre terminé ou annulé
                    if status in ["matched", "canceled", "expired"] or new_filled >= order.initial_qty:
                        # Calculer reste à annuler dans pending
                        remaining = max(0.0, order.initial_qty - new_filled)

                        if self.on_order_end:
                            if asyncio.iscoroutinefunction(self.on_order_end):
                                await self.on_order_end(order.market_id, order.side, remaining)
                            else:
                                self.on_order_end(order.market_id, order.side, remaining)

                        if order.order_id in self._tracked_orders:
                            del self._tracked_orders[order.order_id]

                except Exception as e:
                    print(f"⚠️ Fill poll error for {order.order_id}: {e}")

            await asyncio.sleep(self.poll_interval)
