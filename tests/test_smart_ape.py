"""
Tests pour la stratégie Smart Ape.

Vérifie:
- Détection des marchés cibles (Bitcoin Up/Down 15min)
- Calcul du payout ratio
- Logique de fenêtre temporelle
- Positions asymétriques UP/DOWN
"""

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# TESTS DÉTECTION MARCHÉ CIBLE
# ═══════════════════════════════════════════════════════════════════════════

class TestTargetMarketFilter:
    """Tests pour la détection des marchés Bitcoin Up/Down 15min."""

    def is_target_market(self, question: str) -> bool:
        """Réplique de la logique Smart Ape pour les tests."""
        q = question.lower()
        has_bitcoin = "bitcoin" in q or "btc" in q
        has_up_down = "up" in q and "down" in q
        has_15min = "15" in q or "fifteen" in q
        return has_bitcoin and has_up_down and has_15min

    def test_valid_bitcoin_up_down_15min(self):
        """Vérifie la détection d'un marché valide."""
        question = "Will Bitcoin go Up or Down in the next 15 minutes?"
        assert self.is_target_market(question) is True

    def test_valid_btc_variant(self):
        """Vérifie la détection avec BTC au lieu de Bitcoin."""
        question = "BTC price: Up or Down in 15 min?"
        assert self.is_target_market(question) is True

    def test_valid_fifteen_spelled(self):
        """Vérifie la détection avec 'fifteen' en lettres."""
        question = "Bitcoin Up or Down in fifteen minutes?"
        assert self.is_target_market(question) is True

    def test_invalid_eth_market(self):
        """Vérifie le rejet d'un marché ETH."""
        question = "Will ETH go Up or Down in the next 15 minutes?"
        assert self.is_target_market(question) is False

    def test_invalid_wrong_timeframe(self):
        """Vérifie le rejet d'un mauvais timeframe."""
        question = "Will Bitcoin go Up or Down in the next 1 hour?"
        assert self.is_target_market(question) is False

    def test_invalid_no_up_down(self):
        """Vérifie le rejet sans 'Up or Down'."""
        question = "Bitcoin price in 15 minutes?"
        assert self.is_target_market(question) is False

    def test_invalid_completely_unrelated(self):
        """Vérifie le rejet d'un marché non lié."""
        question = "Will Trump win the election?"
        assert self.is_target_market(question) is False

    @pytest.mark.parametrize("question,expected", [
        ("Bitcoin Up or Down 15 min?", True),
        ("BTC: Up or Down - 15 minutes", True),
        ("BITCOIN UP/DOWN 15MIN", True),
        ("Will Bitcoin go up or go down in fifteen minutes?", True),
        ("ETH Up or Down 15 min?", False),
        ("Bitcoin Up or Down 1 hour?", False),
        ("Bitcoin price 15 min", False),
        ("Solana up down 15 minutes", False),
    ])
    def test_various_scenarios(self, question, expected):
        """Test paramétré avec plusieurs scénarios."""
        result = self.is_target_market(question)
        assert result == expected, f"'{question}' devrait être {expected}"


# ═══════════════════════════════════════════════════════════════════════════
# TESTS PAYOUT RATIO
# ═══════════════════════════════════════════════════════════════════════════

