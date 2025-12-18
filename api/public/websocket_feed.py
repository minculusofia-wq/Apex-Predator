"""
WebSocket Feed - Flux temps réel des prix et orderbooks

Se connecte au WebSocket Polymarket pour recevoir:
- Updates de prix en temps réel
- Changements d'orderbook
- Trades exécutés

Permet une réaction rapide aux changements de marché.
"""

import asyncio
import ssl
import websockets
from typing import Optional, Callable, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from config import get_settings
from core.performance import json_loads, json_dumps  # 5.7: orjson pour parsing rapide


class MessageType(Enum):
    """Types de messages WebSocket."""
    PRICE_UPDATE = "price"
    BOOK_UPDATE = "book"
    TRADE = "trade"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


@dataclass
class PriceUpdate:
    """Mise à jour de prix."""
    token_id: str
    price: float
    timestamp: datetime


@dataclass
class BookUpdate:
    """Mise à jour d'orderbook."""
    token_id: str
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    timestamp: datetime


@dataclass
class TradeUpdate:
    """Notification de trade."""
    token_id: str
    price: float
    size: float
    side: str  # "buy" ou "sell"
    timestamp: datetime


class WebSocketFeed:
    """
    Connexion WebSocket pour données temps réel.
    
    Usage:
        feed = WebSocketFeed()
        feed.on_price_update = my_callback
        await feed.connect()
        await feed.subscribe(["token_id_1", "token_id_2"])
        await feed.listen()
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.ws_url = self.settings.polymarket_ws_url
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscriptions: set[str] = set()
        self._running: bool = False
        self._reconnect_attempts: int = 0
        self._max_reconnect_attempts: int = 3  # Réduit pour fallback rapide vers REST
        self._connection_failed_permanently: bool = False  # Si True, ne plus tenter de reconnecter

        # Callbacks
        self.on_price_update: Optional[Callable[[PriceUpdate], None]] = None
        self.on_book_update: Optional[Callable[[BookUpdate], None]] = None
        self.on_trade: Optional[Callable[[TradeUpdate], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None
    
    @property
    def is_connected(self) -> bool:
        """Vérifie si le WebSocket est connecté (compatible websockets 15.x)."""
        if self._ws is None:
            return False
        # websockets 15.x: utiliser state au lieu de open
        try:
            from websockets.protocol import State
            return self._ws.state == State.OPEN
        except (ImportError, AttributeError):
            # Fallback pour anciennes versions
            return hasattr(self._ws, 'open') and self._ws.open
    
    async def connect(self) -> bool:
        """
        Établit la connexion WebSocket.

        Returns:
            True si connecté, False sinon
        """
        # Si on a abandonné définitivement, ne pas réessayer
        if self._connection_failed_permanently:
            return False

        try:
            # SSL context pour contourner les problèmes de certificat (VPN)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            self._ws = await websockets.connect(
                self.ws_url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
                open_timeout=10,  # Réduit pour fail fast
                ssl=ssl_context,
                additional_headers={
                    "User-Agent": "HFT-Scalper-Bot/2.0",
                    "Origin": "https://polymarket.com"
                }
            )
            self._running = True
            self._reconnect_attempts = 0

            if self.on_connect and self._reconnect_attempts == 0:
                self.on_connect() # Log uniquement à la connexion initiale ou première reconnexion

            return True
        except asyncio.TimeoutError:
            # Timeout = probablement pas de WebSocket disponible
            return False
        except Exception as e:
            if self.on_error:
                self.on_error(e)
            return False
    
    async def disconnect(self) -> None:
        """Ferme la connexion WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        if self.on_disconnect:
            self.on_disconnect()
    
    async def subscribe(self, token_ids: list[str]) -> None:
        """
        S'abonne aux updates pour les tokens spécifiés.
        
        Args:
            token_ids: Liste des IDs de tokens à suivre
        """
        if not self.is_connected:
            raise RuntimeError("WebSocket non connecté")
        
        for token_id in token_ids:
            if token_id not in self._subscriptions:
                message = {
                    "type": "subscribe",
                    "channel": "price",
                    "token_id": token_id
                }
                await self._ws.send(json_dumps(message))
                self._subscriptions.add(token_id)
                # Rate Limit Protection: 50 msg/sec max pour éviter le ban 429
                await asyncio.sleep(0.02)
    
    async def unsubscribe(self, token_ids: list[str]) -> None:
        """Se désabonne des tokens spécifiés."""
        if not self.is_connected:
            return
        
        for token_id in token_ids:
            if token_id in self._subscriptions:
                message = {
                    "type": "unsubscribe",
                    "channel": "price",
                    "token_id": token_id
                }
                await self._ws.send(json_dumps(message))
                self._subscriptions.discard(token_id)
    
    async def listen(self) -> None:
        """
        Écoute les messages WebSocket en continu.

        Boucle infinie avec reconnexion automatique.
        Sort de la boucle si connexion échoue définitivement.
        """
        while self._running and not self._connection_failed_permanently:
            try:
                if not self.is_connected:
                    connected = await self._reconnect()
                    if not connected:
                        if self._connection_failed_permanently:
                            # Sortir de la boucle silencieusement
                            break
                        await asyncio.sleep(5)
                        continue

                # Écoute des messages
                async for message in self._ws:
                    if not self._running:
                        break

                    await self._handle_message(message)

            except websockets.exceptions.ConnectionClosed:
                if self.on_disconnect:
                    self.on_disconnect()

                if self._running and not self._connection_failed_permanently:
                    await self._reconnect()

            except Exception as e:
                if self.on_error and not self._connection_failed_permanently:
                    self.on_error(e)

                if not self._connection_failed_permanently:
                    await asyncio.sleep(1)
    
    async def _reconnect(self) -> bool:
        """Tente de se reconnecter."""
        # Si déjà abandonné, ne pas réessayer
        if self._connection_failed_permanently:
            return False

        self._reconnect_attempts += 1

        if self._reconnect_attempts > self._max_reconnect_attempts:
            self._running = False
            self._connection_failed_permanently = True
            print("⚠️ [WS] Max reconnexions atteint - Mode REST uniquement (silencieux)")
            return False

        # Backoff exponentiel mais court
        wait_time = min(10, 2 ** self._reconnect_attempts)
        await asyncio.sleep(wait_time)

        connected = await self.connect()

        # Re-souscrire aux tokens
        if connected and self._subscriptions:
            for token_id in list(self._subscriptions):
                try:
                    message = {
                        "type": "subscribe",
                        "channel": "price",
                        "token_id": token_id
                    }
                    await self._ws.send(json_dumps(message))
                except Exception:
                    pass

        return connected
    
    async def _handle_message(self, raw_message: str) -> None:
        """Traite un message WebSocket."""
        try:
            data = json_loads(raw_message)  # 5.7: orjson 3x plus rapide
            msg_type = data.get("type", data.get("event", ""))
            
            if msg_type in ["price", "price_update"]:
                update = PriceUpdate(
                    token_id=data.get("asset_id", data.get("token_id", "")),
                    price=float(data.get("price", 0)),
                    timestamp=datetime.now()
                )
                if self.on_price_update:
                    self.on_price_update(update)
            
            elif msg_type in ["book", "book_update"]:
                update = BookUpdate(
                    token_id=data.get("asset_id", data.get("token_id", "")),
                    bids=[(float(b[0]), float(b[1])) for b in data.get("bids", [])],
                    asks=[(float(a[0]), float(a[1])) for a in data.get("asks", [])],
                    timestamp=datetime.now()
                )
                if self.on_book_update:
                    self.on_book_update(update)
            
            elif msg_type == "trade":
                update = TradeUpdate(
                    token_id=data.get("asset_id", data.get("token_id", "")),
                    price=float(data.get("price", 0)),
                    size=float(data.get("size", 0)),
                    side=data.get("side", ""),
                    timestamp=datetime.now()
                )
                if self.on_trade:
                    self.on_trade(update)
            
            elif msg_type == "heartbeat":
                pass  # Ignorer les heartbeats
            
            elif msg_type == "error":
                error = Exception(data.get("message", "WebSocket error"))
                if self.on_error:
                    self.on_error(error)
        
        except (ValueError, TypeError):
            # orjson/json decode errors
            pass
        except Exception as e:
            if self.on_error:
                self.on_error(e)
    
    async def send_heartbeat(self) -> None:
        """Envoie un heartbeat pour maintenir la connexion."""
        if self.is_connected:
            try:
                await self._ws.send(json_dumps({"type": "ping"}))
            except Exception:
                pass
