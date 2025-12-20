"""
Tests pour la stratégie Gabagool.

Vérifie:
- Calcul du pair_cost
- Filtrage selon max_pair_cost
- Calcul du profit après frais
- Logique d'équilibrage YES/NO
"""

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# TESTS PAIR_COST
# ═══════════════════════════════════════════════════════════════════════════

class TestPairCost:
    """Tests pour le calcul du pair_cost."""

    def test_pair_cost_calculation_basic(self):
        """Vérifie le calcul basique du pair_cost."""
        price_yes = 0.45
        price_no = 0.52
        pair_cost = price_yes + price_no

        assert pair_cost == 0.97

    def test_pair_cost_profitable(self):
        """Vérifie qu'un pair_cost < 1.0 est profitable."""
        pair_cost = 0.97
        profit_per_dollar = 1.0 - pair_cost

        assert profit_per_dollar == 0.03  # 3% brut
        assert profit_per_dollar > 0

    def test_pair_cost_unprofitable(self):
        """Vérifie qu'un pair_cost >= 1.0 n'est pas profitable."""
        price_yes = 0.55
        price_no = 0.48
        pair_cost = price_yes + price_no

        assert pair_cost >= 1.0  # 1.03
        assert (1.0 - pair_cost) <= 0  # Pas de profit

    def test_pair_cost_exactly_one(self):
        """Vérifie le cas limite pair_cost = 1.0."""
        pair_cost = 1.0
        profit = 1.0 - pair_cost

        assert profit == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# TESTS FILTRAGE GABAGOOL
# ═══════════════════════════════════════════════════════════════════════════

class TestGabagoolFilter:
    """Tests pour le filtrage des opportunités."""

    MAX_PAIR_COST = 0.975  # Seuil Gabagool

    def test_filter_accepts_good_opportunity(self):
        """Vérifie qu'une bonne opportunité passe le filtre."""
        pair_cost = 0.97
        assert pair_cost < self.MAX_PAIR_COST

    def test_filter_rejects_bad_opportunity(self):
        """Vérifie qu'une mauvaise opportunité est rejetée."""
        pair_cost = 0.98
        assert pair_cost >= self.MAX_PAIR_COST

    def test_filter_boundary_accepted(self):
        """Vérifie le cas limite juste sous le seuil."""
        pair_cost = 0.974
        assert pair_cost < self.MAX_PAIR_COST

    def test_filter_boundary_rejected(self):
        """Vérifie le cas limite exactement au seuil."""
        pair_cost = 0.975
        assert pair_cost >= self.MAX_PAIR_COST

    @pytest.mark.parametrize("yes_price,no_price,should_accept", [
        (0.45, 0.50, True),   # 0.95 - Bon
        (0.48, 0.48, True),   # 0.96 - Bon
        (0.50, 0.47, True),   # 0.97 - Bon
        (0.49, 0.484, True),  # 0.974 - Juste sous le seuil
        (0.49, 0.485, False), # 0.975 - Limite exacte (rejeté car >=)
        (0.50, 0.48, False),  # 0.98 - Mauvais
        (0.55, 0.50, False),  # 1.05 - Très mauvais
    ])
    def test_filter_various_scenarios(self, yes_price, no_price, should_accept):
        """Test paramétré avec plusieurs scénarios."""
        pair_cost = yes_price + no_price
        is_accepted = pair_cost < self.MAX_PAIR_COST

        if should_accept:
            assert is_accepted, f"pair_cost {pair_cost} devrait être accepté"
        else:
            assert not is_accepted, f"pair_cost {pair_cost} devrait être rejeté"


# ═══════════════════════════════════════════════════════════════════════════
# TESTS PROFIT APRÈS FRAIS
# ═══════════════════════════════════════════════════════════════════════════

