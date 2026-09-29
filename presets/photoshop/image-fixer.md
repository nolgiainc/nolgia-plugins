---
slug: photoshop-image-fixer
name: Clean up an image in Photoshop
category: Film assistant
app: photoshop
min_app_version: "25.0"
beta: true
starter_prompt: "Use NOLGIA to clean up my generated location image in Photoshop: remove what does not belong, repair smeared signs and broken textures, and match the light, keeping every fix on its own editable layer."
---

# Clean up an image in Photoshop

Refine a generated location image so it holds up on screen: stray objects
gone, smeared signs and warped surfaces repaired, light and grain consistent,
the composition unchanged. Every fix is its own layer with a mask inside one
group, so the person can see, adjust or switch off each one, and the original
pixels are never touched.

## Ask

1. Which image? (Default: the active document. If it is in the NOLGIA
   library, bring it in with `nolgia_app_import`.) Preview it and read it
   carefully first.
2. What should go? Give a short numbered list of what you see that does not
   belong (stray objects, doubled or melted details, people or cars that
   break the shot) and let the person pick by number. (Default: everything
   on your list.)
3. Which textures and surfaces need repair? List what looks generated:
   smeared or garbled signs, warped bricks or tiles, repeating patterns,
   melted edges. For each sign, ask what it should say, or whether it should
   be plain. (Default: signs become clean and plain, surfaces match their
   surroundings.)
4. The light: keep it as it is, or a time of day, direction and mood (for
   example "late afternoon sun from the left, warmer"). How strong? (Default:
   keep the light, only match fixes to it.)
5. Photoshop only (free), or NOLGIA edits where they do better (about 8
   credits each with `gpt-image-2.5-flare`)? Give the total for your plan
   before anything is generated. (Default: Photoshop first, NOLGIA for the
   fixes Photoshop cannot do well, with the cost agreed first.)

## Build

1. **Set up without touching the original.** Make the safety copy (see the
   rules above) and save the working PSD. Keep the original layer as it is at
   the bottom (a background layer stays locked; that is fine). Make one group
   `NOLGIA Cleanup` above it for everything you add, with inner groups
   `Remove`, `Repair` and `Light`, in that order from the bottom. Note the
   document id and the original layer's id: every later step finds them by id.
2. **Remove, one item per layer.** For each item: select it generously (a
   rectangle, ellipse or polygon a few pixels outside its edge, including its
   shadow and reflection), expand the selection 4 to 8 px. Duplicate the
   original layer into `Remove`, name it `Remove: <item>`, run Content-Aware
   Fill on the copy inside the selection, then give the copy a mask revealing
   only the selection, feathered 2 px. Preview a close crop. If the fill looks
   cloned or smeared (a big area, or structure behind it such as a doorway or
   railing), replace that layer with a NOLGIA masked edit (step 3) and say so.
3. **Repair with NOLGIA masked edits.** For each sign or surface the person
   picked (and removals Photoshop cannot fill well): export the document as
   `png`; make a mask document the same size (transparent background, filled
   black, the area to repaint deleted, a few pixels wider than the damage) and
   export it as `png` too; call `nolgia_image_to_image` with the document as
   `image_url`, the mask as `mask_asset_id`, `gpt-image-2.5-flare`, the
   document's aspect ratio, and a prompt that names only what should appear
   there and asks to keep everything else ("a clean painted shop sign reading
   BAKERY in white serif letters; keep the rest of the street exactly as it
   is"). Import the result with `nolgia_app_import` while a layer in `Repair`
   is selected, name it `Repair: <surface>`, and mask it to the repaired area
   (the same selection, feathered 2 px) so the rest of the frame stays the
   person's pixels. Close the mask document without saving. Several fixes
   that sit far apart can share one edit and one mask to save credits.
4. **Match each patch to the plate.** Preview each patch close up next to its
   surroundings. When a patch is brighter, darker or a different colour,
   clip a Curves or Color Balance adjustment layer to it and nudge it until
   the edges disappear. NOLGIA edits come back cleaner than the plate, so the
   grain step below matters.
5. **Light.** Only if the person asked for a change: in `Light`, add
   adjustment layers named for their job (`Light: warm sun`, `Light: shade
   left side`): a Curves or Brightness/Contrast layer for the overall level, a
   Photo Filter or Color Balance for warmth, and a Curves layer with a
   gradient mask for direction (brighter on the sun side). Keep each
   adjustment gentle; stack them rather than pushing one.
6. **Grain.** Add a layer `Grain` at the top of `NOLGIA Cleanup`: filled 50%
   gray, blend mode Overlay, monochromatic Add Noise about 3 to 5% (Gaussian),
   opacity set by eye so the patches and the plate have the same texture in
   a 100% crop. Mask it to the patched areas if the plate already has grain
   elsewhere.

## Check

- Preview the whole frame, then a close crop of every fix. Look for cloned
  patterns, hard or haloed mask edges, patches brighter or smoother than
  their surroundings, signs that still read as garbled, lines that bend where
  they should be straight.
- Hide `NOLGIA Cleanup`, preview (before), show it, preview (after), and
  compare: the composition, the camera and everything the person did not ask
  about must be the same.
- `nolgia_app_info`: the original layer is unchanged, every fix is a named
  layer or Smart Object with a mask inside `NOLGIA Cleanup`, nothing is
  flattened or rasterized.

## Deliver

Save the PSD, export a full-size `png` (and a `jpg` if they want a smaller
file), and show the before and after previews side by side. Tell the person
which layer holds each fix and how to adjust one (paint its mask white to
show more, black to show less; hide a layer to switch the fix off), what
NOLGIA generated and what it cost, and where the safety copy is.
