#!/bin/bash
export DISPLAY=:99

pkill -f java 2>/dev/null || true
sleep 3

/opt/ibgateway/ibgateway &

echo "Warte 45s auf vollstaendigen Gateway-Start (Splash + WebView)..."
sleep 45

# Alle Fenster auflisten inkl. Titel und Position
echo "=== Alle Fenster (wmctrl) ==="
wmctrl -l -G 2>/dev/null || echo "(wmctrl nicht verfuegbar)"

# Fenster finden (nicht Xvfb Root > 700k px)
declare -a WINS=()
for w in $(xdotool search --any --name "" 2>/dev/null); do
    GEOM=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Geometry | awk '{print $2}')
    if [ -n "$GEOM" ]; then
        W=$(echo "$GEOM" | cut -dx -f1)
        H=$(echo "$GEOM" | cut -dx -f2)
        AREA=$((W * H))
        POS=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Position | awk '{print $2}')
        echo "Fenster $w: ${W}x${H} (${AREA}px) @ $POS"
        if [ "$AREA" -gt 100000 ] && [ "$AREA" -lt 700000 ]; then
            WINS+=("$w")
        fi
    fi
done

if [ ${#WINS[@]} -eq 0 ]; then
    echo "FEHLER: Kein gueltiges Fenster gefunden"
    ps aux | grep java | grep -v grep
    exit 1
fi

# Fenster mit aktuellem X11-Fokus bevorzugen, sonst niedrigste ID (urspruengliches Fenster)
FOCUS_WIN=$(xdotool getwindowfocus 2>/dev/null)
echo "Aktueller X11-Fokus: $FOCUS_WIN"

WIN=""
for w in "${WINS[@]}"; do
    [ "$w" = "$FOCUS_WIN" ] && WIN="$w" && break
done
if [ -z "$WIN" ]; then
    # Niedrigste ID = erstes (aeltestes) Fenster
    WIN="${WINS[0]}"
    for w in "${WINS[@]}"; do
        [ "$w" -lt "$WIN" ] && WIN="$w"
    done
fi
echo "Verwende Fenster: $WIN"

WIN_POS=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Position | awk '{print $2}')
WIN_X=$(echo "$WIN_POS" | cut -d, -f1)
WIN_Y=$(echo "$WIN_POS" | cut -d, -f2)
GEOM=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Geometry | awk '{print $2}')
W=$(echo "$GEOM" | cut -dx -f1)
H=$(echo "$GEOM" | cut -dx -f2)
echo "Fenster-Position: (${WIN_X}, ${WIN_Y}), Groesse: ${W}x${H}"

# Screenshot + ASCII nur des Fensterbereichs + Pixel-Inspektion an bestimmten y-Positionen
inspect_shot() {
    local f=$1 label=$2 y1=$3 y2=$4
    scrot "$f" 2>/dev/null || true
    echo "=== $label ==="
    /opt/Aktien/venv/bin/python3 - "$f" "$WIN_X" "$WIN_Y" "$W" "$H" "$y1" "$y2" <<'PYEOF'
import sys
from PIL import Image
f = sys.argv[1]
wx, wy, ww, wh = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
y1, y2 = int(sys.argv[6]), int(sys.argv[7])
chars = ' .:-=+*#%@'
try:
    img = Image.open(f)
    win = img.crop((wx, wy, wx+ww, wy+wh)).convert('L')
    # Zeige Bereich y1-y2 im Fenster (Zeilen um die Klickposition)
    area = win.crop((0, max(0, y1-40), ww, min(wh, y2+40))).resize((100, 20))
    for y in range(area.height):
        print(''.join(chars[int(area.getpixel((x,y))*(len(chars)-1)/255)] for x in range(area.width)))
    # Pixel-Werte in der Mitte bei y1 und y2
    cx = ww // 2
    for py in [y1, y2]:
        if 0 <= py < wh:
            vals = [win.getpixel((cx+dx, py)) for dx in range(-20, 21, 4)]
            avg = sum(vals) // len(vals)
            print(f"  Pixel y={py}: avg={avg}/255 ({['schwarz','dunkel','grau','hell','weiss'][min(4,avg*5//256)]})")
except Exception as e:
    print(f'Fehler: {e}')
PYEOF
}

