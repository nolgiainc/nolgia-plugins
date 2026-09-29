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
   `Remove`, `Repair` and `Light`, in that order from the bottom (groups are
   made at the top level; move each into `NOLGIA Cleanup`, `Remove` first).
   Note the document id and the ids of the original layer and the groups:
   every later step finds them by id.
2. **Remove, one item per layer.** For each item: duplicate the original
   layer into `Remove` as `Remove: <item>` and select the copy; select the
   item generously (a rectangle, ellipse or polygon a few pixels outside its
   edge, including its shadow), expand the selection 4 to 8 px, run
   Content-Aware Fill on the copy, feather the selection 2 px and give the
   copy a mask revealing only the selection. Preview a close crop. On open
   ground (cobbles, pavement, grass, sky) this is usually invisible. If the
   fill looks cloned or smeared (structure behind the object, such as a
   shopfront, doorway or railing), use a NOLGIA masked edit (step 3) for
   that item instead and say so.
3. **Repair with NOLGIA masked edits.** For the signs and surfaces the
   person picked (and removals Photoshop cannot fill well), in one edit when
   they sit apart from each other (one edit, one price):
   - Export the document as `png` with `nolgia_app_export` (after the
     removals, so the edit sees them done).
   - Make the mask: `app.documents.add({ width, height, resolution, mode:
     "RGBColorMode", fill: "transparent", name: "NOLGIA edit mask" })` with
     the document's size, then work on `app.activeDocument` (see the rules
     above); fill it black, and for each area select a rectangle a few
     pixels larger than the damage (the whole lettering of a sign, the whole
     object to remove with its shadow) and delete it. Export it as `png` (it
     is the active document now; the export keeps the transparency), then
     close it without saving.
   - Call `nolgia_image_to_image` with the document's signed URL as
     `image_url`, the mask as `mask_asset_id`, `gpt-image-2.5-flare`, the
     document's aspect ratio, and a prompt that numbers each fix, says what
     should be there, and asks to keep everything else ("1) remove the white
     plastic chair in front of the bakery: show the wooden lower panel and the
     sunlit pavement behind it; 2) repaint the sign so it reads Boulangerie in
     crisp dark script, same size and slant; keep everything else exactly as
     it is"). Show the person the result before you place it.
   - Import it with `nolgia_app_import`, move it into `Repair`, name it
     `Repair: <what>`, and give it a mask of the repaired things, feathered
     2 to 3 px. Make that mask cover each whole thing you repaired, not only
     the area NOLGIA was asked to repaint: new lettering can run past it (a
     sign cut off at `Boulangeri` needs the whole sign board in the mask).
4. **Match each patch to the plate.** Preview each patch close up next to its
   surroundings. When a patch is brighter, darker or a different colour,
   select it and add a Curves adjustment layer `Match: <what>` clipped to it,
   and nudge it until the edges disappear. NOLGIA edits tend to come back a
   little brighter and cleaner than the plate (in testing, about 7 levels out
   of 255): start by lowering the midtones that much, and keep the grain step
   below.
5. **Light.** Only if the person asked for a change: add adjustment layers
   named for their job and move them into `Light`: a warming Photo Filter
   (density 15 to 25) for warmth over the whole frame (no selection), and
   for direction a gentle Curves lift made while a big selection is active:
   a rectangle that runs off the canvas on the sun side and ends about half
   way across, feathered 200 px, so the lift fades out away from the sun.
   Keep each adjustment gentle; stack them rather than pushing one.
6. **Grain.** Add a layer `Grain` at the top of `NOLGIA Cleanup`: filled 50%
   gray, monochromatic Gaussian Add Noise about 3 to 5%, blend mode Overlay,
   opacity about 50% and then by eye, so the patches and the plate have the
   same texture in a 100% crop. Mask it to the patched areas if the plate
   already has strong grain elsewhere.

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
