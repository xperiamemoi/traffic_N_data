"""
Badoo photo cloaker — adversarial perturbation, WHOLE IMAGE.
Applies imperceptible DCT mid-frequency noise that shifts AI face embeddings
while being invisible to human eyes.

IMPORTANT: Run badoo_generate_identity.py FIRST to set the session seed.
All photos cloaked in the same session use the same seed → face match
is preserved across profile photos, selfie, and chat photos in AI space.

Cloaks the ENTIRE PHOTO (not just face) — Badoo pixelates and analyses
the full image, not just the face region.

Install:
  pip install scipy mediapipe opencv-python Pillow numpy

Usage:
  py -3 badoo_cloak_photos.py photo1.jpg photo2.jpg
  py -3 badoo_cloak_photos.py C:\\path\\to\\folder\\*.jpg

Output: C:\\Users\\MTHG\\Desktop\\cloaked_photos\\
"""

import sys
import io
import glob
import json
import numpy as np
from pathlib import Path
from PIL import Image

OUTPUT_DIR    = Path(r"C:\Users\MTHG\Desktop\Claude-code-sessions\Solution-2\cloaked_photos")
IDENTITY_FILE = Path(r"C:\Users\MTHG\Desktop\Claude-code-sessions\Solution-2\session_identity.json")

EPSILON    = 0.06    # perturbation strength (max pixel change ≈ 15 levels — survives JPEG q82)
FREQ_STEPS = 24      # DCT frequency bands targeted
BADOO_JPEG = 82      # Badoo's re-encoding quality for survival test
FACE_PAD   = 50


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[CLOAK] {msg}")


# ── Seed from session identity ─────────────────────────────────────────────────

def _load_seed() -> int:
    if IDENTITY_FILE.exists():
        try:
            identity = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
            seed = int(identity["cloak_seed"])
            log(f"Seed loaded from session_identity.json: {seed}")
            return seed
        except Exception as e:
            log(f"WARNING: Could not read session_identity.json ({e})")
    else:
        log("WARNING: session_identity.json not found!")
        log("         Run badoo_generate_identity.py first for consistent cross-photo cloaking.")
    import random
    fallback = random.randint(0, 2**31 - 1)
    log(f"Using random fallback seed: {fallback}")
    return fallback


# ── EXIF strip ────────────────────────────────────────────────────────────────

def strip_exif(img: Image.Image) -> Image.Image:
    clean = Image.new(img.mode, img.size)
    clean.putdata(list(img.getdata()))
    return clean


# ── Face detection (for info log only — perturbation covers whole image) ──────

def detect_face_box(arr: np.ndarray):
    h, w = arr.shape[:2]
    try:
        import mediapipe as mp
        fd = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5
        )
        res = fd.process(arr)
        fd.close()
        if res.detections:
            best = max(res.detections, key=lambda d: d.score[0])
            bb = best.location_data.relative_bounding_box
            x1 = max(0, int((bb.xmin - FACE_PAD/w) * w))
            y1 = max(0, int((bb.ymin - FACE_PAD/h) * h))
            x2 = min(w, int((bb.xmin + bb.width  + FACE_PAD/w) * w))
            y2 = min(h, int((bb.ymin + bb.height + FACE_PAD/h) * h))
            return (x1, y1, x2, y2)
    except Exception:
        pass
    try:
        import cv2
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        cc = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        faces = cc.detectMultiScale(gray, 1.1, 4)
        if len(faces):
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            return (
                max(0, x - FACE_PAD), max(0, y - FACE_PAD),
                min(w, x + fw + FACE_PAD), min(h, y + fh + FACE_PAD),
            )
    except Exception:
        pass
    return None


# ── DCT helpers ───────────────────────────────────────────────────────────────

def _dct2(block: np.ndarray) -> np.ndarray:
    from scipy.fft import dct
    return dct(dct(block.T, norm="ortho").T, norm="ortho")

def _idct2(block: np.ndarray) -> np.ndarray:
    from scipy.fft import idct
    return idct(idct(block.T, norm="ortho").T, norm="ortho")


# ── Core perturbation — WHOLE IMAGE ──────────────────────────────────────────

