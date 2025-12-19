"""
Logger Centralisé - Système de logging structuré pour le bot HFT

Remplace tous les print() par un logging propre avec:
- Rotation automatique des fichiers
- Niveaux: DEBUG, INFO, TRADE, WARNING, ERROR
- Format JSON pour analyse
- Console colorée pour debug
"""

import logging
import logging.handlers
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from functools import wraps
import time


# Créer le dossier logs s'il n'existe pas
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


# Niveau custom pour les TRADES
TRADE_LEVEL = 25  # Entre INFO (20) et WARNING (30)
logging.addLevelName(TRADE_LEVEL, "TRADE")


class JsonFormatter(logging.Formatter):
    """Formatter JSON pour analyse et parsing."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }

        # Ajouter les extras si présents
        if hasattr(record, "market_id"):
            log_data["market_id"] = record.market_id
        if hasattr(record, "side"):
            log_data["side"] = record.side
        if hasattr(record, "price"):
            log_data["price"] = record.price
        if hasattr(record, "qty"):
            log_data["qty"] = record.qty
        if hasattr(record, "latency_ms"):
            log_data["latency_ms"] = record.latency_ms
        if hasattr(record, "error"):
            log_data["error"] = record.error

        return json.dumps(log_data)


class ColoredFormatter(logging.Formatter):
    """Formatter coloré pour la console."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "TRADE": "\033[35m",     # Magenta
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[41m",  # Red background
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)

        # Emoji par niveau
        emoji = {
            "DEBUG": "🔍",
            "INFO": "ℹ️",
            "TRADE": "💰",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "CRITICAL": "🚨",
        }.get(record.levelname, "")

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        module = record.module[:12].ljust(12)

        return f"{color}{timestamp} {emoji} [{record.levelname:8}] {module} | {record.getMessage()}{self.RESET}"


class BotLogger:
    """Logger principal du bot avec méthodes spécialisées."""

    _instance: Optional["BotLogger"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.logger = logging.getLogger("polymarket_bot")
        self.logger.setLevel(logging.DEBUG)

        # Éviter les doublons
        if self.logger.handlers:
            return

        # Handler Console (coloré)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(ColoredFormatter())
        self.logger.addHandler(console_handler)

        # Handler Fichier principal (rotation 10MB, 5 fichiers)
        file_handler = logging.handlers.RotatingFileHandler(
            LOGS_DIR / "bot.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonFormatter())
        self.logger.addHandler(file_handler)

        # Handler Trades uniquement (pour analyse)
        trades_handler = logging.handlers.RotatingFileHandler(
            LOGS_DIR / "trades.log",
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=10,
            encoding="utf-8"
        )
        trades_handler.setLevel(TRADE_LEVEL)
        trades_handler.setFormatter(JsonFormatter())
        trades_handler.addFilter(lambda r: r.levelno == TRADE_LEVEL)
        self.logger.addHandler(trades_handler)

        # Handler Erreurs uniquement
        error_handler = logging.handlers.RotatingFileHandler(
            LOGS_DIR / "errors.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(JsonFormatter())
        self.logger.addHandler(error_handler)

    def debug(self, msg: str, **kwargs):
        self.logger.debug(msg, extra=kwargs)

    def info(self, msg: str, **kwargs):
        self.logger.info(msg, extra=kwargs)

    def warning(self, msg: str, **kwargs):
        self.logger.warning(msg, extra=kwargs)

    def error(self, msg: str, **kwargs):
        self.logger.error(msg, extra=kwargs)

    def critical(self, msg: str, **kwargs):
        self.logger.critical(msg, extra=kwargs)

    def trade(self, msg: str, market_id: str = "", side: str = "",
              price: float = 0, qty: float = 0, **kwargs):
        """Log spécial pour les trades."""
        extra = {
            "market_id": market_id,
            "side": side,
            "price": price,
            "qty": qty,
            **kwargs
        }
        self.logger.log(TRADE_LEVEL, msg, extra=extra)

    def latency(self, operation: str, latency_ms: float):
        """Log de latence pour monitoring."""
        self.logger.info(f"{operation} completed", extra={"latency_ms": latency_ms})


# Instance globale
_logger: Optional[BotLogger] = None


def get_logger() -> BotLogger:
    """Retourne l'instance singleton du logger."""
    global _logger
    if _logger is None:
        _logger = BotLogger()
    return _logger


def log_execution_time(operation_name: str = ""):
    """Décorateur pour mesurer et logger le temps d'exécution."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                get_logger().latency(operation_name or func.__name__, elapsed_ms)
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000
                get_logger().error(
                    f"{operation_name or func.__name__} failed after {elapsed_ms:.1f}ms",
                    error=str(e)
                )
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                get_logger().latency(operation_name or func.__name__, elapsed_ms)
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000
                get_logger().error(
                    f"{operation_name or func.__name__} failed after {elapsed_ms:.1f}ms",
                    error=str(e)
                )
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator
