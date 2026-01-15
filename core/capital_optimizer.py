"""
Capital Optimizer - Apex Predator v8.1

Calcule les paramètres de capital optimaux basés sur:
- Capital disponible (paper ou réel)
- Règles de risk management
- Kelly Criterion (optionnel)
- Historique de performance

Points de référence calibrés pour différents niveaux de capital.
Interpolation linéaire entre les niveaux pour un scaling smooth.

Usage:
    optimizer = CapitalOptimizer(capital=1000.0)
    params = optimizer.calculate_optimal_params()
    optimizer.apply_to_trading_params()
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from datetime import datetime
import math


@dataclass
class CapitalTier:
    """Point de référence pour un niveau de capital."""
    capital: float
    label: str

    # Gabagool
    gabagool_trade_pct: float      # % du capital alloué par trade
    gabagool_max_positions: int    # Positions simultanées max
    gabagool_allocation_pct: float # % du capital total pour Gabagool

    # Smart Ape
    smart_ape_trade_pct: float
    smart_ape_max_positions: int
    smart_ape_allocation_pct: float

    # Risk Management
    max_daily_loss_pct: float      # Perte max journalière (%)
    position_size_cap: float       # Taille max d'une position en $


# Points de référence calibrés (du paper trading aux whales)
CAPITAL_TIERS: List[CapitalTier] = [
    # Nano ($50) - Ultra conservateur, apprendre les mécaniques
    CapitalTier(
        capital=50.0, label="nano",
        gabagool_trade_pct=10.0,    # $5 par trade
        gabagool_max_positions=3,
        gabagool_allocation_pct=70.0,
        smart_ape_trade_pct=15.0,   # $4.50 par trade
        smart_ape_max_positions=2,
        smart_ape_allocation_pct=30.0,
        max_daily_loss_pct=20.0,    # $10 max loss
        position_size_cap=10.0,
    ),

    # Micro ($100) - Encore très conservateur
    CapitalTier(
        capital=100.0, label="micro",
        gabagool_trade_pct=8.0,     # $8 par trade
        gabagool_max_positions=5,
        gabagool_allocation_pct=65.0,
        smart_ape_trade_pct=12.0,   # $4.20 par trade
        smart_ape_max_positions=3,
        smart_ape_allocation_pct=35.0,
        max_daily_loss_pct=15.0,    # $15 max loss
        position_size_cap=15.0,
    ),

    # Small ($250) - Début de diversification sérieuse
    CapitalTier(
        capital=250.0, label="small",
        gabagool_trade_pct=6.0,     # $15 par trade
        gabagool_max_positions=8,
        gabagool_allocation_pct=60.0,
        smart_ape_trade_pct=10.0,   # $10 par trade
        smart_ape_max_positions=4,
        smart_ape_allocation_pct=40.0,
        max_daily_loss_pct=12.0,    # $30 max loss
        position_size_cap=25.0,
    ),

    # Medium ($500) - Capital paper trading typique
    CapitalTier(
        capital=500.0, label="medium",
        gabagool_trade_pct=5.0,     # $25 par trade
        gabagool_max_positions=12,
        gabagool_allocation_pct=60.0,
        smart_ape_trade_pct=8.0,    # $16 par trade
        smart_ape_max_positions=5,
        smart_ape_allocation_pct=40.0,
        max_daily_loss_pct=10.0,    # $50 max loss
        position_size_cap=40.0,
    ),

    # Standard ($1000) - Capital recommandé pour démarrer en réel
    CapitalTier(
        capital=1000.0, label="standard",
        gabagool_trade_pct=5.0,     # $50 par trade (5% de gabagool capital)
        gabagool_max_positions=15,
        gabagool_allocation_pct=60.0,  # $600 pour Gabagool
        smart_ape_trade_pct=8.0,    # $32 par trade
        smart_ape_max_positions=5,
        smart_ape_allocation_pct=40.0,  # $400 pour Smart Ape
        max_daily_loss_pct=10.0,    # $100 max loss
        position_size_cap=60.0,
    ),

    # Large ($2500) - Trader confirmé
    CapitalTier(
        capital=2500.0, label="large",
        gabagool_trade_pct=4.0,     # $60 par trade
        gabagool_max_positions=20,
        gabagool_allocation_pct=55.0,
        smart_ape_trade_pct=6.0,    # $67.50 par trade
        smart_ape_max_positions=6,
        smart_ape_allocation_pct=45.0,
        max_daily_loss_pct=8.0,     # $200 max loss
        position_size_cap=100.0,
    ),

    # XLarge ($5000) - Gros joueur
    CapitalTier(
        capital=5000.0, label="xlarge",
        gabagool_trade_pct=3.0,     # $82.50 par trade
        gabagool_max_positions=25,
        gabagool_allocation_pct=55.0,
        smart_ape_trade_pct=5.0,    # $112.50 par trade
        smart_ape_max_positions=8,
        smart_ape_allocation_pct=45.0,
        max_daily_loss_pct=6.0,     # $300 max loss
        position_size_cap=150.0,
    ),

    # Whale ($10000+) - Full pro
    CapitalTier(
        capital=10000.0, label="whale",
        gabagool_trade_pct=2.5,     # $137.50 par trade
        gabagool_max_positions=30,
        gabagool_allocation_pct=50.0,
        smart_ape_trade_pct=4.0,    # $200 par trade
        smart_ape_max_positions=10,
        smart_ape_allocation_pct=50.0,
        max_daily_loss_pct=5.0,     # $500 max loss
        position_size_cap=250.0,
    ),
]


@dataclass
class OptimizedCapitalParams:
    """Paramètres de capital optimisés."""
    # Input
    total_capital: float
    tier_label: str

    # Gabagool
    gabagool_capital_usd: float
    gabagool_trade_percent: float
    gabagool_trade_size_usd: float  # Calculé
    gabagool_max_positions: int

    # Smart Ape
    smart_ape_capital_usd: float
    smart_ape_trade_percent: float
    smart_ape_trade_size_usd: float  # Calculé
    smart_ape_max_positions: int

    # Risk Management
    max_daily_loss_usd: float
    max_daily_loss_percent: float
    max_total_exposure: float
    position_size_cap: float

    # Legacy (pour compatibilité)
    capital_per_trade: float
    max_open_positions: int

    # Metadata
    calculated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        return {
            "total_capital": self.total_capital,
            "tier": self.tier_label,
            "gabagool": {
                "capital_usd": round(self.gabagool_capital_usd, 2),
                "trade_percent": round(self.gabagool_trade_percent, 2),
                "trade_size_usd": round(self.gabagool_trade_size_usd, 2),
                "max_positions": self.gabagool_max_positions,
            },
            "smart_ape": {
                "capital_usd": round(self.smart_ape_capital_usd, 2),
                "trade_percent": round(self.smart_ape_trade_percent, 2),
                "trade_size_usd": round(self.smart_ape_trade_size_usd, 2),
                "max_positions": self.smart_ape_max_positions,
            },
            "risk": {
                "max_daily_loss_usd": round(self.max_daily_loss_usd, 2),
                "max_daily_loss_percent": round(self.max_daily_loss_percent, 2),
                "max_total_exposure": round(self.max_total_exposure, 2),
                "position_size_cap": round(self.position_size_cap, 2),
            },
            "legacy": {
                "capital_per_trade": round(self.capital_per_trade, 2),
                "max_open_positions": self.max_open_positions,
            },
            "calculated_at": self.calculated_at.isoformat(),
        }


class CapitalOptimizer:
    """
    Optimise les paramètres de trading basés sur le capital disponible.

    Utilise une interpolation linéaire entre les points de référence
    pour un scaling smooth des paramètres.

    Usage:
        optimizer = CapitalOptimizer(capital=1000.0)
        params = optimizer.calculate_optimal_params()

        # Appliquer aux trading params
        optimizer.apply_to_trading_params()

        # Ou obtenir un résumé
        print(optimizer.get_summary())
    """

    def __init__(
        self,
        capital: float,
        win_rate: Optional[float] = None,
        avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None,
        use_kelly: bool = False,
        kelly_fraction: float = 0.25,
    ):
        """
        Args:
            capital: Capital total disponible en USD
            win_rate: Taux de gain historique (0-1) pour Kelly
            avg_win: Gain moyen par trade gagnant
            avg_loss: Perte moyenne par trade perdant
            use_kelly: Activer le dimensionnement Kelly
            kelly_fraction: Fraction de Kelly (0.25 = quarter Kelly)
        """
        self.capital = max(10.0, capital)  # Minimum $10
        self.win_rate = win_rate
        self.avg_win = avg_win
        self.avg_loss = avg_loss
        self.use_kelly = use_kelly
        self.kelly_fraction = kelly_fraction

        self._params: Optional[OptimizedCapitalParams] = None

    @property
    def params(self) -> OptimizedCapitalParams:
        """Retourne les paramètres optimisés (calcule si nécessaire)."""
        if self._params is None:
            self._params = self.calculate_optimal_params()
        return self._params

    def calculate_optimal_params(self, strategy_mode: str = "both") -> OptimizedCapitalParams:
        """
        Calcule les paramètres optimaux pour le capital donné.

        Args:
            strategy_mode: "gabagool", "smart_ape", ou "both" (v8.2)
                - "both": Allocation standard entre les deux stratégies
                - "gabagool": 100% du capital à Gabagool
                - "smart_ape": 100% du capital à Smart Ape
        """
        # Trouver les tiers encadrants
        lower_tier, upper_tier = self._find_bounding_tiers()

        # Interpoler les paramètres
        params = self._interpolate_params(lower_tier, upper_tier)

        # Ajuster l'allocation selon le mode stratégie (v8.2)
        params = self._apply_strategy_mode(params, strategy_mode)

        # Appliquer Kelly si activé et données disponibles
        if self.use_kelly and self._can_use_kelly():
            params = self._apply_kelly_adjustment(params)

        self._params = params
        return params

    def _apply_strategy_mode(
        self,
        params: OptimizedCapitalParams,
        strategy_mode: str
    ) -> OptimizedCapitalParams:
        """Ajuste l'allocation selon le mode stratégie (v8.2)."""

        if strategy_mode == "gabagool":
            # 100% du capital à Gabagool
            params.gabagool_capital_usd = self.capital
            params.smart_ape_capital_usd = 0.0

            # Recalculer la taille de trade Gabagool
            params.gabagool_trade_size_usd = self.capital * params.gabagool_trade_percent / 100.0

            # Augmenter le nombre de positions possibles (jusqu'à 2x, plafonné)
            params.gabagool_max_positions = min(30, int(params.gabagool_max_positions * 1.5))
            params.smart_ape_max_positions = 0
            params.smart_ape_trade_size_usd = 0.0

            # Recalculer l'exposition max
            params.max_total_exposure = params.gabagool_trade_size_usd * params.gabagool_max_positions

            # Legacy
            params.capital_per_trade = params.gabagool_trade_size_usd
            params.max_open_positions = params.gabagool_max_positions

        elif strategy_mode == "smart_ape":
            # 100% du capital à Smart Ape
            params.gabagool_capital_usd = 0.0
            params.smart_ape_capital_usd = self.capital

            # Recalculer la taille de trade Smart Ape
            params.smart_ape_trade_size_usd = self.capital * params.smart_ape_trade_percent / 100.0

            # Augmenter le nombre de positions possibles (jusqu'à 2x, plafonné)
            params.smart_ape_max_positions = min(15, int(params.smart_ape_max_positions * 1.5))
            params.gabagool_max_positions = 0
            params.gabagool_trade_size_usd = 0.0

            # Recalculer l'exposition max
            params.max_total_exposure = params.smart_ape_trade_size_usd * params.smart_ape_max_positions

            # Legacy
            params.capital_per_trade = params.smart_ape_trade_size_usd
            params.max_open_positions = params.smart_ape_max_positions

        # Mode "both" - garder les paramètres interpolés tels quels

        return params

    def _find_bounding_tiers(self) -> Tuple[CapitalTier, CapitalTier]:
        """Trouve les deux tiers qui encadrent le capital."""
        # Sous le minimum
        if self.capital <= CAPITAL_TIERS[0].capital:
            return CAPITAL_TIERS[0], CAPITAL_TIERS[0]

        # Au-dessus du maximum
        if self.capital >= CAPITAL_TIERS[-1].capital:
            return CAPITAL_TIERS[-1], CAPITAL_TIERS[-1]

        # Entre deux tiers
        for i in range(len(CAPITAL_TIERS) - 1):
            if CAPITAL_TIERS[i].capital <= self.capital < CAPITAL_TIERS[i + 1].capital:
                return CAPITAL_TIERS[i], CAPITAL_TIERS[i + 1]

        return CAPITAL_TIERS[-1], CAPITAL_TIERS[-1]

    def _interpolate_params(
        self,
        lower: CapitalTier,
        upper: CapitalTier
    ) -> OptimizedCapitalParams:
        """Interpole les paramètres entre deux tiers."""

        # Calcul du ratio d'interpolation
        if lower.capital == upper.capital:
            ratio = 1.0
        else:
            ratio = (self.capital - lower.capital) / (upper.capital - lower.capital)

        def lerp(a: float, b: float) -> float:
            """Interpolation linéaire."""
            return a + (b - a) * ratio

        def lerp_int(a: int, b: int) -> int:
            """Interpolation linéaire pour entiers."""
            return round(a + (b - a) * ratio)

        # Déterminer le label du tier
        if ratio < 0.5:
            tier_label = lower.label
        else:
            tier_label = upper.label

        # Interpoler les allocations (%)
        gabagool_alloc_pct = lerp(lower.gabagool_allocation_pct, upper.gabagool_allocation_pct)
        smart_ape_alloc_pct = lerp(lower.smart_ape_allocation_pct, upper.smart_ape_allocation_pct)

        # Calculer les capitaux par stratégie
        gabagool_capital = self.capital * gabagool_alloc_pct / 100.0
        smart_ape_capital = self.capital * smart_ape_alloc_pct / 100.0

        # Interpoler les % de trade
        gabagool_trade_pct = lerp(lower.gabagool_trade_pct, upper.gabagool_trade_pct)
        smart_ape_trade_pct = lerp(lower.smart_ape_trade_pct, upper.smart_ape_trade_pct)

        # Calculer les tailles de trade en $
        gabagool_trade_size = gabagool_capital * gabagool_trade_pct / 100.0
        smart_ape_trade_size = smart_ape_capital * smart_ape_trade_pct / 100.0

        # Interpoler les positions max
        gabagool_max_pos = lerp_int(lower.gabagool_max_positions, upper.gabagool_max_positions)
        smart_ape_max_pos = lerp_int(lower.smart_ape_max_positions, upper.smart_ape_max_positions)

        # Risk management
        max_daily_loss_pct = lerp(lower.max_daily_loss_pct, upper.max_daily_loss_pct)
        max_daily_loss_usd = self.capital * max_daily_loss_pct / 100.0
        position_size_cap = lerp(lower.position_size_cap, upper.position_size_cap)

        # Exposition totale max (somme des deux stratégies à pleine capacité)
        max_exposure = (gabagool_trade_size * gabagool_max_pos) + \
                      (smart_ape_trade_size * smart_ape_max_pos)

        # Legacy params (moyenne pondérée)
        total_positions = gabagool_max_pos + smart_ape_max_pos
        avg_trade_size = (gabagool_trade_size * gabagool_max_pos +
                         smart_ape_trade_size * smart_ape_max_pos) / max(1, total_positions)

        return OptimizedCapitalParams(
            total_capital=self.capital,
            tier_label=tier_label,

            gabagool_capital_usd=gabagool_capital,
            gabagool_trade_percent=gabagool_trade_pct,
            gabagool_trade_size_usd=gabagool_trade_size,
            gabagool_max_positions=gabagool_max_pos,

            smart_ape_capital_usd=smart_ape_capital,
            smart_ape_trade_percent=smart_ape_trade_pct,
            smart_ape_trade_size_usd=smart_ape_trade_size,
            smart_ape_max_positions=smart_ape_max_pos,

            max_daily_loss_usd=max_daily_loss_usd,
            max_daily_loss_percent=max_daily_loss_pct,
            max_total_exposure=max_exposure,
            position_size_cap=position_size_cap,

            capital_per_trade=avg_trade_size,
            max_open_positions=total_positions,
        )

    def _can_use_kelly(self) -> bool:
        """Vérifie si les données pour Kelly sont disponibles."""
        return (
            self.win_rate is not None and
            self.avg_win is not None and
            self.avg_loss is not None and
            self.win_rate > 0 and
            self.avg_loss > 0
        )

    def _calculate_kelly(self) -> float:
        """
        Calcule le Kelly Criterion.

        f* = (p * b - q) / b
        où:
          p = probabilité de gain (win_rate)
          q = probabilité de perte (1 - win_rate)
          b = ratio gain/perte (avg_win / avg_loss)
        """
        if not self._can_use_kelly():
            return 0.0

        p = self.win_rate
        q = 1 - p
        b = abs(self.avg_win / self.avg_loss)

        kelly = (p * b - q) / b

        # Kelly négatif = pas d'edge, ne pas trader
        if kelly <= 0:
            return 0.0

        # Appliquer la fraction Kelly (plus conservateur)
        return kelly * self.kelly_fraction

    def _apply_kelly_adjustment(
        self,
        params: OptimizedCapitalParams
    ) -> OptimizedCapitalParams:
        """Ajuste les tailles de trade selon Kelly."""
        kelly = self._calculate_kelly()

        if kelly <= 0:
            return params

        # Kelly donne le % optimal du capital à risquer
        # Limiter à 2x la taille de base max
        kelly_multiplier = min(2.0, 1.0 + kelly)

        # Ajuster les tailles de trade
        params.gabagool_trade_size_usd *= kelly_multiplier
        params.smart_ape_trade_size_usd *= kelly_multiplier
        params.capital_per_trade *= kelly_multiplier

        # Recalculer les %
        if params.gabagool_capital_usd > 0:
            params.gabagool_trade_percent = (
                params.gabagool_trade_size_usd / params.gabagool_capital_usd * 100
            )
        if params.smart_ape_capital_usd > 0:
            params.smart_ape_trade_percent = (
                params.smart_ape_trade_size_usd / params.smart_ape_capital_usd * 100
            )

        return params

    def apply_to_trading_params(self) -> None:
        """
        Applique les paramètres optimisés aux TradingParams globaux.

        Met à jour:
        - Capital par stratégie
        - % de trade par stratégie
        - Positions max
        - Limites de perte journalière
        """
        from config import get_trading_params, update_trading_params

        params = self.params
        trading_params = get_trading_params()

        # Gabagool
        trading_params.gabagool_capital_usd = params.gabagool_capital_usd
        trading_params.gabagool_trade_percent = params.gabagool_trade_percent
        trading_params.gabagool_max_positions = params.gabagool_max_positions

        # Smart Ape
        trading_params.smart_ape_capital_usd = params.smart_ape_capital_usd
        trading_params.smart_ape_trade_percent = params.smart_ape_trade_percent
        trading_params.smart_ape_max_positions = params.smart_ape_max_positions

        # Risk Management
        trading_params.max_daily_loss_usd = params.max_daily_loss_usd
        trading_params.max_daily_loss_percent = params.max_daily_loss_percent
        trading_params.max_total_exposure = params.max_total_exposure

        # Legacy
        trading_params.capital_per_trade = params.capital_per_trade
        trading_params.max_open_positions = params.max_open_positions

        # Sauvegarder
        update_trading_params(trading_params)

    def apply_to_paper_config(self) -> None:
        """
        Applique les paramètres au paper trading config.

        Met à jour également le capital de départ paper.
        """
        from config import get_trading_params, update_trading_params

        trading_params = get_trading_params()

        # Mettre à jour le capital paper
        trading_params.paper_starting_capital = self.capital

        # Appliquer les autres paramètres
        self.apply_to_trading_params()

    def get_summary(self) -> str:
        """Génère un résumé textuel des paramètres optimisés."""
        p = self.params

        lines = [
            "",
            "═" * 60,
            f"💰 CAPITAL OPTIMIZER - ${p.total_capital:,.2f} ({p.tier_label.upper()})",
            "═" * 60,
            "",
            "ALLOCATION",
            f"  Gabagool:  ${p.gabagool_capital_usd:,.2f} ({p.gabagool_capital_usd/p.total_capital*100:.0f}%)",
            f"  Smart Ape: ${p.smart_ape_capital_usd:,.2f} ({p.smart_ape_capital_usd/p.total_capital*100:.0f}%)",
            "",
            "GABAGOOL",
            f"  Trade Size:    ${p.gabagool_trade_size_usd:.2f} ({p.gabagool_trade_percent:.1f}%)",
            f"  Max Positions: {p.gabagool_max_positions}",
            f"  Max Exposure:  ${p.gabagool_trade_size_usd * p.gabagool_max_positions:.2f}",
            "",
            "SMART APE",
            f"  Trade Size:    ${p.smart_ape_trade_size_usd:.2f} ({p.smart_ape_trade_percent:.1f}%)",
            f"  Max Positions: {p.smart_ape_max_positions}",
            f"  Max Exposure:  ${p.smart_ape_trade_size_usd * p.smart_ape_max_positions:.2f}",
            "",
            "RISK MANAGEMENT",
            f"  Max Daily Loss:   ${p.max_daily_loss_usd:.2f} ({p.max_daily_loss_percent:.1f}%)",
            f"  Max Exposure:     ${p.max_total_exposure:.2f}",
            f"  Position Cap:     ${p.position_size_cap:.2f}",
        ]

        if self.use_kelly and self._can_use_kelly():
            kelly = self._calculate_kelly()
            lines.extend([
                "",
                "KELLY CRITERION",
                f"  Win Rate:    {self.win_rate*100:.1f}%",
                f"  Avg Win:     ${self.avg_win:.2f}",
                f"  Avg Loss:    ${abs(self.avg_loss):.2f}",
                f"  Full Kelly:  {kelly/self.kelly_fraction*100:.1f}%",
                f"  Applied:     {kelly*100:.1f}% ({self.kelly_fraction*100:.0f}% Kelly)",
            ])

        lines.extend([
            "",
            "═" * 60,
        ])

        return "\n".join(lines)

    def print_summary(self) -> None:
        """Affiche le résumé dans la console."""
        print(self.get_summary())


