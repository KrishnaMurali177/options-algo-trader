#!/bin/bash
# Bundle the gitignored SECRETS needed to run the stack on another machine into a
# single zip (default: ~/ubuntu_migration.zip). Contains live-money credentials —
# transfer over a trusted channel and delete both copies once imported.
#
# The .env files are stored flat so the target unpacks with:
#   unzip ubuntu_migration.zip -d options_agent/
#
# Journals/data_cache regenerate at runtime and are NOT included by default.
# Pass --with-live-journal to also bundle real-money trade history.

set -euo pipefail

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGENT_DIR="$PROJ_DIR/options_agent"
OUT="${1:-$HOME/ubuntu_migration.zip}"
[ "${1:-}" = "--with-live-journal" ] && OUT="$HOME/ubuntu_migration.zip"

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

if [ "${1:-}" = "--with-live-journal" ] && [ -d sweet_spot_journal_live ]; then
    zip -qr "$OUT" sweet_spot_journal_live
fi

echo "Wrote $OUT"
echo "Contents (names only, no secret values):"
unzip -l "$OUT"
echo
echo "⚠️  Contains live credentials — scp/USB it, then delete from both machines."
