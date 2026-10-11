#!/usr/bin/env bash
# KTSB — Hardware detection + docker-compose.override.yml generator
# Run this once before "docker compose up" to enable GPU support.
# Safe to run again at any time to refresh detection.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="$SCRIPT_DIR/state"
OVERRIDE_FILE="$SCRIPT_DIR/docker-compose.override.yml"
HW_FILE="$STATE_DIR/hardware.json"

mkdir -p "$STATE_DIR"

echo ""
echo "=== KTSB Hardware Detection ==="
echo ""

# ── Platform ─────────────────────────────────────────────────────────────────
PLATFORM="linux"
if [[ "$(uname -s)" == "Darwin" ]]; then
  PLATFORM="darwin"
fi

# ── CPU ──────────────────────────────────────────────────────────────────────
CPU_MODEL="unknown"
CPU_CORES=0
if [[ -f /proc/cpuinfo ]]; then
  CPU_MODEL=$(grep -m1 "^model name" /proc/cpuinfo | cut -d: -f2 | xargs)
  CPU_CORES=$(grep -c "^processor" /proc/cpuinfo || echo 0)
elif [[ "$PLATFORM" == "darwin" ]]; then
  CPU_MODEL=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "unknown")
  CPU_CORES=$(sysctl -n hw.logicalcpu 2>/dev/null || echo 0)
fi
echo "  CPU:  $CPU_MODEL ($CPU_CORES cores)"

# ── RAM ──────────────────────────────────────────────────────────────────────
RAM_GB=0
if [[ -f /proc/meminfo ]]; then
  RAM_KB=$(grep "^MemTotal:" /proc/meminfo | awk '{print $2}')
  RAM_GB=$(echo "scale=1; $RAM_KB / 1048576" | bc 2>/dev/null || echo 0)
elif [[ "$PLATFORM" == "darwin" ]]; then
  RAM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
  RAM_GB=$(echo "scale=1; $RAM_BYTES / 1073741824" | bc 2>/dev/null || echo 0)
fi
echo "  RAM:  ${RAM_GB} GB"

# ── GPU detection ─────────────────────────────────────────────────────────────
# GPU_TYPE/GPU_NAME/GPU_VRAM_GB select the *primary* GPU used for Ollama's passthrough
# config below (NVIDIA > AMD > Intel priority — Ollama only uses one accelerator backend).
# GPU_JSON_ENTRIES is the full list of all detected GPUs written to state/hardware.json —
# an Intel iGPU is always included there since it commonly coexists with a discrete
# NVIDIA/AMD card and must not be hidden just because a discrete GPU was already found.
GPU_TYPE="none"
GPU_NAME=""
GPU_VRAM_GB=""
GPU_JSON_ENTRIES=()

# NVIDIA
if command -v nvidia-smi &>/dev/null; then
  NVIDIA_OUT=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null || true)
  if [[ -n "$NVIDIA_OUT" ]]; then
    GPU_TYPE="nvidia"
    GPU_NAME=$(echo "$NVIDIA_OUT" | head -1 | cut -d, -f1 | xargs)
    VRAM_MB=$(echo "$NVIDIA_OUT" | head -1 | cut -d, -f2 | xargs)
    GPU_VRAM_GB=$(echo "scale=1; $VRAM_MB / 1024" | bc 2>/dev/null || echo "?")
    echo "  GPU:  NVIDIA $GPU_NAME (${GPU_VRAM_GB} GB VRAM)"
    while IFS=, read -r N V; do
      N=$(echo "$N" | xargs); V=$(echo "$V" | xargs)
      VG=$(echo "scale=1; $V / 1024" | bc 2>/dev/null || echo null)
      GPU_JSON_ENTRIES+=("{\"type\": \"nvidia\", \"id\": ${#GPU_JSON_ENTRIES[@]}, \"name\": \"$N\", \"vram_gb\": $VG}")
    done <<< "$NVIDIA_OUT"
  fi
fi

# AMD (rocm-smi)
if [[ "$GPU_TYPE" == "none" ]] && command -v rocm-smi &>/dev/null; then
  ROCM_OUT=$(rocm-smi --showproductname 2>/dev/null || true)
  if echo "$ROCM_OUT" | grep -qi "card"; then
    GPU_TYPE="amd"
    GPU_NAME=$(echo "$ROCM_OUT" | grep -i "card" | head -1 | awk '{print $NF}')
    echo "  GPU:  AMD $GPU_NAME"
    GPU_JSON_ENTRIES+=("{\"type\": \"amd\", \"id\": ${#GPU_JSON_ENTRIES[@]}, \"name\": \"$GPU_NAME\", \"vram_gb\": null}")
  fi
fi

