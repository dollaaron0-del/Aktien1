#!/bin/bash
export DISPLAY=:99

pkill -f java 2>/dev/null || true
sleep 3

/opt/ibgateway/ibgateway &

echo "Warte 45s auf vollstaendigen Gateway-Start..."
sleep 45

# Fenster finden
WIN=""
BEST_AREA=0
for w in $(xdotool search --any --name "" 2>/dev/null); do
    GEOM=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Geometry | awk '{print $2}')
    if [ -n "$GEOM" ]; then
        W=$(echo "$GEOM" | cut -dx -f1)
        H=$(echo "$GEOM" | cut -dx -f2)
        AREA=$((W * H))
        if [ "$AREA" -gt "$BEST_AREA" ] && [ "$AREA" -lt 700000 ] && [ "$AREA" -gt 100000 ]; then
            BEST_AREA=$AREA
            WIN=$w
        fi
    fi
done
[ -z "$WIN" ] && echo "FEHLER: Kein Fenster" && exit 1

WIN_POS=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Position | awk '{print $2}')
WIN_X=$(echo "$WIN_POS" | cut -d, -f1)
WIN_Y=$(echo "$WIN_POS" | cut -d, -f2)
GEOM=$(xdotool getwindowgeometry "$WIN" 2>/dev/null | grep Geometry | awk '{print $2}')
W=$(echo "$GEOM" | cut -dx -f1)
H=$(echo "$GEOM" | cut -dx -f2)
echo "Fenster: $WIN @ (${WIN_X},${WIN_Y}) ${W}x${H}"

scrot /tmp/ibgw_scan.png 2>/dev/null || true

# Schritt 1: Vollbild-ASCII des ganzen Fensters (100x60 fuer bessere Aufloesung)
echo "=== Vollbild-Fenster 100x60 ==="
/opt/Aktien/venv/bin/python3 - /tmp/ibgw_scan.png "$WIN_X" "$WIN_Y" "$W" "$H" <<'PYEOF'
import sys
from PIL import Image
f, wx, wy, ww, wh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
chars = ' .:-=+*#%@'
img = Image.open(f)
win = img.crop((wx, wy, wx+ww, wy+wh)).convert('L').resize((100, 60))
for y in range(win.height):
    print(''.join(chars[int(win.getpixel((x,y))*(len(chars)-1)/255)] for x in range(win.width)))
PYEOF

