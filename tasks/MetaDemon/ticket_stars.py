"""Verify a matched ticket by measuring its six illuminated star positions."""
import cv2
import numpy as np


def recognize_ticket_stars(image, area, *, synthesis=False) -> int | None:
    x, y, width, height = map(int, area)
    if (image is None or image.ndim != 3 or image.shape[2] != 3
            or min(width, height) <= 0 or x < 0 or y < 0
            or x + width > image.shape[1] or y + height > image.shape[0]):
        return None
    card = cv2.resize(image[y:y + height, x:x + width], (72, 72), interpolation=cv2.INTER_LINEAR)
    strip = cv2.cvtColor(card[55:70], cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(strip, (5, 110, 145), (35, 255, 255))
    # Convolve each six-pixel-wide star slot to obtain its orange pixel fraction.
    fractions = cv2.filter2D(
        (mask > 0).astype(np.float32), -1, np.ones((15, 6), np.float32) / 90,
        anchor=(3, 7),
    )
    # Synthesis templates crop the card more tightly, so their star row starts
    # closer to the left edge than the summon templates.
    centers = (4, 16, 28, 40, 52, 64) if synthesis else (11, 21, 31, 41, 51, 61)
    values = [float(fractions[7, center]) for center in centers]
    if any(0.08 < value < 0.24 for value in values):
        return None
    illuminated = [value >= 0.24 for value in values]
    stars = sum(illuminated)
    if stars == 0 or illuminated != [True] * stars + [False] * (6 - stars):
        return None
    return stars
