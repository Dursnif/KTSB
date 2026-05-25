#!/usr/bin/env bash


uv run python -m scripts.augment --source data/positive/ --output data/augmented/ --factor 3

# 2. Preprosesser alle tre klassene til MFCC
uv run python -m scripts.preprocess --input data/positive/ --output data/mfcc_positive.npy
uv run python -m scripts.preprocess --input data/negative/ --output data/mfcc_negative.npy
uv run python -m scripts.preprocess --input data/background/ --output data/mfcc_background.npy

# 3. Preprosesser augmenterte også
uv run python -m scripts.preprocess --input data/augmented/ --output data/mfcc_augmented.npy

echo "[+] Done"

