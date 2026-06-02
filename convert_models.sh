#!/bin/bash
set -e

OUTPUT_BASE="./models"
mkdir -p "$OUTPUT_BASE"

echo "=== Installerer optimum + openvino ==="
pip install "optimum[openvino]" "nncf" --quiet

declare -A MODELS=(
  ["0.8b"]="huihui-ai/Huihui-Qwen3.5-0.8B-abliterated"
  ["2b"]="huihui-ai/Huihui-Qwen3.5-2B-abliterated"
  ["4b"]="huihui-ai/Huihui-Qwen3.5-4B-abliterated"
)

for size in "0.8b" "2b" "4b"; do
  model="${MODELS[$size]}"
  output="$OUTPUT_BASE/qwen35_${size}_ov_int4"

  if [ -d "$output" ]; then
    echo "=== $size allerede konvertert, hopper over ==="
    continue
  fi

  echo ""
  echo "=== Konverterer $size ($model) ==="
  optimum-cli export openvino \
    --model "$model" \
    --weight-format int4 \
    --group-size 128 \
    --task text-generation-with-past \
    --trust-remote-code \
    "$output"

  echo "=== $size ferdig → $output ==="
done

echo ""
echo "Alle modeller konvertert:"
du -sh "$OUTPUT_BASE"/*/
