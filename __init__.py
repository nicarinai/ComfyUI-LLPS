"""
ComfyUI-LLPS-v1
LiveLatentPreviewer & Saver v1

This v1 intentionally avoids global monkeypatching. It provides:
- LLPS Config: creates preview/save settings.
- LLPS KSampler: KSampler-compatible sampler that uses LLPS settings for live latent preview and/or saving.
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple

import torch
from PIL import Image

import comfy.sample
import comfy.samplers
import comfy.utils
from comfy.cli_args import args
import folder_paths
import latent_preview


LLPS_CATEGORY = "LLPS/Live Latent Preview"


def _sanitize_piece(text: str, fallback: str = "LLPS") -> str:
    text = str(text or "").strip()
    if not text:
        return fallback
    # Windows-safe and reasonably portable.
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip().strip(".")
    return text[:120] or fallback


def _bool(v: Any) -> bool:
    return bool(v) and str(v).lower() not in {"false", "0", "none", "off"}


@dataclass
class LLPSConfigData:
    enabled: bool = True
    live_preview_method: str = "latent2rgb"  # server_default, none, auto, latent2rgb, taesd
    show_preview: bool = True
    save_preview: bool = False
    save_base_path: str = ""
    subfolder: str = ""
    filename_prefix: str = "LLPS"
    image_format: str = "jpg"  # jpg, png, webp
    save_every_n_steps: int = 1
    save_metadata_json: bool = True
    jpeg_quality: int = 90

    @classmethod
    def from_obj(cls, obj: Optional[Dict[str, Any]]) -> "LLPSConfigData":
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, dict):
            return cls()
        data = cls()
        for k in asdict(data).keys():
            if k in obj:
                setattr(data, k, obj[k])
        data.enabled = _bool(data.enabled)
        data.show_preview = _bool(data.show_preview)
        data.save_preview = _bool(data.save_preview)
        data.save_metadata_json = _bool(data.save_metadata_json)
        data.save_every_n_steps = max(1, int(data.save_every_n_steps or 1))
        data.jpeg_quality = max(1, min(100, int(data.jpeg_quality or 90)))
        data.live_preview_method = str(data.live_preview_method or "server_default")
        if data.live_preview_method == "default":
            data.live_preview_method = "server_default"
        data.image_format = str(data.image_format or "jpg").lower()
        if data.image_format == "jpeg":
            data.image_format = "jpg"
        if data.image_format not in {"jpg", "png", "webp"}:
            data.image_format = "jpg"
        return data


def _resolve_save_dir(cfg: LLPSConfigData, node_label: str) -> str:
    base = cfg.save_base_path.strip()
    if not base:
        base = os.path.join(folder_paths.get_output_directory(), "LLPS")
    # Relative paths are placed below ComfyUI output directory for safety/predictability.
    if not os.path.isabs(base):
        base = os.path.join(folder_paths.get_output_directory(), base)

    sub = _sanitize_piece(cfg.subfolder, "")
    if sub:
        base = os.path.join(base, sub)
    else:
        # For v1, separate the LLPS sampler output by node label automatically.
        base = os.path.join(base, _sanitize_piece(node_label, "LLPS_KSampler"))

    os.makedirs(base, exist_ok=True)
    return base


def _previewer_for_method(model, method: str):
    """Create a ComfyUI latent previewer for a specific method, then restore global setting.

    ComfyUI's latent_preview.get_previewer currently reads args.preview_method. v1 therefore
    changes it only while constructing the previewer, not during the whole sample.
    """
    if method in {"none", "disabled"}:
        return None

    previous = args.preview_method
    try:
        if method not in {"server_default", "default", ""}:
            latent_preview.set_preview_method(method)
        # server_default/default: keep current server setting.
        return latent_preview.get_previewer(model.load_device, model.model.latent_format)
    finally:
        args.preview_method = previous


def _save_preview_image(
    preview_tuple: Tuple[Any, ...],
    save_dir: str,
    cfg: LLPSConfigData,
    node_label: str,
    step: int,
    total_steps: int,
    seed: int,
    method: str,
) -> Optional[str]:
    if not preview_tuple or len(preview_tuple) < 2:
        return None

    img = preview_tuple[1]
    if not isinstance(img, Image.Image):
        return None

    max_res = preview_tuple[2] if len(preview_tuple) >= 3 else None
    out = img.copy()
    if isinstance(max_res, int) and max_res > 0:
        out.thumbnail((max_res, max_res), Image.Resampling.LANCZOS)

    ext = cfg.image_format
    pil_format = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[ext]
    prefix = _sanitize_piece(cfg.filename_prefix, "LLPS")
    label = _sanitize_piece(node_label, "KSampler")
    filename = f"{prefix}_{label}_seed-{int(seed)}_step-{step + 1:04d}-of-{int(total_steps):04d}_{method}.{ext}"
    path = os.path.join(save_dir, filename)

    save_kwargs = {}
    if pil_format == "JPEG":
        if out.mode not in {"RGB", "L"}:
            out = out.convert("RGB")
        save_kwargs.update({"quality": cfg.jpeg_quality, "optimize": True})
    elif pil_format == "WEBP":
        save_kwargs.update({"quality": cfg.jpeg_quality})

    out.save(path, pil_format, **save_kwargs)
    return path


def _make_llps_callback(model, steps: int, cfg: LLPSConfigData, node_label: str, seed: int):
    preview_format_for_comfy = "JPEG"
    pbar = comfy.utils.ProgressBar(steps)

    if not cfg.enabled or cfg.live_preview_method in {"none", "disabled"}:
        previewer = None
    else:
        previewer = _previewer_for_method(model, cfg.live_preview_method)

    save_dir = None
    saved_files = []
    if cfg.enabled and cfg.save_preview:
        save_dir = _resolve_save_dir(cfg, node_label)

    def callback(step, x0, x, total_steps):
        preview_tuple = None
        wants_frame = cfg.enabled and previewer is not None and (cfg.show_preview or cfg.save_preview)

        if wants_frame:
            try:
                # In some environments the first callback-delivered preview can be a stale warm-up frame.
                # We still show it live (to keep UI behavior natural), but saving logic below skips callback
                # step 0 and explicitly flushes the final latent after sampling ends.
                preview_tuple = previewer.decode_latent_to_preview_image(preview_format_for_comfy, x0)
            except Exception as e:
                logging.exception("[LLPS] Failed to decode preview at step %s: %s", step, e)
                preview_tuple = None

        if cfg.enabled and cfg.show_preview and preview_tuple is not None:
            pbar.update_absolute(step + 1, total_steps, preview_tuple)
        else:
            # Keeps normal progress visible without emitting preview frames.
            pbar.update_absolute(step + 1, total_steps, None)

        if cfg.enabled and cfg.save_preview and preview_tuple is not None:
            # Empirically, saving callback step 0 can capture a stale warm-up frame in some preview methods
            # (notably TAESD on some setups). We therefore skip callback step 0 for file saving, shift
            # numbering by one, and force-save the final latent after sampling completes.
            if step > 0:
                adjusted_step = step - 1
                if (adjusted_step % cfg.save_every_n_steps) == 0:
                    try:
                        saved = _save_preview_image(
                            preview_tuple,
                            save_dir,
                            cfg,
                            node_label,
                            int(adjusted_step),
                            int(total_steps),
                            int(seed),
                            cfg.live_preview_method,
                        )
                        if saved:
                            saved_files.append(saved)
                    except Exception as e:
                        logging.exception("[LLPS] Failed to save preview at step %s: %s", step, e)

    callback.llps_saved_files = saved_files
    callback.llps_save_dir = save_dir
    callback.llps_config = cfg
    callback.llps_previewer = previewer
    callback.llps_preview_format = preview_format_for_comfy
    return callback



class LLPSConfig:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True}),
                "live_preview_method": (["server_default", "none", "auto", "latent2rgb", "taesd"], {"default": "latent2rgb"}),
                "show_preview": ("BOOLEAN", {"default": True}),
                "save_preview": ("BOOLEAN", {"default": False}),
                "save_base_path": ("STRING", {"default": "", "multiline": False}),
                "subfolder": ("STRING", {"default": "", "multiline": False}),
                "filename_prefix": ("STRING", {"default": "LLPS", "multiline": False}),
                "image_format": (["jpg", "png", "webp"], {"default": "jpg"}),
                "save_every_n_steps": ("INT", {"default": 1, "min": 1, "max": 1000, "step": 1}),
                "save_metadata_json": ("BOOLEAN", {"default": True}),
                "jpeg_quality": ("INT", {"default": 90, "min": 1, "max": 100, "step": 1}),
            }
        }

    RETURN_TYPES = ("LLPS_CONFIG",)
    RETURN_NAMES = ("llps_config",)
    FUNCTION = "make_config"
    CATEGORY = LLPS_CATEGORY

    def make_config(
        self,
        enabled=True,
        live_preview_method="latent2rgb",
        show_preview=True,
        save_preview=False,
        save_base_path="",
        subfolder="",
        filename_prefix="LLPS",
        image_format="jpg",
        save_every_n_steps=1,
        save_metadata_json=True,
        jpeg_quality=90,
    ):
        cfg = LLPSConfigData(
            enabled=enabled,
            live_preview_method=live_preview_method,
            show_preview=show_preview,
            save_preview=save_preview,
            save_base_path=save_base_path,
            subfolder=subfolder,
            filename_prefix=filename_prefix,
            image_format=image_format,
            save_every_n_steps=save_every_n_steps,
            save_metadata_json=save_metadata_json,
            jpeg_quality=jpeg_quality,
        )
        return (asdict(LLPSConfigData.from_obj(asdict(cfg))),)


class LLPSKSampler:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "steps": ("INT", {"default": 20, "min": 1, "max": 10000}),
                "cfg": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0, "step": 0.1, "round": 0.01}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS,),
                "scheduler": (comfy.samplers.KSampler.SCHEDULERS,),
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "latent_image": ("LATENT",),
                "denoise": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
                "llps_config": ("LLPS_CONFIG",),
            },
            "optional": {
                "node_label": ("STRING", {"default": "LLPS_KSampler", "multiline": False}),
            },
        }

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("latent",)
    FUNCTION = "sample"
    CATEGORY = LLPS_CATEGORY

    def sample(
        self,
        model,
        seed,
        steps,
        cfg,
        sampler_name,
        scheduler,
        positive,
        negative,
        latent_image,
        denoise=1.0,
        llps_config=None,
        node_label="LLPS_KSampler",
    ):
        cfg_data = LLPSConfigData.from_obj(llps_config)
        samples_tensor = latent_image["samples"]
        batch_inds = latent_image.get("batch_index", None)

        noise = comfy.sample.prepare_noise(samples_tensor, seed, batch_inds)
        callback = _make_llps_callback(model, int(steps), cfg_data, node_label, int(seed))

        result_samples = comfy.sample.sample(
            model,
            noise,
            int(steps),
            float(cfg),
            sampler_name,
            scheduler,
            positive,
            negative,
            samples_tensor,
            denoise=float(denoise),
            disable_noise=False,
            start_step=None,
            last_step=None,
            force_full_denoise=True,
            noise_mask=latent_image.get("noise_mask", None),
            callback=callback,
            disable_pbar=False,
            seed=int(seed),
        )

        # Explicitly flush the final preview from the finished latent. This fixes the common case
        # where callback-delivered previews are effectively one frame late for file saving.
        if cfg_data.enabled and cfg_data.save_preview and callback.llps_save_dir and callback.llps_previewer is not None:
            try:
                final_preview_tuple = callback.llps_previewer.decode_latent_to_preview_image(
                    callback.llps_preview_format, result_samples
                )
                saved = _save_preview_image(
                    final_preview_tuple,
                    callback.llps_save_dir,
                    cfg_data,
                    node_label,
                    int(steps) - 1,
                    int(steps),
                    int(seed),
                    cfg_data.live_preview_method,
                )
                if saved:
                    callback.llps_saved_files.append(saved)
            except Exception as e:
                logging.exception("[LLPS] Failed to flush final preview image: %s", e)

        out = latent_image.copy()
        out["samples"] = result_samples

        if cfg_data.save_preview and cfg_data.save_metadata_json and callback.llps_save_dir:
            try:
                metadata = {
                    "node": "LLPS KSampler",
                    "node_label": node_label,
                    "seed": int(seed),
                    "steps": int(steps),
                    "cfg": float(cfg),
                    "sampler_name": sampler_name,
                    "scheduler": scheduler,
                    "denoise": float(denoise),
                    "llps_config": asdict(cfg_data),
                    "saved_count": len(callback.llps_saved_files),
                    "saved_files": [os.path.basename(p) for p in callback.llps_saved_files],
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                with open(os.path.join(callback.llps_save_dir, "metadata.json"), "w", encoding="utf-8") as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logging.exception("[LLPS] Failed to write metadata.json: %s", e)

        return (out,)


NODE_CLASS_MAPPINGS = {
    "LLPSConfig": LLPSConfig,
    "LLPSKSampler": LLPSKSampler,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LLPSConfig": "LLPS Config",
    "LLPSKSampler": "LLPS KSampler",
}
