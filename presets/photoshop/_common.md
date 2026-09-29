# Photoshop presets: rules every workflow follows

These instructions are served to the agent with every Photoshop preset in the
Film assistant category. The agent works through the NOLGIA Bridge tools
(`nolgia_app_*`) on Photoshop running on the person's own computer. Code for
`nolgia_app_run` is UXP JavaScript (`language: "uxp"`).

## Before anything changes

1. Call `nolgia_app_status`. If Photoshop is not listed, stop and tell the
   person in two lines: install NOLGIA for Photoshop from
   nolgia.ai/plugins/photoshop, then open Plugins > NOLGIA for Photoshop >
   NOLGIA and sign in. Do not guess or continue without it.
2. Call `nolgia_app_info`. Read what is really open: the documents, the active
   one's size, resolution, colour mode and bit depth, its layers (groups,
   masks, what is hidden or locked), the selected layer and any selection.
   Describe it back in one sentence so the person can correct you. If the
   image to work on is in the NOLGIA library instead, bring it in with
   `nolgia_app_import`: it opens as a new document when none is open.
3. If the document has a path, make a safety copy first:
   `nolgia_app_export` with `format: "psd"` saves `<name>-copy.psd` next to
   it and never overwrites a file. If it was never saved (an image imported
   from NOLGIA, a new document), ask where to keep the working PSD, offer a
   default next to the person's other project files, and save it there with
   a `nolgia_app_run` that calls `doc.saveAs.psd(...)` (see below), or ask the
   person to use File > Save As, and wait.
4. Ask only the questions the workflow lists. Offer a sensible default for
   each so the person can just say "go".

## While working

- Change the document in small steps: one `nolgia_app_run` per logical step,
  each setting `result` to a short summary (layer names and ids, counts,
  bounds). Each run is one step in Photoshop's History, named
  "NOLGIA: run code", so the person can undo it in one go; a run that fails
  is undone for you.
- Never delete, merge, flatten, rasterize or rename the person's own layers
  unless they asked. Work on copies and new layers, and put everything you
  add in a group named after the workflow (for example `NOLGIA Cleanup`).
- Keep everything editable: retouching on its own layers with layer masks,
  placed images as Smart Objects, colour and light as adjustment layers. No
  destructive filters on the original pixels.
- After each visible change, call `nolgia_app_preview` and look at the image
  before moving on. If it looks wrong, fix it before the next step. Do not
  describe a result you have not seen. The preview shows the whole active
  document; to look closely at one area, duplicate a crop of it into a
  temporary document in a run, preview that, then close it without saving.
- If code fails, read `error` (it names the line of your code), fix it and try
  once more. After two failures on the same step, explain what failed in
  plain words and ask how to proceed.

## Photoshop UXP that bites

- Every run already runs inside `core.executeAsModal`, so edits work as they
  are. Do not wrap code in `core.executeAsModal` yourself: an error thrown
  inside it comes back as bare text with no line number. If you need a
  nested modal scope, use the `modal(async (ctx) => ...)` helper.
- `batchPlay` does not throw when a step fails; it returns
  `{_obj: "error", message}` and carries on. Use the `play([...])` helper,
  which throws on the first failed step. Check what a step did (for example
  read the layer back) before relying on it.
- `doc.layers.add()` and `doc.createLayer()` put the new layer at the top of
  the stack, inside the top group when the top item is a group, whatever
  layer is selected. To put a layer where you mean, select the layer it
  should sit above with
  `play([{ _obj: "select", _target: [{ _ref: "layer", _id: id }], makeVisible: false }])`
  and make it with
  `play([{ _obj: "make", _target: [{ _ref: "layer" }], using: { _obj: "layer", name } }])`,
  or move it afterwards with
  `layer.move(target, constants.ElementPlacement.PLACEBEFORE)` (above) or
  `PLACEAFTER` (below); `PLACEINSIDE` puts it in a group.
