"""
Fixtures pytest pour les tests du bot.
"""

import pytest
import sys
from pathlib import Path

# Ajouter le répertoire parent au path pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def sample_market_data():
    """Données de marché exemple."""
    return {
        "market_id": "0x123abc",
        "question": "Will BTC reach $100k by end of day?",
        "yes_price": 0.45,
        "no_price": 0.52,
        "volume_24h": 5000.0,
        "end_date": "2025-01-20T15:00:00Z",
    }


@pytest.fixture
def profitable_opportunity():
    """Opportunité profitable (pair_cost < 0.975)."""
    return {
        "yes_price": 0.45,
        "no_price": 0.50,
        "pair_cost": 0.95,
        "expected_profit_pct": 5.0,
    }


@pytest.fixture
def marginal_opportunity():
    """Opportunité marginale (pair_cost ~ 0.97)."""
    return {
        "yes_price": 0.48,
        "no_price": 0.49,
        "pair_cost": 0.97,
        "expected_profit_pct": 3.0,
    }


@pytest.fixture
def unprofitable_opportunity():
    """Opportunité non profitable (pair_cost >= 0.975)."""
    return {
        "yes_price": 0.50,
        "no_price": 0.48,
        "pair_cost": 0.98,
        "expected_profit_pct": 2.0,  # Trop faible après frais
    }


@pytest.fixture
def gabagool_config():
    """Configuration Gabagool par défaut."""
    return {
        "max_pair_cost": 0.975,
        "min_profit_margin": 0.025,
        "order_size_usd": 25.0,
        "max_position_usd": 500.0,
        "balance_ratio_threshold": 1.3,
        "kill_switch_minutes": 15,
    }


@pytest.fixture
def sample_position():
    """Position Gabagool exemple."""
    return {
        "market_id": "0x123abc",
        "qty_yes": 100,
        "qty_no": 95,
        "cost_yes": 45.0,
        "cost_no": 49.4,
        "avg_price_yes": 0.45,
        "avg_price_no": 0.52,
    }
