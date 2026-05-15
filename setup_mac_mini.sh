#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Stock Sentiment Bot – Mac mini M5 Setup-Skript
# Kompatibel mit Apple Silicon (M1/M2/M3/M4/M5), macOS 14+
#
# Ausführen:
#   chmod +x setup_mac_mini.sh
#   ./setup_mac_mini.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║    Stock Sentiment Bot – Mac mini M5 Setup               ║"
echo "║    Apple Silicon · macOS 14+ · Python 3.11+              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── RAM erkennen für Modell-Empfehlung ────────────────────────────────────────
RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
info "Erkannter RAM: ${RAM_GB} GB"

if   [ "$RAM_GB" -ge 32 ]; then RECOMMENDED_MODEL="llama3.3:70b"
elif [ "$RAM_GB" -ge 24 ]; then RECOMMENDED_MODEL="qwen2.5:14b"
elif [ "$RAM_GB" -ge 16 ]; then RECOMMENDED_MODEL="llama3.1:8b"
else                             RECOMMENDED_MODEL="llama3.2:3b"
fi
info "Empfohlenes Ollama-Modell für ${RAM_GB} GB RAM: ${BOLD}${RECOMMENDED_MODEL}${NC}"

# ── Apple Silicon prüfen ──────────────────────────────────────────────────────
ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    warn "Kein Apple Silicon erkannt (${ARCH}) – Script ist für M-Chips optimiert."
fi

# ── Homebrew ──────────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    info "Installiere Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Apple Silicon: Homebrew in /opt/homebrew
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
    success "Homebrew installiert"
else
    success "Homebrew bereits vorhanden ($(brew --version | head -1))"
fi

# ── Python 3.11+ ──────────────────────────────────────────────────────────────
if ! command -v python3.11 &>/dev/null && ! python3 --version 2>&1 | grep -qE "3\.(11|12|13)"; then
    info "Installiere Python 3.11..."
    brew install python@3.11
    success "Python 3.11 installiert"
else
    success "Python $(python3 --version) vorhanden"
fi

PYTHON=$(command -v python3.11 || command -v python3)

# ── Ollama ────────────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    info "Installiere Ollama (Apple Silicon, Metal GPU-Beschleunigung)..."
    brew install ollama
    success "Ollama installiert"
else
    success "Ollama bereits vorhanden ($(ollama --version 2>/dev/null | head -1))"
fi

# Ollama als Hintergrunddienst starten
if ! pgrep -x ollama &>/dev/null; then
    info "Starte Ollama-Dienst..."
    brew services start ollama
    sleep 3
    success "Ollama-Dienst gestartet"
else
    success "Ollama-Dienst läuft bereits"
fi

# ── Ollama-Modell herunterladen ───────────────────────────────────────────────
info "Lade Modell: ${RECOMMENDED_MODEL}"
info "(Das kann beim ersten Mal 5–30 Minuten dauern...)"

if ollama list 2>/dev/null | grep -q "${RECOMMENDED_MODEL%:*}"; then
    success "Modell ${RECOMMENDED_MODEL} bereits vorhanden"
else
    ollama pull "$RECOMMENDED_MODEL"
    success "Modell ${RECOMMENDED_MODEL} heruntergeladen"
fi

# ── Python Virtual Environment ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    info "Erstelle Python Virtual Environment..."
    "$PYTHON" -m venv "$VENV_DIR"
    success "Virtual Environment erstellt: $VENV_DIR"
else
    success "Virtual Environment bereits vorhanden"
fi

source "$VENV_DIR/bin/activate"

# pip aktualisieren
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

# ── Python Dependencies ───────────────────────────────────────────────────────
info "Installiere Python-Abhängigkeiten..."
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" --quiet
    success "Python-Pakete installiert"
else
    # Fallback: Direkte Installation
    "$VENV_DIR/bin/pip" install --quiet \
        anthropic yfinance requests schedule streamlit \
        pandas plotly rich python-dotenv
    success "Python-Pakete installiert (ohne requirements.txt)"
fi

# ── .env Datei ────────────────────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    info "Erstelle .env Vorlage..."
    cat > "$ENV_FILE" << EOF
# ─── API Keys ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...        # Pflicht: https://console.anthropic.com
ALPACA_API_KEY=PK...                # Alpaca Paper Trading
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
BROKER_MODE=alpaca

