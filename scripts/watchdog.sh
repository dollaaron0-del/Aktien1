#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Watchdog für den Aktien-Bot
#
# Überwacht den Bot-Prozess und startet ihn bei Absturz automatisch neu.
# Sendet optionale Telegram-Benachrichtigungen bei Neustart und Fehler.
#
# Verwendung:
#   chmod +x scripts/watchdog.sh
#   ./scripts/watchdog.sh            # Im Vordergrund (Entwicklung)
#   nohup ./scripts/watchdog.sh &    # Im Hintergrund
#
# Oder als systemd-Service: siehe scripts/aktien_bot.service
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Konfiguration ─────────────────────────────────────────────────────────────
BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BOT_CMD="python main.py"
LOG_FILE="$BOT_DIR/logs/watchdog.log"
PID_FILE="$BOT_DIR/logs/bot.pid"
MAX_RESTARTS=10          # Nach X Neustarts: 1 Stunde Pause
RESTART_WINDOW=3600      # Zeitfenster in Sekunden für MAX_RESTARTS
RESTART_DELAY=10         # Sekunden warten vor Neustart
CRASH_THRESHOLD=30       # Prozess der weniger als X Sekunden lief = Crash

# Telegram (aus .env lesen falls vorhanden)
if [[ -f "$BOT_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$BOT_DIR/.env"; set +a
fi
TG_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TG_CHAT="${TELEGRAM_CHAT_ID:-}"

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

send_telegram() {
    local msg="$1"
    if [[ -n "$TG_TOKEN" && -n "$TG_CHAT" ]]; then
        curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d "chat_id=${TG_CHAT}" \
            -d "text=${msg}" \
            -d "parse_mode=HTML" \
            --max-time 10 > /dev/null 2>&1 || true
    fi
}

cleanup() {
    log "Watchdog beendet."
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
}

# ── Setup ─────────────────────────────────────────────────────────────────────
mkdir -p "$BOT_DIR/logs"
trap cleanup EXIT INT TERM

log "═══════════════════════════════════════════"
log "Aktien-Bot Watchdog gestartet"
log "Bot-Verzeichnis: $BOT_DIR"
log "Kommando: $BOT_CMD"
log "═══════════════════════════════════════════"

cd "$BOT_DIR"

# ── Neustart-Tracking ─────────────────────────────────────────────────────────
restart_times=()

# ── Haupt-Schleife ────────────────────────────────────────────────────────────
while true; do
    start_ts=$(date +%s)

    log "Bot wird gestartet..."
    $BOT_CMD >> "$BOT_DIR/logs/bot.log" 2>&1 &
    bot_pid=$!
    echo "$bot_pid" > "$PID_FILE"
    log "Bot PID: $bot_pid"

    # Warten bis der Bot-Prozess endet
    wait "$bot_pid" || true
    exit_code=$?
    end_ts=$(date +%s)
    runtime=$((end_ts - start_ts))

    log "Bot beendet (Exit-Code: $exit_code, Laufzeit: ${runtime}s)"
    rm -f "$PID_FILE"

    # ── Neustart-Rate prüfen ──────────────────────────────────────────────────
    now=$(date +%s)
    restart_times+=("$now")

    # Einträge außerhalb des Zeitfensters entfernen
    cutoff=$((now - RESTART_WINDOW))
    new_times=()
    for t in "${restart_times[@]}"; do
        if [[ $t -gt $cutoff ]]; then
            new_times+=("$t")
        fi
    done
    restart_times=("${new_times[@]+"${new_times[@]}"}")

    if [[ ${#restart_times[@]} -ge $MAX_RESTARTS ]]; then
        msg="⚠️ Aktien-Bot: $MAX_RESTARTS Abstürze in ${RESTART_WINDOW}s – Watchdog pausiert 1 Stunde!"
        log "$msg"
        send_telegram "$msg"
        sleep 3600
        restart_times=()
        continue
    fi

    # ── Neustart-Entscheidung ─────────────────────────────────────────────────
    if [[ $runtime -lt $CRASH_THRESHOLD ]]; then
        msg="🔴 Aktien-Bot abgestürzt (Exit: $exit_code, Laufzeit: ${runtime}s) – Neustart in ${RESTART_DELAY}s"
        log "$msg"
        send_telegram "$msg"
    else
        msg="🟡 Aktien-Bot beendet (Exit: $exit_code, nach ${runtime}s) – Neustart in ${RESTART_DELAY}s"
        log "$msg"
        send_telegram "$msg"
    fi

    sleep "$RESTART_DELAY"

    send_telegram "🟢 Aktien-Bot wird neu gestartet (Versuch ${#restart_times[@]}/$MAX_RESTARTS)"
done
