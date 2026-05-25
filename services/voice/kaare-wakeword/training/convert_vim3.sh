#!/bin/bash
# Convert TFLite model to VIM3 NPU format using Khadas SDK
# Requires Khadas VIM3 NPU toolkit installed

set -e

MODEL="${1:-models/wakeword.tflite}"
OUTPUT="${2:-models/wakeword_vim3.nb}"

if [ ! -f "$MODEL" ]; then
    echo "Error: Model not found: $MODEL"
    exit 1
fi

echo "Converting $MODEL to VIM3 NPU format..."

# Method 1: TFLite → ONNX → VIM3 .nb
# Requires tflite2onnx and Khadas acuity toolkit

# First convert to ONNX
echo "Step 1: Converting TFLite to ONNX..."
python3 -c "
from tflite2onnx import convert
import sys
try:
    convert('$MODEL', '$MODEL.onnx')
    print('ONNX conversion successful')
except Exception as e:
    print(f'ONNX conversion failed: {e}')
    sys.exit(1)
"

# Then convert ONNX to VIM3 .nb using Khadas tools
echo "Step 2: Converting ONNX to VIM3 .nb..."
echo "This requires Khadas VIM3 NPU SDK"
echo "See: https://docs.khadas.com/products/sbc/vim3/npu/convert-onnx"
echo ""
echo "Commands to run on a system with Khadas SDK:"
echo "  1_quantize_model.sh $MODEL.onnx"
echo "  2_convert_model.sh $MODEL.onnx_quantized"
echo ""
echo "Or use TFLite directly with Verisilicon delegate on VIM3"
echo "(no conversion needed for TFLite runtime)"

echo ""
echo "Note: For VIM3, you can also use TFLite directly:"
echo "  - Install: pip3 install ksnn-1.4-py3-none-any.whl"
echo "  - The TFLite model will use Verisilicon NPU delegate"
