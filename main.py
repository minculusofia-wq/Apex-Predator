#!/usr/bin/env python3
"""
HFT Scalper Bot - Polymarket

Bot de trading haute fréquence automatisé pour les marchés crypto Up/Down.

Usage:
    python main.py              # Lance l'interface graphique
    python main.py --cli        # Mode ligne de commande simple
    python main.py --help       # Affiche l'aide

Author: Bot HFT Polymarket
Version: 1.0.0
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Ajouter le répertoire racine au path
sys.path.insert(0, str(Path(__file__).parent))

from config import get_settings, get_trading_params
from utils.logger import setup_logging, bot_logger


def parse_args():
    """Parse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="🚀 HFT Scalper Bot - Polymarket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python main.py              Lance l'interface graphique Textual
  python main.py --cli        Mode ligne de commande (sans interface)
  python main.py --debug      Active les logs de débogage
        """
    )
    
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Mode ligne de commande (sans interface graphique)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Active les logs de débogage"
    )
    
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Fichier de log (optionnel)"
    )
    
    return parser.parse_args()


async def run_cli_mode():
    """Mode ligne de commande simple."""
    from core import MarketScanner, OpportunityAnalyzer
    from core.alpha import BinanceOracle
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich import box
    
    console = Console()
    settings = get_settings()
    params = get_trading_params()
    
    console.print("\n[bold cyan]🚀 HFT Scalper Bot - Mode CLI[/bold cyan]")
    console.print(f"[dim]Spread minimum: ${params.min_spread:.2f}[/dim]")
    console.print(f"[dim]Capital/trade: ${params.capital_per_trade:.2f}[/dim]")
    console.print()
    
    # Initialiser les composants
    scanner = MarketScanner()
    analyzer = OpportunityAnalyzer(params)
    oracle = BinanceOracle()

    # Start Oracle in background
    await oracle.start()
    
    console.print("[yellow]⏳ Connexion aux APIs...[/yellow]")
    
    try:
        await scanner.start()
        console.print(f"[green]✅ Connecté! {scanner.market_count} marchés détectés[/green]")
    except Exception as e:
        console.print(f"[red]❌ Erreur connexion: {e}[/red]")
        return
    
    console.print("\n[bold]📊 Scan en cours... (Ctrl+C pour quitter)[/bold]\n")
    
    try:
        while True:
            # Analyser les marchés
            markets = scanner.markets
            opportunities = analyzer.analyze_all_markets(markets)
            
            # Créer la table
            table = Table(
                title="🎯 Opportunités Détectées",
                box=box.ROUNDED,
                show_header=True,
                header_style="bold cyan"
            )
            
            table.add_column("Score", style="yellow", justify="center")
            table.add_column("Marché", style="white", max_width=40)
            table.add_column("Spread", style="green", justify="right")
            table.add_column("Volume", style="blue", justify="right")
            table.add_column("Action", style="magenta", justify="center")
            
            for opp in opportunities[:10]:
                stars = "⭐" * opp.score
                market = opp.question[:38] + "..." if len(opp.question) > 38 else opp.question
                spread = f"${opp.effective_spread:.3f}"
                
                if opp.volume >= 1000000:
                    volume = f"${opp.volume/1000000:.1f}M"
                elif opp.volume >= 1000:
                    volume = f"${opp.volume/1000:.1f}k"
                else:
                    volume = f"${opp.volume:.0f}"
                
                if opp.action.value == "trade":
                    action = "[bold green]🚀 TRADE[/bold green]"
                elif opp.action.value == "watch":
                    action = "[yellow]👀 WATCH[/yellow]"
                else:
                    action = "[dim]⏭️ SKIP[/dim]"
                
                table.add_row(stars, market, spread, volume, action)
            
            # Afficher
            console.clear()
            console.print(table)
            console.print(f"\n[dim]Dernière mise à jour: {asyncio.get_event_loop().time():.0f}s | Marchés: {len(markets)}[/dim]")
            console.print("[dim]Appuyez Ctrl+C pour quitter[/dim]")
            
            # Attendre avant le prochain scan
            await asyncio.sleep(3)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]⏹️ Arrêt du bot...[/yellow]")
    finally:
        if 'oracle' in locals():
            await oracle.stop()
        await scanner.stop()
        console.print("[green]✅ Bot arrêté proprement[/green]")


def run_gui_mode():
    """Mode interface graphique Textual."""
    from ui import HFTScalperApp
    
    app = HFTScalperApp()
    app.run()


def main():
    """Point d'entrée principal."""
    args = parse_args()

    # HFT: Activer uvloop AVANT toute création d'event loop
    from core.performance import setup_uvloop
    setup_uvloop()

    # Configurer le logging
    import logging
    level = logging.DEBUG if args.debug else logging.INFO
    setup_logging(level=level, log_file=args.log_file)
    
    # Afficher la bannière
    print("""
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   🚀 HFT SCALPER BOT - POLYMARKET                            ║
║                                                               ║
║   Trading automatisé sur les marchés crypto Up/Down          ║
║   BTC • SOL • ETH • XRP                                      ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
    """)
    
    if args.cli:
        # Mode CLI
        asyncio.run(run_cli_mode())
    else:
        # Mode GUI
        run_gui_mode()


if __name__ == "__main__":
    main()
