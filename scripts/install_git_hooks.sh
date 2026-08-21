#!/usr/bin/env bash
# Installiert die versionierten Git-Hooks (Roadmap 4.4a) — .git/hooks wird
# von Git selbst nie mitversioniert, deshalb liegt die Quelle unter
# scripts/git-hooks/ und wird hier verlinkt. Idempotent, überschreibt nur
# Symlinks, die bereits auf unsere Quelle zeigen oder komplett fehlen (ein
# fremder bestehender Hook wird NICHT stillschweigend ersetzt).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

for hook in scripts/git-hooks/*; do
    name="$(basename "$hook")"
    target=".git/hooks/$name"
    if [ -e "$target" ] && [ ! -L "$target" ]; then
        echo "ÜBERSPRINGE $target: existiert bereits und ist kein von uns gesetzter Symlink."
        continue
    fi
    ln -sf "../../$hook" "$target"
    chmod +x "$hook"
    echo "installiert: $target -> $hook"
done
