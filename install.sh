#!/bin/bash
# AYA (Agent Your Agent) — one-line installer
# Usage: git clone https://github.com/kuangren777/agent-your-agent.git && cd agent-your-agent && ./install.sh

set -e

SKILL_DIR="$HOME/.claude/skills/aya"
REPO_URL="https://github.com/kuangren777/agent-your-agent.git"

echo ""
echo "  ╔═══════════════════════════════════╗"
echo "  ║   AYA — Agent Your Agent          ║"
echo "  ║   Multi-agent orchestration for   ║"
echo "  ║   Claude Code                     ║"
echo "  ╚═══════════════════════════════════╝"
echo ""

# If running from a cloned repo, use local files; otherwise clone to tmp
if [ -f "$(dirname "$0")/src/aya/workspace.py" ]; then
    SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
else
    echo "Cloning from GitHub..."
    SRC_DIR=$(mktemp -d)
    git clone --depth 1 "$REPO_URL" "$SRC_DIR" 2>/dev/null
    CLEANUP_SRC=1
fi

# Create skill directory
mkdir -p "$SKILL_DIR"

# Copy skill file
cp "$SRC_DIR/.claude/skills/aya.md" "$SKILL_DIR/SKILL.md"

# Copy Python package
rm -rf "$SKILL_DIR/aya"
cp -r "$SRC_DIR/src/aya" "$SKILL_DIR/aya"
rm -rf "$SKILL_DIR/aya/__pycache__"

echo "Files installed to $SKILL_DIR"
echo ""

# Cleanup if we cloned
if [ "${CLEANUP_SRC:-0}" = "1" ]; then
    rm -rf "$SRC_DIR"
fi

# Run model setup if no models.json yet
if [ ! -f "$SKILL_DIR/models.json" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  First-time setup: configure models"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    PYTHONPATH="$SKILL_DIR" python3 -m aya.workspace setup
else
    echo "Models already configured ($SKILL_DIR/models.json)"
    echo "To reconfigure: PYTHONPATH=$SKILL_DIR python3 -m aya.workspace setup"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Installation complete!"
echo ""
echo "  In Claude Code, type:"
echo "    /aya \"your task here\""
echo ""
echo "  Or run /aya to enter PM mode"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
