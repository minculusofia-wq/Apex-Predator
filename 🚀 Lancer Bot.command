#!/bin/bash
# =============================================================================
# 🚀 HFT Scalper Bot - Interface Web
# =============================================================================
# Double-cliquez pour lancer le dashboard web !
# Utilise automatiquement l'environnement virtuel Python
# =============================================================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Couleurs
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

clear
echo -e "${CYAN}"
cat << "EOF"
╔═══════════════════════════════════════════════════════════════════════╗
║                                                                       ║
║   ██╗  ██╗███████╗████████╗    ███████╗ ██████╗ █████╗ ██╗     ██████╗║
║   ██║  ██║██╔════╝╚══██╔══╝    ██╔════╝██╔════╝██╔══██╗██║     ██╔══██║
║   ███████║█████╗     ██║       ███████╗██║     ███████║██║     ██████╔╝
║   ██╔══██║██╔══╝     ██║       ╚════██║██║     ██╔══██║██║     ██╔═══╝ ║
║   ██║  ██║██║        ██║       ███████║╚██████╗██║  ██║███████╗██║     ║
║   ╚═╝  ╚═╝╚═╝        ╚═╝       ╚══════╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝     ║
║                                                                       ║
║                    🌐 DASHBOARD WEB 🌐                                ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

# Vérifier Python
echo -e "${YELLOW}⏳ Vérification de Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 non trouvé !${NC}"
    read -p "Appuyez sur Entrée pour fermer..."
    exit 1
fi
echo -e "${GREEN}✓ Python3 OK${NC}"

# Vérifier/Créer l'environnement virtuel
if [ ! -d "venv" ] || [ ! -f "venv/bin/activate" ]; then
    echo -e "${YELLOW}⏳ Création de l'environnement virtuel...${NC}"
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Échec de création du venv${NC}"
        read -p "Appuyez sur Entrée pour fermer..."
        exit 1
    fi
    echo -e "${GREEN}✓ Environnement virtuel créé${NC}"
fi

# Activer l'environnement virtuel
echo -e "${YELLOW}⏳ Activation de l'environnement virtuel...${NC}"
source venv/bin/activate
echo -e "${GREEN}✓ Environnement virtuel activé${NC}"

# Vérifier les dépendances dans le venv
python3 -c "import fastapi, uvicorn" 2>/dev/null
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}⏳ Installation des dépendances...${NC}"
    pip install --upgrade pip --quiet 2>/dev/null
    pip install -r requirements.txt --quiet 2>/dev/null || pip install -r requirements.txt
    echo -e "${GREEN}✓ Dépendances installées${NC}"
fi

# Créer .env si nécessaire
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}✓ Fichier .env créé${NC}"
    fi
fi

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🌐 LANCEMENT DU SERVEUR WEB...${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════════${NC}"
echo ""

# Kill port 8000 if needed
lsof -ti:8000 | xargs kill -9 2>/dev/null

echo -e "${GREEN}📊 Dashboard disponible sur: ${CYAN}http://localhost:8000${NC}"
echo ""

# Ouvrir le navigateur après 2 secondes
(sleep 2 && open "http://localhost:8000") &

# Lancer le serveur avec le Python du venv
python -m uvicorn web.server:app --host 0.0.0.0 --port 8000 --reload

echo ""
echo -e "${YELLOW}Serveur arrêté. Appuyez sur Entrée pour fermer...${NC}"
read
