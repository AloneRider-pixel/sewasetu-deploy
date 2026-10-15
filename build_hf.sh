#!/bin/sh
# Build the Hugging Face Space bundle: same code, seed compressed (HF's 10 MB per-file limit), Space metadata.
set -e
OUT=${1:-/tmp/hf-space}
rm -rf "$OUT"; mkdir -p "$OUT/seed"
git ls-files | grep -v '^seed/seed.sql$' | grep -v '^README.md$' | while read f; do mkdir -p "$OUT/$(dirname "$f")"; cp "$f" "$OUT/$f"; done
xz -9e -c seed/seed.sql > "$OUT/seed/seed.sql.xz"
{ printf -- '---\ntitle: Sewa Setu Assistant\nemoji: 🏛️\ncolorFrom: blue\ncolorTo: indigo\nsdk: docker\napp_port: 7860\npinned: false\n---\n\n'; cat README.md; } > "$OUT/README.md"
echo "built $OUT"; du -sh "$OUT"