# Intel iGPU / Arc — always checked, regardless of a discrete GPU already being found
if [[ -d /sys/class/drm ]]; then
  for card in /sys/class/drm/card[0-9]*; do
    VENDOR_FILE="$card/device/vendor"
    [[ -f "$VENDOR_FILE" ]] || continue
    VENDOR=$(cat "$VENDOR_FILE" 2>/dev/null)
    [[ "$VENDOR" == "0x8086" ]] || continue
    PCI_ADDR=$(basename "$(readlink -f "$card/device")")
    BUS="${PCI_ADDR#*:}"; BUS="${BUS%%:*}"
    if [[ "$BUS" == "00" ]]; then
      INTEL_GPU_TYPE="intel-igpu"; INTEL_GPU_NAME="Intel iGPU"
    else
      INTEL_GPU_TYPE="intel-arc"; INTEL_GPU_NAME="Intel Arc GPU"
    fi
    if command -v lspci &>/dev/null; then
      LSPCI_NAME=$(lspci -mm -s "$PCI_ADDR" 2>/dev/null | awk -F'"' '{print $8}')
      [[ -n "$LSPCI_NAME" ]] && INTEL_GPU_NAME="$LSPCI_NAME"
    fi
    echo "  GPU:  $INTEL_GPU_NAME ($INTEL_GPU_TYPE)"
    GPU_JSON_ENTRIES+=("{\"type\": \"$INTEL_GPU_TYPE\", \"id\": ${#GPU_JSON_ENTRIES[@]}, \"name\": \"$INTEL_GPU_NAME\", \"vram_gb\": null}")
    if [[ "$GPU_TYPE" == "none" ]]; then
      GPU_TYPE="intel"
      GPU_NAME="$INTEL_GPU_NAME"
    fi
    break  # only one Intel GPU typically present
  done
fi

if [[ "$GPU_TYPE" == "none" ]]; then
  echo "  GPU:  None detected (CPU mode)"
fi

# ── NPU ──────────────────────────────────────────────────────────────────────
NPU_DETECTED=false
if [[ -d /dev/accel ]] && compgen -G "/dev/accel/*" > /dev/null; then
  NPU_DETECTED=true
  echo "  NPU:  Intel NPU detected ($(cd /dev/accel && echo *))"
else
  echo "  NPU:  None detected"
fi

echo ""

# ── Ollama choice ─────────────────────────────────────────────────────────────
echo "=== LLM source ==="
echo ""
echo "  Kåre needs a language model (LLM) to work."
echo "  The built-in Ollama downloads a ~10 GB Docker image."
echo "  Skip this if you have Ollama or another LLM server already running."
echo ""
read -rp "  Include built-in Ollama? [Y/n]: " OLLAMA_CHOICE
OLLAMA_CHOICE="${OLLAMA_CHOICE:-y}"
echo ""

# ── Write state/hardware.json ─────────────────────────────────────────────────
cat > "$HW_FILE" <<EOF
{
  "detected_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source": "host_script",
  "platform": "$PLATFORM",
  "cpu": {
    "model": "$CPU_MODEL",
    "cores": $CPU_CORES
  },
  "ram_gb": $RAM_GB,
  "gpus": [$(IFS=,; echo "${GPU_JSON_ENTRIES[*]:-}")],
  "npu": { "detected": $NPU_DETECTED }
}
EOF

echo "  Hardware info saved to: state/hardware.json"
echo ""

# ── Generate docker-compose.override.yml ─────────────────────────────────────
if [[ "$GPU_TYPE" == "nvidia" ]]; then
  cat > "$OVERRIDE_FILE" <<'OVERRIDE'
# Auto-generated by setup.sh — NVIDIA GPU detected.
# Adds GPU passthrough to Ollama.
# Requirements: nvidia-container-toolkit must be installed on the host.
services:
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
OVERRIDE
  echo "  ✓ docker-compose.override.yml written (NVIDIA GPU enabled for Ollama)"

elif [[ "$GPU_TYPE" == "amd" ]]; then
  cat > "$OVERRIDE_FILE" <<'OVERRIDE'
# Auto-generated by setup.sh — AMD GPU detected.
# Adds ROCm device passthrough to Ollama.
services:
  ollama:
    devices:
      - /dev/kfd
      - /dev/dri
    environment:
      - HSA_OVERRIDE_GFX_VERSION=10.3.0
OVERRIDE
  echo "  ✓ docker-compose.override.yml written (AMD GPU enabled for Ollama)"

elif [[ "$GPU_TYPE" == "intel" ]]; then
  cat > "$OVERRIDE_FILE" <<'OVERRIDE'
# Auto-generated by setup.sh — Intel GPU detected.
# Adds Intel GPU device passthrough to Ollama.
services:
  ollama:
    devices:
      - /dev/dri
    environment:
      - OLLAMA_INTEL_GPU=1
OVERRIDE
  echo "  ✓ docker-compose.override.yml written (Intel GPU enabled for Ollama)"

