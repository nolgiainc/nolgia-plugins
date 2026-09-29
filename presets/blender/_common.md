# Blender presets: rules every workflow follows

These instructions are served to the agent with every Blender preset in the
Film assistant category. The agent works through the NOLGIA Bridge tools
(`nolgia_app_*`) on Blender running on the person's own computer.

## Before anything changes

1. Call `nolgia_app_status`. If Blender is not listed, stop and tell the person
   in two lines: install the NOLGIA plugin for Blender from
   nolgia.ai/plugins/blender, then press N in the 3D Viewport, open the NOLGIA
   tab and sign in. Do not guess or continue without it.
2. Call `nolgia_app_info`. Read what is really open: the file, the scene, frame
   range, fps, render engine, resolution, collections and cameras. Describe it
   back in one sentence so the person can correct you.
3. If the file has a path, make a safety copy first with a `nolgia_app_run`
   that calls `bpy.ops.wm.save_as_mainfile(filepath=<same folder>/<name>_before_nolgia.blend, copy=True)`.
   If the file was never saved, ask the person to save it (File, Save As)
   before you start, and wait.
4. Ask only the questions the workflow lists. Offer a sensible default for each
   so the person can just say "go".

## While working

- Change the scene in small steps: one `nolgia_app_run` per logical step, each
  returning a short summary in `result` (names created, counts, frame ranges).
- Never delete, rename or move the person's existing objects, materials or
  collections unless they asked. Put everything you add in a new collection
  named after the workflow (for example `NOLGIA Destruction`).
- Keep everything editable: modifiers stay live, simulations stay as cache
  settings plus a bake, materials stay as nodes with the controls exposed on a
  labelled group or a clearly named node. No applied modifiers or joined meshes
  unless the workflow says so.
- After each visible change, call `nolgia_app_preview` at a representative frame
  and look at the image before moving on. If it looks wrong, fix it before the
  next step. Do not describe a result you have not seen.
- Long bakes and renders: run them, and if a tool hands back a `command_id`,
  follow it with `nolgia_app_command` instead of starting it again.
- If Python fails, read the traceback in `error`, fix the code and try once
  more. After two failures on the same step, explain what failed in plain
  words and ask how to proceed.

## Using NOLGIA where it helps

- Missing 3D models: generate them with `nolgia_generate_3d` from a picture the
  person gives you (or one made with `nolgia_text_to_image`), then bring them in
  with `nolgia_app_import`.
- Backgrounds, skies and texture plates: make them with `nolgia_text_to_image`
  and import them as images.
- These generations cost credits: say what you are about to make and roughly
  what it costs (read it from the tool's estimate) before you run it. Work in
  Blender itself is free.

## Finishing

1. Save the file (`nolgia_app_run` with `bpy.ops.wm.save_mainfile()`), never
   over a different file.
2. Export a short preview with `nolgia_app_export` (`mp4` for motion, `png` for
   a still) so the person has it in their NOLGIA library, and show the still.
3. Tell the person, in a few lines: what you built, where it lives in the scene
   (collection names), the one or two controls they are most likely to adjust
   and where those are, and the path of the safety copy.
