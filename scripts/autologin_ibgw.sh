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
scrot /tmp/ibgw_before.png 2>/dev/null || true

xdotool windowfocus --sync "$WIN" 2>/dev/null || true
sleep 1
xdotool windowraise "$WIN" 2>/dev/null || true
sleep 0.5

GEOM=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Geometry | awk '{print $2}')
W=$(echo "$GEOM" | cut -dx -f1)
H=$(echo "$GEOM" | cut -dx -f2)
echo "Fenstergrösse: ${W}x${H}"

# --- Direkte Koordinaten fuer das Login-Formular ---
# ASCII-Art zeigt Form-Elemente in Zeilen 7-11 (obere 31% = y=122-191px)
# Benutzernamefeld: ca. 23% von oben (bei 610px: y=140)
USER_Y=$((H * 23 / 100))
# Passwortfeld: ca. 34% von oben (bei 610px: y=207)
PASS_Y=$((H * 34 / 100))
CENTER_X=$((W / 2))

echo "Klicke Benutzernamefeld bei (${CENTER_X}, ${USER_Y})"
xdotool mousemove --window "$WIN" "$CENTER_X" "$USER_Y" click 3 2>/dev/null || true
sleep 0.8
xdotool type --clearmodifiers --delay 50 "stocksentimenttradingbot"
sleep 0.5

echo "Klicke Passwortfeld bei (${CENTER_X}, ${PASS_Y})"
xdotool mousemove --window "$WIN" "$CENTER_X" "$PASS_Y" click 3 2>/dev/null || true
sleep 0.8
xdotool type --clearmodifiers --delay 50 "narjAv-qixru3-b1whaj"
sleep 0.5

xdotool key Return 2>/dev/null || true
echo "Login abgeschickt um $(date)"

sleep 5
scrot /tmp/ibgw_after.png 2>/dev/null || true
echo "Screenshot: /tmp/ibgw_after.png"

# Persistenter HTTP-Server (ueberlebt Script-Ende)
pkill -f "python3 -m http.server 9092" 2>/dev/null || true
nohup python3 -m http.server 9092 --directory /tmp > /tmp/http_server.log 2>&1 &
disown
echo "=== Screenshots abrufbar ==="
echo "  Vorher: http://161.97.166.88:9092/ibgw_before.png"
echo "  Nachher: http://161.97.166.88:9092/ibgw_after.png"

sleep 60
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 nach 65s noch nicht offen"
    ps aux | grep java | grep -v grep | awk '{print "Java laeuft:", $1, $11}'
fi
