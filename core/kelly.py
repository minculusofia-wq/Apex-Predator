"""
Kelly Criterion Position Sizing - Dimensionnement optimal des positions

Le critère de Kelly calcule la taille de position optimale pour maximiser
la croissance du capital à long terme basé sur l'avantage statistique.

Formule: f* = (p * b - q) / b
  - f* = fraction optimale du capital à risquer
  - p  = probabilité de gain
  - q  = probabilité de perte (1 - p)
  - b  = odds (ratio gain/perte)

En pratique, on utilise une fraction de Kelly (quarter/half) pour réduire
la variance et le risque de ruine.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
import json
from pathlib import Path


class Strategy(Enum):
    """Stratégies supportées."""
    GABAGOOL = "gabagool"
    SMART_APE = "smart_ape"


@dataclass
class TradeRecord:
    """Enregistrement d'un trade pour le calcul Kelly."""
    strategy: Strategy
    timestamp: datetime
    size_usd: float
    pnl_usd: float
    pair_cost: Optional[float] = None      # Pour Gabagool
    payout_ratio: Optional[float] = None   # Pour Smart Ape

    @property
    def is_win(self) -> bool:
        return self.pnl_usd > 0

    @property
    def return_pct(self) -> float:
        """Retourne le % de gain/perte."""
        if self.size_usd == 0:
            return 0.0
        return self.pnl_usd / self.size_usd


@dataclass
class KellyStats:
    """Statistiques Kelly pour une stratégie."""
    strategy: Strategy
    win_rate: float = 0.0           # Taux de réussite (0-1)
    avg_win: float = 0.0            # Gain moyen en %
    avg_loss: float = 0.0           # Perte moyenne en % (valeur absolue)
    edge: float = 0.0               # Avantage statistique
    kelly_fraction: float = 0.0     # Fraction Kelly optimale
    recommended_size: float = 1.0   # Multiplicateur recommandé
    sample_size: int = 0            # Nombre de trades analysés
    last_updated: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy.value,
            "win_rate": round(self.win_rate, 4),
            "avg_win": round(self.avg_win, 4),
            "avg_loss": round(self.avg_loss, 4),
            "edge": round(self.edge, 4),
            "kelly_fraction": round(self.kelly_fraction, 4),
            "recommended_size": round(self.recommended_size, 2),
            "sample_size": self.sample_size,
            "last_updated": self.last_updated.isoformat()
        }


