"""
Daily Loss Manager - Protection contre la ruine (v7.3)

Fonctionnalités:
1. Tracker le PnL journalier en temps réel
2. Bloquer le trading si limite atteinte
3. Réduire les tailles de position après pertes
4. Reset automatique à minuit UTC
5. Alertes avant d'atteindre la limite
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, List
from enum import Enum


class DailyLossStatus(Enum):
    """États du gestionnaire de pertes journalières."""
    NORMAL = "normal"           # Trading normal
    WARNING = "warning"         # Proche de la limite (70%+)
    REDUCED = "reduced"         # Tailles réduites (50%+ de la limite)
    BLOCKED = "blocked"         # Limite atteinte, trading bloqué


@dataclass
class DailyStats:
    """Statistiques journalières."""
    date: str                           # Format YYYY-MM-DD
    starting_balance: float = 0.0       # Balance au début de la journée
    current_pnl: float = 0.0            # PnL réalisé aujourd'hui
    unrealized_pnl: float = 0.0         # PnL non réalisé (positions ouvertes)
    trades_count: int = 0               # Nombre de trades
    winning_trades: int = 0             # Trades gagnants
    losing_trades: int = 0              # Trades perdants
    largest_win: float = 0.0            # Plus gros gain
    largest_loss: float = 0.0           # Plus grosse perte
    blocked_at: Optional[str] = None    # Heure du blocage si limite atteinte

    @property
    def total_pnl(self) -> float:
        """PnL total (réalisé + non réalisé)."""
        return self.current_pnl + self.unrealized_pnl

    @property
    def win_rate(self) -> float:
        """Taux de victoire."""
        if self.trades_count == 0:
            return 0.0
        return (self.winning_trades / self.trades_count) * 100

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DailyStats":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class DailyLossManager:
    """
    Gestionnaire des pertes journalières.

    Protège contre la ruine en:
    - Bloquant le trading après une perte définie
    - Réduisant les tailles de position après des pertes
    - Alertant l'utilisateur à l'approche de la limite
    """

    def __init__(
        self,
        max_daily_loss_usd: float = 100.0,
        max_daily_loss_percent: float = 10.0,
        total_capital: float = 1000.0,
        reset_hour_utc: int = 0,
        warning_threshold: float = 0.7,
        reduction_threshold: float = 0.5,
        persistence_file: str = "data/daily_stats.json"
    ):
        self.max_daily_loss_usd = max_daily_loss_usd
        self.max_daily_loss_percent = max_daily_loss_percent
        self.total_capital = total_capital
        self.reset_hour_utc = reset_hour_utc
        self.warning_threshold = warning_threshold
        self.reduction_threshold = reduction_threshold
        self._persistence_path = Path(persistence_file)

        # État
        self._stats: DailyStats = DailyStats(date=self._get_today_str())
        self._status: DailyLossStatus = DailyLossStatus.NORMAL
        self._is_running = False
        self._reset_task: Optional[asyncio.Task] = None

        # Callbacks
        self.on_status_change: Optional[Callable[[DailyLossStatus], None]] = None
        self.on_warning: Optional[Callable[[float, float], None]] = None  # (current_loss, limit)
        self.on_blocked: Optional[Callable[[float], None]] = None  # (total_loss)
        self.on_trade_blocked: Optional[Callable[[str], None]] = None  # (reason)

        # Historique
        self._history: List[DailyStats] = []

        # Lock pour thread-safety
        self._lock = asyncio.Lock()

    def _get_today_str(self) -> str:
        """Retourne la date du jour en format YYYY-MM-DD."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_effective_limit(self) -> float:
        """
        Retourne la limite effective (le minimum entre USD et %).
        """
        percent_limit = (self.max_daily_loss_percent / 100) * self.total_capital
        return min(self.max_daily_loss_usd, percent_limit)

    @property
    def status(self) -> DailyLossStatus:
        return self._status

    @property
    def stats(self) -> DailyStats:
        return self._stats

    @property
    def current_loss(self) -> float:
        """Perte actuelle (valeur positive si perte)."""
        return -self._stats.total_pnl if self._stats.total_pnl < 0 else 0.0

    @property
    def loss_percentage(self) -> float:
        """Pourcentage de la limite atteint."""
        limit = self._get_effective_limit()
        if limit <= 0:
            return 0.0
        return (self.current_loss / limit) * 100

    @property
    def remaining_loss_budget(self) -> float:
        """Budget de perte restant avant blocage."""
        return max(0, self._get_effective_limit() - self.current_loss)

    @property
    def can_trade(self) -> tuple[bool, str]:
        """Vérifie si le trading est autorisé."""
        if self._status == DailyLossStatus.BLOCKED:
            return False, f"Limite de perte journalière atteinte (${self.current_loss:.2f}/${self._get_effective_limit():.2f})"
        return True, ""

    @property
    def position_size_multiplier(self) -> float:
        """
        Multiplicateur de taille de position basé sur les pertes.

        Returns:
            1.0 = normal, 0.5 = réduit de moitié, etc.
        """
        if self._status == DailyLossStatus.BLOCKED:
            return 0.0
        elif self._status == DailyLossStatus.REDUCED:
            # Réduire proportionnellement à la perte
            # Plus on perd, plus on réduit
            loss_ratio = self.current_loss / self._get_effective_limit()
            # De 0.5 (à 50% de la limite) à 0.25 (à 90% de la limite)
            return max(0.25, 1.0 - loss_ratio)
        elif self._status == DailyLossStatus.WARNING:
            return 0.75  # Réduction légère en mode warning
        return 1.0

    async def start(self, starting_balance: Optional[float] = None):
        """Démarre le gestionnaire."""
        async with self._lock:
            self._load_stats()

            # Vérifier si c'est un nouveau jour
            today = self._get_today_str()
            if self._stats.date != today:
                # Archiver les stats d'hier
                if self._stats.trades_count > 0:
                    self._history.append(self._stats)
                # Créer nouvelles stats
                self._stats = DailyStats(date=today)

            # Mettre à jour la balance de départ
            if starting_balance is not None:
                self._stats.starting_balance = starting_balance
                self.total_capital = starting_balance

            self._is_running = True
            self._update_status()

            # Démarrer la tâche de reset automatique
            self._reset_task = asyncio.create_task(self._reset_loop())

            print(f"📊 Daily Loss Manager démarré. Limite: ${self._get_effective_limit():.2f}/jour")

    async def stop(self):
        """Arrête le gestionnaire."""
        async with self._lock:
            self._is_running = False

            if self._reset_task:
                self._reset_task.cancel()
                try:
                    await self._reset_task
                except asyncio.CancelledError:
                    pass
                self._reset_task = None

            self._save_stats()
            print("📊 Daily Loss Manager arrêté.")

    async def record_trade(
        self,
        pnl: float,
        trade_type: str = "unknown",
        market_id: str = ""
    ):
        """
        Enregistre le résultat d'un trade.

        Args:
            pnl: Profit/perte du trade (positif = gain, négatif = perte)
            trade_type: Type de trade (gabagool, smart_ape, etc.)
            market_id: ID du marché
        """
        async with self._lock:
            self._stats.current_pnl += pnl
            self._stats.trades_count += 1

            if pnl >= 0:
                self._stats.winning_trades += 1
                if pnl > self._stats.largest_win:
                    self._stats.largest_win = pnl
            else:
                self._stats.losing_trades += 1
                if abs(pnl) > abs(self._stats.largest_loss):
                    self._stats.largest_loss = pnl

            # Mettre à jour le statut
            previous_status = self._status
            self._update_status()

            # Sauvegarder
            self._save_stats()

            # Log
            status_emoji = {
                DailyLossStatus.NORMAL: "✅",
                DailyLossStatus.WARNING: "⚠️",
                DailyLossStatus.REDUCED: "📉",
                DailyLossStatus.BLOCKED: "🛑"
            }
            print(f"{status_emoji[self._status]} Trade enregistré: {'+' if pnl >= 0 else ''}{pnl:.2f}$ | "
                  f"PnL jour: {self._stats.current_pnl:+.2f}$ | "
                  f"Limite: {self.loss_percentage:.1f}%")

            # Callbacks si changement de statut
            if self._status != previous_status:
                if self.on_status_change:
                    self.on_status_change(self._status)

                if self._status == DailyLossStatus.WARNING and self.on_warning:
                    self.on_warning(self.current_loss, self._get_effective_limit())
                elif self._status == DailyLossStatus.BLOCKED and self.on_blocked:
                    self._stats.blocked_at = datetime.now(timezone.utc).isoformat()
                    self.on_blocked(self.current_loss)

    async def update_unrealized_pnl(self, unrealized: float):
        """Met à jour le PnL non réalisé (positions ouvertes)."""
        async with self._lock:
            self._stats.unrealized_pnl = unrealized
            self._update_status()

    def _update_status(self):
        """Met à jour le statut basé sur les pertes."""
        loss_ratio = self.current_loss / self._get_effective_limit() if self._get_effective_limit() > 0 else 0

        if loss_ratio >= 1.0:
            self._status = DailyLossStatus.BLOCKED
        elif loss_ratio >= self.warning_threshold:
            self._status = DailyLossStatus.WARNING
        elif loss_ratio >= self.reduction_threshold:
            self._status = DailyLossStatus.REDUCED
        else:
            self._status = DailyLossStatus.NORMAL

    async def _reset_loop(self):
        """Boucle de reset automatique à minuit UTC."""
        while self._is_running:
            try:
                # Calculer le temps jusqu'au prochain reset
                now = datetime.now(timezone.utc)
                next_reset = now.replace(
                    hour=self.reset_hour_utc,
                    minute=0,
                    second=0,
                    microsecond=0
                )
                if next_reset <= now:
                    next_reset = next_reset.replace(day=next_reset.day + 1)

                wait_seconds = (next_reset - now).total_seconds()

                # Attendre jusqu'au reset
                await asyncio.sleep(wait_seconds)

                # Reset
                async with self._lock:
                    # Archiver les stats du jour
                    if self._stats.trades_count > 0:
                        self._history.append(self._stats)

                    # Créer nouvelles stats
                    self._stats = DailyStats(
                        date=self._get_today_str(),
                        starting_balance=self.total_capital
                    )
                    self._status = DailyLossStatus.NORMAL

                    self._save_stats()
                    print("🔄 Daily Loss Manager: Reset journalier effectué.")

                    if self.on_status_change:
                        self.on_status_change(self._status)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Erreur dans reset loop: {e}")
                await asyncio.sleep(60)  # Retry après 1 minute

    def _save_stats(self):
        """Sauvegarde les stats dans un fichier JSON."""
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "current": self._stats.to_dict(),
            "history": [s.to_dict() for s in self._history[-30:]]  # Garder 30 jours
        }
        with open(self._persistence_path, "w") as f:
            json.dump(data, f, indent=2)

    def _load_stats(self):
        """Charge les stats depuis un fichier JSON."""
        if not self._persistence_path.exists():
            return

        try:
            with open(self._persistence_path, "r") as f:
                data = json.load(f)
                self._stats = DailyStats.from_dict(data.get("current", {}))
                self._history = [
                    DailyStats.from_dict(h) for h in data.get("history", [])
                ]
        except (json.JSONDecodeError, TypeError) as e:
            print(f"⚠️ Erreur chargement daily stats: {e}")

    def get_summary(self) -> dict:
        """Retourne un résumé pour l'affichage."""
        return {
            "status": self._status.value,
            "current_pnl": self._stats.current_pnl,
            "unrealized_pnl": self._stats.unrealized_pnl,
            "total_pnl": self._stats.total_pnl,
            "current_loss": self.current_loss,
            "loss_limit": self._get_effective_limit(),
            "loss_percentage": self.loss_percentage,
            "remaining_budget": self.remaining_loss_budget,
            "position_multiplier": self.position_size_multiplier,
            "trades_today": self._stats.trades_count,
            "win_rate": self._stats.win_rate,
            "can_trade": self.can_trade[0],
            "blocked_reason": self.can_trade[1] if not self.can_trade[0] else None
        }


# Instance globale
_daily_loss_manager: Optional[DailyLossManager] = None


def get_daily_loss_manager() -> Optional[DailyLossManager]:
    """Retourne l'instance globale du gestionnaire."""
    return _daily_loss_manager


def init_daily_loss_manager(
    max_daily_loss_usd: float = 100.0,
    max_daily_loss_percent: float = 10.0,
    total_capital: float = 1000.0,
    **kwargs
) -> DailyLossManager:
    """Initialise le gestionnaire global."""
    global _daily_loss_manager
    _daily_loss_manager = DailyLossManager(
        max_daily_loss_usd=max_daily_loss_usd,
        max_daily_loss_percent=max_daily_loss_percent,
        total_capital=total_capital,
        **kwargs
    )
    return _daily_loss_manager
