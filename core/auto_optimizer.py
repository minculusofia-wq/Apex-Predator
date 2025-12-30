"""
Auto-Optimizer v7.2 - Optimisation Dynamique Dual-Strategy

Ajuste automatiquement les paramètres en temps réel pour:
- Gabagool: max_pair_cost, min_improvement selon spread/volume
- Smart Ape: window_minutes, dump_threshold, payout_ratio selon volatilité BTC

Sources de données:
- Scanner Polymarket (spread, volume, liquidité)
- CoinGecko (volatilité crypto globale)
- Binance (prix BTC temps réel pour Smart Ape)

Modes:
- MANUAL: Paramètres fixes
- SEMI_AUTO: Suggestions avec confirmation
- FULL_AUTO: Ajustement automatique
"""

import asyncio
import httpx
from typing import Optional, Dict, List, TYPE_CHECKING, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

if TYPE_CHECKING:
    from core.scanner import Scanner, MarketData
    from core.gabagool import GabagoolEngine
    from core.smart_ape import SmartApeEngine
    from api.public.coingecko_client import CoinGeckoClient


class OptimizerMode(Enum):
    """Mode de fonctionnement de l'optimiseur."""
    MANUAL = "manual"           # Paramètres fixes
    SEMI_AUTO = "semi_auto"     # Suggestions avec confirmation
    FULL_AUTO = "full_auto"     # Ajustement automatique


@dataclass
class BTCConditions:
    """Conditions BTC temps réel pour Smart Ape."""
    price: float = 0.0                    # Prix actuel BTC
    price_1m_ago: float = 0.0             # Prix il y a 1 minute
    price_5m_ago: float = 0.0             # Prix il y a 5 minutes
    change_1m_pct: float = 0.0            # Variation 1 min en %
    change_5m_pct: float = 0.0            # Variation 5 min en %
    volatility_1m: float = 0.0            # Volatilité 1 min
    momentum: str = "neutral"             # "up", "down", "neutral"
    last_updated: datetime = field(default_factory=datetime.now)


@dataclass
class MarketConditions:
    """Snapshot des conditions de marché actuelles."""
    # Polymarket general
    avg_spread: float = 0.10            # Spread moyen sur les marchés actifs
    avg_volume: float = 20000.0         # Volume moyen 24h
    avg_liquidity: float = 10000.0      # Liquidité moyenne
    volatility_score: float = 50.0      # Score volatilité crypto (0-100)
    ws_connected: bool = False          # WebSocket actif

    # Gabagool specific
    gabagool_active_positions: int = 0
    gabagool_locked_positions: int = 0
    gabagool_avg_pair_cost: float = 1.0

    # Smart Ape specific
    smart_ape_active_rounds: int = 0
    smart_ape_avg_payout: float = 1.5

    # BTC (for Smart Ape)
    btc: BTCConditions = field(default_factory=BTCConditions)

    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class GabagoolParams:
    """Paramètres optimisés pour Gabagool."""
    max_pair_cost: float = 0.975        # 0.95 - 0.985
    min_improvement: float = 0.005      # 0.000 - 0.010
    first_buy_threshold: float = 0.55   # 0.45 - 0.65

    def to_dict(self) -> dict:
        return {
            "max_pair_cost": round(self.max_pair_cost, 4),
            "min_improvement": round(self.min_improvement, 4),
            "first_buy_threshold": round(self.first_buy_threshold, 3),
        }


@dataclass
class SmartApeParams:
    """Paramètres optimisés pour Smart Ape."""
    window_minutes: int = 2             # 1 - 4
    dump_threshold: float = 0.15        # 0.10 - 0.25
    min_payout_ratio: float = 1.5       # 1.3 - 2.0

    def to_dict(self) -> dict:
        return {
            "window_minutes": self.window_minutes,
            "dump_threshold": round(self.dump_threshold, 3),
            "min_payout_ratio": round(self.min_payout_ratio, 2),
        }


@dataclass
class OptimizationEvent:
    """Événement de modification de paramètres."""
    timestamp: datetime
    strategy: str           # "gabagool" or "smart_ape"
    param_name: str
    old_value: float
    new_value: float
    reason: str