# Schritt 2: Vertikal-Helligkeits-Scan (findet Eingabefelder)
echo "=== Vertikal-Helligkeits-Scan (Mitte des Fensters) ==="
SCAN_OUTPUT=$(/opt/Aktien/venv/bin/python3 - /tmp/ibgw_scan.png "$WIN_X" "$WIN_Y" "$W" "$H" <<'PYEOF'
import sys
from PIL import Image
f, wx, wy, ww, wh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
img = Image.open(f)
win = img.crop((wx, wy, wx+ww, wy+wh)).convert('L')
cx = ww // 2

# Helligkeit jede 5px
avgs = []
for y in range(0, wh, 5):
    row = [win.getpixel((cx + dx, y)) for dx in range(-60, 61, 6)]
    avgs.append((y, sum(row) // len(row)))

# Ausgabe als Balkendiagramm
for y, avg in avgs:
    bar = '#' * (avg // 8)
    pct = y * 100 // wh
    print(f"y={y:3d}({pct:2d}%): {avg:3d} {bar}")

# Felder-Erkennung: Bereiche heller als Hintergrund
bg = sorted(a for _,a in avgs)[len(avgs)//5]  # 20. Perzentile = Hintergrund
threshold = bg + 20
print(f"\nHintergrund-Helligkeit: {bg}/255, Schwellwert: {threshold}/255")
fields = []
in_field = False
for y, avg in avgs:
    if avg > threshold and not in_field:
        in_field = True; start = y
    elif avg <= threshold and in_field:
        in_field = False
        if y - start >= 10:
            mid = (start + y) // 2
            fields.append(mid)
            print(f"FELD_ERKANNT: y={start}-{y}, Mitte={mid} ({mid*100//wh}%)")
if not fields:
    print("KEIN_FELD_GEFUNDEN")
PYEOF
)
echo "$SCAN_OUTPUT"

# Koordinaten aus Scan-Ergebnis extrahieren
USER_REL_Y=""
PASS_REL_Y=""
while IFS= read -r line; do
    if [[ "$line" == FELD_ERKANNT:* ]]; then
        MID=$(echo "$line" | grep -oP 'Mitte=\K[0-9]+')
        if [ -z "$USER_REL_Y" ]; then
            USER_REL_Y="$MID"
        elif [ -z "$PASS_REL_Y" ]; then
            PASS_REL_Y="$MID"
        fi
    fi
done <<< "$SCAN_OUTPUT"

# Fallback wenn keine Felder erkannt
[ -z "$USER_REL_Y" ] && USER_REL_Y=$((H * 35 / 100)) && echo "Fallback USERNAME y=35%"
[ -z "$PASS_REL_Y" ] && PASS_REL_Y=$((H * 47 / 100)) && echo "Fallback PASSWORD y=47%"

USER_ABS_X=$((WIN_X + W / 2))
USER_ABS_Y=$((WIN_Y + USER_REL_Y))
PASS_ABS_Y=$((WIN_Y + PASS_REL_Y))
echo "Finale Koordinaten: Username (${USER_ABS_X}, ${USER_ABS_Y}) = ${USER_REL_Y}px, Passwort (${USER_ABS_X}, ${PASS_ABS_Y}) = ${PASS_REL_Y}px"

# ---- Login ----
xdotool windowraise "$WIN" 2>/dev/null || true
sleep 0.5
xdotool windowfocus --sync "$WIN" 2>/dev/null || true
sleep 0.5

# Neutraler Klick
xdotool mousemove "$USER_ABS_X" "$((WIN_Y + 20))"
sleep 0.2; xdotool click 1; sleep 0.8

# Benutzernamefeld
echo "Klicke Username: (${USER_ABS_X}, ${USER_ABS_Y})"
xdotool mousemove "$USER_ABS_X" "$USER_ABS_Y"; sleep 0.3
xdotool click 1; sleep 0.8; xdotool click 1; sleep 1.0
xdotool windowfocus --sync "$WIN" 2>/dev/null || true; sleep 0.3
echo "Fokus: $(xdotool getwindowfocus 2>/dev/null) (erwartet: $WIN)"

scrot /tmp/ibgw_before_type.png 2>/dev/null || true
xdotool type --clearmodifiers --delay 150 "stocksentimenttradingbot"
sleep 0.8
scrot /tmp/ibgw_after_type.png 2>/dev/null || true

# Pixel-Vergleich: hat sich was geaendert?
echo "=== Pixel-Aenderung nach Eingabe ==="
/opt/Aktien/venv/bin/python3 - /tmp/ibgw_before_type.png /tmp/ibgw_after_type.png "$WIN_X" "$WIN_Y" "$W" "$H" "$USER_REL_Y" <<'PYEOF'
import sys
from PIL import Image
f1, f2 = sys.argv[1], sys.argv[2]
wx, wy, ww, wh, cy = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
b = Image.open(f1).crop((wx, wy, wx+ww, wy+wh)).convert('L')
a = Image.open(f2).crop((wx, wy, wx+ww, wy+wh)).convert('L')
diffs = sum(abs(a.getpixel((x, cy)) - b.getpixel((x, cy))) for x in range(ww))
print(f"Pixel-Differenz bei y={cy}: {diffs} (>100 = Text sichtbar, ~0 = nichts geaendert)")
PYEOF

xdotool key --clearmodifiers Tab; sleep 2.0

# Passwortfeld
echo "Klicke Passwort: (${USER_ABS_X}, ${PASS_ABS_Y})"
xdotool mousemove "$USER_ABS_X" "$PASS_ABS_Y"; sleep 0.3
xdotool click 1; sleep 0.8; xdotool click 1; sleep 1.0
xdotool windowfocus --sync "$WIN" 2>/dev/null || true; sleep 0.3
xdotool type --clearmodifiers --delay 150 "narjAv-qixru3-b1whaj"; sleep 0.8

xdotool windowfocus --sync "$WIN" 2>/dev/null || true
xdotool key Return
echo "Login abgeschickt um $(date)"
sleep 5

scrot /tmp/ibgw_after_login.png 2>/dev/null || true
echo "=== Nach Login-Submit ==="
/opt/Aktien/venv/bin/python3 - /tmp/ibgw_after_login.png "$WIN_X" "$WIN_Y" "$W" "$H" <<'PYEOF'
import sys
from PIL import Image
f, wx, wy, ww, wh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
chars = ' .:-=+*#%@'
img = Image.open(f)
win = img.crop((wx, wy, wx+ww, wy+wh)).convert('L').resize((100, 30))
for y in range(win.height):
    print(''.join(chars[int(win.getpixel((x,y))*(len(chars)-1)/255)] for x in range(win.width)))
PYEOF

sleep 55
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 nach 60s noch nicht offen"
    ps aux | grep java | grep -v grep | awk '{print "Java laeuft:", $1, $11}'
fi
