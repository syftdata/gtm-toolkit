#!/usr/bin/env bash
# Install gtm-toolkit skills to agent skill directories.
#
# Claude Code: use the plugin instead — `claude plugin add gtm-toolkit`
# This script handles other compatible agents (Codex, etc.)

set -euo pipefail

SKILLS_DIR="$(cd "$(dirname "$0")/skills" && pwd)"

install_to() {
  local dest="$1"
  local label="$2"
  mkdir -p "$dest"
  for skill_dir in "$SKILLS_DIR"/*/; do
    name="$(basename "$skill_dir")"
    target="$dest/$name"
    if [ -L "$target" ]; then
      rm "$target"
    elif [ -d "$target" ]; then
      echo "  WARNING: $target exists and is not a symlink — skipping $name for $label"
      continue
    fi
    ln -s "$skill_dir" "$target"
    echo "  linked $name"
  done
}

echo "Codex (~/.agents/skills/):"
install_to "$HOME/.agents/skills" "Codex"
echo ""
echo "Done. $(ls "$SKILLS_DIR" | wc -l | tr -d ' ') skill(s) installed."
echo ""
echo "For Claude Code, use: claude plugin add gtm-toolkit"
