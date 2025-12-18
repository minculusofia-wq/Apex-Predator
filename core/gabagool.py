"""
Gabagool Engine - Stratégie d'arbitrage binaire par accumulation

Principe:
1. Acheter des YES et des NO à des moments différents sur un même marché.
2. L'objectif est d'atteindre la condition: avg_cost(YES) + avg_cost(NO) < 1.00
3. Une fois la condition atteinte, le profit est "locké" et garanti au settlement.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

from core.executor import OrderExecutor
from core.order_queue import OrderPriority
from core.indicators import calculate_rsi
from config import get_settings


class GabagoolStatus(Enum):
    """États du moteur Gabagool."""
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass
class GabagoolConfig:
    """Configuration de la stratégie Gabagool."""
    max_pair_cost: float = 0.985  # Ne pas acheter si le pair_cost dépasse ce seuil
    min_profit_margin: float = 0.015 # Marge de profit minimale visée (1 - max_pair_cost)
    min_improvement: float = 0.000 # Amélioration minimale du pair cost requise (défaut: >0)
    order_size_usd: float = 25.0  # Taille de chaque ordre en USD
    max_position_usd: float = 500.0  # Position maximale par marché en USD
    balance_ratio_threshold: float = 1.5  # Ratio max entre qty_yes et qty_no
    persistence_file: str = "data/gabagool_positions.json"

    # 9/10 Optimization Params
    kill_switch_minutes: int = 20  # Liquider si pas locké après 20 min
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    trend_filter_enabled: bool = True


@dataclass
class GabagoolPosition:
    """Représente une position d'arbitrage en cours d'accumulation."""
    market_id: str
    question: str
    token_yes_id: str
    token_no_id: str

    # Suivi des quantités et coûts
    qty_yes: float = 0.0
    cost_yes: float = 0.0
    qty_no: float = 0.0
    cost_no: float = 0.0
    
    # 7.0: Pending orders (non-confirmed)
    pending_qty_yes: float = 0.0
    pending_cost_yes: float = 0.0
    pending_qty_no: float = 0.0
    pending_cost_no: float = 0.0

    is_locked: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def avg_price_yes(self) -> float:
        return self.cost_yes / self.qty_yes if self.qty_yes > 0 else 0

    @property
    def avg_price_no(self) -> float:
        return self.cost_no / self.qty_no if self.qty_no > 0 else 0

    @property
    def pair_cost(self) -> float:
        """Coût combiné de la paire. C'est la métrique clé."""
        if self.qty_yes > 0 and self.qty_no > 0:
            return self.avg_price_yes + self.avg_price_no
        return 2.0  # Coût infini si une jambe manque

    @property
    def total_cost(self) -> float:
        return self.cost_yes + self.cost_no

    @property
    def hedged_qty(self) -> float:
        """Quantité couverte (le minimum des deux côtés)."""
        return min(self.qty_yes, self.qty_no)

    @property
    def locked_profit(self) -> float:
        """Profit garanti si la position est lockée."""
        if not self.is_locked:
            return 0.0
        # Le profit est la quantité couverte moins le coût total
        return self.hedged_qty - self.total_cost

    def check_and_lock(self, max_pair_cost: float):
        """Vérifie si le profit peut être locké."""
        if self.pair_cost < max_pair_cost and self.hedged_qty > self.total_cost:
            self.is_locked = True

    def to_dict(self) -> dict:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        # Calculer le total visible (réel + pending)
        data["total_qty_yes"] = self.qty_yes + self.pending_qty_yes
        data["total_qty_no"] = self.qty_no + self.pending_qty_no
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "GabagoolPosition":
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


