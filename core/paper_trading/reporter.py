"""
Paper Trading Reporter - Apex Predator v8.0

Génère des rapports de performance détaillés pour le paper trading.
Inclut métriques de risque, statistiques de fills, et breakdown par stratégie.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from .config import PaperConfig, get_paper_config
from .capital_manager import PaperCapitalManager
from .trade_store import PaperTradeStore, PaperTrade


@dataclass
class PerformanceMetrics:
    """Métriques de performance calculées."""
    # Basic stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    # P&L
    gross_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    net_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    avg_winning_trade: float = 0.0
    avg_losing_trade: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0

    # Risk metrics
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    # Fill statistics
    full_fill_rate: float = 0.0
    partial_fill_rate: float = 0.0
    rejection_rate: float = 0.0
    avg_slippage_bps: float = 0.0
    avg_fill_delay_ms: float = 0.0

    # Timing
    avg_trade_duration_seconds: float = 0.0
    trades_per_hour: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate * 100, 2),
            "gross_pnl": round(self.gross_pnl, 2),
            "total_fees": round(self.total_fees, 2),
            "total_slippage": round(self.total_slippage, 2),
            "net_pnl": round(self.net_pnl, 2),
            "avg_pnl_per_trade": round(self.avg_pnl_per_trade, 2),
            "avg_winning_trade": round(self.avg_winning_trade, 2),
            "avg_losing_trade": round(self.avg_losing_trade, 2),
            "largest_win": round(self.largest_win, 2),
            "largest_loss": round(self.largest_loss, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy": round(self.expectancy, 2),
            "full_fill_rate": round(self.full_fill_rate * 100, 1),
            "partial_fill_rate": round(self.partial_fill_rate * 100, 1),
            "rejection_rate": round(self.rejection_rate * 100, 1),
            "avg_slippage_bps": round(self.avg_slippage_bps, 2),
            "avg_fill_delay_ms": round(self.avg_fill_delay_ms, 0),
            "avg_trade_duration_seconds": round(self.avg_trade_duration_seconds, 0),
            "trades_per_hour": round(self.trades_per_hour, 2),
        }


@dataclass
class StrategyBreakdown:
    """Breakdown des performances par stratégie (v8.2 enrichi)."""
    strategy: str
    enabled: bool = True
    trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    avg_pnl: float = 0.0
    avg_winning_trade: float = 0.0
    avg_losing_trade: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    roi_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    # Capital allocation (v8.2)
    starting_capital: float = 0.0
    current_balance: float = 0.0
    capital_allocated: float = 0.0
    total_equity: float = 0.0
    capital_used_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "enabled": self.enabled,
            "trades": self.trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate * 100, 1),
            "gross_pnl": round(self.gross_pnl, 2),
            "total_fees": round(self.total_fees, 2),
            "total_slippage": round(self.total_slippage, 2),
            "net_pnl": round(self.net_pnl, 2),
            "avg_pnl": round(self.avg_pnl, 2),
            "avg_winning_trade": round(self.avg_winning_trade, 2),
            "avg_losing_trade": round(self.avg_losing_trade, 2),
            "largest_win": round(self.largest_win, 2),
            "largest_loss": round(self.largest_loss, 2),
            "roi_pct": round(self.roi_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy": round(self.expectancy, 2),
            "starting_capital": round(self.starting_capital, 2),
            "current_balance": round(self.current_balance, 2),
            "capital_allocated": round(self.capital_allocated, 2),
            "total_equity": round(self.total_equity, 2),
            "capital_used_pct": round(self.capital_used_pct, 2),
        }


class PaperReporter:
    """
    Génère des rapports de performance pour le paper trading.

    Usage:
        reporter = PaperReporter(trade_store, capital_manager)
        metrics = reporter.calculate_metrics()
        print(reporter.generate_summary())
    """

    def __init__(
        self,
        trade_store: PaperTradeStore,
        capital_manager: PaperCapitalManager,
        config: Optional[PaperConfig] = None
    ):
        self.trade_store = trade_store
        self.capital_manager = capital_manager
        self.config = config or get_paper_config()

    def calculate_metrics(self, trades: Optional[List[PaperTrade]] = None) -> PerformanceMetrics:
        """Calcule toutes les métriques de performance."""
        if trades is None:
            trades = self.trade_store.get_closed_trades()

        metrics = PerformanceMetrics()

        if not trades:
            return metrics

        # Basic counts
        metrics.total_trades = len(trades)
        winning = [t for t in trades if t.net_pnl > 0]
        losing = [t for t in trades if t.net_pnl <= 0]
        metrics.winning_trades = len(winning)
        metrics.losing_trades = len(losing)
        metrics.win_rate = len(winning) / len(trades) if trades else 0.0

        # P&L
        metrics.gross_pnl = sum(t.gross_pnl for t in trades)
        metrics.total_fees = sum(t.fees for t in trades)
        metrics.total_slippage = sum(t.slippage_cost for t in trades)
        metrics.net_pnl = sum(t.net_pnl for t in trades)
        metrics.avg_pnl_per_trade = metrics.net_pnl / len(trades)

        if winning:
            metrics.avg_winning_trade = sum(t.net_pnl for t in winning) / len(winning)
            metrics.largest_win = max(t.net_pnl for t in winning)
        if losing:
            metrics.avg_losing_trade = sum(t.net_pnl for t in losing) / len(losing)
            metrics.largest_loss = min(t.net_pnl for t in losing)

        # Risk metrics
        metrics.sharpe_ratio = self._calculate_sharpe_ratio(trades)
        metrics.max_drawdown, metrics.max_drawdown_pct = self._calculate_max_drawdown(trades)
        metrics.profit_factor = self._calculate_profit_factor(trades)
        metrics.expectancy = self._calculate_expectancy(metrics)

        # Fill statistics
        metrics.full_fill_rate, metrics.partial_fill_rate, metrics.rejection_rate = \
            self._calculate_fill_rates(trades)
        metrics.avg_slippage_bps = self._calculate_avg_slippage(trades)
        metrics.avg_fill_delay_ms = self._calculate_avg_fill_delay(trades)

        # Timing
        durations = [t.duration_seconds for t in trades if t.duration_seconds > 0]
        if durations:
            metrics.avg_trade_duration_seconds = sum(durations) / len(durations)

        if trades:
            first_trade = min(t.entry_time for t in trades if t.entry_time)
            last_trade = max(t.exit_time for t in trades if t.exit_time)
            if first_trade and last_trade:
                hours = (last_trade - first_trade).total_seconds() / 3600
                if hours > 0:
                    metrics.trades_per_hour = len(trades) / hours

        return metrics

    def _calculate_sharpe_ratio(
        self,
        trades: List[PaperTrade],
        risk_free_rate: float = 0.0
    ) -> float:
        """Calcule le Sharpe Ratio."""
        if len(trades) < 2:
            return 0.0

        returns = [t.net_pnl for t in trades]
        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance) if variance > 0 else 0.001

        # Annualisé (assumant 24 trades/jour, 365 jours/an)
        daily_sharpe = (avg_return - risk_free_rate) / std_dev
        annualized_sharpe = daily_sharpe * math.sqrt(365 * 24)

        return annualized_sharpe

    def _calculate_max_drawdown(self, trades: List[PaperTrade]) -> tuple[float, float]:
        """Calcule le drawdown maximum."""
        if not trades:
            return 0.0, 0.0

        # Trier par temps
        sorted_trades = sorted(trades, key=lambda t: t.exit_time or datetime.min)

        cumulative_pnl = 0.0
        peak = 0.0
        max_dd = 0.0

        starting_capital = self.config.starting_capital

        for trade in sorted_trades:
            cumulative_pnl += trade.net_pnl
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = (max_dd / starting_capital * 100) if starting_capital > 0 else 0.0

        return max_dd, max_dd_pct

    def _calculate_profit_factor(self, trades: List[PaperTrade]) -> float:
        """Calcule le profit factor (gross wins / gross losses)."""
        gross_wins = sum(t.net_pnl for t in trades if t.net_pnl > 0)
        gross_losses = abs(sum(t.net_pnl for t in trades if t.net_pnl < 0))

        if gross_losses == 0:
            return float('inf') if gross_wins > 0 else 0.0

        return gross_wins / gross_losses

    def _calculate_expectancy(self, metrics: PerformanceMetrics) -> float:
        """Calcule l'expectancy (avg win * win rate - avg loss * loss rate)."""
        if metrics.total_trades == 0:
            return 0.0

        win_rate = metrics.win_rate
        loss_rate = 1 - win_rate

        return (metrics.avg_winning_trade * win_rate) + (metrics.avg_losing_trade * loss_rate)

    def _calculate_fill_rates(self, trades: List[PaperTrade]) -> tuple[float, float, float]:
        """Calcule les taux de fill."""
        all_orders = []
        for trade in trades:
            all_orders.extend(trade.entry_orders)
            all_orders.extend(trade.exit_orders)

        if not all_orders:
            return 0.0, 0.0, 0.0

        full = sum(1 for o in all_orders if o.fill_type == "full")
        partial = sum(1 for o in all_orders if o.fill_type == "partial")
        rejected = sum(1 for o in all_orders if o.fill_type == "rejected")

        total = len(all_orders)
        return full / total, partial / total, rejected / total

    def _calculate_avg_slippage(self, trades: List[PaperTrade]) -> float:
        """Calcule le slippage moyen en bps."""
        slippages = [t.avg_slippage_bps for t in trades if t.avg_slippage_bps > 0]
        return sum(slippages) / len(slippages) if slippages else 0.0

    def _calculate_avg_fill_delay(self, trades: List[PaperTrade]) -> float:
        """Calcule le délai de fill moyen en ms."""
        delays = [t.avg_fill_delay_ms for t in trades if t.avg_fill_delay_ms > 0]
        return sum(delays) / len(delays) if delays else 0.0

    def get_strategy_breakdown(self) -> Dict[str, StrategyBreakdown]:
        """Retourne le breakdown enrichi par stratégie avec capital tracking (v8.2)."""
        trades = self.trade_store.get_closed_trades()
        breakdowns = {}

        for strategy in ["gabagool", "smart_ape"]:
            strat_trades = [t for t in trades if t.strategy == strategy]
            winning = [t for t in strat_trades if t.net_pnl > 0]
            losing = [t for t in strat_trades if t.net_pnl <= 0]

            # Récupérer les stats de capital depuis le manager
            capital_stats = self.capital_manager.get_strategy_stats(strategy)

            # Calculer métriques avancées
            sharpe = self._calculate_sharpe_ratio(strat_trades) if strat_trades else 0.0
            max_dd, max_dd_pct = self._calculate_max_drawdown_for_strategy(strat_trades, capital_stats["starting_capital"])
            profit_factor = self._calculate_profit_factor(strat_trades) if strat_trades else 0.0

            # Calculer expectancy
            win_rate = len(winning) / len(strat_trades) if strat_trades else 0.0
            avg_win = sum(t.net_pnl for t in winning) / len(winning) if winning else 0.0
            avg_loss = sum(t.net_pnl for t in losing) / len(losing) if losing else 0.0
            expectancy = (avg_win * win_rate) + (avg_loss * (1 - win_rate))

            breakdown = StrategyBreakdown(
                strategy=strategy,
                enabled=capital_stats["enabled"],
                trades=len(strat_trades),
                winning_trades=len(winning),
                losing_trades=len(losing),
                win_rate=win_rate,
                gross_pnl=sum(t.gross_pnl for t in strat_trades),
                total_fees=sum(t.fees for t in strat_trades),
                total_slippage=sum(t.slippage_cost for t in strat_trades),
                net_pnl=sum(t.net_pnl for t in strat_trades),
                avg_pnl=sum(t.net_pnl for t in strat_trades) / len(strat_trades) if strat_trades else 0.0,
                avg_winning_trade=avg_win,
                avg_losing_trade=avg_loss,
                largest_win=max((t.net_pnl for t in winning), default=0.0),
                largest_loss=min((t.net_pnl for t in losing), default=0.0),
                roi_pct=capital_stats["return_pct"],
                sharpe_ratio=sharpe,
                max_drawdown=max_dd,
                max_drawdown_pct=max_dd_pct,
                profit_factor=profit_factor,
                expectancy=expectancy,
                # Capital (v8.2)
                starting_capital=capital_stats["starting_capital"],
                current_balance=capital_stats["balance"],
                capital_allocated=capital_stats["allocated"],
                total_equity=capital_stats["total_equity"],
                capital_used_pct=(capital_stats["allocated"] / capital_stats["starting_capital"] * 100) if capital_stats["starting_capital"] > 0 else 0.0,
            )

            breakdowns[strategy] = breakdown

        return breakdowns

    def _calculate_max_drawdown_for_strategy(
        self,
        trades: List[PaperTrade],
        starting_capital: float
    ) -> tuple[float, float]:
        """Calcule le drawdown maximum pour une liste de trades."""
        if not trades:
            return 0.0, 0.0

        # Trier par temps
        sorted_trades = sorted(trades, key=lambda t: t.exit_time or datetime.min)

        cumulative_pnl = 0.0
        peak = 0.0
        max_dd = 0.0

        for trade in sorted_trades:
            cumulative_pnl += trade.net_pnl
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = (max_dd / starting_capital * 100) if starting_capital > 0 else 0.0

        return max_dd, max_dd_pct

    def generate_summary(self) -> str:
        """Génère un résumé textuel des performances."""
        metrics = self.calculate_metrics()
        capital_stats = self.capital_manager.get_stats()
        breakdowns = self.get_strategy_breakdown()

        starting = capital_stats["starting_capital"]
        current = capital_stats["total_equity"]
        return_pct = capital_stats["total_return_pct"]

        lines = [
            "",
            "═" * 56,
            "📝 PAPER TRADING SUMMARY",
            "═" * 56,
            "",
            f"Capital: ${starting:.2f} → ${current:.2f} ({return_pct:+.2f}%)",
            "",
            "PERFORMANCE",
            f"  Trades:     {metrics.total_trades} ({metrics.winning_trades}W / {metrics.losing_trades}L)",
            f"  Win Rate:   {metrics.win_rate * 100:.1f}%",
            f"  Net P&L:    ${metrics.net_pnl:+.2f}",
            f"  Fees:       -${metrics.total_fees:.2f}",
            f"  Slippage:   -${metrics.total_slippage:.2f}",
            "",
            "RISK",
            f"  Sharpe:     {metrics.sharpe_ratio:.2f}",
            f"  Max DD:     -${metrics.max_drawdown:.2f} ({metrics.max_drawdown_pct:.1f}%)",
            f"  Profit F:   {metrics.profit_factor:.2f}",
            "",
            "FILLS",
            f"  Full:       {metrics.full_fill_rate * 100:.0f}%",
            f"  Partial:    {metrics.partial_fill_rate * 100:.0f}%",
            f"  Reject:     {metrics.rejection_rate * 100:.0f}%",
            f"  Avg Slip:   {metrics.avg_slippage_bps:.1f} bps",
            "",
            "STRATEGIES",
        ]

        for name, breakdown in breakdowns.items():
            if breakdown.trades > 0:
                lines.append(
                    f"  {name.capitalize()}: ${breakdown.net_pnl:+.2f} ({breakdown.trades} trades, {breakdown.win_rate * 100:.0f}% win)"
                )

        lines.extend([
            "",
            "═" * 56,
        ])

        return "\n".join(lines)

    def generate_report(self) -> Dict[str, Any]:
        """Génère un rapport complet en format JSON."""
        metrics = self.calculate_metrics()
        capital_stats = self.capital_manager.get_stats()
        breakdowns = self.get_strategy_breakdown()
        recent_trades = self.trade_store.get_recent_trades(20)

        return {
            "generated_at": datetime.now().isoformat(),
            "capital": capital_stats,
            "metrics": metrics.to_dict(),
            "strategies": {k: v.to_dict() for k, v in breakdowns.items()},
            "recent_trades": [t.to_dict() for t in recent_trades],
        }

    def print_summary(self) -> None:
        """Affiche le résumé dans la console."""
        print(self.generate_summary())

    def generate_strategy_report(self, strategy: str) -> str:
        """Génère un rapport détaillé pour une stratégie spécifique (v8.2)."""
        breakdowns = self.get_strategy_breakdown()
        if strategy not in breakdowns:
            return f"Strategy '{strategy}' not found"

        b = breakdowns[strategy]

        status = "ENABLED" if b.enabled else "DISABLED"

        lines = [
            "",
            "═" * 60,
            f"📝 {strategy.upper()} STRATEGY REPORT [{status}]",
            "═" * 60,
            "",
            f"Capital: ${b.starting_capital:.2f} → ${b.total_equity:.2f} ({b.roi_pct:+.2f}%)",
            f"Balance: ${b.current_balance:.2f} | Allocated: ${b.capital_allocated:.2f}",
            "",
            "PERFORMANCE",
            f"  Trades:     {b.trades} ({b.winning_trades}W / {b.losing_trades}L)",
            f"  Win Rate:   {b.win_rate * 100:.1f}%",
            f"  Net P&L:    ${b.net_pnl:+.2f}",
            f"  Avg Trade:  ${b.avg_pnl:+.2f}",
            f"  Expectancy: ${b.expectancy:+.2f}",
            "",
            "RISK METRICS",
            f"  Sharpe:     {b.sharpe_ratio:.2f}",
            f"  Max DD:     -${b.max_drawdown:.2f} ({b.max_drawdown_pct:.1f}%)",
            f"  Profit F:   {b.profit_factor:.2f}",
            "",
            "TRADE STATS",
            f"  Avg Win:    ${b.avg_winning_trade:+.2f}",
            f"  Avg Loss:   ${b.avg_losing_trade:+.2f}",
            f"  Best:       ${b.largest_win:+.2f}",
            f"  Worst:      ${b.largest_loss:+.2f}",
            "",
            "COSTS",
            f"  Fees:       -${b.total_fees:.2f}",
            f"  Slippage:   -${b.total_slippage:.2f}",
            "",
            "═" * 60,
        ]

        return "\n".join(lines)

    def generate_dual_strategy_summary(self) -> str:
        """Génère un résumé comparatif des deux stratégies (v8.2)."""
        breakdowns = self.get_strategy_breakdown()
        capital_stats = self.capital_manager.get_stats()

        gab = breakdowns.get("gabagool", StrategyBreakdown(strategy="gabagool"))
        sa = breakdowns.get("smart_ape", StrategyBreakdown(strategy="smart_ape"))

        mode = capital_stats.get("strategy_mode", "both")
        total_equity = capital_stats.get("total_equity", 0)
        starting = capital_stats.get("starting_capital", 0)
        return_pct = ((total_equity - starting) / starting * 100) if starting > 0 else 0

        lines = [
            "",
            "═" * 72,
            f"📝 PAPER TRADING SUMMARY - MODE: {mode.upper()}",
            "═" * 72,
            "",
            f"GLOBAL: ${starting:.2f} → ${total_equity:.2f} ({return_pct:+.2f}%)",
            "",
            "┌" + "─" * 34 + "┬" + "─" * 34 + "┐",
            f"│ {'GABAGOOL':^32} │ {'SMART APE':^32} │",
            "├" + "─" * 34 + "┼" + "─" * 34 + "┤",
        ]

        # Comparer les métriques
        def fmt_row(label: str, gab_val: str, sa_val: str) -> str:
            return f"│ {label}: {gab_val:<23} │ {label}: {sa_val:<23} │"

        gab_status = "ON" if gab.enabled else "OFF"
        sa_status = "ON" if sa.enabled else "OFF"
        lines.append(fmt_row("Status", gab_status, sa_status))

        lines.append(fmt_row("Capital", f"${gab.starting_capital:.0f}→${gab.total_equity:.0f}", f"${sa.starting_capital:.0f}→${sa.total_equity:.0f}"))
        lines.append(fmt_row("Return", f"{gab.roi_pct:+.1f}%", f"{sa.roi_pct:+.1f}%"))
        lines.append(fmt_row("Trades", f"{gab.trades} ({gab.winning_trades}W/{gab.losing_trades}L)", f"{sa.trades} ({sa.winning_trades}W/{sa.losing_trades}L)"))
        lines.append(fmt_row("Win Rate", f"{gab.win_rate*100:.1f}%", f"{sa.win_rate*100:.1f}%"))
        lines.append(fmt_row("Net P&L", f"${gab.net_pnl:+.2f}", f"${sa.net_pnl:+.2f}"))
        lines.append(fmt_row("Sharpe", f"{gab.sharpe_ratio:.2f}", f"{sa.sharpe_ratio:.2f}"))
        lines.append(fmt_row("Max DD", f"-{gab.max_drawdown_pct:.1f}%", f"-{sa.max_drawdown_pct:.1f}%"))

        lines.extend([
            "└" + "─" * 34 + "┴" + "─" * 34 + "┘",
            "",
            "═" * 72,
        ])

        return "\n".join(lines)
