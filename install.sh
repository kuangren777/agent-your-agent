#!/bin/bash
# AYA (Agent Your Agent) — installer
#
# One-liner:
#   curl -fsSL https://raw.githubusercontent.com/kuangren777/agent-your-agent/main/install.sh | bash
#
# Or:
#   git clone https://github.com/kuangren777/agent-your-agent.git && cd agent-your-agent && ./install.sh

set -e

AYA_HOME="$HOME/.aya"
AYA_SRC="$AYA_HOME/src"
CLAUDE_SKILL="$HOME/.claude/skills/aya"
REPO_URL="https://github.com/kuangren777/agent-your-agent.git"

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║   AYA — Agent Your Agent              ║"
echo "  ║   Multi-agent orchestration for       ║"
echo "  ║   Claude Code & Codex                 ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""

# --- Locate source ---
if [ -f "$(dirname "$0")/src/aya/workspace.py" ]; then
    SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
else
    echo "Cloning from GitHub..."
    SRC_DIR=$(mktemp -d)
    git clone --depth 1 "$REPO_URL" "$SRC_DIR" 2>/dev/null
    CLEANUP_SRC=1
fi

# --- Core install: ~/.aya/ ---
echo "[1/3] Installing core to $AYA_HOME"
mkdir -p "$AYA_HOME"
rm -rf "$AYA_SRC"
mkdir -p "$AYA_SRC"
cp -r "$SRC_DIR/src/aya" "$AYA_SRC/aya"
rm -rf "$AYA_SRC/aya/__pycache__"
echo "  ✓ Python package → $AYA_SRC/aya/"

# --- Claude Code integration ---
echo "[2/3] Installing Claude Code skill"
mkdir -p "$CLAUDE_SKILL"
cp "$SRC_DIR/.claude/skills/aya.md" "$CLAUDE_SKILL/SKILL.md"
echo "  ✓ Skill → $CLAUDE_SKILL/SKILL.md"

# --- Codex integration ---
echo "[3/3] Installing Codex instructions"
CODEX_DIR="$HOME/.codex"
mkdir -p "$CODEX_DIR"
if [ ! -f "$CODEX_DIR/instructions.md" ]; then
    cat > "$CODEX_DIR/instructions.md" << 'CODEX_EOF'
# AYA — Agent Your Agent

When the user says "aya" or asks to use AYA for a task, you are entering AYA PM mode.

AYA CLI is at: PYTHONPATH=~/.aya/src python3 -m aya.workspace <command>

Run `PYTHONPATH=~/.aya/src python3 -m aya.workspace status` to check current state.
Run `PYTHONPATH=~/.aya/src python3 -m aya.workspace --help` for all commands.

For full PM instructions, read: ~/.claude/skills/aya/SKILL.md
CODEX_EOF
    echo "  ✓ Instructions → $CODEX_DIR/instructions.md"
else
    echo "  ⊘ $CODEX_DIR/instructions.md already exists, skipped"
fi

# --- Cleanup temp clone ---
if [ "${CLEANUP_SRC:-0}" = "1" ]; then
    rm -rf "$SRC_DIR"
fi

echo ""

# --- Model setup on first install ---
if [ ! -f "$AYA_HOME/models.json" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  First-time setup: configure your models"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    PYTHONPATH="$AYA_SRC" python3 -m aya.workspace setup
else
    echo "Models already configured ($AYA_HOME/models.json)"
    echo "To reconfigure: PYTHONPATH=~/.aya/src python3 -m aya.workspace setup"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Installation complete!"
echo ""
echo "  Claude Code:  /aya \"your task\""
echo "  Codex:        mention \"aya\" in prompt"
echo "  CLI:          PYTHONPATH=~/.aya/src python3 -m aya.workspace status"
echo ""
echo "  Update:       PYTHONPATH=~/.aya/src python3 -m aya.workspace self-update"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
