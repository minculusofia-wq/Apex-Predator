"""
Smart Ape Engine - Stratégie Bitcoin Up/Down 15 minutes

Principe:
1. Cibler uniquement les marchés "Bitcoin Up or Down" de 15 minutes
2. Trader dans les premières minutes du round (fenêtre configurable)
3. Positions asymétriques autorisées (ex: 5 UP + 4 DOWN)
4. Profit si UP + DOWN < payout minimum garanti

Inspiré de: https://x.com/the_smart_ape
"""

import asyncio
import json
import re
from collections import deque
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

from core.order_queue import OrderPriority
from config.trading_params import get_trading_params


class SmartApeStatus(Enum):
    """États du moteur Smart Ape."""
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class SmartApeConfig:
    """
    Configuration de la stratégie Smart Ape.

    Stratégie:
    - Cibler marchés "Bitcoin Up or Down" 15min
    - Trader dans les N premières minutes du round
    - Positions asymétriques (5 UP + 4 DOWN = OK)
    - Profit si total_cost < payout_minimum
    """
    window_minutes: int = 2              # Fenêtre de trading (premières minutes)
    dump_threshold: float = 0.15         # Seuil de dump Binance (15%)
    min_payout_ratio: float = 1.5        # Ratio payout minimum (profit > 50%)
    order_size_usd: float = 25.0         # Taille de chaque ordre en USD
    max_position_usd: float = 200.0      # Position maximale par round
    max_rounds: int = 10                 # Rounds simultanés max
    persistence_file: str = "data/smart_ape_positions.json"

    # Patterns de détection des marchés cibles
    market_patterns: List[str] = field(default_factory=lambda: [
        r"bitcoin.*up.*down",
        r"btc.*up.*down",
        r"bitcoin.*15.*min",
        r"btc.*15.*min"
    ])


