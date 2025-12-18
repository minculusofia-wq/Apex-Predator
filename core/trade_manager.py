"""
Trade Manager - Gestion des trades avec Stop-Loss / Take-Profit

Fonctionnalités:
1. Enregistrer les trades entrés
2. Suivre les P&L en temps réel
3. Stop-Loss automatique (fermeture si perte > seuil)
4. Take-Profit automatique (fermeture si gain > seuil)
5. Trailing Stop optionnel
6. Sortie manuelle toujours possible
"""

from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import asyncio
from pathlib import Path
from api.private import PolymarketPrivateClient


class TradeStatus(Enum):
    """États d'un trade."""
    PENDING = "pending"           # En attente d'exécution
    ACTIVE = "active"             # Position ouverte
    CLOSED = "closed"             # Fermé manuellement
    STOPPED_OUT = "stopped_out"   # Fermé par Stop-Loss
    TAKE_PROFIT = "take_profit"   # Fermé par Take-Profit
    TRAILING_STOP = "trailing"    # Fermé par Trailing Stop
    CANCELLED = "cancelled"       # Annulé


class TradeSide(Enum):
    """Côté du trade."""
    YES = "yes"
    NO = "no"


class CloseReason(Enum):
    """Raison de la fermeture."""
    MANUAL = "manual"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TRAILING_STOP = "trailing_stop"
    TIMEOUT = "timeout"


