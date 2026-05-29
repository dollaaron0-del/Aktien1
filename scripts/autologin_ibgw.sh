#!/bin/bash
export DISPLAY=:99

pkill -f java 2>/dev/null || true
sleep 3

/opt/ibgateway/ibgateway &

echo "Warte 35s auf vollstaendigen Gateway-Start (Splash + WebView)..."
sleep 35

# Größtes Fenster auf dem Display finden (Login-Fenster, nicht Splash)
WIN=""
BEST_AREA=0
for w in $(xdotool search --any --name "" 2>/dev/null); do
    GEOM=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Geometry | awk '{print $2}')
    if [ -n "$GEOM" ]; then
        W=$(echo "$GEOM" | cut -dx -f1)
        H=$(echo "$GEOM" | cut -dx -f2)
        AREA=$((W * H))
        echo "Fenster $w: ${W}x${H} (Flaeche: $AREA)"
        if [ "$AREA" -gt "$BEST_AREA" ]; then
            BEST_AREA=$AREA
            WIN=$w
        fi
    fi
done

if [ -z "$WIN" ] || [ "$BEST_AREA" -lt 50000 ]; then
    echo "FEHLER: Kein gueltiges Fenster gefunden (bestes: $BEST_AREA px)"
    echo "Laufende Java-Prozesse:"
    ps aux | grep java | grep -v grep
    exit 1
fi

echo "Verwende Fenster: $WIN (Flaeche: $BEST_AREA px)"
scrot /tmp/ibgw_before.png 2>/dev/null || true

xdotool windowfocus --sync "$WIN" 2>/dev/null || true
sleep 1
xdotool windowraise "$WIN" 2>/dev/null || true
sleep 0.5

GEOM=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Geometry | awk '{print $2}')
W=$(echo "$GEOM" | cut -dx -f1)
H=$(echo "$GEOM" | cut -dx -f2)
echo "Fenstergrösse: ${W}x${H}"

# Mitte anklicken um OS-Fokus zu geben
xdotool mousemove --window "$WIN" $((W/2)) $((H/2)) click 1 2>/dev/null || true
sleep 1

# Tab navigiert zum ersten Formularfeld (Benutzername im WebView)
xdotool key Tab 2>/dev/null || true
sleep 0.5
xdotool key ctrl+a 2>/dev/null || true
xdotool type --clearmodifiers --delay 50 "stocksentimenttradingbot"
sleep 0.5

# Tab zum Passwortfeld
xdotool key Tab 2>/dev/null || true
sleep 0.5
xdotool type --clearmodifiers --delay 50 "narjAv-qixru3-b1whaj"
sleep 0.5

xdotool key Return 2>/dev/null || true
echo "Login abgeschickt um $(date)"

sleep 5
scrot /tmp/ibgw_after.png 2>/dev/null || true
echo "Screenshot: /tmp/ibgw_after.png"

sleep 30
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 noch nicht offen"
    ps aux | grep java | grep -v grep | awk '{print "Java laeuft:", $1, $11}'
fi