# Optional
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
NEWSAPI_KEY=

# ─── Ollama (lokal, Mac mini M5) ─────────────────────────────────────────────
OLLAMA_ENABLED=true
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=${RECOMMENDED_MODEL}
OLLAMA_TIMEOUT=30

# ─── Trading ──────────────────────────────────────────────────────────────────
INITIAL_CAPITAL=10000.0
BROKER_MODE=alpaca
MARKET_EXCHANGES=NYSE,XETRA
ENABLE_SOCIAL_SCAN=true
AUTO_SCAN_WATCHLIST=true
SCAN_MAX_PICKS=12

# ─── Risiko ───────────────────────────────────────────────────────────────────
STOP_LOSS_PCT=0.07
TAKE_PROFIT_PCT=0.20
MAX_DAILY_LOSS_PCT=0.05
MAX_DRAWDOWN_PCT=0.15
FOCUS_MODE=WEALTH_BUILDING
EOF
    warn ".env erstellt – bitte API-Keys eintragen: $ENV_FILE"
else
    success ".env bereits vorhanden"
    # Ollama-Settings aktualisieren falls nicht vorhanden
    if ! grep -q "OLLAMA_ENABLED" "$ENV_FILE"; then
        echo "" >> "$ENV_FILE"
        echo "# Ollama (lokal)" >> "$ENV_FILE"
        echo "OLLAMA_ENABLED=true" >> "$ENV_FILE"
        echo "OLLAMA_URL=http://localhost:11434" >> "$ENV_FILE"
        echo "OLLAMA_MODEL=${RECOMMENDED_MODEL}" >> "$ENV_FILE"
        echo "OLLAMA_TIMEOUT=30" >> "$ENV_FILE"
        success "Ollama-Settings zur .env hinzugefügt"
    fi
fi

# ── Autostart via launchd (optional) ─────────────────────────────────────────
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/com.stockbot.plist"

echo ""
echo -e "${YELLOW}Autostart einrichten? (Bot startet automatisch beim Mac-Login)${NC}"
read -r -p "Autostart aktivieren? [j/N] " AUTOSTART
if [[ "$AUTOSTART" =~ ^[jJyY]$ ]]; then
    mkdir -p "$PLIST_DIR"
    cat > "$PLIST_FILE" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.stockbot</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/python</string>
        <string>${SCRIPT_DIR}/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/data/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/data/logs/launchd_error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF
    launchctl load "$PLIST_FILE" 2>/dev/null || true
    success "Autostart eingerichtet: $PLIST_FILE"
    info "Bot-Log: ${SCRIPT_DIR}/data/logs/launchd.log"
    info "Deaktivieren: launchctl unload $PLIST_FILE"
fi

# ── Ollama Test ───────────────────────────────────────────────────────────────
echo ""
info "Teste Ollama-Verbindung..."
TEST_RESULT=$(curl -s -X POST http://localhost:11434/api/generate \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${RECOMMENDED_MODEL}\",\"prompt\":\"Reply with only: OK\",\"stream\":false,\"options\":{\"num_predict\":5}}" \
    2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('response','').strip())" 2>/dev/null || echo "FEHLER")

if echo "$TEST_RESULT" | grep -qi "ok"; then
    success "Ollama antwortet korrekt: '${TEST_RESULT}'"
else
    warn "Ollama-Test: '${TEST_RESULT}' – prüfe ob Modell geladen ist"
fi

# ── Zusammenfassung ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Setup abgeschlossen!${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "  RAM:           ${RAM_GB} GB"
echo "  Ollama-Modell: ${RECOMMENDED_MODEL}"
echo "  Python:        $("$VENV_DIR/bin/python" --version)"
echo "  Venv:          $VENV_DIR"
echo ""
echo -e "${YELLOW}Nächste Schritte:${NC}"
echo "  1. API-Keys in .env eintragen: open '$ENV_FILE'"
echo "  2. Bot starten:  source .venv/bin/activate && python main.py"
echo "  3. Dashboard:    source .venv/bin/activate && python main.py --dashboard"
echo "  4. Status:       source .venv/bin/activate && python main.py --status"
echo ""
echo -e "  Bot-Log:  ${BLUE}${SCRIPT_DIR}/data/logs/bot.log${NC}"
echo -e "  Ollama:   ${BLUE}http://localhost:11434${NC}"
echo ""