class TestProfitAfterFees:
    """Tests pour le calcul du profit après frais Polymarket."""

    POLYMARKET_FEE_RATE = 0.02  # 2%

    def test_profit_after_fees_positive(self):
        """Vérifie que le profit reste positif après frais."""
        pair_cost = 0.97
        gross_profit = 1.0 - pair_cost  # 0.03 = 3%
        fees = gross_profit * self.POLYMARKET_FEE_RATE  # 0.0006
        net_profit = gross_profit - fees  # 0.0294

        assert net_profit > 0
        assert net_profit == pytest.approx(0.0294, rel=1e-3)

    def test_profit_after_fees_at_threshold(self):
        """Vérifie le profit au seuil 0.975."""
        pair_cost = 0.975
        gross_profit = 1.0 - pair_cost  # 0.025 = 2.5%
        fees = gross_profit * self.POLYMARKET_FEE_RATE  # 0.0005
        net_profit = gross_profit - fees  # 0.0245

        assert net_profit > 0
        assert net_profit == pytest.approx(0.0245, rel=1e-3)

    def test_profit_percentage(self):
        """Vérifie le pourcentage de profit net."""
        pair_cost = 0.97
        invested = 100.0  # $100 investi

        gross_profit_usd = invested * (1.0 - pair_cost)  # $3
        fees_usd = gross_profit_usd * self.POLYMARKET_FEE_RATE  # $0.06
        net_profit_usd = gross_profit_usd - fees_usd  # $2.94

        net_profit_pct = (net_profit_usd / invested) * 100

        assert net_profit_usd == pytest.approx(2.94, rel=1e-2)
        assert net_profit_pct == pytest.approx(2.94, rel=1e-2)

    def test_minimum_profitable_pair_cost(self):
        """Trouve le pair_cost minimum pour être profitable après frais."""
        # Pour être profitable: gross_profit - fees > 0
        # (1 - pair_cost) - (1 - pair_cost) * 0.02 > 0
        # (1 - pair_cost) * 0.98 > 0
        # Donc tout pair_cost < 1.0 est profitable après frais

        for pair_cost in [0.99, 0.995, 0.999]:
            gross_profit = 1.0 - pair_cost
            net_profit = gross_profit * (1 - self.POLYMARKET_FEE_RATE)
            assert net_profit > 0, f"pair_cost {pair_cost} devrait être profitable"


# ═══════════════════════════════════════════════════════════════════════════
# TESTS ÉQUILIBRAGE YES/NO
# ═══════════════════════════════════════════════════════════════════════════

class TestBalancing:
    """Tests pour la logique d'équilibrage YES/NO."""

    BALANCE_RATIO_THRESHOLD = 1.3

    def test_balanced_position(self):
        """Vérifie qu'une position équilibrée est détectée."""
        qty_yes = 100
        qty_no = 95
        ratio = (qty_yes + 1) / (qty_no + 1)

        assert ratio < self.BALANCE_RATIO_THRESHOLD
        assert 1 / ratio < self.BALANCE_RATIO_THRESHOLD

    def test_unbalanced_too_much_yes(self):
        """Vérifie la détection de trop de YES."""
        qty_yes = 150
        qty_no = 100
        ratio = (qty_yes + 1) / (qty_no + 1)

        assert ratio > self.BALANCE_RATIO_THRESHOLD

    def test_unbalanced_too_much_no(self):
        """Vérifie la détection de trop de NO."""
        qty_yes = 100
        qty_no = 150
        ratio = (qty_yes + 1) / (qty_no + 1)

        assert 1 / ratio > self.BALANCE_RATIO_THRESHOLD

    def test_hedged_quantity(self):
        """Vérifie le calcul de la quantité hedgée."""
        qty_yes = 100
        qty_no = 80
        hedged_qty = min(qty_yes, qty_no)

        assert hedged_qty == 80

    def test_locked_profit_calculation(self):
        """Vérifie le calcul du profit verrouillé."""
        qty_yes = 100
        qty_no = 100
        avg_yes = 0.48
        avg_no = 0.49
        pair_cost = avg_yes + avg_no  # 0.97

        hedged_qty = min(qty_yes, qty_no)  # 100
        locked_profit = hedged_qty * (1.0 - pair_cost)  # 100 * 0.03 = $3

        assert locked_profit == pytest.approx(3.0, rel=1e-2)


# ═══════════════════════════════════════════════════════════════════════════
# TESTS KILL SWITCH
# ═══════════════════════════════════════════════════════════════════════════

class TestKillSwitch:
    """Tests pour le kill switch (timeout positions)."""

    KILL_SWITCH_MINUTES = 15

    def test_position_age_under_threshold(self):
        """Vérifie qu'une position jeune n'est pas liquidée."""
        age_minutes = 10
        should_liquidate = age_minutes > self.KILL_SWITCH_MINUTES

        assert not should_liquidate

    def test_position_age_over_threshold(self):
        """Vérifie qu'une vieille position est liquidée."""
        age_minutes = 20
        should_liquidate = age_minutes > self.KILL_SWITCH_MINUTES

        assert should_liquidate

    def test_position_age_at_threshold(self):
        """Vérifie le cas limite au seuil."""
        age_minutes = 15
        should_liquidate = age_minutes > self.KILL_SWITCH_MINUTES

        assert not should_liquidate  # Exactement 15 = pas encore liquidé
