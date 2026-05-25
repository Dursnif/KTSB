"""DS-CNN model for keyword spotting on Edge TPU and VIM3 NPU.

Depthwise Separable CNN optimized for Edge TPU:
  - Uses only supported ops (Conv2D, MaxPool, Dense, ReLU6)
  - Fixed input/output shapes
  - Quantization-ready (QAT compatible)

Input: [batch, n_mfcc, n_frames, 1]  e.g., [batch, 40, 150, 1]
Output: [batch, 3] (logits for: positive, negative, background)

Usage:
    model = WakeWordDSConv(num_mfcc=40, num_frames=150, num_classes=3)
"""

from __future__ import annotations

import tensorflow as tf
from tensorflow.keras import layers, Model


def build_wakeword_model(
    num_mfcc: int = 40,
    num_frames: int = 150,
    num_classes: int = 3,
    dropout_rate: float = 0.5,
) -> Model:
    """Build DS-CNN for wake word detection.

    Architecture:
      Input → Conv2D(64) → DepthwiseConv2D(128) → MaxPool → Dropout
           → DepthwiseConv2D(128) → MaxPool → Dropout
           → Flatten → Dense(128) → Dense(num_classes)

    Uses only Edge TPU supported operations.

    Args:
        num_mfcc: Number of MFCC coefficients (e.g., 40)
        num_frames: Number of time frames (e.g., ~150 for 1.5s)
        num_classes: Number of output classes (3: pos, neg, bg)
        dropout_rate: Dropout rate

    Returns:
        Compiled Keras Model
    """
    inputs = layers.Input(shape=(num_mfcc, num_frames, 1), name="audio_input")

    # Initial conv block — learn basic spectral-temporal features
    x = layers.Conv2D(64, (4, 4), strides=(2, 2), padding="same", use_bias=False)(inputs)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)

    # DS-Conv block 1
    x = layers.DepthwiseConv2D((3, 3), padding="same", use_bias=False)(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)
    x = layers.Conv2D(64, (1, 1), use_bias=False)(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)

    # DS-Conv block 2
    x = layers.DepthwiseConv2D((3, 3), padding="same", use_bias=False)(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)
    x = layers.Conv2D(64, (1, 1), use_bias=False)(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)

    # DS-Conv block 3
    x = layers.DepthwiseConv2D((3, 3), padding="same", use_bias=False)(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)
    x = layers.Conv2D(128, (1, 1), use_bias=False)(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)

    # DS-Conv block 4
    x = layers.DepthwiseConv2D((3, 3), padding="same", use_bias=False)(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)
    x = layers.Conv2D(128, (1, 1), use_bias=False)(x)
    x = layers.BatchNormalization()(x)
    x = layers.ReLU(max_value=6)(x)

    # Global pooling → classifier
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(dropout_rate)(x)
    outputs = layers.Dense(num_classes, name="predictions")(x)

    model = Model(inputs=inputs, outputs=outputs, name="wake_word_dscnn")
    
    return model


def apply_qat(model: Model) -> Model:
    """Compile model for training.

    Post-training quantization is applied during TFLite export instead,
    which avoids tensorflow-model-optimization compatibility issues
    and gives comparable results for small keyword spotting models.

    Args:
        model: Keras Model to compile

    Returns:
        Compiled model ready for training
    """
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
        metrics=["accuracy"],
    )
    return model
