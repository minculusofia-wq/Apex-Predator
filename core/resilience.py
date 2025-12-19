"""
Resilience - Système de robustesse pour le bot HFT

Inclut:
- Retry avec backoff exponentiel
- Circuit Breaker pour éviter les cascades d'erreurs
- Validation pré-exécution des ordres
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Callable, Optional, Any, TypeVar, List
from core.logger import get_logger

T = TypeVar('T')


# ═══════════════════════════════════════════════════════════════════════════
# RETRY AVEC BACKOFF EXPONENTIEL
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RetryConfig:
    """Configuration pour les retries."""
    max_attempts: int = 3
    base_delay: float = 0.1  # 100ms
    max_delay: float = 5.0   # 5 secondes max
    exponential_base: float = 2.0
    retryable_exceptions: tuple = (Exception,)


def retry_async(config: Optional[RetryConfig] = None):
    """
    Décorateur de retry pour fonctions async.

    Usage:
        @retry_async(RetryConfig(max_attempts=3))
        async def my_function():
            ...
    """
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            logger = get_logger()
            last_exception = None

            for attempt in range(1, config.max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except config.retryable_exceptions as e:
                    last_exception = e
                    if attempt < config.max_attempts:
                        delay = min(
                            config.base_delay * (config.exponential_base ** (attempt - 1)),
                            config.max_delay
                        )
                        logger.warning(
                            f"Retry {attempt}/{config.max_attempts} for {func.__name__} "
                            f"after {delay:.2f}s - Error: {e}"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"All {config.max_attempts} retries failed for {func.__name__}",
                            error=str(e)
                        )

            raise last_exception

        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════

class CircuitState(Enum):
    """États du circuit breaker."""
    CLOSED = "closed"      # Normal, requêtes passent
    OPEN = "open"          # Trop d'erreurs, requêtes bloquées
    HALF_OPEN = "half_open"  # Test de récupération


@dataclass
class CircuitBreakerConfig:
    """Configuration du circuit breaker."""
    failure_threshold: int = 5      # Erreurs avant ouverture
    success_threshold: int = 2      # Succès avant fermeture
    timeout_seconds: float = 30.0   # Temps avant test de récupération
    half_open_max_calls: int = 3    # Appels max en half-open


class CircuitBreaker:
    """
    Circuit Breaker pour protéger contre les cascades d'erreurs.

    Usage:
        breaker = CircuitBreaker("polymarket_api")

        @breaker
        async def call_api():
            ...
    """

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._half_open_calls = 0
        self._lock = asyncio.Lock()
        self._logger = get_logger()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_closed(self) -> bool:
        return self._state == CircuitState.CLOSED

    async def _check_state(self) -> bool:
        """Vérifie et met à jour l'état. Retourne True si l'appel est autorisé."""
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                # Vérifier si timeout expiré
                if self._last_failure_time:
                    elapsed = (datetime.now() - self._last_failure_time).total_seconds()
                    if elapsed >= self.config.timeout_seconds:
                        self._state = CircuitState.HALF_OPEN
                        self._half_open_calls = 0
                        self._logger.info(f"Circuit {self.name}: OPEN -> HALF_OPEN")
                        return True
                return False

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self.config.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False

            return False

    async def _record_success(self):
        """Enregistre un succès."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._logger.info(f"Circuit {self.name}: HALF_OPEN -> CLOSED")
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0  # Reset sur succès

    async def _record_failure(self):
        """Enregistre un échec."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = datetime.now()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._success_count = 0
                self._logger.warning(f"Circuit {self.name}: HALF_OPEN -> OPEN (failure during recovery)")
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.config.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._logger.warning(
                        f"Circuit {self.name}: CLOSED -> OPEN "
                        f"({self._failure_count} failures)"
                    )

    def __call__(self, func: Callable) -> Callable:
        """Décorateur pour protéger une fonction."""
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if not await self._check_state():
                raise CircuitOpenError(
                    f"Circuit {self.name} is OPEN - call rejected"
                )

            try:
                result = await func(*args, **kwargs)
                await self._record_success()
                return result
            except Exception as e:
                await self._record_failure()
                raise

        return wrapper

    def get_stats(self) -> dict:
        """Retourne les stats du circuit breaker."""
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure": self._last_failure_time.isoformat() if self._last_failure_time else None
        }


class CircuitOpenError(Exception):
    """Exception levée quand le circuit est ouvert."""
    pass


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION PRÉ-EXÉCUTION DES ORDRES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class OrderValidationResult:
    """Résultat de validation d'un ordre."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class OrderValidator:
    """
    Validateur d'ordres avant exécution.

    Vérifie:
    - Balance suffisante
    - Slippage acceptable
    - Position limits
    - Rate limits
    """

    def __init__(
        self,
        max_order_size_usd: float = 500.0,
        max_slippage_pct: float = 2.0,
        min_order_size_usd: float = 1.0,
        max_position_per_market: float = 1000.0,
    ):
        self.max_order_size_usd = max_order_size_usd
        self.max_slippage_pct = max_slippage_pct
        self.min_order_size_usd = min_order_size_usd
        self.max_position_per_market = max_position_per_market
        self._logger = get_logger()

    def validate_order(
        self,
        side: str,
        price: float,
        qty: float,
        expected_price: Optional[float] = None,
        current_balance: Optional[float] = None,
        current_position_value: float = 0.0,
    ) -> OrderValidationResult:
        """
        Valide un ordre avant exécution.

        Args:
            side: "BUY" ou "SELL"
            price: Prix de l'ordre
            qty: Quantité
            expected_price: Prix attendu (pour calcul slippage)
            current_balance: Balance actuelle
            current_position_value: Valeur position actuelle sur ce marché
        """
        errors = []
        warnings = []
        order_value = price * qty

        # 1. Prix valide
        if price <= 0 or price >= 1:
            errors.append(f"Prix invalide: {price} (doit être entre 0 et 1)")

        # 2. Quantité valide
        if qty <= 0:
            errors.append(f"Quantité invalide: {qty}")

        # 3. Taille minimum
        if order_value < self.min_order_size_usd:
            errors.append(f"Ordre trop petit: ${order_value:.2f} < ${self.min_order_size_usd}")

        # 4. Taille maximum
        if order_value > self.max_order_size_usd:
            errors.append(f"Ordre trop grand: ${order_value:.2f} > ${self.max_order_size_usd}")

        # 5. Balance suffisante (si fournie)
        if current_balance is not None and side == "BUY":
            if order_value > current_balance:
                errors.append(f"Balance insuffisante: ${order_value:.2f} > ${current_balance:.2f}")
            elif order_value > current_balance * 0.9:
                warnings.append(f"Ordre utilise >90% de la balance")

        # 6. Position limit
        new_position_value = current_position_value + order_value
        if new_position_value > self.max_position_per_market:
            errors.append(
                f"Position limit dépassée: ${new_position_value:.2f} > ${self.max_position_per_market}"
            )

        # 7. Slippage (si prix attendu fourni)
        if expected_price is not None and expected_price > 0:
            slippage_pct = abs(price - expected_price) / expected_price * 100
            if slippage_pct > self.max_slippage_pct:
                errors.append(
                    f"Slippage trop élevé: {slippage_pct:.2f}% > {self.max_slippage_pct}%"
                )
            elif slippage_pct > self.max_slippage_pct * 0.5:
                warnings.append(f"Slippage élevé: {slippage_pct:.2f}%")

        result = OrderValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )

        # Logger les résultats
        if not result.valid:
            self._logger.warning(
                f"Order validation FAILED: {errors}",
                side=side, price=price, qty=qty
            )
        elif warnings:
            self._logger.info(
                f"Order validation OK with warnings: {warnings}",
                side=side, price=price, qty=qty
            )

        return result


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCES GLOBALES
# ═══════════════════════════════════════════════════════════════════════════

# Circuit Breakers pré-configurés
polymarket_circuit = CircuitBreaker("polymarket_api", CircuitBreakerConfig(
    failure_threshold=5,
    timeout_seconds=30
))

order_circuit = CircuitBreaker("order_execution", CircuitBreakerConfig(
    failure_threshold=3,
    timeout_seconds=60
))

# Validateur d'ordres
order_validator = OrderValidator()


def get_order_validator() -> OrderValidator:
    """Retourne le validateur d'ordres singleton."""
    return order_validator


def get_circuit_stats() -> dict:
    """Retourne les stats de tous les circuit breakers."""
    return {
        "polymarket_api": polymarket_circuit.get_stats(),
        "order_execution": order_circuit.get_stats()
    }