class KellySizer:
    """
    Gestionnaire de dimensionnement Kelly pour les deux stratégies.

    Calcule la taille optimale des positions basée sur:
    - Historique des trades (win rate, avg win/loss)
    - Fraction Kelly configurée (conservative)
    - Limites de risque (max multiplier)
    """

    def __init__(
        self,
        fraction: float = 0.25,
        min_edge: float = 0.02,
        max_multiplier: float = 2.0,
        lookback_trades: int = 50,
        persistence_file: str = "data/kelly_trades.json"
    ):
        self.fraction = fraction
        self.min_edge = min_edge
        self.max_multiplier = max_multiplier
        self.lookback_trades = lookback_trades
        self.persistence_file = persistence_file

        # Historique des trades par stratégie
        self._trades: Dict[Strategy, List[TradeRecord]] = {
            Strategy.GABAGOOL: [],
            Strategy.SMART_APE: []
        }

        # Stats calculées
        self._stats: Dict[Strategy, KellyStats] = {
            Strategy.GABAGOOL: KellyStats(strategy=Strategy.GABAGOOL),
            Strategy.SMART_APE: KellyStats(strategy=Strategy.SMART_APE)
        }

        # Charger l'historique
        self._load_trades()

    # ═══════════════════════════════════════════════════════════════
    # CALCUL KELLY
    # ═══════════════════════════════════════════════════════════════

    def calculate_kelly(self, strategy: Strategy) -> KellyStats:
        """
        Calcule les statistiques Kelly pour une stratégie.

        Returns:
            KellyStats avec les métriques calculées
        """
        trades = self._trades[strategy][-self.lookback_trades:]
        stats = KellyStats(strategy=strategy)

        if len(trades) < 10:
            # Pas assez de données, retourner défaut
            stats.sample_size = len(trades)
            stats.recommended_size = 1.0
            return stats

        # Séparer wins et losses
        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]

        stats.sample_size = len(trades)
        stats.win_rate = len(wins) / len(trades)

        # Calcul avg win/loss
        if wins:
            stats.avg_win = sum(t.return_pct for t in wins) / len(wins)
        if losses:
            stats.avg_loss = abs(sum(t.return_pct for t in losses) / len(losses))

        # Edge = Expected Value
        # EV = (win_rate * avg_win) - (loss_rate * avg_loss)
        loss_rate = 1 - stats.win_rate
        stats.edge = (stats.win_rate * stats.avg_win) - (loss_rate * stats.avg_loss)

        # Kelly Fraction
        # f* = (p * b - q) / b où b = avg_win / avg_loss (odds)
        if stats.avg_loss > 0:
            odds = stats.avg_win / stats.avg_loss
            numerator = (stats.win_rate * odds) - loss_rate
            if odds > 0:
                stats.kelly_fraction = numerator / odds
            else:
                stats.kelly_fraction = 0
        else:
            stats.kelly_fraction = 0

        # Appliquer fraction conservative
        adjusted_kelly = stats.kelly_fraction * self.fraction

        # Déterminer le multiplicateur recommandé
        if stats.edge < self.min_edge:
            # Edge insuffisant, taille standard
            stats.recommended_size = 1.0
        elif adjusted_kelly <= 0:
            # Kelly négatif = pas d'edge, réduire
            stats.recommended_size = 0.5
        else:
            # Appliquer Kelly avec limites
            stats.recommended_size = min(
                1.0 + adjusted_kelly,
                self.max_multiplier
            )

        stats.last_updated = datetime.now()
        self._stats[strategy] = stats

        return stats

    def get_position_size(
        self,
        strategy: Strategy,
        base_size: float,
        kelly_enabled: bool = True
    ) -> Tuple[float, KellyStats]:
        """
        Calcule la taille de position ajustée par Kelly.

        Args:
            strategy: Stratégie (GABAGOOL ou SMART_APE)
            base_size: Taille de base en USD
            kelly_enabled: Si Kelly est activé

        Returns:
            (taille_ajustée, stats)
        """
        stats = self.calculate_kelly(strategy)

        if not kelly_enabled:
            return base_size, stats

        adjusted_size = base_size * stats.recommended_size
        return round(adjusted_size, 2), stats

    # ═══════════════════════════════════════════════════════════════
    # CALCUL SPÉCIFIQUE PAR STRATÉGIE
    # ═══════════════════════════════════════════════════════════════

    def get_gabagool_size(
        self,
        base_size: float,
        pair_cost: float,
        kelly_enabled: bool = True
    ) -> Tuple[float, KellyStats]:
        """
        Calcule la taille pour Gabagool avec ajustement pair_cost.

        Un pair_cost plus bas = edge plus élevé = taille plus grande
        """
        size, stats = self.get_position_size(
            Strategy.GABAGOOL,
            base_size,
            kelly_enabled
        )

        if kelly_enabled and pair_cost < 0.97:
            # Bonus pour excellent pair_cost
            edge_bonus = (0.97 - pair_cost) * 5  # +5% par 0.01 sous 0.97
            size *= (1 + min(edge_bonus, 0.3))  # Max +30%

        return round(size, 2), stats

    def get_smart_ape_size(
        self,
        base_size: float,
        payout_ratio: float,
        kelly_enabled: bool = True
    ) -> Tuple[float, KellyStats]:
        """
        Calcule la taille pour Smart Ape avec ajustement payout.

        Un payout_ratio plus élevé = edge plus élevé = taille plus grande
        """
        size, stats = self.get_position_size(
            Strategy.SMART_APE,
            base_size,
            kelly_enabled
        )

        if kelly_enabled and payout_ratio > 1.8:
            # Bonus pour excellent payout
            edge_bonus = (payout_ratio - 1.8) * 0.5  # +50% par 1.0 au-dessus de 1.8
            size *= (1 + min(edge_bonus, 0.3))  # Max +30%

        return round(size, 2), stats

    # ═══════════════════════════════════════════════════════════════
    # ENREGISTREMENT DES TRADES
    # ═══════════════════════════════════════════════════════════════

    def record_trade(
        self,
        strategy: Strategy,
        size_usd: float,
        pnl_usd: float,
        pair_cost: Optional[float] = None,
        payout_ratio: Optional[float] = None
    ) -> None:
        """Enregistre un trade pour le calcul Kelly."""
        record = TradeRecord(
            strategy=strategy,
            timestamp=datetime.now(),
            size_usd=size_usd,
            pnl_usd=pnl_usd,
            pair_cost=pair_cost,
            payout_ratio=payout_ratio
        )

        self._trades[strategy].append(record)

        # Garder seulement les N derniers trades
        if len(self._trades[strategy]) > self.lookback_trades * 2:
            self._trades[strategy] = self._trades[strategy][-self.lookback_trades:]

        # Sauvegarder
        self._save_trades()

        # Recalculer stats
        self.calculate_kelly(strategy)

    def record_gabagool_trade(
        self,
        size_usd: float,
        pnl_usd: float,
        pair_cost: float
    ) -> None:
        """Shortcut pour enregistrer un trade Gabagool."""
        self.record_trade(
            Strategy.GABAGOOL,
            size_usd,
            pnl_usd,
            pair_cost=pair_cost
        )

    def record_smart_ape_trade(
        self,
        size_usd: float,
        pnl_usd: float,
        payout_ratio: float
    ) -> None:
        """Shortcut pour enregistrer un trade Smart Ape."""
        self.record_trade(
            Strategy.SMART_APE,
            size_usd,
            pnl_usd,
            payout_ratio=payout_ratio
        )

    # ═══════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════════

    def _save_trades(self) -> None:
        """Sauvegarde les trades dans un fichier JSON."""
        try:
            path = Path(self.persistence_file)
            path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "gabagool": [
                    {
                        "timestamp": t.timestamp.isoformat(),
                        "size_usd": t.size_usd,
                        "pnl_usd": t.pnl_usd,
                        "pair_cost": t.pair_cost
                    }
                    for t in self._trades[Strategy.GABAGOOL]
                ],
                "smart_ape": [
                    {
                        "timestamp": t.timestamp.isoformat(),
                        "size_usd": t.size_usd,
                        "pnl_usd": t.pnl_usd,
                        "payout_ratio": t.payout_ratio
                    }
                    for t in self._trades[Strategy.SMART_APE]
                ]
            }

            with open(path, "w") as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            print(f"[Kelly] Erreur sauvegarde: {e}")

    def _load_trades(self) -> None:
        """Charge les trades depuis le fichier JSON."""
        try:
            path = Path(self.persistence_file)
            if not path.exists():
                return

            with open(path, "r") as f:
                data = json.load(f)

            # Charger Gabagool trades
            for t in data.get("gabagool", []):
                self._trades[Strategy.GABAGOOL].append(TradeRecord(
                    strategy=Strategy.GABAGOOL,
                    timestamp=datetime.fromisoformat(t["timestamp"]),
                    size_usd=t["size_usd"],
                    pnl_usd=t["pnl_usd"],
                    pair_cost=t.get("pair_cost")
                ))

            # Charger Smart Ape trades
            for t in data.get("smart_ape", []):
                self._trades[Strategy.SMART_APE].append(TradeRecord(
                    strategy=Strategy.SMART_APE,
                    timestamp=datetime.fromisoformat(t["timestamp"]),
                    size_usd=t["size_usd"],
                    pnl_usd=t["pnl_usd"],
                    payout_ratio=t.get("payout_ratio")
                ))

            print(f"[Kelly] Chargé {len(self._trades[Strategy.GABAGOOL])} trades Gabagool, "
                  f"{len(self._trades[Strategy.SMART_APE])} trades Smart Ape")

        except Exception as e:
            print(f"[Kelly] Erreur chargement: {e}")

    # ═══════════════════════════════════════════════════════════════
    # STATUS
    # ═══════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """Retourne le status complet du Kelly Sizer."""
        return {
            "config": {
                "fraction": self.fraction,
                "min_edge": self.min_edge,
                "max_multiplier": self.max_multiplier,
                "lookback_trades": self.lookback_trades
            },
            "gabagool": self._stats[Strategy.GABAGOOL].to_dict(),
            "smart_ape": self._stats[Strategy.SMART_APE].to_dict(),
            "trade_counts": {
                "gabagool": len(self._trades[Strategy.GABAGOOL]),
                "smart_ape": len(self._trades[Strategy.SMART_APE])
            }
        }

    def get_stats(self, strategy: Strategy) -> KellyStats:
        """Retourne les stats pour une stratégie."""
        return self._stats[strategy]


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCE GLOBALE
# ═══════════════════════════════════════════════════════════════════════════

_kelly_sizer: Optional[KellySizer] = None


def get_kelly_sizer() -> KellySizer:
    """Retourne l'instance globale du Kelly Sizer."""
    global _kelly_sizer
    if _kelly_sizer is None:
        from config.trading_params import get_trading_params
        params = get_trading_params()
        _kelly_sizer = KellySizer(
            fraction=params.kelly_fraction,
            min_edge=params.kelly_min_edge,
            max_multiplier=params.kelly_max_size_multiplier,
            lookback_trades=params.kelly_lookback_trades
        )
    return _kelly_sizer


def update_kelly_config(
    fraction: float = None,
    min_edge: float = None,
    max_multiplier: float = None
) -> None:
    """Met à jour la configuration Kelly."""
    sizer = get_kelly_sizer()
    if fraction is not None:
        sizer.fraction = fraction
    if min_edge is not None:
        sizer.min_edge = min_edge
    if max_multiplier is not None:
        sizer.max_multiplier = max_multiplier
