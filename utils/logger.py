"""
Logger - Configuration du logging

Fournit un logging formaté avec Rich pour une meilleure lisibilité.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from rich.logging import RichHandler
from rich.console import Console


# Console Rich globale
console = Console()

# Format par défaut
LOG_FORMAT = "%(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    rich_output: bool = True
) -> None:
    """
    Configure le logging global.
    
    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR)
        log_file: Chemin vers le fichier de log (optionnel)
        rich_output: Utiliser Rich pour la sortie console
    """
    handlers = []
    
    # Handler console
    if rich_output:
        handlers.append(RichHandler(
            console=console,
            show_time=True,
            show_path=False,
            rich_tracebacks=True,
            tracebacks_show_locals=True,
        ))
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt=DATE_FORMAT
        ))
        handlers.append(console_handler)
    
    # Handler fichier
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt=DATE_FORMAT
        ))
        handlers.append(file_handler)
    
    # Configuration root logger
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        handlers=handlers,
        force=True
    )
    
    # Réduire le bruit des bibliothèques externes
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Récupère un logger nommé.
    
    Args:
        name: Nom du logger (généralement __name__)
        
    Returns:
        Logger configuré
    """
    return logging.getLogger(name)


class BotLogger:
    """
    Logger spécialisé pour le bot avec méthodes helpers.
    """
    
    def __init__(self, name: str = "hft-bot"):
        self._logger = get_logger(name)
    
    def info(self, message: str) -> None:
        """Log info."""
        self._logger.info(message)
    
    def warning(self, message: str) -> None:
        """Log warning."""
        self._logger.warning(message)
    
    def error(self, message: str, exc_info: bool = False) -> None:
        """Log error."""
        self._logger.error(message, exc_info=exc_info)
    
    def debug(self, message: str) -> None:
        """Log debug."""
        self._logger.debug(message)
    
    def trade(self, action: str, market: str, details: str = "") -> None:
        """Log un trade."""
        msg = f"🚀 TRADE | {action} | {market}"
        if details:
            msg += f" | {details}"
        self._logger.info(msg)
    
    def opportunity(self, market: str, spread: float, score: int) -> None:
        """Log une opportunité détectée."""
        self._logger.info(
            f"🎯 OPPORTUNITY | {market[:40]}... | "
            f"Spread: ${spread:.3f} | Score: {'⭐' * score}"
        )
    
    def pnl(self, amount: float, trade_id: str = "") -> None:
        """Log un PnL."""
        sign = "+" if amount >= 0 else ""
        emoji = "💰" if amount >= 0 else "📉"
        msg = f"{emoji} PnL: {sign}${amount:.2f}"
        if trade_id:
            msg += f" | Trade: {trade_id}"
        self._logger.info(msg)
    
    def wallet(self, action: str, address: str = "", amount: float = 0) -> None:
        """Log une action wallet."""
        msg = f"💳 WALLET | {action}"
        if address:
            short = f"{address[:6]}...{address[-4:]}"
            msg += f" | {short}"
        if amount > 0:
            msg += f" | ${amount:.2f}"
        self._logger.info(msg)


# Instance globale
bot_logger = BotLogger()
