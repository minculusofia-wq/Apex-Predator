"""
Web Server - API FastAPI pour le bot HFT

Endpoints:
- GET /: Dashboard principal
- GET /api/status: Statut du bot
- GET /api/opportunities: Liste des opportunités
- GET /api/stats: Statistiques
- POST /api/config: Mettre à jour la config
- POST /api/start: Démarrer le scanner
- POST /api/stop: Arrêter le scanner
- WebSocket /ws: Updates temps réel

Optimisations HFT v2.0:
- uvloop pour event loop rapide
- orjson pour JSON rapide
- Connection pooling HTTP/2
"""

import asyncio
from datetime import datetime
from typing import Optional
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_settings, get_trading_params, update_trading_params, TradingParams
from core import MarketScanner, OpportunityAnalyzer, OrderManager, OrderExecutor, TradeManager, TradeSide, MarketMaker, MMConfig, GabagoolEngine, GabagoolConfig
from core.scanner import ScannerState
from core.lifecycle import get_health_checker, get_metrics_manager, get_graceful_shutdown, ComponentHealth
from core.resilience import get_circuit_stats
from core.auto_optimizer import AutoOptimizer, OptimizerMode
from core.performance import (
    setup_uvloop,
    json_dumps,
    get_performance_status,
    print_performance_status,
)
from api.public import CoinGeckoClient, BinanceClient
from api.private import PolymarketPrivateClient, PolymarketCredentials


# État global
scanner: Optional[MarketScanner] = None
analyzer: Optional[OpportunityAnalyzer] = None
order_manager: Optional[OrderManager] = None
executor: Optional[OrderExecutor] = None
trade_manager: Optional[TradeManager] = None
market_maker: Optional[MarketMaker] = None
gabagool_engine: Optional[GabagoolEngine] = None
auto_optimizer: Optional[AutoOptimizer] = None
cg_client: Optional[CoinGeckoClient] = None
binance_client: Optional[BinanceClient] = None
private_client: Optional[PolymarketPrivateClient] = None
is_running = False
start_time = datetime.now()
connected_websockets: list[WebSocket] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 HFT Scalper Bot v2.0 - Serveur Web démarré")
    print("📊 Dashboard: http://localhost:8000")
    print_performance_status()  # Affiche le statut des optimisations

    # Afficher le statut auto-trading au démarrage
    params = get_trading_params()
    auto_status = "✅ ACTIVÉ" if params.auto_trading_enabled else "⏸️ DÉSACTIVÉ"
    print(f"🤖 [Auto Trading] {auto_status}")
    yield
    # Shutdown
    print("\n🛑 Arrêt du bot en cours...")
    global scanner, binance_client, cg_client, market_maker, gabagool_engine, auto_optimizer, is_running

    is_running = False

    if auto_optimizer:
        await auto_optimizer.stop()
        print("✓ Auto-Optimizer arrêté")

    if gabagool_engine:
        await gabagool_engine.stop()
        print("✓ Gabagool Engine arrêté")

    if market_maker:
        await market_maker.stop()
        print("✓ Market Maker arrêté")

    if scanner:
        await scanner.stop()
        print("✓ Scanner arrêté")

    if executor:
        await executor.stop()
        print("✓ Executor arrêté")

    if binance_client:
        await binance_client.__aexit__(None, None, None)
        print("✓ Binance client fermé")

    if cg_client:
        await cg_client.__aexit__(None, None, None)
        print("✓ CoinGecko client fermé")

    print("✅ Arrêt complet terminé.")


