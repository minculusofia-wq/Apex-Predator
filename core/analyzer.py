"""
Opportunity Analyzer - Analyse et scoring des opportunités de trading

Fonctionnalités:
1. Analyse les spreads des marchés
2. Score les opportunités selon plusieurs critères
3. Filtre selon les paramètres utilisateur
4. Recommande les trades à exécuter
"""

import asyncio
import os
import time
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

from core.scanner import MarketData
from config import get_trading_params, TradingParams


# 5.11: Cache global pour volatility map
_volatility_cache: Optional[Tuple[float, Dict]] = None  # (timestamp, data)
VOLATILITY_CACHE_TTL = 60.0  # 60 secondes


class OpportunityScore(Enum):
    """Niveaux de score d'opportunité."""
    EXCELLENT = 5  # ⭐⭐⭐⭐⭐
    VERY_GOOD = 4  # ⭐⭐⭐⭐
    GOOD = 3       # ⭐⭐⭐
    AVERAGE = 2    # ⭐⭐
    POOR = 1       # ⭐


class OpportunityAction(Enum):
    """Actions recommandées."""
    TRADE = "trade"      # Trader immédiatement
    WATCH = "watch"      # Surveiller
    SKIP = "skip"        # Ignorer


@dataclass(slots=True)
class Opportunity:
    """
    Représente une opportunité de trading détectée (slots=True pour performance HFT).

    Contient toutes les informations nécessaires pour décider
    si on doit trader et à quel prix.
    """
    
    # Identification
    id: str
    market_id: str
    question: str
    
    # Tokens
    token_yes_id: str
    token_no_id: str
    
    # Prix et spreads
    best_bid_yes: float
    best_ask_yes: float
    best_bid_no: float
    best_ask_no: float
    spread_yes: float
    spread_no: float
    
    # Prix recommandés pour placement d'ordres
    recommended_price_yes: float
    recommended_price_no: float
    
    # Métriques
    volume: float
    liquidity: float
    
    # Scoring
    score: int  # 1-5
    
    # OBI (Orderbook Imbalance)
    obi_yes: float = 0.0
    obi_no: float = 0.0

    # Gabagool: Pair Cost (YES ask + NO ask)
    pair_cost: float = 2.0  # Coût pour acheter YES + NO (< 1.0 = profit possible)
    profit_margin: float = 0.0  # 1.0 - pair_cost (marge de profit)

    score_breakdown: dict = field(default_factory=dict)
    
    # Action
    action: OpportunityAction = OpportunityAction.SKIP
    
    # Timing
    detected_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    
    @property
    def effective_spread(self) -> float:
        """Spread moyen."""
        return (self.spread_yes + self.spread_no) / 2
    
    @property
    def potential_profit_per_share(self) -> float:
        """Profit potentiel par share (estimation)."""
        return self.effective_spread * 0.5  # Estimation conservatrice
    
    @property
    def score_stars(self) -> str:
        """Représentation en étoiles du score."""
        return "⭐" * self.score
    
    def to_dict(self) -> dict:
        """Convertit en dictionnaire."""
        return {
            "id": self.id,
            "market_id": self.market_id,
            "question": self.question,
            "spread_yes": self.spread_yes,
            "spread_no": self.spread_no,
            "effective_spread": self.effective_spread,
            "volume": self.volume,
            "score": self.score,
            "action": self.action.value,
            "detected_at": self.detected_at.isoformat(),
            # Gabagool metrics
            "pair_cost": self.pair_cost,
            "profit_margin": self.profit_margin,
            "best_ask_yes": self.best_ask_yes,
            "best_ask_no": self.best_ask_no,
        }


