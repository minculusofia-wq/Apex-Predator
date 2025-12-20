"""
Tests pour le module de résilience.

Vérifie:
- Validation des ordres
- Circuit breaker
- Retry avec backoff
"""

import pytest
from core.resilience import (
    OrderValidator,
    OrderValidationResult,
    RetryConfig,
    CircuitBreakerConfig,
)


# ═══════════════════════════════════════════════════════════════════════════
# TESTS ORDER VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════

class TestOrderValidator:
    """Tests pour la validation des ordres."""

    @pytest.fixture
    def validator(self):
        """Crée un validateur avec config par défaut."""
        return OrderValidator(
            max_order_size_usd=500.0,
            max_slippage_pct=2.0,
            min_order_size_usd=1.0,
            max_position_per_market=1000.0,
        )

    def test_valid_order(self, validator):
        """Vérifie qu'un ordre valide passe."""
        result = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=100,
            expected_price=0.50,
            current_balance=1000.0,
        )

        assert result.valid
        assert len(result.errors) == 0

    def test_invalid_price_zero(self, validator):
        """Vérifie le rejet d'un prix à 0."""
        result = validator.validate_order(
            side="BUY",
            price=0.0,
            qty=100,
        )

        assert not result.valid
        assert any("Prix invalide" in e for e in result.errors)

    def test_invalid_price_above_one(self, validator):
        """Vérifie le rejet d'un prix >= 1."""
        result = validator.validate_order(
            side="BUY",
            price=1.05,
            qty=100,
        )

        assert not result.valid
        assert any("Prix invalide" in e for e in result.errors)

    def test_invalid_quantity_zero(self, validator):
        """Vérifie le rejet d'une quantité à 0."""
        result = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=0,
        )

        assert not result.valid
        assert any("Quantité invalide" in e for e in result.errors)

    def test_order_too_small(self, validator):
        """Vérifie le rejet d'un ordre trop petit."""
        result = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=1,  # $0.50 < $1 minimum
        )

        assert not result.valid
        assert any("trop petit" in e for e in result.errors)

    def test_order_too_large(self, validator):
        """Vérifie le rejet d'un ordre trop grand."""
        result = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=2000,  # $1000 > $500 maximum
        )

        assert not result.valid
        assert any("trop grand" in e for e in result.errors)

    def test_insufficient_balance(self, validator):
        """Vérifie le rejet si balance insuffisante."""
        result = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=100,  # $50 ordre
            current_balance=40.0,  # Seulement $40
        )

        assert not result.valid
        assert any("Balance insuffisante" in e for e in result.errors)

    def test_slippage_too_high(self, validator):
        """Vérifie le rejet si slippage trop élevé."""
        result = validator.validate_order(
            side="BUY",
            price=0.55,
            qty=100,
            expected_price=0.50,  # 10% slippage!
        )

        assert not result.valid
        assert any("Slippage" in e for e in result.errors)

    def test_slippage_acceptable(self, validator):
        """Vérifie l'acceptation si slippage acceptable."""
        result = validator.validate_order(
            side="BUY",
            price=0.505,
            qty=100,
            expected_price=0.50,  # 1% slippage
            current_balance=1000.0,
        )

        assert result.valid

    def test_position_limit_exceeded(self, validator):
        """Vérifie le rejet si position limit dépassée."""
        result = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=100,
            current_position_value=980.0,  # Déjà $980, +$50 = $1030 > $1000
        )

        assert not result.valid
        assert any("Position limit" in e for e in result.errors)

    def test_warning_high_balance_usage(self, validator):
        """Vérifie le warning si >90% de balance utilisée."""
        result = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=190,  # $95 sur $100 = 95%
            current_balance=100.0,
        )

        assert result.valid  # Accepté mais avec warning
        assert len(result.warnings) > 0
        assert any("90%" in w for w in result.warnings)


# ═══════════════════════════════════════════════════════════════════════════
# TESTS RETRY CONFIG
# ═══════════════════════════════════════════════════════════════════════════

