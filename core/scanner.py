"""
Market Scanner - Scanne les marchés crypto Up/Down en temps réel

Fonctionnalités:
1. Récupère tous les marchés actifs de Polymarket
2. Filtre par mots-clés (BTC, SOL, ETH, XRP)
3. Filtre par type (Up/Down)
4. Analyse les orderbooks en temps réel
5. Émet des événements quand une opportunité est détectée

Optimisations HFT v2.0:
- Concurrence augmentée (20 requêtes parallèles)
- Batch processing pour orderbooks
- Cache intégré pour réduire latence
"""

import asyncio
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import time

from api.public import PolymarketPublicClient, GammaClient, WebSocketFeed
from api.public.polymarket_public import Market, OrderBook
from api.public.websocket_feed import PriceUpdate, BookUpdate
from config import get_settings, get_trading_params


class ScannerState(Enum):
    """États du scanner."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass(slots=True)
class MarketData:
    """Données complètes d'un marché (slots=True pour performance HFT)."""
    market: Market
    orderbook_yes: Optional[dict] = None
    orderbook_no: Optional[dict] = None
    
    # Prix best bid/ask
    best_bid_yes: Optional[float] = None
    best_ask_yes: Optional[float] = None
    best_bid_no: Optional[float] = None
    best_ask_no: Optional[float] = None
    
    # Spreads calculés
    spread_yes: Optional[float] = None
    spread_no: Optional[float] = None
    
    # Métadonnées
    last_update: datetime = field(default_factory=datetime.now)
    
    @property
    def effective_spread(self) -> float:
        """Spread effectif moyen."""
        spreads = [s for s in [self.spread_yes, self.spread_no] if s is not None]
        return sum(spreads) / len(spreads) if spreads else 0.0
    
    @property
    def is_valid(self) -> bool:
        """Vérifie si les données sont complètes."""
        return (
            self.best_bid_yes is not None and
            self.best_ask_yes is not None and
            self.spread_yes is not None
        )


