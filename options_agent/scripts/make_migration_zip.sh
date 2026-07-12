#!/bin/bash
# Bundle the gitignored SECRETS needed to run the stack on another machine into a
# single zip (default: ~/ubuntu_migration.zip). Contains live-money credentials —
# transfer over a trusted channel and delete both copies once imported.
#
# The .env files are stored flat so the target unpacks with:
#   unzip ubuntu_migration.zip -d options_agent/
#
# Journals/data_cache regenerate at runtime and are NOT included by default.
# Flags (bundle more for a full migration):
#   --with-live-journal   also include real-money trade history
#   --full                include ALL journals (live/paper/shadow/public) + data_cache
#
# Everything is stored relative to options_agent/, so the target unpacks with:
#   unzip ubuntu_migration.zip -d options_agent/

set -euo pipefail

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGENT_DIR="$PROJ_DIR/options_agent"
MODE="${1:-}"
OUT="$HOME/ubuntu_migration.zip"

cd "$AGENT_DIR"

FILES=()
for f in .env .env.live .env.public .env.shadow; do
    [ -f "$f" ] && FILES+=("$f")
done

if [ ${#FILES[@]} -eq 0 ]; then
    echo "No .env* secrets found in $AGENT_DIR — nothing to bundle." >&2
    exit 1
fi

rm -f "$OUT"
zip -q "$OUT" "${FILES[@]}"

case "$MODE" in
    --with-live-journal)
        [ -d sweet_spot_journal_live ] && zip -qr "$OUT" sweet_spot_journal_live
        ;;
    --full)
        for d in sweet_spot_journal sweet_spot_journal_live \
                 sweet_spot_journal_shadow sweet_spot_journal_public data_cache; do
            [ -d "$d" ] && zip -qr "$OUT" "$d"
        done
        ;;
esac

echo "Wrote $OUT"
echo "Contents (names only, no secret values):"
unzip -l "$OUT"
echo
echo "⚠️  Contains live credentials — scp/USB it, then delete from both machines."
