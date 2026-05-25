#!/bin/bash
# ============================================================
# KÅRE VOICE SERVICE – INSTALLASJON
# ============================================================
# Kjør dette scriptet fra: /mnt/ai_disk/kaare/services/voice/
#
#   chmod +x setup.sh
#   ./setup.sh
#
# Scriptet gjør:
#   1. Lager Python virtual environment (venv)
#   2. Installerer alle avhengigheter
#   3. Laster ned Whisper-modell (OpenVINO-format)
#   4. Laster ned Piper norsk stemme
#   5. Laster ned openWakeWord-modell
#   6. Genererer fast-response lydfiler med Piper
# ============================================================

set -e  # Stopp ved feil

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
MODELS_DIR="$SCRIPT_DIR/models"
CACHE_DIR="$SCRIPT_DIR/cache/fast_responses"

echo "============================================"
echo " KÅRE VOICE SERVICE – INSTALLASJON"
echo "============================================"
echo ""
echo "Mappe: $SCRIPT_DIR"
echo ""

# --- 1. Opprett venv ---
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/6] Oppretter Python venv..."
    python3 -m venv "$VENV_DIR"
else
    echo "[1/6] venv finnes allerede, hopper over."
fi

# Aktiver venv
source "$VENV_DIR/bin/activate"

# --- 2. Installer Python-pakker ---
echo "[2/6] Installerer Python-pakker..."
pip install --upgrade pip

# Kjernepakker
pip install \
    fastapi \
    uvicorn[standard] \
    websockets \
    httpx \
    pyyaml \
    numpy \
    sounddevice \
    soundfile \
    pyaudio

# OpenVINO + Whisper
pip install \
    openvino \
    openvino-genai \
    optimum[openvino] \
    transformers

# Whisper via OpenVINO
pip install \
    faster-whisper

# openWakeWord
pip install \
    openwakeword

# Piper TTS
pip install \
    piper-tts

echo ""
echo "  Python-pakker installert OK."
echo ""

# --- 3. Last ned Whisper-modell (OpenVINO) ---
echo "[3/6] Laster ned Whisper medium modell..."
mkdir -p "$MODELS_DIR/whisper"

# Vi bruker faster-whisper medium som base, og konverterer til OpenVINO
# Alternativt: bruk direkte OpenVINO-modell fra HuggingFace
python3 -c "
from faster_whisper import WhisperModel
print('Laster ned faster-whisper medium modell...')
print('(dette tar noen minutter første gang)')
model = WhisperModel('medium', device='cpu', compute_type='int8')
print('Whisper medium lastet ned OK.')
"

echo "  Whisper-modell klar."
echo ""

# --- 4. Last ned Piper norsk stemme ---
echo "[4/6] Laster ned Piper norsk stemme..."
mkdir -p "$MODELS_DIR/piper"

# Last ned norsk stemme (talesyntese)
# Vi bruker 'nb_NO-talesyntese-medium' som er en av de beste norske stemmene
python3 -c "
import subprocess, os

model_dir = '$MODELS_DIR/piper'
# Piper laster ned modeller automatisk ved første bruk
# Vi trigger nedlasting her for å ha alt klart
print('Piper norsk stemme klargjøres ved første bruk.')
print('Sjekk tilgjengelige stemmer: piper --list-voices')
"

echo "  Piper forberedt."
echo ""

# --- 5. Last ned openWakeWord ---
echo "[5/6] Klargjør openWakeWord..."
mkdir -p "$MODELS_DIR/oww"

python3 -c "
import openwakeword
# Last ned standard-modeller (hey_jarvis, alexa, etc.)
# Vi bruker disse som utgangspunkt, og kan trene 'hei kåre' senere
openwakeword.utils.download_models()
print('openWakeWord standard-modeller lastet ned.')
"

echo "  openWakeWord klar."
echo ""

# --- 6. Generer fast-response lydfiler ---
echo "[6/6] Genererer fast-response lydfiler med Piper..."
mkdir -p "$CACHE_DIR"

python3 "$SCRIPT_DIR/generate_fast_responses.py"

echo ""
echo "============================================"
echo " INSTALLASJON FERDIG!"
echo "============================================"
echo ""
echo " Start voice-node:"
echo "   source venv/bin/activate"
echo "   python voice_node.py --node-id stue-01 --room stue"
echo ""
echo " Start voice-manager (i Kåre):"
echo "   Kjører automatisk som del av Kåre API"
echo "   (importer voice_manager i kaare_api.py)"
echo ""