else
  # Remove override if no GPU (CPU mode)
  if [[ -f "$OVERRIDE_FILE" ]]; then
    rm "$OVERRIDE_FILE"
    echo "  ✓ docker-compose.override.yml removed (CPU mode)"
  else
    echo "  ✓ Running in CPU mode (no GPU override needed)"
  fi
fi

# ── Write .env ────────────────────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
  echo "  ✓ .env created from .env.example"
fi

# Build COMPOSE_PROFILES value
PROFILES="medium"
if [[ "$GPU_TYPE" != "none" ]]; then
  PROFILES="full"
fi
if [[ "${OLLAMA_CHOICE,,}" == "y" || "${OLLAMA_CHOICE,,}" == "yes" || "$OLLAMA_CHOICE" == "" ]]; then
  PROFILES="ollama,$PROFILES"
fi

# Update COMPOSE_PROFILES in .env
if grep -q "^COMPOSE_PROFILES=" "$ENV_FILE"; then
  sed -i "s|^COMPOSE_PROFILES=.*|COMPOSE_PROFILES=$PROFILES|" "$ENV_FILE"
else
  echo "COMPOSE_PROFILES=$PROFILES" >> "$ENV_FILE"
fi
echo "  ✓ COMPOSE_PROFILES=$PROFILES written to .env"

# ── Install path + version (read by the in-app updater) ──────────────────────
set_env() {
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$val" 'BEGIN{done=0} $0 ~ "^"k"=" {print k"="v; done=1; next} {print} END{if(!done) print k"="v}' \
    "$ENV_FILE" > "$tmp" && cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}
# On Linux and macOS the Docker engine sees this folder under the same path.
set_env KTSB_HOME "$SCRIPT_DIR"
set_env KTSB_HOME_HOST "$SCRIPT_DIR"
echo "  ✓ KTSB_HOME=$SCRIPT_DIR written to .env"
# ── Git clone → updatable install (the first upgrade from v1.9, P57) ────────
# The in-app updater replaces docker-compose.yml and friends; a later `git pull` would then conflict. So a git clone
# is converted once: every file stays where it is, .git is only moved aside (rename it back to undo), and the release
# the clone is on is written to VERSION. From then on updates come from the Maintenance page.
if [[ -d "$SCRIPT_DIR/.git" ]] && command -v git >/dev/null 2>&1; then
  CLONE_TAG="$(git -C "$SCRIPT_DIR" describe --tags --abbrev=0 --exclude '*-*' 2>/dev/null || true)"
  if [[ "$CLONE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo ""
    echo "  This folder is a git clone (release $CLONE_TAG)."
    echo "  Converting it lets KTSB update itself from the Maintenance page; git pull is then no longer used."
    read -rp "  Convert now? [Y/n]: " CONVERT_CHOICE
    CONVERT_CHOICE="${CONVERT_CHOICE:-y}"
    if [[ "${CONVERT_CHOICE,,}" == "y" || "${CONVERT_CHOICE,,}" == "yes" ]]; then
      CHANGED="$(git -C "$SCRIPT_DIR" status --porcelain -- docker-compose.yml docker-compose.gpu.yml .env.example setup.sh setup.ps1 docker 2>/dev/null || true)"
      if [[ -n "$CHANGED" ]]; then
        echo "  ! Your own changes in these files will be replaced by future updates —"
        echo "    move them to docker-compose.override.yml:"
        printf '%s\n' "$CHANGED" | sed 's/^/      /'
      fi
      GIT_ASIDE="$SCRIPT_DIR/.git-before-ktsb-updater"
      [[ -e "$GIT_ASIDE" ]] && GIT_ASIDE="$GIT_ASIDE-$(date +%Y%m%d%H%M%S)"
      echo "$CLONE_TAG" > "$SCRIPT_DIR/VERSION"
      mv "$SCRIPT_DIR/.git" "$GIT_ASIDE"
      set_env KTSB_VERSION "$CLONE_TAG"
      echo "  ✓ Converted: .git moved to $(basename "$GIT_ASIDE"), KTSB_VERSION=$CLONE_TAG"
      echo "    (to undo: rename it back to .git and delete VERSION)"
    else
      echo "  Kept as a git clone — updates from the Maintenance page stay off."
    fi
  fi
fi

# A release bundle carries VERSION; never overwrite a version the updater already set.
if [[ -f "$SCRIPT_DIR/VERSION" ]] && ! grep -q "^KTSB_VERSION=." "$ENV_FILE"; then
  set_env KTSB_VERSION "$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION")"
  echo "  ✓ KTSB_VERSION=$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION") written to .env"
fi

echo ""
echo "=== Next steps ==="
echo ""
echo "  docker compose up -d"
echo ""
if [[ "$GPU_TYPE" == "nvidia" ]]; then
  echo "  NOTE: NVIDIA GPU support requires nvidia-container-toolkit."
  echo "  Install guide: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
  echo ""
fi
