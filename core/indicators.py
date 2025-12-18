"""
Technical Indicators - Module léger d'analyse technique.

Fournit des fonctions standard (RSI, SMA) opérant sur des listes simples.
Optimisé pour la rapidité et l'absence de dépendances lourdes (pandas).
"""

from typing import List, Optional

def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    """Calcule la Moyenne Mobile Simple."""
    if not prices or len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """
    Calcule le Relative Strength Index (RSI).
    
    Args:
        prices: Liste de prix (le plus récent à la fin)
        period: Période de calcul (défaut 14)
        
    Returns:
        RSI (0-100) ou None si pas assez de données.
    """
    if not prices or len(prices) < period + 1:
        return None

    # Calculer les variations
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    
    # Séparer gains et pertes
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    # Moyenne initiale
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Lissage (Wilder's Smoothing)
    for i in range(period, len(prices) - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
        
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def get_trend_strength(prices: List[float], short_window: int = 5, long_window: int = 20) -> str:
    """
    Détermine la tendance de base.
    Returns: "UP", "DOWN", or "NEUTRAL"
    """
    sma_short = calculate_sma(prices, short_window)
    sma_long = calculate_sma(prices, long_window)
    
    if not sma_short or not sma_long:
        return "NEUTRAL"
        
    if sma_short > sma_long * 1.01: # +1%
        return "UP"
    elif sma_short < sma_long * 0.99: # -1%
        return "DOWN"
    
    return "NEUTRAL"
