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

# Natuerlichen X11-Fokus VOR jeder Interaktion merken
NATURAL_FOCUS=$(xdotool getwindowfocus 2>/dev/null)
echo "Natuerlicher X11-Fokus (JavaFX Eingabe-Handler): $NATURAL_FOCUS"

scrot /tmp/ibgw_init.png 2>/dev/null || true

# Vollbild-ASCII mit y-Koordinaten (Diagnose)
echo "=== Vollbild-Fenster (100x60, mit y-Koordinaten) ==="
/opt/Aktien/venv/bin/python3 - /tmp/ibgw_init.png "$WIN_X" "$WIN_Y" "$W" "$H" <<'PYEOF'
import sys
from PIL import Image
f, wx, wy, ww, wh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
chars = ' .:-=+*#%@'
img = Image.open(f)
win = img.crop((wx, wy, wx+ww, wy+wh)).convert('L').resize((100, 60))
for y in range(60):
    row = ''.join(chars[int(win.getpixel((x,y))*(len(chars)-1)/255)] for x in range(100))
    wy_px = y * wh // 60
    print(f"{wy_px:3d}px: {row}")
PYEOF

# Pixel-Scan: Helle Eingabefelder automatisch erkennen (y=210-300, threshold>150, >30 helle Pixel)
# Verbose-Diagnose via stderr; FELD-Zeilen via stdout fuer bash-Parsing
echo "=== Pixel-Scan Eingabefelder ==="
SCAN=$(/opt/Aktien/venv/bin/python3 - /tmp/ibgw_init.png "$WIN_X" "$WIN_Y" "$W" "$H" <<'PYEOF'
import sys
from PIL import Image
f, wx, wy, ww, wh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
img = Image.open(f)
win = img.crop((wx, wy, wx+ww, wy+wh)).convert('L')
# Verbose: jede Zeile mit >5 hellen Pixeln ausgeben (stderr)
sys.stderr.write("=== Scan y=210-300, thresh>150 ===\n")
for y in range(210, min(wh, 300), 4):
    bp = [x for x in range(ww) if win.getpixel((x, y)) > 150]
    if len(bp) > 5:
        sys.stderr.write(f"  y={y:3d}: n={len(bp):3d} x={min(bp)}-{max(bp)}\n")
# Cluster-Erkennung (stdout -> bash)
rows = [(y, [x for x in range(ww) if win.getpixel((x, y)) > 150])
        for y in range(210, min(wh, 300))]
rows = [(y, bp) for y, bp in rows if len(bp) > 30]
if not rows:
    sys.stderr.write("  FALLBACK: keine Cluster gefunden\n")
    print(f"FELD0 {ww//2} 223")
    print(f"FELD1 {ww//2} 257")
else:
    clusters, cl = [], [rows[0]]
    for r in rows[1:]:
        if r[0] - cl[-1][0] <= 5:
            cl.append(r)
        else:
            clusters.append(cl)
            cl = [r]
    clusters.append(cl)
    for i, cl in enumerate(clusters):
        best = max(cl, key=lambda r: len(r[1]))
        # 70th-Perzentile der hellen x-Positionen: landet im Eingabefeld, rechts von Label
        sx = sorted(best[1])
        xc = sx[len(sx) * 7 // 10]
        sys.stderr.write(f"  FELD{i}: y={best[0]} x_range={min(best[1])}-{max(best[1])} x70pct={xc}\n")
        print(f"FELD{i} {xc} {best[0]}")
PYEOF
)
echo "$SCAN"
SCAN_X=$(echo "$SCAN"  | awk 'NR==1{print $2}')
SCAN_Y0=$(echo "$SCAN" | awk 'NR==1{print $3}')
SCAN_Y1=$(echo "$SCAN" | awk 'NR==2{print $3}')
if [ -n "$SCAN_X"  ]; then USER_ABS_X=$((WIN_X + SCAN_X)); else USER_ABS_X=$((WIN_X + W/2)); fi
if [ -n "$SCAN_Y0" ]; then USER_REL_Y="$SCAN_Y0";           else USER_REL_Y=223; fi
if [ -n "$SCAN_Y1" ]; then PASS_REL_Y="$SCAN_Y1";           else PASS_REL_Y=257; fi
USER_ABS_Y=$((WIN_Y + USER_REL_Y))
PASS_ABS_Y=$((WIN_Y + PASS_REL_Y))
echo "Adaptive Koordinaten: Username (${USER_ABS_X}, ${USER_ABS_Y}), Passwort (${USER_ABS_X}, ${PASS_ABS_Y})"

# Fenster in Vordergrund und X11-Fokus setzen (beides noetig fuer JavaFX Widget-Fokus)
xdotool windowraise "$WIN" 2>/dev/null || true
xdotool windowfocus --sync "$WIN" 2>/dev/null || true
sleep 0.5
echo "X11-Fokus nach windowfocus: $(xdotool getwindowfocus 2>/dev/null) (erwartet: $WIN)"

# ---- Benutzernamefeld ----
echo "Klicke Username: (${USER_ABS_X}, ${USER_ABS_Y})"
xdotool mousemove "$USER_ABS_X" "$USER_ABS_Y"
sleep 0.3
echo "Maus-Position: $(xdotool getmouselocation 2>/dev/null)"
xdotool click 1
sleep 0.8
xdotool click 1
sleep 1.2
echo "X11-Fokus nach Klick: $(xdotool getwindowfocus 2>/dev/null) (war: $NATURAL_FOCUS)"

scrot /tmp/ibgw_before_type.png 2>/dev/null || true

# Diagnose: hatte der Klick selbst eine visuelle Wirkung?
echo "=== Klick-Wirkung (init vs before_type) ==="
/opt/Aktien/venv/bin/python3 - /tmp/ibgw_init.png /tmp/ibgw_before_type.png \
    "$WIN_X" "$WIN_Y" "$W" "$H" "$USER_REL_Y" <<'PYEOF'
import sys
from PIL import Image
f1, f2 = sys.argv[1], sys.argv[2]
wx, wy, ww, wh, uy = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
b = Image.open(f1).crop((wx, wy, wx+ww, wy+wh)).convert('L')
a = Image.open(f2).crop((wx, wy, wx+ww, wy+wh)).convert('L')
row_d = sum(abs(a.getpixel((x, uy)) - b.getpixel((x, uy))) for x in range(ww))
total = sum(abs(a.getpixel((x, y)) - b.getpixel((x, y))) for y in range(wh) for x in range(0, ww, 4))
print(f"Zeile y={uy}: {row_d} ({'Fokus-Cursor!' if row_d>100 else 'keine Aenderung'})")
print(f"Gesamt: {total} ({'Klick hatte Wirkung' if total>300 else 'Klick hatte KEINE Wirkung'})")
PYEOF

# KEIN windowfocus - natuerlicherweise bleibt JavaFX-Handler
xdotool type --clearmodifiers --delay 150 "stocksentimenttradingbot"
sleep 1.0

scrot /tmp/ibgw_after_type.png 2>/dev/null || true

# Pixel-Differenz: hat das Fenster auf die Eingabe reagiert?
echo "=== Pixel-Differenz Username-Zeile ==="
/opt/Aktien/venv/bin/python3 - /tmp/ibgw_before_type.png /tmp/ibgw_after_type.png \
    "$WIN_X" "$WIN_Y" "$W" "$H" "$USER_REL_Y" "$PASS_REL_Y" <<'PYEOF'
import sys
from PIL import Image
f1, f2 = sys.argv[1], sys.argv[2]
wx, wy, ww, wh = int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
uy, py = int(sys.argv[7]), int(sys.argv[8])
b = Image.open(f1).crop((wx, wy, wx+ww, wy+wh)).convert('L')
a = Image.open(f2).crop((wx, wy, wx+ww, wy+wh)).convert('L')
for label, y in [('Username', uy), ('Passwort', py)]:
    d = sum(abs(a.getpixel((x, y)) - b.getpixel((x, y))) for x in range(ww))
    print(f"{label} y={y}: Differenz={d} ({'OK: Text erschienen!' if d>200 else 'LEER: keine Aenderung' if d<50 else 'unklar'})")
# Gesamtdiff (irgendwo im Fenster)
total = sum(abs(a.getpixel((x, y)) - b.getpixel((x, y))) for y in range(wh) for x in range(0, ww, 4))
print(f"Gesamt-Differenz im Fenster: {total} ({'Etwas hat sich geaendert' if total>500 else 'Nichts hat sich geaendert'})")
PYEOF

echo "=== Nach Username-Eingabe ==="
/opt/Aktien/venv/bin/python3 - /tmp/ibgw_after_type.png "$WIN_X" "$WIN_Y" "$W" "$H" <<'PYEOF'
import sys
from PIL import Image
f, wx, wy, ww, wh = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
chars = ' .:-=+*#%@'
img = Image.open(f)
win = img.crop((wx, wy, wx+ww, wy+wh)).convert('L').resize((100, 30))
for y in range(30):
    row = ''.join(chars[int(win.getpixel((x,y))*(len(chars)-1)/255)] for x in range(100))
    wy_px = y * wh // 30
    print(f"{wy_px:3d}px: {row}")
PYEOF

xdotool key --clearmodifiers Tab
sleep 2.0

# ---- Passwortfeld ----
echo "Klicke Passwort: (${USER_ABS_X}, ${PASS_ABS_Y})"
xdotool mousemove "$USER_ABS_X" "$PASS_ABS_Y"
sleep 0.3
xdotool click 1; sleep 0.8; xdotool click 1; sleep 1.2

xdotool type --clearmodifiers --delay 150 "narjAv-qixru3-b1whaj"
sleep 1.0

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
for y in range(30):
    row = ''.join(chars[int(win.getpixel((x,y))*(len(chars)-1)/255)] for x in range(100))
    wy_px = y * wh // 30
    print(f"{wy_px:3d}px: {row}")
PYEOF

sleep 55
if ss -tlnp 2>/dev/null | grep -q ':4002'; then
    echo "ERFOLG: Port 4002 ist offen"
else
    echo "WARNUNG: Port 4002 nach 60s noch nicht offen"
    ps aux | grep java | grep -v grep | awk '{print "Java laeuft:", $1, $11}'
fi