# Ausgangszustand + Pixel-Check bei geplanten Klick-Positionen
# Neue Koordinaten: 55%/67% statt 48%/60% (GSTAT-Panel reicht weiter nach unten)
USER_REL_Y=$((H * 55 / 100))
PASS_REL_Y=$((H * 67 / 100))
USER_ABS_X=$((WIN_X + W / 2))
USER_ABS_Y=$((WIN_Y + USER_REL_Y))
PASS_ABS_Y=$((WIN_Y + PASS_REL_Y))

echo "Neue Koordinaten: Username y=${USER_REL_Y} (${USER_ABS_Y} abs, 55%), Passwort y=${PASS_REL_Y} (${PASS_ABS_Y} abs, 67%)"
inspect_shot /tmp/ibgw_step0.png "SCHRITT 0: Ausgangszustand (Pixel bei 55%/67%)" "$USER_REL_Y" "$PASS_REL_Y"

# Fenster in Vordergrund bringen und fokussieren
xdotool windowraise "$WIN" 2>/dev/null || true
sleep 0.5
xdotool windowfocus --sync "$WIN" 2>/dev/null || true
sleep 0.5
echo "X11-Fokus nach windowfocus: $(xdotool getwindowfocus 2>/dev/null) (erwartet: $WIN)"

# Neutraler Klick oben
xdotool mousemove "$USER_ABS_X" "$((WIN_Y + 30))"
sleep 0.2
xdotool click 1
sleep 0.8

# ---- Benutzernamefeld ----
echo "Klicke Username bei y=55% (${USER_ABS_X}, ${USER_ABS_Y})"
xdotool windowraise "$WIN" 2>/dev/null || true
xdotool mousemove "$USER_ABS_X" "$USER_ABS_Y"
sleep 0.3
xdotool click 1
sleep 0.8
xdotool click 1
sleep 1.0
xdotool windowfocus --sync "$WIN" 2>/dev/null || true
sleep 0.3
echo "X11-Fokus vor Username-Eingabe: $(xdotool getwindowfocus 2>/dev/null) (erwartet: $WIN)"

inspect_shot /tmp/ibgw_step2.png "SCHRITT 2: Nach Username-Klick (Cursor?)" "$USER_REL_Y" "$PASS_REL_Y"

xdotool type --clearmodifiers --delay 150 "stocksentimenttradingbot"
sleep 1.0

inspect_shot /tmp/ibgw_step3.png "SCHRITT 3: Nach Username-Eingabe (Text?)" "$USER_REL_Y" "$PASS_REL_Y"

xdotool key --clearmodifiers Tab
sleep 2.0

# ---- Passwortfeld ----
echo "Klicke Passwort bei y=67% (${USER_ABS_X}, ${PASS_ABS_Y})"
xdotool windowraise "$WIN" 2>/dev/null || true
xdotool mousemove "$USER_ABS_X" "$PASS_ABS_Y"
sleep 0.3
xdotool click 1
sleep 0.8
xdotool click 1
sleep 1.0
xdotool windowfocus --sync "$WIN" 2>/dev/null || true
sleep 0.3
echo "X11-Fokus vor Passwort-Eingabe: $(xdotool getwindowfocus 2>/dev/null) (erwartet: $WIN)"

xdotool type --clearmodifiers --delay 150 "narjAv-qixru3-b1whaj"
sleep 1.0

inspect_shot /tmp/ibgw_step5.png "SCHRITT 5: Nach Passwort-Eingabe" "$USER_REL_Y" "$PASS_REL_Y"

xdotool windowfocus --sync "$WIN" 2>/dev/null || true
xdotool key Return
echo "Login abgeschickt um $(date)"
sleep 5

inspect_shot /tmp/ibgw_step6.png "SCHRITT 6: Nach Submit" "$USER_REL_Y" "$PASS_REL_Y"

sleep 55
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 nach 60s noch nicht offen"
    inspect_shot /tmp/ibgw_step7.png "SCHRITT 7: Endzustand" "$USER_REL_Y" "$PASS_REL_Y"
    ps aux | grep java | grep -v grep | awk '{print "Java laeuft:", $1, $11}'
fi