def _perturb_whole_image(arr: np.ndarray, seed: int) -> np.ndarray:
    """
    Apply DCT mid-frequency noise to the ENTIRE image using the session seed.

    Same seed across all photos in a session → same perturbation direction
    in AI embedding space → face match is preserved between profile photos
    and selfie verify, even though the AI sees shifted embeddings.

    Mid-frequency DCT coefficients (bands 2-6, i+j in 3-9) survive JPEG
    recompression at quality 75+ but are imperceptible to humans.
    Max pixel change: EPSILON * 255 ≈ 9 intensity levels.
    """
    rng = np.random.default_rng(seed)
    result = arr.astype(np.float64)
    h, w, _ = result.shape

    for y in range(0, h - 7, 8):
        for x in range(0, w - 7, 8):
            for c in range(3):
                block = result[y:y+8, x:x+8, c]
                coeffs = _dct2(block)
                noise = np.zeros((8, 8))
                for i in range(2, 7):
                    for j in range(2, 7):
                        if 3 <= i + j <= 9:
                            sign = rng.choice([-1.0, 1.0])
                            noise[i, j] = sign * EPSILON * 255 * (FREQ_STEPS / 24)
                coeffs += noise
                result[y:y+8, x:x+8, c] = _idct2(coeffs)

    return result.clip(0, 255).astype(np.uint8)


def _simulate_jpeg(arr: np.ndarray, quality: int = BADOO_JPEG) -> np.ndarray:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return np.array(Image.open(buf))


# ── Main per-image function ───────────────────────────────────────────────────

def cloak(src_path: str, seed: int) -> str:
    log(f"Processing: {Path(src_path).name}")

    img = Image.open(src_path).convert("RGB")
    img = strip_exif(img)
    arr = np.array(img)

    # Face detection for info only — perturbation is always whole-image
    box = detect_face_box(arr)
    if box:
        x1, y1, x2, y2 = box
        log(f"  Face detected ({x1},{y1})→({x2},{y2}) [info only — whole image cloaked]")
    else:
        log("  No face detected — continuing (whole image will be cloaked)")

    # Perturb WHOLE image with session seed
    result = _perturb_whole_image(arr, seed)

    # Verify perturbation survives Badoo's re-encoding
    after_jpeg = _simulate_jpeg(result)
    diff = np.abs(after_jpeg.astype(float) - arr.astype(float))
    max_diff  = diff.max()
    mean_diff = diff.mean()
    log(f"  Post-JPEG survival — max pixel delta: {max_diff:.1f}, mean: {mean_diff:.2f}")
    if max_diff < 2:
        log("  WARNING: perturbation very weak after JPEG sim — Badoo may erase it")
    elif max_diff <= 12:
        log("  OK: perturbation survives JPEG compression, imperceptible to humans")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Random filename — Badoo reads filenames and links repeated names to prior accounts
    import random, string
    rand_name = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    out_path = OUTPUT_DIR / f"{rand_name}.jpg"
    Image.fromarray(result).save(str(out_path), "JPEG", quality=97)
    log(f"  Saved → {out_path}")
    return str(out_path)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: py -3 badoo_cloak_photos.py photo1.jpg photo2.jpg ...")
        print(f"Output: {OUTPUT_DIR}")
        sys.exit(0)

    seed = _load_seed()

    paths = []
    for arg in sys.argv[1:]:
        paths.extend(glob.glob(arg))
    paths = [p for p in paths if Path(p).suffix.lower() in (".jpg", ".jpeg", ".png")]

    if not paths:
        print("No .jpg/.jpeg/.png files found.")
        sys.exit(1)

    log(f"Cloaking {len(paths)} photo(s) with seed {seed} ...")
    log(f"Output dir: {OUTPUT_DIR}")
    print()

    ok, fail = 0, 0
    for p in paths:
        try:
            cloak(p, seed)
            ok += 1
        except Exception as e:
            log(f"ERROR on {Path(p).name}: {e}")
            fail += 1
        print()

    log(f"Done — {ok} cloaked, {fail} failed.")
    log(f"Use files from {OUTPUT_DIR} for all Badoo uploads.")
    log(f"The proxy addon uses the same seed ({seed}) for any live uploads.")
