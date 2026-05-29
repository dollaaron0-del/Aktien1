#!/bin/bash
export DISPLAY=:99

pkill -f java 2>/dev/null || true
sleep 3

/opt/ibgateway/ibgateway &

echo "Warte 45s auf vollstaendigen Gateway-Start (Splash + WebView)..."
sleep 45

# Groesstes Fenster finden (nicht Xvfb Root > 700k px)
WIN=""
BEST_AREA=0
for w in $(xdotool search --any --name "" 2>/dev/null); do
    GEOM=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Geometry | awk '{print $2}')
    if [ -n "$GEOM" ]; then
        W=$(echo "$GEOM" | cut -dx -f1)
        H=$(echo "$GEOM" | cut -dx -f2)
        AREA=$((W * H))
        echo "Fenster $w: ${W}x${H} (Flaeche: $AREA)"
        if [ "$AREA" -gt "$BEST_AREA" ] && [ "$AREA" -lt 700000 ] && [ "$AREA" -gt 100000 ]; then
            BEST_AREA=$AREA
            WIN=$w
        fi
    fi
done

if [ -z "$WIN" ] || [ "$BEST_AREA" -lt 50000 ]; then
    echo "FEHLER: Kein gueltiges Fenster gefunden"
    ps aux | grep java | grep -v grep
    exit 1
fi

echo "Verwende Fenster: $WIN (${BEST_AREA} px)"

WIN_POS=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Position | awk '{print $2}')
WIN_X=$(echo "$WIN_POS" | cut -d, -f1)
WIN_Y=$(echo "$WIN_POS" | cut -d, -f2)
GEOM=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Geometry | awk '{print $2}')
W=$(echo "$GEOM" | cut -dx -f1)
H=$(echo "$GEOM" | cut -dx -f2)
echo "Fenster-Position: (${WIN_X}, ${WIN_Y}), Groesse: ${W}x${H}"

# Screenshot: Nur das Fenster, 100x50 ASCII (bessere Aufloesung)
ascii_win() {
    local f=$1 label=$2
    scrot "$f" 2>/dev/null || true
    echo "=== $label ==="
    /opt/Aktien/venv/bin/python3 - "$f" "$WIN_X" "$WIN_Y" "$W" "$H" <<'PYEOF'
import sys
from PIL import Image
f, wx, wy, ww, wh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
chars = ' .:-=+*#%@'
try:
    img = Image.open(f)
    # Nur Fensterbereich, keine Xvfb-Umgebung
    win = img.crop((wx, wy, wx+ww, wy+wh)).convert('L').resize((100, 50))
    for y in range(win.height):
        print(''.join(chars[int(win.getpixel((x,y))*(len(chars)-1)/255)] for x in range(win.width)))
except Exception as e:
    print(f'PIL-Fehler: {e}')
PYEOF
}

ascii_win /tmp/ibgw_step0.png "SCHRITT 0: Ausgangszustand (Fenster 100x50)"

# Absolute Koordinaten (XTEST)
# GSTAT-Panel belegt obere ~190px, Login-Formular darunter
USER_ABS_X=$((WIN_X + W / 2))
USER_ABS_Y=$((WIN_Y + H * 48 / 100))
PASS_ABS_Y=$((WIN_Y + H * 60 / 100))

echo "Benutzername-Feld: (${USER_ABS_X}, ${USER_ABS_Y}) = relativ ($(( W/2 )), $(( H * 48 / 100 ))) im Fenster"
echo "Passwort-Feld:     (${USER_ABS_X}, ${PASS_ABS_Y}) = relativ ($(( W/2 )), $(( H * 60 / 100 ))) im Fenster"

# ---- Schritt 1: Fenster aktivieren ----
xdotool windowactivate --sync "$WIN" 2>/dev/null || true
sleep 0.5
xdotool windowraise "$WIN" 2>/dev/null || true
sleep 0.5

NEUTRAL_Y=$((WIN_Y + 30))
xdotool mousemove "$USER_ABS_X" "$NEUTRAL_Y"
sleep 0.3
xdotool click 1
sleep 1.0
echo "Aktives Fenster nach Neutral-Klick: $(xdotool getactivewindow 2>/dev/null) (erwartet: $WIN)"

ascii_win /tmp/ibgw_step1.png "SCHRITT 1: Nach Fenster-Aktivierung"

# ---- Schritt 2: Benutzernamefeld ----
echo "Klicke Benutzernamefeld: (${USER_ABS_X}, ${USER_ABS_Y})"
xdotool mousemove "$USER_ABS_X" "$USER_ABS_Y"
sleep 0.3
xdotool click 1
sleep 1.0
xdotool click 1
sleep 1.5
echo "Aktives Fenster nach Username-Klick: $(xdotool getactivewindow 2>/dev/null)"

ascii_win /tmp/ibgw_step2.png "SCHRITT 2: Nach Benutzername-Fokus (Cursor?"

xdotool type --clearmodifiers --delay 120 "stocksentimenttradingbot"
sleep 1.0

ascii_win /tmp/ibgw_step3.png "SCHRITT 3: Nach Benutzername-Eingabe (Text?"

# Tab: bei zweistufigem Login => Weiter-Button; bei einstufigem => Passwortfeld
xdotool key --clearmodifiers Tab
sleep 3.0

ascii_win /tmp/ibgw_step4.png "SCHRITT 4: Nach Tab (Seite gewechselt?)"

# ---- Schritt 3: Passwortfeld (Klick UND Tab-Navigation) ----
echo "Klicke Passwortfeld: (${USER_ABS_X}, ${PASS_ABS_Y})"
xdotool mousemove "$USER_ABS_X" "$PASS_ABS_Y"
sleep 0.3
xdotool click 1
sleep 1.0
xdotool click 1
sleep 1.5
echo "Aktives Fenster nach Passwort-Klick: $(xdotool getactivewindow 2>/dev/null)"

ascii_win /tmp/ibgw_step5.png "SCHRITT 5: Nach Passwort-Fokus"

xdotool type --clearmodifiers --delay 120 "narjAv-qixru3-b1whaj"
sleep 1.0

ascii_win /tmp/ibgw_step6.png "SCHRITT 6: Nach Passwort-Eingabe"

xdotool key Return
echo "Login abgeschickt um $(date)"
sleep 5

ascii_win /tmp/ibgw_step7.png "SCHRITT 7: Nach Login-Submit"

sleep 55
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 nach 60s noch nicht offen"
    ascii_win /tmp/ibgw_step8.png "SCHRITT 8: Endzustand"
    ps aux | grep java | grep -v grep | awk '{print "Java laeuft:", $1, $11}'
fi
