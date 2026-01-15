"""
Paper Trading Configuration - Apex Predator v8.0

Configuration pour la simulation réaliste de paper trading.
Les paramètres contrôlent le comportement des fills simulés.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PaperConfig:
    """
    Configuration du mode paper trading.

    Paramètres de simulation:
    - Probabilités de fill (immediate/delayed/timeout)
    - Modèle de slippage
    - Capital virtuel initial
    - Frais Polymarket
    """

    # ═══════════════════════════════════════════════════════════════
    # PROBABILITÉS DE FILL (total = 100%)
    # Simule le comportement réel des ordres sur Polymarket
    # ═══════════════════════════════════════════════════════════════
    immediate_fill_pct: float = 70.0    # 70% des ordres fill instantanément
    delayed_fill_pct: float = 20.0      # 20% fill en 1-5 secondes
    timeout_pct: float = 10.0           # 10% timeout ou fill partiel

    # ═══════════════════════════════════════════════════════════════
    # TIMING DE SIMULATION
    # Délais réalistes pour les fills
    # ═══════════════════════════════════════════════════════════════
    min_fill_delay_ms: int = 50         # Latence réseau minimum (ms)
    max_immediate_delay_ms: int = 150   # Délai max pour fill "immédiat"
    max_delayed_delay_ms: int = 5000    # Délai max pour fill "différé"
    fill_timeout_seconds: float = 30.0  # Timeout avant abandon

    # ═══════════════════════════════════════════════════════════════
    # MODÈLE DE SLIPPAGE
    # slippage = base + (order_value / depth) × size_factor
    # ═══════════════════════════════════════════════════════════════
    base_slippage_bps: float = 5.0      # Slippage de base (0.05%)
    size_impact_factor: float = 0.1     # Impact additionnel par $100 de taille
    max_slippage_bps: float = 100.0     # Slippage maximum (1%)

    # ═══════════════════════════════════════════════════════════════
    # CONTRAINTES DE LIQUIDITÉ
    # Basé sur la profondeur du carnet d'ordres
    # ═══════════════════════════════════════════════════════════════
    min_depth_for_full_fill: float = 500.0   # Profondeur min pour fill complet ($)
    partial_fill_threshold: float = 0.30     # Fill si depth >= 30% de l'ordre

    # ═══════════════════════════════════════════════════════════════
    # REJECTION SCENARIOS
    # ═══════════════════════════════════════════════════════════════
    price_drift_rejection_bps: float = 50.0  # Rejeter si prix drift > 0.5%
    stale_orderbook_seconds: float = 5.0     # Orderbook considéré stale après 5s

    # ═══════════════════════════════════════════════════════════════
    # CAPITAL VIRTUEL
    # ═══════════════════════════════════════════════════════════════
    starting_capital: float = 1000.0    # Capital initial en USDC

    # ═══════════════════════════════════════════════════════════════
    # FRAIS (identique au trading réel)
    # ═══════════════════════════════════════════════════════════════
    polymarket_fee_rate: float = 0.02   # 2% de frais sur les gains

    # ═══════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════════
    trades_file: str = "data/paper_trades.json"
    positions_file: str = "data/paper_positions.json"
    stats_file: str = "data/paper_stats.json"

    # ═══════════════════════════════════════════════════════════════
    # MODE STRATÉGIE (v8.2)
    # Permet de tester les stratégies indépendamment ou ensemble
    # ═══════════════════════════════════════════════════════════════
    strategy_mode: str = "both"  # "gabagool" | "smart_ape" | "both"

    # Allocation capital par stratégie en mode "both" (total = 100%)
    gabagool_capital_pct: float = 60.0   # 60% à Gabagool
    smart_ape_capital_pct: float = 40.0  # 40% à Smart Ape

    def validate(self) -> list[str]:
        """Valide la cohérence des paramètres."""
        errors = []

        # Vérifier que les probabilités totalisent ~100%
        total_pct = self.immediate_fill_pct + self.delayed_fill_pct + self.timeout_pct
        if abs(total_pct - 100.0) > 0.1:
            errors.append(f"Fill probabilities must sum to 100%, got {total_pct}%")

        # Vérifier les bornes de slippage
        if self.base_slippage_bps < 0:
            errors.append("base_slippage_bps must be >= 0")
        if self.max_slippage_bps < self.base_slippage_bps:
            errors.append("max_slippage_bps must be >= base_slippage_bps")

        # Vérifier le capital
        if self.starting_capital <= 0:
            errors.append("starting_capital must be > 0")

        # Vérifier les frais
        if not (0 <= self.polymarket_fee_rate <= 1):
            errors.append("polymarket_fee_rate must be between 0 and 1")

        # Vérifier le mode stratégie
        if self.strategy_mode not in ("gabagool", "smart_ape", "both"):
            errors.append("strategy_mode must be 'gabagool', 'smart_ape', or 'both'")

        # Vérifier l'allocation en mode both
        if self.strategy_mode == "both":
            total_pct = self.gabagool_capital_pct + self.smart_ape_capital_pct
            if abs(total_pct - 100.0) > 0.1:
                errors.append(f"Capital allocation must sum to 100%, got {total_pct}%")

        return errors


# ═══════════════════════════════════════════════════════════════
# SINGLETON PATTERN
# ═══════════════════════════════════════════════════════════════

_paper_config: Optional[PaperConfig] = None


def get_paper_config() -> PaperConfig:
    """Retourne l'instance singleton de la configuration paper."""
    global _paper_config
    if _paper_config is None:
        _paper_config = PaperConfig()
    return _paper_config


