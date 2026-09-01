from io import BytesIO
from typing import Tuple

from PIL import Image, ImageFilter
import numpy as np

# Optional CLIP support (zero-shot classification). If transformers + torch are
# installed, we'll use CLIP for better, learned classification. Otherwise we
# fall back to the heuristic below.
_clip_model = None
_clip_processor = None
_image_embedding_model = None
_image_embedding_transform = None
_clip_labels = [
    "Daño en pintura / carrocería",
    "Rasguño / fisura",
    "Daño en neumático / rueda",
    "Foco / luz",
    "Vidrio / cristal",
    "Suciedad / manchas",
    "Suciedad / iluminación",
    "Cambio visual",
]


def _heuristic_classify(pil_img: Image.Image) -> Tuple[str, float, str]:
    """Lightweight heuristic classifier used when no ML model is available.
    Returns (label, confidence, engine_name).
    """
    img = pil_img.convert("L")
    arr = np.array(img)
    h, w = arr.shape
    mean = float(arr.mean())

    # edge density
    edges = img.filter(ImageFilter.FIND_EDGES)
    earr = np.array(edges)
    edge_density = float((earr > 20).sum()) / (w * h)

    # area / aspect heuristics
    aspect = w / max(1, h)

    # simple rules: tuned for inspection crops
    if edge_density > 0.045 and (0.6 <= aspect <= 1.7) and mean < 160:
        return ("Daño en neumático / rueda", min(0.96, 0.6 + edge_density * 6), "heuristic")
    if mean > 200 and edge_density > 0.025:
        return ("Foco / luz", min(0.92, 0.55 + edge_density * 4), "heuristic")
    if edge_density > 0.045 and 0.7 <= aspect <= 1.5 and 120 <= mean <= 220:
        return ("Vidrio / cristal", min(0.92, 0.5 + edge_density * 4), "heuristic")
    if edge_density > 0.03 and (aspect > 2.2 or (1 / aspect) > 2.2):
        return ("Rasguño / fisura", min(0.95, 0.45 + edge_density * 4), "heuristic")
    if mean < 100 and edge_density < 0.02:
        return ("Suciedad / manchas", min(0.9, 0.4 + (160 - mean) / 200), "heuristic")
    # paint damage / color change (high mean diff or high overall contrast)
    contrast = float(arr.std())
    if contrast > 28 or mean > 200:
        return ("Daño en pintura / carrocería", min(0.95, 0.5 + contrast / 150), "heuristic")

    return ("Cambio visual", 0.45, "heuristic")


def _ensure_clip_loaded():
    global _clip_model, _clip_processor
    if _clip_model is not None and _clip_processor is not None:
        return True
    try:
        from transformers import CLIPProcessor, CLIPModel
        import torch

        # load small clip model for speed
        _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        # put model in eval and to CPU (or GPU if available)
        _clip_model.eval()
        if torch.cuda.is_available():
            _clip_model.to("cuda")
        return True
    except Exception:
        _clip_model = None
        _clip_processor = None
        return False


def _ensure_image_embedding_model():
    global _image_embedding_model, _image_embedding_transform
    if _image_embedding_model is not None and _image_embedding_transform is not None:
        return True
    try:
        import torch
        from torchvision import models, transforms

        _image_embedding_model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
        _image_embedding_model.fc = torch.nn.Identity()
        _image_embedding_model.eval()
        if torch.cuda.is_available():
            _image_embedding_model.to("cuda")
        _image_embedding_transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        return True
    except Exception:
        _image_embedding_model = None
        _image_embedding_transform = None
        return False


def _classify_with_clip(image: Image.Image, texts=None):
    try:
        import torch
        if not _ensure_clip_loaded():
            return None
        labels = texts if texts is not None else _clip_labels
        inputs = _clip_processor(text=labels, images=image, return_tensors="pt", padding=True)
        # move tensors to GPU if model is on cuda
        if next(_clip_model.parameters()).is_cuda:
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        with torch.no_grad():
            outputs = _clip_model(**inputs)
            logits_per_image = outputs.logits_per_image  # shape (batch, text)
            probs = logits_per_image.softmax(dim=1).cpu().numpy()[0]
        best_idx = int(probs.argmax())
        return {"label": labels[best_idx], "confidence": float(probs[best_idx]), "engine": "clip"}
    except Exception:
        return None


def get_clip_embedding(image: Image.Image):
    try:
        import torch
        if not _ensure_clip_loaded():
            return None
        inputs = _clip_processor(images=image, return_tensors="pt")
        if next(_clip_model.parameters()).is_cuda:
            inputs = {k: v.to("cuda") for k, v in inputs.items()}
        with torch.no_grad():
            features = _clip_model.get_image_features(**inputs)
        features = features.cpu()
        return features / features.norm(p=2, dim=-1, keepdim=True)
    except Exception:
        return None


def get_image_embedding(image: Image.Image):
    try:
        import torch
        if not _ensure_image_embedding_model():
            return None
        img_tensor = _image_embedding_transform(image).unsqueeze(0)
        if next(_image_embedding_model.parameters()).is_cuda:
            img_tensor = img_tensor.to("cuda")
        with torch.no_grad():
            features = _image_embedding_model(img_tensor)
        features = features.cpu()
        return features / features.norm(p=2, dim=-1, keepdim=True)
    except Exception:
        return None


def get_best_embedding(image: Image.Image):
    emb = get_clip_embedding(image)
    if emb is not None:
        return emb
    return get_image_embedding(image)


def classify_image_bytes(image_bytes: bytes, labels=None) -> dict:
    """Classify an image given as raw bytes. Tries CLIP (transformers) first,
    otherwise falls back to the heuristic.
    Returns a dict: {label, confidence, engine}
    """
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return {"label": "Cambio visual", "confidence": 0.0, "engine": "error"}

    # try CLIP zero-shot classification
    clip_res = _classify_with_clip(img, texts=labels)
    if clip_res:
        return clip_res

    # fallback
    label, conf, eng = _heuristic_classify(img)
    return {"label": label, "confidence": float(conf), "engine": eng}
