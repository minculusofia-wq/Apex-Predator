"""
Lifecycle - Gestion du cycle de vie du bot

Inclut:
- Graceful Shutdown (arrêt propre)
- Health Check
- Métriques internes
"""

import asyncio
import signal
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any
from core.logger import get_logger


# ═══════════════════════════════════════════════════════════════════════════
# MÉTRIQUES INTERNES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class BotMetrics:
    """Métriques du bot pour monitoring."""

    # Trades
    trades_executed: int = 0
    trades_success: int = 0
    trades_failed: int = 0
    trades_rejected: int = 0  # Validation failed

    # Profit
    total_profit_usd: float = 0.0
    total_volume_usd: float = 0.0

    # Latence
    total_latency_ms: float = 0.0
    latency_samples: int = 0

    # Positions
    positions_opened: int = 0
    positions_closed: int = 0
    positions_locked: int = 0  # Profit garanti

    # Erreurs
    errors_count: int = 0
    circuit_breaks: int = 0

    # Uptime
    start_time: str = ""
    last_updated: str = ""

    @property
    def avg_latency_ms(self) -> float:
        if self.latency_samples == 0:
            return 0.0
        return self.total_latency_ms / self.latency_samples

    @property
    def success_rate(self) -> float:
        if self.trades_executed == 0:
            return 0.0
        return (self.trades_success / self.trades_executed) * 100

    @property
    def uptime_seconds(self) -> float:
        if not self.start_time:
            return 0.0
        try:
            # Parse ISO format avec ou sans timezone
            start_str = self.start_time.replace("Z", "+00:00")
            start = datetime.fromisoformat(start_str)
            # Comparer en UTC naive pour éviter les problèmes de timezone
            now = datetime.utcnow()
            start_naive = start.replace(tzinfo=None)
            return (now - start_naive).total_seconds()
        except Exception:
            return 0.0

    def to_dict(self) -> dict:
        """Export en dict avec propriétés calculées."""
        data = asdict(self)
        data["avg_latency_ms"] = round(self.avg_latency_ms, 2)
        data["success_rate"] = round(self.success_rate, 2)
        data["uptime_hours"] = round(self.uptime_seconds / 3600, 2)
        return data


class MetricsManager:
    """Gestionnaire de métriques avec persistance."""

    _instance: Optional["MetricsManager"] = None
    METRICS_FILE = Path("data/metrics.json")

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._metrics = BotMetrics()
        self._lock = asyncio.Lock()
        self._logger = get_logger()

        # Créer le dossier data si nécessaire
        self.METRICS_FILE.parent.mkdir(exist_ok=True)

        # Charger métriques existantes ou initialiser
        self._load()
        self._metrics.start_time = datetime.utcnow().isoformat() + "Z"

    def _load(self):
        """Charge les métriques depuis le fichier."""
        if self.METRICS_FILE.exists():
            try:
                with open(self.METRICS_FILE, "r") as f:
                    data = json.load(f)
                    # Restaurer les compteurs cumulatifs
                    self._metrics.trades_executed = data.get("trades_executed", 0)
                    self._metrics.trades_success = data.get("trades_success", 0)
                    self._metrics.trades_failed = data.get("trades_failed", 0)
                    self._metrics.total_profit_usd = data.get("total_profit_usd", 0.0)
                    self._metrics.total_volume_usd = data.get("total_volume_usd", 0.0)
                    self._metrics.positions_locked = data.get("positions_locked", 0)
                    self._logger.info("Metrics restored from file")
            except Exception as e:
                self._logger.warning(f"Could not load metrics: {e}")

    def save(self):
        """Sauvegarde les métriques dans le fichier."""
        self._metrics.last_updated = datetime.utcnow().isoformat() + "Z"
        try:
            with open(self.METRICS_FILE, "w") as f:
                json.dump(self._metrics.to_dict(), f, indent=2)
        except Exception as e:
            self._logger.error(f"Could not save metrics: {e}")

    async def record_trade(self, success: bool, volume_usd: float = 0.0, profit_usd: float = 0.0):
        """Enregistre un trade."""
        async with self._lock:
            self._metrics.trades_executed += 1
            if success:
                self._metrics.trades_success += 1
                self._metrics.total_volume_usd += volume_usd
                self._metrics.total_profit_usd += profit_usd
            else:
                self._metrics.trades_failed += 1
            self.save()

    async def record_latency(self, latency_ms: float):
        """Enregistre une latence."""
        async with self._lock:
            self._metrics.total_latency_ms += latency_ms
            self._metrics.latency_samples += 1

    async def record_error(self):
        """Enregistre une erreur."""
        async with self._lock:
            self._metrics.errors_count += 1

    async def record_circuit_break(self):
        """Enregistre une ouverture de circuit."""
        async with self._lock:
            self._metrics.circuit_breaks += 1

    async def record_position_locked(self, profit_usd: float):
        """Enregistre une position verrouillée (profit garanti)."""
        async with self._lock:
            self._metrics.positions_locked += 1
            self._metrics.total_profit_usd += profit_usd
            self.save()

    def get_metrics(self) -> dict:
        """Retourne les métriques actuelles."""
        return self._metrics.to_dict()


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ComponentHealth:
    """Santé d'un composant."""
    name: str
    status: str  # "healthy", "degraded", "unhealthy"
    message: str = ""
    last_check: str = ""