def set_paper_config(config: PaperConfig) -> None:
    """Définit la configuration paper (pour tests ou config externe)."""
    global _paper_config
    _paper_config = config


def setup_paper_trading_with_capital(
    capital: float,
    strategy_mode: str = "both",
    use_kelly: bool = False,
    win_rate: float = None,
    avg_win: float = None,
    avg_loss: float = None,
) -> PaperConfig:
    """
    Configure le paper trading avec un capital donné et optimise les paramètres.

    Cette fonction:
    1. Crée un PaperConfig avec le capital spécifié
    2. Utilise CapitalOptimizer pour calculer les paramètres optimaux
    3. Applique ces paramètres aux TradingParams globaux
    4. Retourne le PaperConfig configuré

    Args:
        capital: Capital initial en USD pour le paper trading
        strategy_mode: Mode stratégie - "gabagool" | "smart_ape" | "both"
        use_kelly: Activer le dimensionnement Kelly basé sur l'historique
        win_rate: Taux de gain historique (0-1) pour Kelly
        avg_win: Gain moyen par trade gagnant pour Kelly
        avg_loss: Perte moyenne par trade perdant pour Kelly

    Returns:
        PaperConfig configuré

    Usage:
        # Configuration simple avec les deux stratégies
        config = setup_paper_trading_with_capital(500.0)

        # Gabagool uniquement
        config = setup_paper_trading_with_capital(500.0, strategy_mode="gabagool")

        # Smart Ape uniquement
        config = setup_paper_trading_with_capital(500.0, strategy_mode="smart_ape")

        # Avec Kelly criterion basé sur historique paper
        config = setup_paper_trading_with_capital(
            capital=1000.0,
            strategy_mode="both",
            use_kelly=True,
            win_rate=0.68,
            avg_win=2.50,
            avg_loss=1.20
        )
    """
    from core.capital_optimizer import CapitalOptimizer
    from config import get_trading_params, update_trading_params

    # Valider strategy_mode
    if strategy_mode not in ("gabagool", "smart_ape", "both"):
        raise ValueError(f"strategy_mode must be 'gabagool', 'smart_ape', or 'both', got '{strategy_mode}'")

    # 1. Créer l'optimizer
    optimizer = CapitalOptimizer(
        capital=capital,
        use_kelly=use_kelly,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
    )

    # 2. Calculer les paramètres optimaux avec le mode stratégie
    params = optimizer.calculate_optimal_params(strategy_mode=strategy_mode)

    # 3. Récupérer les TradingParams existants
    trading_params = get_trading_params()

    # Calculer allocations selon le mode
    if strategy_mode == "gabagool":
        gabagool_pct, smart_ape_pct = 100.0, 0.0
    elif strategy_mode == "smart_ape":
        gabagool_pct, smart_ape_pct = 0.0, 100.0
    else:
        gabagool_pct = trading_params.paper_gabagool_capital_pct
        smart_ape_pct = trading_params.paper_smart_ape_capital_pct

    # 4. Créer le PaperConfig avec le capital et le mode stratégie
    config = PaperConfig(
        starting_capital=capital,
        strategy_mode=strategy_mode,
        gabagool_capital_pct=gabagool_pct,
        smart_ape_capital_pct=smart_ape_pct,
        immediate_fill_pct=trading_params.paper_immediate_fill_pct,
        delayed_fill_pct=trading_params.paper_delayed_fill_pct,
        timeout_pct=trading_params.paper_timeout_pct,
        base_slippage_bps=trading_params.paper_base_slippage_bps,
        size_impact_factor=trading_params.paper_size_impact_factor,
    )

    # 5. Appliquer les paramètres de capital aux TradingParams
    trading_params.paper_trading_enabled = True
    trading_params.paper_starting_capital = capital
    trading_params.paper_strategy_mode = strategy_mode
    trading_params.paper_gabagool_capital_pct = gabagool_pct
    trading_params.paper_smart_ape_capital_pct = smart_ape_pct

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

    # 6. Sauvegarder
    update_trading_params(trading_params)

    # 7. Définir comme config active
    set_paper_config(config)

    # 8. Afficher la configuration
    mode_label = {"gabagool": "GABAGOOL ONLY", "smart_ape": "SMART APE ONLY", "both": "BOTH STRATEGIES"}
    print(f"📝 Paper Trading configuré pour ${capital:.2f}")
    print(f"   Mode: {mode_label[strategy_mode]}")
    print(f"   Tier: {params.tier_label.upper()}")

    if strategy_mode in ("gabagool", "both"):
        print(f"   Gabagool: ${params.gabagool_trade_size_usd:.2f}/trade, {params.gabagool_max_positions} positions max (${params.gabagool_capital_usd:.2f} capital)")

    if strategy_mode in ("smart_ape", "both"):
        print(f"   Smart Ape: ${params.smart_ape_trade_size_usd:.2f}/trade, {params.smart_ape_max_positions} positions max (${params.smart_ape_capital_usd:.2f} capital)")

    print(f"   Max Daily Loss: ${params.max_daily_loss_usd:.2f} ({params.max_daily_loss_percent:.1f}%)")

    return config


def get_optimized_paper_params(capital: float = None, strategy_mode: str = None) -> dict:
    """
    Retourne les paramètres optimisés pour un capital paper donné.

    Si capital n'est pas fourni, utilise le capital actuel du paper config.
    Si strategy_mode n'est pas fourni, utilise le mode actuel du paper config.

    Args:
        capital: Capital en USD (défaut: capital du paper config actuel)
        strategy_mode: Mode stratégie - "gabagool" | "smart_ape" | "both"

    Returns:
        Dict avec tous les paramètres optimisés et leur explication.
    """
    from core.capital_optimizer import CapitalOptimizer

    paper_config = get_paper_config()

    if capital is None:
        capital = paper_config.starting_capital

    if strategy_mode is None:
        strategy_mode = paper_config.strategy_mode

    optimizer = CapitalOptimizer(capital=capital)
    params = optimizer.calculate_optimal_params(strategy_mode=strategy_mode)

    return {
        "capital": capital,
        "strategy_mode": strategy_mode,
        "tier": params.tier_label,
        "summary": optimizer.get_summary(),
        "params": params.to_dict(),
    }
