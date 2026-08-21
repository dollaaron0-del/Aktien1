#!/bin/bash
export DISPLAY=:99

# Keyboard-Setup: setxkbmap + xmodmap Fallback
# (Xvfb hat ohne explizites Setup keine Buchstaben-Keycodes)
setxkbmap -layout us -rules evdev -model pc105 2>/dev/null || \
    setxkbmap -layout us 2>/dev/null || true

# Pruefen ob 's' gemappt ist (repraesentativ fuer alle Buchstaben)
S_KC=$(xmodmap -pke 2>/dev/null | grep -m1 " = s " | awk '{print $2}')
echo "Keyboard: Keycode fuer 's' nach setxkbmap: ${S_KC:-NICHT GEMAPPT}"

if [ -z "$S_KC" ]; then
    echo "setxkbmap nicht wirksam -> xmodmap Fallback"
    xmodmap \
        -e "keycode 10 = 1 exclam" \
        -e "keycode 11 = 2 at" \
        -e "keycode 12 = 3 numbersign" \
        -e "keycode 13 = 4 dollar" \
        -e "keycode 14 = 5 percent" \
        -e "keycode 19 = 0 parenright" \
        -e "keycode 20 = minus underscore" \
        -e "keycode 24 = q Q" \
        -e "keycode 25 = w W" \
        -e "keycode 26 = e E" \
        -e "keycode 27 = r R" \
        -e "keycode 28 = t T" \
        -e "keycode 29 = y Y" \
        -e "keycode 30 = u U" \
        -e "keycode 31 = i I" \
        -e "keycode 32 = o O" \
        -e "keycode 33 = p P" \
        -e "keycode 38 = a A" \
        -e "keycode 39 = s S" \
        -e "keycode 40 = d D" \
        -e "keycode 42 = g G" \
        -e "keycode 43 = h H" \
        -e "keycode 44 = j J" \
        -e "keycode 45 = k K" \
        -e "keycode 53 = x X" \
        -e "keycode 54 = c C" \
        -e "keycode 55 = v V" \
        -e "keycode 56 = b B" \
        -e "keycode 57 = n N" \
        -e "keycode 58 = m M" \
        2>/dev/null || echo "FEHLER: xmodmap fehlgeschlagen"
    S_KC2=$(xmodmap -pke 2>/dev/null | grep -m1 " = s " | awk '{print $2}')
    echo "Keyboard nach xmodmap: Keycode fuer 's': ${S_KC2:-IMMER NOCH NICHT GEMAPPT}"
fi
sleep 0.5

echo "===== AUTOLOGIN v6 ====="

pkill -f java 2>/dev/null || true
sleep 3

# ---- xterm sicherstellen (IBC-Voraussetzung) ----
if ! command -v xterm >/dev/null 2>&1; then
    echo "xterm fehlt - versuche apt-get..."
    apt-get install -y xterm 2>/dev/null || true
fi
if ! command -v xterm >/dev/null 2>&1; then
    echo "apt-get fehlgeschlagen - erstelle xterm-Stub"
    cat > /usr/local/bin/xterm << 'XWRAP'
