"""
Core Module - Logique principale du Bot HFT
"""

from .scanner import MarketScanner, MarketData
from .analyzer import OpportunityAnalyzer, Opportunity
from .order_manager import OrderManager
from .trade_manager import TradeManager, Trade, TradeStatus, TradeSide, CloseReason
from .market_maker import MarketMaker, MMConfig, MMPosition, MMStatus
from .gabagool import GabagoolEngine, GabagoolConfig, GabagoolPosition, GabagoolStatus
from .order_queue import (
    OrderQueue,
    QueuedOrder,
    QueueOrderStatus,
    OrderPriority,
    QueueStats,
    OrderQueueManager,
    order_queue_manager,
)
from .executor import OrderExecutor
from .performance import (
    setup_uvloop,
    json_dumps,
    json_loads,
    MarketCache,
    orderbook_cache,
    market_cache,
    get_performance_status,
)
from .speculative_engine import SpeculativeEngine, SpeculativeOrder
from .local_orderbook import LocalOrderbook, OrderbookManager

__all__ = [
    "MarketScanner",
    "MarketData",
    "OpportunityAnalyzer",
    "Opportunity",
    "OrderManager",
    "OrderExecutor",  # Added
    "TradeManager",
    "Trade",
    "TradeStatus",
    "TradeSide",
    "CloseReason",
    # Market Maker
    "MarketMaker",
    "MMConfig",
    "MMPosition",
    "MMStatus",
    # Gabagool
    "GabagoolEngine",
    "GabagoolConfig",
    "GabagoolPosition",
    "GabagoolStatus",
    # Order Queue (4.1)
    "OrderQueue",
    "QueuedOrder",
    "QueueOrderStatus",
    "OrderPriority",
    "QueueStats",
    "OrderQueueManager",
    "order_queue_manager",
    # Performance
    "setup_uvloop",
    "json_dumps",
    "json_loads",
    "MarketCache",
    "orderbook_cache",
    "market_cache",
    "get_performance_status",
    # Speculative Engine (HFT)
    "SpeculativeEngine",
    "SpeculativeOrder",
    # Local Orderbook Mirror (HFT)
    "LocalOrderbook",
    "OrderbookManager",
]
