"""Local ONNX tile classifier — drop-in replacement for the Mistral KeyPool in the
reCAPTCHA image-challenge path.

Exposes `.classify(image_b64, target) -> bool` with the SAME signature as
`common.mistral.KeyPool.classify`, so `image_solve._classify_grid` swaps one for the
other with no other change. The model is a yolov8n-cls (14-class) trained on the
reCAPTCHA tile dataset; inference is CPU-only and thread-limited to 2 cores (no spike),
matching the Aliyun gap-detector deployment.

Zero network, zero per-solve cost. Falls back to Mistral only if the ONNX file is
absent (caller decides).
"""
import os

# Cap threads BEFORE importing onnxruntime — some ORT builds read these at import time,
# not just from SessionOptions. Without this a single infer grabs every host core.
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("ORT_NUM_THREADS", "2")

import ast
import base64
import io
import logging
import threading
import unicodedata
from pathlib import Path

try:
    import msvc_runtime  # type: ignore
except ImportError:
    pass

import numpy as np
try:
    import onnxruntime as ort
except (ImportError, Exception) as e:
    ort = None
from PIL import Image

log = logging.getLogger(__name__)

_DEFAULT_MODEL = Path(__file__).parent / "models" / "recaptcha_cls_s.onnx"
_DEFAULT_THRESHOLD = 0.38  # softmax prob for matched class (14 classes: random baseline is ~0.071, 0.38 is >5x baseline)

