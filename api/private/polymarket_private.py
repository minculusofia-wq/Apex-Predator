"""
Polymarket Private API Client (Order Placement)

Utilise py-clob-client officiel de Polymarket.
Documentation: https://github.com/Polymarket/py-clob-client

Modes supportés:
1. Direct EOA (MetaMask, hardware wallet) - signature_type=0
2. Email/Magic wallet proxy - signature_type=1
3. Browser wallet proxy - signature_type=2
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

# Import py-clob-client
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, MarketOrderArgs, ApiCreds
    from py_clob_client.order_builder.constants import BUY, SELL
    _HAS_CLOB_CLIENT = True
except ImportError:
    _HAS_CLOB_CLIENT = False
    print("⚠️ py-clob-client non installé. pip install py-clob-client")


class SignatureType(Enum):
    """Types de signature supportés par Polymarket."""
    EOA = 0           # Direct wallet (MetaMask, Ledger)
    MAGIC = 1         # Email/Magic wallet
    BROWSER_PROXY = 2 # Browser wallet proxy


class OrderSide(Enum):
    """Côté de l'ordre."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass(slots=True)
class PreSignedOrder:
    """
    Ordre pré-signé prêt pour envoi ultra-rapide.

    Le pre-signing sépare la signature crypto (lente ~5-8ms)
    de l'envoi HTTP, permettant de préparer l'ordre à l'avance.

    TTL recommandé: 30 secondes (nonce expiry).
    """
    signed_order: Any      # Ordre signé par py-clob-client
    token_id: str
    side: str
    price: float
    size: float
    order_type: str        # "GTC" ou "FOK"
    created_at: float      # timestamp
    expires_at: float      # timestamp (créé + 30s)

    def is_expired(self) -> bool:
        """Vérifie si l'ordre pré-signé a expiré."""
        return time.time() > self.expires_at

    def time_remaining(self) -> float:
        """Temps restant avant expiration (en secondes)."""
        return max(0, self.expires_at - time.time())


