"""
Auto-Optimizer - Optimisation Dynamique des Paramètres de Trading

Ajuste automatiquement les paramètres en temps réel basé sur:
- Conditions de marché (spread, volume, liquidité)
- État des positions (progress vers lock, équilibre YES/NO)
- Volatilité externe (données Binance/CoinGecko)

Modes:
- MANUAL: Paramètres fixes
- SEMI_AUTO: Suggestions avec confirmation
- FULL_AUTO: Ajustement automatique
"""

import asyncio
from typing import Optional, Dict, List, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

if TYPE_CHECKING:
    from core.scanner import Scanner, MarketData
    from core.gabagool import GabagoolEngine, PairPosition
    from api.public.coingecko_client import CoinGeckoClient


class OptimizerMode(Enum):
    """Mode de fonctionnement de l'optimiseur."""
    MANUAL = "manual"           # Paramètres fixes
    SEMI_AUTO = "semi_auto"     # Suggestions avec confirmation
    FULL_AUTO = "full_auto"     # Ajustement automatique


@dataclass
class MarketConditions:
    """Snapshot des conditions de marché actuelles."""
    avg_spread: float = 0.10            # Spread moyen sur les marchés actifs
    avg_volume: float = 20000.0         # Volume moyen 24h
    avg_liquidity: float = 10000.0      # Liquidité moyenne
    volatility_score: float = 50.0      # Score volatilité (0-100)
    active_positions: int = 0           # Nombre de positions ouvertes
    locked_positions: int = 0           # Positions avec profit verrouillé
    avg_pair_cost: float = 1.0          # Pair cost moyen des positions actives
    ws_connected: bool = False          # WebSocket actif
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class OptimizedParams:
    """Paramètres optimisés calculés."""
    max_pair_cost: float = 0.98         # 0.90 - 0.99 selon conditions
    min_improvement: float = 0.005      # 0.000 - 0.010 selon état position
    first_buy_threshold: float = 0.55   # 0.45 - 0.65 selon spread
    refresh_interval: float = 1.0       # 0.5 - 2.0s selon volatilité

    def to_dict(self) -> dict:
        """Convertit en dictionnaire."""
        return {
            "max_pair_cost": round(self.max_pair_cost, 3),
            "min_improvement": round(self.min_improvement, 4),
            "first_buy_threshold": round(self.first_buy_threshold, 3),
            "refresh_interval": round(self.refresh_interval, 2),
        }


@dataclass
class OptimizationEvent:
    """Événement de modification de paramètres."""
    timestamp: datetime
    param_name: str
    old_value: float
    new_value: float
    reason: str


