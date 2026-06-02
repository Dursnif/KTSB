"""Analyze dataset balance before training.

Shows distribution of positive/negative/background samples.
Suggests if more negative samples are needed.

Usage:
    python -m scripts.analyze_data
"""

from __future__ import annotations

from pathlib import Path


def analyze_dataset() -> None:
    """Count samples in each dataset directory."""
    data_dir = Path("data")

    # Count positive ("Kåre")
    positive_dir = data_dir / "positive"
    if positive_dir.exists():
        positive_count = len(list(positive_dir.glob("*.wav")))
    else:
        positive_count = 0

    # Count negative (other words)
    negative_dir = data_dir / "negative"
    if negative_dir.exists():
        negative_count = len(list(negative_dir.glob("*.wav")))
    else:
        negative_count = 0

    # Count background
    background_dir = data_dir / "background"
    if background_dir.exists():
        background_count = len(list(background_dir.glob("*.wav")))
    else:
        background_count = 0

    # Show results
    print("\n" + "="*50)
    print("Distribusjon av datasett")
    print("="*50)
    print(f"\nPositive (\"Kåre\"):  {positive_count:4d}")
    print(f"Negative (andre ord): {negative_count:4d}")
    print(f"Background (støy):    {background_count:4d}")
    print(f"\nTotalt:                {positive_count + negative_count + background_count:4d}")
    print("="*50)

    # Check balance
    print("\nBalance-analyse:")
    if positive_count > 0:
        ratio = negative_count / positive_count
        print(f"  Negativ/Positiv ratio: {ratio:.2f}")

        if ratio < 1.0:
            print(f"  ⚠️  NEGATIV SAMPLE ER FOR LITE!")
            needed = int(positive_count * 1.5) - negative_count
            print(f"      Trenger ca. {needed} flere negative opptak")
            print(f"      Kjør: python -m scripts.record_negative --count {needed}")
        elif ratio > 3.0:
            print(f"  ⚠️  NEGATIV SAMPLE ER FOR MYE!")
            print(f"      Vurder å redusere eller øke positive")
        else:
            print(f"  ✅ Bra balans!")
    else:
        print("  ⚠️  Ingen positive opptak ennå")

    # Background recommendations
    total_audio = positive_count + negative_count
    if background_count == 0:
        print(f"\n⚠️  Ingen bakgrunnsstøy!")
        print("      Ta opp 5-10 minutter støy fra hvert rom")
    elif background_count < 10:
        print(f"\n⚠️  Lite bakgrunnsstøy ({background_count} filer)")
        print("      Anbefaler 5-10 minutter totalt")
    else:
        duration_min = background_count * 1.5 / 60
        print(f"\n✅ Bakgrunnsstøy: ca. {duration_min:.1f} minutter")


if __name__ == "__main__":
    analyze_dataset()
