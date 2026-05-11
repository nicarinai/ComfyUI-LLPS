# ComfyUI-LLPS-v1

LLPS = **LiveLatentPreviewer & Saver**.

This v2-alpha provides three nodes and a manager panel:

- **LLPS Config**: choose live preview method, show/save toggles, save path, filename, image format, interval.
- **LLPS Controller**: workflow-level declaration for LLPS preview management.
- **LLPS KSampler**: legacy/prototype KSampler-compatible node that uses `LLPS_CONFIG` for callback-based preview saving.
- **LLPS Manager panel**: frontend workflow scanner for sampler-like nodes and LLPS-controlled status.

## v2-alpha design boundary

This version does **not** globally override every sampler in the workflow yet. The **LLPS Controller** is the workflow-level authority used by the Manager panel to classify sampler-like nodes as controlled, uncontrolled, or candidate.

`LLPS KSampler` remains available as a legacy/prototype node for v1.2 callback-based preview saving, but it is not the architectural center of LLPS v2.

## Install

Copy this folder into:

```text
ComfyUI/custom_nodes/ComfyUI-LLPS-v1/
```

Restart ComfyUI.

## Basic use

1. Add `LLPS Controller`.
2. Open the **LLPS Manager** panel.
3. Use **Refresh** to scan sampler-like nodes in the workflow.
4. Use the panel to inspect which sampler-like nodes are covered by the enabled Controller.

For legacy v1.2 preview saving, `LLPS Config` can still be connected to `LLPS KSampler`.

## LLPS Manager panel

After restarting ComfyUI, use the floating **LLPS** button or the **LLPS** menu to open the manager panel.

The panel scans the current workflow and shows:

- LLPS nodes
- sampler-like nodes such as `KSampler`, `KSamplerAdvanced`, `SamplerCustom`, `SamplerCustomAdvanced`, and Ultimate SD Upscale nodes
- whether each sampler is LLPS-controlled, uncontrolled, or a candidate
- Controller coverage status for sampler-like nodes
- node id, node type, node title
- status filters, **Focus**, **Refresh**, and uncontrolled-node selection actions

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
