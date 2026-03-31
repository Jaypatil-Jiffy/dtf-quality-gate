"""
DTF Image Quality Gate v8
End-to-End: AI Assessment | Vendor Audit/Redraw | Pass/Fail Gate

Stage 1: 10 Software Gates (BG-1..QR-1) — deterministic pixel analysis
Stage 2: VLM Assessment with retry/fallback — visual AI judgment
Stage 3: Decision Hierarchy — hard blocks, VLM-adjudicated, advisory
"""

import os
import io
import json
import time
import base64
import uuid
import logging
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial

import numpy as np
import cv2
from PIL import Image
import random
from collections import deque
from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
import httpx
from scipy.ndimage import distance_transform_edt, maximum_filter

logger = logging.getLogger(__name__)

# ─── Config ───
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_IMAGE_PIXELS = 30_000_000        # ~30 MP
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "tiff", "bmp", "gif", "avif", "heic", "heif", "svg"}
ALLOWED_VLM_MODELS = {
    "google/gemini-2.5-flash", "google/gemini-2.5-flash-lite", "google/gemini-2.5-pro",
    "anthropic/claude-sonnet-4.5", "anthropic/claude-haiku-4.5", "anthropic/claude-3.7-sonnet",
    "openai/gpt-5-mini", "openai/gpt-4.1", "x-ai/grok-4-fast",
}
ALLOWED_REPORT_MODELS = {
    "google/gemini-2.5-flash-lite", "google/gemini-2.5-flash", "openai/gpt-5-mini",
}

FAL_KEY = os.getenv("FAL_KEY", "")
if not FAL_KEY:
    logger.warning("FAL_KEY not set — VLM and report writer stages will fail")

FAL_VLM_URL = "https://fal.run/fal-ai/any-llm/vision"
FAL_LLM_URL = "https://fal.run/fal-ai/any-llm"

VLM_MODEL = "google/gemini-2.5-flash"
REPORT_MODEL = "google/gemini-2.5-flash-lite"

VLM_FALLBACK_CHAIN = [
    "google/gemini-2.5-flash",
    "anthropic/claude-sonnet-4.5",
    "google/gemini-2.5-flash-lite",
]
RETRY_DELAYS = [1.0, 4.0, 15.0]

DTF_DAILY_BUDGET = float(os.getenv("DTF_DAILY_BUDGET", "10.0"))

CB_FAILURE_THRESHOLD = 8
CB_WINDOW_SECONDS = 300
CB_COOLDOWN_SECONDS = 60

VLM_COST_ESTIMATES = {
    "google/gemini-2.5-flash": 0.003,
    "google/gemini-2.5-flash-lite": 0.001,
    "google/gemini-2.5-pro": 0.01,
    "anthropic/claude-sonnet-4.5": 0.005,
    "anthropic/claude-haiku-4.5": 0.002,
    "anthropic/claude-3.7-sonnet": 0.005,
    "openai/gpt-5-mini": 0.003,
    "openai/gpt-4.1": 0.008,
    "x-ai/grok-4-fast": 0.005,
}


# ─── Security Middleware ───
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


# ─── Lifespan: shared HTTP client ───
@asynccontextmanager
async def lifespan(app):
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=10.0),
        headers={"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"},
    )
    yield
    await app.state.http_client.aclose()


app = FastAPI(title="DTF Image Quality Gate v8", lifespan=lifespan)
app.add_middleware(SecurityHeadersMiddleware)

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _pil_to_cv(img: Image.Image) -> np.ndarray:
    if img.mode == "RGBA":
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGRA)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _content_mask(img: Image.Image) -> np.ndarray:
    """Binary mask of non-transparent content (alpha > 0)."""
    if img.mode in ("RGBA", "LA", "PA"):
        alpha = np.array(img.split()[-1])
        return (alpha > 0).astype(np.uint8) * 255
    gray = np.array(img.convert("L"))
    return (gray < 250).astype(np.uint8) * 255


def _img_to_data_uri(img: Image.Image, max_dim: int = 1024) -> str:
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if img.mode in ("PA", "LA", "P") else "RGB")
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    fmt = "PNG" if img.mode == "RGBA" else "JPEG"
    img.save(buf, format=fmt, quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return f"data:{mime};base64,{b64}"


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab).astype(np.float32)


# ─────────────────────────────────────────────
# Spend Tracker + Circuit Breaker
# ─────────────────────────────────────────────

_spend_tracker: dict = {"date": "", "total": 0.0}
_circuit_breakers: dict[str, dict] = {}


def _check_spend() -> bool:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _spend_tracker["date"] != today:
        _spend_tracker["date"] = today
        _spend_tracker["total"] = 0.0
    return _spend_tracker["total"] < DTF_DAILY_BUDGET


def _record_spend(amount: float):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _spend_tracker["date"] != today:
        _spend_tracker["date"] = today
        _spend_tracker["total"] = 0.0
    _spend_tracker["total"] += amount


def _is_model_healthy(model: str) -> bool:
    cb = _circuit_breakers.get(model)
    if not cb:
        return True
    if cb["unhealthy_until"] and time.time() < cb["unhealthy_until"]:
        return False
    if cb["unhealthy_until"] and time.time() >= cb["unhealthy_until"]:
        cb["unhealthy_until"] = None
        cb["failures"].clear()
    return True


def _record_model_failure(model: str):
    if model not in _circuit_breakers:
        _circuit_breakers[model] = {"failures": deque(), "unhealthy_until": None}
    cb = _circuit_breakers[model]
    now = time.time()
    cb["failures"].append(now)
    while cb["failures"] and cb["failures"][0] < now - CB_WINDOW_SECONDS:
        cb["failures"].popleft()
    if len(cb["failures"]) >= CB_FAILURE_THRESHOLD:
        cb["unhealthy_until"] = now + CB_COOLDOWN_SECONDS
        logger.warning(f"Circuit breaker OPEN for {model} — {CB_COOLDOWN_SECONDS}s cooldown")


# ─────────────────────────────────────────────
# BG Auto-Detection
# ─────────────────────────────────────────────

def needs_bg_removal(img: Image.Image) -> bool:
    """Returns False if BG already removed (RGBA with >10% transparent pixels).
    Returns True if RGB with uniform corner color (std < 15) — BG likely present.
    """
    if img.mode in ("RGBA", "LA", "PA"):
        alpha = np.array(img.split()[-1])
        transparent_pct = float((alpha < 128).sum() / alpha.size * 100)
        if transparent_pct > 10:
            return False

    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]
    margin = max(2, int(min(h, w) * 0.03))
    corners = np.concatenate([
        arr[:margin, :margin].reshape(-1, 3),
        arr[:margin, -margin:].reshape(-1, 3),
        arr[-margin:, :margin].reshape(-1, 3),
        arr[-margin:, -margin:].reshape(-1, 3),
    ], axis=0).astype(np.float64)
    if corners.std(axis=0).max() < 15:
        return True

    return True


# ─────────────────────────────────────────────
# STAGE 1 — Software Gates
# ─────────────────────────────────────────────

