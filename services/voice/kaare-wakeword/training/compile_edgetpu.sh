#!/bin/bash
# Compile TFLite model for Google Coral Edge TPU
# Requires edgetpu-compiler installed on Linux x86_64

set -e

MODEL="${1:-models/wakeword.tflite}"
OUTPUT="${2:-models/wakeword_edgetpu.tflite}"

if [ ! -f "$MODEL" ]; then
    echo "Error: Model not found: $MODEL"
    exit 1
fi

echo "Compiling $MODEL for Edge TPU..."
edgetpu_compiler "$MODEL" -o "$(dirname "$OUTPUT")"

# Check if compiled model was created
if [ ! -f "$OUTPUT" ]; then
    # edgetpu_compiler might use different naming
    COMPILED=$(echo "$MODEL" | sed 's/\.tflite$/_edgetpu.tflite/')
    if [ -f "$COMPILED" ]; then
        mv "$COMPILED" "$OUTPUT"
    else
        echo "Error: Failed to compile for Edge TPU"
        exit 1
    fi
fi

echo "Edge TPU model compiled: $OUTPUT"
