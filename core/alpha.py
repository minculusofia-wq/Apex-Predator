"""
Alpha Module - Signaux HFT Prédictifs (Lead-Lag)

Ce module fournit des signaux "Alpha" en connectant des sources de données
plus rapides que la blockchain (ex: Binance CEX) pour anticiper les mouvements
sur Polymarket.
"""

import asyncio
import json
import time
from typing import Dict, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import websockets
from datetime import datetime

class AlphaSignal(Enum):
    BUY = "BUY"       # Signal d'achat fort (Lead pump)
    SELL = "SELL"     # Signal de vente fort (Lead dump)
    NEUTRAL = "NEUTRAL"

@dataclass
class AlphaData:
    timestamp: float
    price: float
    delta_1s: float  # Chg% sur 1 seconde
    signal: AlphaSignal

class BinanceOracle:
    """
    Oracle connecté au WebSocket Binance pour détecter les mouvements
    de prix AVANT qu'ils ne se répercutent sur Polymarket/Polygon.
    """
    def __init__(self, symbols: list[str] = ["btcusdt", "ethusdt", "solusdt"]):
        self.symbols = [s.lower() for s in symbols]
        self.ws_url = f"wss://stream.binance.com:9443/stream?streams={'/'.join([s + '@trade' for s in self.symbols])}"
        self._prices: Dict[str, float] = {}
        self._history: Dict[str, list[tuple[float, float]]] = {s: [] for s in self.symbols} # (ts, price)
        self._signals: Dict[str, AlphaSignal] = {s: AlphaSignal.NEUTRAL for s in self.symbols}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_update: float = 0
        
        # Seuils HFT (Configurable)
        self.PUMP_THRESHOLD_1S = 0.003  # +0.3% en 1s = HUGE pump
        self.DUMP_THRESHOLD_1S = -0.003 # -0.3% en 1s = HUGE dump

    async def start(self):
        """Démarre le flux WebSocket Binance."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._connect())
        print(f"📡 Binance Oracle démarré sur {self.symbols}")

    async def stop(self):
        """Arrête le flux."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print("📡 Binance Oracle arrêté")

    async def _connect(self):
        while self._running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    print("✅ Binance CEX Connected (Lead-Lag Source)")
                    while self._running:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        await self._process_message(data)
            except Exception as e:
                print(f"⚠️ Binance WS Erreur: {e}. Reconnexion dans 2s...")
                await asyncio.sleep(2)

    async def _process_message(self, msg: dict):
        """Traite un message trade de Binance."""
        # Format: {"stream": "btcusdt@trade", "data": {"p": "100000.00", "T": 123456789}}
        if "data" not in msg:
            return

        payload = msg["data"]
        symbol = payload["s"].lower() # BTCUSDT -> btcusdt
        price = float(payload["p"])
        now = time.time()

        # Update Last Price
        self._prices[symbol] = price
        self._last_update = now

        # Update History (Garder 5 sec max)
        hist = self._history.get(symbol, [])
        hist.append((now, price))
        
        # Nettoyer vieux historique (> 2s suffisent pour le calcul)
        cutoff = now - 2.0
        self._history[symbol] = [h for h in hist if h[0] > cutoff]
        
        # Calculer Delta 1s
        delta_1s = 0.0
        # Trouver le prix d'il y a ~1s
        target_ts = now - 1.0
        
        # Chercher le point le plus proche de target_ts
        past_price = None
        for ts, p in self._history[symbol]:
            if ts >= target_ts:
                past_price = p
                break
        
        if past_price:
            delta_1s = (price - past_price) / past_price

        # Générer Signal
        new_signal = AlphaSignal.NEUTRAL
        if delta_1s > self.PUMP_THRESHOLD_1S:
            new_signal = AlphaSignal.BUY
            # Log seulement sur changement d'état pour éviter spam
            if self._signals[symbol] != AlphaSignal.BUY:
                print(f"🚨 [LEAD-LAG] {symbol.upper()} PUMP détecté! (+{delta_1s*100:.2f}% en 1s) -> SIGNAL BUY")
        elif delta_1s < self.DUMP_THRESHOLD_1S:
            new_signal = AlphaSignal.SELL
            if self._signals[symbol] != AlphaSignal.SELL:
                print(f"🚨 [LEAD-LAG] {symbol.upper()} DUMP détecté! ({delta_1s*100:.2f}% en 1s) -> SIGNAL SELL")
        
        self._signals[symbol] = new_signal

    def get_price(self, symbol: str) -> float:
        """Retourne le dernier prix connu."""
        return self._prices.get(symbol.lower(), 0.0)

    def get_signal(self, symbol: str) -> AlphaSignal:
        """Retourne le signal actuel pour un symbole (ex: 'btcusdt')."""
        return self._signals.get(symbol.lower(), AlphaSignal.NEUTRAL)
