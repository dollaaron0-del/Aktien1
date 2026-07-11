#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Aktien-Bot Restore
#
# Stellt ein Backup auf einem neuen PC / Server wieder her.
# Voraussetzung: git clone bereits gemacht, Python installiert.
#
# Verwendung:
#   bash scripts/restore.sh aktien_backup_20260520_143000.tar.gz
#   bash scripts/restore.sh ~/Downloads/aktien_backup_20260520_143000.tar.gz
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ARCHIVE="${1:-}"

# ── Argumente prüfen ──────────────────────────────────────────────────────────

if [[ -z "$ARCHIVE" ]]; then
    echo ""
    echo "Verwendung: bash scripts/restore.sh <backup-archiv.tar.gz>"
    echo ""
    echo "Beispiel:"
    echo "  bash scripts/restore.sh ~/Downloads/aktien_backup_20260520_143000.tar.gz"
    echo ""
    exit 1
fi

if [[ ! -f "$ARCHIVE" ]]; then
    echo "✗  Archiv nicht gefunden: $ARCHIVE"
    exit 1
fi

# ── Header ────────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║           Aktien-Bot Restore                             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  Archiv:    $ARCHIVE"
echo "  Ziel:      $PROJECT_DIR"
echo ""

# Bestehende Daten sichern falls vorhanden
if [[ -f "$PROJECT_DIR/.env" || -f "$PROJECT_DIR/data/trade_journal.db" ]]; then
    EXISTING_BACKUP="$PROJECT_DIR/data/pre_restore_backup_$(date +%Y%m%d_%H%M%S).tar.gz"
    echo "⚠  Bestehende Daten gefunden – erstelle Sicherheitskopie:"
    echo "   $EXISTING_BACKUP"
    echo ""
    cd "$PROJECT_DIR"
    EXISTING=()
    [[ -f ".env" ]]                    && EXISTING+=(".env")
    [[ -d "data" ]]                    && EXISTING+=("data/")
    [[ ${#EXISTING[@]} -gt 0 ]]        && tar -czf "$EXISTING_BACKUP" "${EXISTING[@]}" 2>/dev/null || true
fi

# ── Archiv entpacken ──────────────────────────────────────────────────────────

echo "Entpacke Archiv..."
cd "$PROJECT_DIR"
tar -xzf "$ARCHIVE"
echo ""

# ── Python-Abhängigkeiten installieren ───────────────────────────────────────

echo "Installiere Python-Abhängigkeiten..."
echo ""

if command -v pip3 &>/dev/null; then
    PIP="pip3"
elif command -v pip &>/dev/null; then
    PIP="pip"
else
    echo "⚠  pip nicht gefunden – bitte manuell ausführen: pip install -r requirements.lock"
    PIP=""
fi

if [[ -n "$PIP" && -f "$PROJECT_DIR/requirements.lock" ]]; then
    $PIP install -r "$PROJECT_DIR/requirements.lock" -q && echo "  ✓ Pakete installiert (requirements.lock, gepinnt)" || echo "  ⚠ pip install fehlgeschlagen – bitte manuell prüfen"
elif [[ -n "$PIP" && -f "$PROJECT_DIR/requirements.txt" ]]; then
    $PIP install -r "$PROJECT_DIR/requirements.txt" -q && echo "  ✓ Pakete installiert (requirements.txt, kein Lockfile gefunden)" || echo "  ⚠ pip install fehlgeschlagen – bitte manuell prüfen"
fi

# ── Datenbank-Verzeichnis sicherstellen ───────────────────────────────────────

mkdir -p "$PROJECT_DIR/data"
mkdir -p "$PROJECT_DIR/logs"

# ── Prüfung nach Restore ──────────────────────────────────────────────────────

echo ""
echo "Prüfe wiederhergestellte Dateien:"
echo ""

ALL_OK=true

check_file() {
    local label="$1"
    local file="$PROJECT_DIR/$2"
    if [[ -e "$file" ]]; then
        size=$(du -sh "$file" 2>/dev/null | cut -f1)
        printf "  ✓  %-12s  %-40s  %s\n" "$label" "$2" "$size"
    else
        printf "  ✗  %-12s  %-40s  %s\n" "$label" "$2" "FEHLT"
        if [[ "$label" == "KRITISCH" ]]; then
            ALL_OK=false
        fi
    fi
}

check_file "KRITISCH"  ".env"
check_file "WICHTIG"   "data/trade_journal.db"
check_file "WICHTIG"   "data/performance.db"
check_file "WICHTIG"   "data/bot_score.json"
check_file "WICHTIG"   "data/sentiment_memory.json"
check_file "OPTIONAL"  "data/news_archive.db"
check_file "OPTIONAL"  "data/social_pulse.db"
check_file "OPTIONAL"  "data/short_positions.json"

echo ""

# ── Schnelltest ───────────────────────────────────────────────────────────────

echo "Führe Schnelltest durch..."
echo ""

if command -v python3 &>/dev/null; then
    cd "$PROJECT_DIR"
    if python3 -c "from config import config, validate_config; validate_config()" 2>/dev/null; then
        echo "  ✓ Konfiguration valide"
    else
        echo "  ⚠ Konfiguration fehlerhaft – .env prüfen"
        ALL_OK=false
    fi
    if python3 -c "from portfolio.portfolio import Portfolio; p = Portfolio(10000)" 2>/dev/null; then
        echo "  ✓ Datenbank-Zugriff OK"
    else
        echo "  ⚠ Datenbank-Fehler – data/ Verzeichnis prüfen"
    fi
else
    echo "  ⚠ python3 nicht gefunden"
fi

echo ""

# ── Abschluss ─────────────────────────────────────────────────────────────────

if [[ "$ALL_OK" == true ]]; then
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  ✓  Restore erfolgreich                                  ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
    echo "Bot starten:"
    echo "  cd $PROJECT_DIR"
    echo "  python3 main.py --status     # Portfolio-Übersicht"
    echo "  python3 main.py              # Bot starten"
else
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║  ⚠  Restore mit Warnungen abgeschlossen                  ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
    echo "Fehlende KRITISCHE Dateien müssen manuell ergänzt werden:"
    echo "  .env  →  API-Keys und Bot-Konfiguration (siehe README)"
    echo ""
fi
