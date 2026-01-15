"""
Paper Trading Module - Apex Predator v8.0

Simulation de trading réaliste utilisant les données de marché réelles
mais avec exécution virtuelle des ordres.

Composants:
- PaperConfig: Configuration du mode paper
- FillSimulator: Simulation réaliste des fills
- PaperCapitalManager: Gestion du capital virtuel
- PaperTradeStore: Persistance des trades paper
- PaperExecutor: Executeur compatible avec OrderExecutor
- PaperReporter: Génération de rapports de performance
"""

from .config import (
    PaperConfig,
    get_paper_config,
    set_paper_config,
    setup_paper_trading_with_capital,
    get_optimized_paper_params,
)
from .fill_simulator import FillSimulator, FillResult, FillType
from .capital_manager import PaperCapitalManager
from .trade_store import PaperTradeStore, PaperTrade
from .paper_executor import PaperExecutor
from .reporter import PaperReporter

__all__ = [
    # Config
    "PaperConfig",
    "get_paper_config",
    "set_paper_config",
    "setup_paper_trading_with_capital",
    "get_optimized_paper_params",
    # Fill simulation
    "FillSimulator",
    "FillResult",
    "FillType",
    # Capital
    "PaperCapitalManager",
    # Storage
    "PaperTradeStore",
    "PaperTrade",
    # Executor
    "PaperExecutor",
    # Reporting
    "PaperReporter",
]