class MarketScanner:
    """
    Scanne les marchés crypto Up/Down de Polymarket.

    Usage:
        scanner = MarketScanner()
        scanner.on_market_update = my_callback
        await scanner.start()

    Optimisations HFT:
        - 20 requêtes parallèles (au lieu de 10)
        - Batch processing des orderbooks
        - Métriques de performance intégrées
    """

    def __init__(self):
        self.settings = get_settings()
        self._state = ScannerState.STOPPED
        self._markets: dict[str, MarketData] = {}
        self._scan_task: Optional[asyncio.Task] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._concurrency = asyncio.Semaphore(20)  # HFT: 20 requêtes parallèles

        # Clients API
        self._polymarket_client: Optional[PolymarketPublicClient] = None
        self._gamma_client: Optional[GammaClient] = None
        self._ws_feed: Optional[WebSocketFeed] = None

        # Callbacks
        self.on_market_update: Optional[Callable[[MarketData], None]] = None
        self.on_new_market: Optional[Callable[[Market], None]] = None
        self.on_market_removed: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        self.on_state_change: Optional[Callable[[ScannerState], None]] = None
        # HFT: Callback pour analyse immédiate event-driven (gain ~20-50ms)
        self.on_immediate_analysis: Optional[Callable[[MarketData], Any]] = None

        # Métriques de performance
        self._last_cycle_duration: float = 0.0
        self._total_cycles: int = 0
        self._avg_cycle_duration: float = 0.0
        self._ws_updates: int = 0  # Compteur updates WebSocket

        # Mapping token_id -> market_id pour WebSocket
        self._token_to_market: dict[str, tuple[str, str]] = {}  # token_id -> (market_id, "yes"|"no")

        # IDs prioritaires pour refresh (marchés avec positions actives)
        self._priority_market_ids: set = set()

        # Flag pour éviter spam logs WebSocket
        self._ws_logged_disconnect: bool = False

        # 5.12: Cache métadonnées marchés (refresh périodique)
        self._last_markets_refresh: float = 0.0
        self._markets_refresh_interval: float = 60.0  # Refresh liste marchés toutes les 60s

        # HFT: Event-driven trigger parameters (Ultra-sensible)
        self._min_spread_trigger: float = 0.025  # Spread 2.5 cents minimum pour trigger
        self._immediate_triggers: int = 0  # Compteur de triggers immédiats
        self._last_trigger_times: dict[str, float] = {}  # Anti-spam: market_id -> last_trigger_time
        self._trigger_cooldown: float = 0.3  # Cooldown 300ms (plus réactif)

        # Thread-safety pour accès concurrent aux marchés
        self._markets_lock = asyncio.Lock()

    @property
    def state(self) -> ScannerState:
        """État actuel du scanner."""
        return self._state

    @property
    def markets(self) -> dict[str, MarketData]:
        """Marchés actuellement suivis (référence directe - pas de copie)."""
        return self._markets  # 5.6: Retourner référence directe (pas de .copy())

    async def get_markets_snapshot(self) -> dict[str, MarketData]:
        """Retourne une copie thread-safe des marchés pour éviter les race conditions."""
        async with self._markets_lock:
            return dict(self._markets)

    @property
    def market_count(self) -> int:
        """Nombre de marchés suivis."""
        return len(self._markets)

    @property
    def performance_stats(self) -> dict:
        """Retourne les statistiques de performance du scanner."""
        return {
            "last_cycle_ms": round(self._last_cycle_duration * 1000, 1),
            "avg_cycle_ms": round(self._avg_cycle_duration * 1000, 1),
            "total_cycles": self._total_cycles,
            "markets_count": len(self._markets),
            "concurrency_limit": 20,
            "ws_connected": self._ws_feed.is_connected if self._ws_feed else False,
            "ws_updates": self._ws_updates,
            "immediate_triggers": self._immediate_triggers,  # HFT: Triggers event-driven
        }

    @property
    def is_websocket_connected(self) -> bool:
        """Vérifie si le WebSocket est connecté."""
        return self._ws_feed.is_connected if self._ws_feed else False

    def _set_state(self, state: ScannerState) -> None:
        """Change l'état du scanner."""
        self._state = state
        if self.on_state_change:
            self.on_state_change(state)

    async def start(self) -> None:
        """
        Démarre le scanner avec WebSocket temps réel.
        """
        if self._state == ScannerState.RUNNING:
            return

        self._set_state(ScannerState.STARTING)

        try:
            # Initialiser les clients
            self._polymarket_client = PolymarketPublicClient()
            self._gamma_client = GammaClient()

            await self._polymarket_client.__aenter__()
            await self._gamma_client.__aenter__()

            # Charger les marchés initiaux
            await self._load_markets()

            # Initialiser le WebSocket pour données temps réel
            await self._init_websocket()

            # Démarrer la boucle de scan (fallback + refresh marchés)
            self._scan_task = asyncio.create_task(self._scan_loop())

            self._set_state(ScannerState.RUNNING)

        except Exception as e:
            self._set_state(ScannerState.ERROR)
            if self.on_error:
                self.on_error(e)
            raise

    async def _init_websocket(self) -> None:
        """Initialise et connecte le WebSocket pour données temps réel."""
        self._ws_logged_disconnect = False  # Flag pour éviter spam logs

        try:
            self._ws_feed = WebSocketFeed()

            # Configurer les callbacks
            self._ws_feed.on_price_update = self._handle_price_update
            self._ws_feed.on_book_update = self._handle_book_update
            self._ws_feed.on_error = self._handle_ws_error
            self._ws_feed.on_connect = lambda: print("🔌 [WS] WebSocket connecté - Mode temps réel activé")

            def on_ws_disconnect():
                if not self._ws_logged_disconnect:
                    print("⚠️ [WS] WebSocket déconnecté - Fallback REST")
                    self._ws_logged_disconnect = True

            self._ws_feed.on_disconnect = on_ws_disconnect

            # Construire le mapping token_id -> market
            self._build_token_mapping()

            # Se connecter
            connected = await self._ws_feed.connect()
            if connected:
                # S'abonner à tous les tokens
                token_ids = list(self._token_to_market.keys())
                if token_ids:
                    try:
                        await self._ws_feed.subscribe(token_ids)
                        print(f"📡 [WS] Abonné à {len(token_ids)} tokens")
                    except Exception:
                        print("⚠️ [WS] Échec subscription - Mode REST uniquement")
                        return

                # Lancer la tâche d'écoute en background
                self._ws_task = asyncio.create_task(self._ws_feed.listen())
            else:
                print("ℹ️ [WS] WebSocket non disponible - Mode REST (normal)")

        except Exception as e:
            print(f"ℹ️ [WS] Mode REST uniquement (WebSocket: {type(e).__name__})")

    def _build_token_mapping(self) -> None:
        """Construit le mapping token_id -> (market_id, side)."""
        self._token_to_market.clear()
        for market_id, market_data in self._markets.items():
            market = market_data.market
            self._token_to_market[market.token_yes_id] = (market_id, "yes")
            self._token_to_market[market.token_no_id] = (market_id, "no")

    def _handle_price_update(self, update: PriceUpdate) -> None:
        """Handler pour les mises à jour de prix WebSocket."""
        mapping = self._token_to_market.get(update.token_id)
        if not mapping:
            return

        market_id, side = mapping
        market_data = self._markets.get(market_id)
        if not market_data:
            return

        self._ws_updates += 1

        # Mettre à jour le prix selon le côté
        if side == "yes":
            market_data.best_ask_yes = update.price
        else:
            market_data.best_ask_no = update.price

        market_data.last_update = datetime.now()

        if self.on_market_update:
            self.on_market_update(market_data)

    def _handle_book_update(self, update: BookUpdate) -> None:
        """Handler pour les mises à jour d'orderbook WebSocket."""
        mapping = self._token_to_market.get(update.token_id)
        if not mapping:
            return

        market_id, side = mapping
        market_data = self._markets.get(market_id)
        if not market_data:
            return

        self._ws_updates += 1

        # Mettre à jour les prix et spreads
        if side == "yes":
            if update.bids:
                market_data.best_bid_yes = update.bids[0][0]
            if update.asks:
                market_data.best_ask_yes = update.asks[0][0]
            if market_data.best_bid_yes and market_data.best_ask_yes:
                market_data.spread_yes = market_data.best_ask_yes - market_data.best_bid_yes
        else:
            if update.bids:
                market_data.best_bid_no = update.bids[0][0]
            if update.asks:
                market_data.best_ask_no = update.asks[0][0]
            if market_data.best_bid_no and market_data.best_ask_no:
                market_data.spread_no = market_data.best_ask_no - market_data.best_bid_no

        market_data.last_update = datetime.now()

        if self.on_market_update:
            self.on_market_update(market_data)

        # HFT: Event-driven trigger - Analyse immédiate si conditions remplies
        if self._should_trigger_analysis(market_data):
            self._trigger_immediate_analysis(market_data)

    def _handle_ws_error(self, error: Exception) -> None:
        """Handler pour les erreurs WebSocket."""
        print(f"⚠️ [WS] Erreur: {error}")

    def _should_trigger_analysis(self, market_data: MarketData) -> bool:
        """
        HFT: Détermine si on doit déclencher une analyse immédiate.

        Conditions pour trigger:
        - Données valides (bid/ask présents)
        - Spread suffisant (> min_spread_trigger)
        - Pas de trigger récent pour ce marché (cooldown)

        Args:
            market_data: Données du marché mises à jour

        Returns:
            True si l'analyse immédiate doit être déclenchée
        """
        if not market_data.is_valid:
            return False

        # Vérifier le spread minimum
        spread = market_data.spread_yes or 0
        if spread < self._min_spread_trigger:
            return False

        # Vérifier le cooldown anti-spam
        market_id = market_data.market.id
        now = time.time()
        last_trigger = self._last_trigger_times.get(market_id, 0)

        if now - last_trigger < self._trigger_cooldown:
            return False

        return True

    def _trigger_immediate_analysis(self, market_data: MarketData) -> None:
        """
        HFT: Déclenche l'analyse immédiate de façon asynchrone.

        Cette méthode est appelée depuis les handlers WebSocket (sync)
        et lance l'analyse en background sans bloquer.
        """
        if not self.on_immediate_analysis:
            return

        # Mettre à jour le cooldown
        market_id = market_data.market.id
        self._last_trigger_times[market_id] = time.time()
        self._immediate_triggers += 1

        # Appeler le callback (peut être sync ou async)
        try:
            result = self.on_immediate_analysis(market_data)
            # Si c'est une coroutine, la programmer pour exécution
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
        except Exception:
            pass  # Ne pas bloquer le WebSocket sur erreur callback

    async def stop(self) -> None:
        """Arrête le scanner."""
        self._set_state(ScannerState.STOPPED)
        
        # Annuler les tâches
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        # Fermer les clients
        if self._polymarket_client:
            await self._polymarket_client.__aexit__(None, None, None)
        if self._gamma_client:
            await self._gamma_client.__aexit__(None, None, None)
        if self._ws_feed:
            await self._ws_feed.disconnect()
            
    def pause(self) -> None:
        if self._state == ScannerState.RUNNING:
            self._set_state(ScannerState.PAUSED)
            
    def resume(self) -> None:
        if self._state == ScannerState.PAUSED:
            self._set_state(ScannerState.RUNNING)

    async def _fetch_market_details(self, condition_id: str) -> Optional[Market]:
        """Worker pour récupérer les détails d'un marché avec Semaphore."""
        async with self._concurrency:
            try:
                market_details = await self._polymarket_client.get_market(condition_id)
                if not market_details:
                    return None
                return self._polymarket_client.parse_market(market_details)
            except Exception as e:
                # print(f"❌ Error fetching {condition_id}: {e}")
                return None

    async def _load_markets(self) -> None:
        """Charge et filtre les marchés crypto Up/Down via Gamma API (Parallélisé)."""
        if not self._polymarket_client or not self._gamma_client:
            return
        
        try:
            # 1. Découverte rapide via Gamma API
            print("🔄 [Scanner] Recherche via Gamma API...")
            gamma_markets = await self._gamma_client.get_crypto_markets()
            print(f"✅ [Scanner] Gamma trouvé: {len(gamma_markets)} marchés potentiels")
            
            # Filtrer ceux qu'on a déjà
            candidates = []
            for gm in gamma_markets:
                condition_id = gm.get("conditionId") or gm.get("condition_id") or gm.get("id")
                if condition_id and not any(m.market.condition_id == condition_id for m in self._markets.values()):
                    candidates.append(condition_id)
            
            if not candidates:
                print("🎉 [Scanner] Aucun nouveau marché à ajouter.")
                return

            print(f"🚀 [Scanner] Traitement parallèle de {len(candidates)} marchés...")
            
            # 2. Récupération parallèle des détails
            tasks = [self._fetch_market_details(cid) for cid in candidates]
            results = await asyncio.gather(*tasks)
            
            count_added = 0
            for market in results:
                if market and market.active:
                    self._markets[market.id] = MarketData(market=market)
                    count_added += 1
                    if self.on_new_market:
                        self.on_new_market(market)
            
            print(f"🎉 [Scanner] Chargement terminé. {count_added} nouveaux marchés ajoutés.")
                    
        except Exception as e:
            print(f"❌ [Scanner] Erreur globale load_markets: {e}")
            if self.on_error:
                self.on_error(e)

    async def _scan_loop(self) -> None:
        """Boucle principale de scan avec métriques de performance."""
        error_count = 0
        max_errors = 5

        while self._state in (ScannerState.RUNNING, ScannerState.PAUSED):
            try:
                if self._state == ScannerState.PAUSED:
                    await asyncio.sleep(1)
                    continue

                # Mesure du temps de cycle
                cycle_start = time.perf_counter()

                await self._refresh_markets()
                await self._update_orderbooks()

                # Calcul des métriques de performance
                cycle_duration = time.perf_counter() - cycle_start
                self._last_cycle_duration = cycle_duration
                self._total_cycles += 1

                # Moyenne mobile exponentielle pour cycle moyen
                alpha = 0.1
                if self._avg_cycle_duration == 0:
                    self._avg_cycle_duration = cycle_duration
                else:
                    self._avg_cycle_duration = (alpha * cycle_duration +
                                                (1 - alpha) * self._avg_cycle_duration)

                # Reset error count on successful cycle
                error_count = 0
                
                # 9.0: Auto-Reconnect WebSocket check
                if not self.is_websocket_connected and self._total_cycles % 60 == 0:
                     print("🔄 [Scanner] Tentative reconnexion WebSocket...")
                     await self._init_websocket()

                # Intervalle de scan dynamique (min 1s si cycle long)
                sleep_time = max(0.5, self.settings.scan_interval_seconds - cycle_duration)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as e:
                error_count += 1
                print(f"⚠️ [Scanner] Erreur cycle #{error_count}: {e}")

                if self.on_error:
                    self.on_error(e)

                # Backoff progressif en cas d'erreurs répétées
                if error_count >= max_errors:
                    print(f"❌ [Scanner] Trop d'erreurs ({error_count}), pause de 30s")
                    await asyncio.sleep(30)
                    error_count = 0
                else:
                    await asyncio.sleep(2 * error_count)  # Backoff plus court pour HFT

    async def _refresh_markets(self) -> None:
        """
        5.12: Rafraîchit la liste des marchés (avec cache).

        Ne fait un refresh complet que toutes les 60 secondes
        pour réduire les appels API. Les orderbooks sont mis à jour
        plus fréquemment séparément.
        """
        now = time.time()

        # 5.12: Vérifier si on doit rafraîchir la liste des marchés
        if now - self._last_markets_refresh >= self._markets_refresh_interval:
            await self._load_markets()
            self._last_markets_refresh = now
        # Sinon, on garde les marchés existants et on update juste les orderbooks
    
    async def _fetch_single_orderbook(self, market_data: MarketData) -> None:
        """Worker pour update un seul orderbook (optimisé: parallel + cache)."""
        async with self._concurrency:
            try:
                # 5.4 + 5.5: Fetch YES et NO en PARALLÈLE avec cache activé
                orderbook_yes, orderbook_no = await asyncio.gather(
                    self._polymarket_client.get_orderbook(
                        market_data.market.token_yes_id,
                        use_cache=True  # 5.5: Activer le cache
                    ),
                    self._polymarket_client.get_orderbook(
                        market_data.market.token_no_id,
                        use_cache=True  # 5.5: Activer le cache
                    )
                )

                # Parse YES orderbook
                market_data.orderbook_yes = orderbook_yes
                bids = orderbook_yes.get("bids", [])
                asks = orderbook_yes.get("asks", [])
                market_data.best_bid_yes = float(bids[0]["price"]) if bids else None
                market_data.best_ask_yes = float(asks[0]["price"]) if asks else None
                if market_data.best_bid_yes and market_data.best_ask_yes:
                    market_data.spread_yes = market_data.best_ask_yes - market_data.best_bid_yes

                # Parse NO orderbook
                market_data.orderbook_no = orderbook_no
                bids = orderbook_no.get("bids", [])
                asks = orderbook_no.get("asks", [])
                market_data.best_bid_no = float(bids[0]["price"]) if bids else None
                market_data.best_ask_no = float(asks[0]["price"]) if asks else None
                if market_data.best_bid_no and market_data.best_ask_no:
                    market_data.spread_no = market_data.best_ask_no - market_data.best_bid_no

                market_data.last_update = datetime.now()

                if self.on_market_update:
                    self.on_market_update(market_data)

            except Exception:
                pass

    async def _update_orderbooks(self) -> None:
        """
        Met à jour les orderbooks en parallèle.

        Optimisation HFT: Fetch les marchés prioritaires (positions actives) EN PREMIER.
        """
        if not self._polymarket_client:
            return

        # Séparer marchés prioritaires et autres
        priority_markets = []
        other_markets = []

        for md in self._markets.values():
            if md.market.id in self._priority_market_ids:
                priority_markets.append(md)
            else:
                other_markets.append(md)

        # Fetch prioritaires d'abord (données plus fraîches pour stratégie)
        if priority_markets:
            priority_tasks = [self._fetch_single_orderbook(md) for md in priority_markets]
            await asyncio.gather(*priority_tasks)

        # Puis les autres
        if other_markets:
            other_tasks = [self._fetch_single_orderbook(md) for md in other_markets]
            await asyncio.gather(*other_tasks)

    def set_priority_markets(self, market_ids: set) -> None:
        """Définit les marchés prioritaires pour le refresh."""
        self._priority_market_ids = market_ids

    async def get_market_data(self, market_id: str) -> Optional[MarketData]:
        """Récupère les données d'un marché spécifique."""
        return self._markets.get(market_id)

    async def force_refresh(self) -> None:
        """Force un rafraîchissement immédiat."""
        await self._refresh_markets()
        await self._update_orderbooks()