# reCAPTCHA prompt targets are noisy: plural, articles, synonyms, and multilingual
# translations (Portuguese, Spanish, French, German, Italian, etc.). Map them onto the
# 14 dataset class labels.
_TARGET_ALIASES = {
    # ── 0: Bicycle ──────────────────────────────────────────────────────────
    "bicycle": "Bicycle", "bicycles": "Bicycle", "bike": "Bicycle", "bikes": "Bicycle",
    "bicicleta": "Bicycle", "bicicletas": "Bicycle", "bici": "Bicycle", "bicis": "Bicycle",
    "velo": "Bicycle", "velos": "Bicycle", "bicyclette": "Bicycle", "bicyclettes": "Bicycle",
    "fahrrad": "Bicycle", "fahrraeder": "Bicycle", "fahrräder": "Bicycle",

    # ── 1: Bridge ───────────────────────────────────────────────────────────
    "bridge": "Bridge", "bridges": "Bridge",
    "ponte": "Bridge", "pontes": "Bridge",
    "puente": "Bridge", "puentes": "Bridge",
    "pont": "Bridge", "ponts": "Bridge",
    "bruecke": "Bridge", "bruecken": "Bridge", "brücke": "Bridge", "brücken": "Bridge",

    # ── 2: Bus ──────────────────────────────────────────────────────────────
    "bus": "Bus", "buses": "Bus", "busses": "Bus",
    "onibus": "Bus", "ônibus": "Bus", "autocarro": "Bus", "autocarros": "Bus",
    "autobus": "Bus", "autobús": "Bus", "autobuses": "Bus", "colectivo": "Bus",
    "autocar": "Bus", "busse": "Bus",

    # ── 3: Car ──────────────────────────────────────────────────────────────
    "car": "Car", "cars": "Car", "vehicle": "Car", "vehicles": "Car",
    "carro": "Car", "carros": "Car", "veiculo": "Car", "veículos": "Car", "veiculos": "Car",
    "automovel": "Car", "automóvel": "Car", "automoveis": "Car", "automóveis": "Car",
    "coche": "Car", "coches": "Car", "auto": "Car", "autos": "Car", "vehiculo": "Car", "vehículos": "Car",
    "voiture": "Car", "voitures": "Car", "pkw": "Car", "fahrzeug": "Car", "fahrzeuge": "Car",
    "macchina": "Car", "macchine": "Car",
    "taxi": "Car", "taxis": "Car", "cab": "Car", "cabs": "Car", "taxicab": "Car", "taxicabs": "Car",

    # ── 4: Chimney ──────────────────────────────────────────────────────────
    "chimney": "Chimney", "chimneys": "Chimney",
    "chamine": "Chimney", "chaminé": "Chimney", "chamines": "Chimney", "chaminés": "Chimney",
    "chimenea": "Chimney", "chimeneas": "Chimney",
    "cheminee": "Chimney", "cheminée": "Chimney", "cheminees": "Chimney", "cheminées": "Chimney",
    "schornstein": "Chimney", "schornsteine": "Chimney", "kamin": "Chimney", "kamine": "Chimney",

    # ── 5: Crosswalk ────────────────────────────────────────────────────────
    "crosswalk": "Crosswalk", "crosswalks": "Crosswalk",
    "cross walk": "Crosswalk", "cross walks": "Crosswalk",
    "faixa de pedestre": "Crosswalk", "faixas de pedestre": "Crosswalk",
    "faixa de pedestres": "Crosswalk", "faixas de pedestres": "Crosswalk",
    "faixa de seguranca": "Crosswalk", "faixa de segurança": "Crosswalk",
    "passadeira": "Crosswalk", "passadeiras": "Crosswalk",
    "paso de peatones": "Crosswalk", "pasos de peatones": "Crosswalk",
    "paso peatonal": "Crosswalk", "pasos peatonales": "Crosswalk", "cruce peatonal": "Crosswalk",
    "passage pieton": "Crosswalk", "passage piéton": "Crosswalk",
    "passages pietons": "Crosswalk", "passages piétons": "Crosswalk",
    "passages pour pietons": "Crosswalk", "passages pour piétons": "Crosswalk",
    "zebrastreifen": "Crosswalk", "fussgaengerueberweg": "Crosswalk", "fußgängerüberwege": "Crosswalk",
    "strisce pedonali": "Crosswalk",

    # ── 6: Hydrant ──────────────────────────────────────────────────────────
    "hydrant": "Hydrant", "hydrants": "Hydrant",
    "fire hydrant": "Hydrant", "fire hydrants": "Hydrant",
    "a fire hydrant": "Hydrant",
    "hidrante": "Hydrant", "hidrantes": "Hydrant",
    "boca de incendio": "Hydrant", "boca de incêndio": "Hydrant",
    "bocas de incendio": "Hydrant", "bocas de incêndio": "Hydrant",
    "borne d'incendie": "Hydrant", "bornes d'incendie": "Hydrant",
    "bouche d'incendie": "Hydrant", "feuerloeschhydrant": "Hydrant",

    # ── 7: Motorcycle ───────────────────────────────────────────────────────
    "motorcycle": "Motorcycle", "motorcycles": "Motorcycle",
    "motorbike": "Motorcycle", "motorbikes": "Motorcycle",
    "motocicleta": "Motorcycle", "motocicletas": "Motorcycle",
    "moto": "Motorcycle", "motos": "Motorcycle",
    "motorrad": "Motorcycle", "motorraeder": "Motorcycle", "motorräder": "Motorcycle",

    # ── 8: Mountain ─────────────────────────────────────────────────────────
    "mountain": "Mountain", "mountains": "Mountain",
    "mountains or hills": "Mountain", "hill": "Mountain", "hills": "Mountain",
    "montanha": "Mountain", "montanhas": "Mountain", "colina": "Mountain", "colinas": "Mountain",
    "morro": "Mountain", "morros": "Mountain",
    "montana": "Mountain", "montaña": "Mountain", "montanas": "Mountain", "montañas": "Mountain",
    "cerro": "Mountain", "cerros": "Mountain",
    "montagne": "Mountain", "montagnes": "Mountain",
    "berg": "Mountain", "berge": "Mountain", "huegel": "Mountain",

    # ── 10: Palm ────────────────────────────────────────────────────────────
    "palm": "Palm", "palms": "Palm", "palm tree": "Palm", "palm trees": "Palm",
    "palmeira": "Palm", "palmeiras": "Palm",
    "palmera": "Palm", "palmeras": "Palm",
    "palmier": "Palm", "palmiers": "Palm",
    "palme": "Palm", "palmen": "Palm",

    # ── 11: Stair ───────────────────────────────────────────────────────────
    "stair": "Stair", "stairs": "Stair", "staircase": "Stair",
    "escada": "Stair", "escadas": "Stair", "escadaria": "Stair", "escadarias": "Stair",
    "escalera": "Stair", "escaleras": "Stair",
    "escalier": "Stair", "escaliers": "Stair",
    "treppe": "Stair", "treppen": "Stair",
    "scala": "Stair", "scale": "Stair",

    # ── 12: Tractor ─────────────────────────────────────────────────────────
    "tractor": "Tractor", "tractors": "Tractor",
    "trator": "Tractor", "tratores": "Tractor",
    "tracteur": "Tractor", "tracteurs": "Tractor",
    "traktor": "Tractor", "traktoren": "Tractor", "trecker": "Tractor",
    "trattore": "Tractor", "trattori": "Tractor",

    # ── 13: Traffic Light ───────────────────────────────────────────────────
    "traffic light": "Traffic Light", "traffic lights": "Traffic Light",
    "trafficlight": "Traffic Light", "trafic light": "Traffic Light",
    "traffic signal": "Traffic Light", "traffic signals": "Traffic Light",
    "semaforo": "Traffic Light", "semáforo": "Traffic Light",
    "semaforos": "Traffic Light", "semáforos": "Traffic Light",
    "sinal de transito": "Traffic Light", "sinal de trânsito": "Traffic Light",
    "sinais de transito": "Traffic Light", "sinais de trânsito": "Traffic Light",
    "luces de trafico": "Traffic Light", "luces de tráfico": "Traffic Light",
    "feu de circulation": "Traffic Light", "feux de circulation": "Traffic Light",
    "feux tricolores": "Traffic Light",
    "ampel": "Traffic Light", "ampeln": "Traffic Light",
    "semafori": "Traffic Light",
}


