---
slug: blender-cartoon-shaders
name: Cartoon look in Blender
category: Film assistant
app: blender
min_app_version: "4.2"
starter_prompt: "Use NOLGIA to give my open Blender scene a cartoon look: toon shading, clean outlines and adjustable colours, keeping my original colours."
---

# Cartoon look in Blender

Turn the scene's materials into a reusable toon look: flat colour with two or
three shading bands, a soft colour shift in the shadows, optional paper or
halftone texture, and clean outlines. The original colours stay, and every
control sits in one place.

## Ask

1. A style reference: an image, a show or film they like, or plain words
   ("flat anime", "soft storybook", "bold comic"). (Default: two bands and black
   outlines.)
2. Which objects get the look: everything, a collection, or the selection.
3. Outlines: thin, bold, or none; and should inner lines (creases) show?
4. Keep the engine as it is, or switch to EEVEE for real-time previews? (The
   toon look needs EEVEE's Shader to RGB; in Cycles it falls back to a
   stepped-light approximation. Say so.)

## Build

1. **One shared node group.** Create a node group `NOLGIA Toon` with inputs:
   Base Color, Shadow Tint, Band Count (1 to 4), Band Softness, Rim Amount,
   Texture Amount. Inside: Diffuse BSDF, Shader to RGB, a Map Range or Color
   Ramp set to constant interpolation with stops built from Band Count, mixing
   Base Color toward Base Color times Shadow Tint, plus a Fresnel-driven rim.
   Shader to RGB gives raw light levels, often well above 1, so divide by a
   `Light Level` input before the ramp and set it from a preview; otherwise
   everything lands in one band.
2. **Keep the original colours.** For each targeted material, read its current
   base colour (the Principled BSDF Base Color, or the image texture feeding
   it) and wire it into `NOLGIA Toon` Base Color. Do not delete the old shader:
   keep it in the node tree, disconnected, in a frame labelled "Original", so
   the person can switch back. Name the new material `<old name> Toon` only if
   they asked for copies; by default, edit in place after the safety copy.
3. **Style from the reference.** Pick Band Count, Shadow Tint (a cool shadow for
   warm scenes, warm for cool ones), and Band Softness from the reference.
   Offer one texture: a subtle paper grain or a halftone in the shadow band,
   driven by Texture Amount.
4. **Outlines.** Add Line Art for the whole scene or collection: in Blender
   4.3 and later, `bpy.ops.object.grease_pencil_add(type='LINEART_SCENE')` (or
   `'LINEART_COLLECTION'`); earlier versions use a Grease Pencil Line Art
   object. Contour on (it covers the silhouette), crease on only if asked.
   Thickness is the modifier's `radius` in metres: about 0.01 reads well for a
   scene seen from 10 m, and 0.0035 is a hairline.
   For a fast alternative, a Solidify modifier with flipped normals and a
   backface-culled black material on each object: ask which they prefer.
5. **One control panel.** An empty `Toon Controls` with custom properties for
   the node group inputs and the outline thickness, connected with drivers, so
   the whole look changes in one place.

## Check

- Preview the main camera before and after, and show both. Colours must still
  read as the same colours, only stepped.
- Preview one close-up and one wide: outlines must not flicker or vanish at
  distance (adjust thickness falloff if they do).

## Deliver

Save, export a `png` before and after, and tell the person where the controls
are (`Toon Controls`), how to switch a material back (reconnect the frame
labelled "Original"), and that the node group is reusable in other files
(append `NOLGIA Toon`).
