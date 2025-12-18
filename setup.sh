#!/bin/bash
# =============================================================================
# 🚀 Setup Bot HFT Polymarket
# =============================================================================
# Ce script crée l'environnement virtuel et installe les dépendances
# Résout le problème "externally-managed-environment" de Python 3.12+
# =============================================================================

set -e

# Couleurs
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${CYAN}"
echo "═══════════════════════════════════════════════════════════════════"
echo "🚀 Setup Bot HFT Polymarket"
echo "═══════════════════════════════════════════════════════════════════"
echo -e "${NC}"

# Vérifier Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 n'est pas installé${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo -e "${GREEN}✓ $PYTHON_VERSION détecté${NC}"

# Supprimer ancien venv si corrompu
if [ -d "venv" ] && [ ! -f "venv/bin/activate" ]; then
    echo -e "${YELLOW}⚠️  Environnement virtuel corrompu, suppression...${NC}"
    rm -rf venv
fi

# Créer l'environnement virtuel s'il n'existe pas
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}⏳ Création de l'environnement virtuel...${NC}"
    python3 -m venv venv
    echo -e "${GREEN}✓ Environnement virtuel créé${NC}"
else
    echo -e "${GREEN}✓ Environnement virtuel existant${NC}"
fi

# Activer l'environnement virtuel
echo -e "${YELLOW}⏳ Activation de l'environnement virtuel...${NC}"
source venv/bin/activate

# Mettre à jour pip dans le venv
echo -e "${YELLOW}⏳ Mise à jour de pip...${NC}"
pip install --upgrade pip --quiet 2>/dev/null || pip install --upgrade pip

# Installer les dépendances dans le venv
echo -e "${YELLOW}⏳ Installation des dépendances...${NC}"
pip install -r requirements.txt --quiet 2>/dev/null || pip install -r requirements.txt

# Créer .env si nécessaire
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}✓ Fichier .env créé depuis .env.example${NC}"
    fi
fi

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ Setup terminé avec succès!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "Pour lancer le bot:"
echo -e "  ${CYAN}./🚀\\ Lancer\\ Bot.command${NC}"
echo ""
echo -e "Ou manuellement:"
echo -e "  ${CYAN}source venv/bin/activate${NC}"
echo -e "  ${CYAN}python web/server.py${NC}"
echo ""