@dataclass
class Trade:
    """Représente un trade actif ou historique."""

    id: str
    market_id: str
    market_question: str

    # Position
    side: TradeSide
    entry_price: float
    size: float  # Nombre de shares

    # Prix actuel (mis à jour en temps réel)
    current_price: float = 0.0

    # Stop-Loss / Take-Profit
    stop_loss: Optional[float] = None      # Prix de stop-loss (ex: 0.40 si entry 0.50)
    take_profit: Optional[float] = None    # Prix de take-profit (ex: 0.65 si entry 0.50)
    trailing_stop_pct: Optional[float] = None  # % de trailing stop (ex: 0.10 = 10%)
    highest_price: float = 0.0             # Plus haut prix atteint (pour trailing)

    # Timing
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: Optional[datetime] = None
    max_duration_seconds: int = 0          # 0 = pas de timeout

    # Status
    status: TradeStatus = TradeStatus.ACTIVE
    close_reason: Optional[CloseReason] = None

    # P&L
    exit_price: Optional[float] = None
    
    @property
    def unrealized_pnl(self) -> float:
        """P&L non réalisé."""
        if self.status != TradeStatus.ACTIVE:
            return 0.0
        return (self.current_price - self.entry_price) * self.size
    
    @property
    def realized_pnl(self) -> float:
        """P&L réalisé (après fermeture)."""
        if self.status != TradeStatus.CLOSED or self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.size
    
    @property
    def pnl_percent(self) -> float:
        """P&L en pourcentage."""
        if self.entry_price == 0:
            return 0.0
        if self.status == TradeStatus.ACTIVE:
            return ((self.current_price - self.entry_price) / self.entry_price) * 100
        elif self.exit_price:
            return ((self.exit_price - self.entry_price) / self.entry_price) * 100
        return 0.0
    
    @property
    def duration_seconds(self) -> int:
        """Durée du trade en secondes."""
        end = self.closed_at or datetime.now()
        return int((end - self.opened_at).total_seconds())
    
    @property
    def trailing_stop_price(self) -> Optional[float]:
        """Calcule le prix de trailing stop actuel."""
        if not self.trailing_stop_pct or self.highest_price <= 0:
            return None
        return self.highest_price * (1 - self.trailing_stop_pct)

    def should_stop_loss(self) -> bool:
        """Vérifie si le stop-loss doit être déclenché."""
        if not self.stop_loss or self.status != TradeStatus.ACTIVE:
            return False
        return self.current_price <= self.stop_loss

    def should_take_profit(self) -> bool:
        """Vérifie si le take-profit doit être déclenché."""
        if not self.take_profit or self.status != TradeStatus.ACTIVE:
            return False
        return self.current_price >= self.take_profit

    def should_trailing_stop(self) -> bool:
        """Vérifie si le trailing stop doit être déclenché."""
        trailing_price = self.trailing_stop_price
        if not trailing_price or self.status != TradeStatus.ACTIVE:
            return False
        return self.current_price <= trailing_price

    def should_timeout(self) -> bool:
        """Vérifie si le trade a expiré."""
        if self.max_duration_seconds <= 0 or self.status != TradeStatus.ACTIVE:
            return False
        return self.duration_seconds >= self.max_duration_seconds

    def check_exit_conditions(self) -> Optional[CloseReason]:
        """
        Vérifie toutes les conditions de sortie.

        Returns:
            CloseReason si une condition est remplie, None sinon
        """
        if self.should_stop_loss():
            return CloseReason.STOP_LOSS
        if self.should_take_profit():
            return CloseReason.TAKE_PROFIT
        if self.should_trailing_stop():
            return CloseReason.TRAILING_STOP
        if self.should_timeout():
            return CloseReason.TIMEOUT
        return None

    def to_dict(self) -> dict:
        """Convertit en dictionnaire."""
        return {
            "id": self.id,
            "market_id": self.market_id,
            "market_question": self.market_question,
            "side": self.side.value,
            "entry_price": self.entry_price,
            "size": self.size,
            "current_price": self.current_price,
            # Stop-Loss / Take-Profit
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "trailing_stop_pct": self.trailing_stop_pct,
            "trailing_stop_price": self.trailing_stop_price,
            "highest_price": self.highest_price,
            # Timing
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "max_duration_seconds": self.max_duration_seconds,
            # Status
            "status": self.status.value,
            "close_reason": self.close_reason.value if self.close_reason else None,
            "exit_price": self.exit_price,
            # P&L
            "unrealized_pnl": round(self.unrealized_pnl, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "pnl_percent": round(self.pnl_percent, 2),
            "duration_seconds": self.duration_seconds
        }


class TradeManager:
    """
    Gestionnaire de trades avec Stop-Loss / Take-Profit automatiques.

    Usage:
        manager = TradeManager()
        trade = await manager.open_trade(
            market_id, side, entry_price, size,
            stop_loss=0.40,      # Ferme si prix <= 0.40
            take_profit=0.65,    # Ferme si prix >= 0.65
            trailing_stop_pct=0.10  # 10% trailing stop
        )

        # Lancer le monitoring en background
        asyncio.create_task(manager.monitor_trades())
    """

    # Configuration par défaut des SL/TP
    DEFAULT_STOP_LOSS_PCT = 0.15      # -15% par défaut
    DEFAULT_TAKE_PROFIT_PCT = 0.20    # +20% par défaut
    MONITOR_INTERVAL = 1.0            # Vérification chaque seconde

    def __init__(
        self,
        data_file: str = "data/trades.json",
        private_client: Optional[PolymarketPrivateClient] = None,
        auto_sl_tp: bool = True
    ):
        self._trades: Dict[str, Trade] = {}
        self._trade_counter = 0
        self._data_file = Path(data_file)
        self.private_client = private_client
        self.auto_sl_tp = auto_sl_tp  # Activer SL/TP auto par défaut
        self._monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None

        # 5.10: Index par market_id pour lookups O(1) sur price updates
        self._trades_by_market: Dict[str, List[str]] = {}  # market_id -> [trade_ids]

        # Callbacks pour notifications
        self.on_trade_closed: Optional[Callable[[Trade, CloseReason], None]] = None
        self.on_sl_triggered: Optional[Callable[[Trade], None]] = None
        self.on_tp_triggered: Optional[Callable[[Trade], None]] = None

        self._load_trades()
    
    def _load_trades(self) -> None:
        """Charge les trades depuis le fichier."""
        if self._data_file.exists():
            try:
                with open(self._data_file, "r") as f:
                    data = json.load(f)
                    # Reconstruire les trades (simplifié)
                    self._trade_counter = data.get("counter", 0)
            except Exception:
                pass

    def _save_trades_sync(self) -> None:
        """Sauvegarde synchrone des trades (interne)."""
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._data_file, "w") as f:
            json.dump({
                "counter": self._trade_counter,
                "trades": [t.to_dict() for t in self._trades.values()]
            }, f, indent=2)

    def _save_trades(self) -> None:
        """Sauvegarde les trades (lance en background si possible)."""
        # 5.3: Non-bloquant - schedule la sauvegarde en background
        try:
            loop = asyncio.get_running_loop()
            # Si dans un contexte async, exécuter en thread séparé
            loop.run_in_executor(None, self._save_trades_sync)
        except RuntimeError:
            # Pas de loop async, exécution synchrone
            self._save_trades_sync()

    async def _save_trades_async(self) -> None:
        """5.3: Sauvegarde asynchrone des trades (ne bloque pas l'event loop)."""
        await asyncio.to_thread(self._save_trades_sync)
    
    @property
    def active_trades(self) -> List[Trade]:
        """Retourne les trades actifs."""
        return [t for t in self._trades.values() if t.status == TradeStatus.ACTIVE]
    
    @property
    def closed_trades(self) -> List[Trade]:
        """Retourne les trades fermés."""
        return [t for t in self._trades.values() if t.status == TradeStatus.CLOSED]
    
    @property
    def total_unrealized_pnl(self) -> float:
        """P&L non réalisé total."""
        return sum(t.unrealized_pnl for t in self.active_trades)
    
    @property
    def total_realized_pnl(self) -> float:
        """P&L réalisé total."""
        return sum(t.realized_pnl for t in self.closed_trades)
    
    async def open_trade(
        self,
        market_id: str,
        market_question: str,
        side: TradeSide,
        entry_price: float,
        size: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        trailing_stop_pct: Optional[float] = None,
        max_duration_seconds: int = 0
    ) -> Trade:
        """
        Ouvre un nouveau trade avec SL/TP optionnels.

        Args:
            market_id: ID du marché Polymarket
            market_question: Question du marché
            side: YES ou NO
            entry_price: Prix d'entrée
            size: Nombre de shares
            stop_loss: Prix de stop-loss (None = calculé auto si auto_sl_tp)
            take_profit: Prix de take-profit (None = calculé auto si auto_sl_tp)
            trailing_stop_pct: Pourcentage trailing stop (ex: 0.10 = 10%)
            max_duration_seconds: Durée max du trade (0 = pas de limite)

        Returns:
            Le trade créé
        """
        self._trade_counter += 1
        trade_id = f"trade_{self._trade_counter}_{int(datetime.now().timestamp())}"

        # Calcul automatique SL/TP si activé et non fournis
        if self.auto_sl_tp:
            if stop_loss is None:
                stop_loss = entry_price * (1 - self.DEFAULT_STOP_LOSS_PCT)
            if take_profit is None:
                take_profit = entry_price * (1 + self.DEFAULT_TAKE_PROFIT_PCT)

        # S'assurer que SL/TP sont dans les bornes valides (0.01 - 0.99)
        if stop_loss is not None:
            stop_loss = max(0.01, min(0.99, stop_loss))
        if take_profit is not None:
            take_profit = max(0.01, min(0.99, take_profit))

        trade = Trade(
            id=trade_id,
            market_id=market_id,
            market_question=market_question,
            side=side,
            entry_price=entry_price,
            size=size,
            current_price=entry_price,
            highest_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop_pct=trailing_stop_pct,
            max_duration_seconds=max_duration_seconds,
            status=TradeStatus.ACTIVE
        )

        # Execute Real Order if client available
        if self.private_client:
            try:
                await self.private_client.create_order(market_id, side.value, entry_price, size)
                print(f"✅ Order execution sent for {trade_id}")
            except Exception as e:
                print(f"❌ Order execution failed: {e}")

        self._trades[trade_id] = trade
        self._save_trades()

        # Log SL/TP info
        sl_str = f"SL=${stop_loss:.2f}" if stop_loss else "No SL"
        tp_str = f"TP=${take_profit:.2f}" if take_profit else "No TP"
        print(f"📈 Trade ouvert: {side.value} {size} @ ${entry_price:.2f} | {sl_str} | {tp_str}")

        return trade
    
    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        reason: CloseReason = CloseReason.MANUAL
    ) -> Optional[Trade]:
        """
        Ferme un trade.

        Args:
            trade_id: ID du trade
            exit_price: Prix de sortie
            reason: Raison de la fermeture

        Returns:
            Le trade fermé ou None si non trouvé
        """
        trade = self._trades.get(trade_id)
        if not trade or trade.status != TradeStatus.ACTIVE:
            return None

        # Déterminer le statut selon la raison
        if reason == CloseReason.STOP_LOSS:
            trade.status = TradeStatus.STOPPED_OUT
        elif reason == CloseReason.TAKE_PROFIT:
            trade.status = TradeStatus.TAKE_PROFIT
        elif reason == CloseReason.TRAILING_STOP:
            trade.status = TradeStatus.TRAILING_STOP
        else:
            trade.status = TradeStatus.CLOSED

        trade.exit_price = exit_price
        trade.closed_at = datetime.now()
        trade.close_reason = reason

        self._save_trades()

        # Notifications
        pnl = trade.realized_pnl
        pnl_pct = trade.pnl_percent
        emoji = "🟢" if pnl >= 0 else "🔴"
        print(f"{emoji} Trade fermé ({reason.value}): P&L ${pnl:.2f} ({pnl_pct:+.1f}%)")

        # Callbacks
        if self.on_trade_closed:
            self.on_trade_closed(trade, reason)
        if reason == CloseReason.STOP_LOSS and self.on_sl_triggered:
            self.on_sl_triggered(trade)
        if reason == CloseReason.TAKE_PROFIT and self.on_tp_triggered:
            self.on_tp_triggered(trade)

        return trade

    async def close_trade_async(
        self,
        trade_id: str,
        exit_price: float,
        reason: CloseReason = CloseReason.MANUAL
    ) -> Optional[Trade]:
        """
        Ferme un trade avec exécution d'ordre réel.

        Args:
            trade_id: ID du trade
            exit_price: Prix de sortie
            reason: Raison de la fermeture

        Returns:
            Le trade fermé ou None
        """
        trade = self._trades.get(trade_id)
        if not trade or trade.status != TradeStatus.ACTIVE:
            return None

        # Exécuter l'ordre de vente si client disponible
        if self.private_client:
            try:
                # Vendre les shares (opposé du side d'entrée)
                sell_side = "SELL"
                await self.private_client.create_limit_order(
                    token_id=trade.market_id,
                    side=sell_side,
                    price=exit_price,
                    size=trade.size
                )
            except Exception as e:
                print(f"⚠️ Erreur exécution ordre de sortie: {e}")

        return self.close_trade(trade_id, exit_price, reason)
    
    def update_price(self, trade_id: str, current_price: float) -> Optional[CloseReason]:
        """
        Met à jour le prix actuel d'un trade et vérifie les conditions de sortie.

        Args:
            trade_id: ID du trade
            current_price: Prix actuel

        Returns:
            CloseReason si une condition de sortie est déclenchée, None sinon
        """
        trade = self._trades.get(trade_id)
        if not trade or trade.status != TradeStatus.ACTIVE:
            return None

        trade.current_price = current_price

        # Mettre à jour le plus haut prix (pour trailing stop)
        if current_price > trade.highest_price:
            trade.highest_price = current_price

        # Vérifier les conditions de sortie
        return trade.check_exit_conditions()
    
    def get_trade(self, trade_id: str) -> Optional[Trade]:
        """Récupère un trade par son ID."""
        return self._trades.get(trade_id)
    
    def get_all_trades(self) -> List[Trade]:
        """Retourne tous les trades."""
        return list(self._trades.values())
    
    def get_stats(self) -> dict:
        """Retourne les statistiques des trades."""
        active = self.active_trades
        closed = self.closed_trades

        win_trades = [t for t in closed if t.realized_pnl > 0]
        stopped_trades = [t for t in closed if t.close_reason == CloseReason.STOP_LOSS]
        tp_trades = [t for t in closed if t.close_reason == CloseReason.TAKE_PROFIT]

        return {
            "active_count": len(active),
            "closed_count": len(closed),
            "total_trades": len(self._trades),
            "unrealized_pnl": round(self.total_unrealized_pnl, 2),
            "realized_pnl": round(self.total_realized_pnl, 2),
            "win_rate": round(len(win_trades) / len(closed) * 100, 1) if closed else 0,
            "stopped_out_count": len(stopped_trades),
            "take_profit_count": len(tp_trades),
            "monitoring": self._monitoring
        }

    # ═══════════════════════════════════════════════════════════════
    # MONITORING AUTOMATIQUE SL/TP
    # ═══════════════════════════════════════════════════════════════

    async def start_monitoring(self) -> None:
        """Démarre le monitoring automatique des SL/TP."""
        if self._monitoring:
            return

        self._monitoring = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        print("🔍 Monitoring SL/TP démarré")

    async def stop_monitoring(self) -> None:
        """Arrête le monitoring automatique."""
        self._monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        print("🔍 Monitoring SL/TP arrêté")

    async def _monitor_loop(self) -> None:
        """Boucle de monitoring des conditions SL/TP."""
        while self._monitoring:
            try:
                await self._check_all_exit_conditions()
                await asyncio.sleep(self.MONITOR_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Erreur monitoring: {e}")
                await asyncio.sleep(5)

    async def _check_all_exit_conditions(self) -> None:
        """Vérifie les conditions de sortie pour tous les trades actifs."""
        for trade in self.active_trades:
            close_reason = trade.check_exit_conditions()
            if close_reason:
                await self.close_trade_async(
                    trade_id=trade.id,
                    exit_price=trade.current_price,
                    reason=close_reason
                )

    def check_and_close_trades(self, prices: Dict[str, float]) -> List[Trade]:
        """
        Vérifie et ferme les trades selon les prix fournis (mode synchrone).

        Args:
            prices: Dict {market_id: current_price}

        Returns:
            Liste des trades fermés
        """
        closed_trades = []

        for trade in self.active_trades:
            if trade.market_id in prices:
                current_price = prices[trade.market_id]
                close_reason = self.update_price(trade.id, current_price)

                if close_reason:
                    closed_trade = self.close_trade(
                        trade_id=trade.id,
                        exit_price=current_price,
                        reason=close_reason
                    )
                    if closed_trade:
                        closed_trades.append(closed_trade)

        return closed_trades

    def set_stop_loss(self, trade_id: str, stop_loss: float) -> bool:
        """Modifie le stop-loss d'un trade actif."""
        trade = self._trades.get(trade_id)
        if not trade or trade.status != TradeStatus.ACTIVE:
            return False

        trade.stop_loss = max(0.01, min(0.99, stop_loss))
        self._save_trades()
        print(f"🛡️ Stop-loss modifié: {trade_id} -> ${stop_loss:.2f}")
        return True

    def set_take_profit(self, trade_id: str, take_profit: float) -> bool:
        """Modifie le take-profit d'un trade actif."""
        trade = self._trades.get(trade_id)
        if not trade or trade.status != TradeStatus.ACTIVE:
            return False

        trade.take_profit = max(0.01, min(0.99, take_profit))
        self._save_trades()
        print(f"🎯 Take-profit modifié: {trade_id} -> ${take_profit:.2f}")
        return True

    def set_trailing_stop(self, trade_id: str, trailing_pct: float) -> bool:
        """Active/modifie le trailing stop d'un trade actif."""
        trade = self._trades.get(trade_id)
        if not trade or trade.status != TradeStatus.ACTIVE:
            return False

        trade.trailing_stop_pct = max(0.01, min(0.50, trailing_pct))
        self._save_trades()
        print(f"📈 Trailing stop activé: {trade_id} -> {trailing_pct*100:.0f}%")
        return True

    def remove_stop_loss(self, trade_id: str) -> bool:
        """Supprime le stop-loss d'un trade."""
        trade = self._trades.get(trade_id)
        if not trade or trade.status != TradeStatus.ACTIVE:
            return False

        trade.stop_loss = None
        self._save_trades()
        print(f"⚠️ Stop-loss supprimé: {trade_id}")
        return True

    def remove_take_profit(self, trade_id: str) -> bool:
        """Supprime le take-profit d'un trade."""
        trade = self._trades.get(trade_id)
        if not trade or trade.status != TradeStatus.ACTIVE:
            return False

        trade.take_profit = None
        self._save_trades()
        print(f"⚠️ Take-profit supprimé: {trade_id}")
        return True

    # ═══════════════════════════════════════════════════════════════
    # 5.10: EVENT-DRIVEN SL/TP (Réaction immédiate aux prix WebSocket)
    # ═══════════════════════════════════════════════════════════════

    def _add_to_market_index(self, trade: Trade) -> None:
        """5.10: Ajoute un trade à l'index par marché."""
        market_id = trade.market_id
        if market_id not in self._trades_by_market:
            self._trades_by_market[market_id] = []
        if trade.id not in self._trades_by_market[market_id]:
            self._trades_by_market[market_id].append(trade.id)

    def _remove_from_market_index(self, trade: Trade) -> None:
        """5.10: Retire un trade de l'index par marché."""
        market_id = trade.market_id
        if market_id in self._trades_by_market:
            if trade.id in self._trades_by_market[market_id]:
                self._trades_by_market[market_id].remove(trade.id)
            # Nettoyer si vide
            if not self._trades_by_market[market_id]:
                del self._trades_by_market[market_id]

    def get_trades_for_market(self, market_id: str) -> List[Trade]:
        """5.10: Récupère les trades actifs pour un marché spécifique (O(1) lookup)."""
        trade_ids = self._trades_by_market.get(market_id, [])
        return [
            self._trades[tid] for tid in trade_ids
            if tid in self._trades and self._trades[tid].status == TradeStatus.ACTIVE
        ]

    async def on_price_update(self, market_id: str, price: float) -> List[Trade]:
        """
        5.10: Appelé par WebSocket quand un prix change.

        Vérifie immédiatement les conditions SL/TP pour les trades
        de ce marché spécifique. Réaction en <50ms au lieu de 1000ms.

        Args:
            market_id: ID du marché mis à jour
            price: Nouveau prix

        Returns:
            Liste des trades fermés suite à ce price update
        """
        closed_trades = []

        # O(1) lookup grâce à l'index
        trades = self.get_trades_for_market(market_id)

        for trade in trades:
            # Mettre à jour le prix et vérifier les conditions
            close_reason = self.update_price(trade.id, price)

            if close_reason:
                # Fermer immédiatement
                closed_trade = await self.close_trade_async(
                    trade_id=trade.id,
                    exit_price=price,
                    reason=close_reason
                )
                if closed_trade:
                    closed_trades.append(closed_trade)
                    # Retirer de l'index
                    self._remove_from_market_index(closed_trade)

        return closed_trades

    def register_trade_for_events(self, trade: Trade) -> None:
        """5.10: Enregistre un trade pour recevoir les price updates."""
        self._add_to_market_index(trade)

    def unregister_trade_from_events(self, trade: Trade) -> None:
        """5.10: Désenregistre un trade des price updates."""
        self._remove_from_market_index(trade)