class AutoOptimizer:
    """
    Moteur d'optimisation automatique dual-strategy.

    Optimise séparément:
    - Gabagool: basé sur spread, volume, pair_cost moyen
    - Smart Ape: basé sur prix BTC, volatilité, momentum

    Usage:
        optimizer = AutoOptimizer(scanner, gabagool, smart_ape)
        await optimizer.start()
    """

    def __init__(
        self,
        scanner: Optional["Scanner"] = None,
        gabagool: Optional["GabagoolEngine"] = None,
        smart_ape: Optional["SmartApeEngine"] = None,
        mode: OptimizerMode = OptimizerMode.FULL_AUTO
    ):
        self.scanner = scanner
        self.gabagool = gabagool
        self.smart_ape = smart_ape
        self.mode = mode
        self._enabled = True
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Contrôle par stratégie
        self._optimize_gabagool = True
        self._optimize_smart_ape = True

        # Intervalle de mise à jour (secondes)
        self._update_interval = 5.0

        # État actuel
        self._conditions: Optional[MarketConditions] = None
        self._gabagool_params: GabagoolParams = GabagoolParams()
        self._smart_ape_params: SmartApeParams = SmartApeParams()
        self._last_update: Optional[datetime] = None

        # Historique BTC pour calcul momentum
        self._btc_history: List[Tuple[datetime, float]] = []

        # Historique des modifications
        self._events: List[OptimizationEvent] = []
        self._total_adjustments = 0

        # Clients externes
        self._cg_client: Optional["CoinGeckoClient"] = None
        self._cg_client_initialized = False
        self._http_client: Optional[httpx.AsyncClient] = None

        # Callbacks
        self.on_params_updated: Optional[callable] = None

    # ═══════════════════════════════════════════════════════════════
    # PROPRIÉTÉS
    # ═══════════════════════════════════════════════════════════════

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    @property
    def conditions(self) -> Optional[MarketConditions]:
        return self._conditions

    @property
    def gabagool_params(self) -> GabagoolParams:
        return self._gabagool_params

    @property
    def smart_ape_params(self) -> SmartApeParams:
        return self._smart_ape_params

    @property
    def last_update(self) -> Optional[datetime]:
        return self._last_update

    @property
    def total_adjustments(self) -> int:
        return self._total_adjustments

    @property
    def recent_events(self) -> List[OptimizationEvent]:
        """Retourne les 20 derniers événements."""
        return self._events[-20:]

    # ═══════════════════════════════════════════════════════════════
    # CONTRÔLE
    # ═══════════════════════════════════════════════════════════════

    async def start(self) -> None:
        """Démarre la boucle d'optimisation."""
        if self._running:
            return

        self._running = True
        self._http_client = httpx.AsyncClient(timeout=5.0)
        self._task = asyncio.create_task(self._optimization_loop())
        print(f"🧠 [Optimizer] Démarré en mode {self.mode.value} (Gabagool={self._optimize_gabagool}, SmartApe={self._optimize_smart_ape})")

    async def stop(self) -> None:
        """Arrête la boucle d'optimisation."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Fermer les clients
        if self._cg_client:
            try:
                await self._cg_client.__aexit__(None, None, None)
            except Exception:
                pass
            self._cg_client = None
            self._cg_client_initialized = False

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        print("🧠 [Optimizer] Arrêté")

    def set_mode(self, mode: OptimizerMode) -> None:
        """Change le mode de fonctionnement."""
        old_mode = self.mode
        self.mode = mode
        print(f"🧠 [Optimizer] Mode changé: {old_mode.value} → {mode.value}")

    def set_strategy_optimization(self, gabagool: bool = None, smart_ape: bool = None) -> None:
        """Active/désactive l'optimisation par stratégie."""
        if gabagool is not None:
            self._optimize_gabagool = gabagool
        if smart_ape is not None:
            self._optimize_smart_ape = smart_ape
        print(f"🧠 [Optimizer] Gabagool={self._optimize_gabagool}, SmartApe={self._optimize_smart_ape}")

    # ═══════════════════════════════════════════════════════════════
    # BOUCLE PRINCIPALE
    # ═══════════════════════════════════════════════════════════════

    async def _optimization_loop(self) -> None:
        """Boucle principale d'optimisation."""
        while self._running:
            try:
                if self._enabled and self.mode != OptimizerMode.MANUAL:
                    # 1. Collecter les conditions actuelles
                    self._conditions = await self._collect_conditions()

                    # 2. Optimiser Gabagool si activé
                    if self._optimize_gabagool and self.gabagool:
                        self._gabagool_params = self._optimize_gabagool_params(self._conditions)
                        if self.mode == OptimizerMode.FULL_AUTO:
                            self._apply_gabagool_params(self._gabagool_params)

                    # 3. Optimiser Smart Ape si activé
                    if self._optimize_smart_ape and self.smart_ape:
                        self._smart_ape_params = self._optimize_smart_ape_params(self._conditions)
                        if self.mode == OptimizerMode.FULL_AUTO:
                            self._apply_smart_ape_params(self._smart_ape_params)

                    self._last_update = datetime.now()

                await asyncio.sleep(self._update_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ [Optimizer] Erreur: {e}")
                await asyncio.sleep(self._update_interval)

    # ═══════════════════════════════════════════════════════════════
    # COLLECTE DES CONDITIONS
    # ═══════════════════════════════════════════════════════════════

    async def _collect_conditions(self) -> MarketConditions:
        """Collecte les métriques de marché actuelles."""
        conditions = MarketConditions()

        # Données du scanner Polymarket
        if self.scanner:
            markets = list(self.scanner.markets.values())

            if markets:
                spreads = [m.effective_spread for m in markets if m.is_valid and m.effective_spread > 0]
                if spreads:
                    conditions.avg_spread = sum(spreads) / len(spreads)

                volumes = [m.market.volume for m in markets if m.market.volume > 0]
                if volumes:
                    conditions.avg_volume = sum(volumes) / len(volumes)

                liquidities = [m.market.liquidity for m in markets if m.market.liquidity > 0]
                if liquidities:
                    conditions.avg_liquidity = sum(liquidities) / len(liquidities)

            conditions.ws_connected = self.scanner._ws_feed.is_connected if self.scanner._ws_feed else False

        # Données Gabagool
        if self.gabagool:
            positions = self.gabagool.get_all_positions()
            active = [p for p in positions if not p.is_locked]
            locked = [p for p in positions if p.is_locked]

            conditions.gabagool_active_positions = len(active)
            conditions.gabagool_locked_positions = len(locked)

            if active:
                conditions.gabagool_avg_pair_cost = sum(p.pair_cost for p in active) / len(active)

        # Données Smart Ape
        if self.smart_ape:
            positions = self.smart_ape.get_all_positions()
            conditions.smart_ape_active_rounds = len([p for p in positions if not p.is_closed])

        # Volatilité CoinGecko
        conditions.volatility_score = await self._get_volatility_score()

        # Prix BTC (Binance)
        conditions.btc = await self._get_btc_conditions()

        conditions.timestamp = datetime.now()
        return conditions

    async def _get_volatility_score(self) -> float:
        """Récupère le score de volatilité depuis CoinGecko."""
        try:
            if not self._cg_client_initialized:
                from api.public.coingecko_client import CoinGeckoClient
                self._cg_client = CoinGeckoClient()
                await self._cg_client.__aenter__()
                self._cg_client_initialized = True

            if self._cg_client:
                ranking = await self._cg_client.get_volatility_ranking()
                if ranking:
                    scores = [score for _, score in ranking]
                    return sum(scores) / len(scores) if scores else 50.0

        except Exception:
            pass

        return 50.0

    async def _get_btc_conditions(self) -> BTCConditions:
        """Récupère les conditions BTC depuis Binance."""
        btc = BTCConditions()

        try:
            if not self._http_client:
                return btc

            # Prix actuel BTC
            resp = await self._http_client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"}
            )

            if resp.status_code == 200:
                data = resp.json()
                btc.price = float(data["price"])
                btc.last_updated = datetime.now()

                # Ajouter à l'historique
                self._btc_history.append((datetime.now(), btc.price))

                # Garder seulement les 10 dernières minutes
                cutoff = datetime.now() - timedelta(minutes=10)
                self._btc_history = [(t, p) for t, p in self._btc_history if t > cutoff]

                # Calculer les variations
                if len(self._btc_history) >= 2:
                    # Prix il y a ~1 minute
                    one_min_ago = datetime.now() - timedelta(minutes=1)
                    prices_1m = [p for t, p in self._btc_history if t < one_min_ago]
                    if prices_1m:
                        btc.price_1m_ago = prices_1m[-1]
                        btc.change_1m_pct = ((btc.price - btc.price_1m_ago) / btc.price_1m_ago) * 100

                    # Prix il y a ~5 minutes
                    five_min_ago = datetime.now() - timedelta(minutes=5)
                    prices_5m = [p for t, p in self._btc_history if t < five_min_ago]
                    if prices_5m:
                        btc.price_5m_ago = prices_5m[-1]
                        btc.change_5m_pct = ((btc.price - btc.price_5m_ago) / btc.price_5m_ago) * 100

                    # Calculer volatilité 1 min (écart-type des variations)
                    recent = [p for t, p in self._btc_history if t > one_min_ago]
                    if len(recent) >= 3:
                        avg = sum(recent) / len(recent)
                        variance = sum((p - avg) ** 2 for p in recent) / len(recent)
                        btc.volatility_1m = (variance ** 0.5) / avg * 100

                    # Déterminer le momentum
                    if btc.change_1m_pct > 0.1:
                        btc.momentum = "up"
                    elif btc.change_1m_pct < -0.1:
                        btc.momentum = "down"
                    else:
                        btc.momentum = "neutral"

        except Exception as e:
            # Silencieux - pas critique
            pass

        return btc

    # ═══════════════════════════════════════════════════════════════
    # OPTIMISATION GABAGOOL
    # ═══════════════════════════════════════════════════════════════

    def _optimize_gabagool_params(self, conditions: MarketConditions) -> GabagoolParams:
        """Calcule les paramètres optimaux pour Gabagool."""
        params = GabagoolParams()

        # max_pair_cost selon spread et volatilité
        base_mpc = 0.975

        if conditions.avg_spread > 0.15:
            base_mpc = 0.965  # Gros spread = plus de marge possible
        elif conditions.avg_spread > 0.10:
            base_mpc = 0.970
        elif conditions.avg_spread < 0.05:
            base_mpc = 0.980  # Spread serré = accepter moins

        if conditions.volatility_score > 70:
            base_mpc -= 0.005  # Plus conservateur en haute vol
        elif conditions.volatility_score < 30:
            base_mpc += 0.005  # Plus agressif en basse vol

        params.max_pair_cost = max(0.950, min(0.985, base_mpc))

        # min_improvement selon état des positions
        if conditions.gabagool_active_positions == 0:
            params.min_improvement = 0.0
        elif conditions.gabagool_avg_pair_cost > 0.98:
            params.min_improvement = 0.001  # Besoin d'améliorer rapidement
        elif conditions.gabagool_avg_pair_cost > 0.96:
            params.min_improvement = 0.002
        elif conditions.gabagool_avg_pair_cost > 0.94:
            params.min_improvement = 0.005
        else:
            params.min_improvement = 0.008  # Déjà bon, être strict

        # first_buy_threshold selon spread
        base_fbt = 0.55
        if conditions.avg_spread > 0.12:
            base_fbt = 0.50  # Plus agressif
        elif conditions.avg_spread < 0.06:
            base_fbt = 0.60  # Plus conservateur

        if conditions.volatility_score > 70:
            base_fbt -= 0.05
        elif conditions.volatility_score < 30:
            base_fbt += 0.05

        params.first_buy_threshold = max(0.45, min(0.65, base_fbt))

        return params

    def _apply_gabagool_params(self, params: GabagoolParams) -> List[str]:
        """Applique les paramètres optimisés à Gabagool."""
        if not self.gabagool:
            return []

        changes = []
        config = self.gabagool.config
        THRESHOLD = 0.01

        # max_pair_cost
        if abs(config.max_pair_cost - params.max_pair_cost) / config.max_pair_cost > THRESHOLD:
            old = config.max_pair_cost
            config.max_pair_cost = params.max_pair_cost
            changes.append(f"max_pair_cost: {old:.3f} → {params.max_pair_cost:.3f}")
            self._log_event("gabagool", "max_pair_cost", old, params.max_pair_cost, "spread/volatility")

        # min_improvement
        min_imp_changed = False
        if config.min_improvement == 0 and params.min_improvement > 0:
            min_imp_changed = True
        elif config.min_improvement > 0 and params.min_improvement == 0:
            min_imp_changed = True
        elif config.min_improvement > 0 and abs(config.min_improvement - params.min_improvement) / config.min_improvement > THRESHOLD:
            min_imp_changed = True

        if min_imp_changed:
            old = config.min_improvement
            config.min_improvement = params.min_improvement
            changes.append(f"min_improvement: {old:.4f} → {params.min_improvement:.4f}")
            self._log_event("gabagool", "min_improvement", old, params.min_improvement, "position_state")

        if changes:
            self._total_adjustments += len(changes)

        return changes

    # ═══════════════════════════════════════════════════════════════
    # OPTIMISATION SMART APE
    # ═══════════════════════════════════════════════════════════════

    def _optimize_smart_ape_params(self, conditions: MarketConditions) -> SmartApeParams:
        """Calcule les paramètres optimaux pour Smart Ape."""
        params = SmartApeParams()
        btc = conditions.btc

        # window_minutes selon momentum BTC
        # Momentum fort = fenêtre courte (capturer vite)
        # Momentum faible = fenêtre large (attendre confirmation)
        if abs(btc.change_1m_pct) > 0.3:
            params.window_minutes = 1  # Mouvement fort, agir vite
        elif abs(btc.change_1m_pct) > 0.15:
            params.window_minutes = 2
        else:
            params.window_minutes = 3  # Calme, attendre

        # dump_threshold selon volatilité BTC
        # Haute volatilité = seuil plus élevé (éviter faux signaux)
        # Basse volatilité = seuil plus bas (signaux plus rares)
        if btc.volatility_1m > 0.5:
            params.dump_threshold = 0.20  # Volatil, exiger plus
        elif btc.volatility_1m > 0.2:
            params.dump_threshold = 0.15
        else:
            params.dump_threshold = 0.12  # Calme, seuil bas

        # min_payout_ratio selon volatilité globale
        if conditions.volatility_score > 70:
            params.min_payout_ratio = 1.7  # Risqué, exiger plus
        elif conditions.volatility_score > 50:
            params.min_payout_ratio = 1.5
        else:
            params.min_payout_ratio = 1.4  # Calme, accepter moins

        return params

    def _apply_smart_ape_params(self, params: SmartApeParams) -> List[str]:
        """Applique les paramètres optimisés à Smart Ape."""
        if not self.smart_ape:
            return []

        changes = []
        config = self.smart_ape.config

        # window_minutes
        if config.window_minutes != params.window_minutes:
            old = config.window_minutes
            config.window_minutes = params.window_minutes
            changes.append(f"window_minutes: {old} → {params.window_minutes}")
            self._log_event("smart_ape", "window_minutes", old, params.window_minutes, "btc_momentum")

        # dump_threshold (seuil 5%)
        if abs(config.dump_threshold - params.dump_threshold) / config.dump_threshold > 0.05:
            old = config.dump_threshold
            config.dump_threshold = params.dump_threshold
            changes.append(f"dump_threshold: {old:.2f} → {params.dump_threshold:.2f}")
            self._log_event("smart_ape", "dump_threshold", old, params.dump_threshold, "btc_volatility")

        # min_payout_ratio (seuil 5%)
        if abs(config.min_payout_ratio - params.min_payout_ratio) / config.min_payout_ratio > 0.05:
            old = config.min_payout_ratio
            config.min_payout_ratio = params.min_payout_ratio
            changes.append(f"min_payout_ratio: {old:.2f} → {params.min_payout_ratio:.2f}")
            self._log_event("smart_ape", "min_payout_ratio", old, params.min_payout_ratio, "volatility")

        if changes:
            self._total_adjustments += len(changes)

        return changes

    # ═══════════════════════════════════════════════════════════════
    # LOGGING
    # ═══════════════════════════════════════════════════════════════

    def _log_event(self, strategy: str, param: str, old: float, new: float, reason: str) -> None:
        """Enregistre un événement de modification."""
        event = OptimizationEvent(
            timestamp=datetime.now(),
            strategy=strategy,
            param_name=param,
            old_value=old,
            new_value=new,
            reason=reason
        )
        self._events.append(event)

        if len(self._events) > 100:
            self._events = self._events[-100:]

    # ═══════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """Retourne le status complet de l'optimiseur."""
        gabagool_current = {}
        if self.gabagool:
            config = self.gabagool.config
            gabagool_current = {
                "max_pair_cost": config.max_pair_cost,
                "min_improvement": config.min_improvement,
            }

        smart_ape_current = {}
        if self.smart_ape:
            config = self.smart_ape.config
            smart_ape_current = {
                "window_minutes": config.window_minutes,
                "dump_threshold": config.dump_threshold,
                "min_payout_ratio": config.min_payout_ratio,
            }

        conditions_dict = {}
        if self._conditions:
            conditions_dict = {
                "avg_spread": round(self._conditions.avg_spread, 4),
                "avg_volume": round(self._conditions.avg_volume, 0),
                "avg_liquidity": round(self._conditions.avg_liquidity, 0),
                "volatility_score": round(self._conditions.volatility_score, 1),
                "ws_connected": self._conditions.ws_connected,
                "gabagool_positions": self._conditions.gabagool_active_positions,
                "gabagool_avg_pair_cost": round(self._conditions.gabagool_avg_pair_cost, 4),
                "smart_ape_rounds": self._conditions.smart_ape_active_rounds,
                "btc_price": round(self._conditions.btc.price, 2),
                "btc_change_1m": round(self._conditions.btc.change_1m_pct, 3),
                "btc_momentum": self._conditions.btc.momentum,
            }

        return {
            "enabled": self._enabled,
            "mode": self.mode.value,
            "running": self._running,
            "optimize_gabagool": self._optimize_gabagool,
            "optimize_smart_ape": self._optimize_smart_ape,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "total_adjustments": self._total_adjustments,
            "gabagool": {
                "current": gabagool_current,
                "optimized": self._gabagool_params.to_dict(),
            },
            "smart_ape": {
                "current": smart_ape_current,
                "optimized": self._smart_ape_params.to_dict(),
            },
            "conditions": conditions_dict,
            "recent_events": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "strategy": e.strategy,
                    "param": e.param_name,
                    "old": e.old_value,
                    "new": e.new_value,
                    "reason": e.reason
                }
                for e in self.recent_events
            ]
        }

    def get_suggestions(self) -> dict:
        """Retourne les suggestions de paramètres (mode SEMI_AUTO)."""
        suggestions = {
            "gabagool": [],
            "smart_ape": [],
            "conditions": {}
        }

        if not self._conditions:
            return suggestions

        # Gabagool suggestions
        if self.gabagool:
            config = self.gabagool.config
            optimized = self._gabagool_params

            if abs(config.max_pair_cost - optimized.max_pair_cost) / config.max_pair_cost > 0.01:
                suggestions["gabagool"].append({
                    "param": "max_pair_cost",
                    "current": config.max_pair_cost,
                    "suggested": optimized.max_pair_cost,
                    "reason": "spread/volatility"
                })

        # Smart Ape suggestions
        if self.smart_ape:
            config = self.smart_ape.config
            optimized = self._smart_ape_params

            if config.window_minutes != optimized.window_minutes:
                suggestions["smart_ape"].append({
                    "param": "window_minutes",
                    "current": config.window_minutes,
                    "suggested": optimized.window_minutes,
                    "reason": "btc_momentum"
                })

            if abs(config.dump_threshold - optimized.dump_threshold) > 0.02:
                suggestions["smart_ape"].append({
                    "param": "dump_threshold",
                    "current": config.dump_threshold,
                    "suggested": optimized.dump_threshold,
                    "reason": "btc_volatility"
                })

        suggestions["conditions"] = {
            "btc_price": round(self._conditions.btc.price, 2),
            "btc_momentum": self._conditions.btc.momentum,
            "volatility": round(self._conditions.volatility_score, 1),
        }

        return suggestions