class TestRetryConfig:
    """Tests pour la configuration du retry."""

    def test_default_config(self):
        """Vérifie les valeurs par défaut."""
        config = RetryConfig()

        assert config.max_attempts == 3
        assert config.base_delay == 0.1
        assert config.max_delay == 5.0
        assert config.exponential_base == 2.0

    def test_custom_config(self):
        """Vérifie une config personnalisée."""
        config = RetryConfig(
            max_attempts=5,
            base_delay=0.5,
            max_delay=10.0,
        )

        assert config.max_attempts == 5
        assert config.base_delay == 0.5
        assert config.max_delay == 10.0

    def test_backoff_calculation(self):
        """Vérifie le calcul du backoff exponentiel."""
        config = RetryConfig(base_delay=0.1, exponential_base=2.0, max_delay=5.0)

        # Attempt 1: 0.1 * 2^0 = 0.1
        delay_1 = min(config.base_delay * (config.exponential_base ** 0), config.max_delay)
        assert delay_1 == 0.1

        # Attempt 2: 0.1 * 2^1 = 0.2
        delay_2 = min(config.base_delay * (config.exponential_base ** 1), config.max_delay)
        assert delay_2 == 0.2

        # Attempt 3: 0.1 * 2^2 = 0.4
        delay_3 = min(config.base_delay * (config.exponential_base ** 2), config.max_delay)
        assert delay_3 == 0.4

    def test_backoff_capped_at_max(self):
        """Vérifie que le backoff est plafonné."""
        config = RetryConfig(base_delay=1.0, exponential_base=2.0, max_delay=5.0)

        # Attempt 10: 1.0 * 2^9 = 512, capped at 5.0
        delay = min(config.base_delay * (config.exponential_base ** 9), config.max_delay)
        assert delay == 5.0


# ═══════════════════════════════════════════════════════════════════════════
# TESTS CIRCUIT BREAKER CONFIG
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerConfig:
    """Tests pour la configuration du circuit breaker."""

    def test_default_config(self):
        """Vérifie les valeurs par défaut."""
        config = CircuitBreakerConfig()

        assert config.failure_threshold == 5
        assert config.success_threshold == 2
        assert config.timeout_seconds == 30.0
        assert config.half_open_max_calls == 3

    def test_custom_config(self):
        """Vérifie une config personnalisée."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=1,
            timeout_seconds=60.0,
        )

        assert config.failure_threshold == 3
        assert config.success_threshold == 1
        assert config.timeout_seconds == 60.0


# ═══════════════════════════════════════════════════════════════════════════
# TESTS INTEGRATION VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class TestValidationIntegration:
    """Tests d'intégration pour la validation."""

    def test_full_order_lifecycle(self):
        """Simule le cycle complet de validation d'un ordre."""
        validator = OrderValidator(
            max_order_size_usd=100.0,
            max_slippage_pct=2.0,
            min_order_size_usd=5.0,
            max_position_per_market=500.0,
        )

        # Ordre 1: Valide
        result1 = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=50,  # $25
            expected_price=0.50,
            current_balance=1000.0,
            current_position_value=0.0,
        )
        assert result1.valid

        # Ordre 2: Position mise à jour
        result2 = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=50,  # $25
            expected_price=0.50,
            current_balance=975.0,  # Moins $25
            current_position_value=25.0,  # +$25 de position
        )
        assert result2.valid

        # Ordre 3: Approche de la limite
        result3 = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=900,  # $450 → position totale = $500 (limite)
            expected_price=0.50,
            current_balance=950.0,
            current_position_value=50.0,
        )
        assert result3.valid

        # Ordre 4: Dépasse la limite
        result4 = validator.validate_order(
            side="BUY",
            price=0.50,
            qty=20,  # $10 → position totale = $510 > $500
            expected_price=0.50,
            current_balance=500.0,
            current_position_value=500.0,
        )
        assert not result4.valid
