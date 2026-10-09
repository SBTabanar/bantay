#!/usr/bin/env bash
# LoRA fine-tune of the Bantay classifier on data/mlx. Env: MODEL, ITERS, BATCH, LAYERS, ADAPTER.
set -euo pipefail
cd "$(dirname "$0")/.."
MODEL=${MODEL:-mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit}
ITERS=${ITERS:-800}
BATCH=${BATCH:-4}
LAYERS=${LAYERS:-8}
ADAPTER=${ADAPTER:-adapters/bantay-lora}
mkdir -p "$ADAPTER"
.venv/bin/python -m mlx_lm lora --model "$MODEL" --train --data data/mlx --adapter-path "$ADAPTER" \
  --iters "$ITERS" --batch-size "$BATCH" --num-layers "$LAYERS" --learning-rate 1e-4 --steps-per-eval 100 \
  --steps-per-report 20 --max-seq-length 1024 --mask-prompt --grad-checkpoint --seed 7 2>&1 | tee "$ADAPTER/train.log"