#!/bin/bash
# Stub fuer IBC: findet -e Flag und fuehrt Argumente aus
while [[ $# -gt 0 ]]; do
    if [[ "$1" == "-e" ]]; then
        shift
        if [[ $# -eq 1 ]]; then eval "$1"; else exec "$@"; fi
        exit $?
    fi
    shift
done
XWRAP
    chmod +x /usr/local/bin/xterm
fi
echo "xterm: $(command -v xterm)"

# ---- IBC: i4jruntime.jar Symlink-Fix ----
# IBC iteriert ueber alle *.jar in den Gateway-Jars; symlink macht i4jruntime automatisch verfuegbar
I4J_JAR="/opt/ibgateway/.install4j/i4jruntime.jar"
GW_JARS="/opt/ibgateway/1045/jars"
if [ -f "$I4J_JAR" ] && [ -d "$GW_JARS" ] && [ ! -e "$GW_JARS/i4jruntime.jar" ]; then
    ln -sf "$I4J_JAR" "$GW_JARS/i4jruntime.jar" && \
        echo "IBC Fix: i4jruntime.jar -> $GW_JARS/ (OK)" || \
        echo "IBC Fix: FEHLER beim Symlink"
else
    ls "$GW_JARS/i4jruntime.jar" 2>/dev/null && echo "IBC Fix: Symlink bereits vorhanden" || \
        echo "IBC Fix: Voraussetzungen fehlen (JAR=$I4J_JAR JARS=$GW_JARS)"
fi

# ---- IBC config.ini: headless-sichere Einstellungen ----
IBC_CONFIG="/opt/ibc/config.ini"
if [ -f "$IBC_CONFIG" ]; then
    # 'manual' wuerde auf Benutzereingabe warten -> haengt lautlos in Xvfb
    sed -i 's/ExistingSessionDetectedAction=manual/ExistingSessionDetectedAction=primaryonly/' "$IBC_CONFIG"
    echo "IBC Config: $(grep ExistingSessionDetectedAction "$IBC_CONFIG")"
fi
mkdir -p /tmp/ibc_logs

# ---- IBC-Login-Versuch ----
# Zombie-Prozesse aus vorherigen Laeufen bereinigen
pkill -f 'displaybannerandlaunch' 2>/dev/null || true
pkill -9 -f 'tee.*ibc' 2>/dev/null || true
sleep 1
IBC_OK=false
if [ -f "/opt/ibc/gatewaystart.sh" ]; then
    echo "Starte IBC-Login..."
    DISPLAY=:99 bash /opt/ibc/gatewaystart.sh &
    echo "Warte bis 150s auf Port 4002 (IBC)..."
    for i in $(seq 1 15); do
        sleep 10
        if ss -tlnp 2>/dev/null | grep -qE ':4001|:4002'; then
            PORT=$(ss -tlnp 2>/dev/null | grep -oE ':400[12]' | head -1 | tr -d ':')
            echo "IBC ERFOLG: Port $PORT nach $((i*10))s offen"
            IBC_OK=true
            break
        fi
        pgrep -f java >/dev/null 2>&1 || { echo "  Java-Prozess gestorben - IBC abgebrochen"; break; }
        # Alle 30s: Screenshot des Xvfb zeigen
        if [ $((i % 3)) -eq 0 ]; then
            echo "  --- Xvfb-Status bei ${i}0s ---"
            pgrep -a java 2>/dev/null | head -2 | sed 's/^/  Java: /'
            scrot /tmp/ibc_diag_${i}0s.png 2>/dev/null && \
            /opt/Aktien/venv/bin/python3 - /tmp/ibc_diag_${i}0s.png <<'PYEOF'
import sys; from PIL import Image
img = Image.open(sys.argv[1]).convert('L').resize((80, 15))
chars = ' .:-=+*#%@'
for y in range(15):
    print('  '+''.join(chars[int(img.getpixel((x,y))*(len(chars)-1)/255)] for x in range(80)))
PYEOF
        fi
        echo "  ${i}0s: warte..."
    done
    if ! $IBC_OK; then
        echo "IBC FEHLGESCHLAGEN - Diagnose:"
        ps aux | grep -E 'java|xterm|ibc' | grep -v grep | head -5
        echo "=== /tmp/ibc_run.log ==="
        cat /tmp/ibc_run.log 2>/dev/null || echo "(keine /tmp/ibc_run.log)"
        echo "=== launcher.log (letzte 50 Zeilen) ==="
        LAUNCHER_TAIL=$(tail -50 /root/Jts/launcher.log 2>/dev/null)
        echo "${LAUNCHER_TAIL:-(keine /root/Jts/launcher.log)}"
        echo "=== IBC-Logs-Verzeichnis ==="
        ls -la /tmp/ibc_logs/ 2>/dev/null || echo "(leer oder nicht vorhanden)"
        cat /tmp/ibc_logs/*.txt 2>/dev/null | tail -80 || echo "(keine .txt Logs in /tmp/ibc_logs/)"
        if echo "$LAUNCHER_TAIL" | grep -q "INVALID_USERNAME_OR_BAD_IP"; then
            echo ""
            echo "!!! KRITISCH: IBKR lehnt Zugangsdaten/IP ab (INVALID_USERNAME_OR_BAD_IP) !!!"
            echo "Verwendeter Username: $(grep IbLoginId /opt/ibc/config.ini 2>/dev/null | cut -d= -f2)"
            echo "TradingMode:          $(grep TradingMode /opt/ibc/config.ini 2>/dev/null | cut -d= -f2)"
            echo "Server-IP:            $(curl -s4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
            echo "==> Zugangsdaten auf paper.ibkr.com testen + IP-Whitelist pruefen!"
        fi
        pkill -f java 2>/dev/null || true
        sleep 3
    fi
fi
$IBC_OK && exit 0

# ---- xdotool Fallback ----
echo "Starte xdotool-Fallback (direkte Gateway-GUI)..."
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
sleep 0.3

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

FOCUS_WIN=$(xdotool getwindowfocus 2>/dev/null)
echo "X11-Fokus vor Eingabe: $FOCUS_WIN"
# PRIMARY-Selection + Middle-Click (X11-Paste, umgeht JavaFX Keyboard-Filter)
printf 'stocksentimenttradingbot' | xclip -display :99 -selection primary 2>/dev/null || true
sleep 0.3
xdotool mousemove "$USER_ABS_X" "$USER_ABS_Y"
sleep 0.2
xdotool click 2
sleep 0.5

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
echo "X11-Fokus nach Passwort-Klick: $(xdotool getwindowfocus 2>/dev/null)"
sleep 0.3
# PRIMARY-Selection + Middle-Click fuer Passwort
printf 'narjAv-qixru3-b1whaj' | xclip -display :99 -selection primary 2>/dev/null || true
sleep 0.3
xdotool mousemove "$USER_ABS_X" "$PASS_ABS_Y"
sleep 0.2
xdotool click 2
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
