#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Aktien-Bot Backup
#
# Packt alle Daten (Trades, Portfolio, Konfiguration) in ein tar.gz Archiv.
# Der Code selbst muss nicht gesichert werden – er liegt auf GitHub.
#
# Verwendung:
#   bash scripts/backup.sh                    # Backup im aktuellen Verzeichnis
#   bash scripts/backup.sh /mnt/backup        # Backup in bestimmten Ordner
#   bash scripts/backup.sh --verify           # Nur prüfen was gesichert würde
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DEST_DIR="${1:-$PROJECT_DIR}"
VERIFY_ONLY=false

if [[ "${1:-}" == "--verify" ]]; then
    VERIFY_ONLY=true
    DEST_DIR="$PROJECT_DIR"
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE_NAME="aktien_backup_${TIMESTAMP}.tar.gz"
ARCHIVE_PATH="$DEST_DIR/$ARCHIVE_NAME"

# ── Dateien die gesichert werden ─────────────────────────────────────────────

# Kritisch: ohne diese Dateien läuft gar nichts
CRITICAL=(
    ".env"
)

# Wichtig: Trade-Historie, Portfolio, gelerntes Wissen
IMPORTANT=(
    "data/trade_journal.db"
    "data/performance.db"
    "data/reflections.db"
    "data/bot_score.json"
    "data/sentiment_memory.json"
    "data/news_velocity.json"
    "data/reentry_watch.json"
    "data/short_positions.json"
)

# Optional: Social-Daten, News-Archiv (groß, aber nützlich)
OPTIONAL=(
    "data/social_pulse.db"
    "data/weekly_briefing.db"
    "data/signal_queue.db"
    "data/news_archive.db"
    "logs/"
)

# ── Prüfung ───────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║           Aktien-Bot Backup                              ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

MISSING_CRITICAL=()
for f in "${CRITICAL[@]}"; do
    if [[ ! -e "$PROJECT_DIR/$f" ]]; then
        MISSING_CRITICAL+=("$f")
    fi
done

if [[ ${#MISSING_CRITICAL[@]} -gt 0 ]]; then
    echo "⚠  WARNUNG: Kritische Dateien fehlen:"
    for f in "${MISSING_CRITICAL[@]}"; do
        echo "   • $f"
    done
    echo ""
fi

# Sammle alle tatsächlich vorhandenen Dateien
FILES_TO_BACKUP=()
TOTAL_SIZE=0

check_and_add() {
    local label="$1"
    local file="$2"
    if [[ -e "$PROJECT_DIR/$file" ]]; then
        FILES_TO_BACKUP+=("$file")
        size=$(du -sh "$PROJECT_DIR/$file" 2>/dev/null | cut -f1)
        printf "  %-8s  %-40s  %s\n" "[$label]" "$file" "$size"
    else
        printf "  %-8s  %-40s  %s\n" "[$label]" "$file" "(nicht vorhanden – wird übersprungen)"
    fi
}

echo "Dateien die gesichert werden:"
echo ""
for f in "${CRITICAL[@]}";  do check_and_add "KRITISCH" "$f"; done
for f in "${IMPORTANT[@]}"; do check_and_add "WICHTIG"  "$f"; done
for f in "${OPTIONAL[@]}";  do check_and_add "OPTIONAL" "$f"; done
echo ""

if [[ "$VERIFY_ONLY" == true ]]; then
    echo "ℹ  --verify Modus: kein Archiv erstellt."
    echo ""
    exit 0
fi

if [[ ${#FILES_TO_BACKUP[@]} -eq 0 ]]; then
    echo "✗  Keine Dateien zum Sichern gefunden. Bitte im Projektverzeichnis ausführen."
    exit 1
fi

# ── Archiv erstellen ──────────────────────────────────────────────────────────

mkdir -p "$DEST_DIR"

cd "$PROJECT_DIR"
echo "Erstelle Archiv: $ARCHIVE_PATH"
echo ""

# Nur vorhandene Dateien übergeben
tar -czf "$ARCHIVE_PATH" "${FILES_TO_BACKUP[@]}" 2>/dev/null || true

ARCHIVE_SIZE=$(du -sh "$ARCHIVE_PATH" 2>/dev/null | cut -f1)

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ✓  Backup erstellt                                      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  Archiv:  $ARCHIVE_PATH"
echo "  Größe:   $ARCHIVE_SIZE"
echo ""
echo "Auf den PC übertragen (Beispiel):"
echo "  scp $ARCHIVE_PATH user@mein-pc:~/Downloads/"
echo ""
echo "Wiederherstellen auf dem PC:"
echo "  bash scripts/restore.sh ~/Downloads/$ARCHIVE_NAME"
echo ""