app = FastAPI(title="HFT Scalper Bot", version="1.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConfigUpdate(BaseModel):
    min_spread: float = 0.06
    min_volume: float = 20000.0
    max_duration_hours: int = 24
    capital_per_trade: float = 50.0
    max_open_positions: int = 5
    enable_kelly_sizing: bool = False
    kelly_min_bet: float = 5.0
    kelly_max_bet: float = 50.0
    auto_trading_enabled: bool = True  # Trading automatique


class TradeRequest(BaseModel):
    market_id: str
    market_question: str
    side: str  # "yes" or "no"
    entry_price: float
    size: float


class ExitTradeRequest(BaseModel):
    exit_price: float


class MMConfigUpdate(BaseModel):
    target_spread: float = 0.04
    order_size: float = 50.0
    max_position: float = 500.0
    price_offset: float = 0.01
    refresh_interval: float = 2.0


# ═══════════════════════════════════════════════════════════════
# PAGES HTML
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Page principale du dashboard."""
    html_path = Path(__file__).parent / "templates" / "index.html"
    return html_path.read_text()


# ═══════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/status")
async def get_status():
    """Statut du bot."""
    settings = get_settings()
    
    # Calculate uptime based on the global start_time
    elapsed = (datetime.now() - start_time).total_seconds()
    hours, remainder = divmod(int(elapsed), 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return {
        "is_running": is_running, # Use global is_running for consistency with existing code
        "scanner_state": scanner.state.value if scanner else "stopped",
        "markets_count": scanner.market_count if scanner else 0,
        "uptime": uptime_str,
        "wallet_connected": False, # Keep as False as wallet is not implemented in this file
        "config": {
            "keywords": settings.target_keywords,
            "types": settings.market_types
        }
    }


@app.get("/api/opportunities")
async def get_opportunities():
    """Retourne les opportunités détectées."""
    global scanner, analyzer
    
    if not scanner or not analyzer:
        return {"opportunities": []}

    # Thread-safe: utiliser snapshot au lieu d'accès direct
    markets = await scanner.get_markets_snapshot()
    opportunities = analyzer.analyze_all_markets(markets)
    
    return {
        "opportunities": [
            {
                "id": opp.id,
                "market": opp.question,
                "spread": round(opp.effective_spread, 4),
                "volume": opp.volume,
                "price_yes": round(opp.best_ask_yes, 2),
                "price_no": round(opp.best_ask_no, 2),
                "score": opp.score,
                "action": opp.action.value,
            }
            for opp in opportunities[:20]
        ]
    }


@app.get("/api/stats")
async def get_stats():
    """Retourne les statistiques."""
    global order_manager
    
    params = get_trading_params()
    
    if order_manager:
        stats = order_manager.stats
        return {
            "trades_today": stats["total_trades"],
            "win_rate": round(stats["win_rate"], 1),
            "pnl_today": round(order_manager.get_daily_pnl(), 2),
            "open_positions": stats["open_positions"],
            "max_positions": params.max_open_positions,
        }
    
    return {
        "trades_today": 0,
        "win_rate": 0,
        "pnl_today": 0,
        "open_positions": 0,
        "max_positions": params.max_open_positions,
    }


@app.get("/api/config")
async def get_config():
    """Retourne la configuration actuelle."""
    params = get_trading_params()
    settings = get_settings()
    return {
        "min_spread": params.min_spread,
        "max_spread": params.max_spread,
        "min_volume": params.min_volume_usd,
        "max_duration_hours": params.max_duration_hours,
        "capital_per_trade": params.capital_per_trade,
        "max_open_positions": params.max_open_positions,
        "max_total_exposure": params.max_total_exposure,
        "enable_kelly_sizing": settings.enable_kelly_sizing,
        "kelly_min_bet": settings.kelly_min_bet,
        "kelly_max_bet": settings.kelly_max_bet,
        "assets": settings.target_keywords,
        "auto_trading_enabled": params.auto_trading_enabled,
    }


@app.post("/api/config")
async def update_config(config: ConfigUpdate):
    """Met à jour la configuration."""
    global analyzer
    
    params = get_trading_params()
    params.min_spread = max(0, min(1.0, config.min_spread))
    params.min_volume_usd = max(0, config.min_volume)
    params.max_duration_hours = max(1, min(720, config.max_duration_hours))
    params.capital_per_trade = max(0, min(10000, config.capital_per_trade))
    params.max_open_positions = max(0, min(50, config.max_open_positions))
    old_auto_trading = params.auto_trading_enabled
    params.auto_trading_enabled = config.auto_trading_enabled
    if old_auto_trading != config.auto_trading_enabled:
        status = "✅ ACTIVÉ" if config.auto_trading_enabled else "⏸️ DÉSACTIVÉ"
        print(f"🤖 [Auto Trading] {status}")

    # Update Kelly Settings
    settings = get_settings()
    settings.enable_kelly_sizing = config.enable_kelly_sizing
    settings.kelly_min_bet = max(1.0, config.kelly_min_bet)
    settings.kelly_max_bet = max(settings.kelly_min_bet, min(1000.0, config.kelly_max_bet))
    
    update_trading_params(params)
    
    if analyzer:
        analyzer.update_params(params)

    # SYNC: Update Gabagool Engine with new capital setting
    global gabagool_engine
    if gabagool_engine:
        gabagool_engine.config.order_size_usd = params.capital_per_trade
        print(f"💰 Gabagool capital updated to ${params.capital_per_trade}")
    
    await broadcast({"type": "config_updated", "config": config.dict()})
    
    return {"success": True, "message": "Configuration mise à jour"}


@app.post("/api/start")
async def start_scanner():
    """Démarre le scanner."""
    global scanner, analyzer, order_manager, trade_manager, binance_client, cg_client, private_client, executor, is_running

    if is_running:
        return {"success": False, "message": "Déjà en cours d'exécution"}

    try:
        settings = get_settings()

        # Initialize Private Client & Executor if keys exist
        if settings.has_private_key and settings.has_api_credentials:
            try:
                # 1. Credentials Object
                credentials = PolymarketCredentials(
                    private_key=settings.polymarket_private_key,
                    api_key=settings.polymarket_api_key,
                    api_secret=settings.polymarket_api_secret,
                    passphrase=settings.polymarket_passphrase
                )
                
                # 2. Legacy Private Client (for MarketMaker / TradeManager)
                if not private_client:
                    private_client = PolymarketPrivateClient(credentials)
                    print("🔐 Private Client loaded successfully")

                # 3. Order Executor (for Gabagool / Strategies)
                if not executor:
                    executor = OrderExecutor(credentials=credentials, order_manager=order_manager)
                    if await executor.start():
                        print("🚀 Order Executor started")
                    else:
                        print("⚠️ Order Executor failed to start")

            except Exception as e:
                print(f"⚠️ Failed to load Private Client/Executor: {e}")

        if not order_manager:
            order_manager = OrderManager()
        if not analyzer:
            analyzer = OpportunityAnalyzer()
        if not scanner:
            scanner = MarketScanner()
        if not trade_manager:
            # Pass private client to TradeManager
            trade_manager = TradeManager(private_client=private_client)
        if not binance_client:
            binance_client = BinanceClient()
            await binance_client.__aenter__()
        if not cg_client:
            cg_client = CoinGeckoClient()
            await cg_client.__aenter__()
        
        await scanner.start()
        is_running = True
        
        # Démarrer la tâche de broadcast
        asyncio.create_task(broadcast_loop())
        
        await broadcast({"type": "started", "markets": scanner.market_count})
        
        return {
            "success": True,
            "message": f"Scanner démarré - {scanner.market_count} marchés"
        }
        
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/stop")
async def stop_scanner():
    """Arrête le scanner."""
    global scanner, is_running
    
    if scanner:
        await scanner.stop()
    
    is_running = False
    await broadcast({"type": "stopped"})
    
    return {"success": True, "message": "Scanner arrêté"}


@app.post("/api/trades/enter")
async def enter_trade(trade: TradeRequest):
    """Entre manuellement un trade."""
    global trade_manager

    if not trade_manager:
        return {"success": False, "message": "Trade manager non initialisé"}

    try:
        new_trade = await trade_manager.open_trade(
            market_id=trade.market_id,
            market_question=trade.market_question,
            side=TradeSide(trade.side),
            entry_price=trade.entry_price,
            size=trade.size
        )
        await broadcast_trades()
        return {"success": True, "trade": new_trade.to_dict()}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/trades/{trade_id}/exit")
async def exit_trade(trade_id: str, request: ExitTradeRequest):
    """Sort manuellement d'un trade."""
    global trade_manager

    if not trade_manager:
        return {"success": False, "message": "Trade manager non initialisé"}

    closed_trade = trade_manager.close_trade(trade_id, request.exit_price)
    if closed_trade:
        await broadcast_trades()
        return {"success": True, "trade": closed_trade.to_dict()}

    return {"success": False, "message": "Trade non trouvé"}


@app.get("/api/trades")
async def get_trades():
    """Liste tous les trades."""
    global trade_manager
    if not trade_manager:
        return {"active": [], "closed": [], "stats": {}}
        
    return {
        "active": [t.to_dict() for t in trade_manager.active_trades],
        "closed": [t.to_dict() for t in trade_manager.closed_trades],
        "stats": trade_manager.get_stats()
    }


@app.get("/api/binance")
async def get_binance_data():
    """Récupère les données de volatilité (via CoinGecko maintenant)."""
    global cg_client
    if not cg_client:
        return []
    return await cg_client.get_volatility_ranking()


# ═══════════════════════════════════════════════════════════════
# MARKET MAKER ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.post("/api/market-maker/start")
async def start_market_maker():
    """Démarre le Market Maker."""
    global market_maker, scanner, private_client

    if not scanner or not is_running:
        return {"success": False, "message": "Scanner non démarré. Lancez le scanner d'abord."}

    if market_maker and market_maker.is_running:
        return {"success": False, "message": "Market Maker déjà en cours"}

    try:
        if not market_maker:
            market_maker = MarketMaker(private_client=private_client)

        markets_snapshot = await scanner.get_markets_snapshot()
        await market_maker.start(markets_snapshot)
        return {"success": True, "message": "Market Maker démarré", "markets": len(markets_snapshot)}

    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/market-maker/stop")
async def stop_market_maker():
    """Arrête le Market Maker."""
    global market_maker

    if not market_maker:
        return {"success": False, "message": "Market Maker non initialisé"}

    await market_maker.stop()
    return {"success": True, "message": "Market Maker arrêté"}


@app.get("/api/market-maker/status")
async def get_market_maker_status():
    """Retourne le statut et les stats du Market Maker."""
    global market_maker

    if not market_maker:
        return {
            "status": "stopped",
            "is_running": False,
            "stats": {},
            "positions": [],
            "config": MMConfig().__dict__
        }

    return {
        "status": market_maker.status.value,
        "is_running": market_maker.is_running,
        "stats": market_maker.stats,
        "positions": [
            {
                "market_id": p.market_id,
                "yes_shares": p.yes_shares,
                "no_shares": p.no_shares,
                "net_position": p.net_position,
                "realized_pnl": p.realized_pnl,
            }
            for p in market_maker.get_all_positions()
        ],
        "config": {
            "target_spread": market_maker.config.target_spread,
            "order_size": market_maker.config.order_size,
            "max_position": market_maker.config.max_position,
            "price_offset": market_maker.config.price_offset,
            "refresh_interval": market_maker.config.refresh_interval,
        }
    }


@app.post("/api/market-maker/config")
async def update_market_maker_config(config: MMConfigUpdate):
    """Met à jour la configuration du Market Maker."""
    global market_maker

    if not market_maker:
        market_maker = MarketMaker(private_client=private_client)

    market_maker.config.target_spread = config.target_spread
    market_maker.config.order_size = config.order_size
    market_maker.config.max_position = config.max_position
    market_maker.config.price_offset = config.price_offset
    market_maker.config.refresh_interval = config.refresh_interval

    return {"success": True, "message": "Configuration MM mise à jour", "config": config.dict()}


@app.post("/api/market-maker/pause")
async def pause_market_maker():
    """Met en pause le Market Maker (garde les ordres)."""
    global market_maker

    if not market_maker:
        return {"success": False, "message": "Market Maker non initialisé"}

    await market_maker.pause()
    return {"success": True, "message": "Market Maker en pause"}


@app.post("/api/market-maker/resume")
async def resume_market_maker():
    """Reprend le Market Maker après une pause."""
    global market_maker

    if not market_maker:
        return {"success": False, "message": "Market Maker non initialisé"}

    await market_maker.resume()
    return {"success": True, "message": "Market Maker repris"}


# ═══════════════════════════════════════════════════════════════
# GABAGOOL STRATEGY ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.post("/api/gabagool/start")
async def start_gabagool():
    """Démarre la stratégie Gabagool."""
    global gabagool_engine, scanner, private_client, executor

    if not scanner or not is_running:
        return {"success": False, "message": "Scanner non démarré. Lancez le scanner d'abord."}

    if gabagool_engine and gabagool_engine.is_running:
        return {"success": False, "message": "Gabagool déjà en cours"}

    try:
        if not gabagool_engine:
            gabagool_engine = GabagoolEngine(config=GabagoolConfig(), executor=executor)

        await gabagool_engine.start()

        # ═══════════════════════════════════════════════════════════════
        # EVENT-DRIVEN: Connecter le callback pour réaction instantanée
        # ═══════════════════════════════════════════════════════════════
        async def on_immediate_opportunity(market_data):
            """Callback instantané quand le scanner détecte un changement de prix."""
            if not gabagool_engine or not gabagool_engine.is_running:
                return

            # Vérifier que les prix sont valides
            price_yes = market_data.best_ask_yes or 0
            price_no = market_data.best_ask_no or 0
            if price_yes <= 0 or price_no <= 0:
                return

            # Quick filter: pair_cost doit être < 1.0 pour être intéressant
            pair_cost = price_yes + price_no
            if pair_cost >= 0.995:
                return

            try:
                # Analyser immédiatement sans attendre broadcast_loop
                action, size_usd = await gabagool_engine.analyze_opportunity(
                    market_id=market_data.market.id,
                    token_yes_id=market_data.market.token_yes_id,
                    token_no_id=market_data.market.token_no_id,
                    price_yes=price_yes,
                    price_no=price_no,
                    question=market_data.market.question,
                    obi_yes=0.0,
                    obi_no=0.0
                )

                if action == "buy_yes" and price_yes > 0:
                    print(f"🔥 [Event-Driven] BUY YES {market_data.market.question[:40]}... @ {price_yes:.3f}")
                    await gabagool_engine.buy_yes(
                        market_id=market_data.market.id,
                        token_id=market_data.market.token_yes_id,
                        price=price_yes,
                        qty=size_usd / price_yes,
                        question=market_data.market.question
                    )
                elif action == "buy_no" and price_no > 0:
                    print(f"🔥 [Event-Driven] BUY NO {market_data.market.question[:40]}... @ {price_no:.3f}")
                    await gabagool_engine.buy_no(
                        market_id=market_data.market.id,
                        token_id=market_data.market.token_no_id,
                        price=price_no,
                        qty=size_usd / price_no,
                        question=market_data.market.question
                    )
            except Exception as e:
                print(f"⚠️ [Event-Driven] Erreur: {e}")

        # Connecter le callback au scanner
        scanner.on_immediate_analysis = on_immediate_opportunity
        print("🔗 [Gabagool] Event-driven callback connecté au scanner")

        return {"success": True, "message": "Gabagool démarré (Event-Driven activé)"}

    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/gabagool/stop")
async def stop_gabagool():
    """Arrête la stratégie Gabagool."""
    global gabagool_engine

    if not gabagool_engine:
        return {"success": False, "message": "Gabagool non initialisé"}

    await gabagool_engine.stop()
    return {"success": True, "message": "Gabagool arrêté"}


@app.get("/api/gabagool/status")
async def get_gabagool_status():
    """Retourne le statut et les stats de Gabagool."""
    global gabagool_engine

    if not gabagool_engine:
        return {
            "status": "stopped",
            "is_running": False,
            "stats": {},
            "positions": []
        }

    return {
        "status": gabagool_engine.status.value,
        "is_running": gabagool_engine.is_running,
        "stats": gabagool_engine.get_stats(),
        "positions": [p.to_dict() for p in gabagool_engine.get_all_positions()]
    }


@app.get("/api/gabagool/positions")
async def get_gabagool_positions():
    """Retourne toutes les positions Gabagool."""
    global gabagool_engine

    if not gabagool_engine:
        return {"positions": [], "locked": [], "active": []}

    return {
        "positions": [p.to_dict() for p in gabagool_engine.get_all_positions()],
        "locked": [p.to_dict() for p in gabagool_engine.get_locked_positions()],
        "active": [p.to_dict() for p in gabagool_engine.get_active_positions()]
    }


@app.post("/api/gabagool/config")
async def update_gabagool_config(
    max_pair_cost: float = 0.98,
    order_size_usd: float = 25.0,
    max_position_usd: float = 500.0
):
    """Met à jour la configuration Gabagool."""
    global gabagool_engine, private_client, executor

    if not gabagool_engine:
        gabagool_engine = GabagoolEngine(config=GabagoolConfig(), executor=executor)

    gabagool_engine.config.max_pair_cost = max_pair_cost
    gabagool_engine.config.order_size_usd = order_size_usd
    gabagool_engine.config.max_position_usd = max_position_usd

    return {
        "success": True,
        "message": "Configuration Gabagool mise à jour",
        "config": {
            "max_pair_cost": max_pair_cost,
            "order_size_usd": order_size_usd,
            "max_position_usd": max_position_usd
        }
    }


# ═══════════════════════════════════════════════════════════════
# AUTO-OPTIMIZER ENDPOINTS
# ═══════════════════════════════════════════════════════════════

class OptimizerModeUpdate(BaseModel):
    mode: str  # "manual", "semi_auto", "full_auto"


@app.post("/api/optimizer/start")
async def start_optimizer():
    """Démarre l'Auto-Optimizer."""
    global auto_optimizer, scanner, gabagool_engine, executor

    if not scanner or not is_running:
        return {"success": False, "message": "Scanner non démarré. Lancez le scanner d'abord."}

    if not gabagool_engine:
        gabagool_engine = GabagoolEngine(config=GabagoolConfig(), executor=executor)

    try:
        if not auto_optimizer:
            auto_optimizer = AutoOptimizer(scanner=scanner, gabagool=gabagool_engine)

        await auto_optimizer.start()
        return {"success": True, "message": f"Auto-Optimizer démarré en mode {auto_optimizer.mode.value}"}

    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/optimizer/stop")
async def stop_optimizer():
    """Arrête l'Auto-Optimizer."""
    global auto_optimizer

    if not auto_optimizer:
        return {"success": False, "message": "Auto-Optimizer non initialisé"}

    await auto_optimizer.stop()
    return {"success": True, "message": "Auto-Optimizer arrêté"}


@app.get("/api/optimizer/status")
async def get_optimizer_status():
    """Retourne le statut complet de l'Auto-Optimizer."""
    global auto_optimizer

    if not auto_optimizer:
        return {
            "enabled": False,
            "mode": "manual",
            "running": False,
            "last_update": None,
            "total_adjustments": 0,
            "current_params": {},
            "optimized_params": {},
            "conditions": {},
            "recent_events": []
        }

    return auto_optimizer.get_status()


@app.post("/api/optimizer/mode")
async def set_optimizer_mode(update: OptimizerModeUpdate):
    """Change le mode de l'Auto-Optimizer."""
    global auto_optimizer, scanner, gabagool_engine, executor

    # Créer l'optimizer si nécessaire
    if not auto_optimizer:
        if not gabagool_engine:
            gabagool_engine = GabagoolEngine(config=GabagoolConfig(), executor=executor)
        auto_optimizer = AutoOptimizer(scanner=scanner, gabagool=gabagool_engine)

    try:
        mode = OptimizerMode(update.mode)
        auto_optimizer.set_mode(mode)
        return {"success": True, "message": f"Mode changé: {mode.value}", "mode": mode.value}
    except ValueError:
        return {"success": False, "message": f"Mode invalide: {update.mode}. Utilisez: manual, semi_auto, full_auto"}


@app.get("/api/optimizer/suggestions")
async def get_optimizer_suggestions():
    """Retourne les suggestions d'optimisation (pour mode semi-auto)."""
    global auto_optimizer

    if not auto_optimizer:
        return {"conditions": {}, "suggestions": [], "timestamp": None}

    return auto_optimizer.get_suggestions()


@app.post("/api/optimizer/apply/{param_name}")
async def apply_optimizer_suggestion(param_name: str):
    """Applique une suggestion spécifique."""
    global auto_optimizer

    if not auto_optimizer:
        return {"success": False, "message": "Auto-Optimizer non initialisé"}

    success = auto_optimizer.apply_suggestion(param_name)
    if success:
        return {"success": True, "message": f"Suggestion '{param_name}' appliquée"}
    return {"success": False, "message": f"Paramètre '{param_name}' non trouvé"}


@app.post("/api/optimizer/toggle")
async def toggle_optimizer():
    """Active/désactive l'Auto-Optimizer."""
    global auto_optimizer

    if not auto_optimizer:
        return {"success": False, "message": "Auto-Optimizer non initialisé"}

    auto_optimizer.enabled = not auto_optimizer.enabled
    status = "activé" if auto_optimizer.enabled else "désactivé"
    return {"success": True, "message": f"Auto-Optimizer {status}", "enabled": auto_optimizer.enabled}


# ═══════════════════════════════════════════════════════════════
# WEBSOCKET
# ═══════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket pour les updates temps réel."""
    await websocket.accept()
    connected_websockets.append(websocket)
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_websockets.remove(websocket)


async def broadcast(message: dict):
    """Envoie un message à tous les WebSockets connectés."""
    for ws in connected_websockets.copy():
        try:
            await ws.send_json(message)
        except Exception:
            connected_websockets.remove(ws)


async def broadcast_loop():
    """Boucle de broadcast des données."""
    global scanner, analyzer, is_running, cg_client, trade_manager, market_maker, gabagool_engine, auto_optimizer

    # HFT: Connection warming périodique pour éviter cold starts
    async def keep_connections_warm():
        while is_running:
            await asyncio.sleep(30)  # Toutes les 30 secondes
            try:
                if private_client:
                    await private_client.warm_connections()
            except Exception:
                pass  # Silencieux en cas d'erreur

    # Lancer le warming en background
    warming_task = asyncio.create_task(keep_connections_warm())

    while is_running:
        try:
            if scanner and analyzer:
                # Get Volatility Data if available
                volatility_map = {}
                try:
                    if cg_client:
                        # Convert list of tuples to dict {asset: score}
                        vol_data = await cg_client.get_volatility_ranking()
                        volatility_map = {item[0]: item[1] for item in vol_data}
                except Exception as e:
                    print(f"CoinGecko Vol error: {e}")

                # Thread-safe: utiliser snapshot au lieu d'accès direct
                markets = await scanner.get_markets_snapshot()
                opportunities = analyzer.analyze_all_markets(markets, volatility_map)

                # Update Market Maker with fresh market data
                if market_maker and market_maker.is_running:
                    market_maker.update_markets(markets)

                # Gabagool: Analyser les opportunités d'arbitrage (PARALLÉLISÉ)
                if gabagool_engine and gabagool_engine.is_running:
                    # Mettre à jour les marchés prioritaires pour le scanner
                    scanner.set_priority_markets(gabagool_engine.get_active_position_ids())

                    # Créer liste de marchés valides
                    valid_markets = [
                        (mid, mdata) for mid, mdata in markets.items()
                        if mdata.is_valid and mdata.best_ask_yes and mdata.best_ask_no
                    ]

                    # Analyser par batch de 10 en parallèle
                    async def analyze_single(mid, mdata):
                        try:
                            market = mdata.market
                            action, size_usd = await gabagool_engine.analyze_opportunity(
                                market_id=mid,
                                token_yes_id=market.token_yes_id,
                                token_no_id=market.token_no_id,
                                price_yes=mdata.best_ask_yes or 1.0,
                                price_no=mdata.best_ask_no or 1.0,
                                question=market.question
                            )
                            return (mid, mdata, action, size_usd)
                        except Exception:
                            return (mid, mdata, None, 0)

                    # Traiter par batch pour éviter surcharge
                    batch_size = 10
                    for i in range(0, len(valid_markets), batch_size):
                        batch = valid_markets[i:i+batch_size]
                        results = await asyncio.gather(*[
                            analyze_single(mid, mdata) for mid, mdata in batch
                        ])

                        # Exécuter les ordres (séquentiellement pour éviter race conditions)
                        for mid, mdata, action, size_usd in results:
                            if action == "buy_yes" and mdata.best_ask_yes:
                                qty = size_usd / mdata.best_ask_yes
                                await gabagool_engine.buy_yes(
                                    mid, mdata.market.token_yes_id,
                                    mdata.best_ask_yes, qty, mdata.market.question
                                )
                            elif action == "buy_no" and mdata.best_ask_no:
                                qty = size_usd / mdata.best_ask_no
                                await gabagool_engine.buy_no(
                                    mid, mdata.market.token_no_id,
                                    mdata.best_ask_no, qty, mdata.market.question
                                )

                # Update trade prices if any
                if trade_manager:
                    for trade in trade_manager.active_trades:
                        market_data = await scanner.get_market_data(trade.market_id)
                        if market_data:
                            # Update current price based on side
                            current_price = (
                                market_data.best_bid_yes if trade.side == TradeSide.YES
                                else market_data.best_bid_no
                            )
                            if current_price:
                                trade_manager.update_price(trade.id, current_price)

                await broadcast({
                    "type": "update",
                    "data": {
                        "markets_count": len(markets),
                        "opportunities": [
                            {
                                "id": opp.id,
                                "market": opp.question[:50],
                                "spread": round(opp.effective_spread, 4),
                                "volume": opp.volume,
                                "price_yes": round(opp.best_ask_yes, 2),
                                "price_no": round(opp.best_ask_no, 2),
                                "score": opp.score,
                                "action": opp.action.value,
                                # Added full data for frontend
                                "market_id": opp.market_id,
                                "question": opp.question,
                            }
                            for opp in opportunities[:15]
                        ],
                        "active_trades": [t.to_dict() for t in trade_manager.active_trades] if trade_manager else [],
                        "stats": trade_manager.get_stats() if trade_manager else {},
                        "market_maker": market_maker.stats if market_maker else {},
                        "gabagool": gabagool_engine.get_stats() if gabagool_engine else {},
                        "optimizer": auto_optimizer.get_status() if auto_optimizer else {},
                        "timestamp": datetime.now().isoformat(),
                    }
                })

            await asyncio.sleep(0.5)  # HFT: 500ms au lieu de 2s (4x plus réactif)

        except Exception as e:
            print(f"Broadcast error: {e}")
            await asyncio.sleep(2)  # Attente réduite en cas d'erreur

async def broadcast_trades():
    """Broadcast uniquement les mises à jour de trades."""
    if not trade_manager:
        return

    await broadcast({
        "type": "trades_update",
        "data": {
            "active": [t.to_dict() for t in trade_manager.active_trades],
            "closed": [t.to_dict() for t in trade_manager.closed_trades],
            "stats": trade_manager.get_stats()
        }
    })


# ═══════════════════════════════════════════════════════════════
# ENDPOINT PERFORMANCE HFT
# ═══════════════════════════════════════════════════════════════

@app.get("/api/performance")
async def get_performance():
    """Retourne les statistiques de performance HFT."""
    global scanner

    perf_status = get_performance_status()
    scanner_stats = scanner.performance_stats if scanner else {}

    return {
        "optimizations": {
            "uvloop": perf_status["uvloop"],
            "orjson": perf_status["orjson"],
            "cachetools": perf_status["cachetools"],
        },
        "cache": {
            "orderbook": perf_status["orderbook_cache"],
            "market": perf_status["market_cache"],
        },
        "scanner": scanner_stats,
    }


# ═══════════════════════════════════════════════════════════════
# HEALTH CHECK & METRICS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/health")
async def health_check():
    """
    Health check endpoint pour monitoring.

    Retourne le statut de santé de tous les composants.
    """
    global scanner, gabagool_engine, executor

    health_checker = get_health_checker()

    # Enregistrer les composants si pas déjà fait
    def scanner_health() -> ComponentHealth:
        if scanner and scanner.state == ScannerState.RUNNING:
            return ComponentHealth("scanner", "healthy", "Running")
        elif scanner and scanner.state == ScannerState.STARTING:
            return ComponentHealth("scanner", "degraded", "Starting")
        return ComponentHealth("scanner", "unhealthy", "Not running")

    def gabagool_health() -> ComponentHealth:
        if gabagool_engine and gabagool_engine._is_running:
            return ComponentHealth("gabagool", "healthy", "Running")
        return ComponentHealth("gabagool", "unhealthy", "Not running")

    def executor_health() -> ComponentHealth:
        if executor:
            return ComponentHealth("executor", "healthy", "Ready")
        return ComponentHealth("executor", "unhealthy", "Not initialized")

    health_checker.register_component("scanner", scanner_health)
    health_checker.register_component("gabagool", gabagool_health)
    health_checker.register_component("executor", executor_health)

    return health_checker.check_all()


@app.get("/api/metrics")
async def get_metrics():
    """
    Retourne les métriques internes du bot.

    Inclut: trades, profit, latence, erreurs, uptime.
    """
    metrics = get_metrics_manager().get_metrics()
    circuits = get_circuit_stats()

    return {
        "metrics": metrics,
        "circuit_breakers": circuits
    }


@app.post("/api/metrics/reset")
async def reset_metrics():
    """Remet à zéro les métriques (sauf les cumulatifs)."""
    metrics_manager = get_metrics_manager()
    metrics_manager._metrics.latency_samples = 0
    metrics_manager._metrics.total_latency_ms = 0
    metrics_manager._metrics.errors_count = 0
    metrics_manager.save()
    return {"success": True, "message": "Metrics reset"}


if __name__ == "__main__":
    import uvicorn

    # Activer uvloop AVANT de démarrer uvicorn
    setup_uvloop()

    uvicorn.run(
        "web.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        loop="uvloop"  # Utiliser uvloop si disponible
    )
