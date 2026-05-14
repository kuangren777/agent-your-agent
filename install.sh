#!/bin/bash
# AYA (Agent Your Agent) — one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/kuangren777/agent-your-agent/main/install.sh | bash
#    or: git clone + ./install.sh

set -e

SKILL_DIR="$HOME/.claude/skills/aya"
REPO_URL="https://github.com/kuangren777/agent-your-agent.git"

echo "Installing AYA (Agent Your Agent)..."

# If running from a cloned repo, use local files; otherwise clone to tmp
if [ -f "$(dirname "$0")/src/hive/workspace.py" ]; then
    SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
else
    SRC_DIR=$(mktemp -d)
    git clone --depth 1 "$REPO_URL" "$SRC_DIR" 2>/dev/null
    CLEANUP_SRC=1
fi

# Create skill directory
mkdir -p "$SKILL_DIR"

# Copy skill file
cp "$SRC_DIR/.claude/skills/aya.md" "$SKILL_DIR/SKILL.md"

# Copy Python package
rm -rf "$SKILL_DIR/hive"
cp -r "$SRC_DIR/src/hive" "$SKILL_DIR/hive"
rm -rf "$SKILL_DIR/hive/__pycache__"

# Cleanup if we cloned
if [ "${CLEANUP_SRC:-0}" = "1" ]; then
    rm -rf "$SRC_DIR"
fi

echo ""
echo "AYA installed successfully!"
echo ""
echo "  Skill:  $SKILL_DIR/SKILL.md"
echo "  Code:   $SKILL_DIR/hive/"
echo ""
echo "Usage: In any Claude Code session, type:"
echo "  /aya \"Build a REST API with auth\""
echo ""
