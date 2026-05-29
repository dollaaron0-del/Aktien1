#!/bin/bash
set -e
export DISPLAY=:99

pkill -f java 2>/dev/null || true
sleep 3

/opt/ibgateway/ibgateway &

echo "Warte auf IB Gateway Fenster..."
for i in $(seq 1 40); do
    WIN=$(xdotool search --name "IB Gateway" 2>/dev/null | tail -1)
    [ -n "$WIN" ] && break
    sleep 1
done

[ -z "$WIN" ] && { echo "FEHLER: Fenster nicht gefunden nach 40s"; exit 1; }
echo "Fenster gefunden: $WIN — warte 12s auf WebView..."
sleep 12

xdotool windowfocus --sync "$WIN"
sleep 1
xdotool windowraise "$WIN"
sleep 0.5

GEOM=$(xdotool getwindowgeometry "$WIN" | grep Geometry | awk '{print $2}')
W=$(echo "$GEOM" | cut -dx -f1)
H=$(echo "$GEOM" | cut -dx -f2)
echo "Fenstergröße: ${W}x${H}"

# Mitte anklicken um Fokus zu setzen
xdotool mousemove --window "$WIN" $((W/2)) $((H/2)) click 1
sleep 1

# Tab navigiert zum ersten Formularfeld (Benutzername)
xdotool key Tab
sleep 0.5

# Feld leeren und Benutzername eingeben
xdotool key ctrl+a
xdotool type --clearmodifiers --delay 50 "stocksentimenttradingbot"
sleep 0.5

# Tab zum Passwortfeld
xdotool key Tab
sleep 0.5

xdotool type --clearmodifiers --delay 50 "narjAv-qixru3-b1whaj"
sleep 0.5

# Absenden
xdotool key Return
echo "Login abgeschickt um $(date)"

sleep 5
scrot /tmp/ibgw_after.png 2>/dev/null || true

sleep 20
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 noch nicht offen nach 25s"
fi
