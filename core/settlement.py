"""
Settlement Manager - Gestion des résolutions et réclamations

Fonctionnalités:
1. Surveille les positions ouvertes pour détecter les résolutions.
2. Interagit avec l'API pour "redeem" les gains.
3. Met à jour l'état des positions dans l'OrderManager.

Note: La redemption nécessite souvent une interaction smart contract (CTF Exchange).
Si l'API CLOB ne le supporte pas directement, ce module notifie l'utilisateur.
"""

import asyncio
from typing import Optional, List
from datetime import datetime

from core.order_manager import OrderManager, Position
from api.private import PolymarketPrivateClient
from api.public import PolymarketPublicClient

class SettlementManager:
    """
    Gère le cycle de vie post-trade: Résolution & Redemption.
    """
    
    def __init__(
        self, 
        order_manager: OrderManager,
        private_client: Optional[PolymarketPrivateClient],
        public_client: Optional[PolymarketPublicClient]
    ):
        self._order_manager = order_manager
        self._private_client = private_client
        self._public_client = public_client
        self._is_running = False
        self._check_interval = 300  # Vérifier toutes les 5 minutes

    async def start(self) -> None:
        """Démarre la boucle de surveillance des settlements."""
        if self._is_running:
            return
            
        self._is_running = True
        print("🏛️ [Settlement] Manager démarré")
        asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        self._is_running = False

    async def _monitor_loop(self) -> None:
        """Boucle de monitoring."""
        while self._is_running:
            try:
                await self.check_resolutions()
            except Exception as e:
                print(f"⚠️ [Settlement] Erreur monitoring: {e}")
            
            await asyncio.sleep(self._check_interval)

    async def check_resolutions(self) -> None:
        """Vérifie si des positions sont sur des marchés résolus."""
        positions = self._order_manager.get_all_positions()
        if not positions:
            return

        # Filtrer positions non résolues
        active_positions = [p for p in positions if not p.is_resolved]
        
        for position in active_positions:
            try:
                # Vérifier statut marché via API publique
                if not self._public_client:
                    continue
                    
                market = await self._public_client.get_market(position.market_id)
                if not market:
                    continue
                
                # Vérifier si résolu
                # Note: API response structure for resolution varies. 
                # Checking 'closed' or 'resolved' status.
                is_closed = market.get("closed", False) or market.get("resolved", False)
                
                if is_closed:
                    print(f"⚖️ [Settlement] Marché {position.question} RÉSOLU!")
                    # Tenter de redeem
                    await self._redeem_winnings(position)
                    
            except Exception as e:
                print(f"⚠️ [Settlement] Erreur check position {position.market_id}: {e}")

    async def _redeem_winnings(self, position: Position) -> None:
        """
        Tente de récupérer les gains.
        
        Note: C'est ici que l'appel API critique se fait.
        Si 'redeem' n'est pas dispo dans py-clob-client, on log alert.
        """
        if not self._private_client:
            print(f"🔔 [Settlement] Veuillez réclamer manuellement pour: {position.question}")
            return

        print(f"💸 [Settlement] Tentative de redemption pour {position.market_id}...")
        
        try:
            # Hypothetical method - adapt based on actual library capabilities
            # py-clob-client might expose specific redemption methods or we interact with contract
            # For now, simplistic approach:
            if hasattr(self._private_client, "redeem_all"):
                 result = await self._private_client.redeem_all(position.market_id)
                 print(f"✅ [Settlement] Redemption Succès: {result}")
                 
                 # Marquer comme résolu/fermé dans l'order manager
                 # Calculer PnL final (1.00 ou 0.00 selon issue)
                 # Note: Cela demande de savoir qui a gagné.
                 position.is_resolved = True
                 # self._order_manager.close_position(...) # Need updated logic for settlement close
            else:
                 print(f"ℹ️ [Settlement] API 'redeem' non détectée. Veuillez redeem sur l'interface web.")
                 
        except Exception as e:
            print(f"❌ [Settlement] Échec redemption: {e}")