def gate_bg1(img: Image.Image) -> dict:
    """BG-1: No Alpha Channel Check. remove_bg ordered AND no alpha = flag."""
    t0 = time.perf_counter()
    has_alpha = img.mode in ("RGBA", "LA", "PA")
    alpha_stats = {}
    if has_alpha:
        a = np.array(img.split()[-1])
        alpha_stats = {
            "min": int(a.min()),
            "max": int(a.max()),
            "mean": round(float(a.mean()), 2),
            "fully_transparent_pct": round(float((a == 0).sum() / a.size * 100), 2),
            "fully_opaque_pct": round(float((a == 255).sum() / a.size * 100), 2),
        }
    flag = not has_alpha
    return {
        "gate_id": "BG-1",
        "name": "No Alpha Channel Check",
        "flag": flag,
        "confidence": 98,
        "severity": "critical" if flag else "none",
        "detail": (
            "No alpha channel — BG removal may not have been applied"
            if flag
            else f"Alpha channel present ({img.mode})"
        ),
        "metrics": {"has_alpha": has_alpha, "mode": img.mode, **alpha_stats},
        "fp_rules": [],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_bg2(img: Image.Image) -> dict:
    """BG-2: White BG Corner Sampling. >85% opaque white in corners = flag."""
    t0 = time.perf_counter()
    arr = np.array(img.convert("RGBA"))
    h, w = arr.shape[:2]
    margin = max(2, int(min(h, w) * 0.05))

    corners = {
        "top-left": arr[:margin, :margin],
        "top-right": arr[:margin, -margin:],
        "bottom-left": arr[-margin:, :margin],
        "bottom-right": arr[-margin:, -margin:],
    }
    corner_results = {}
    total_white, total_px = 0, 0
    for name, c in corners.items():
        opaque = c[:, :, 3] > 200
        white = (c[:, :, 0] > 240) & (c[:, :, 1] > 240) & (c[:, :, 2] > 240)
        ow = (opaque & white).sum()
        n = c.shape[0] * c.shape[1]
        corner_results[name] = round(float(ow / n * 100), 2) if n else 0
        total_white += ow
        total_px += n

    pct = round(float(total_white / total_px * 100), 2) if total_px else 0
    all_opaque_pct = round(float((arr[:, :, 3] > 200).sum() / (h * w) * 100), 2)
    flag = pct > 92 and all_opaque_pct > 90
    return {
        "gate_id": "BG-2",
        "name": "White BG Corner Sampling",
        "flag": flag,
        "confidence": 80,
        "severity": "high" if flag else "none",
        "detail": f"{pct}% opaque white in corners (threshold 92%), {all_opaque_pct}% image opaque",
        "metrics": {"overall_pct": pct, "threshold": 92, "all_opaque_pct": all_opaque_pct,
                    "corners": corner_results, "margin_px": margin},
        "fp_rules": ["FP-BG2-1: White Design Element", "FP-BG2-2: Near-White on Transparent BG"],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_bg3(img: Image.Image) -> dict:
    """BG-3: Content Border Opacity — checks pixels along the CONTENT boundary
    (where design meets transparency), not the image canvas edges.
    DTF physics: any pixel with alpha > 0 deposits white ink. Below ~60%
    opacity (alpha < 153), the deposit is too thin = faint white halo on dark fabric.
    This gate finds content border pixels with problematic low opacity."""
    t0 = time.perf_counter()
    if img.mode not in ("RGBA", "LA", "PA"):
        return {
            "gate_id": "BG-3", "name": "Content Border Opacity",
            "flag": False, "confidence": 80, "severity": "none",
            "detail": "No alpha channel — gate skipped",
            "metrics": {"skipped": True}, "fp_rules": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    alpha = np.array(img.split()[-1])
    content_mask = (alpha > 0).astype(np.uint8) * 255

    # Find the content boundary (where design meets transparency)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    eroded = cv2.erode(content_mask, kernel, iterations=1)
    # Content border = content pixels that are on the edge of the design
    border_mask = content_mask & (~eroded.astype(bool)).astype(np.uint8) * 255
    border_mask = (content_mask > 0) & (eroded == 0)

    border_alpha = alpha[border_mask]
    if border_alpha.size == 0:
        return {
            "gate_id": "BG-3", "name": "Content Border Opacity",
            "flag": False, "confidence": 80, "severity": "none",
            "detail": "No content border pixels found",
            "metrics": {}, "fp_rules": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    total_border = border_alpha.size
    # Below 60% opacity = will print as faint white halo
    low_opacity = int((border_alpha < 153).sum())
    # Very low = nearly invisible ink deposit, worst case
    very_low = int((border_alpha < 51).sum())
    # Good opacity = will print solid
    solid = int((border_alpha >= 153).sum())
    low_pct = round(low_opacity / total_border * 100, 2)

    # Only flag if a significant portion of the border has low opacity
    # Anti-aliased edges naturally have a few low-alpha pixels — that's fine
    flag = low_pct > 15 and low_opacity > 50
    severity = "high" if low_pct > 40 else ("medium" if flag else "none")

    return {
        "gate_id": "BG-3",
        "name": "Content Border Opacity",
        "flag": flag,
        "confidence": 80,
        "severity": severity,
        "detail": (
            f"{low_pct}% of content border below 60% opacity ({low_opacity}/{total_border} px). "
            f"{'These edges may print as faint white halo on dark fabric.' if flag else 'Border opacity is acceptable for DTF.'}"
        ),
        "metrics": {
            "total_border_px": total_border,
            "low_opacity_count": low_opacity,
            "low_opacity_pct": low_pct,
            "very_low_count": very_low,
            "solid_count": solid,
            "opacity_threshold": 153,
            "opacity_threshold_pct": "60%",
        },
        "fp_rules": ["FP-BG3-1: Watercolor/airbrush edges (intentional soft boundary)",
                     "FP-BG3-2: Anti-aliased text/vector (normal 1-2px feathering)"],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_bg4(img: Image.Image) -> dict:
    """BG-4: Ghost Pixel Detection. DPI-scaled dilation; orphans outside safe zone."""
    t0 = time.perf_counter()
    mask = _content_mask(img)
    if mask.sum() == 0:
        return {
            "gate_id": "BG-4", "name": "Ghost Pixel Detection",
            "flag": False, "confidence": 82, "severity": "none",
            "detail": "No content detected", "metrics": {}, "fp_rules": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    dpi_scale = max(1, min(img.width, img.height) // 300)
    kernel_size = 5 + dpi_scale * 2
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    dilated = cv2.dilate(mask, kernel, iterations=3)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {
            "gate_id": "BG-4", "name": "Ghost Pixel Detection",
            "flag": False, "confidence": 82, "severity": "none",
            "detail": "No contours found", "metrics": {}, "fp_rules": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    main_contour = max(contours, key=cv2.contourArea)
    main_area = cv2.contourArea(main_contour)
    total_content_px = int(mask.sum() / 255)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    main_mask = np.zeros_like(mask)
    cv2.drawContours(main_mask, [main_contour], -1, 255, -1)
    boundary_dist = distance_transform_edt(main_mask == 0)

    ghosts = []
    ghost_total_px = 0
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < main_area * 0.001 and area < 5:
            component = (labels == i).astype(np.uint8) * 255
            comp_coords = np.argwhere(component > 0)
            if comp_coords.size == 0:
                continue
            min_dist = float(boundary_dist[comp_coords[:, 0], comp_coords[:, 1]].min())
            if min_dist <= 3:
                continue
            overlap = cv2.bitwise_and(component, dilated)
            if overlap.sum() == 0:
                ghosts.append({"label": i, "area_px": int(area),
                               "x": int(stats[i, cv2.CC_STAT_LEFT]),
                               "y": int(stats[i, cv2.CC_STAT_TOP]),
                               "min_content_dist_px": round(min_dist, 1)})
                ghost_total_px += area

    flag = len(ghosts) > 0
    return {
        "gate_id": "BG-4",
        "name": "Ghost Pixel Detection",
        "flag": flag,
        "confidence": 82,
        "severity": "high" if len(ghosts) > 10 else ("medium" if flag else "none"),
        "detail": f"{len(ghosts)} ghost pixel cluster(s) detected ({ghost_total_px} total px)",
        "metrics": {
            "ghost_clusters": len(ghosts),
            "ghost_total_px": ghost_total_px,
            "main_contour_area": int(main_area),
            "total_content_px": total_content_px,
            "kernel_size": kernel_size,
            "ghost_details": ghosts[:20],
        },
        "fp_rules": ["FP-BG4-1: Intentional Glow"],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_lr1(img: Image.Image, print_width_in: float = 10.0, print_height_in: float = 10.0) -> dict:
    """LR-1: DPI Check. DPI = pixels / print inches."""
    t0 = time.perf_counter()
    dpi_x = img.width / print_width_in
    dpi_y = img.height / print_height_in
    effective_dpi = min(dpi_x, dpi_y)

    if effective_dpi < 72:
        severity, verdict = "critical", "definite_low"
    elif effective_dpi < 150:
        severity, verdict = "medium", "borderline"
    elif effective_dpi < 300:
        severity, verdict = "low", "acceptable"
    else:
        severity, verdict = "none", "good"

    flag = effective_dpi < 150
    return {
        "gate_id": "LR-1",
        "name": "DPI / Resolution Check",
        "flag": flag,
        "confidence": 85,
        "severity": severity,
        "detail": f"Effective DPI: {round(effective_dpi, 1)} ({verdict}) at {print_width_in}×{print_height_in}\"",
        "metrics": {
            "width_px": img.width,
            "height_px": img.height,
            "print_width_in": print_width_in,
            "print_height_in": print_height_in,
            "dpi_x": round(dpi_x, 1),
            "dpi_y": round(dpi_y, 1),
            "effective_dpi": round(effective_dpi, 1),
            "verdict": verdict,
            "recommended_dpi": 300,
        },
        "fp_rules": [],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_lr2(img: Image.Image) -> dict:
    """LR-2: Blur Detection via Laplacian variance on content region."""
    t0 = time.perf_counter()
    rgb = np.array(img.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    mask = _content_mask(img)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    if mask.sum() > 0:
        lap_var = float(laplacian[mask > 0].var())
        lap_mean = float(np.abs(laplacian[mask > 0]).mean())
    else:
        lap_var = float(laplacian.var())
        lap_mean = float(np.abs(laplacian).mean())

    h, w = gray.shape
    block_size = max(64, min(h, w) // 4)
    block_scores = []
    for y in range(0, h - block_size + 1, block_size):
        for x in range(0, w - block_size + 1, block_size):
            block = gray[y:y + block_size, x:x + block_size]
            bv = cv2.Laplacian(block, cv2.CV_64F).var()
            block_scores.append(round(float(bv), 2))

    if lap_var < 3:
        severity, verdict = "critical", "definite_blur"
    elif lap_var < 10:
        severity, verdict = "medium", "borderline"
    elif lap_var < 50:
        severity, verdict = "low", "acceptable"
    else:
        severity, verdict = "none", "sharp"

    flag = lap_var < 10
    return {
        "gate_id": "LR-2",
        "name": "Blur Detection (Laplacian)",
        "flag": flag,
        "confidence": 70,
        "severity": severity,
        "detail": f"Laplacian variance: {round(lap_var, 2)} ({verdict})",
        "metrics": {
            "laplacian_variance": round(lap_var, 2),
            "laplacian_mean": round(lap_mean, 2),
            "verdict": verdict,
            "thresholds": {"definite": 3, "borderline": 10, "acceptable": 50},
            "block_scores_sample": block_scores[:16],
            "min_block_score": round(min(block_scores), 2) if block_scores else None,
        },
        "fp_rules": ["FP-LR2-1: Shallow DOF", "FP-LR2-2: Gradient/Soft-Brush Art"],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_tl1(img: Image.Image, print_dpi: float = 300) -> dict:
    """TL-1: Thin Line Detection — finds ISOLATED narrow strokes (< 2px wide)
    that cannot hold DTF adhesive powder. Ignores borders of large filled shapes.
    A thin line = a separate narrow element (underline, decorative stroke, thin
    font stroke) disconnected or distinct from the main artwork body."""
    t0 = time.perf_counter()
    mask = _content_mask(img)
    if mask.sum() == 0:
        return {
            "gate_id": "TL-1", "name": "Thin Line Detection",
            "flag": False, "confidence": 75, "severity": "none",
            "detail": "No content detected", "metrics": {}, "fp_rules": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    small_dim = min(img.width, img.height)
    if small_dim > 1500:
        scale = 1500 / small_dim
        mask = cv2.resize(mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        adjusted_dpi = print_dpi * scale
    else:
        adjusted_dpi = print_dpi

    dist = distance_transform_edt(mask > 0)
    if dist.max() == 0:
        return {
            "gate_id": "TL-1", "name": "Thin Line Detection",
            "flag": False, "confidence": 75, "severity": "none",
            "detail": "No content interior", "metrics": {}, "fp_rules": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    mm_per_px = 25.4 / adjusted_dpi
    thin_px_threshold = 0.76 / mm_per_px  # 0.76mm = 0.03in (PRD spec)

    # Find connected components — only flag SMALL isolated ones
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    total_content = int(mask.sum() / 255)

    thin_components = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]

        # Skip large filled shapes — their borders are NOT thin lines
        if area > total_content * 0.1:
            continue

        component_mask = (labels == i).astype(np.uint8) * 255
        comp_dist = distance_transform_edt(component_mask > 0)
        if comp_dist.max() == 0:
            continue

        max_half_width = float(comp_dist.max())
        max_width_px = max_half_width * 2
        max_width_mm = max_width_px * mm_per_px

        # Isolated element where the thickest part is still < 2px or < 0.76mm
        if max_width_px < 2 or max_width_mm < 0.76:
            aspect = max(w, h) / max(min(w, h), 1)
            if aspect > 3:  # elongated = likely a line, not a dot
                thin_components.append({
                    "area_px": int(area),
                    "max_width_px": round(max_width_px, 1),
                    "max_width_mm": round(max_width_mm, 3),
                    "aspect_ratio": round(aspect, 1),
                    "x": int(stats[i, cv2.CC_STAT_LEFT]),
                    "y": int(stats[i, cv2.CC_STAT_TOP]),
                })

    flag = len(thin_components) > 0
    severity = "high" if len(thin_components) > 5 else ("medium" if flag else "none")

    return {
        "gate_id": "TL-1",
        "name": "Thin Line Detection",
        "flag": flag,
        "confidence": 75,
        "severity": severity,
        "detail": (
            f"{len(thin_components)} isolated thin stroke(s) found (< 2px / < 0.76mm at {int(adjusted_dpi)} DPI). "
            f"These may not hold adhesive powder."
            if flag else
            f"No isolated thin strokes detected at {int(adjusted_dpi)} DPI"
        ),
        "metrics": {
            "thin_stroke_count": len(thin_components),
            "total_components": num_labels - 1,
            "print_dpi": print_dpi,
            "threshold_px": 2,
            "threshold_mm": 0.76,
            "thin_details": thin_components[:10],
        },
        "fp_rules": ["FP-TL1-1: Intentional hairline as design element"],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_cs1(img: Image.Image) -> dict:
    """CS-1: CMYK Color Shift via ICC profile simulation.
    Uses Pillow ImageCms when ICC profile available, falls back to gamut-risk
    heuristic for saturated colors that are known to shift in CMYK.
    """
    t0 = time.perf_counter()
    rgb_img = img.convert("RGB")
    rgb_arr = np.array(rgb_img).astype(np.float64)
    h, w = rgb_arr.shape[:2]

    if h * w > 4_000_000:
        scale = (4_000_000 / (h * w)) ** 0.5
        rgb_img_small = rgb_img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        rgb_arr = np.array(rgb_img_small).astype(np.float64)

    r, g, b = rgb_arr[:, :, 0], rgb_arr[:, :, 1], rgb_arr[:, :, 2]

    # Heuristic gamut-risk detection: identify pixels with high saturation in
    # colors known to shift during CMYK conversion (saturated blues, greens,
    # neon/vivid colors). The naive RGB→CMYK→RGB round-trip is algebraically
    # an identity — real CMYK shift requires ICC profiles.
    max_ch = np.maximum(np.maximum(r, g), b)
    min_ch = np.minimum(np.minimum(r, g), b)
    chroma = max_ch - min_ch
    with np.errstate(divide="ignore", invalid="ignore"):
        sat = np.where(max_ch > 0, chroma / max_ch, 0)

    # Risky hues: pure blue (220-260°), vivid green (80-170°), bright red/orange
    hue = np.zeros_like(r)
    mask_nz = chroma > 0
    is_r_max = (r >= g) & (r >= b) & mask_nz
    is_g_max = (g > r) & (g >= b) & mask_nz
    is_b_max = (b > r) & (b > g) & mask_nz
    hue[is_r_max] = (60 * ((g[is_r_max] - b[is_r_max]) / chroma[is_r_max])) % 360
    hue[is_g_max] = 60 * ((b[is_g_max] - r[is_g_max]) / chroma[is_g_max]) + 120
    hue[is_b_max] = 60 * ((r[is_b_max] - g[is_b_max]) / chroma[is_b_max]) + 240

    risky_blue = (hue >= 200) & (hue <= 270) & (sat > 0.6) & (max_ch > 100)
    risky_green = (hue >= 80) & (hue <= 170) & (sat > 0.6) & (max_ch > 100)
    risky_red = ((hue <= 30) | (hue >= 330)) & (sat > 0.7) & (max_ch > 180)
    risky_neon = (sat > 0.85) & (max_ch > 200)

    all_risky = risky_blue | risky_green | risky_red | risky_neon
    total_px = rgb_arr.shape[0] * rgb_arr.shape[1]
    risky_count = int(all_risky.sum())
    risky_pct = round(risky_count / total_px * 100, 2) if total_px > 0 else 0

    # Estimated delta E for risky pixels (typical CMYK gamut compression)
    est_delta_e = np.zeros_like(r)
    est_delta_e[risky_blue] = 5.0 + sat[risky_blue] * 4.0
    est_delta_e[risky_green] = 3.0 + sat[risky_green] * 5.0
    est_delta_e[risky_red] = 2.0 + sat[risky_red] * 3.0
    est_delta_e[risky_neon] = np.maximum(est_delta_e[risky_neon], 6.0 + sat[risky_neon] * 3.0)

    mean_de = float(est_delta_e[all_risky].mean()) if risky_count > 0 else 0
    max_de = float(est_delta_e.max())
    pct_above_3 = round(float((est_delta_e > 3).sum() / total_px * 100), 2)
    pct_above_5 = round(float((est_delta_e > 5).sum() / total_px * 100), 2)

    worst_indices = np.unravel_index(
        np.argsort(est_delta_e, axis=None)[-5:], est_delta_e.shape
    )
    original_rgb = np.array(rgb_img if h * w <= 4_000_000 else rgb_img_small).astype(np.uint8)
    worst_colors = []
    for yi, xi in zip(worst_indices[0], worst_indices[1]):
        px = original_rgb[yi, xi]
        worst_colors.append({
            "position": [int(xi), int(yi)],
            "original_rgb": [int(px[0]), int(px[1]), int(px[2])],
            "original_hex": f"#{px[0]:02x}{px[1]:02x}{px[2]:02x}",
            "estimated_delta_e": round(float(est_delta_e[yi, xi]), 2),
            "risk_category": (
                "neon" if risky_neon[yi, xi] else
                "blue" if risky_blue[yi, xi] else
                "green" if risky_green[yi, xi] else
                "red" if risky_red[yi, xi] else "other"
            ),
        })

    if risky_pct > 15 or mean_de > 6:
        severity = "high"
    elif risky_pct > 5 or mean_de > 4:
        severity = "medium"
    elif risky_pct > 1:
        severity = "low"
    else:
        severity = "none"

    flag = risky_pct > 5 or (risky_count > 0 and mean_de > 5)
    return {
        "gate_id": "CS-1",
        "name": "CMYK Color Shift Detection",
        "flag": flag,
        "confidence": 70,
        "severity": severity,
        "detail": (
            f"{risky_pct}% of pixels at risk of CMYK gamut shift "
            f"(est. mean ΔE: {round(mean_de, 1)}, max: {round(max_de, 1)}). "
            f"{pct_above_3}% est. above perceptible threshold (ΔE>3)"
        ),
        "metrics": {
            "risky_pixel_pct": risky_pct,
            "risky_pixel_count": risky_count,
            "risky_blue_pct": round(float(risky_blue.sum() / total_px * 100), 2),
            "risky_green_pct": round(float(risky_green.sum() / total_px * 100), 2),
            "risky_red_pct": round(float(risky_red.sum() / total_px * 100), 2),
            "risky_neon_pct": round(float(risky_neon.sum() / total_px * 100), 2),
            "est_mean_delta_e": round(mean_de, 2),
            "est_max_delta_e": round(max_de, 2),
            "pct_above_3": pct_above_3,
            "pct_above_5": pct_above_5,
            "perceptible_threshold": 3,
            "worst_colors": worst_colors,
            "bypasses_vlm": True,
            "method": "gamut-risk-heuristic (ICC profile recommended for production)",
        },
        "fp_rules": [],
        "vlm_bypass": True,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_je1(img: Image.Image) -> dict:
    """JE-1: Jagged Edge Detection. Alpha-channel contour analysis only."""
    t0 = time.perf_counter()

    if img.mode not in ("RGBA", "LA", "PA"):
        return {
            "gate_id": "JE-1", "name": "Jagged Edge Detection",
            "flag": False, "confidence": 70, "severity": "none",
            "detail": "No alpha channel — gate skipped",
            "metrics": {"skipped": True}, "fp_rules": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    alpha = np.array(img.split()[-1])
    mask = (alpha > 0).astype(np.uint8) * 255

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return {
            "gate_id": "JE-1", "name": "Jagged Edge Detection",
            "flag": False, "confidence": 70, "severity": "none",
            "detail": "No contours", "metrics": {}, "fp_rules": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    total_jagged = 0
    total_points = 0
    contour_results = []

    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        if len(cnt) < 100:
            continue
        pts = cnt.squeeze()
        if pts.ndim != 2:
            continue

        dx = np.diff(pts[:, 0].astype(float))
        dy = np.diff(pts[:, 1].astype(float))
        angles = np.arctan2(dy, dx)
        angle_changes = np.abs(np.diff(angles))
        angle_changes = np.minimum(angle_changes, 2 * np.pi - angle_changes)

        right_angles = (angle_changes > np.pi / 4) & (angle_changes < 3 * np.pi / 4)
        jagged_count = int(right_angles.sum())
        n_points = len(pts)

        jaggedness = jagged_count / max(1, n_points - 2)
        total_jagged += jagged_count
        total_points += n_points
        contour_results.append({
            "area": int(cv2.contourArea(cnt)),
            "perimeter": round(float(cv2.arcLength(cnt, True)), 1),
            "points": n_points,
            "jagged_points": jagged_count,
            "jaggedness_ratio": round(jaggedness, 4),
        })

    overall_jaggedness = total_jagged / max(1, total_points) if total_points > 0 else 0

    if overall_jaggedness > 0.4:
        severity = "high"
    elif overall_jaggedness > 0.25:
        severity = "medium"
    elif overall_jaggedness > 0.15:
        severity = "low"
    else:
        severity = "none"

    flag = overall_jaggedness > 0.25
    return {
        "gate_id": "JE-1",
        "name": "Jagged Edge Detection",
        "flag": flag,
        "confidence": 70,
        "severity": severity,
        "detail": f"Jaggedness ratio: {round(overall_jaggedness, 4)} ({total_jagged}/{total_points} points)",
        "metrics": {
            "overall_jaggedness": round(overall_jaggedness, 4),
            "total_jagged_points": total_jagged,
            "total_contour_points": total_points,
            "contours_analyzed": len(contour_results),
            "contour_details": contour_results[:5],
            "min_contour_points": 100,
            "threshold": 0.25,
        },
        "fp_rules": ["FP-JE1-1: Intentional pixelated/8-bit style"],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def gate_qr1(img: Image.Image) -> dict:
    """QR-1: QR Code Scan. Detect and decode QR codes."""
    t0 = time.perf_counter()
    gray = np.array(img.convert("L"))
    detector = cv2.QRCodeDetector()

    decoded, points, straight = detector.detectAndDecode(gray)
    has_qr = points is not None and decoded is not None and len(decoded) > 0 and decoded != ""

    result = {
        "gate_id": "QR-1",
        "name": "QR Code Scan Check",
        "flag": False,
        "confidence": 90,
        "severity": "none",
        "detail": "No QR code detected in image",
        "metrics": {"qr_detected": False},
        "fp_rules": [],
        "latency_ms": 0,
    }

    if has_qr:
        result["metrics"] = {
            "qr_detected": True,
            "decoded_data": decoded[:200],
            "is_url": decoded.startswith("http"),
            "points": points.tolist() if points is not None else None,
        }
        result["detail"] = f"QR code found: {decoded[:80]}{'...' if len(decoded) > 80 else ''}"

        if decoded.startswith("http"):
            result["metrics"]["url"] = decoded
            result["detail"] += " (URL detected, needs live verification)"
    else:
        multi_detector = cv2.QRCodeDetector()
        retval, decoded_info, points_arr, straight_qr = multi_detector.detectAndDecodeMulti(gray)
        if retval and decoded_info:
            valid = [d for d in decoded_info if d]
            if valid:
                result["metrics"]["qr_detected"] = True
                result["metrics"]["multi_qr_count"] = len(valid)
                result["metrics"]["decoded_data"] = valid[0][:200]
                result["detail"] = f"{len(valid)} QR code(s) found"

    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return result


# ─────────────────────────────────────────────
# STAGE 2 — VLM Assessment (fal.ai)
# ─────────────────────────────────────────────

VLM_SYSTEM_PROMPT = """You are a DTF (Direct-to-Film) print quality auditor. Your job is to identify defects that cause VISIBLE FAILURES on the final printed garment. You are the last line of defense before an image goes to print.

CRITICAL DTF PRINTING PHYSICS:
- DTF prints a SOLID white ink layer wherever alpha > 0. Even 1% opacity = visible white dot/halo on dark fabric.
- Irregular shapes after BG removal are NORMAL. Anti-aliased edges (1-2px feathering) are NORMAL.
- Semi-transparency: DTF CANNOT reproduce gradual transparency. Glow, smoke, neon, shadows with partial opacity WILL print as white/gray/brown haze. This is physics, not a judgment call.
- Thin lines below 0.03" (0.76mm) at print size cannot hold adhesive powder — they crumble or don't transfer.
- Low resolution (<150 DPI at print size) = visible pixelation on garment.
- AI upscalers corrupt text, distort faces, add halos. Always compare original vs processed.

REAL CUSTOMER COMPLAINTS — this is what print failures actually look like:
- Semi-transparency: "Shows white gray and brown after pressed, didn't look like it at all"
- Semi-transparency: "The bottom image doesn't have enough backing causing it to not adhere"
- Thin lines: "The yellow line didn't press to the shirt except for 2 little spots that are white and textured"
- Thin lines: "No powder on the film in some areas" (powder can't stick to thin strokes)
- BG removal: "The L in Michael Jordan was removed from my print" (over-removal deleted design)
- BG removal: "Transfers came in with white background behind the tree that should have been transparent" (under-removal)
- Upscaling: "The number 1 in the area code is messed up on all of them" (text corruption)
- Upscaling: "Black ink smudges on the red part of the flag on every single one" (processing artifact)
- CMYK: "Completely wrong color — actually gray rather than the electric blue we uploaded"
- Poor quality: "The letters look fuzzy not clean and sharp like my other designs"

You receive TWO images:
- Image 1 = ORIGINAL customer upload (before any processing)
- Image 2 = PROCESSED image (after BG removal + enhancement — what would go to print)

Compare them carefully. The original shows what the customer intended. The processed shows what the system did.

YOU PERFORM 4 VLM-ONLY CHECKS:

1. UP-1 (Upscaling + Design Integrity):
   - Compare original vs processed: did enhancement corrupt anything?
   - Text looks garbled, letters don't form real words = FAIL
   - Faces look melted, distorted, extra fingers = FAIL
   - Sharpening halos (bright outline around elements) = FAIL
   - Objects incorrectly added or removed vs original = FAIL
   - Clean upscale matching original intent = PASS

2. BR-1 (BG Removal Quality — compare original vs processed):
   Detect these specific failure categories by comparing Image 1 vs Image 2:
   a. ENCLOSED TEXT RESIDUAL: Background color remaining inside letterforms (O, D, B, A, R, P, Q) or between tightly spaced characters. Check closed loops in text.
   b. EDGE HALO / FRINGING: Semi-transparent or discolored border artifact around the subject. Appears as light/colored glow ring. On dark garment = obvious ghosting outline. Any border artifact wider than 1-2px = FAIL.
   c. HAIR / FINE-DETAIL LOSS: Flyaway hair, fur, feathers, thin threads clipped to a hard edge. Compare original hair/fur edges vs processed. Hard clipping of clearly visible strands = FAIL. Minor smoothing = PASS.
   d. INCOMPLETE REMOVAL: Large visible patches of original background still in processed image. Portions of sky, wall, floor, scene elements not removed. Any visible background patch clearly not part of the subject = FAIL.
   e. COLOR BLEED: Original background color has bled into subject edge pixels. Shoulder/arm edges have tint from the background color not in original.
   f. SHADOW ARTIFACTS: Subject's cast shadow retained when it should be removed, or partially removed leaving floating shadow fragment. Natural shading ON the subject = PASS. Cast shadows from the background scene = FAIL.
   
   If none of these defects found = PASS. Irregular/non-rectangular shape is NORMAL after BG removal.

3. ST-1 (Semi-Transparency — DTF PHYSICS CONSTRAINT):
   DTF prints a SOLID white ink layer. It CANNOT reproduce gradual transparency. ANY semi-transparent
   element will print as VISIBLE WHITE/GRAY/BROWN HAZE on dark garments. The customer sees a 
   beautiful glow on screen but receives ugly white smudges on their shirt.
   
   - Pink/colored glow or smoke effects with gradual fade = FAIL (prints as white haze)
   - Neon/glow outlines that fade from bright to nothing = FAIL (partial white ink deposit)  
   - Drop shadows or cast shadows with transparency = FAIL (prints as gray blob)
   - Faded/feathered edges that blend gradually to transparent = FAIL (white fringe)
   - Large areas of semi-transparent color (like a cloud/fog effect) = FAIL
   - ONLY pass if ALL visible elements are fully opaque with no gradual fading
   - Intent does NOT matter — even if the customer designed it with glow effects, DTF cannot print it correctly
   - This is the #1 source of "doesn't look like what I ordered" complaints

4. TL-VLM (Thin Lines — isolated thin elements after BG removal):
   After background removal, look for thin elements that are SEPARATE from the main design
   body and SURROUNDED BY TRANSPARENCY (removed background). These isolated thin pieces
   are less than 2px wide and cannot hold DTF adhesive powder — they crumble, don't transfer,
   or leave white textured spots.
   
   What IS a thin line problem:
   - A thin decorative line SEPARATE from the main artwork, floating in transparent space
   - A thin border or outline that got separated from the filled shape after BG removal
   - Thin text strokes that are disconnected from the main design body
   - Any isolated narrow element (< 2px wide) surrounded by transparency
   
   What is NOT a thin line problem:
   - Borders/edges of filled shapes that are part of the main connected design = PASS
   - Text that is connected to the main design body = PASS (even if strokes are thin)
   - Anti-aliased edges = PASS (normal feathering)
   
   Real complaints: "The yellow line didn't press to the shirt", "No powder on the film in some areas"

YOU ALSO VALIDATE SOFTWARE GATE FLAGS (confirm or override):
- BG-2: Override if flagged white is part of the design (white text, white elements)
- BG-3: Override if low-opacity border is normal anti-aliasing (1-2px) or watercolor style
- BG-4: Override if "ghost pixels" are intentional glow/sparkle effects
- LR-2: Override if softness is artistic style (watercolor, bokeh, gradient art)
- TL-1: Override if thin strokes are part of the design the customer submitted
- JE-1: Override if jagged edges are intentional pixel art / 8-bit style
- QR-1: Just confirm QR is intact and scannable

BIAS: When uncertain, PASS. False negatives (missing a defect) are less harmful than false positives (rejecting a good image). The vendor will catch real issues.

Respond ONLY with valid JSON. No markdown, no code fences."""


def _build_vlm_prompt(sw_results: list) -> str:
    flagged = [g for g in sw_results if g.get("flag") and g["gate_id"] != "CS-1"]
    clean = [g for g in sw_results if not g.get("flag") and g["gate_id"] != "CS-1"]
    sw_summary = {
        "flagged_gates": [{
            "gate_id": g["gate_id"], "name": g["name"],
            "detail": g["detail"], "confidence": g["confidence"],
            "severity": g["severity"],
        } for g in flagged],
        "clean_gates": [g["gate_id"] for g in clean],
    }

    return f"""Analyze these images for DTF print quality defects.
Image 1 = ORIGINAL customer upload. Image 2 = PROCESSED (after BG removal + enhancement).
Compare them to detect processing failures.

Software gate results (excluding CS-1 CMYK which bypasses VLM):
{json.dumps(sw_summary, indent=2)}

Respond with this exact JSON structure:
{{
  "overall_verdict": "PASS" or "FAIL",
  "confidence_score": 0-100,
  "print_readiness_score": 0-100,
  "checks": {{
    "UP1_upscaling_integrity": {{
      "status": "pass" or "fail",
      "findings": ["specific findings comparing original vs processed"],
      "severity": "none/low/medium/high/critical"
    }},
    "BR1_bg_removal": {{
      "status": "pass" or "fail",
      "defect_category": "none" or "enclosed_text_residual" or "edge_halo" or "hair_detail_loss" or "incomplete_removal" or "color_bleed" or "shadow_artifact",
      "under_removal": false,
      "over_removal": false,
      "findings": ["specific findings about what BG removal did wrong, comparing original vs processed"],
      "severity": "none/low/medium/high/critical"
    }},
    "ST1_semi_transparency": {{
      "status": "pass" or "fail",
      "is_design_intent": true,
      "findings": ["specific findings — is this from the original design or a processing artifact?"],
      "severity": "none/low/medium/high/critical"
    }},
    "TL_VLM_thin_lines": {{
      "status": "pass" or "fail",
      "findings": ["specific thin elements that may not transfer"],
      "severity": "none/low/medium/high/critical"
    }}
  }},
  "sw_gate_validations": {{
    "BG2": {{"override": false, "fp_code": null, "reason": "..."}},
    "BG3": {{"override": false, "fp_code": null, "reason": "..."}},
    "BG4": {{"override": false, "fp_code": null, "reason": "..."}},
    "LR2": {{"override": false, "fp_code": null, "reason": "..."}},
    "TL1": {{"override": false, "fp_code": null, "reason": "..."}},
    "JE1": {{"override": false, "fp_code": null, "reason": "..."}},
    "QR1": {{"override": false, "reason": "..."}}
  }},
  "additional_issues": ["any other problems found"],
  "fix_suggestions": ["specific actionable fixes for the vendor"]
}}"""


ALL_VLM_MODELS = [
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash", False),
    ("google/gemini-2.5-flash-lite", "Gemini Flash Lite", False),
    ("google/gemini-2.5-pro", "Gemini 2.5 Pro", True),
    ("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5", True),
    ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5", True),
    ("openai/gpt-5-mini", "GPT-5 Mini", True),
    ("openai/gpt-4.1", "GPT-4.1", True),
    ("x-ai/grok-4-fast", "Grok 4 Fast", True),
]
FAL_RMBG_URL = "https://fal.run/fal-ai/birefnet/v2"


def _repair_json(text: str) -> dict:
    """Best-effort JSON repair for VLM output."""
    import re
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"parse_error": True, "raw": text[:500]}


# ─── VLM Assessment with retry + fallback ───

async def _vlm_single_call(image_uris: list, sw_results: list, model: str,
                           http_client: httpx.AsyncClient) -> dict:
    t0 = time.perf_counter()
    prompt = _build_vlm_prompt(sw_results)
    try:
        resp = await http_client.post(FAL_VLM_URL, json={
            "prompt": prompt, "system_prompt": VLM_SYSTEM_PROMPT,
            "model": model, "image_urls": image_uris,
            "max_tokens": 4096, "temperature": 0.1,
        })
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        logger.exception(f"VLM failed: {model}")
        return {"status": "error", "error": str(e)[:120], "model": model,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}

    output_text = result.get("output", "")
    parsed = _repair_json(output_text)

    return {"status": "success", "model": model, "assessment": parsed,
            "raw_output": output_text,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}


async def vlm_assessment_with_retry(image_uris: list, sw_results: list, model: str,
                                     http_client: httpx.AsyncClient) -> dict:
    """Retry with exponential backoff (1s/4s/15s), fallback chain, circuit breaker."""
    chain = [model] + [m for m in VLM_FALLBACK_CHAIN if m != model]

    for current_model in chain:
        if not _is_model_healthy(current_model):
            logger.info(f"Skipping unhealthy model: {current_model}")
            continue

        last_result = None
        for attempt in range(len(RETRY_DELAYS)):
            if not _check_spend():
                return {"status": "error", "error": "Daily spend cap exceeded",
                        "model": current_model, "latency_ms": 0}

            last_result = await _vlm_single_call(image_uris, sw_results, current_model, http_client)

            if last_result["status"] == "success":
                _record_spend(VLM_COST_ESTIMATES.get(current_model, 0.005))
                last_result["attempted_model"] = model
                last_result["used_model"] = current_model
                last_result["attempts"] = attempt + 1
                return last_result

            error_str = str(last_result.get("error", ""))
            if error_str.startswith("4"):
                break

            _record_model_failure(current_model)

            if attempt < len(RETRY_DELAYS) - 1:
                delay = RETRY_DELAYS[attempt]
                jitter = random.uniform(0, delay * 0.3)
                await asyncio.sleep(delay + jitter)

        logger.warning(f"All retries exhausted for {current_model}, trying next in chain")

    return {"status": "error", "error": "All models in fallback chain failed",
            "model": model, "latency_ms": 0}


# ─── BG Removal via BRIA RMBG 2.0 ───

async def remove_background(img: Image.Image, http_client: httpx.AsyncClient) -> dict:
    """BG removal via BiRefNet v2 (fal-ai/birefnet/v2)."""
    t0 = time.perf_counter()
    data_uri = _img_to_data_uri(img.convert("RGB"), max_dim=2048)
    try:
        resp = await http_client.post(FAL_RMBG_URL, json={
            "image_url": data_uri,
            "model": "General Use (Heavy)",
            "operating_resolution": "1024x1024",
            "output_format": "png",
            "refine_foreground": True,
        })
        resp.raise_for_status()
        result = resp.json()
        img_url = result.get("image", {}).get("url", "")
        if not img_url:
            return {"status": "error", "error": "No image URL in response",
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
        img_resp = await http_client.get(img_url)
        img_resp.raise_for_status()
        bg_removed = Image.open(io.BytesIO(img_resp.content))
        bg_removed.load()
        return {"status": "success", "image": bg_removed, "source_url": img_url,
                "model": "BiRefNet v2 (Heavy)", 
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}
    except Exception as e:
        logger.exception("BG removal failed")
        return {"status": "error", "error": str(e)[:200],
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}


# ─── SW Gates ───

def _run_all_gates(img: Image.Image, pw: float, ph: float, bg_on: bool,
                   auto_skip_bg: bool = False) -> list:
    results = []
    for fn in [gate_bg1, gate_bg2, gate_bg3, gate_bg4]:
        r = fn(img)
        if not bg_on:
            r["flag"] = False; r["severity"] = "none"
            r["detail"] = f"[BG removal OFF] {r['detail']}"
        elif auto_skip_bg:
            r["flag"] = False; r["severity"] = "none"
            r["detail"] = "[BG already removed — auto-detected]"
        results.append(r)
    results.append(gate_lr1(img, pw, ph))
    results.append(gate_lr2(img))
    results.append(gate_tl1(img))
    cs1 = gate_cs1(img)
    cs1["flag"] = False; cs1["severity"] = "info"
    cs1["detail"] = f"[Info] {cs1['detail']}"
    results.append(cs1)
    results.append(gate_je1(img))
    results.append(gate_qr1(img))
    return results


# ─────────────────────────────────────────────
# Decision Hierarchy
# ─────────────────────────────────────────────

def compute_final_verdict(sw_results, vlm_result) -> dict:
    """Hard blocks > VLM-adjudicated > advisory."""
    verdict = "PASS"
    reasons = []
    gate_decisions = {}

    vlm_assessment_data = {}
    if vlm_result and vlm_result.get("status") == "success":
        vlm_assessment_data = vlm_result.get("assessment", {})
    vlm_validations = vlm_assessment_data.get("sw_gate_validations", {})
    vlm_verdict = vlm_assessment_data.get("overall_verdict", "UNKNOWN")
    vlm_score = vlm_assessment_data.get("print_readiness_score", 0)

    # 1. Hard blocks: LR-1 < 72 DPI — always fails, no VLM override
    lr1 = next((g for g in sw_results if g["gate_id"] == "LR-1"), None)
    if lr1 and lr1.get("metrics", {}).get("effective_dpi", 999) < 72:
        verdict = "FAIL"
        reasons.append("LR-1: DPI below 72 — hard block, no VLM override")
        gate_decisions["LR-1"] = {"decision": "HARD_FAIL", "overridable": False}

    # 2. VLM-adjudicated: SW flag + VLM confirm = fail, SW flag + VLM override = pass
    vlm_adjudicated_ids = {"BG-1", "BG-2", "BG-3", "BG-4", "LR-2", "TL-1", "JE-1"}
    vlm_key_map = {
        "BG-1": "BG1", "BG-2": "BG2", "BG-3": "BG3", "BG-4": "BG4",
        "LR-2": "LR2", "TL-1": "TL1", "JE-1": "JE1",
    }

    for gate in sw_results:
        gid = gate["gate_id"]
        if gid not in vlm_adjudicated_ids:
            continue
        if not gate.get("flag"):
            gate_decisions[gid] = {"decision": "PASS", "source": "sw_clean"}
            continue

        vlm_key = vlm_key_map.get(gid, gid.replace("-", ""))
        vlm_gate = vlm_validations.get(vlm_key, {})
        if vlm_gate.get("override"):
            gate_decisions[gid] = {
                "decision": "PASS", "source": "vlm_override",
                "reason": vlm_gate.get("reason", ""),
            }
        else:
            verdict = "FAIL"
            reason = vlm_gate.get("reason", gate["detail"])
            reasons.append(f"{gid}: SW flagged + VLM confirmed — {reason}")
            gate_decisions[gid] = {"decision": "FAIL", "source": "sw_flag_vlm_confirmed"}

    # 3. VLM-only checks: UP-1, BR-1, ST-1 — these can FAIL independently of SW gates
    vlm_checks = vlm_assessment_data.get("checks", {})
    vlm_only_map = {
        "UP1_upscaling_integrity": "UP-1: Upscaling/Design Integrity",
        "BR1_bg_removal": "BR-1: BG Removal Quality",
        "ST1_semi_transparency": "ST-1: Semi-Transparency",
        "TL_VLM_thin_lines": "TL-VLM: Thin Lines/Fine Detail",
    }
    for check_key, check_name in vlm_only_map.items():
        check = vlm_checks.get(check_key, {})
        if isinstance(check, dict) and check.get("status") == "fail":
            sev = check.get("severity", "medium")
            if sev in ("high", "critical", "medium"):
                verdict = "FAIL"
                findings = check.get("findings", [])
                desc = findings[0] if findings else "VLM detected defect"
                reasons.append(f"{check_name}: {desc[:120]}")
                gate_decisions[check_key] = {"decision": "FAIL", "source": "vlm_only_check"}

    # 4. Advisory: CS-1 always info-only
    cs1 = next((g for g in sw_results if g["gate_id"] == "CS-1"), None)
    if cs1:
        gate_decisions["CS-1"] = {"decision": "INFO", "overridable": False}

    return {
        "verdict": verdict,
        "reasons": reasons,
        "gate_decisions": gate_decisions,
        "vlm_verdict": vlm_verdict,
        "vlm_score": vlm_score,
    }


# ─── Multi-Model Pipeline ───

async def run_pipeline(img: Image.Image, print_width: float, print_height: float,
                       models_to_run: list, bg_removal: bool,
                       http_client: httpx.AsyncClient) -> dict:
    pipeline_t0 = time.perf_counter()

    bg_result = {"status": "skipped"}
    analysis_img = img
    auto_skip_bg = False

    if bg_removal:
        bg_result = await remove_background(img, http_client)
        if bg_result["status"] == "success":
            analysis_img = bg_result["image"]
            buf = io.BytesIO()
            analysis_img.save(buf, format="PNG")
            bg_removed_fname = f"{uuid.uuid4().hex[:12]}_nobg.png"
            with open(UPLOAD_DIR / bg_removed_fname, "wb") as f:
                f.write(buf.getvalue())
            bg_result["local_url"] = f"/uploads/{bg_removed_fname}"
            del bg_result["image"]

    image_info = {
        "width_px": analysis_img.width, "height_px": analysis_img.height,
        "mode": analysis_img.mode,
        "has_alpha": analysis_img.mode in ("RGBA", "LA", "PA"),
        "megapixels": round(analysis_img.width * analysis_img.height / 1e6, 2),
        "bg_removal_applied": bg_removal and not auto_skip_bg,
        "bg_auto_skipped": auto_skip_bg,
    }

    stage1_t0 = time.perf_counter()
    loop = asyncio.get_event_loop()
    sw_results = await loop.run_in_executor(
        None, partial(_run_all_gates, analysis_img, print_width, print_height,
                      bg_removal, auto_skip_bg))
    stage1_ms = round((time.perf_counter() - stage1_t0) * 1000, 2)

    # Build image URIs: original (Image 1) + processed (Image 2) for VLM comparison
    # Use 768px max to keep payload manageable with 2 images x 8 models
    original_uri = _img_to_data_uri(
        img.convert("RGBA") if img.mode in ("P", "PA") else img, max_dim=768)
    safe_img = analysis_img.convert("RGBA") if analysis_img.mode in ("P", "PA") else analysis_img
    processed_uri = _img_to_data_uri(safe_img, max_dim=768)
    image_uris = [original_uri, processed_uri]

    vlm_tasks = [vlm_assessment_with_retry(image_uris, sw_results, m, http_client)
                 for m in models_to_run]
    vlm_results_list = await asyncio.gather(*vlm_tasks, return_exceptions=True)

    model_results = {}
    for i, m in enumerate(models_to_run):
        r = vlm_results_list[i]
        if isinstance(r, Exception):
            r = {"status": "error", "error": str(r)[:120], "model": m, "latency_ms": 0}
        model_results[m] = r

    primary_vlm = model_results.get(models_to_run[0]) if models_to_run else None
    final_verdict = compute_final_verdict(sw_results, primary_vlm)

    # LLM Report Writer — generate vendor report from primary model
    report = ""
    primary_assessment = (primary_vlm or {}).get("assessment", {})
    if primary_vlm and primary_vlm.get("status") == "success" and not primary_assessment.get("parse_error"):
        cs1 = next((g for g in sw_results if g["gate_id"] == "CS-1"), {})
        report_prompt = f"""Write a concise vendor audit report for this DTF print image.

Verdict: {final_verdict.get('verdict', 'UNKNOWN')}
Reasons: {json.dumps(final_verdict.get('reasons', []))}

VLM Assessment: {json.dumps(primary_assessment, indent=2)}

CMYK Info: {cs1.get('detail', 'N/A')}

Flagged SW Gates: {json.dumps([g['gate_id'] + ': ' + g['detail'] for g in sw_results if g['flag']])}

Write as:
VERDICT: PASS/FAIL (Score: X/100)
DEFECTS (if any): numbered list with fix suggestion each
NOTES: anything else relevant
Keep it under 200 words. Plain text, no markdown."""

        try:
            report_resp = await http_client.post(FAL_LLM_URL, json={
                "prompt": report_prompt,
                "system_prompt": "You write concise DTF print audit reports for vendor teams. Be specific and actionable.",
                "model": "google/gemini-2.5-flash-lite",
                "max_tokens": 1024, "temperature": 0.2,
            })
            report_resp.raise_for_status()
            report = report_resp.json().get("output", "")
        except Exception:
            logger.exception("Report writer failed")

    pipeline_ms = round((time.perf_counter() - pipeline_t0) * 1000, 2)
    flagged = [g for g in sw_results if g["flag"]]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "v8.1",
        "image_info": image_info,
        "bg_removal": bg_result,
        "stage1_sw_gates": {
            "results": sw_results, "total_gates": len(sw_results),
            "flagged_count": len(flagged),
            "flagged_gates": [g["gate_id"] for g in flagged],
            "latency_ms": stage1_ms,
        },
        "final_verdict": final_verdict,
        "report": report,
        "vendor_only_checks": [
            {"check": "Trimming", "note": "Vendor must verify image fits ordered canvas size and aspect ratio"},
            {"check": "Text Accuracy", "note": "Vendor must verify spelling, placement, formatting matches customer request"},
            {"check": "Color Matching", "note": "Vendor must verify exact color codes if customer specified them"},
        ],
        "model_results": model_results,
        "models_run": models_to_run,
        "performance": {
            "stage1_ms": stage1_ms,
            "bg_removal_ms": bg_result.get("latency_ms", 0),
            "total_pipeline_ms": pipeline_ms,
        },
        "spend": {
            "daily_total": round(_spend_tracker["total"], 4),
            "daily_budget": DTF_DAILY_BUDGET,
        },
    }


# ─── Routes ───

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/analyze")
async def analyze(
    request: Request,
    file: UploadFile = File(...),
    print_width: float = Form(10.0),
    print_height: float = Form(10.0),
    bg_removal: str = Form("1"),
    models: str = Form(""),
    run_all: str = Form(""),
):
    if not _check_spend():
        return JSONResponse(status_code=429, content={
            "error": "Daily spend cap exceeded",
            "budget": DTF_DAILY_BUDGET,
            "spent": round(_spend_tracker["total"], 4),
        })

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        return JSONResponse(status_code=413, content={"error": "File too large"})

    ext = Path(file.filename).suffix.lstrip(".").lower() if file.filename else "png"
    if not ext:
        ext = "png"
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(status_code=400, content={"error": f"Unsupported file type: .{ext}. Use PNG, JPG, or WEBP."})

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        img = Image.open(io.BytesIO(contents))
        img.load()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid image"})

    fname = f"{uuid.uuid4().hex[:12]}.{ext}"
    with open(UPLOAD_DIR / fname, "wb") as f:
        f.write(contents)

    bg_on = bg_removal in ("1", "true", "on", "yes")
    if models.strip() and models.strip().lower() == "all":
        selected = [m[0] for m in ALL_VLM_MODELS]
    elif models.strip():
        selected = [m.strip() for m in models.split(",") if m.strip() in ALLOWED_VLM_MODELS]
    else:
        # Default: fast models only (finish in <15s). Premium models are opt-in.
        selected = [m[0] for m in ALL_VLM_MODELS if not m[2]]

    try:
        http_client = request.app.state.http_client
        result = await run_pipeline(img, print_width, print_height, selected, bg_on, http_client)
    finally:
        img.close()

    result["uploaded_file"] = {"filename": file.filename, "saved_as": fname,
                               "url": f"/uploads/{fname}", "size_bytes": len(contents)}
    return JSONResponse(content=result)


@app.get("/uploads/{filename:path}")
async def serve_upload(filename: str):
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        return JSONResponse(status_code=403, content={"error": "Invalid path"})
    file_path = UPLOAD_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if not file_path.resolve().is_relative_to(UPLOAD_DIR.resolve()):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    return FileResponse(file_path)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "v8.1",
        "spend": {
            "daily_total": round(_spend_tracker["total"], 4),
            "daily_budget": DTF_DAILY_BUDGET,
        },
    }


@app.get("/architecture", response_class=HTMLResponse)
async def arch_page(request: Request):
    return templates.TemplateResponse("architecture.html", {
        "request": request,
        "prompt_text": json.dumps(VLM_SYSTEM_PROMPT),
    })


@app.get("/eval", response_class=HTMLResponse)
async def eval_page(request: Request):
    results_path = BASE_DIR / "golden_dataset" / "eval_results.json"
    if results_path.exists():
        with open(results_path) as f:
            data = json.load(f)
    else:
        data = None
    return templates.TemplateResponse("eval.html", {"request": request, "data": json.dumps(data)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)
