# ComfyUI-LLPS-v1

LLPS = **LiveLatentPreviewer & Saver**.

This v1 provides two nodes:

- **LLPS Config**: choose live preview method, show/save toggles, save path, filename, image format, interval.
- **LLPS KSampler**: KSampler-compatible node that uses `LLPS_CONFIG` instead of ComfyUI's global live preview setting.
- **LLPS Manager panel**: frontend workflow scanner for sampler-like nodes and LLPS-controlled status.

## v1 design boundary

This version does **not** globally override every KSampler in the workflow. Only **LLPS KSampler** follows LLPS settings. Other samplers keep using ComfyUI's own settings.

That is intentional: it avoids global monkeypatch/race-condition issues and makes the graph explicit.

## Install

Copy this folder into:

```text
ComfyUI/custom_nodes/ComfyUI-LLPS-v1/
```

Restart ComfyUI.

## Basic use

1. Add `LLPS Config`.
2. Add `LLPS KSampler`.
3. Connect `LLPS Config.llps_config` to `LLPS KSampler.llps_config`.
4. Use `LLPS KSampler` in place of the normal `KSampler`.

## LLPS Manager panel

After restarting ComfyUI, use the floating **LLPS** button or the **LLPS** menu to open the manager panel.

The panel scans the current workflow and shows:

- LLPS nodes
- sampler-like nodes such as `KSampler`, `KSamplerAdvanced`, `SamplerCustom`, and `SamplerCustomAdvanced`
- whether each sampler is LLPS-controlled, uncontrolled, or a candidate
- node id, node type, node title
- **Focus** and **Refresh** actions

When the panel is open, matching nodes are visually marked on the canvas.

## Settings

- `live_preview_method`: `server_default`, `none`, `auto`, `latent2rgb`, `taesd`
- `show_preview`: send live preview frames to ComfyUI UI
- `save_preview`: save the same preview frames to disk
- `save_base_path`: empty = `ComfyUI/output/LLPS`
- `subfolder`: optional explicit subfolder
- `filename_prefix`: prefix for step files
- `image_format`: `jpg`, `png`, `webp`
- `save_every_n_steps`: save interval; `1` saves every step
- `save_metadata_json`: writes `metadata.json` next to saved frames

## Important

`none` means no preview frame is decoded, so it cannot save frames. For “save only,” use:

```text
live_preview_method = latent2rgb or taesd
show_preview = false
save_preview = true
```
