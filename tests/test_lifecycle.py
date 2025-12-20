"""
Tests pour le module lifecycle.

Vérifie:
- Métriques (calculs, persistance)
- Health check
"""

import pytest
from core.lifecycle import BotMetrics


# ═══════════════════════════════════════════════════════════════════════════
# TESTS MÉTRIQUES
# ═══════════════════════════════════════════════════════════════════════════

class TestBotMetrics:
    """Tests pour les métriques du bot."""

    def test_default_values(self):
        """Vérifie les valeurs par défaut."""
        metrics = BotMetrics()

        assert metrics.trades_executed == 0
        assert metrics.trades_success == 0
        assert metrics.trades_failed == 0
        assert metrics.total_profit_usd == 0.0
        assert metrics.errors_count == 0

    def test_avg_latency_zero_samples(self):
        """Vérifie la latence moyenne avec 0 samples."""
        metrics = BotMetrics()

        assert metrics.avg_latency_ms == 0.0

    def test_avg_latency_calculation(self):
        """Vérifie le calcul de la latence moyenne."""
        metrics = BotMetrics(
            total_latency_ms=500.0,
            latency_samples=5,
        )

        assert metrics.avg_latency_ms == 100.0

    def test_success_rate_zero_trades(self):
        """Vérifie le taux de succès avec 0 trades."""
        metrics = BotMetrics()

        assert metrics.success_rate == 0.0

    def test_success_rate_calculation(self):
        """Vérifie le calcul du taux de succès."""
        metrics = BotMetrics(
            trades_executed=100,
            trades_success=91,
        )

        assert metrics.success_rate == 91.0

    def test_success_rate_all_success(self):
        """Vérifie le taux de succès à 100%."""
        metrics = BotMetrics(
            trades_executed=50,
            trades_success=50,
        )

        assert metrics.success_rate == 100.0

    def test_success_rate_all_failed(self):
        """Vérifie le taux de succès à 0%."""
        metrics = BotMetrics(
            trades_executed=50,
            trades_success=0,
        )

        assert metrics.success_rate == 0.0

    def test_uptime_no_start_time(self):
        """Vérifie l'uptime sans start_time."""
        metrics = BotMetrics()

        assert metrics.uptime_seconds == 0.0

    def test_to_dict(self):
        """Vérifie la conversion en dictionnaire."""
        metrics = BotMetrics(
            trades_executed=100,
            trades_success=90,
            total_latency_ms=1000.0,
            latency_samples=10,
        )

        data = metrics.to_dict()

        assert "trades_executed" in data
        assert "avg_latency_ms" in data
        assert "success_rate" in data
        assert "uptime_hours" in data

        assert data["trades_executed"] == 100
        assert data["avg_latency_ms"] == 100.0
        assert data["success_rate"] == 90.0

    def test_to_dict_rounding(self):
        """Vérifie l'arrondi dans to_dict."""
        metrics = BotMetrics(
            total_latency_ms=333.333,
            latency_samples=1,
        )

        data = metrics.to_dict()

        # Arrondi à 2 décimales
        assert data["avg_latency_ms"] == 333.33


# ═══════════════════════════════════════════════════════════════════════════
# TESTS MÉTRIQUES TRADE
# ═══════════════════════════════════════════════════════════════════════════

class TestTradeMetrics:
    """Tests pour les métriques de trading."""

    def test_profit_accumulation(self):
        """Vérifie l'accumulation des profits."""
        metrics = BotMetrics()

        # Simuler des trades
        profits = [2.50, 3.00, -1.00, 4.50]
        total = sum(profits)

        metrics.total_profit_usd = total

        assert metrics.total_profit_usd == 9.0

    def test_volume_accumulation(self):
        """Vérifie l'accumulation du volume."""
        metrics = BotMetrics()

        volumes = [100.0, 200.0, 150.0]
        total = sum(volumes)

        metrics.total_volume_usd = total

        assert metrics.total_volume_usd == 450.0

    def test_trade_counters(self):
        """Vérifie les compteurs de trades."""
        metrics = BotMetrics(
            trades_executed=100,
            trades_success=85,
            trades_failed=10,
            trades_rejected=5,
        )

        # Total devrait correspondre
        assert metrics.trades_success + metrics.trades_failed + metrics.trades_rejected == 100

    def test_position_counters(self):
        """Vérifie les compteurs de positions."""
        metrics = BotMetrics(
            positions_opened=50,
            positions_closed=30,
            positions_locked=15,
        )

        # Positions actives = opened - closed
        active = metrics.positions_opened - metrics.positions_closed
        assert active == 20


# ═══════════════════════════════════════════════════════════════════════════
# TESTS ERREURS
# ═══════════════════════════════════════════════════════════════════════════

class TestErrorMetrics:
    """Tests pour les métriques d'erreurs."""

    def test_error_counting(self):
        """Vérifie le comptage des erreurs."""
        metrics = BotMetrics(errors_count=5)

        assert metrics.errors_count == 5

    def test_circuit_break_counting(self):
        """Vérifie le comptage des circuit breaks."""
        metrics = BotMetrics(circuit_breaks=2)

        assert metrics.circuit_breaks == 2

    def test_error_rate_calculation(self):
        """Calcule le taux d'erreur."""
        metrics = BotMetrics(
            trades_executed=100,
            trades_failed=10,
        )

        error_rate = (metrics.trades_failed / metrics.trades_executed) * 100

        assert error_rate == 10.0


# ═══════════════════════════════════════════════════════════════════════════
# TESTS SCÉNARIOS RÉALISTES
# ═══════════════════════════════════════════════════════════════════════════

class TestRealisticScenarios:
    """Tests avec des scénarios réalistes."""

    def test_typical_trading_session(self):
        """Simule une session de trading typique."""
        metrics = BotMetrics(
            trades_executed=156,
            trades_success=142,
            trades_failed=14,
            trades_rejected=0,
            total_profit_usd=127.50,
            total_volume_usd=3900.0,
            total_latency_ms=15600.0,
            latency_samples=156,
            positions_opened=50,
            positions_closed=47,
            positions_locked=35,
            errors_count=3,
            circuit_breaks=0,
        )

        # Vérifications
        assert metrics.success_rate == pytest.approx(91.03, rel=0.1)
        assert metrics.avg_latency_ms == 100.0  # 100ms moyenne
        assert metrics.total_profit_usd == 127.50

        # Positions actives = 50 - 47 = 3
        active_positions = metrics.positions_opened - metrics.positions_closed
        assert active_positions == 3

    def test_bad_trading_session(self):
        """Simule une mauvaise session de trading."""
        metrics = BotMetrics(
            trades_executed=50,
            trades_success=20,
            trades_failed=25,
            trades_rejected=5,
            total_profit_usd=-45.00,  # Perte!
            errors_count=15,
            circuit_breaks=3,
        )

        assert metrics.success_rate == 40.0
        assert metrics.total_profit_usd < 0
        assert metrics.circuit_breaks > 0

    def test_no_activity(self):
        """Simule une session sans activité."""
        metrics = BotMetrics()

        assert metrics.success_rate == 0.0
        assert metrics.avg_latency_ms == 0.0
        assert metrics.total_profit_usd == 0.0
        assert metrics.uptime_seconds == 0.0
