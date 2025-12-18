import asyncio
import sys
import os

# Ajouter le dossier courant au path
sys.path.append(os.getcwd())

from api.public import GammaClient, PolymarketPublicClient
from config import get_settings

async def test_market_discovery():
    print("🚀 Test de découverte des marchés via Gamma API...")
    
    settings = get_settings()
    print(f"📋 Configuration:")
    print(f"  - Mots-clés: {settings.target_keywords}")
    print(f"  - Types: {settings.market_types}")
    
    async with GammaClient() as gamma, PolymarketPublicClient() as poly:
        # 1. Test Gamma Search
        print("\n🔍 Recherche Gamma...")
        try:
            gamma_markets = await gamma.get_crypto_markets()
            print(f"✅ Gamma a trouvé {len(gamma_markets)} marchés potentiels.")
            
            if len(gamma_markets) > 0:
                print("📝 Exemple de marché Gamma:")
                print(gamma_markets[0])
        except Exception as e:
            print(f"❌ Erreur Gamma: {e}")
            return

        print("\n🔍 Récupération détails Polymarket (Marchés OUVERTS seulement)...")
        valid_count = 0
        tested_count = 0
        
        for gm in gamma_markets:
            if gm.get("closed") is True:
                continue
                
            tested_count += 1
            if tested_count > 5:
                break
                
            condition_id = gm.get("conditionId") or gm.get("condition_id") or gm.get("id")
            print(f"\n  Market: {gm.get('question')}")
            print(f"  ID: {condition_id}")
            
            try:
                details = await poly.get_market(condition_id)
                if details:
                    market = poly.parse_market(details)
                    if market:
                        print("  ✅ Parsé avec succès!")
                        valid_count += 1
                        
                        # Test Orderbook
                        print("  📖 Récupération orderbook...")
                        try:
                            ob_yes = await poly.get_orderbook(market.token_yes_id)
                            ob_no = await poly.get_orderbook(market.token_no_id)
                            
                            bids_yes = ob_yes.get("bids", [])
                            asks_yes = ob_yes.get("asks", [])
                            
                            print(f"    - Orderbook YES: {len(bids_yes)} bids, {len(asks_yes)} asks")
                            if bids_yes: print(f"      Best Bid YES: {bids_yes[0]}")
                            if asks_yes: print(f"      Best Ask YES: {asks_yes[0]}")
                            
                            # Simuler analyse
                            from core.analyzer import OpportunityAnalyzer
                            from core.scanner import MarketData
                            md = MarketData(market=market)
                            md.orderbook_yes = ob_yes
                            md.orderbook_no = ob_no
                            if bids_yes: md.best_bid_yes = float(bids_yes[0]["price"])
                            if asks_yes: md.best_ask_yes = float(asks_yes[0]["price"])
                            
                            bids_no = ob_no.get("bids", [])
                            asks_no = ob_no.get("asks", [])
                            if bids_no: md.best_bid_no = float(bids_no[0]["price"])
                            if asks_no: md.best_ask_no = float(asks_no[0]["price"])
                            
                            if md.best_bid_no and md.best_ask_no:
                                md.spread_no = md.best_ask_no - md.best_bid_no
                            if md.best_bid_yes and md.best_ask_yes:
                                md.spread_yes = md.best_ask_yes - md.best_bid_yes
                                
                            analyzer = OpportunityAnalyzer()
                            opp = analyzer.analyze_market(md)
                            
                            if opp:
                                print(f"  🎯 Opportunité détectée! Score: {opp.score}")
                                print(f"     Action: {opp.action}")
                            else:
                                print("  ⚠️ Pas d'opportunité générée (Données invalides?)")
                                print(f"     Is Valid: {md.is_valid}")
                                
                        except Exception as e:
                            print(f"  ❌ Erreur Orderbook: {e}")
                            
                    else:
                        print("  ❌ Échec parsing")
                else:
                    print("  ❌ Pas de détails trouvés")
            except Exception as e:
                print(f"  ❌ Erreur détail: {e}")
        
        print(f"\n📊 Résultat: {valid_count}/5 marchés validés.")

if __name__ == "__main__":
    asyncio.run(test_market_discovery())