class OpportunityAnalyzer:
    """
    Analyse les marchés et détecte les opportunités de trading.
    
    Usage:
        analyzer = OpportunityAnalyzer()
        opportunities = analyzer.analyze_markets(market_data_list)
    """
    
    def __init__(self, params: Optional[TradingParams] = None):
        self._params = params or get_trading_params()
        self._opportunity_counter = 0
    
    @property
    def params(self) -> TradingParams:
        """Paramètres de trading actuels."""
        return self._params
    
    def update_params(self, params: TradingParams) -> None:
        """Met à jour les paramètres."""
        self._params = params
    
    def analyze_market(self, market_data: MarketData, volatility_map: dict = None) -> Optional[Opportunity]:
        """
        Analyse un marché et retourne une opportunité si valide.

        CRITÈRE PRINCIPAL (Gabagool): pair_cost < max_pair_cost
        pair_cost = best_ask_yes + best_ask_no
        Si pair_cost < 1.00 → profit garanti possible

        Args:
            market_data: Données du marché
            volatility_map: Map optionnelle {asset_symbol: volatility_score}

        Returns:
            Opportunity si les critères sont remplis, None sinon
        """
        # Vérifier que les données sont valides
        if not market_data.is_valid:
            return None

        market = market_data.market

        # ═══════════════════════════════════════════════════════════════
        # CRITÈRE GABAGOOL PRINCIPAL: pair_cost < max_pair_cost
        # ═══════════════════════════════════════════════════════════════
        best_ask_yes = market_data.best_ask_yes or 0
        best_ask_no = market_data.best_ask_no or 0

        # Vérifier que les asks sont disponibles
        if best_ask_yes <= 0 or best_ask_no <= 0:
            return None

        # Calculer le pair_cost (coût pour acheter YES + NO)
        pair_cost = best_ask_yes + best_ask_no
        profit_margin = 1.0 - pair_cost

        # FILTRE PRINCIPAL: Si pair_cost >= max_pair_cost → pas de profit possible
        if pair_cost >= self._params.max_pair_cost:
            return None

        # Vérifier la marge de profit minimum
        if profit_margin < self._params.min_profit_margin:
            return None

        # ═══════════════════════════════════════════════════════════════
        # FILTRES SECONDAIRES (optionnels)
        # ═══════════════════════════════════════════════════════════════
        spread_yes = market_data.spread_yes or 0
        spread_no = market_data.spread_no or 0
        effective_spread = (spread_yes + spread_no) / 2

        # Volume minimum (garde un seuil bas pour liquidité)
        if market.volume < self._params.min_volume_usd:
            return None

        # Vérifier la durée (Short-term focus)
        duration_hours = market.hours_until_end
        if duration_hours > self._params.max_duration_hours:
            return None

        # Exclure les marchés sans fin définie ou trop lointains
        if duration_hours <= 0 and not market.end_date:
             return None

        # Calculer les prix recommandés (off-best)
        recommended_yes = (market_data.best_bid_yes or 0) + self._params.order_offset
        recommended_no = (market_data.best_bid_no or 0) + self._params.order_offset

        # S'assurer que les prix sont valides (entre 0.01 et 0.99)
        recommended_yes = max(0.01, min(0.99, recommended_yes))
        recommended_no = max(0.01, min(0.99, recommended_no))
        
        # Calculer le score
        score, breakdown = self._calculate_score(market_data, effective_spread, volatility_map)
        
        # Déterminer l'action
        if score >= 4:
            action = OpportunityAction.TRADE
        elif score >= 3:
            action = OpportunityAction.WATCH
        else:
            action = OpportunityAction.SKIP
        
        # Créer l'opportunité
        self._opportunity_counter += 1
        
        return Opportunity(
            id=f"opp_{self._opportunity_counter}_{int(datetime.now().timestamp())}",
            market_id=market.id,
            question=market.question,
            token_yes_id=market.token_yes_id,
            token_no_id=market.token_no_id,
            best_bid_yes=market_data.best_bid_yes or 0,
            best_ask_yes=best_ask_yes,
            best_bid_no=market_data.best_bid_no or 0,
            best_ask_no=best_ask_no,
            spread_yes=spread_yes,
            spread_no=spread_no,
            recommended_price_yes=recommended_yes,
            recommended_price_no=recommended_no,
            volume=market.volume,
            liquidity=market.liquidity,
            obi_yes=breakdown.get("obi_yes", 0.0),
            obi_no=breakdown.get("obi_no", 0.0),
            pair_cost=pair_cost,
            profit_margin=profit_margin,
            score=score,
            score_breakdown=breakdown,
            action=action,
            detected_at=datetime.now(),
            expires_at=market.end_date,
        )
    
    def _calculate_score(
        self,
        market_data: MarketData,
        effective_spread: float,
        volatility_map: dict = None
    ) -> tuple[int, dict]:
        """
        Calcule le score d'une opportunité (Optimisé Gabagool).

        Critères prioritaires:
        1. PROFIT MARGIN (pair_cost < 1.0) - CRITIQUE
        2. Durée (court terme = mieux)
        3. Volume et Liquidité
        4. Équilibre des prix

        Returns:
            Tuple (score 1-5, breakdown)
        """
        breakdown = {}
        total_points = 0
        max_points = 0

        # ═══════════════════════════════════════════════════════════════
        # GABAGOOL: Score profit_margin (0-40 points) - LE PLUS IMPORTANT
        # ═══════════════════════════════════════════════════════════════
        best_ask_yes = market_data.best_ask_yes or 0
        best_ask_no = market_data.best_ask_no or 0
        pair_cost = best_ask_yes + best_ask_no
        profit_margin = 1.0 - pair_cost

        max_points += 40
        if profit_margin >= 0.03:  # 3%+ profit margin = excellent
            margin_points = 40
        elif profit_margin >= 0.02:  # 2%+ = très bon
            margin_points = 35
        elif profit_margin >= 0.015:  # 1.5%+ = bon
            margin_points = 30
        elif profit_margin >= 0.01:  # 1%+ = acceptable
            margin_points = 20
        elif profit_margin >= 0.005:  # 0.5%+ = minimal
            margin_points = 10
        else:
            margin_points = 0
        total_points += margin_points
        breakdown["profit_margin"] = margin_points
        breakdown["pair_cost"] = round(pair_cost, 4)
        
        # --- 6.3: OBI (Orderbook Imbalance) Calculation ---
        # OBI = (Bids - Asks) / (Bids + Asks)
        # Range: -1 (Sell Pressure) to +1 (Buy Pressure)
        obi_yes = 0.0
        obi_no = 0.0
        
        def calculate_obi(orderbook):
            if not orderbook: return 0.0
            total_bid_vol = sum(float(b[1]) for b in orderbook.get("bids", [])[:5]) # Top 5 depth
            total_ask_vol = sum(float(a[1]) for a in orderbook.get("asks", [])[:5])
            total = total_bid_vol + total_ask_vol
            if total == 0: return 0.0
            return (total_bid_vol - total_ask_vol) / total

        if market_data.orderbook_yes:
            obi_yes = calculate_obi(market_data.orderbook_yes)
        if market_data.orderbook_no:
            obi_no = calculate_obi(market_data.orderbook_no)
            
        breakdown["obi_yes"] = obi_yes
        breakdown["obi_no"] = obi_no

        # 0. Score volatilité externe (Bonus)
        if volatility_map:
            market_text = market_data.market.question.upper()
            asset_vol = 0
            for asset, vol in volatility_map.items():
                if asset in market_text:
                    asset_vol = vol
                    break
            
            if asset_vol > 0:
                max_points += 20
                if asset_vol >= 5.0: vol_points = 20
                elif asset_vol >= 3.0: vol_points = 15
                elif asset_vol >= 1.5: vol_points = 10
                else: vol_points = 5
                
                total_points += vol_points
                breakdown["binance_vol"] = vol_points

        # 0. Score durée (0-30 points) - CRITIQUE POUR HFT
        # Plus c'est court, mieux c'est pour la volatilité
        max_points += 30
        duration_hours = market_data.market.hours_until_end
        if duration_hours <= 1:
            duration_points = 30  # Max points pour < 1h
        elif duration_hours <= 4:
            duration_points = 25
        elif duration_hours <= 12:
            duration_points = 20
        elif duration_hours <= 24:
            duration_points = 15
        elif duration_hours <= 48:
            duration_points = 10
        else:
            duration_points = 5
        total_points += duration_points
        breakdown["duration"] = duration_points
        
        # 1. Score spread (0-25 points)
        max_points += 25
        if effective_spread >= 0.10:
            spread_points = 25
        elif effective_spread >= 0.08:
            spread_points = 20
        elif effective_spread >= 0.06:
            spread_points = 15
        elif effective_spread >= 0.04:
            spread_points = 10
        else:
            spread_points = 5
        total_points += spread_points
        breakdown["spread"] = spread_points
        
        # 2. Score volume (0-25 points)
        max_points += 25
        volume = market_data.market.volume
        if volume >= 100000:
            volume_points = 25
        elif volume >= 50000:
            volume_points = 20
        elif volume >= 20000:
            volume_points = 15
        elif volume >= 5000:
            volume_points = 10
        else:
            volume_points = 5
        total_points += volume_points
        breakdown["volume"] = volume_points
        
        # 3. Score liquidité globale (0-25 points)
        max_points += 25
        liquidity = market_data.market.liquidity
        if liquidity >= 50000:
            liquidity_points = 25
        elif liquidity >= 20000:
            liquidity_points = 20
        elif liquidity >= 10000:
            liquidity_points = 15
        elif liquidity >= 5000:
            liquidity_points = 10
        else:
            liquidity_points = 5
        total_points += liquidity_points
        breakdown["liquidity"] = liquidity_points
        
        # 4. Score équilibre (0-25 points)
        # Prix proche de 0.50 = marché incertain = plus de volatilité
        max_points += 25
        price_yes = market_data.market.price_yes
        distance_from_50 = abs(price_yes - 0.50)
        if distance_from_50 <= 0.10:
            balance_points = 25
        elif distance_from_50 <= 0.20:
            balance_points = 20
        elif distance_from_50 <= 0.30:
            balance_points = 15
        elif distance_from_50 <= 0.40:
            balance_points = 10
        else:
            balance_points = 5
        total_points += balance_points
        breakdown["balance"] = balance_points
        
        # --- 6.2: Depth Analysis (Malus) ---
        # Vérifier si le Top of Book a une taille raisonnable (ex > $10)
        # Si orderbooks disponibles
        depth_penalty = 0
        min_depth_size = 10.0 # $10 minimum visible liquidity
        
        # Check YES Ask depth
        if market_data.orderbook_yes and market_data.orderbook_yes.get('asks'):
             top_ask = market_data.orderbook_yes['asks'][0] # (price, size)
             # API public returns [{"price": "0.50", "size": "100"}] or list of lists depending on client
             # Assuming standard format handling in previous steps, but need to be careful
             try:
                 size = float(top_ask[1]) if isinstance(top_ask, (list, tuple)) else float(top_ask.get("size", 0))
                 price = float(top_ask[0]) if isinstance(top_ask, (list, tuple)) else float(top_ask.get("price", 0))
                 if size * price < min_depth_size:
                     depth_penalty += 10
             except: pass

        # Check NO Ask depth
        if market_data.orderbook_no and market_data.orderbook_no.get('asks'):
             top_ask = market_data.orderbook_no['asks'][0]
             try:
                 size = float(top_ask[1]) if isinstance(top_ask, (list, tuple)) else float(top_ask.get("size", 0))
                 price = float(top_ask[0]) if isinstance(top_ask, (list, tuple)) else float(top_ask.get("price", 0))
                 if size * price < min_depth_size:
                     depth_penalty += 10
             except: pass
        
        if depth_penalty > 0:
            total_points = max(0, total_points - depth_penalty)
            breakdown["depth_penalty"] = -depth_penalty

        # Calculer le score final (1-5)
        percentage = (total_points / max_points) * 100
        if percentage >= 80:
            final_score = 5
        elif percentage >= 60:
            final_score = 4
        elif percentage >= 40:
            final_score = 3
        elif percentage >= 20:
            final_score = 2
        else:
            final_score = 1
        
        breakdown["total_points"] = total_points
        breakdown["max_points"] = max_points
        breakdown["percentage"] = percentage
        
        return final_score, breakdown
    
    def analyze_all_markets(
        self,
        markets: dict[str, MarketData],
        volatility_map: dict = None
    ) -> list[Opportunity]:
        """
        Analyse tous les marchés et retourne les opportunités Gabagool.

        Args:
            markets: Dictionnaire de MarketData

        Returns:
            Liste d'opportunités triées par profit_margin (desc)
        """
        opportunities = []
        filtered_reasons = {
            "invalid": 0,
            "pair_cost_high": 0,  # pair_cost >= max_pair_cost (pas de profit)
            "volume": 0,
            "duration": 0,
            "passed": 0
        }

        for market_data in markets.values():
            opportunity = self.analyze_market(market_data, volatility_map)
            if opportunity:
                opportunities.append(opportunity)
                filtered_reasons["passed"] += 1
            else:
                # Track why filtered (Gabagool-focused)
                if not market_data.is_valid:
                    filtered_reasons["invalid"] += 1
                elif market_data.market.volume < self._params.min_volume_usd:
                    filtered_reasons["volume"] += 1
                else:
                    # Calculer pair_cost pour diagnostic
                    ask_yes = market_data.best_ask_yes or 0
                    ask_no = market_data.best_ask_no or 0
                    if ask_yes > 0 and ask_no > 0:
                        pair_cost = ask_yes + ask_no
                        if pair_cost >= self._params.max_pair_cost:
                            filtered_reasons["pair_cost_high"] += 1
                        else:
                            filtered_reasons["duration"] += 1
                    else:
                        filtered_reasons["invalid"] += 1

        # Log filtering stats
        total = len(markets)
        if total > 0:
            if filtered_reasons["passed"] == 0:
                print(f"⚠️ [Analyzer] 0/{total} opportunités Gabagool - pair_cost>={self._params.max_pair_cost}={filtered_reasons['pair_cost_high']}, vol={filtered_reasons['volume']}, invalid={filtered_reasons['invalid']}")
            elif filtered_reasons["passed"] > 0:
                print(f"💰 [Analyzer] {filtered_reasons['passed']}/{total} marchés tradables (pair_cost < {self._params.max_pair_cost})")

        # Trier par profit_margin décroissant (meilleur profit d'abord)
        opportunities.sort(key=lambda x: (x.score, x.profit_margin), reverse=True)

        return opportunities

    async def analyze_all_markets_parallel(
        self,
        markets: dict[str, MarketData],
        volatility_map: dict = None,
        max_workers: int = None
    ) -> list[Opportunity]:
        """
        5.9: Analyse parallèle de tous les marchés (CPU-bound).

        Utilise un ThreadPoolExecutor pour paralléliser l'analyse
        sur plusieurs cœurs CPU.

        Args:
            markets: Dictionnaire de MarketData
            volatility_map: Map optionnelle de volatilité
            max_workers: Nombre de workers parallèles (défaut: auto basé sur CPU)

        Returns:
            Liste d'opportunités triées par score (desc)
        """
        if not markets:
            return []

        loop = asyncio.get_event_loop()
        market_list = list(markets.values())

        # HFT: Calculer le nombre optimal de workers
        if max_workers is None:
            max_workers = min(os.cpu_count() or 4, len(market_list), 8)

        # Utiliser ThreadPoolExecutor pour paralléliser le travail CPU
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Créer les tâches
            tasks = [
                loop.run_in_executor(
                    executor,
                    self.analyze_market,
                    market_data,
                    volatility_map
                )
                for market_data in market_list
            ]

            # Exécuter en parallèle
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filtrer les résultats valides
        opportunities = [
            r for r in results
            if r is not None and not isinstance(r, Exception)
        ]

        # Trier par score décroissant
        opportunities.sort(key=lambda x: (x.score, x.effective_spread), reverse=True)

        return opportunities
    
    def get_tradeable_opportunities(
        self,
        markets: dict[str, MarketData]
    ) -> list[Opportunity]:
        """
        Retourne uniquement les opportunités à trader.
        
        Args:
            markets: Dictionnaire de MarketData
            
        Returns:
            Liste d'opportunités avec action=TRADE
        """
        all_opportunities = self.analyze_all_markets(markets)
        return [op for op in all_opportunities if op.action == OpportunityAction.TRADE]
    
    def should_trade(self, opportunity: Opportunity) -> bool:
        """
        Détermine si on doit trader cette opportunité.

        Vérifie les paramètres de trading et les sécurités.
        """
        if not self._params.auto_trading_enabled:
            return False

        if opportunity.action != OpportunityAction.TRADE:
            return False

        if opportunity.score < 4:
            return False

        return True

    def analyze_immediate(
        self,
        market_data: MarketData,
        volatility_map: dict = None
    ) -> Optional[Opportunity]:
        """
        HFT: Analyse immédiate pour event-driven trigger.

        Cette méthode est optimisée pour être appelée depuis le callback
        on_immediate_analysis du scanner. Elle analyse le marché et
        retourne l'opportunité UNIQUEMENT si elle est tradeable.

        Usage:
            scanner.on_immediate_analysis = analyzer.analyze_immediate
            # ou
            scanner.on_immediate_analysis = lambda md: analyzer.analyze_immediate(md, vol_map)

        Args:
            market_data: Données du marché (depuis WebSocket update)
            volatility_map: Map optionnelle de volatilité externe

        Returns:
            Opportunity si tradeable, None sinon
        """
        # Analyse rapide
        opportunity = self.analyze_market(market_data, volatility_map)

        if opportunity is None:
            return None

        # Vérifier si on doit trader (score >= 4, action = TRADE)
        if self.should_trade(opportunity):
            return opportunity

        return None


