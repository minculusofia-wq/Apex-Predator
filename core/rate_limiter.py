"""
Rate Limiter - Protection contre les bans API (v7.3)

Implémente un Token Bucket Rate Limiter pour contrôler
le débit des requêtes vers l'API Polymarket.

Usage:
    limiter = TokenBucketRateLimiter(tokens_per_second=10, max_tokens=20)
    await limiter.acquire()  # Attend si nécessaire
    # Faire la requête API
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class RateLimiterStats:
    """Statistiques du rate limiter."""
    total_requests: int = 0
    total_waits: int = 0
    total_wait_time_ms: float = 0.0
    current_tokens: float = 0.0
    last_request_time: Optional[float] = None

    @property
    def avg_wait_time_ms(self) -> float:
        if self.total_waits == 0:
            return 0.0
        return self.total_wait_time_ms / self.total_waits


class TokenBucketRateLimiter:
    """
    Token Bucket Rate Limiter.

    Contrôle le débit des requêtes en utilisant un bucket de tokens
    qui se remplit à un taux constant.

    Args:
        tokens_per_second: Taux de remplissage (tokens/seconde)
        max_tokens: Capacité maximale du bucket
        initial_tokens: Tokens initiaux (défaut = max_tokens)
    """

    def __init__(
        self,
        tokens_per_second: float = 10.0,
        max_tokens: int = 20,
        initial_tokens: Optional[int] = None
    ):
        self.tokens_per_second = tokens_per_second
        self.max_tokens = max_tokens
        self._tokens = float(initial_tokens if initial_tokens is not None else max_tokens)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

        # Stats
        self._stats = RateLimiterStats(current_tokens=self._tokens)

    async def acquire(self, tokens: int = 1) -> float:
        """
        Acquiert des tokens, attend si nécessaire.

        Args:
            tokens: Nombre de tokens à acquérir (défaut 1)

        Returns:
            Temps d'attente en secondes (0 si pas d'attente)
        """
        async with self._lock:
            wait_time = 0.0

            # Remplir le bucket basé sur le temps écoulé
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(self.max_tokens, self._tokens + elapsed * self.tokens_per_second)
            self._last_update = now

            # Si pas assez de tokens, calculer le temps d'attente
            if self._tokens < tokens:
                deficit = tokens - self._tokens
                wait_time = deficit / self.tokens_per_second

                # Attendre
                await asyncio.sleep(wait_time)

                # Mettre à jour après l'attente
                self._tokens = min(self.max_tokens, self._tokens + wait_time * self.tokens_per_second)
                self._last_update = time.monotonic()

                # Stats
                self._stats.total_waits += 1
                self._stats.total_wait_time_ms += wait_time * 1000

            # Consommer les tokens
            self._tokens -= tokens

            # Stats
            self._stats.total_requests += 1
            self._stats.current_tokens = self._tokens
            self._stats.last_request_time = time.time()

            return wait_time

    def try_acquire(self, tokens: int = 1) -> bool:
        """
        Tente d'acquérir des tokens sans attendre.

        Returns:
            True si acquis, False si pas assez de tokens
        """
        # Note: Version synchrone pour vérification rapide
        now = time.monotonic()
        elapsed = now - self._last_update
        available = min(self.max_tokens, self._tokens + elapsed * self.tokens_per_second)

        if available >= tokens:
            return True
        return False

    @property
    def available_tokens(self) -> float:
        """Retourne le nombre de tokens actuellement disponibles."""
        now = time.monotonic()
        elapsed = now - self._last_update
        return min(self.max_tokens, self._tokens + elapsed * self.tokens_per_second)

    @property
    def stats(self) -> RateLimiterStats:
        """Retourne les statistiques."""
        self._stats.current_tokens = self.available_tokens
        return self._stats

    def reset(self):
        """Reset le rate limiter à sa capacité maximale."""
        self._tokens = float(self.max_tokens)
        self._last_update = time.monotonic()


class AdaptiveRateLimiter(TokenBucketRateLimiter):
    """
    Rate Limiter adaptatif qui ajuste son taux en fonction des réponses API.

    - Réduit le taux si on reçoit des erreurs 429 (rate limited)
    - Augmente progressivement si tout va bien
    """

    def __init__(
        self,
        tokens_per_second: float = 10.0,
        max_tokens: int = 20,
        min_rate: float = 1.0,
        max_rate: float = 20.0,
        backoff_factor: float = 0.5,
        recovery_factor: float = 1.1
    ):
        super().__init__(tokens_per_second, max_tokens)
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.backoff_factor = backoff_factor
        self.recovery_factor = recovery_factor
        self._consecutive_successes = 0
        self._recovery_threshold = 10  # Succès avant d'augmenter le taux

    def on_rate_limited(self):
        """Appelé quand on reçoit une erreur 429."""
        self.tokens_per_second = max(
            self.min_rate,
            self.tokens_per_second * self.backoff_factor
        )
        self._consecutive_successes = 0
        print(f"⚠️ [RateLimiter] Rate limited! Nouveau taux: {self.tokens_per_second:.1f}/s")

    def on_success(self):
        """Appelé après une requête réussie."""
        self._consecutive_successes += 1

        if self._consecutive_successes >= self._recovery_threshold:
            old_rate = self.tokens_per_second
            self.tokens_per_second = min(
                self.max_rate,
                self.tokens_per_second * self.recovery_factor
            )
            if self.tokens_per_second > old_rate:
                print(f"✅ [RateLimiter] Recovery! Nouveau taux: {self.tokens_per_second:.1f}/s")
            self._consecutive_successes = 0


# Instance globale pour l'API Polymarket
_polymarket_rate_limiter: Optional[TokenBucketRateLimiter] = None


def get_polymarket_rate_limiter() -> TokenBucketRateLimiter:
    """Retourne le rate limiter global pour Polymarket."""
    global _polymarket_rate_limiter
    if _polymarket_rate_limiter is None:
        # Polymarket semble avoir une limite de ~10 req/s
        _polymarket_rate_limiter = AdaptiveRateLimiter(
            tokens_per_second=8.0,   # Conservateur
            max_tokens=15,           # Burst capacity
            min_rate=2.0,            # Minimum en cas de rate limit
            max_rate=12.0            # Maximum
        )
    return _polymarket_rate_limiter


def reset_polymarket_rate_limiter():
    """Reset le rate limiter global."""
    global _polymarket_rate_limiter
    if _polymarket_rate_limiter:
        _polymarket_rate_limiter.reset()