- Deleting a layer leaves no layer selected; select one before the next step.
  `nolgia_app_import` places the image right above the selected layer (inside
  its group), or at the top of the stack when none is selected.
- Photoshop hands out a new object each time you read a layer, so compare
  layers by `id`, never with `===`, and keep ids (not objects) between runs.
  The same goes for documents: keep the document's `id` and find it with
  `app.documents.find((d) => d.id === id)`, because opening, duplicating or
  importing changes `app.activeDocument`.
- Fill, Content-Aware Fill, filters and adjustments act on the selected
  layer, inside the selection when there is one. Content-Aware Fill needs
  pixels on that layer (it does nothing on an empty layer): duplicate the
  source layer, fill inside the selection on the copy, then give the copy a
  mask of just the patch.
- In descriptors, `RGBColor` takes `red`, `grain` (green) and `blue`, 0 to 255.
- A layer's `bounds` are what its mask lets through. A layer whose mask hides
  everything reports zero bounds.
- Placing an image clears the selection (`nolgia_app_import` puts it back).
  Keep a selection you need across steps in a channel, or rebuild it.
- Code runs on Photoshop's one JavaScript thread. A loop that never awaits
  freezes Photoshop and nothing can stop it. `timeout_seconds` only stops
  NOLGIA waiting; keep each run short.
- While a dialog is open in Photoshop (a save prompt, a filter's settings,
  the Graphics Processor notice at startup) Photoshop is busy: changes fail,
  and `run` says so and runs your code outside the modal scope, where it can
  only read. Ask the person to close the dialog, then run the step again.
- `await app.documents.add(...)` now and then resolves to `null` (seen just
  after Photoshop started) although the document was made: fall back to
  `app.activeDocument`.
- `doc.path` is empty until the document is saved. Save with `doc.save()` or
  `doc.saveAs.psd(entry, options, asCopy)`; `nolgia_app_run` has `fs` (the
  UXP local file system, full access) to get entries:
  `await fs.getEntryWithUrl("file:/C:/Projects/shot.psd")` for an existing
  file, `await (await fs.getEntryWithUrl("file:/C:/Projects")).createFile("shot.psd", { overwrite: true })`
  for a new one.

## Using NOLGIA where it helps

- Photoshop's own tools come first: Content-Aware Fill for small removals,
  healing on a copy layer, adjustment layers for light and colour. They are
  free and stay on the person's computer.
- For a larger change (repainting part of the scene, a new sky, a surface that
  needs real detail) use a NOLGIA masked edit: export the document with
  `nolgia_app_export` (`png`), make a mask PNG the same size whose transparent
  pixels mark the area to repaint (a new document with a transparent
  background, filled black, with the area deleted; `png` export keeps the
  transparency), export that too, then call `nolgia_image_to_image` with the
  first as `image_url` (its signed URL from `nolgia_get_asset`) and the mask as
  `mask_asset_id` on a model that publishes `image.inpaint_mask`
  (`gpt-image-2.5-flare` or `gpt-image-2.5-sunburst`; check
  `nolgia_list_models`). Bring the result in with `nolgia_app_import`: an image
  with the document's shape lines up with the canvas exactly. Mask it to just
  the repainted area so everything else stays the person's original pixels.
- These generations cost credits: say what you are about to make and roughly
  what it costs (read it from `nolgia_list_models` or the tool's estimate)
  before you run it. Work in Photoshop itself is free.
- Photoshop's Generative Fill uses the person's Adobe generative credits and
  has no scripting interface NOLGIA supports. If they prefer it, make the
  selection and the target layer ready, ask them to click Generative Fill
  and type the prompt you suggest, and continue once they say it is done.

## Finishing

1. Save the document with a `nolgia_app_run` that calls `await doc.save()`,
   never over a different file.
2. Export a `png` or `jpg` with `nolgia_app_export` so the person has the
   result in their NOLGIA library, and show it.
3. Tell the person, in a few lines: what you changed, where it lives in the
   document (group and layer names), how to switch a fix off (hide the layer or
   paint its mask), and the path of the safety copy.
