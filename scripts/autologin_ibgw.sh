#!/bin/bash
export DISPLAY=:99

pkill -f java 2>/dev/null || true
sleep 3

/opt/ibgateway/ibgateway &

echo "Warte 35s auf vollstaendigen Gateway-Start (Splash + WebView)..."
sleep 35

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

# Fensterposition fuer absolute Koordinaten bestimmen
WIN_POS=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Position | awk '{print $2}')
WIN_X=$(echo "$WIN_POS" | cut -d, -f1)
WIN_Y=$(echo "$WIN_POS" | cut -d, -f2)
GEOM=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Geometry | awk '{print $2}')
W=$(echo "$GEOM" | cut -dx -f1)
H=$(echo "$GEOM" | cut -dx -f2)
echo "Fenster-Position: (${WIN_X}, ${WIN_Y}), Groesse: ${W}x${H}"

scrot /tmp/ibgw_before.png 2>/dev/null || true

xdotool windowfocus --sync "$WIN" 2>/dev/null || true
sleep 1
xdotool windowraise "$WIN" 2>/dev/null || true
sleep 0.5

# ABSOLUTE Koordinaten berechnen (XTEST simuliert echte Hardware-Eingabe)
# GSTAT-Panel belegt obere ~190px - Login-Formular ist darunter
# Benutzernamefeld: ca. 48% von oben (y~293 relativ, wie Original-autologin y=311)
USER_ABS_X=$((WIN_X + W / 2))
USER_ABS_Y=$((WIN_Y + H * 48 / 100))
# Passwortfeld: ca. 60% von oben (y~366 relativ)
PASS_ABS_Y=$((WIN_Y + H * 60 / 100))

# Erster Klick: Fenster aktivieren (neutraler Bereich oben)
NEUTRAL_Y=$((WIN_Y + 30))
xdotool mousemove "$USER_ABS_X" "$NEUTRAL_Y"
sleep 0.3
xdotool click 1
sleep 0.5

# Zweiter Klick: Benutzernamefeld fokussieren (JavaFX braucht oft 2 Klicks)
echo "Klicke Benutzernamefeld (absolut): (${USER_ABS_X}, ${USER_ABS_Y})"
xdotool mousemove "$USER_ABS_X" "$USER_ABS_Y"
sleep 0.3
xdotool click 1
sleep 0.5
xdotool click 1
sleep 0.8
xdotool type --clearmodifiers --delay 80 "stocksentimenttradingbot"
sleep 0.5

echo "Klicke Passwortfeld (absolut): (${USER_ABS_X}, ${PASS_ABS_Y})"
xdotool mousemove "$USER_ABS_X" "$PASS_ABS_Y"
sleep 0.3
xdotool click 1
sleep 0.5
xdotool click 1
sleep 0.8
xdotool type --clearmodifiers --delay 80 "narjAv-qixru3-b1whaj"
sleep 0.5

xdotool key Return
echo "Login abgeschickt um $(date)"

sleep 5
scrot /tmp/ibgw_after.png 2>/dev/null || true

# After-Screenshot sofort als ASCII ausgeben
echo "=== AFTER Screenshot ASCII ==="
/opt/Aktien/venv/bin/python3 -c "
from PIL import Image
img = Image.open('/tmp/ibgw_after.png').convert('L').resize((100, 20))
chars = ' .:-=+*#%@'
for y in range(img.height):
    print(''.join(chars[int(img.getpixel((x,y))*(len(chars)-1)/255)] for x in range(img.width)))
" 2>/dev/null || true

sleep 55
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 nach 60s noch nicht offen"
    ps aux | grep java | grep -v grep | awk '{print "Java laeuft:", $1, $11}'
fi
