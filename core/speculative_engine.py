"""
Speculative Engine - Pré-calcul des ordres pour les meilleures opportunités

Optimisation HFT: Pré-signer les ordres pour les top N opportunités
avant qu'elles ne soient exécutées, réduisant la latence de ~5-8ms.

Usage:
    engine = SpeculativeEngine(client, top_n=3)
    await engine.start()

    # Après analyse des opportunités
    await engine.update_top_opportunities(opportunities)

    # Au moment de trader
    presigned = await engine.get_presigned(opportunity.id)
    if presigned:
        result = await client.submit_presigned(presigned)
"""

import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from api.private import PolymarketPrivateClient, PreSignedOrder
from core.analyzer import Opportunity


@dataclass(slots=True)
class SpeculativeOrder:
    """Paire d'ordres pré-signés pour une opportunité (YES + NO)."""
    opportunity_id: str
    market_id: str
    presigned_yes: Optional[PreSignedOrder] = None
    presigned_no: Optional[PreSignedOrder] = None
    created_at: datetime = field(default_factory=datetime.now)

    def is_complete(self) -> bool:
        """Vérifie si les deux ordres sont pré-signés."""
        return self.presigned_yes is not None and self.presigned_no is not None

    def is_expired(self) -> bool:
        """Vérifie si un des ordres a expiré."""
        if self.presigned_yes and self.presigned_yes.is_expired():
            return True
        if self.presigned_no and self.presigned_no.is_expired():
            return True
        return False


class SpeculativeEngine:
    """
    Moteur de pré-calcul spéculatif des ordres.

    Maintient un cache des ordres pré-signés pour les meilleures
    opportunités, permettant une exécution ultra-rapide.

    Fonctionnement:
    1. L'analyzer détecte les opportunités et les score
    2. SpeculativeEngine pré-signe les top N opportunités
    3. Quand on décide de trader, l'ordre est déjà signé
    4. On n'a plus qu'à l'envoyer (~2-3ms au lieu de ~8-10ms)
    """

    def __init__(
        self,
        client: PolymarketPrivateClient,
        top_n: int = 3,
        ttl_seconds: float = 30.0,
        cleanup_interval: float = 10.0
    ):
        """
        Initialise le moteur spéculatif.

        Args:
            client: Client Polymarket pour pré-signer
            top_n: Nombre d'opportunités à pré-signer
            ttl_seconds: Durée de vie des ordres pré-signés
            cleanup_interval: Intervalle de nettoyage des expirés
        """
        self._client = client
        self._top_n = top_n
        self._ttl = ttl_seconds
        self._cleanup_interval = cleanup_interval

        self._speculative: Dict[str, SpeculativeOrder] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._cleanup_task: Optional[asyncio.Task] = None

        # Stats
        self._presigns_created = 0
        self._presigns_used = 0
        self._presigns_expired = 0

    @property
    def stats(self) -> dict:
        """Retourne les statistiques du moteur."""
        return {
            "cached_opportunities": len(self._speculative),
            "presigns_created": self._presigns_created,
            "presigns_used": self._presigns_used,
            "presigns_expired": self._presigns_expired,
            "hit_rate": (
                self._presigns_used / max(1, self._presigns_created) * 100
            )
        }

    async def start(self) -> None:
        """Démarre le moteur spéculatif."""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        print(f"🔮 [Speculative] Démarré (top_n={self._top_n}, ttl={self._ttl}s)")

    async def stop(self) -> None:
        """Arrête le moteur."""
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            self._speculative.clear()

        print("🔮 [Speculative] Arrêté")

    async def update_top_opportunities(self, opportunities: List[Opportunity]) -> int:
        """
        Met à jour les ordres pré-signés pour les meilleures opportunités.

        Args:
            opportunities: Liste d'opportunités triées par score décroissant

        Returns:
            Nombre d'ordres pré-signés créés
        """
        # Filtrer les meilleures opportunités (score >= 4)
        top_opps = [
            opp for opp in opportunities[:self._top_n * 2]
            if opp.score >= 4
        ][:self._top_n]

        created = 0
        async with self._lock:
            # Identifier les nouvelles opportunités à pré-signer
            current_ids = set(self._speculative.keys())
            new_ids = {opp.id for opp in top_opps}

            # Supprimer les anciennes qui ne sont plus dans le top
            for old_id in current_ids - new_ids:
                del self._speculative[old_id]

            # Pré-signer les nouvelles
            for opp in top_opps:
                if opp.id not in self._speculative or self._speculative[opp.id].is_expired():
                    spec_order = await self._presign_opportunity(opp)
                    if spec_order and spec_order.is_complete():
                        self._speculative[opp.id] = spec_order
                        created += 1

        if created > 0:
            print(f"🔮 [Speculative] {created} opportunités pré-signées")

        return created

    async def _presign_opportunity(self, opp: Opportunity) -> Optional[SpeculativeOrder]:
        """Pré-signe une paire d'ordres YES/NO pour une opportunité."""
        try:
            # Calculer la taille (simplifié - utiliser la même logique que l'executor)
            # Pour une vraie implémentation, injecter le calculateur de taille
            size = 50.0  # Taille par défaut, à ajuster

            # Pré-signer en parallèle
            presigned_yes, presigned_no = await asyncio.gather(
                self._client.presign_order(
                    token_id=opp.token_yes_id,
                    side="BUY",
                    price=opp.recommended_price_yes,
                    size=size,
                    ttl_seconds=self._ttl
                ),
                self._client.presign_order(
                    token_id=opp.token_no_id,
                    side="BUY",
                    price=opp.recommended_price_no,
                    size=size,
                    ttl_seconds=self._ttl
                )
            )

            self._presigns_created += 2

            return SpeculativeOrder(
                opportunity_id=opp.id,
                market_id=opp.market_id,
                presigned_yes=presigned_yes,
                presigned_no=presigned_no
            )

        except Exception as e:
            print(f"⚠️ [Speculative] Erreur presign {opp.id}: {e}")
            return None

    async def get_presigned(self, opportunity_id: str) -> Optional[SpeculativeOrder]:
        """
        Récupère les ordres pré-signés pour une opportunité.

        Args:
            opportunity_id: ID de l'opportunité

        Returns:
            SpeculativeOrder si disponible et non expiré, sinon None
        """
        async with self._lock:
            spec = self._speculative.get(opportunity_id)

            if spec and not spec.is_expired():
                self._presigns_used += 2
                # Retirer du cache car il va être utilisé
                del self._speculative[opportunity_id]
                return spec

            return None

    async def has_presigned(self, opportunity_id: str) -> bool:
        """Vérifie si une opportunité a des ordres pré-signés valides."""
        async with self._lock:
            spec = self._speculative.get(opportunity_id)
            return spec is not None and not spec.is_expired()

    async def _cleanup_loop(self) -> None:
        """Boucle de nettoyage des ordres expirés."""
        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ [Speculative] Erreur cleanup: {e}")

    async def _cleanup_expired(self) -> int:
        """Nettoie les ordres pré-signés expirés."""
        removed = 0
        async with self._lock:
            expired_ids = [
                oid for oid, spec in self._speculative.items()
                if spec.is_expired()
            ]

            for oid in expired_ids:
                del self._speculative[oid]
                removed += 1
                self._presigns_expired += 2

        if removed > 0:
            print(f"🔮 [Speculative] {removed} expirés nettoyés")

        return removed