def optimize_for_capital(
    capital: float,
    apply_params: bool = True,
    use_kelly: bool = False,
    win_rate: Optional[float] = None,
    avg_win: Optional[float] = None,
    avg_loss: Optional[float] = None,
    strategy_mode: str = "both",
) -> OptimizedCapitalParams:
    """
    Fonction utilitaire pour optimiser les paramètres pour un capital donné.

    Args:
        capital: Capital en USD
        apply_params: Appliquer automatiquement aux TradingParams
        use_kelly: Activer le dimensionnement Kelly
        win_rate/avg_win/avg_loss: Stats pour Kelly
        strategy_mode: "gabagool", "smart_ape", ou "both" (v8.2)

    Returns:
        OptimizedCapitalParams

    Usage:
        # Simple
        params = optimize_for_capital(1000.0)

        # Mode mono-stratégie
        params = optimize_for_capital(1000.0, strategy_mode="gabagool")

        # Avec Kelly
        params = optimize_for_capital(
            1000.0,
            use_kelly=True,
            win_rate=0.65,
            avg_win=2.50,
            avg_loss=1.20
        )
    """
    optimizer = CapitalOptimizer(
        capital=capital,
        use_kelly=use_kelly,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
    )

    params = optimizer.calculate_optimal_params(strategy_mode=strategy_mode)

    if apply_params:
        optimizer.apply_to_trading_params()

    return params