@dataclass
class SmartApePosition:
    """Représente une position Smart Ape en cours."""
    market_id: str
    question: str
    token_up_id: str
    token_down_id: str
    round_start: datetime

    # Quantités et coûts
    qty_up: float = 0.0
    cost_up: float = 0.0
    qty_down: float = 0.0
    cost_down: float = 0.0

    # Pending orders
    pending_qty_up: float = 0.0
    pending_qty_down: float = 0.0

    is_closed: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def total_cost(self) -> float:
        """Coût total investi."""
        return self.cost_up + self.cost_down

    @property
    def total_qty(self) -> Tuple[float, float]:
        """Retourne (qty_up, qty_down)."""
        return (self.qty_up, self.qty_down)

    @property
    def min_payout(self) -> float:
        """Payout minimum garanti (le plus petit des deux côtés)."""
        return min(self.qty_up, self.qty_down)

    @property
    def max_payout(self) -> float:
        """Payout maximum possible (le plus grand des deux côtés)."""
        return max(self.qty_up, self.qty_down)

    @property
    def expected_profit(self) -> float:
        """Profit attendu (payout min - coût total)."""
        return self.min_payout - self.total_cost

    @property
    def profit_ratio(self) -> float:
        """Ratio de profit (payout / cost)."""
        if self.total_cost <= 0:
            return 0.0
        return self.min_payout / self.total_cost

    @property
    def is_profitable(self) -> bool:
        """True si la position est profitable."""
        return self.expected_profit > 0

    @property
    def round_age_minutes(self) -> float:
        """Âge du round en minutes."""
        return (datetime.now() - self.round_start).total_seconds() / 60

    def to_dict(self) -> dict:
        data = asdict(self)
        data["round_start"] = self.round_start.isoformat()
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SmartApePosition":
        data["round_start"] = datetime.fromisoformat(data["round_start"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


class SmartApeEngine:
    """Moteur de la stratégie Smart Ape."""

    def __init__(self, config: SmartApeConfig = None, executor=None, oracle=None):
        self.config = config or SmartApeConfig()
        self.executor = executor
        self.oracle = oracle  # BinanceOracle pour détection de dump
        self.positions: Dict[str, SmartApePosition] = {}
        self._is_running = False
        self._persistence_path = Path(self.config.persistence_file)
        self._lock = asyncio.Lock()

        # Statistiques
        self._stats = {
            "total_rounds": 0,
            "profitable_rounds": 0,
            "total_pnl": 0.0,
            "trades_executed": 0
        }

        # Patterns compilés pour performance
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self.config.market_patterns
        ]

    def is_target_market(self, question: str) -> bool:
        """
        Vérifie si le marché est un "Bitcoin Up or Down" 15 minutes.

        Retourne True si le marché correspond aux patterns cibles.
        """
        if not question:
            return False

        q = question.lower()

        # Vérification rapide des mots-clés essentiels
        has_bitcoin = "bitcoin" in q or "btc" in q
        has_up_down = "up" in q and "down" in q
        has_15min = "15" in q or "fifteen" in q

        if has_bitcoin and has_up_down and has_15min:
            return True

        # Vérification par patterns regex (backup)
        for pattern in self._compiled_patterns:
            if pattern.search(question):
                return True

        return False

    def _extract_round_info(self, question: str) -> Optional[datetime]:
        """
        Extrait l'heure de début du round depuis la question.

        Ex: "Bitcoin Up or Down 15min (14:30 UTC)" -> datetime(14:30)
        """
        # Pattern pour extraire l'heure
        time_pattern = r"(\d{1,2}):(\d{2})"
        match = re.search(time_pattern, question)

        if match:
            hour, minute = int(match.group(1)), int(match.group(2))
            now = datetime.now()
            round_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # Si l'heure est dans le passé de plus de 12h, c'est probablement demain
            if (now - round_start).total_seconds() > 12 * 3600:
                round_start = round_start.replace(day=now.day + 1)

            return round_start

        # Fallback: utiliser l'heure actuelle
        return datetime.now()

    async def start(self):
        """Démarre le moteur Smart Ape."""
        async with self._lock:
            self._load_positions()
            self._is_running = True

            if self.executor:
                self.executor.on_fill = self._on_fill_callback
                self.executor.on_order_end = self._on_order_end_callback

            print(f"🦍 Smart Ape Engine démarré. {len(self.positions)} positions chargées.")

    async def stop(self):
        """Arrête le moteur Smart Ape."""
        async with self._lock:
            if self.executor:
                self.executor.on_fill = None
                self.executor.on_order_end = None

            self._save_positions()
            self._is_running = False
            print("🦍 Smart Ape Engine arrêté. Positions sauvegardées.")

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def status(self) -> SmartApeStatus:
        if self._is_running:
            return SmartApeStatus.RUNNING
        return SmartApeStatus.STOPPED

    def set_executor(self, executor):
        """Configure l'executor pour les ordres."""
        self.executor = executor
        if self._is_running:
            self.executor.on_fill = self._on_fill_callback
            self.executor.on_order_end = self._on_order_end_callback

    async def _on_fill_callback(self, market_id: str, side: str, filled_qty: float, price: float):
        """Callback appelé quand un ordre est rempli."""
        async with self._lock:
            position = self.positions.get(market_id)
            if not position:
                return

            print(f"💰 [SmartApe] FILL: {side} +{filled_qty:.1f} @ ${price:.3f} ({market_id[:8]}...)")

            cost = filled_qty * price

            if side.upper() in ("UP", "YES"):
                position.qty_up += filled_qty
                position.cost_up += cost
                position.pending_qty_up = max(0, position.pending_qty_up - filled_qty)
            else:  # DOWN / NO
                position.qty_down += filled_qty
                position.cost_down += cost
                position.pending_qty_down = max(0, position.pending_qty_down - filled_qty)

            position.updated_at = datetime.now()
            self._stats["trades_executed"] += 1
            self._save_positions()

    async def _on_order_end_callback(self, market_id: str, side: str, remaining_qty: float):
        """Callback quand un ordre se termine (cancel/expire)."""
        async with self._lock:
            position = self.positions.get(market_id)
            if not position:
                return

            if side.upper() in ("UP", "YES"):
                position.pending_qty_up = max(0, position.pending_qty_up - remaining_qty)
            else:
                position.pending_qty_down = max(0, position.pending_qty_down - remaining_qty)

            self._save_positions()

    async def analyze_opportunity(
        self,
        market_id: str,
        token_up_id: str,
        token_down_id: str,
        price_up: float,
        price_down: float,
        question: str
    ) -> Tuple[Optional[str], float]:
        """
        Analyse un marché Smart Ape et décide s'il faut acheter.

        Retourne (decision, size_usd):
        - decision: "buy_up", "buy_down", ou None
        - size_usd: montant à investir
        """
        if not self._is_running:
            return None, 0.0

        # Vérifier si c'est un marché cible
        if not self.is_target_market(question):
            return None, 0.0

        params = get_trading_params()

        async with self._lock:
            position = self.positions.get(market_id)

            # Créer ou récupérer la position
            if not position:
                round_start = self._extract_round_info(question)
                position = SmartApePosition(
                    market_id=market_id,
                    question=question,
                    token_up_id=token_up_id,
                    token_down_id=token_down_id,
                    round_start=round_start
                )
                self.positions[market_id] = position
                self._stats["total_rounds"] += 1

            # Vérifier la fenêtre de trading
            if position.round_age_minutes > self.config.window_minutes:
                # Hors fenêtre de trading - ne pas entrer
                return None, 0.0

            # Vérifier la position maximale
            if position.total_cost >= self.config.max_position_usd:
                return None, 0.0

            # Calculer le payout ratio potentiel
            total_price = price_up + price_down
            potential_payout_ratio = 1.0 / total_price if total_price > 0 else 0

            # Vérifier si le ratio est suffisant
            if potential_payout_ratio < self.config.min_payout_ratio:
                return None, 0.0

            # Décision: acheter le côté le moins cher ou équilibrer
            order_size = min(
                self.config.order_size_usd,
                self.config.max_position_usd - position.total_cost
            )

            if order_size < 5.0:  # Minimum $5
                return None, 0.0

            # Stratégie: favoriser le côté le moins cher
            if price_up < price_down:
                # UP est moins cher
                if position.qty_up < position.qty_down * 1.5:  # Limite asymétrie
                    return "buy_up", order_size
            else:
                # DOWN est moins cher
                if position.qty_down < position.qty_up * 1.5:  # Limite asymétrie
                    return "buy_down", order_size

            # Si équilibré, acheter les deux
            if position.qty_up <= position.qty_down:
                return "buy_up", order_size
            else:
                return "buy_down", order_size

    async def place_order(self, market_id: str, side: str, price: float, qty: float) -> bool:
        """Place un ordre Smart Ape."""
        params = get_trading_params()

        if not params.auto_trading_enabled:
            print(f"⏸️ [SmartApe] Auto Trading OFF - Ordre {side} ignoré")
            return False

        if not self.executor:
            print("⚠️ [SmartApe] Executor non configuré. Mode Simulation.")
            return False

        position = self.positions.get(market_id)
        if not position:
            return False

        if price <= 0:
            print(f"❌ [SmartApe] Prix invalide: {price}")
            return False

        # Mettre à jour pending
        cost = qty * price
        if side.upper() in ("UP", "YES"):
            position.pending_qty_up += qty
        else:
            position.pending_qty_down += qty

        position.updated_at = datetime.now()
        self._save_positions()

        # Envoyer l'ordre
        token_id = position.token_up_id if side.upper() in ("UP", "YES") else position.token_down_id

        await self.executor.queue_order(
            token_id=token_id,
            side="BUY",
            price=price,
            size=qty,
            priority=OrderPriority.NORMAL,
            market_id=market_id,
            metadata={"strategy": "smart_ape", "side": side}
        )

        print(f"🦍 [SmartApe] Ordre {side} envoyé: {qty:.0f} @ ${price:.3f} ({market_id[:8]}...)")
        return True

    async def buy_up(self, market_id: str, price: float, qty: float) -> bool:
        """Achète des tokens UP."""
        return await self.place_order(market_id, "UP", price, qty)

    async def buy_down(self, market_id: str, price: float, qty: float) -> bool:
        """Achète des tokens DOWN."""
        return await self.place_order(market_id, "DOWN", price, qty)

    def get_stats(self) -> dict:
        """Retourne les statistiques du moteur."""
        active_positions = [p for p in self.positions.values() if not p.is_closed]
        closed_positions = [p for p in self.positions.values() if p.is_closed]

        total_pnl = sum(p.expected_profit for p in closed_positions if p.is_profitable)

        return {
            "status": self.status.value,
            "active_rounds": len(active_positions),
            "closed_rounds": len(closed_positions),
            "total_pnl": total_pnl,
            "trades_executed": self._stats["trades_executed"],
            "win_rate": (
                self._stats["profitable_rounds"] / max(1, self._stats["total_rounds"]) * 100
            )
        }

    def get_positions_summary(self) -> List[dict]:
        """Retourne un résumé des positions."""
        return [
            {
                **p.to_dict(),
                "profit_ratio": p.profit_ratio,
                "expected_profit": p.expected_profit,
                "is_profitable": p.is_profitable
            }
            for p in sorted(
                self.positions.values(),
                key=lambda x: x.created_at,
                reverse=True
            )
        ]

    def _save_positions(self):
        """Sauvegarde les positions."""
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "positions": {
                mid: pos.to_dict()
                for mid, pos in self.positions.items()
            },
            "stats": self._stats
        }
        with open(self._persistence_path, "w") as f:
            json.dump(data, f, indent=2)

    def _load_positions(self):
        """Charge les positions."""
        if not self._persistence_path.exists():
            return

        try:
            with open(self._persistence_path, "r") as f:
                data = json.load(f)

            if "positions" in data:
                self.positions = {
                    mid: SmartApePosition.from_dict(pos_data)
                    for mid, pos_data in data["positions"].items()
                }
            if "stats" in data:
                self._stats.update(data["stats"])

        except (json.JSONDecodeError, TypeError, KeyError) as e:
            print(f"⚠️ [SmartApe] Erreur chargement positions: {e}")
            self.positions = {}
