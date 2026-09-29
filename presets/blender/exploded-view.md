---
slug: blender-exploded-view
name: Exploded view in Blender
category: Film assistant
app: blender
min_app_version: "4.2"
starter_prompt: "Use NOLGIA to make an exploded view of the product in my open Blender scene: pull the parts apart so you can see how it fits together, then animate it assembling."
---

# Exploded view in Blender

Show how a product is built: the parts move apart along their natural axes so
every part is visible, hold, then assemble back into the finished object in a
clear order. Every part ends exactly where it started.

## Ask

1. Which object or collection is the product? (Default: the selected
   collection, else the collection with the most meshes.)
2. Exploded, assembling, or both? (Default: both: explode 1.5 s, hold 1 s,
   assemble 2.5 s.)
3. Anything that must stay fixed, like a base or a frame? (Default: the largest
   part stays put.)
4. No model yet? Offer to make one: a photo of the product through
   `nolgia_generate_3d` gives a single mesh; say plainly that a generated model
   is one piece and cannot be exploded into its real parts, so this workflow
   needs a model made of separate parts.

## Build

1. **Record the truth first.** Store every part's current world matrix as a
   custom property (`nolgia_home`) so the final frame can be verified and
   restored. Do not change origins or apply transforms.
2. **Order the parts.** Group parts into layers from outside to inside by their
   distance from the product's centre and by nesting (a part whose bounding box
   sits inside another's is inner). Fasteners (small parts, many copies) go
   first on explode and last on assemble.
3. **Choose each part's direction.** Move along the axis the part would slide
   out on: the axis from the product centre through the part's centre, snapped
   to the nearest of the product's local axes when it is within 20 degrees.
   Distance grows with the layer so nothing overlaps: roughly 0.6 times the
   product's size per layer, then check for overlaps in the exploded pose and
   push apart any pair whose bounding boxes intersect.
4. **Animate.** Keyframe location only (not rotation, not scale) on each part:
   home, exploded, hold, home. Stagger parts in the same layer by 2 to 3 frames
   so the motion reads as a sequence. Use ease in and out (Bezier, auto clamped)
   and a slight overshoot-free settle on assembly. Keep all keys on a single
   action per part, named `NOLGIA Exploded view`.
5. **Guides (optional, ask).** Thin lines or dashed curves from each part's
   home to its exploded position, visible only during the hold.
6. **Camera and light.** A three-quarter camera slightly above the product,
   framing the exploded pose with 15 percent margin, with a slow orbit of 20 to
   30 degrees over the shot. A soft key light, a rim light and a neutral
   studio floor or backdrop in the `NOLGIA Exploded view` collection. Leave the
   person's lights alone; if they have their own, ask before adding.

## Check

- On the final frame, every part's world matrix must match `nolgia_home` to
  within 0.0001. Verify this in code and report it.
- Preview the exploded hold: no two parts may overlap, every part visible from
  the camera, nothing leaving the frame.
- Preview the midpoint of the assembly: motion reads outside to inside.

## Deliver

Save, export an `mp4` preview, and tell the person: how the layers were
ordered, how to change the explode distance (one controls value on an empty
`Exploded View Controls` that scales every offset through a driver), and how
to reorder a part (move its keyframes).
