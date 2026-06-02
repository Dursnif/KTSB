"""
Speaker recognition using WeSpeaker VoxCeleb ResNet34-LM (ONNX).
Enrollment: extract embedding from audio, save per-user voiceprint.
Identification: compare live audio against stored voiceprints.
"""
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("speaker_recognition")

_speaker_model = None


def _get_model():
    global _speaker_model
    if _speaker_model is None:
        import wespeakerruntime as wespeaker
        _speaker_model = wespeaker.Speaker(lang="en")
        log.info("WeSpeaker ResNet34-LM model loaded.")
    return _speaker_model


def extract_embedding(audio_16k: np.ndarray) -> np.ndarray:
    """
    Extract speaker embedding from 16 kHz mono float32 numpy array.

    Replicates wespeakerruntime._compute_fbank using torchaudio.compliance.kaldi.fbank,
    which accepts tensors directly and does not require torchcodec (torchaudio 2.10+).
    """
    import torch
    import torchaudio.compliance.kaldi as kaldi

    model = _get_model()

    # (1, N) float32 — wespeakerruntime scales float audio to int16 range before fbank
    waveform = torch.from_numpy(audio_16k).unsqueeze(0).float() * (1 << 15)

    feats = kaldi.fbank(
        waveform,
        num_mel_bins=80,
        frame_length=25,
        frame_shift=10,
        dither=0.0,
        sample_frequency=16000,
        window_type="hamming",
        use_energy=False,
    )  # (T, 80)
    feats = feats.unsqueeze(0)  # (1, T, 80)

    embedding = model.session.run(
        output_names=["embs"],
        input_feed={"feats": feats.numpy()},
    )
    return np.array(embedding[0][0], dtype=np.float32)


def save_voiceprint(username: str, embedding: np.ndarray, enrollment_dir: Path) -> None:
    """Save speaker embedding to disk as voiceprint.npy."""
    user_dir = enrollment_dir / username
    user_dir.mkdir(parents=True, exist_ok=True)
    np.save(str(user_dir / "voiceprint.npy"), embedding)
    log.info("Voiceprint saved for user '%s'.", username)


def load_all_voiceprints(enrollment_dir: Path) -> dict[str, np.ndarray]:
    """Load all stored voiceprints from enrollment dir."""
    result: dict[str, np.ndarray] = {}
    if not enrollment_dir.exists():
        return result
    for user_dir in enrollment_dir.iterdir():
        vp = user_dir / "voiceprint.npy"
        if vp.exists():
            try:
                result[user_dir.name] = np.load(str(vp))
            except Exception as exc:
                log.warning("Could not load voiceprint for '%s': %s", user_dir.name, exc)
    return result


def identify(
    audio_16k: np.ndarray,
    enrollment_dir: Path,
    threshold: float = 0.75,
) -> tuple[str | None, str | None, float]:
    """
    Identify speaker from 16 kHz mono audio.
    Returns (confirmed_user, best_user, best_score).
    confirmed_user is set only when best_score >= threshold.
    best_user is always the closest match (or None if no enrollments).
    """
    try:
        embedding = extract_embedding(audio_16k)
        voiceprints = load_all_voiceprints(enrollment_dir)
        if not voiceprints:
            return None, None, 0.0

        model = _get_model()
        best_user: str | None = None
        best_score = 0.0

        for username, stored in voiceprints.items():
            score = float(model.compute_cosine_score(embedding, stored))
            if score > best_score:
                best_score = score
                best_user = username

        if best_score >= threshold:
            log.info("Speaker identified: %s (score=%.3f)", best_user, best_score)
            return best_user, best_user, best_score

        log.info(
            "Speaker not identified (best=%s score=%.3f threshold=%.2f)",
            best_user, best_score, threshold,
        )
        return None, best_user, best_score

    except Exception as exc:
        log.error("Speaker identification error: %s", exc)
        return None, None, 0.0


def has_voiceprint(username: str, enrollment_dir: Path) -> bool:
    return (enrollment_dir / username / "voiceprint.npy").exists()


def delete_voiceprint(username: str, enrollment_dir: Path) -> bool:
    vp = enrollment_dir / username / "voiceprint.npy"
    if vp.exists():
        vp.unlink()
        log.info("Voiceprint deleted for user '%s'.", username)
        return True
    return False