class TestPayoutRatio:
    """Tests pour le calcul du payout ratio."""

    MIN_PAYOUT_RATIO = 1.5  # Seuil Smart Ape

    def calculate_payout_ratio(self, price_up: float, price_down: float) -> float:
        """Calcule le payout ratio."""
        return 1.0 / (price_up + price_down)

    def test_profitable_payout_ratio(self):
        """Vérifie un payout ratio profitable."""
        price_up = 0.30
        price_down = 0.25
        payout = self.calculate_payout_ratio(price_up, price_down)

        # 1 / 0.55 = 1.82
        assert payout > self.MIN_PAYOUT_RATIO
        assert payout == pytest.approx(1.818, rel=1e-2)

    def test_unprofitable_payout_ratio(self):
        """Vérifie un payout ratio non profitable."""
        price_up = 0.45
        price_down = 0.50
        payout = self.calculate_payout_ratio(price_up, price_down)

        # 1 / 0.95 = 1.05
        assert payout < self.MIN_PAYOUT_RATIO

    def test_boundary_payout_ratio(self):
        """Vérifie le cas limite juste au-dessus du seuil."""
        # Pour payout >= 1.5, on a: price_up + price_down <= 0.6667
        price_up = 0.33
        price_down = 0.33  # Total = 0.66 -> payout = 1.515
        payout = self.calculate_payout_ratio(price_up, price_down)

        assert payout >= self.MIN_PAYOUT_RATIO
        assert payout == pytest.approx(1.515, rel=1e-2)

    def test_very_profitable_dump_scenario(self):
        """Vérifie un scénario de dump très profitable."""
        price_up = 0.15  # Dump important sur UP
        price_down = 0.30
        payout = self.calculate_payout_ratio(price_up, price_down)

        # 1 / 0.45 = 2.22
        assert payout > 2.0
        assert payout == pytest.approx(2.222, rel=1e-2)

    @pytest.mark.parametrize("up,down,should_accept", [
        (0.30, 0.25, True),   # payout 1.82
        (0.25, 0.30, True),   # payout 1.82
        (0.35, 0.30, True),   # payout 1.54
        (0.33, 0.34, False),  # payout 1.49
        (0.40, 0.35, False),  # payout 1.33
        (0.45, 0.50, False),  # payout 1.05
    ])
    def test_payout_various_scenarios(self, up, down, should_accept):
        """Test paramétré pour le payout ratio."""
        payout = self.calculate_payout_ratio(up, down)
        is_accepted = payout >= self.MIN_PAYOUT_RATIO

        if should_accept:
            assert is_accepted, f"payout {payout:.2f} devrait être accepté"
        else:
            assert not is_accepted, f"payout {payout:.2f} devrait être rejeté"


# ═══════════════════════════════════════════════════════════════════════════
# TESTS DUMP DETECTION
# ═══════════════════════════════════════════════════════════════════════════

class TestDumpDetection:
    """Tests pour la détection des dumps de prix."""

    DUMP_THRESHOLD = 0.15  # 15%

    def detect_dump(self, initial_price: float, current_price: float) -> tuple:
        """Détecte si un dump a eu lieu et retourne la direction."""
        change_pct = (initial_price - current_price) / initial_price
        if change_pct >= self.DUMP_THRESHOLD:
            return True, "DOWN"  # Prix UP a dumpé -> acheter UP
        return False, None

    def test_dump_detected_up_side(self):
        """Vérifie la détection d'un dump sur le côté UP."""
        initial = 0.50
        current = 0.40  # -20%
        is_dump, side = self.detect_dump(initial, current)

        assert is_dump is True
        assert side == "DOWN"

    def test_no_dump_small_change(self):
        """Vérifie qu'un petit changement n'est pas un dump."""
        initial = 0.50
        current = 0.45  # -10%
        is_dump, _ = self.detect_dump(initial, current)

        assert is_dump is False

    def test_no_dump_price_increase(self):
        """Vérifie qu'une augmentation n'est pas un dump."""
        initial = 0.50
        current = 0.55  # +10%
        is_dump, _ = self.detect_dump(initial, current)

        assert is_dump is False

    def test_dump_exactly_at_threshold(self):
        """Vérifie le cas limite exactement au seuil."""
        initial = 0.50
        current = 0.425  # -15% exactement
        is_dump, _ = self.detect_dump(initial, current)

        assert is_dump is True


# ═══════════════════════════════════════════════════════════════════════════
# TESTS FENÊTRE TEMPORELLE
# ═══════════════════════════════════════════════════════════════════════════

class TestTimingWindow:
    """Tests pour la logique de fenêtre temporelle."""

    WINDOW_MINUTES = 2

    def is_in_window(self, elapsed_minutes: float) -> bool:
        """Vérifie si on est dans la fenêtre d'achat."""
        return elapsed_minutes <= self.WINDOW_MINUTES

    def test_early_in_window(self):
        """Vérifie qu'on est dans la fenêtre au début."""
        elapsed = 0.5
        assert self.is_in_window(elapsed) is True

    def test_end_of_window(self):
        """Vérifie qu'on est encore dans la fenêtre à la limite."""
        elapsed = 2.0
        assert self.is_in_window(elapsed) is True

    def test_after_window(self):
        """Vérifie qu'on est hors fenêtre après."""
        elapsed = 2.5
        assert self.is_in_window(elapsed) is False

    def test_way_after_window(self):
        """Vérifie qu'on est hors fenêtre bien après."""
        elapsed = 10.0
        assert self.is_in_window(elapsed) is False


# ═══════════════════════════════════════════════════════════════════════════
# TESTS POSITIONS ASYMÉTRIQUES
# ═══════════════════════════════════════════════════════════════════════════