class PolymarketPrivateClient:
    """
    Client privé Polymarket pour l'exécution d'ordres.

    Usage:
        from api.private import PolymarketCredentials
        credentials = PolymarketCredentials(
            private_key="0x...",
            api_key="...",
            api_secret="...",
            passphrase="..."
        )
        client = PolymarketPrivateClient(credentials)
        await client.create_limit_order(token_id, "BUY", 0.55, 100)
    """

    # Polymarket CLOB endpoints
    HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137  # Polygon Mainnet

    def __init__(
        self,
        credentials,  # PolymarketCredentials ou dict-like avec les champs requis
        signature_type: SignatureType = SignatureType.EOA,
        funder_address: Optional[str] = None
    ):
        """
        Initialise le client privé.

        Args:
            credentials: Objet PolymarketCredentials ou dict avec private_key, api_key, api_secret, passphrase
            signature_type: Type de signature (EOA, MAGIC, BROWSER_PROXY)
            funder_address: Adresse du funder (pour proxy wallets)
        """
        # Supporter à la fois un objet credentials et un dict
        if hasattr(credentials, 'private_key'):
            self.private_key = credentials.private_key or ""
            self.api_key = credentials.api_key or ""
            self.api_secret = credentials.api_secret or ""
            self.passphrase = getattr(credentials, 'passphrase', "") or ""
        else:
            # Fallback pour dict
            self.private_key = credentials.get('private_key', "")
            self.api_key = credentials.get('api_key', "")
            self.api_secret = credentials.get('api_secret', "")
            self.passphrase = credentials.get('passphrase', "")

        self.signature_type = signature_type
        self.funder_address = funder_address

        self._client: Optional[ClobClient] = None
        self._initialized = False
        self._mock_mode = not _HAS_CLOB_CLIENT or not self.private_key

        # HFT: Thread pool dédié pour ordres (évite contention avec default pool)
        self._order_executor = ThreadPoolExecutor(
            max_workers=12,
            thread_name_prefix="polymarket-order"
        )

        if self._mock_mode:
            print("🔐 Private Client: Mode SIMULATION (pas de clé privée ou SDK manquant)")
        else:
            self._initialize_client()

    def _initialize_client(self) -> None:
        """Initialise le ClobClient officiel."""
        if not _HAS_CLOB_CLIENT:
            return

        try:
            # Configuration selon le type de signature
            kwargs = {
                "host": self.HOST,
                "key": self.private_key,
                "chain_id": self.CHAIN_ID,
            }

            # Ajouter credentials API si disponibles (utiliser ApiCreds, pas dict)
            if self.api_key and self.api_secret and self.passphrase:
                kwargs["creds"] = ApiCreds(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    api_passphrase=self.passphrase
                )

            # Configuration pour proxy wallets
            if self.signature_type != SignatureType.EOA:
                kwargs["signature_type"] = self.signature_type.value
                if self.funder_address:
                    kwargs["funder"] = self.funder_address

            self._client = ClobClient(**kwargs)
            self._initialized = True
            print("🔐 Private Client: Connecté à Polymarket CLOB")

        except Exception as e:
            print(f"❌ Erreur initialisation ClobClient: {e}")
            self._mock_mode = True

    @property
    def is_ready(self) -> bool:
        """Vérifie si le client est prêt pour trader."""
        return self._initialized and self._client is not None

    def close(self) -> None:
        """Ferme proprement le client et libère les ressources."""
        if self._order_executor:
            self._order_executor.shutdown(wait=False)
            self._order_executor = None

    def __del__(self):
        """Cleanup à la destruction."""
        self.close()

    async def get_balance(self) -> Dict[str, float]:
        """Récupère les balances du wallet."""
        if self._mock_mode:
            return {"USDC": 1000.0, "mock": True}

        try:
            # py-clob-client est synchrone, on l'exécute dans un thread
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(self._order_executor, self._client.get_balance_allowance)
            return result
        except Exception as e:
            print(f"❌ Erreur get_balance: {e}")
            return {}

    async def warm_connections(self) -> bool:
        """
        Pré-établit les connexions TLS pour réduire la latence du premier ordre.

        Appeler cette méthode au démarrage pour "chauffer" les connexions.
        Gain estimé: 50-150ms sur le premier ordre.

        Returns:
            True si le warming a réussi
        """
        if self._mock_mode:
            return True

        # Warming simple via HTTP GET sur l'endpoint public
        # Évite les bugs py-clob-client avec signature_type
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get(f"{self.HOST}/tick-size")
            print("⚡ [WARM] Connexions TLS pré-établies")
            return True
        except Exception:
            # Le warming est optionnel, ne pas bloquer si ça échoue
            return True

    async def create_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        time_in_force: str = "GTC"
    ) -> Dict[str, Any]:
        """
        Crée un ordre limite.

        Args:
            token_id: ID du token (YES ou NO)
            side: "BUY" ou "SELL"
            price: Prix de l'ordre (0.01 - 0.99)
            size: Quantité en shares
            time_in_force: GTC (Good Till Cancel) ou FOK (Fill or Kill)

        Returns:
            Détails de l'ordre créé
        """
        if self._mock_mode:
            print(f"📝 [SIMULATION] {side} {size} shares @ ${price} (token: {token_id[:16]}...)")
            return {
                "orderID": f"mock-{token_id[:8]}-{int(price*100)}",
                "status": "SIMULATED",
                "side": side,
                "price": price,
                "size": size
            }

        try:
            # Construire les arguments de l'ordre
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY if side.upper() == "BUY" else SELL,
            )

            # Créer et signer l'ordre (HFT: utilise thread pool dédié)
            loop = asyncio.get_event_loop()
            signed_order = await loop.run_in_executor(
                self._order_executor,
                self._client.create_order,
                order_args
            )

            # Soumettre l'ordre (HFT: utilise thread pool dédié)
            result = await loop.run_in_executor(
                self._order_executor,
                self._client.post_order,
                signed_order,
                OrderType.GTC if time_in_force == "GTC" else OrderType.FOK
            )

            print(f"✅ Ordre placé: {side} {size} @ ${price}")
            return result

        except Exception as e:
            print(f"❌ Erreur create_limit_order: {e}")
            return {"error": str(e), "status": "FAILED"}

    async def create_market_order(
        self,
        token_id: str,
        side: str,
        amount: float
    ) -> Dict[str, Any]:
        """
        Crée un ordre au marché.

        Args:
            token_id: ID du token
            side: "BUY" ou "SELL"
            amount: Montant en USDC (pour BUY) ou en shares (pour SELL)

        Returns:
            Détails de l'ordre
        """
        if self._mock_mode:
            print(f"📝 [SIMULATION] MARKET {side} ${amount} (token: {token_id[:16]}...)")
            return {
                "orderID": f"mock-market-{token_id[:8]}",
                "status": "SIMULATED",
                "side": side,
                "amount": amount
            }

        try:
            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=BUY if side.upper() == "BUY" else SELL,
            )

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._order_executor,
                self._client.create_and_post_market_order,
                order_args
            )

            print(f"✅ Ordre marché exécuté: {side} ${amount}")
            return result

        except Exception as e:
            print(f"❌ Erreur create_market_order: {e}")
            return {"error": str(e), "status": "FAILED"}

    async def place_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
        time_in_force: str = "GTC"
    ) -> Dict[str, Any]:
        """
        Place un ordre limite (alias pour create_limit_order).

        Utilisé par OrderExecutor pour compatibilité.

        Args:
            token_id: ID du token
            side: OrderSide.BUY ou OrderSide.SELL
            price: Prix limite
            size: Quantité
            time_in_force: "GTC" ou "FOK"

        Returns:
            Résultat de l'ordre avec ID
        """
        # Convertir OrderSide enum en string si nécessaire
        side_str = side.value if hasattr(side, 'value') else str(side)

        return await self.create_limit_order(
            token_id=token_id,
            side=side_str,
            price=price,
            size=size,
            time_in_force=time_in_force
        )

    # ═══════════════════════════════════════════════════════════════════
    # PRE-SIGNING: Signer maintenant, envoyer plus tard (HFT optimization)
    # ═══════════════════════════════════════════════════════════════════

    async def presign_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "GTC",
        ttl_seconds: float = 30.0
    ) -> Optional[PreSignedOrder]:
        """
        Pré-signe un ordre sans l'envoyer.

        Gain: ~5-8ms par ordre (la signature crypto est faite à l'avance).

        Args:
            token_id: ID du token
            side: "BUY" ou "SELL"
            price: Prix limite
            size: Quantité
            order_type: "GTC" ou "FOK"
            ttl_seconds: Durée de validité (défaut 30s)

        Returns:
            PreSignedOrder prêt à être envoyé, ou None si erreur
        """
        if self._mock_mode:
            now = time.time()
            return PreSignedOrder(
                signed_order={"mock": True, "token_id": token_id},
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                order_type=order_type,
                created_at=now,
                expires_at=now + ttl_seconds
            )

        try:
            # Construire les arguments de l'ordre
            order_side = BUY if side.upper() == "BUY" else SELL
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side
            )

            # Phase 1: Signer l'ordre (partie lente ~5-8ms)
            loop = asyncio.get_event_loop()
            signed_order = await loop.run_in_executor(
                self._order_executor,
                self._client.create_order,
                order_args
            )

            now = time.time()
            return PreSignedOrder(
                signed_order=signed_order,
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                order_type=order_type,
                created_at=now,
                expires_at=now + ttl_seconds
            )

        except Exception as e:
            print(f"❌ Erreur presign_order: {e}")
            return None

    async def submit_presigned(self, presigned: PreSignedOrder) -> Dict[str, Any]:
        """
        Envoie un ordre pré-signé (ultra-rapide, ~2-3ms).

        Args:
            presigned: Ordre pré-signé via presign_order()

        Returns:
            Résultat de l'ordre avec ID
        """
        if presigned.is_expired():
            return {
                "error": "Ordre pré-signé expiré",
                "status": "EXPIRED",
                "expired_since": -presigned.time_remaining()
            }

        if self._mock_mode:
            import uuid
            return {
                "orderID": f"mock-{uuid.uuid4().hex[:8]}",
                "status": "LIVE",
                "mock": True
            }

        try:
            # Phase 2: Envoyer l'ordre (partie rapide ~2-3ms)
            order_type = OrderType.GTC if presigned.order_type == "GTC" else OrderType.FOK

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._order_executor,
                self._client.post_order,
                presigned.signed_order,
                order_type
            )

            return result

        except Exception as e:
            print(f"❌ Erreur submit_presigned: {e}")
            return {"error": str(e), "status": "FAILED"}

    async def cancel_order(self, order_id: str) -> bool:
        """
        Annule un ordre.

        Args:
            order_id: ID de l'ordre à annuler

        Returns:
            True si annulé avec succès
        """
        if self._mock_mode:
            print(f"📝 [SIMULATION] Cancel order {order_id}")
            return True

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._order_executor,
                self._client.cancel,
                order_id
            )
            print(f"✅ Ordre annulé: {order_id}")
            return True

        except Exception as e:
            print(f"❌ Erreur cancel_order: {e}")
            return False

    async def redeem_all(self, condition_id: str) -> dict:
        """
        Tente de redeem tous les gains sur un marché résolu.
        
        Args:
            condition_id: ID de la condition (market)
            
        Returns:
            Résultat de la transaction
        """
        if self._mock_mode:
            print(f"🔹 [MOCK] Redeem calls for {condition_id}")
            return {"status": "success", "mock": True}

        # Adapter selon la méthode réelle de la lib py-clob-client
        try:
            # Vérifier si client a accès aux méthodes d'exchange
            # Ceci est expérimental selon la version de la lib
            if hasattr(self._client, "exchange") and hasattr(self._client.exchange, "redeem_all"):
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(self._order_executor, self._client.exchange.redeem_all, condition_id)
            elif hasattr(self._client, "redeem_all"):
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(self._order_executor, self._client.redeem_all, condition_id)
            else:
                 raise NotImplementedError("La méthode redeem_all n'est pas disponible dans cette version du client")
        except Exception as e:
            print(f"❌ Erreur API Redeem: {e}")
            raise

    async def cancel_all_orders(self) -> bool:
        """Annule tous les ordres ouverts."""
        if self._mock_mode:
            print("📝 [SIMULATION] Cancel all orders")
            return True

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._order_executor,
                self._client.cancel_all
            )
            print("✅ Tous les ordres annulés")
            return True

        except Exception as e:
            print(f"❌ Erreur cancel_all_orders: {e}")
            return False

    async def get_open_orders(self) -> List[Dict[str, Any]]:
        """Récupère les ordres ouverts."""
        if self._mock_mode:
            return []

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._order_executor,
                self._client.get_orders
            )
            return result

        except Exception as e:
            print(f"❌ Erreur get_open_orders: {e}")
            return []

    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupère les détails d'un ordre spécifique.

        Args:
            order_id: ID de l'ordre

        Returns:
            Détails de l'ordre ou None si non trouvé
        """
        if self._mock_mode:
            return {
                "orderID": order_id,
                "status": "live",
                "sizeFilled": 0,
                "price": 0.50,
                "mock": True
            }

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._order_executor,
                lambda: self._client.get_order(order_id)
            )
            return result

        except Exception as e:
            print(f"❌ Erreur get_order: {e}")
            return None

    async def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Récupère l'historique des trades."""
        if self._mock_mode:
            return []

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._order_executor,
                lambda: self._client.get_trades(limit=limit)
            )
            return result

        except Exception as e:
            print(f"❌ Erreur get_trades: {e}")
            return []

    # Alias pour compatibilité avec l'ancien code
    async def create_order(
        self,
        market_id: str,
        side: str,
        price: float,
        size: float
    ) -> Dict[str, Any]:
        """Alias pour create_limit_order (compatibilité)."""
        return await self.create_limit_order(
            token_id=market_id,
            side=side.upper(),
            price=price,
            size=size
        )

    # ═══════════════════════════════════════════════════════════════════
    # 4.2: Smart Orders & Advanced Order Types
    # ═══════════════════════════════════════════════════════════════════

    async def create_limit_order_smart(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        prefer_immediate: bool = True
    ) -> Dict[str, Any]:
        """
        Ordre intelligent avec fallback FOK → GTC.

        Stratégie:
        1. Essaye FOK (Fill or Kill) pour fill immédiat complet
        2. Si FOK échoue (liquidité insuffisante), fallback en GTC

        Args:
            token_id: ID du token
            side: "BUY" ou "SELL"
            price: Prix de l'ordre
            size: Quantité en shares
            prefer_immediate: Si True, essaye FOK d'abord

        Returns:
            Détails de l'ordre
        """
        if prefer_immediate:
            # Essayer FOK d'abord pour fill immédiat
            result = await self.create_limit_order(
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                time_in_force="FOK"
            )

            # Vérifier si succès
            if not result.get("error") and result.get("status") != "FAILED":
                return result

            # FOK a échoué, essayer GTC
            print(f"⚠️ [Smart Order] FOK échoué, fallback GTC")

        # Fallback ou mode direct GTC
        return await self.create_limit_order(
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            time_in_force="GTC"
        )

    async def create_iceberg_order(
        self,
        token_id: str,
        side: str,
        price: float,
        total_size: float,
        tranche_size: float = 50.0,
        delay_between_tranches: float = 0.1
    ) -> List[Dict[str, Any]]:
        """
        Divise un gros ordre en tranches pour minimiser l'impact de marché.

        Utile pour les ordres > 100 shares pour éviter le slippage.

        Args:
            token_id: ID du token
            side: "BUY" ou "SELL"
            price: Prix de l'ordre
            total_size: Taille totale en shares
            tranche_size: Taille de chaque tranche (défaut: 50)
            delay_between_tranches: Délai entre tranches en secondes

        Returns:
            Liste des résultats de chaque tranche
        """
        results = []
        remaining = total_size
        tranche_num = 0

        while remaining > 0:
            # Calculer la taille de cette tranche
            size = min(remaining, tranche_size)
            tranche_num += 1

            # Placer l'ordre
            result = await self.create_limit_order(
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                time_in_force="GTC"
            )

            results.append({
                "tranche": tranche_num,
                "size": size,
                "result": result
            })

            remaining -= size

            # Petit délai entre tranches (sauf pour la dernière)
            if remaining > 0:
                await asyncio.sleep(delay_between_tranches)

        print(f"✅ [Iceberg] {tranche_num} tranches placées, total: {total_size} shares")
        return results

    async def create_twap_order(
        self,
        token_id: str,
        side: str,
        price: float,
        total_size: float,
        duration_seconds: float = 60.0,
        num_slices: int = 6
    ) -> List[Dict[str, Any]]:
        """
        Time-Weighted Average Price (TWAP) order.

        Répartit un ordre sur une période de temps pour obtenir
        un prix moyen plus stable.

        Args:
            token_id: ID du token
            side: "BUY" ou "SELL"
            price: Prix limite
            total_size: Taille totale
            duration_seconds: Durée totale (défaut: 60s)
            num_slices: Nombre de tranches (défaut: 6)

        Returns:
            Liste des résultats de chaque slice
        """
        results = []
        slice_size = total_size / num_slices
        delay = duration_seconds / num_slices

        for i in range(num_slices):
            result = await self.create_limit_order(
                token_id=token_id,
                side=side,
                price=price,
                size=slice_size,
                time_in_force="GTC"
            )

            results.append({
                "slice": i + 1,
                "size": slice_size,
                "timestamp": asyncio.get_event_loop().time(),
                "result": result
            })

            # Attendre avant la prochaine slice (sauf pour la dernière)
            if i < num_slices - 1:
                await asyncio.sleep(delay)

        print(f"✅ [TWAP] {num_slices} slices sur {duration_seconds}s")
        return results