# ═══════════════════════════════════════════════════════════════
# 5.11: FONCTIONS DE CACHE VOLATILITY
# ═══════════════════════════════════════════════════════════════

def get_cached_volatility() -> Optional[Dict]:
    """
    5.11: Récupère la volatility map du cache si non expirée.

    Returns:
        Dict de volatilité ou None si cache expiré/vide
    """
    global _volatility_cache

    if _volatility_cache is None:
        return None

    timestamp, data = _volatility_cache
    if time.time() - timestamp < VOLATILITY_CACHE_TTL:
        return data

    return None


def set_cached_volatility(data: Dict) -> None:
    """
    5.11: Met à jour le cache de volatilité.

    Args:
        data: Dict {asset_symbol: volatility_score}
    """
    global _volatility_cache
    _volatility_cache = (time.time(), data)


def clear_volatility_cache() -> None:
    """5.11: Efface le cache de volatilité."""
    global _volatility_cache
    _volatility_cache = None


async def get_volatility_map_cached(fetch_func) -> Dict:
    """
    5.11: Récupère la volatility map avec cache.

    Élimine 95% des appels API en utilisant le cache.

    Args:
        fetch_func: Fonction async pour récupérer les données fraîches

    Returns:
        Dict de volatilité
    """
    # Vérifier le cache d'abord
    cached = get_cached_volatility()
    if cached is not None:
        return cached

    # Sinon, fetch et cacher
    try:
        data = await fetch_func()
        if data:
            set_cached_volatility(data)
            return data
    except Exception:
        pass

    return {}
