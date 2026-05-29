#!/bin/bash
export DISPLAY=:99

pkill -f java 2>/dev/null || true
sleep 3

# Fenster VOR dem Start merken
BEFORE=$(xdotool search --any --name "" 2>/dev/null | sort -n)

/opt/ibgateway/ibgateway &

echo "Warte bis IB Gateway ein Fenster öffnet (max 60s)..."
WIN=""
for i in $(seq 1 60); do
    AFTER=$(xdotool search --any --name "" 2>/dev/null | sort -n)
    for w in $(comm -13 <(echo "$BEFORE") <(echo "$AFTER") 2>/dev/null); do
        GEOM=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Geometry | awk '{print $2}')
        if [ -n "$GEOM" ]; then
            W=$(echo "$GEOM" | cut -dx -f1)
            H=$(echo "$GEOM" | cut -dx -f2)
            AREA=$((W * H))
            if [ "$AREA" -gt 200000 ]; then
                WIN=$w
                echo "Gateway-Fenster gefunden: $WIN (${W}x${H}) nach ${i}s"
                break 2
            fi
        fi
    done
    sleep 1
done

if [ -z "$WIN" ]; then
    echo "FEHLER: Kein grosses Fenster nach 60s. Alle Fenster:"
    xdotool search --any --name "" 2>/dev/null | while read w; do
        echo "  $w: $(xdotool getwindowgeometry $w 2>/dev/null | grep Geometry)"
    done
    exit 1
fi

echo "Warte 15s auf WebView-Ladezeit..."
sleep 15

scrot /tmp/ibgw_before.png 2>/dev/null || true

xdotool windowfocus --sync "$WIN"
sleep 1
xdotool windowraise "$WIN"
sleep 0.5

GEOM=$(xdotool getwindowgeometry "$WIN" | grep Geometry | awk '{print $2}')
W=$(echo "$GEOM" | cut -dx -f1)
H=$(echo "$GEOM" | cut -dx -f2)
echo "Fenstergrösse: ${W}x${H}"

# Mitte anklicken um OS-Fokus zu geben
xdotool mousemove --window "$WIN" $((W/2)) $((H/2)) click 1
sleep 1

# Tab navigiert zum ersten Formularfeld (Benutzername im WebView)
xdotool key Tab
sleep 0.5
xdotool key ctrl+a
xdotool type --clearmodifiers --delay 50 "stocksentimenttradingbot"
sleep 0.5

# Tab zum Passwortfeld
xdotool key Tab
sleep 0.5
xdotool type --clearmodifiers --delay 50 "narjAv-qixru3-b1whaj"
sleep 0.5

xdotool key Return
echo "Login abgeschickt um $(date)"

sleep 5
scrot /tmp/ibgw_after.png 2>/dev/null || true
echo "Screenshot: /tmp/ibgw_after.png"

sleep 25
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 noch nicht offen"
    echo "Laufende Java-Prozesse:"
    ps aux | grep java | grep -v grep | awk '{print $1, $11}'
fi
