#!/usr/bin/env bash
# Full pipeline: augment → MFCC → combine → train → export
#
# Usage:
#   ./prepare.sh                    # full pipeline, export for mac
#   ./prepare.sh --target coral     # export for coral
#   ./prepare.sh --target all       # export for all targets
#   ./prepare.sh --quick            # skip augment
#   FACTOR=5 ./prepare.sh           # 5x augmentation
#
# Targets: mac, rpi, coral, vim3, all
set -euo pipefail

FACTOR=${FACTOR:-3}
TARGET="mac"
QUICK=false

# Parse args
for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
        --target) :;; # next arg is the target
        mac|rpi|coral|vim3|all)  TARGET="$arg" ;;
    esac
done

echo "=== Raw data ==="
echo "  positive:   $(ls data/positive/*.wav 2>/dev/null | wc -l | tr -d ' ') files"
echo "  negative:   $(ls data/negative/*.wav 2>/dev/null | wc -l | tr -d ' ') files"
echo "  background: $(ls data/background/*.wav 2>/dev/null | wc -l | tr -d ' ') files"
echo "  target:     $TARGET"

# Augment
if [[ "$QUICK" == false ]]; then
    echo ""
    echo "=== Augmenting (factor=$FACTOR) ==="
    uv run python -m scripts.augment --source data/positive/   --output data/augmented/            --factor "$FACTOR"
    uv run python -m scripts.augment --source data/negative/   --output data/augmented_negative/   --factor "$FACTOR"
    uv run python -m scripts.augment --source data/background/ --output data/augmented_background/ --factor "$FACTOR"
fi

# MFCC
echo ""
echo "=== Extracting MFCC ==="
uv run python -m scripts.preprocess --input data/positive/   --output data/mfcc_positive_raw.npy
uv run python -m scripts.preprocess --input data/negative/   --output data/mfcc_negative_raw.npy
uv run python -m scripts.preprocess --input data/background/ --output data/mfcc_background_raw.npy

if [[ "$QUICK" == false ]]; then
    uv run python -m scripts.preprocess --input data/augmented/            --output data/mfcc_positive_aug.npy
    uv run python -m scripts.preprocess --input data/augmented_negative/   --output data/mfcc_negative_aug.npy
    uv run python -m scripts.preprocess --input data/augmented_background/ --output data/mfcc_background_aug.npy
fi

# Combine
echo ""
echo "=== Combining ==="
uv run python -c "
import numpy as np
for name in ['positive', 'negative', 'background']:
    parts = []
    for suffix in ['raw', 'aug']:
        path = f'data/mfcc_{name}_{suffix}.npy'
        try:
            parts.append(np.load(path))
        except FileNotFoundError:
            pass
    combined = np.concatenate(parts)
    np.save(f'data/mfcc_{name}_all.npy', combined)
    print(f'  {name}: {len(combined)} samples')
"

# Train
echo ""
echo "=== Training ==="
uv run python -m training.train \
    --positive data/mfcc_positive_all.npy \
    --negative data/mfcc_negative_all.npy \
    --background data/mfcc_background_all.npy

# Export
echo ""
echo "=== Exporting ($TARGET) ==="
uv run python -m training.export_tflite --target "$TARGET"

echo ""
echo "=== Done! ==="
case "$TARGET" in
    mac)   echo "  uv run python -m inference.run_coral --model models/wakeword_mac.tflite --device 0" ;;
    rpi)   echo "  python -m inference.run_coral --model models/wakeword_rpi.tflite --device 0" ;;
    coral) echo "  # Kompiler først: edgetpu_compiler models/wakeword_coral_int8.tflite"
           echo "  python -m inference.run_coral --model models/wakeword_coral_int8_edgetpu.tflite --device 0" ;;
    vim3)  echo "  python -m inference.run_vim3 --model models/wakeword_vim3_int8.tflite --device 0" ;;
    all)   echo "  Alle modeller eksportert til models/" ;;
esac
