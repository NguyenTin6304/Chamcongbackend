"""Phase 4.1 — Basic image quality validation using Pillow only.

No ML/face-detection at this phase. Phase 4.2 will add face-api.js embedding.
Checks: minimum resolution, brightness range, non-blank (std deviation).
"""
import io
import statistics


def validate_face_image(image_bytes: bytes) -> tuple[bool, str | None]:
    """Return (is_valid, reason_or_None).

    Fails fast with the first violated constraint.
    Uses only stdlib + Pillow (already a transitive dependency).
    """
    try:
        from PIL import Image  # type: ignore[import]
    except ImportError:
        # Pillow not installed — skip quality check, accept image
        return True, None

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()  # detect truncated / corrupt files
        # Re-open after verify() because verify() exhausts the stream
        img = Image.open(io.BytesIO(image_bytes)).convert("L")  # grayscale
    except Exception:
        return False, "Ảnh bị lỗi hoặc không đọc được."

    width, height = img.size
    if width < 200 or height < 200:
        return False, f"Ảnh quá nhỏ ({width}×{height}px). Cần tối thiểu 200×200px."

    pixels = list(img.getdata())
    mean = sum(pixels) / len(pixels)

    if mean < 40:
        return False, "Ảnh quá tối. Vui lòng chụp ở nơi đủ sáng."
    if mean > 245:
        return False, "Ảnh quá sáng (overexposed). Vui lòng tránh nguồn sáng trực tiếp."

    # Blank / solid-color detection via std deviation
    try:
        std = statistics.stdev(pixels)
    except statistics.StatisticsError:
        std = 0.0
    if std < 15:
        return False, "Ảnh trông như bị che hoặc trống. Vui lòng chụp lại."

    return True, None