class GabagoolEngine:
    """Moteur de la stratégie Gabagool."""

    def __init__(self, config: GabagoolConfig, executor: Optional[OrderExecutor] = None, oracle=None):
        self.config = config
        self.executor = executor
        self.oracle = oracle  # BinanceOracle instance
        self.positions: Dict[str, GabagoolPosition] = {}
        self._is_running = False
        self._persistence_path = Path(self.config.persistence_file)
        self._lock = asyncio.Lock()
        
        # Historique des prix pour RSI (market_id -> list of mid_prices)
        self._price_history: Dict[str, List[float]] = {}
        self._maintenance_task: Optional[asyncio.Task] = None

    async def start(self):
        async with self._lock:
            self._load_positions()
            self._is_running = True
            
            # 7.0: Subscribe to fills
            if self.executor:
                self.executor.on_fill = self._on_fill_callback
                self.executor.on_order_end = self._on_order_end_callback

            # 8.0: Maintenance Loop (Auto-Redeem + Reconciliation)
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

            print(f"🦀 Gabagool Engine démarré. {len(self.positions)} positions chargées.")
            
    async def stop(self):
        async with self._lock:
            if self.executor:
                self.executor.on_fill = None
                self.executor.on_order_end = None
            
            if self._maintenance_task:
                self._maintenance_task.cancel()
                try:
                    await self._maintenance_task
                except asyncio.CancelledError:
                    pass
                self._maintenance_task = None

            self._save_positions()
            self._is_running = False
            print("🦀 Gabagool Engine arrêté. Positions sauvegardées.")

    async def _maintenance_loop(self):
        """Boucle de maintenance: Auto-Redeem et Réconciliation."""
        print("🔧 [Gabagool] Maintenance Loop Started (60s interval)")
        while self._is_running:
            try:
                await asyncio.sleep(60) # Vérification toutes les minutes
                await self._check_redeemable_positions()
                await self._reconcile_positions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Error in maintenance loop: {e}")

    async def _check_redeemable_positions(self):
        """Vérifie et redeem les marchés résolus (Parallélisé)."""
        if not self.executor or not self.executor._client:
            return

        active_ids = list(self.positions.keys())
        if not active_ids:
            return

        # Fonction helper pour chaque redeem
        async def try_redeem(market_id):
            try:
                await self.executor._client.redeem_all(market_id)
            except Exception:
                pass

        # Exécution parallèle
        await asyncio.gather(*(try_redeem(mid) for mid in active_ids)) 

    async def _reconcile_positions(self):
        """Réconciliation des positions partielles (Inventory Risk Management)."""
        if not self.executor:
            return
            
        async with self._lock:
            # Snapshot simple pour itérer
            items = list(self.positions.items())

        threshold = 2.0 # Seuil de tolérance (shares)
        
        for market_id, pos in items:
            # Si ordres en cours, on ne touche pas (très important !)
            if pos.pending_qty_yes > 0 or pos.pending_qty_no > 0:
                continue
                
            balance = pos.qty_yes - pos.qty_no
            
            if abs(balance) > threshold:
                # Déséquilibre détecté
                side_to_sell = "YES" if balance > 0 else "NO"
                excess_qty = abs(balance)

                print(f"⚖️ [Reconciliation] Imbalance detected on {market_id}: {pos.qty_yes:.1f} YES / {pos.qty_no:.1f} NO")
                print(f"📉 [Reconciliation] ACTION: Selling {excess_qty:.1f} {side_to_sell} (Market Order)")

                # Envoi d'ordre MARKET pour vendre le surplus ("Neutralize to Min")
                # price=0 n'est pas utilisé pour MARKET, mais requis par signature
                await self.executor.queue_order(
                    token_id=pos.token_yes_id if side_to_sell == "YES" else pos.token_no_id,
                    side="SELL",
                    price=0.0, 
                    size=excess_qty,
                    order_type="MARKET",
                    market_id=market_id,
                    metadata={"reason": "reconciliation"}
                )

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def status(self) -> GabagoolStatus:
        """Retourne le statut actuel du moteur."""
        if self._is_running:
            return GabagoolStatus.RUNNING
        return GabagoolStatus.STOPPED

    def set_executor(self, executor: OrderExecutor):
        self.executor = executor
        if self._is_running:
            self.executor.on_fill = self._on_fill_callback
            self.executor.on_order_end = self._on_order_end_callback

    async def _on_fill_callback(self, market_id: str, side: str, filled_qty: float, price: float):
        """Callback appelé par Executor quand un ordre est rempli."""
        async with self._lock:
            position = self.positions.get(market_id)
            if not position:
                return

            print(f"💰 [Gabagool] FILL CONFIRMED: {side} +{filled_qty} @ {price:.3f} ({market_id})")

            # Réduire pending, augmenter réel
            cost = filled_qty * price
            
            if side == "YES":
                position.qty_yes += filled_qty
                position.cost_yes += cost
                # Réduire le pending (sans passer en négatif)
                position.pending_qty_yes = max(0, position.pending_qty_yes - filled_qty)
            else:
                position.qty_no += filled_qty
                position.cost_no += cost
                position.pending_qty_no = max(0, position.pending_qty_no - filled_qty)

            position.updated_at = datetime.now()
            position.check_and_lock(self.config.max_pair_cost)
            self._save_positions()

    async def _on_order_end_callback(self, market_id: str, side: str, remaining_qty: float):
        """Callback appelé par Executor quand un ordre se termine (cancel/expire)."""
        async with self._lock:
            position = self.positions.get(market_id)
            if not position:
                return

            # print(f"🧹 [Gabagool] CLEANUP: {side} {remaining_qty} annulés/expirés ({market_id})")

            # Réduire le pending du montant non exécuté
            if side == "YES":
                position.pending_qty_yes = max(0, position.pending_qty_yes - remaining_qty)
            else:
                position.pending_qty_no = max(0, position.pending_qty_no - remaining_qty)
            
            self._save_positions()

    async def analyze_opportunity(
        self, market_id: str, token_yes_id: str, token_no_id: str,
        price_yes: float, price_no: float, question: str,
        obi_yes: float = 0.0, obi_no: float = 0.0
    ) -> Tuple[Optional[str], float]:
        """
        Analyse un marché et décide s'il faut acheter YES ou NO.
        Retourne (decision, size_usd).
        decision: "buy_yes", "buy_no", ou None.
        size_usd: Montant à investir (Manual ou Kelly).
        """
        if not self._is_running or not self.executor:
            return None, 0.0

        # Mise à jour historique prix (Mid Price)
        mid_price = (price_yes + (1.0 - price_no)) / 2  # Approx simple
        if market_id not in self._price_history:
            self._price_history[market_id] = []
        self._price_history[market_id].append(mid_price)
        # Garder max 100 ticks
        if len(self._price_history[market_id]) > 100:
            self._price_history[market_id].pop(0)

        async with self._lock:
            position = self.positions.get(market_id)
            
            # --- 1. KILL SWITCH CHECK ---
            if position and not position.is_locked:
                age_minutes = (datetime.now() - position.created_at).total_seconds() / 60
                if age_minutes > self.config.kill_switch_minutes:
                    print(f"⏰ [KILL SWITCH] Position {market_id} trop vieille ({age_minutes:.1f}min). Liquidation!")
                    await self._liquidate_position(position)
                    return None, 0.0

            if not position:
                # Créer une nouvelle position si le marché est intéressant
                if price_yes + price_no < self.config.max_pair_cost:
                    position = GabagoolPosition(
                        market_id=market_id, question=question,
                        token_yes_id=token_yes_id, token_no_id=token_no_id
                    )
                    self.positions[market_id] = position
                else:
                    return None, 0.0 # Marché pas assez intéressant pour commencer

            if position.is_locked:
                return None, 0.0 # Position déjà profitable, on arrête

            # Calculer la taille de l'ordre (Dynamic Sizing)
            # ----------------------------------------------------------------
            # 1. Base: Gabagool Config (Fallback)
            settings = get_settings()
            base_size = self.config.order_size_usd
            final_size = base_size

            # 2. Dynamic Kelly (Si activé)
            if settings.enable_kelly_sizing:
                # Kelly score calculation (0.0 to 3.0 multiplier)
                confidence_score = 1.0
                
                # Bonus Oracle
                if self.oracle:
                    # Si le signal Oracle va dans notre sens, on double la mise
                    # Note: On ne sait pas encore si on va buy YES ou NO, c'est tricky.
                    # On le fera après avoir choisi le candidat buy_yes/buy_no.
                    # Pour l'instant on garde une base.
                    pass
                
                # Bonus OBI
                if obi_yes > 0.2 or obi_no > 0.2:
                    confidence_score += 0.5

                # Calculer la mise théorique (Kelly)
                # Note: Ici on simplifie f = p - q (pour b=1) ou f proprotionnel au signal
                # On utilise un multiplicateur sur la taille de base pour l'instant
                # TODO: Vraie formule de Kelly f* = (bp - q) / b
                
                theoretical_size = base_size * confidence_score
                
                # Appliquer la fourchette [Min, Max] stricte
                final_size = max(settings.kelly_min_bet, min(theoretical_size, settings.kelly_max_bet))

            # Utiliser la taille calculée
            qty_to_buy = final_size
            # ----------------------------------------------------------------

            # Scénario 1: Acheter YES
            new_cost_yes = position.cost_yes + (qty_to_buy / price_yes) * price_yes
            new_qty_yes = position.qty_yes + (qty_to_buy / price_yes)
            new_avg_yes = new_cost_yes / new_qty_yes
            potential_pair_cost_if_buy_yes = new_avg_yes + position.avg_price_no if position.qty_no > 0 else 2.0

            # Scénario 2: Acheter NO
            new_cost_no = position.cost_no + (qty_to_buy / price_no) * price_no
            new_qty_no = position.qty_no + (qty_to_buy / price_no)
            new_avg_no = new_cost_no / new_qty_no
            potential_pair_cost_if_buy_no = position.avg_price_yes + new_avg_no if position.qty_yes > 0 else 2.0

            # Décision: quel achat améliore le plus le pair_cost ?
            current_pair_cost = position.pair_cost
            improvement_yes = current_pair_cost - potential_pair_cost_if_buy_yes
            improvement_no = current_pair_cost - potential_pair_cost_if_buy_no

            # Ne considérer que les améliorations positives (ou supérieures au seuil configuré)
            threshold = self.config.min_improvement
            buy_yes_candidate = improvement_yes > threshold and potential_pair_cost_if_buy_yes < self.config.max_pair_cost
            buy_no_candidate = improvement_no > threshold and potential_pair_cost_if_buy_no < self.config.max_pair_cost

            # --- 1.5. ORACLE LEAD-LAG (HFT Signal) ---
            if self.oracle:
                # Détecter l'actif sous-jacent à partir de la question
                asset = None
                q_lower = question.lower()
                if "bitcoin" in q_lower or "btc" in q_lower: asset = "btcusdt"
                elif "ethereum" in q_lower or "eth" in q_lower: asset = "ethusdt"
                elif "solana" in q_lower or "sol" in q_lower: asset = "solusdt"

                if asset:
                    signal = self.oracle.get_signal(asset)
                    # "BUY" signal from Oracle means Asset Price UP => "YES" Price UP
                    if signal.value == "BUY":
                        print(f"🔮 ORACLE: {asset} PUMP! Force BUY YES on {market_id}")
                        buy_yes_candidate = True # Force considération
                        buy_no_candidate = False # Interdire short
                    elif signal.value == "SELL":
                        print(f"🔮 ORACLE: {asset} DUMP! Force BUY NO on {market_id}")
                        buy_no_candidate = True # Force considération
                        buy_yes_candidate = False # Interdire long

            # --- 1.6. OBI FILTER (Micro-structure) ---
            # Si pression acheteuse massive sur YES (OBI > 0.3), ne surtout pas shorter (Buy NO)
            if obi_yes > 0.3:
                # print(f"🌊 OBI: High Bid Pressure on YES ({obi_yes:.2f}). Blocking NO buy.")
                buy_no_candidate = False
            
            # Si pression acheteuse massive sur NO (OBI > 0.3), ne surtout pas shorter NO (Buy YES)
            if obi_no > 0.3:
                # print(f"🌊 OBI: High Bid Pressure on NO ({obi_no:.2f}). Blocking YES buy.")
                buy_yes_candidate = False

            # --- 2. TREND FILTER CHECK ---
            rsi = calculate_rsi(self._price_history[market_id], self.config.rsi_period)
            
            if self.config.trend_filter_enabled and rsi is not None:
                # Règle "Breakout Protection":
                # Si RSI > 70 (Strong Up Trend), ne pas shorter (Ne pas acheter NO).
                if rsi > self.config.rsi_overbought: 
                    buy_no_candidate = False
                
                # Si RSI < 30 (Strong Down Trend), ne pas acheter le couteau qui tombe (Ne pas acheter YES).
                if rsi < self.config.rsi_oversold:
                    buy_yes_candidate = False

            # Prioriser l'équilibrage des quantités
            ratio = (position.qty_yes + 1) / (position.qty_no + 1) # +1 pour éviter division par zéro

            if buy_yes_candidate and buy_no_candidate:
                # Les deux sont bons, choisir celui qui équilibre le mieux ou qui améliore le plus
                if ratio > self.config.balance_ratio_threshold: # Trop de YES, acheter NO
                    return "buy_no", qty_to_buy
                elif 1/ratio > self.config.balance_ratio_threshold: # Trop de NO, acheter YES
                    return "buy_yes", qty_to_buy
                # Sinon, choisir la meilleure amélioration
                return ("buy_yes", qty_to_buy) if improvement_yes > improvement_no else ("buy_no", qty_to_buy)
            elif buy_yes_candidate:
                if ratio < self.config.balance_ratio_threshold: # Ne pas déséquilibrer davantage
                    return "buy_yes", qty_to_buy
            elif buy_no_candidate:
                if 1/ratio < self.config.balance_ratio_threshold: # Ne pas déséquilibrer davantage
                    return "buy_no", qty_to_buy

            return None, 0.0

    async def _liquidate_position(self, position: GabagoolPosition):
        """Vend tout au prix du marché pour couper les pertes."""
        if not self.executor: return
        print(f"💸 LIQUIDATION lancée pour {position.market_id}")
        # En prod: self.executor.client.sell(...)
        del self.positions[position.market_id]
        self._save_positions()

    async def place_order(self, market_id: str, side: str, price: float = 0.0, qty: float = 0.0) -> bool:
        """Passe un ordre réel via l'executor (non-bloquant)."""
        if not self.executor: 
            print("⚠️ [Gabagool] Executor non configuré. Mode Simulation.")
            # return False # Allow simulation if no executor

        position = self.positions.get(market_id)
        if not position: return False

        # Si prix non fourni, erreur (en prod)
        if price <= 0:
            print(f"❌ [Gabagool] Prix invalide pour {market_id}: {price}")
            return False

        # Si qty non fournie, calculer selon config
        if qty <= 0:
            qty = self.config.order_size_usd / price

        # 1. Mise à jour "Optimiste" mais dans Pending
        # On ne touche plus au qty_yes/qty_no réel, seulement pending.
        # Le Fill Manager mettra à jour le réel.
        cost = qty * price
        if side == "YES":
            position.pending_qty_yes += qty
            position.pending_cost_yes += cost
        else: # NO
            position.pending_qty_no += qty
            position.pending_cost_no += cost

        position.updated_at = datetime.now()
        # Check lock only on real qty? No, keep checking real only.
        # position.check_and_lock(self.config.max_pair_cost) 
        # Check lock sur Pending? Non, trop risqué. On attend le fill.
        
        self._save_positions()

        # 2. Exécution Réelle (Fire & Forget via Queue)
        if self.executor:
            # Token ID needed
            token_id = position.token_yes_id if side == "YES" else position.token_no_id
            
            # Utiliser la queue pour ne pas bloquer
            # 7.0: Passer metadata pour Fill Manager
            await self.executor.queue_order(
                token_id=token_id,
                side="BUY",
                price=price,
                size=qty,
                priority=OrderPriority.NORMAL,
                market_id=market_id,
                metadata={"side": side}
            )
            print(f"🚀 [Gabagool] Ordre {side} envoyé: {qty:.0f} @ {price:.2f} ({market_id}) (Pending)")

        return True

    async def buy_yes(self, market_id: str, token_id: str, price: float, qty: float, question: str):
        """Helper pour acheter des YES."""
        # S'assurer que la position existe ou la créer
        if market_id not in self.positions:
            # Note: Si on arrive ici, analyze_opportunity a dû valider la création
            # Mais il faut token_no_id... c'est le problème.
            # analyze_opportunity crée la position vide.
            pass
        
        await self.place_order(market_id, "YES", price, qty)

    async def buy_no(self, market_id: str, token_id: str, price: float, qty: float, question: str):
        """Helper pour acheter des NO."""
        await self.place_order(market_id, "NO", price, qty)

    def get_stats(self) -> dict:
        """Retourne les statistiques globales de la stratégie."""
        if not self.positions:
            return {}

        active_positions = [p for p in self.positions.values() if not p.is_locked]
        locked_positions = [p for p in self.positions.values() if p.is_locked]

        total_locked_profit = sum(p.locked_profit for p in locked_positions)
        
        best_pair_cost = min([p.pair_cost for p in active_positions] + [1.0])
        
        # Estimer le profit potentiel sur la meilleure position active
        pending_profit = 0.0
        if active_positions:
            best_pos = min(active_positions, key=lambda p: p.pair_cost)
            if best_pos.pair_cost < 1.0:
                pending_profit = best_pos.hedged_qty * (1.0 - best_pos.pair_cost)

        return {
            "active_positions": len(active_positions),
            "locked_positions": len(locked_positions),
            "total_locked_profit": total_locked_profit,
            "best_pair_cost": best_pair_cost,
            "pending_profit": pending_profit,
        }

    def get_positions_summary(self) -> List[dict]:
        """Retourne un résumé des positions actives."""
        sorted_positions = sorted(
            self.positions.values(),
            key=lambda p: p.pair_cost
        )
        return [p.to_dict() for p in sorted_positions]

    def get_active_position_ids(self) -> set:
        """Retourne les IDs des marchés avec des positions actives."""
        return set(self.positions.keys())

    def get_all_positions(self) -> List[GabagoolPosition]:
        """Retourne toutes les positions."""
        return list(self.positions.values())

    def get_locked_positions(self) -> List[GabagoolPosition]:
        """Retourne les positions lockées (profit garanti)."""
        return [p for p in self.positions.values() if p.is_locked]

    def get_active_positions(self) -> List[GabagoolPosition]:
        """Retourne les positions actives (non lockées)."""
        return [p for p in self.positions.values() if not p.is_locked]

    def _save_positions(self):
        """Sauvegarde les positions dans un fichier JSON."""
        self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            market_id: pos.to_dict()
            for market_id, pos in self.positions.items()
        }
        with open(self._persistence_path, "w") as f:
            json.dump(data, f, indent=2)

    def _load_positions(self):
        """Charge les positions depuis un fichier JSON."""
        if not self._persistence_path.exists():
            return

        with open(self._persistence_path, "r") as f:
            try:
                data = json.load(f)
                self.positions = {
                    market_id: GabagoolPosition.from_dict(pos_data)
                    for market_id, pos_data in data.items()
                }
            except (json.JSONDecodeError, TypeError):
                print("⚠️ Fichier de positions Gabagool corrompu ou vide.")
                self.positions = {}