class AutoOptimizer:
    """
    Moteur d'optimisation automatique des paramètres.

    Ajuste les paramètres en temps réel pour maximiser:
    1. Le nombre d'opportunités détectées
    2. La vitesse de convergence vers pair_cost < $1
    3. Le profit verrouillé par position

    Usage:
        optimizer = AutoOptimizer(scanner, gabagool)
        await optimizer.start()
    """

    def __init__(
        self,
        scanner: Optional["Scanner"] = None,
        gabagool: Optional["GabagoolEngine"] = None,
        mode: OptimizerMode = OptimizerMode.FULL_AUTO
    ):
        self.scanner = scanner
        self.gabagool = gabagool
        self.mode = mode
        self._enabled = True
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Intervalle de mise à jour (secondes)
        self._update_interval = 5.0

        # État actuel
        self._conditions: Optional[MarketConditions] = None
        self._current_params: OptimizedParams = OptimizedParams()
        self._last_update: Optional[datetime] = None

        # Historique des modifications
        self._events: List[OptimizationEvent] = []
        self._total_adjustments = 0

        # Paramètres de base (référence)
        self._base_params = OptimizedParams()

        # Client CoinGecko persistent (évite rate limit)
        self._cg_client: Optional["CoinGeckoClient"] = None
        self._cg_client_initialized = False

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
    def current_params(self) -> OptimizedParams:
        return self._current_params

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
        self._task = asyncio.create_task(self._optimization_loop())
        print(f"🧠 [Optimizer] Démarré en mode {self.mode.value}")

    async def stop(self) -> None:
        """Arrête la boucle d'optimisation."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Fermer le client CoinGecko
        if self._cg_client:
            try:
                await self._cg_client.__aexit__(None, None, None)
            except Exception:
                pass
            self._cg_client = None
            self._cg_client_initialized = False

        print("🧠 [Optimizer] Arrêté")

    def set_mode(self, mode: OptimizerMode) -> None:
        """Change le mode de fonctionnement."""
        old_mode = self.mode
        self.mode = mode
        print(f"🧠 [Optimizer] Mode changé: {old_mode.value} → {mode.value}")

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

                    # 2. Calculer les paramètres optimaux
                    optimized = self._compute_optimal_params(self._conditions)

                    # 3. Appliquer si changement significatif
                    if self.mode == OptimizerMode.FULL_AUTO:
                        changes = self._apply_params(optimized)
                        if changes:
                            self._last_update = datetime.now()

                    # 4. Stocker pour mode SEMI_AUTO (suggestions)
                    self._current_params = optimized

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

        # Données du scanner
        if self.scanner:
            markets = list(self.scanner.markets.values())

            if markets:
                # Spread moyen
                spreads = [m.effective_spread for m in markets if m.is_valid and m.effective_spread > 0]
                if spreads:
                    conditions.avg_spread = sum(spreads) / len(spreads)

                # Volume moyen
                volumes = [m.market.volume for m in markets if m.market.volume > 0]
                if volumes:
                    conditions.avg_volume = sum(volumes) / len(volumes)

                # Liquidité moyenne
                liquidities = [m.market.liquidity for m in markets if m.market.liquidity > 0]
                if liquidities:
                    conditions.avg_liquidity = sum(liquidities) / len(liquidities)

            # WebSocket status
            conditions.ws_connected = self.scanner._ws_feed.is_connected if self.scanner._ws_feed else False

        # Données Gabagool
        if self.gabagool:
            positions = self.gabagool.get_all_positions()
            active = [p for p in positions if not p.is_locked]
            locked = [p for p in positions if p.is_locked]

            conditions.active_positions = len(active)
            conditions.locked_positions = len(locked)

            # Pair cost moyen des positions actives
            if active:
                conditions.avg_pair_cost = sum(p.pair_cost for p in active) / len(active)

        # Volatilité externe (CoinGecko)
        conditions.volatility_score = await self._get_volatility_score()

        conditions.timestamp = datetime.now()
        return conditions

    async def _get_volatility_score(self) -> float:
        """Récupère le score de volatilité depuis CoinGecko (client persistent)."""
        try:
            # Initialiser le client une seule fois
            if not self._cg_client_initialized:
                from api.public.coingecko_client import CoinGeckoClient
                self._cg_client = CoinGeckoClient()
                await self._cg_client.__aenter__()
                self._cg_client_initialized = True

            if self._cg_client:
                ranking = await self._cg_client.get_volatility_ranking()
                if ranking:
                    # Moyenne des scores de volatilité
                    scores = [score for _, score in ranking]
                    return sum(scores) / len(scores) if scores else 50.0

        except Exception as e:
            # Ne pas spammer les logs - juste retourner la valeur par défaut
            pass

        return 50.0  # Valeur par défaut

    # ═══════════════════════════════════════════════════════════════
    # CALCUL DES PARAMÈTRES OPTIMAUX
    # ═══════════════════════════════════════════════════════════════

    def _compute_optimal_params(self, conditions: MarketConditions) -> OptimizedParams:
        """Calcule les paramètres optimaux basés sur les conditions."""
        params = OptimizedParams()

        params.max_pair_cost = self._optimize_max_pair_cost(conditions)
        params.min_improvement = self._optimize_min_improvement(conditions)
        params.max_pair_cost = self._optimize_max_pair_cost(conditions)
        params.min_improvement = self._optimize_min_improvement(conditions)
        # params.order_size_usd removed
        # params.max_position_usd removed
        params.first_buy_threshold = self._optimize_first_buy_threshold(conditions)
        params.refresh_interval = self._optimize_refresh_interval(conditions)

        return params

    def _optimize_max_pair_cost(self, conditions: MarketConditions) -> float:
        """
        Optimise max_pair_cost selon le spread et la volatilité.

        - Gros spread → accepter plus de marge (0.92)
        - Spread serré → accepter moins (0.98)
        - Haute volatilité → plus conservateur (-0.02)
        """
        base = 0.95

        # Ajuster selon spread
        if conditions.avg_spread > 0.15:
            base = 0.92  # Gros spread = plus de marge possible
        elif conditions.avg_spread > 0.10:
            base = 0.94
        elif conditions.avg_spread < 0.06:
            base = 0.98  # Spread serré = accepter moins

        # Ajuster selon volatilité
        if conditions.volatility_score > 70:
            base -= 0.02  # Plus conservateur en haute vol
        elif conditions.volatility_score < 30:
            base += 0.01  # Plus agressif en basse vol

        return max(0.90, min(0.99, base))

    def _optimize_min_improvement(self, conditions: MarketConditions) -> float:
        """
        Optimise min_improvement selon l'état des positions.

        - Positions nouvelles → pas de seuil (0.000)
        - pair_cost élevé → flexible (0.002)
        - pair_cost bas → strict (0.010)
        """
        # Si pas de positions actives, être ouvert
        if conditions.active_positions == 0:
            return 0.0

        # Selon le pair_cost moyen
        if conditions.avg_pair_cost > 0.98:
            return 0.001  # Besoin d'améliorer rapidement
        elif conditions.avg_pair_cost > 0.96:
            return 0.002
        elif conditions.avg_pair_cost > 0.94:
            return 0.005  # Standard
        else:
            return 0.008  # Déjà bon, être strict

    # _optimize_order_size removed (Capital controlled by User/Kelly)
    # _optimize_max_position removed (Capital controlled by User)

    def _optimize_first_buy_threshold(self, conditions: MarketConditions) -> float:
        """
        Optimise first_buy_threshold selon le spread et la volatilité.

        - Gros spread → plus agressif (0.50)
        - Haute volatilité → plus agressif (0.50)
        - Normal → équilibré (0.55)
        """
        base = 0.55

        # Plus agressif avec gros spread
        if conditions.avg_spread > 0.12:
            base = 0.50
        elif conditions.avg_spread < 0.06:
            base = 0.60  # Plus conservateur avec spread serré

        # Plus agressif en haute volatilité
        if conditions.volatility_score > 70:
            base -= 0.05
        elif conditions.volatility_score < 30:
            base += 0.05

        return max(0.45, min(0.65, base))

    def _optimize_refresh_interval(self, conditions: MarketConditions) -> float:
        """
        Optimise refresh_interval selon les conditions.

        - WebSocket actif → plus lent (données temps réel)
        - Haute volatilité → plus rapide
        - Beaucoup de positions → plus rapide
        """
        base = 1.0

        # WebSocket actif = moins besoin de polling
        if conditions.ws_connected:
            base = 1.5

        # Plus rapide en haute volatilité
        if conditions.volatility_score > 70:
            base = 0.5
        elif conditions.volatility_score > 50:
            base = min(base, 1.0)

        # Plus rapide si positions actives
        if conditions.active_positions > 3:
            base = min(base, 0.5)

        return max(0.5, min(2.0, base))

    # ═══════════════════════════════════════════════════════════════
    # APPLICATION DES PARAMÈTRES
    # ═══════════════════════════════════════════════════════════════

    def _apply_params(self, params: OptimizedParams) -> List[str]:
        """
        Applique les paramètres optimisés au GabagoolEngine.

        Retourne la liste des paramètres modifiés.
        """
        if not self.gabagool:
            return []

        changes = []
        config = self.gabagool.config

        # Seuil de changement significatif
        THRESHOLD = 0.01  # 1% de changement

        # max_pair_cost
        if abs(config.max_pair_cost - params.max_pair_cost) / config.max_pair_cost > THRESHOLD:
            old = config.max_pair_cost
            config.max_pair_cost = params.max_pair_cost
            changes.append(f"max_pair_cost: {old:.3f} → {params.max_pair_cost:.3f}")
            self._log_event("max_pair_cost", old, params.max_pair_cost, "spread/volatility")

        # min_improvement - vérifier qu'il y a un changement réel
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
            self._log_event("min_improvement", old, params.min_improvement, "position_state")

        # order_size_usd REMOVED (Manual/Kelly Only)
        # max_position_usd REMOVED (Manual Only)
        if hasattr(config, 'first_buy_threshold'):
            if abs(config.first_buy_threshold - params.first_buy_threshold) / config.first_buy_threshold > THRESHOLD:
                old = config.first_buy_threshold
                config.first_buy_threshold = params.first_buy_threshold
                changes.append(f"first_buy_threshold: {old:.3f} → {params.first_buy_threshold:.3f}")
                self._log_event("first_buy_threshold", old, params.first_buy_threshold, "spread")

        # Log si changements
        if changes:
            self._total_adjustments += len(changes)
            print(f"⚡ [Optimizer] Paramètres mis à jour:")
            for change in changes:
                print(f"   • {change}")

            # Callback
            if self.on_params_updated:
                self.on_params_updated(params, changes)

        return changes

    def _log_event(self, param: str, old: float, new: float, reason: str) -> None:
        """Enregistre un événement de modification."""
        event = OptimizationEvent(
            timestamp=datetime.now(),
            param_name=param,
            old_value=old,
            new_value=new,
            reason=reason
        )
        self._events.append(event)

        # Garder seulement les 100 derniers événements
        if len(self._events) > 100:
            self._events = self._events[-100:]

    # ═══════════════════════════════════════════════════════════════
    # SUGGESTIONS (MODE SEMI-AUTO)
    # ═══════════════════════════════════════════════════════════════

    def get_suggestions(self) -> dict:
        """
        Retourne les suggestions de paramètres (pour mode SEMI_AUTO).

        Utilisé par le dashboard pour afficher les recommandations.
        """
        if not self._conditions:
            return {}

        current = {}
        if self.gabagool:
            config = self.gabagool.config
            current = {
                "max_pair_cost": config.max_pair_cost,
                "min_improvement": config.min_improvement,
            }

        suggested = self._current_params.to_dict()

        # Calculer les différences
        suggestions = []
        for key in suggested:
            if key in current:
                diff = ((suggested[key] - current[key]) / current[key]) * 100 if current[key] != 0 else 0
                if abs(diff) > 1:  # Plus de 1% de différence
                    suggestions.append({
                        "param": key,
                        "current": current[key],
                        "suggested": suggested[key],
                        "change_pct": round(diff, 1),
                        "direction": "↑" if diff > 0 else "↓"
                    })

        return {
            "conditions": {
                "avg_spread": round(self._conditions.avg_spread, 4),
                "avg_liquidity": round(self._conditions.avg_liquidity, 0),
                "volatility_score": round(self._conditions.volatility_score, 1),
                "active_positions": self._conditions.active_positions,
                "ws_connected": self._conditions.ws_connected,
            },
            "suggestions": suggestions,
            "timestamp": self._conditions.timestamp.isoformat()
        }

    def apply_suggestion(self, param_name: str) -> bool:
        """Applique une suggestion spécifique."""
        if not self.gabagool or not hasattr(self._current_params, param_name):
            return False

        new_value = getattr(self._current_params, param_name)
        config = self.gabagool.config

        if hasattr(config, param_name):
            old_value = getattr(config, param_name)
            setattr(config, param_name, new_value)
            self._log_event(param_name, old_value, new_value, "manual_apply")
            self._total_adjustments += 1
            print(f"✅ [Optimizer] Suggestion appliquée: {param_name} = {new_value}")
            return True

        return False

    # ═══════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """Retourne le status complet de l'optimiseur."""
        current_params = {}
        if self.gabagool:
            config = self.gabagool.config
            current_params = {
                "max_pair_cost": config.max_pair_cost,
                "min_improvement": config.min_improvement,
            }
            if hasattr(config, 'first_buy_threshold'):
                current_params["first_buy_threshold"] = config.first_buy_threshold

        conditions_dict = {}
        if self._conditions:
            conditions_dict = {
                "avg_spread": round(self._conditions.avg_spread, 4),
                "avg_volume": round(self._conditions.avg_volume, 0),
                "avg_liquidity": round(self._conditions.avg_liquidity, 0),
                "volatility_score": round(self._conditions.volatility_score, 1),
                "active_positions": self._conditions.active_positions,
                "locked_positions": self._conditions.locked_positions,
                "avg_pair_cost": round(self._conditions.avg_pair_cost, 4),
                "ws_connected": self._conditions.ws_connected,
            }

        return {
            "enabled": self._enabled,
            "mode": self.mode.value,
            "running": self._running,
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "total_adjustments": self._total_adjustments,
            "current_params": current_params,
            "optimized_params": self._current_params.to_dict(),
            "conditions": conditions_dict,
            "recent_events": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "param": e.param_name,
                    "old": e.old_value,
                    "new": e.new_value,
                    "reason": e.reason
                }
                for e in self.recent_events
            ]
        }
