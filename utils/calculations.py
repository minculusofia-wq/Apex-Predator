"""
Calculations - Fonctions de calcul pour le trading

Fournit des utilitaires pour:
- Calcul de spreads
- Prix optimaux
- Tailles d'ordres
- Formatage
"""

from typing import Optional, Tuple


def calculate_spread(
    best_bid: Optional[float],
    best_ask: Optional[float]
) -> Optional[float]:
    """
    Calcule le spread entre bid et ask.
    
    Args:
        best_bid: Meilleur prix d'achat
        best_ask: Meilleur prix de vente
        
    Returns:
        Spread ou None si données invalides
    """
    if best_bid is None or best_ask is None:
        return None
    if best_bid <= 0 or best_ask <= 0:
        return None
    if best_ask < best_bid:
        return None
    
    return best_ask - best_bid


def calculate_midpoint(
    best_bid: Optional[float],
    best_ask: Optional[float]
) -> Optional[float]:
    """
    Calcule le prix médian.
    
    Args:
        best_bid: Meilleur prix d'achat
        best_ask: Meilleur prix de vente
        
    Returns:
        Prix médian ou None
    """
    if best_bid is None or best_ask is None:
        return None
    
    return (best_bid + best_ask) / 2


def calculate_optimal_price(
    best_bid: float,
    best_ask: float,
    offset: float = 0.01,
    side: str = "buy"
) -> float:
    """
    Calcule le prix optimal pour un ordre.
    
    Pour un achat: légèrement au-dessus du best bid
    Pour une vente: légèrement en-dessous du best ask
    
    Args:
        best_bid: Meilleur prix d'achat
        best_ask: Meilleur prix de vente
        offset: Décalage par rapport au best price
        side: "buy" ou "sell"
        
    Returns:
        Prix optimal (entre 0.01 et 0.99)
    """
    if side == "buy":
        price = best_bid + offset
    else:
        price = best_ask - offset
    
    # Garder dans les limites
    return max(0.01, min(0.99, price))


def calculate_bilateral_prices(
    best_bid_yes: float,
    best_ask_yes: float,
    best_bid_no: float,
    best_ask_no: float,
    offset: float = 0.01
) -> Tuple[float, float]:
    """
    Calcule les prix optimaux pour ordres bilatéraux.
    
    Stratégie: acheter YES et NO légèrement au-dessus des best bids.
    
    Args:
        best_bid_yes: Best bid pour YES
        best_ask_yes: Best ask pour YES
        best_bid_no: Best bid pour NO
        best_ask_no: Best ask pour NO
        offset: Décalage
        
    Returns:
        Tuple (prix_yes, prix_no)
    """
    price_yes = calculate_optimal_price(best_bid_yes, best_ask_yes, offset, "buy")
    price_no = calculate_optimal_price(best_bid_no, best_ask_no, offset, "buy")
    
    return price_yes, price_no


def calculate_order_size(
    capital: float,
    price: float,
    min_size: float = 1.0
) -> float:
    """
    Calcule la taille d'un ordre basé sur le capital.
    
    Args:
        capital: Capital à investir en $
        price: Prix unitaire
        min_size: Taille minimum
        
    Returns:
        Nombre de shares
    """
    if price <= 0:
        return min_size
    
    size = capital / price
    return max(min_size, round(size, 2))


def calculate_bilateral_sizes(
    total_capital: float,
    price_yes: float,
    price_no: float,
    min_size: float = 1.0
) -> Tuple[float, float]:
    """
    Calcule les tailles pour ordres bilatéraux.
    
    Divise le capital également entre YES et NO.
    
    Args:
        total_capital: Capital total
        price_yes: Prix YES
        price_no: Prix NO
        min_size: Taille minimum par côté
        
    Returns:
        Tuple (size_yes, size_no)
    """
    capital_per_side = total_capital / 2
    
    size_yes = calculate_order_size(capital_per_side, price_yes, min_size)
    size_no = calculate_order_size(capital_per_side, price_no, min_size)
    
    return size_yes, size_no


def calculate_pnl(
    entry_price: float,
    exit_price: float,
    size: float,
    side: str = "buy"
) -> float:
    """
    Calcule le PnL d'une position.
    
    Args:
        entry_price: Prix d'entrée
        exit_price: Prix de sortie
        size: Taille de la position
        side: "buy" ou "sell"
        
    Returns:
        PnL en $
    """
    if side == "buy":
        return (exit_price - entry_price) * size
    else:
        return (entry_price - exit_price) * size


def calculate_roi(pnl: float, investment: float) -> float:
    """
    Calcule le ROI en pourcentage.
    
    Args:
        pnl: Profit/Loss
        investment: Investissement initial
        
    Returns:
        ROI en %
    """
    if investment <= 0:
        return 0.0
    
    return (pnl / investment) * 100


def estimate_potential_profit(
    spread: float,
    size: float,
    capture_rate: float = 0.5
) -> float:
    """
    Estime le profit potentiel d'un trade de market making.
    
    Args:
        spread: Spread actuel
        size: Taille prévue
        capture_rate: Taux de capture du spread (0-1)
        
    Returns:
        Profit estimé
    """
    return spread * size * capture_rate


# ═══════════════════════════════════════════════════════════════
# FORMATAGE
# ═══════════════════════════════════════════════════════════════

def format_currency(amount: float, symbol: str = "$") -> str:
    """
    Formate un montant en devise.
    
    Args:
        amount: Montant
        symbol: Symbole de devise
        
    Returns:
        String formaté
    """
    if amount >= 1_000_000:
        return f"{symbol}{amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"{symbol}{amount/1_000:.2f}k"
    else:
        return f"{symbol}{amount:.2f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """
    Formate un pourcentage.
    
    Args:
        value: Valeur en %
        decimals: Décimales
        
    Returns:
        String formaté avec signe
    """
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def format_spread(spread: float) -> str:
    """
    Formate un spread.
    
    Args:
        spread: Spread en $
        
    Returns:
        String formaté
    """
    cents = spread * 100
    return f"{cents:.1f}¢"


def format_pnl(pnl: float) -> str:
    """
    Formate un PnL avec couleur ANSI.
    
    Args:
        pnl: PnL en $
        
    Returns:
        String formaté
    """
    if pnl >= 0:
        return f"+${pnl:.2f}"
    else:
        return f"-${abs(pnl):.2f}"


def format_short_address(address: str) -> str:
    """
    Formate une adresse wallet courte.
    
    Args:
        address: Adresse complète
        
    Returns:
        Format 0x1234...5678
    """
    if not address or len(address) < 10:
        return address
    return f"{address[:6]}...{address[-4:]}"
