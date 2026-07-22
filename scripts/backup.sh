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
# Standardziel: dedizierter backups/-Ordner (NICHT das Projekt-Root – dort würden
# tar.gz-Archive die Repo-Wurzel zumüllen; backups/ ist in .gitignore).
DEST_DIR="${1:-$PROJECT_DIR/backups}"
VERIFY_ONLY=false

if [[ "${1:-}" == "--verify" ]]; then
    VERIFY_ONLY=true
    DEST_DIR="$PROJECT_DIR/backups"
fi

# Rotation: nur die letzten N Archive behalten (0 = nie löschen).
KEEP="${BACKUP_KEEP:-14}"
# Off-Server-Ziel (optional): rsync-fähiges Ziel, z.B. user@host:/pfad oder ein
# gemounteter Pfad. Leer = nur lokal (dann liegen Code+Daten auf EINER Platte).
REMOTE="${BACKUP_REMOTE:-}"

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
    "data/portfolio.db"
    "data/reflections.db"
    "data/bot_score.json"
    "data/sentiment_memory.json"
    "data/news_velocity.json"
    "data/reentry_watch.json"
    "data/short_positions.json"
    "data/tax.db"
    "data/dividends.db"
)

# Lern-Asset (Ziel 4): das über Monate aufgebaute, NICHT reproduzierbare Wissen.
# Fehlte bisher komplett im Backup (Lücken-Audit 5.7.2026) – Verlust wäre
# unwiederbringlich, da diese Daten aus echten Analysen/Outcomes entstehen.
LEARNING=(
    "data/experience.db"          # gelabelte Trades (Selbstlern-Fundament)
    "data/decision_log.db"        # jede Strategie-Entscheidung + Grund
    "data/calibration.json"       # gelernte Kalibrierungstabellen
    "data/paper_forward.json"     # Paper-Forward-Bilanz je Strategie
    "data/strategy_registry.json" # strategy_lab-Registry
    "data/strategy_state.json"
    "data/strategy_state.db"
    "data/analysis_log.db"        # Roh-Analysen (Quelle für Backfill-Labeling)
    "data/rl_weights.json"        # gelernte Gewichte
    # Nachgezogen 22.7. (Umzugsvorbereitung, Roadmap 6.1) – seit 12./14.7. neu
    # entstandenes, nicht reproduzierbares Wissen, fehlte bisher im Backup:
    "data/prompt_archive.db"      # KI-Prompt-Archiv (1.4d, Basis für Replay 4.5)
    "data/order_log.db"           # Order-Lifecycle-Log (1.5f)
    "data/thesis_registry.json"   # These-Verdikte PENDING/PROVEN/ABANDONED (6.10)
    "data/holdout_access.json"    # Anti-Overfit-Holdout-Zugriffslog (6.4b)
    "data/calibration_monitor.json"  # Kalibrierungs-Monitor-Snapshot-Verlauf (1.2)
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
for f in "${LEARNING[@]}";  do check_and_add "LERN"     "$f"; done
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

# ── Off-Server-Kopie (optional) ────────────────────────────────────────────────
# Ohne zweites Ziel liegen Code UND Daten auf derselben Platte – ein Plattenausfall
# vernichtet dann beides. BACKUP_REMOTE setzen (rsync-Ziel), um das zu schließen.
if [[ -n "$REMOTE" ]]; then
    echo "Übertrage off-server nach: $REMOTE"
    if rsync -az --timeout=60 "$ARCHIVE_PATH" "$REMOTE/" 2>/dev/null; then
        echo "  ✓  Off-Server-Kopie erstellt."
    else
        echo "  ⚠  Off-Server-Übertragung FEHLGESCHLAGEN (lokales Archiv bleibt erhalten)."
    fi
    echo ""
else
    echo "ℹ  Kein Off-Server-Ziel gesetzt (BACKUP_REMOTE leer) – Backup nur lokal."
    echo ""
fi

# ── Rotation: nur die letzten $KEEP Archive behalten ───────────────────────────
if [[ "$KEEP" -gt 0 ]]; then
    mapfile -t OLD < <(ls -1t "$DEST_DIR"/aktien_backup_*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))")
    if [[ ${#OLD[@]} -gt 0 ]]; then
        echo "Rotation: entferne ${#OLD[@]} alte(s) Archiv(e) (behalte $KEEP):"
        for f in "${OLD[@]}"; do
            echo "  • $(basename "$f")"
            rm -f "$f"
        done
        echo ""
    fi
fi

echo "Auf den PC übertragen (Beispiel):"
echo "  scp $ARCHIVE_PATH user@mein-pc:~/Downloads/"
echo ""
echo "Wiederherstellen auf dem PC:"
echo "  bash scripts/restore.sh ~/Downloads/$ARCHIVE_NAME"
echo ""