class HealthChecker:
    """Vérificateur de santé du système."""

    _instance: Optional["HealthChecker"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._components: Dict[str, Callable[[], ComponentHealth]] = {}
        self._logger = get_logger()

    def register_component(self, name: str, health_check: Callable[[], ComponentHealth]):
        """Enregistre un composant pour le health check."""
        self._components[name] = health_check
        self._logger.debug(f"Health check registered: {name}")

    def check_all(self) -> dict:
        """Vérifie la santé de tous les composants."""
        results = {}
        overall_status = "healthy"

        for name, check_fn in self._components.items():
            try:
                health = check_fn()
                results[name] = {
                    "status": health.status,
                    "message": health.message,
                    "last_check": datetime.utcnow().isoformat() + "Z"
                }
                if health.status == "unhealthy":
                    overall_status = "unhealthy"
                elif health.status == "degraded" and overall_status == "healthy":
                    overall_status = "degraded"
            except Exception as e:
                results[name] = {
                    "status": "unhealthy",
                    "message": str(e),
                    "last_check": datetime.utcnow().isoformat() + "Z"
                }
                overall_status = "unhealthy"

        # Ajouter métriques
        metrics = get_metrics_manager().get_metrics()

        return {
            "status": overall_status,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "components": results,
            "metrics_summary": {
                "trades_executed": metrics.get("trades_executed", 0),
                "success_rate": metrics.get("success_rate", 0),
                "avg_latency_ms": metrics.get("avg_latency_ms", 0),
                "uptime_hours": metrics.get("uptime_hours", 0),
            }
        }


# ═══════════════════════════════════════════════════════════════════════════
# GRACEFUL SHUTDOWN
# ═══════════════════════════════════════════════════════════════════════════

class GracefulShutdown:
    """Gestionnaire d'arrêt propre du bot."""

    _instance: Optional["GracefulShutdown"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._shutdown_requested = False
        self._shutdown_callbacks: List[Callable] = []
        self._logger = get_logger()

    def register_callback(self, callback: Callable):
        """Enregistre un callback à appeler lors du shutdown."""
        self._shutdown_callbacks.append(callback)
        self._logger.debug(f"Shutdown callback registered: {callback.__name__}")

    def is_shutdown_requested(self) -> bool:
        """Vérifie si un shutdown a été demandé."""
        return self._shutdown_requested

    def setup_signal_handlers(self, loop: asyncio.AbstractEventLoop):
        """Configure les handlers de signaux (SIGINT, SIGTERM)."""

        def signal_handler(sig):
            self._logger.warning(f"Received signal {sig.name}, initiating graceful shutdown...")
            self._shutdown_requested = True
            asyncio.create_task(self._execute_shutdown())

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
            self._logger.info("Signal handlers configured for graceful shutdown")
        except NotImplementedError:
            # Windows ne supporte pas add_signal_handler
            self._logger.warning("Signal handlers not supported on this platform")

    async def _execute_shutdown(self):
        """Exécute les callbacks de shutdown."""
        self._logger.info(f"Executing {len(self._shutdown_callbacks)} shutdown callbacks...")

        for callback in self._shutdown_callbacks:
            try:
                self._logger.info(f"Running shutdown callback: {callback.__name__}")
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                self._logger.error(f"Shutdown callback {callback.__name__} failed: {e}")

        # Sauvegarder les métriques
        get_metrics_manager().save()
        self._logger.info("Graceful shutdown complete")

    async def shutdown(self):
        """Déclenche manuellement le shutdown."""
        self._shutdown_requested = True
        await self._execute_shutdown()


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCES GLOBALES
# ═══════════════════════════════════════════════════════════════════════════

_metrics_manager: Optional[MetricsManager] = None
_health_checker: Optional[HealthChecker] = None
_graceful_shutdown: Optional[GracefulShutdown] = None


def get_metrics_manager() -> MetricsManager:
    """Retourne le gestionnaire de métriques singleton."""
    global _metrics_manager
    if _metrics_manager is None:
        _metrics_manager = MetricsManager()
    return _metrics_manager


def get_health_checker() -> HealthChecker:
    """Retourne le health checker singleton."""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


def get_graceful_shutdown() -> GracefulShutdown:
    """Retourne le gestionnaire de shutdown singleton."""
    global _graceful_shutdown
    if _graceful_shutdown is None:
        _graceful_shutdown = GracefulShutdown()
    return _graceful_shutdown
