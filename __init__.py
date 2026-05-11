"""
ComfyUI-LLPS-v1.2
LiveLatentPreviewer & Saver v1.2

This v1 intentionally avoids global monkeypatching. It provides:
- LLPS Controller: workflow-level preview management declaration for v2 UI.
- Legacy LLPS Config and LLPS KSampler nodes for v1.2 callback-based preview saving.
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
import base64
import threading
import urllib.error
import urllib.request
import uuid
from io import BytesIO
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Tuple

import torch
from PIL import Image

import nodes as comfy_nodes
import comfy.sample
import comfy.samplers
import comfy.utils
from comfy.cli_args import args
from comfy_execution.utils import get_executing_context
import folder_paths
import latent_preview


LLPS_CATEGORY = "LLPS/Live Latent Preview"
LLPS_LEGACY_CATEGORY = "LLPS/Legacy v1.2"
TAESD_PREVIEWER_CLASSES = {"TAESDPreviewerImpl", "TAEHVPreviewerImpl"}
TAESD_DECODER_URLS = {
    "taesd_decoder": "https://raw.githubusercontent.com/madebyollin/taesd/main/taesd_decoder.pth",
    "taesdxl_decoder": "https://raw.githubusercontent.com/madebyollin/taesd/main/taesdxl_decoder.pth",
    "taesd3_decoder": "https://raw.githubusercontent.com/madebyollin/taesd/main/taesd3_decoder.pth",
    "taef1_decoder": "https://raw.githubusercontent.com/madebyollin/taesd/main/taef1_decoder.pth",
}
LLPS_SAMPLER_TYPES = {"KSampler", "KSamplerAdvanced"}
_LLPS_RUN_LOCK = threading.Lock()
_LLPS_PREVIEW_RUNS: Dict[Tuple[str, str], list[Dict[str, Any]]] = {}


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
    preview_max_side: int = 512

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
        data.preview_max_side = 1024 if int(data.preview_max_side or 512) >= 1024 else 512
        data.live_preview_method = str(data.live_preview_method or "server_default")
        if data.live_preview_method == "default":
            data.live_preview_method = "server_default"
        data.image_format = str(data.image_format or "jpg").lower()
        if data.image_format == "jpeg":
            data.image_format = "jpg"
        if data.image_format not in {"jpg", "png", "webp"}:
            data.image_format = "jpg"
        return data


@dataclass
class LLPSControllerData:
    enabled: bool = True
    live_preview_method: str = "latent2rgb"
    save_also: bool = False
    save_base_path: str = ""
    subfolder: str = ""
    filename_prefix: str = "LLPS"
    image_format: str = "jpg"
    save_every_n_steps: int = 1
    save_metadata_json: bool = True
    jpeg_quality: int = 90
    preview_max_side: int = 512
    controller_node_id: Optional[str] = None

    @classmethod
    def from_obj(cls, obj: Optional[Dict[str, Any]]) -> "LLPSControllerData":
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, dict):
            return cls()
        data = cls()
        for key in asdict(data).keys():
            if key in obj:
                setattr(data, key, obj[key])
        data.enabled = _bool(data.enabled)
        data.save_also = _bool(data.save_also)
        data.live_preview_method = str(data.live_preview_method or "server_default")
        if data.live_preview_method == "default":
            data.live_preview_method = "server_default"
        data.image_format = str(data.image_format or "jpg").lower()
        if data.image_format == "jpeg":
            data.image_format = "jpg"
        if data.image_format not in {"jpg", "png", "webp"}:
            data.image_format = "jpg"
        data.save_every_n_steps = max(1, int(data.save_every_n_steps or 1))
        data.jpeg_quality = max(1, min(100, int(data.jpeg_quality or 90)))
        data.preview_max_side = 1024 if int(data.preview_max_side or 512) >= 1024 else 512
        return data

    def to_legacy_config(self, show_preview: bool, save_preview: bool) -> LLPSConfigData:
        return LLPSConfigData.from_obj(
            {
                "enabled": self.enabled,
                "live_preview_method": self.live_preview_method,
                "show_preview": show_preview,
                "save_preview": save_preview,
                "save_base_path": self.save_base_path,
                "subfolder": self.subfolder,
                "filename_prefix": self.filename_prefix,
                "image_format": self.image_format,
                "save_every_n_steps": self.save_every_n_steps,
                "save_metadata_json": self.save_metadata_json,
                "jpeg_quality": self.jpeg_quality,
                "preview_max_side": self.preview_max_side,
            }
        )


def _preview_method_value(method: Any) -> str:
    if hasattr(method, "value"):
        return str(method.value)
    return str(method)


def _previewer_identity(previewer: Any) -> Dict[str, Any]:
    if previewer is None:
        return {
            "actual_previewer_class": None,
            "actual_previewer_module": None,
            "actual_previewer_id": None,
        }
    return {
        "actual_previewer_class": previewer.__class__.__name__,
        "actual_previewer_module": previewer.__class__.__module__,
        "actual_previewer_id": id(previewer),
    }


def _is_taesd_previewer(previewer: Any) -> bool:
    return previewer is not None and previewer.__class__.__name__ in TAESD_PREVIEWER_CLASSES


def _previewer_fallback_reason(requested_method: str, resolved_method: str, previewer: Any) -> Optional[str]:
    requested = str(requested_method or "server_default")
    resolved = str(resolved_method or "none")
    cls_name = previewer.__class__.__name__ if previewer is not None else None

    if previewer is None:
        if requested in {"none", "disabled"} or resolved == "none":
            return "preview_disabled"
        return "previewer_unavailable"
    if requested == "server_default":
        return "server_default_used"
    if requested == "auto":
        return "auto_resolved_by_comfy"
    if requested == "taesd" and cls_name not in TAESD_PREVIEWER_CLASSES:
        return "taesd_requested_but_taesd_previewer_unavailable_or_fell_back"
    if requested == "latent2rgb" and cls_name != "Latent2RGBPreviewer":
        return "latent2rgb_requested_but_different_previewer_returned"
    return None


def _taesd_decoder_candidates(decoder_name: Optional[str]) -> list[str]:
    if not decoder_name:
        return []
    try:
        if hasattr(folder_paths, "get_filename_list_"):
            filenames = folder_paths.get_filename_list_("vae_approx")[0]
        else:
            filenames = folder_paths.get_filename_list("vae_approx")
        return [
            filename
            for filename in filenames
            if filename.startswith(decoder_name)
        ]
    except Exception as e:
        logging.warning("[LLPS] Could not list TAESD decoder candidates for %s: %s", decoder_name, e)
        return []


def _refresh_vae_approx_cache() -> None:
    try:
        folder_paths.filename_list_cache.pop("vae_approx", None)
    except Exception:
        pass
    try:
        folder_paths.cache_helper.clear()
    except Exception:
        pass


def _ensure_taesd_decoder_available(decoder_name: Optional[str]) -> Dict[str, Any]:
    result = {
        "attempted": False,
        "success": False,
        "decoder_name": decoder_name,
        "url": None,
        "path": None,
        "error": None,
    }
    if not decoder_name:
        result["error"] = "latent_format_has_no_taesd_decoder_name"
        return result

    candidates = _taesd_decoder_candidates(decoder_name)
    if candidates:
        result["success"] = True
        result["path"] = folder_paths.get_full_path("vae_approx", candidates[0])
        return result

    url = TAESD_DECODER_URLS.get(decoder_name)
    result["url"] = url
    if not url:
        result["error"] = "no_known_download_url_for_decoder"
        return result

    folders = folder_paths.get_folder_paths("vae_approx")
    if not folders:
        result["error"] = "vae_approx_folder_not_registered"
        return result

    target_dir = folders[0]
    target_path = os.path.join(target_dir, f"{decoder_name}.pth")
    result["path"] = target_path
    result["attempted"] = True

    try:
        os.makedirs(target_dir, exist_ok=True)
        logging.info("[LLPS] Downloading missing TAESD decoder %s to %s", decoder_name, target_path)
        request = urllib.request.Request(url, headers={"User-Agent": "ComfyUI-LLPS/2"})
        tmp_path = f"{target_path}.part"
        with urllib.request.urlopen(request, timeout=60) as response, open(tmp_path, "wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        if os.path.getsize(tmp_path) <= 0:
            raise RuntimeError("downloaded decoder file is empty")
        os.replace(tmp_path, target_path)
        _refresh_vae_approx_cache()
        folder_paths.get_filename_list("vae_approx")
        result["success"] = True
    except (OSError, urllib.error.URLError, RuntimeError) as e:
        result["error"] = str(e)
        try:
            if os.path.exists(f"{target_path}.part"):
                os.remove(f"{target_path}.part")
        except OSError:
            pass
        logging.warning("[LLPS] Could not install TAESD decoder %s: %s", decoder_name, e)

    return result


def _previewer_for_method_with_info(model, method: str, requested_method: Optional[str] = None):
    requested = str(requested_method or method or "server_default")
    source = str(method or "server_default")
    previous = args.preview_method
    previous_method = _preview_method_value(previous)
    info = {
        "requested_live_preview_method": requested,
        "requested_preview_source_method": source,
        "previous_global_preview_method": previous_method,
        "resolved_preview_source_method": None,
        "actual_previewer_class": None,
        "actual_previewer_module": None,
        "actual_previewer_id": None,
        "fallback_reason": None,
        "latent_format_class": None,
        "latent_format_module": None,
        "taesd_decoder_name": None,
        "taesd_decoder_candidates": [],
        "taesd_decoder_install": None,
        "latent_rgb_factors_available": None,
    }

    latent_format = getattr(getattr(model, "model", None), "latent_format", None)
    if latent_format is not None:
        info["latent_format_class"] = latent_format.__class__.__name__
        info["latent_format_module"] = latent_format.__class__.__module__
        info["taesd_decoder_name"] = getattr(latent_format, "taesd_decoder_name", None)
        info["taesd_decoder_candidates"] = _taesd_decoder_candidates(info["taesd_decoder_name"])
        info["latent_rgb_factors_available"] = getattr(latent_format, "latent_rgb_factors", None) is not None

    if source in {"none", "disabled"}:
        info["resolved_preview_source_method"] = "none"
        info["fallback_reason"] = _previewer_fallback_reason(requested, "none", None)
        return None, info

    try:
        if source not in {"server_default", "default", ""}:
            latent_preview.set_preview_method(source)
        resolved = _preview_method_value(args.preview_method)
        previewer = latent_preview.get_previewer(model.load_device, model.model.latent_format)
        info.update(_previewer_identity(previewer))
        info["resolved_preview_source_method"] = resolved
        info["fallback_reason"] = _previewer_fallback_reason(requested, resolved, previewer)
        if source == "taesd" and not _is_taesd_previewer(previewer):
            install_info = _ensure_taesd_decoder_available(info["taesd_decoder_name"])
            info["taesd_decoder_install"] = install_info
            info["taesd_decoder_candidates"] = _taesd_decoder_candidates(info["taesd_decoder_name"])
            if install_info.get("success"):
                previewer = latent_preview.get_previewer(model.load_device, model.model.latent_format)
                info.update(_previewer_identity(previewer))
                info["fallback_reason"] = _previewer_fallback_reason(requested, resolved, previewer)
        return previewer, info
    finally:
        args.preview_method = previous


def _save_base_dir(cfg: LLPSConfigData) -> str:
    base = cfg.save_base_path.strip()
    if not base:
        base = os.path.join(folder_paths.get_output_directory(), "LLPS")
    # Relative paths are placed below ComfyUI output directory for safety/predictability.
    if not os.path.isabs(base):
        base = os.path.join(folder_paths.get_output_directory(), base)
    return base


def _effective_subfolder(cfg: LLPSConfigData, node_label: str, sampler_subfolder: Optional[str] = None) -> str:
    per_sampler = _sanitize_piece(sampler_subfolder or "", "")
    if per_sampler:
        return per_sampler
    controller_subfolder = _sanitize_piece(cfg.subfolder, "")
    if controller_subfolder:
        return controller_subfolder
    return _sanitize_piece(node_label, "LLPS_KSampler")


def _resolve_save_dir(cfg: LLPSConfigData, node_label: str, sampler_subfolder: Optional[str] = None) -> str:
    base = os.path.join(_save_base_dir(cfg), _effective_subfolder(cfg, node_label, sampler_subfolder))
    os.makedirs(base, exist_ok=True)
    return base


def _previewer_for_method(model, method: str):
    """Create a ComfyUI latent previewer for a specific method, then restore global setting.

    ComfyUI's latent_preview.get_previewer currently reads args.preview_method. v1 therefore
    changes it only while constructing the previewer, not during the whole sample.
    """
    previewer, _info = _previewer_for_method_with_info(model, method, method)
    return previewer


def _normalize_preview_image(img: Image.Image, preview_max_side: int) -> Tuple[Image.Image, Dict[str, int]]:
    original_width, original_height = img.size
    max_side = 1024 if int(preview_max_side or 512) >= 1024 else 512
    longest = max(original_width, original_height, 1)
    scale = max_side / longest
    target_width = max(1, int(round(original_width * scale)))
    target_height = max(1, int(round(original_height * scale)))

    if (target_width, target_height) == img.size:
        out = img.copy()
    else:
        out = img.resize((target_width, target_height), Image.Resampling.LANCZOS)

    return out, {
        "preview_max_side": max_side,
        "preview_original_width": int(original_width),
        "preview_original_height": int(original_height),
        "preview_target_width": int(target_width),
        "preview_target_height": int(target_height),
    }


def _normalize_preview_tuple(preview_tuple: Tuple[Any, ...], preview_max_side: int) -> Tuple[Optional[Tuple[Any, ...]], Dict[str, int]]:
    if not preview_tuple or len(preview_tuple) < 2 or not isinstance(preview_tuple[1], Image.Image):
        return None, {}
    out, info = _normalize_preview_image(preview_tuple[1], preview_max_side)
    image_type = preview_tuple[0] if len(preview_tuple) > 0 else "JPEG"
    return (image_type, out, None), info


def _preview_data_url(img: Image.Image, image_type: str = "JPEG", quality: int = 90) -> str:
    pil_format = "PNG" if str(image_type).upper() == "PNG" else "JPEG"
    out = img
    if pil_format == "JPEG" and out.mode not in {"RGB", "L"}:
        out = out.convert("RGB")
    buffer = BytesIO()
    kwargs = {"quality": quality} if pil_format == "JPEG" else {}
    out.save(buffer, pil_format, **kwargs)
    mime = "image/png" if pil_format == "PNG" else "image/jpeg"
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _send_llps_preview_event(payload: Dict[str, Any]) -> None:
    try:
        import server as comfy_server

        instance = comfy_server.PromptServer.instance
        instance.send_sync("llps_preview", payload, instance.client_id)
    except Exception as e:
        logging.debug("[LLPS] Could not send preview event: %s", e)


def _save_preview_image(
    preview_tuple: Tuple[Any, ...],
    save_dir: str,
    cfg: LLPSConfigData,
    node_label: str,
    step: int,
    total_steps: int,
    seed: int,
    method: str,
    temp_run_id: Optional[str] = None,
    sampler_node_id: Optional[str] = None,
) -> Optional[str]:
    if not preview_tuple or len(preview_tuple) < 2:
        return None

    img = preview_tuple[1]
    if not isinstance(img, Image.Image):
        return None

    out = img.copy()
    max_res = preview_tuple[2] if len(preview_tuple) >= 3 else None
    if isinstance(max_res, int) and max_res > 0:
        out.thumbnail((max_res, max_res), Image.Resampling.LANCZOS)

    ext = cfg.image_format
    pil_format = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[ext]
    prefix = _sanitize_piece(cfg.filename_prefix, "LLPS")
    label = _sanitize_piece(node_label, "KSampler")
    if temp_run_id:
        node_part = _sanitize_piece(sampler_node_id or label, "node")
        run_part = _sanitize_piece(temp_run_id, "run")
        filename = f"tmp_{run_part}_n{node_part}_step{step + 1:04d}of{int(total_steps):04d}.{ext}"
    else:
        filename = f"{prefix}_{label}_step{step + 1:04d}of{int(total_steps):04d}_{method}.{ext}"
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


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    counter = 2
    while True:
        candidate = f"{root}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _find_upstream_sampler_ids(prompt: Optional[Dict[str, Any]], node_id: Optional[str]) -> list[str]:
    if not isinstance(prompt, dict) or node_id is None:
        return []
    found: list[str] = []
    visited: set[str] = set()

    def visit_value(value: Any) -> None:
        if _is_prompt_link(value):
            visit_node(str(value[0]))

    def visit_node(current_id: str) -> None:
        if current_id in visited:
            return
        visited.add(current_id)
        node = prompt.get(current_id)
        if not isinstance(node, dict):
            return
        if node.get("class_type") in LLPS_SAMPLER_TYPES:
            found.append(current_id)
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict):
            for value in inputs.values():
                if isinstance(value, list) and _is_prompt_link(value):
                    visit_value(value)
                elif isinstance(value, list):
                    for item in value:
                        visit_value(item)

    save_node = prompt.get(str(node_id))
    if isinstance(save_node, dict):
        inputs = save_node.get("inputs", {})
        if isinstance(inputs, dict):
            visit_value(inputs.get("images"))
    return found


def _rename_pending_previews_for_output(
    prompt_id: Optional[str],
    sampler_ids: list[str],
    final_images: list[Dict[str, Any]],
) -> None:
    if not prompt_id or not sampler_ids or not final_images:
        return
    first_image = final_images[0]
    final_filename = str(first_image.get("filename") or "")
    final_basename = _sanitize_piece(os.path.splitext(os.path.basename(final_filename))[0], "")
    if not final_basename:
        return

    with _LLPS_RUN_LOCK:
        runs = []
        for sampler_id in sampler_ids:
            runs.extend(_LLPS_PREVIEW_RUNS.get((str(prompt_id), str(sampler_id)), []))

    for run in runs:
        if run.get("filename_resolution_status") == "renamed_to_final_image":
            continue
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        prefix = _sanitize_piece(metadata.get("filename_prefix", "LLPS"), "LLPS")
        renamed_files = []
        for entry in run.get("saved_files", []):
            old_path = entry.get("path")
            if not old_path or not os.path.exists(old_path):
                continue
            ext = os.path.splitext(old_path)[1]
            step = int(entry.get("step", 0)) + 1
            total_steps = int(entry.get("total_steps", 0))
            new_name = f"{prefix}_{final_basename}_step{step:04d}of{total_steps:04d}{ext}"
            new_path = _unique_path(os.path.join(os.path.dirname(old_path), new_name))
            try:
                os.replace(old_path, new_path)
            except OSError as e:
                logging.warning("[LLPS] Could not rename preview %s to %s: %s", old_path, new_path, e)
                continue
            entry["path"] = new_path
            entry["filename"] = os.path.basename(new_path)
            renamed_files.append(os.path.basename(new_path))

        if renamed_files:
            run["renamed_files"] = renamed_files
            run["filename_resolution_status"] = "renamed_to_final_image"
            if isinstance(metadata, dict):
                metadata["filename_resolution_status"] = "renamed_to_final_image"
                metadata["final_output"] = first_image
                metadata["saved_files"] = renamed_files
                metadata["renamed_files"] = renamed_files
                metadata["renamed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                metadata_path = run.get("metadata_path")
                if metadata_path:
                    try:
                        _write_metadata_json(os.path.dirname(metadata_path), metadata, os.path.basename(metadata_path))
                    except OSError as e:
                        logging.warning("[LLPS] Could not update renamed preview metadata: %s", e)
            _send_llps_preview_event(
                {
                    "controller_node_id": run.get("controller_node_id"),
                    "sampler_node_id": run.get("sampler_node_id"),
                    "sampler_node_type": run.get("sampler_node_type"),
                    "sampler_label": run.get("node_label"),
                    "prompt_id": str(prompt_id),
                    "run_id": run.get("run_id"),
                    "filename_resolution_status": "renamed_to_final_image",
                    "final_output": first_image,
                    "last_saved_file": renamed_files[-1],
                }
            )


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
                # step 0 and explicitly flushes the last callback preview after sampling ends.
                # Do not decode result_samples here: Comfy's previewer expects callback x0-space, while
                # result_samples may already be in the final latent-output space, which can produce
                # oversaturated / CFG-burnt looking TAESD previews.
                preview_tuple = previewer.decode_latent_to_preview_image(preview_format_for_comfy, x0)
                preview_tuple, _resolution_info = _normalize_preview_tuple(preview_tuple, cfg.preview_max_side)
                callback.llps_last_preview_tuple = preview_tuple
                callback.llps_last_callback_step = int(step)
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
            # numbering by one, and force-save the last callback preview after sampling completes.
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
    callback.llps_last_preview_tuple = None
    callback.llps_last_callback_step = None
    return callback


def _is_prompt_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


def _resolve_prompt_value(prompt: Dict[str, Any], value: Any, seen: Optional[set] = None) -> Any:
    if not _is_prompt_link(value):
        return value

    if seen is None:
        seen = set()
    node_id = str(value[0])
    if node_id in seen:
        return value
    seen.add(node_id)

    node = prompt.get(node_id)
    if not isinstance(node, dict):
        return value
    inputs = node.get("inputs", {})
    if not isinstance(inputs, dict):
        return value

    for key in ("value", "boolean", "bool", "enabled", "save_also", "save_preview"):
        if key in inputs:
            return _resolve_prompt_value(prompt, inputs[key], seen)

    literal_values = [v for v in inputs.values() if not _is_prompt_link(v)]
    bool_values = [v for v in literal_values if isinstance(v, bool)]
    if len(bool_values) == 1:
        return bool_values[0]
    if len(literal_values) == 1:
        return literal_values[0]
    return value


def _prompt_bool(prompt: Dict[str, Any], value: Any, default: bool = False) -> bool:
    resolved = _resolve_prompt_value(prompt, value)
    if _is_prompt_link(resolved) or resolved is None:
        return default
    return _bool(resolved)


def _prompt_text(prompt: Dict[str, Any], value: Any, default: str = "") -> str:
    resolved = _resolve_prompt_value(prompt, value)
    if _is_prompt_link(resolved) or resolved is None:
        return default
    return str(resolved)


def _prompt_int(prompt: Dict[str, Any], value: Any, default: int) -> int:
    resolved = _resolve_prompt_value(prompt, value)
    if _is_prompt_link(resolved) or resolved is None:
        return default
    try:
        return int(resolved)
    except Exception:
        return default


def _active_controller_from_prompt(prompt: Optional[Dict[str, Any]]) -> Optional[LLPSControllerData]:
    if not isinstance(prompt, dict):
        return None

    for node_id in sorted(prompt.keys(), key=lambda x: str(x)):
        node = prompt.get(node_id)
        if not isinstance(node, dict) or node.get("class_type") != "LLPSController":
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            continue

        enabled = _prompt_bool(prompt, inputs.get("enabled", True), True)
        if not enabled:
            continue

        save_also_value = inputs.get("save_also", inputs.get("save_preview", False))
        controller = LLPSControllerData.from_obj(
            {
                "enabled": enabled,
                "live_preview_method": _prompt_text(prompt, inputs.get("live_preview_method", "latent2rgb"), "latent2rgb"),
                "save_also": _prompt_bool(prompt, save_also_value, False),
                "save_base_path": _prompt_text(prompt, inputs.get("save_base_path", ""), ""),
                "subfolder": _prompt_text(prompt, inputs.get("subfolder", ""), ""),
                "filename_prefix": _prompt_text(prompt, inputs.get("filename_prefix", "LLPS"), "LLPS"),
                "image_format": _prompt_text(prompt, inputs.get("image_format", "jpg"), "jpg"),
                "save_every_n_steps": _prompt_int(prompt, inputs.get("save_every_n_steps", 1), 1),
                "save_metadata_json": _prompt_bool(prompt, inputs.get("save_metadata_json", True), True),
                "jpeg_quality": _prompt_int(prompt, inputs.get("jpeg_quality", 90), 90),
                "preview_max_side": _prompt_int(prompt, inputs.get("preview_max_side", 512), 512),
                "controller_node_id": str(node_id),
            }
        )
        return controller

    return None


def _write_metadata_json(save_dir: str, metadata: Dict[str, Any], filename: str = "metadata.json") -> None:
    with open(os.path.join(save_dir, filename), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def _make_controller_callback(
    model,
    steps: int,
    controller: LLPSControllerData,
    node_label: str,
    seed: int,
    sampler_node_id: Optional[str],
    sampler_node_type: str,
    sampler_subfolder: Optional[str] = None,
):
    method = controller.live_preview_method
    show_preview = controller.enabled and method not in {"none", "disabled"}
    save_preview = controller.enabled and controller.save_also
    source_method = method if show_preview else "server_default"
    if show_preview or save_preview:
        previewer, previewer_info = _previewer_for_method_with_info(model, source_method, method)
    else:
        previewer, previewer_info = _previewer_for_method_with_info(model, "none", method)
    cfg = controller.to_legacy_config(show_preview=show_preview, save_preview=save_preview)
    prompt_context = get_executing_context()
    prompt_id = prompt_context.prompt_id if prompt_context is not None else "unknown_prompt"
    run_id = uuid.uuid4().hex[:10]
    effective_subfolder = _effective_subfolder(cfg, node_label, sampler_subfolder)

    save_dir = None
    saved_files = []
    saved_file_entries = []
    if save_preview:
        save_dir = _resolve_save_dir(cfg, node_label, sampler_subfolder)

    pbar = comfy.utils.ProgressBar(steps, node_id=controller.controller_node_id)
    preview_format_for_comfy = "JPEG"
    run_record = {
        "prompt_id": str(prompt_id),
        "run_id": run_id,
        "controller_node_id": controller.controller_node_id,
        "sampler_node_id": str(sampler_node_id) if sampler_node_id is not None else None,
        "sampler_node_type": sampler_node_type,
        "node_label": node_label,
        "save_dir": save_dir,
        "saved_files": saved_file_entries,
        "renamed_files": [],
        "metadata_path": None,
        "metadata": None,
        "filename_resolution_status": "pending_final_image" if save_preview else "not_saving",
    }
    if save_preview and sampler_node_id is not None:
        with _LLPS_RUN_LOCK:
            _LLPS_PREVIEW_RUNS.setdefault((str(prompt_id), str(sampler_node_id)), []).append(run_record)

    def callback(step, x0, x, total_steps):
        preview_tuple = None
        resolution_info = {}
        if previewer is not None:
            try:
                preview_tuple = previewer.decode_latent_to_preview_image(preview_format_for_comfy, x0)
                preview_tuple, resolution_info = _normalize_preview_tuple(preview_tuple, cfg.preview_max_side)
                callback.llps_last_preview_tuple = preview_tuple
                callback.llps_last_preview_resolution = resolution_info
                if preview_tuple is not None and len(preview_tuple) > 1:
                    callback.llps_last_preview_image_id = id(preview_tuple[1])
                callback.llps_last_callback_step = int(step)
            except Exception as e:
                logging.exception("[LLPS] Controller failed to decode preview at step %s: %s", step, e)

        if show_preview and preview_tuple is not None:
            pbar.update_absolute(step + 1, total_steps, None)
            _send_llps_preview_event(
                {
                    "controller_node_id": controller.controller_node_id,
                    "sampler_node_id": str(sampler_node_id) if sampler_node_id is not None else None,
                    "sampler_node_type": sampler_node_type,
                    "sampler_label": node_label,
                    "prompt_id": str(prompt_id),
                    "run_id": run_id,
                    "step": int(step) + 1,
                    "total_steps": int(total_steps),
                    "method": callback.llps_source_method,
                    "previewer_class": previewer_info.get("actual_previewer_class"),
                    "fallback_reason": previewer_info.get("fallback_reason"),
                    "save_also": bool(save_preview),
                    "save_base_path": _save_base_dir(cfg),
                    "controller_subfolder": cfg.subfolder,
                    "sampler_subfolder": sampler_subfolder or "",
                    "effective_subfolder": effective_subfolder,
                    "last_saved_file": os.path.basename(saved_files[-1]) if saved_files else None,
                    "image": _preview_data_url(preview_tuple[1], preview_tuple[0], cfg.jpeg_quality),
                    **resolution_info,
                }
            )
        else:
            pbar.update_absolute(step + 1, total_steps, None)

        if save_preview and preview_tuple is not None and step > 0:
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
                        source_method,
                        temp_run_id=run_id,
                        sampler_node_id=str(sampler_node_id) if sampler_node_id is not None else None,
                    )
                    if saved:
                        saved_files.append(saved)
                        saved_file_entries.append(
                            {
                                "path": saved,
                                "filename": os.path.basename(saved),
                                "step": int(adjusted_step),
                                "total_steps": int(total_steps),
                            }
                        )
                except Exception as e:
                    logging.exception("[LLPS] Controller failed to save preview at step %s: %s", step, e)

    callback.llps_saved_files = saved_files
    callback.llps_save_dir = save_dir
    callback.llps_config = cfg
    callback.llps_controller = controller
    callback.llps_source_method = source_method
    callback.llps_previewer_info = previewer_info
    callback.llps_run_record = run_record
    callback.llps_run_id = run_id
    callback.llps_prompt_id = str(prompt_id)
    callback.llps_sampler_node_id = str(sampler_node_id) if sampler_node_id is not None else None
    callback.llps_sampler_node_type = sampler_node_type
    callback.llps_sampler_subfolder = sampler_subfolder or ""
    callback.llps_effective_subfolder = effective_subfolder
    callback.llps_last_preview_tuple = None
    callback.llps_last_preview_image_id = None
    callback.llps_last_callback_step = None
    callback.llps_last_preview_resolution = {}

    logging.info(
        "[LLPS] Controller sampler previewer: controller=%s sampler=%s run=%s requested=%s source=%s resolved=%s class=%s module=%s fallback=%s",
        controller.controller_node_id,
        node_label,
        run_id,
        previewer_info.get("requested_live_preview_method"),
        previewer_info.get("requested_preview_source_method"),
        previewer_info.get("resolved_preview_source_method"),
        previewer_info.get("actual_previewer_class"),
        previewer_info.get("actual_previewer_module"),
        previewer_info.get("fallback_reason"),
    )
    return callback


_ORIGINAL_COMMON_KSAMPLER = comfy_nodes.common_ksampler
_ORIGINAL_SAVE_IMAGE_INPUTS = comfy_nodes.SaveImage.INPUT_TYPES
_ORIGINAL_SAVE_IMAGE_SAVE_IMAGES = comfy_nodes.SaveImage.save_images


def _llps_common_ksampler(
    model,
    seed,
    steps,
    cfg,
    sampler_name,
    scheduler,
    positive,
    negative,
    latent,
    denoise=1.0,
    disable_noise=False,
    start_step=None,
    last_step=None,
    force_full_denoise=False,
    prompt=None,
    unique_id=None,
    llps_subfolder="",
):
    controller = _active_controller_from_prompt(prompt)
    if controller is None:
        return _ORIGINAL_COMMON_KSAMPLER(
            model,
            seed,
            steps,
            cfg,
            sampler_name,
            scheduler,
            positive,
            negative,
            latent,
            denoise=denoise,
            disable_noise=disable_noise,
            start_step=start_step,
            last_step=last_step,
            force_full_denoise=force_full_denoise,
        )

    latent_image = latent["samples"]
    latent_image = comfy.sample.fix_empty_latent_channels(model, latent_image, latent.get("downscale_ratio_spacial", None))

    if disable_noise:
        noise = torch.zeros(latent_image.size(), dtype=latent_image.dtype, layout=latent_image.layout, device="cpu")
    else:
        batch_inds = latent["batch_index"] if "batch_index" in latent else None
        noise = comfy.sample.prepare_noise(latent_image, seed, batch_inds)

    noise_mask = latent.get("noise_mask", None)
    node_type = "KSampler"
    if isinstance(prompt, dict) and unique_id is not None:
        node_type = prompt.get(str(unique_id), {}).get("class_type", node_type)
    node_label = f"{node_type}_{unique_id or 'node'}"
    callback = _make_controller_callback(
        model,
        int(steps),
        controller,
        node_label,
        int(seed),
        str(unique_id) if unique_id is not None else None,
        node_type,
        llps_subfolder,
    )
    disable_pbar = not comfy.utils.PROGRESS_BAR_ENABLED

    samples = comfy.sample.sample(
        model,
        noise,
        int(steps),
        float(cfg),
        sampler_name,
        scheduler,
        positive,
        negative,
        latent_image,
        denoise=float(denoise),
        disable_noise=disable_noise,
        start_step=start_step,
        last_step=last_step,
        force_full_denoise=force_full_denoise,
        noise_mask=noise_mask,
        callback=callback,
        disable_pbar=disable_pbar,
        seed=int(seed),
    )

    if (
        controller.save_also
        and callback.llps_save_dir
        and callback.llps_last_preview_tuple is not None
    ):
        try:
            saved = _save_preview_image(
                callback.llps_last_preview_tuple,
                callback.llps_save_dir,
                callback.llps_config,
                node_label,
                int(steps) - 1,
                int(steps),
                int(seed),
                callback.llps_source_method,
                temp_run_id=callback.llps_run_id,
                sampler_node_id=callback.llps_sampler_node_id,
            )
            if saved:
                callback.llps_saved_files.append(saved)
                callback.llps_run_record["saved_files"].append(
                    {
                        "path": saved,
                        "filename": os.path.basename(saved),
                        "step": int(steps) - 1,
                        "total_steps": int(steps),
                    }
                )
        except Exception as e:
            logging.exception("[LLPS] Controller failed to flush final preview image: %s", e)

    if controller.save_also and callback.llps_config.save_metadata_json and callback.llps_save_dir:
        try:
            previewer_info = dict(callback.llps_previewer_info)
            previewer_info["last_preview_image_id"] = callback.llps_last_preview_image_id
            resolution_info = dict(callback.llps_last_preview_resolution or {})
            metadata_filename = f"metadata_{callback.llps_run_id}.json"
            metadata = {
                "node": "LLPS Controller",
                "prompt_id": callback.llps_prompt_id,
                "run_id": callback.llps_run_id,
                "controller_node_id": controller.controller_node_id,
                "sampler_node_id": str(unique_id) if unique_id is not None else None,
                "sampler_node_type": node_type,
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": float(denoise),
                "live_preview_method": controller.live_preview_method,
                "preview_source_method": callback.llps_source_method,
                "previewer_info": previewer_info,
                "save_also": bool(controller.save_also),
                "filename_prefix": callback.llps_config.filename_prefix,
                "save_base_path": _save_base_dir(callback.llps_config),
                "controller_subfolder": callback.llps_config.subfolder,
                "sampler_subfolder": callback.llps_sampler_subfolder,
                "effective_subfolder": callback.llps_effective_subfolder,
                "save_dir": callback.llps_save_dir,
                "filename_resolution_status": callback.llps_run_record.get("filename_resolution_status"),
                "final_output": None,
                "saved_count": len(callback.llps_saved_files),
                "saved_files": [os.path.basename(p) for p in callback.llps_saved_files],
                "renamed_files": [],
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                **resolution_info,
            }
            callback.llps_run_record["metadata"] = metadata
            callback.llps_run_record["metadata_path"] = os.path.join(callback.llps_save_dir, metadata_filename)
            _write_metadata_json(
                callback.llps_save_dir,
                metadata,
                metadata_filename,
            )
        except Exception as e:
            logging.exception("[LLPS] Controller failed to write metadata.json: %s", e)

    out = latent.copy()
    out.pop("downscale_ratio_spacial", None)
    out["samples"] = samples
    return (out,)


def _with_llps_hidden(input_types: Dict[str, Any]) -> Dict[str, Any]:
    patched = dict(input_types)
    for section in ("required", "optional", "hidden"):
        if section in patched:
            patched[section] = dict(patched[section])
    optional = patched.setdefault("optional", {})
    optional.setdefault(
        "llps_subfolder",
        ("STRING", {"default": "", "multiline": False, "tooltip": "Optional LLPS per-sampler preview save subfolder."}),
    )
    hidden = patched.setdefault("hidden", {})
    hidden["prompt"] = "PROMPT"
    hidden["unique_id"] = "UNIQUE_ID"
    return patched


def _with_save_image_unique_id(input_types: Dict[str, Any]) -> Dict[str, Any]:
    patched = dict(input_types)
    for section in ("required", "optional", "hidden"):
        if section in patched:
            patched[section] = dict(patched[section])
    hidden = patched.setdefault("hidden", {})
    hidden["unique_id"] = "UNIQUE_ID"
    return patched


def _patch_builtin_samplers() -> None:
    if getattr(comfy_nodes.KSampler, "_llps_controller_patch", False):
        return

    original_ksampler_inputs = comfy_nodes.KSampler.INPUT_TYPES
    original_advanced_inputs = comfy_nodes.KSamplerAdvanced.INPUT_TYPES

    @classmethod
    def ksampler_input_types(cls):
        return _with_llps_hidden(original_ksampler_inputs())

    @classmethod
    def advanced_input_types(cls):
        return _with_llps_hidden(original_advanced_inputs())

    def ksampler_sample(self, model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent_image, denoise=1.0, llps_subfolder="", prompt=None, unique_id=None):
        return _llps_common_ksampler(
            model,
            seed,
            steps,
            cfg,
            sampler_name,
            scheduler,
            positive,
            negative,
            latent_image,
            denoise=denoise,
            prompt=prompt,
            unique_id=unique_id,
            llps_subfolder=llps_subfolder,
        )

    def advanced_sample(
        self,
        model,
        add_noise,
        noise_seed,
        steps,
        cfg,
        sampler_name,
        scheduler,
        positive,
        negative,
        latent_image,
        start_at_step,
        end_at_step,
        return_with_leftover_noise,
        denoise=1.0,
        llps_subfolder="",
        prompt=None,
        unique_id=None,
    ):
        force_full_denoise = return_with_leftover_noise != "enable"
        disable_noise = add_noise == "disable"
        return _llps_common_ksampler(
            model,
            noise_seed,
            steps,
            cfg,
            sampler_name,
            scheduler,
            positive,
            negative,
            latent_image,
            denoise=denoise,
            disable_noise=disable_noise,
            start_step=start_at_step,
            last_step=end_at_step,
            force_full_denoise=force_full_denoise,
            prompt=prompt,
            unique_id=unique_id,
            llps_subfolder=llps_subfolder,
        )

    comfy_nodes.KSampler.INPUT_TYPES = ksampler_input_types
    comfy_nodes.KSampler.sample = ksampler_sample
    comfy_nodes.KSamplerAdvanced.INPUT_TYPES = advanced_input_types
    comfy_nodes.KSamplerAdvanced.sample = advanced_sample
    comfy_nodes.KSampler._llps_controller_patch = True
    comfy_nodes.KSamplerAdvanced._llps_controller_patch = True

    @classmethod
    def save_image_input_types(cls):
        return _with_save_image_unique_id(_ORIGINAL_SAVE_IMAGE_INPUTS())

    def save_images(self, images, filename_prefix="ComfyUI", prompt=None, extra_pnginfo=None, unique_id=None):
        result = _ORIGINAL_SAVE_IMAGE_SAVE_IMAGES(self, images, filename_prefix, prompt, extra_pnginfo)
        if getattr(self, "type", "output") == "output" and isinstance(result, dict):
            output_images = result.get("ui", {}).get("images", [])
            context = get_executing_context()
            prompt_id = context.prompt_id if context is not None else None
            sampler_ids = _find_upstream_sampler_ids(prompt, str(unique_id) if unique_id is not None else None)
            _rename_pending_previews_for_output(prompt_id, sampler_ids, output_images)
        return result

    comfy_nodes.SaveImage.INPUT_TYPES = save_image_input_types
    comfy_nodes.SaveImage.save_images = save_images


_patch_builtin_samplers()



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
                "preview_max_side": (["512", "1024"], {"default": "512"}),
            }
        }

    RETURN_TYPES = ("LLPS_CONFIG",)
    RETURN_NAMES = ("llps_config",)
    FUNCTION = "make_config"
    CATEGORY = LLPS_LEGACY_CATEGORY

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
        preview_max_side=512,
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
            preview_max_side=preview_max_side,
        )
        return (asdict(LLPSConfigData.from_obj(asdict(cfg))),)


class LLPSController:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enabled": ("BOOLEAN", {"default": True}),
                "live_preview_method": (["server_default", "none", "auto", "latent2rgb", "taesd"], {"default": "latent2rgb"}),
                "save_also": ("BOOLEAN", {"default": False}),
                "save_base_path": ("STRING", {"default": "", "multiline": False}),
                "subfolder": ("STRING", {"default": "", "multiline": False}),
                "filename_prefix": ("STRING", {"default": "LLPS", "multiline": False}),
                "image_format": (["jpg", "png", "webp"], {"default": "jpg"}),
                "save_every_n_steps": ("INT", {"default": 1, "min": 1, "max": 1000, "step": 1}),
                "save_metadata_json": ("BOOLEAN", {"default": True}),
                "jpeg_quality": ("INT", {"default": 90, "min": 1, "max": 100, "step": 1}),
                "preview_max_side": (["512", "1024"], {"default": "512"}),
            }
        }

    RETURN_TYPES = ()
    OUTPUT_NODE = True
    FUNCTION = "make_controller"
    CATEGORY = LLPS_CATEGORY

    def make_controller(
        self,
        enabled=True,
        live_preview_method="latent2rgb",
        save_also=False,
        save_base_path="",
        subfolder="",
        filename_prefix="LLPS",
        image_format="jpg",
        save_every_n_steps=1,
        save_metadata_json=True,
        jpeg_quality=90,
        preview_max_side=512,
    ):
        cfg = LLPSConfigData(
            enabled=enabled,
            live_preview_method=live_preview_method,
            show_preview=live_preview_method not in {"none", "disabled"},
            save_preview=save_also,
            save_base_path=save_base_path,
            subfolder=subfolder,
            filename_prefix=filename_prefix,
            image_format=image_format,
            save_every_n_steps=save_every_n_steps,
            save_metadata_json=save_metadata_json,
            jpeg_quality=jpeg_quality,
            preview_max_side=preview_max_side,
        )
        controller = asdict(LLPSConfigData.from_obj(asdict(cfg)))
        controller["node"] = "LLPS Controller"
        return ()


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
    CATEGORY = LLPS_LEGACY_CATEGORY

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

        # Explicitly flush the last callback preview as the final saved frame.
        # Important: do NOT decode result_samples with the previewer here. Comfy's previewer is built
        # for the sampler callback's x0 tensor. Feeding the final returned latent can place the tensor
        # in the wrong latent space for TAESD/latent2rgb and creates oversaturated final previews.
        if (
            cfg_data.enabled
            and cfg_data.save_preview
            and callback.llps_save_dir
            and callback.llps_last_preview_tuple is not None
        ):
            try:
                saved = _save_preview_image(
                    callback.llps_last_preview_tuple,
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
                logging.exception("[LLPS] Failed to flush final callback preview image: %s", e)

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
    "LLPSController": LLPSController,
    "LLPSKSampler": LLPSKSampler,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LLPSConfig": "LLPS Config (Legacy)",
    "LLPSController": "LLPS Controller",
    "LLPSKSampler": "LLPS KSampler (Legacy)",
}

WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