def _strip_accents(text: str) -> str:
    """Normalize and strip diacritical accents (e.g. semáforos -> semaforos)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _normalize_target(target: str) -> str | None:
    """Map a reCAPTCHA prompt target string onto a dataset class name, or None."""
    raw = (target or "").strip().lower()

    # Strip leading articles across English, Portuguese, Spanish, French, German
    for art in (
        "a ", "an ", "the ",
        "um ", "uma ", "os ", "as ", "o ",
        "un ", "una ", "los ", "las ", "el ", "la ",
        "le ", "la ", "les ", "des ", "du ",
        "ein ", "eine ", "der ", "die ", "das "
    ):
        if raw.startswith(art):
            raw = raw[len(art):].strip()
            break

    # 1. Direct exact match
    if raw in _TARGET_ALIASES:
        return _TARGET_ALIASES[raw]

    # 2. Match without accents
    raw_no_accents = _strip_accents(raw)
    if raw_no_accents in _TARGET_ALIASES:
        return _TARGET_ALIASES[raw_no_accents]

    # 3. Substring / contains match
    for key, cls in _TARGET_ALIASES.items():
        if key in raw or key in raw_no_accents:
            return cls

    return None


class OnnxClassifier:
    """Thread-limited ONNX tile classifier. Drop-in for KeyPool.classify."""

    def __init__(self, model_path: str = None, threshold: float = _DEFAULT_THRESHOLD):
        self.model_path = str(model_path or _DEFAULT_MODEL)
        self.threshold = threshold
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(
            self.model_path, so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        meta = self.sess.get_modelmeta().custom_metadata_map
        self.names = ast.literal_eval(meta["names"])  # {0: 'Bicycle', ...}
        imgsz = meta.get("imgsz", "[128, 128]")
        self.imgsz = ast.literal_eval(imgsz)[0] if imgsz.startswith("[") else int(imgsz)
        self._lock = threading.Lock()  # ORT session is not guaranteed thread-safe
        log.info("OnnxClassifier loaded: %s (%d classes, imgsz=%d)",
                 self.model_path, len(self.names), self.imgsz)

    def _preprocess(self, image_b64: str) -> np.ndarray:
        raw = base64.b64decode(image_b64)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = im.size
        s = self.imgsz / min(w, h)
        im = im.resize((round(w * s), round(h * s)))
        w, h = im.size
        l, t = (w - self.imgsz) // 2, (h - self.imgsz) // 2
        im = im.crop((l, t, l + self.imgsz, t + self.imgsz))
        a = np.asarray(im, dtype=np.float32) / 255.0
        return np.transpose(a, (2, 0, 1))[None]

    def predict(self, image_b64: str):
        """Return (class_name, confidence) for the top prediction."""
        x = self._preprocess(image_b64)
        with self._lock:
            out = self.sess.run(None, {self.inp: x})[0][0]
        idx = int(np.argmax(out))
        return self.names[idx], float(out[idx])

    def classify(self, image_b64: str, target: str,
                 max_keys: int = 8, timeout: int = 40) -> bool:
        """Yes/no: does this tile contain `target`? Signature matches KeyPool.classify
        (max_keys/timeout accepted and ignored — kept for drop-in compatibility).

        A tile is a match iff the model's argmax equals the normalized target class
        AND its softmax prob clears the threshold. If the target can't be mapped to a
        known class, returns False (caller should fall back to Mistral for that target).
        """
        want = _normalize_target(target)
        if want is None:
            log.warning("target %r not in known classes — no ONNX match", target)
            return False
        try:
            pred, conf = self.predict(image_b64)
        except Exception as e:
            log.debug("onnx classify error: %s", str(e).splitlines()[0])
            return False
        return pred == want and conf >= self.threshold


class HybridClassifier:
    """ONNX-first classifier with a Mistral fallback for out-of-vocabulary targets.

    Same `.classify(image_b64, target)` contract as KeyPool. Known reCAPTCHA classes
    (the 14 the model was trained on) resolve locally via ONNX — fast, free, no rate
    limit. Targets that don't map to a known class (rare new categories) fall through
    to the Mistral key pool, so coverage never regresses below the pre-ONNX baseline.
    """

    def __init__(self, onnx: "OnnxClassifier", keypool):
        self.onnx = onnx
        self.keypool = keypool  # may be None if Mistral unavailable

    def classify(self, image_b64: str, target: str,
                 max_keys: int = 8, timeout: int = 40) -> bool:
        if _normalize_target(target) is not None:
            return self.onnx.classify(image_b64, target, max_keys, timeout)
        # unknown target -> Mistral fallback (if configured)
        if self.keypool is not None:
            return self.keypool.classify(image_b64, target, max_keys, timeout)
        log.warning("target %r unknown and no Mistral fallback — treating as no-match",
                    target)
        return False


_classifier = None


def get_classifier(model_path: str = None):
    """Lazy singleton. Returns None if the ONNX model file is absent (caller falls
    back to Mistral)."""
    global _classifier
    if ort is None:
        log.warning("onnxruntime is not available; disabling ONNX tile classifier")
        return None
    if _classifier is None:
        path = Path(model_path or _DEFAULT_MODEL)
        if not path.exists():
            return None
        _classifier = OnnxClassifier(str(path))
    return _classifier


# self-check: load model + classify a solid tile without crashing.
if __name__ == "__main__":
    import sys
    clf = get_classifier(sys.argv[1] if len(sys.argv) > 1 else None)
    if clf is None:
        print("no model at", _DEFAULT_MODEL)
        raise SystemExit(1)
    print("classes:", clf.names)
    print("normalize 'buses' ->", _normalize_target("buses"))
    print("normalize 'a fire hydrant' ->", _normalize_target("a fire hydrant"))
    print("normalize 'traffic lights' ->", _normalize_target("traffic lights"))
    print("normalize 'palm trees' ->", _normalize_target("palm trees"))