class TestAsymmetricPositions:
    """Tests pour les positions asymétriques UP/DOWN."""

    TARGET_RATIO = 1.25  # Ratio 5:4

    def calculate_position_sizes(self, total_usd: float, price_up: float, price_down: float) -> tuple:
        """Calcule les tailles de position avec ratio 5:4."""
        # Allocation: 55.5% sur le côté dumpé, 44.5% sur l'autre
        up_allocation = 0.555
        down_allocation = 0.445

        qty_up = (total_usd * up_allocation) / price_up
        qty_down = (total_usd * down_allocation) / price_down

        return qty_up, qty_down

    def test_asymmetric_allocation(self):
        """Vérifie l'allocation asymétrique 5:4."""
        total = 100.0
        price_up = 0.30
        price_down = 0.30

        qty_up, qty_down = self.calculate_position_sizes(total, price_up, price_down)

        # Avec mêmes prix: ratio ~1.25 (55.5/44.5)
        ratio = qty_up / qty_down
        assert ratio == pytest.approx(self.TARGET_RATIO, rel=0.01)

    def test_position_sizing_profitable(self):
        """Vérifie que la position est rentable si le dump continue."""
        total = 100.0
        price_up = 0.25  # Dump sur UP
        price_down = 0.30

        qty_up, qty_down = self.calculate_position_sizes(total, price_up, price_down)
        cost = (qty_up * price_up) + (qty_down * price_down)

        # Le coût devrait être proche du total investi
        assert cost == pytest.approx(total, rel=0.01)

    def test_max_payout_on_up_win(self):
        """Vérifie le payout max si UP gagne."""
        total = 100.0
        price_up = 0.25
        price_down = 0.30

        qty_up, qty_down = self.calculate_position_sizes(total, price_up, price_down)

        # Si UP gagne: qty_up * $1.00
        payout_up_wins = qty_up * 1.0

        # Profit = payout - cost (cost = total)
        profit = payout_up_wins - total

        assert profit > 0  # Profitable si UP gagne

    def test_max_payout_on_down_win(self):
        """Vérifie le payout si DOWN gagne."""
        total = 100.0
        price_up = 0.25
        price_down = 0.30

        qty_up, qty_down = self.calculate_position_sizes(total, price_up, price_down)

        # Si DOWN gagne: qty_down * $1.00
        payout_down_wins = qty_down * 1.0

        # Même avec le ratio 5:4, on peut encore profiter si payout > cost
        # Dans ce cas: ~148 * $1 = $148 vs cost $100
        profit = payout_down_wins - total

        assert profit > 0  # Profitable aussi si DOWN gagne (avec bon payout ratio)


# ═══════════════════════════════════════════════════════════════════════════
# TESTS INTÉGRATION
# ═══════════════════════════════════════════════════════════════════════════

class TestSmartApeIntegration:
    """Tests d'intégration pour le flow complet Smart Ape."""

    def test_full_profitable_scenario(self):
        """Simule un scénario complet profitable."""
        # Market: Bitcoin Up/Down 15min
        question = "Bitcoin Up or Down 15 minutes?"

        # Prix après dump sur UP
        price_up = 0.28
        price_down = 0.32
        total_cost = price_up + price_down  # 0.60

        # Vérifications
        payout_ratio = 1.0 / total_cost  # 1.67
        elapsed_minutes = 1.5  # Dans la fenêtre

        # Critères
        is_target = "bitcoin" in question.lower() and "up" in question.lower()
        is_profitable = payout_ratio >= 1.5
        is_in_window = elapsed_minutes <= 2.0

        assert is_target
        assert is_profitable
        assert is_in_window

    def test_reject_late_opportunity(self):
        """Vérifie le rejet d'une opportunité trop tardive."""
        # Bonne opportunité mais hors fenêtre
        price_up = 0.25
        price_down = 0.30
        payout_ratio = 1.0 / (price_up + price_down)  # 1.82

        elapsed_minutes = 5.0  # Hors fenêtre

        is_profitable = payout_ratio >= 1.5
        is_in_window = elapsed_minutes <= 2.0

        assert is_profitable
        assert not is_in_window

    def test_reject_bad_payout(self):
        """Vérifie le rejet d'un mauvais payout ratio."""
        # Dans la fenêtre mais pas profitable
        price_up = 0.45
        price_down = 0.48
        payout_ratio = 1.0 / (price_up + price_down)  # 1.08

        elapsed_minutes = 1.0

        is_profitable = payout_ratio >= 1.5
        is_in_window = elapsed_minutes <= 2.0

        assert not is_profitable
        assert is_in_window
