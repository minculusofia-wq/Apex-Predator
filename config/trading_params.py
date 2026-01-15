"""
Trading Params - Paramètres de trading modifiables

Ces paramètres peuvent être modifiés en temps réel via l'interface.
Ils sont sauvegardés automatiquement dans trading_params.json.
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List


@dataclass
class TradingParams:
    """
    Paramètres de trading ajustables en temps réel.

    Ces valeurs contrôlent le comportement du bot:
    - Quand entrer en position (spread minimum)
    - Combien trader (capital par trade)
    - Limites de risque (positions max, exposition totale)
    """

    # ═══════════════════════════════════════════════════════════════
    # PARAMÈTRES GABAGOOL (Aligné sur stratégie originale rentable)
    # Frais Polymarket: 2% sur gains → seuil réel doit être < 0.98
    # ═══════════════════════════════════════════════════════════════
    max_pair_cost: float = 0.975  # Coût max YES+NO (inclut marge pour 2% frais Polymarket)
    min_profit_margin: float = 0.025  # Marge profit minimum 2.5% (couvre frais + profit)

    # ═══════════════════════════════════════════════════════════════
    # PARAMÈTRES DE SPREAD (Secondaires pour Gabagool)
    # ═══════════════════════════════════════════════════════════════
    min_spread: float = 0.00      # Spread minimum désactivé (Gabagool n'utilise pas le spread)
    max_spread: float = 1.00      # Spread max désactivé

    # ═══════════════════════════════════════════════════════════════
    # PARAMÈTRES DE VOLUME (Aligné Gabagool - marchés liquides court terme)
    # ═══════════════════════════════════════════════════════════════
    min_volume_usd: float = 500.0      # Volume minimum 500$ (liquidité suffisante)
    min_depth_usd: float = 100.0       # Profondeur carnet min 100$ (évite slippage)
    max_duration_hours: int = 4        # 4h max (focus court terme comme Gabagool original)

    # ═══════════════════════════════════════════════════════════════
    # GESTION CAPITAL PAR STRATÉGIE (v7.1)
    # ═══════════════════════════════════════════════════════════════

    # Capital Gabagool
    gabagool_capital_usd: float = 500.0       # Capital total alloué à Gabagool ($)
    gabagool_trade_percent: float = 5.0       # % du capital Gabagool par trade (5% = $25 si capital=$500)
    gabagool_max_positions: int = 15          # Positions simultanées max Gabagool

    # Capital Smart Ape
    smart_ape_capital_usd: float = 300.0      # Capital total alloué à Smart Ape ($)
    smart_ape_trade_percent: float = 8.0      # % du capital Smart Ape par trade (8% = $24 si capital=$300)
    smart_ape_max_positions: int = 5          # Positions simultanées max Smart Ape

    # Legacy (compatibilité)
    capital_per_trade: float = 25.0     # $ par trade (utilisé si % non défini)
    max_open_positions: int = 15        # Positions max globales
    max_total_exposure: float = 1000.0  # Exposition totale max en $

    # 6.1: Risk Management - Multiplicateurs de capital dynamiques (suggestion)
    capital_multiplier_score_5: float = 1.2  # 120% du capital pour les trades 5 étoiles
    capital_multiplier_score_4: float = 1.0  # 100% du capital pour les trades 4 étoiles

    # ═══════════════════════════════════════════════════════════════
    # PARAMÈTRES D'EXÉCUTION (Optimisés HFT - Ultra-rapide)
    # ═══════════════════════════════════════════════════════════════
    order_offset: float = 0.003         # Décalage du prix (0.3¢ - ultra agressif)
    position_timeout_seconds: int = 0   # 0 = pas de fermeture auto (attendre résolution)
    min_time_between_trades: float = 0.2  # 200ms entre trades (HFT speed)
    
    # 6.1: Risk Management - Seuil de slippage (aligné Gabagool)
    max_pair_cost_slippage_check: float = 0.980  # Annuler si slippage fait dépasser ce seuil

    # ═══════════════════════════════════════════════════════════════
    # ASSETS CIBLES (configurables)
    # ═══════════════════════════════════════════════════════════════
    target_assets: Optional[List[str]] = field(default=None)  # None = utilise settings

    # ═══════════════════════════════════════════════════════════════
    # CONTRÔLES
    # ═══════════════════════════════════════════════════════════════
    auto_trading_enabled: bool = True  # Trading automatique activé
    require_confirmation: bool = True   # Confirmation avant trade

    # ═══════════════════════════════════════════════════════════════
    # SÉLECTION DE STRATÉGIE (v7.0)
    # ═══════════════════════════════════════════════════════════════
    strategy_mode: str = "gabagool"  # "gabagool" | "smart_ape" | "both"

    # ═══════════════════════════════════════════════════════════════
    # PARAMÈTRES SMART APE (v7.0)
    # Stratégie ciblant les marchés "Bitcoin Up or Down" 15 minutes
    # ═══════════════════════════════════════════════════════════════
    smart_ape_enabled: bool = False           # Smart Ape activé
    smart_ape_window_minutes: int = 2         # Fenêtre d'analyse (premières minutes du round)
    smart_ape_dump_threshold: float = 0.15    # Seuil de dump pour signal (15%)
    smart_ape_min_payout_ratio: float = 1.5   # Ratio payout minimum (UP+DOWN < $X)
    smart_ape_order_size_usd: float = 25.0    # Taille ordre en $
    smart_ape_max_position_usd: float = 200.0 # Position max par round

    # ═══════════════════════════════════════════════════════════════
    # KELLY CRITERION SIZING (v7.2)
    # Dimensionnement optimal basé sur l'avantage statistique
    # f* = (p * b - q) / b où p=prob win, q=prob loss, b=odds
    # ═══════════════════════════════════════════════════════════════
    kelly_enabled_gabagool: bool = False      # Kelly activé pour Gabagool
    kelly_enabled_smart_ape: bool = False     # Kelly activé pour Smart Ape
    kelly_fraction: float = 0.25              # Fraction Kelly (0.25 = quarter-Kelly, plus conservateur)
    kelly_min_edge: float = 0.02              # Edge minimum requis (2%) pour appliquer Kelly
    kelly_max_size_multiplier: float = 2.0    # Multiplicateur max vs taille de base (limite risque)
    kelly_lookback_trades: int = 50           # Nombre de trades pour calculer win rate

    # ═══════════════════════════════════════════════════════════════
    # AUTO-OPTIMIZER (v7.2)
    # Optimisation automatique des paramètres par stratégie
    # ═══════════════════════════════════════════════════════════════
    optimizer_enabled: bool = True            # Auto-optimizer global activé
    optimizer_gabagool: bool = True           # Optimiser paramètres Gabagool
    optimizer_smart_ape: bool = True          # Optimiser paramètres Smart Ape
    optimizer_interval_seconds: float = 5.0   # Intervalle de mise à jour

    # ═══════════════════════════════════════════════════════════════
    # DAILY LOSS LIMITS (v7.3) - Protection contre ruine
    # Arrête automatiquement le trading si perte journalière excessive
    # ═══════════════════════════════════════════════════════════════
    daily_loss_limit_enabled: bool = True           # Activer limite perte journalière
    max_daily_loss_usd: float = 100.0               # Perte max en $ par jour (stop trading si atteint)
    max_daily_loss_percent: float = 10.0            # Perte max en % du capital total
    daily_loss_reset_hour_utc: int = 0              # Heure UTC de reset (0 = minuit)
    daily_loss_warning_threshold: float = 0.7       # Alerte à 70% de la limite
    drawdown_reduction_enabled: bool = True         # Réduire taille positions après pertes
    drawdown_reduction_threshold: float = 0.5       # Réduire à 50% de la limite atteinte

    # ═══════════════════════════════════════════════════════════════
    # PAPER TRADING MODE (v8.0)
    # Simulation réaliste avec données de marché réelles
    # ═══════════════════════════════════════════════════════════════
    paper_trading_enabled: bool = False             # Mode paper trading (False = trading réel)
    paper_starting_capital: float = 1000.0          # Capital initial virtuel en USDC
    paper_strategy_mode: str = "both"               # Mode stratégie: "gabagool" | "smart_ape" | "both"

    # Allocation capital par stratégie en mode "both" (total = 100%)
    paper_gabagool_capital_pct: float = 60.0        # % capital alloué à Gabagool
    paper_smart_ape_capital_pct: float = 40.0       # % capital alloué à Smart Ape

    # Probabilités de fill (total = 100%)
    paper_immediate_fill_pct: float = 70.0          # % ordres fill instantané
    paper_delayed_fill_pct: float = 20.0            # % ordres fill en 1-5s
    paper_timeout_pct: float = 10.0                 # % ordres timeout/partiels

    # Modèle de slippage
    paper_base_slippage_bps: float = 5.0            # Slippage de base (0.05%)
    paper_size_impact_factor: float = 0.1           # Impact slippage par $100 de taille

    # Fichiers de données paper
    paper_trades_file: str = "data/paper_trades.json"
    paper_positions_file: str = "data/paper_positions.json"
    paper_stats_file: str = "data/paper_stats.json"

    # ═══════════════════════════════════════════════════════════════
    # MÉTHODES DE CALCUL CAPITAL (v7.1)
    # ═══════════════════════════════════════════════════════════════

    def get_gabagool_trade_size(self) -> float:
        """Calcule la taille de trade Gabagool basée sur le % du capital."""
        return (self.gabagool_capital_usd * self.gabagool_trade_percent) / 100.0

    def get_smart_ape_trade_size(self) -> float:
        """Calcule la taille de trade Smart Ape basée sur le % du capital."""
        return (self.smart_ape_capital_usd * self.smart_ape_trade_percent) / 100.0

    def get_gabagool_remaining_capital(self, current_exposure: float) -> float:
        """Retourne le capital Gabagool restant disponible."""
        return max(0, self.gabagool_capital_usd - current_exposure)

    def get_smart_ape_remaining_capital(self, current_exposure: float) -> float:
        """Retourne le capital Smart Ape restant disponible."""
        return max(0, self.smart_ape_capital_usd - current_exposure)

    def to_dict(self) -> dict:
        """Convertit en dictionnaire pour sauvegarde."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "TradingParams":
        """Crée une instance depuis un dictionnaire."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    def save(self, filepath: str = "config/trading_params.json") -> None:
        """Sauvegarde les paramètres dans un fichier JSON."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load(cls, filepath: str = "config/trading_params.json") -> "TradingParams":
        """Charge les paramètres depuis un fichier JSON."""
        path = Path(filepath)
        if path.exists():
            with open(path, "r") as f:
                data = json.load(f)
                return cls.from_dict(data)
        return cls()  # Valeurs par défaut
    
    def validate(self) -> list[str]:
        """Valide les paramètres et retourne les erreurs."""
        errors = []
        
        if self.min_spread < 0.01:
            errors.append("Spread minimum doit être >= 0.01$")
        if self.min_spread > self.max_spread:
            errors.append("Spread minimum doit être <= spread maximum")
        if self.capital_per_trade < 1:
            errors.append("Capital par trade doit être >= 1$")
        if self.max_open_positions < 1:
            errors.append("Positions max doit être >= 1")
        if self.capital_per_trade * self.max_open_positions > self.max_total_exposure:
            errors.append("Exposition totale risque d'être dépassée")
            
        return errors


# Instance globale avec chargement depuis fichier
_trading_params: Optional[TradingParams] = None


def get_trading_params() -> TradingParams:
    """Retourne l'instance des paramètres de trading."""
    global _trading_params
    if _trading_params is None:
        _trading_params = TradingParams.load()
    return _trading_params


def update_trading_params(params: TradingParams) -> None:
    """Met à jour et sauvegarde les paramètres."""
    global _trading_params
    _trading_params = params
    params.save()
