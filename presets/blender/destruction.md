---
slug: blender-destruction
name: Destruction in Blender
category: Film assistant
app: blender
min_app_version: "4.2"
starter_prompt: "Use NOLGIA to break something apart in my open Blender scene: fracture it, simulate the debris and dust, and give me a camera that shows the moment clearly."
---

# Destruction in Blender

Break an object in the person's scene with a believable collapse: fractured
pieces, debris with weight, dust, and a camera that reads the moment. The
result stays editable: the fracture can be redone at a different piece count
and the simulation re-baked.

## Ask

1. What breaks? (Default: the selected object; if nothing is selected, list
   the three largest meshes and ask.)
2. What breaks it? A hit from an object, a push from one side, an explosion
   from inside, or gravity (a collapse). (Default: a hit from the front.)
3. How long is the shot, and when does the break start? (Default: 4 seconds,
   break at 1 second.)
4. How many pieces: few and chunky, or many and fine? (Default: chunky, about
   40.)

## Build

1. **Fracture a copy, keep the original.** Duplicate the target into the
   `NOLGIA Destruction` collection and hide the original (do not delete it).
   Prefer the Cell Fracture extension if it is installed
   (`bpy.ops.object.add_fracture_cell_objects`); check with
   `addon_utils.check("bl_ext.blender_org.cell_fracture")` or the legacy name.
   If it is not installed, do not install anything without asking: build a
   Voronoi fracture yourself (scatter points inside the bounds, weighted toward
   the impact point, and cut the mesh into convex cells with bmesh bisects per
   point pair), or ask the person to install Cell Fracture from Get Extensions.
   Keep piece count, seed and impact bias as custom properties on an empty named
   `Destruction Controls` so the fracture can be regenerated.
2. **Hold it together until the moment.** Make every piece an active rigid body
   (mesh or convex hull collision, mass from volume), and connect neighbours
   with breakable Fixed constraints whose breaking threshold is exposed on the
   controls empty. Pieces start still.
3. **The trigger.** A hit: a passive or animated rigid body (a simple rounded
   block or the person's own object) moving through the target at the break
   frame, with enough speed to exceed the threshold. A push or explosion: a
   Force field (Force or Wind) keyframed on at the break frame, centred at the
   impact point. A collapse: remove support by animating the lowest
   constraints' enabled state off.
4. **Ground and weight.** Make sure there is a passive floor. Set the rigid
   body world to 60 substeps and 20 solver iterations for stability; gravity
   stays real unless the person wants slow motion (then scale time instead).
5. **Dust and small debris.** A particle system emitted from the fracture faces
   (the inside faces), starting at the break frame for about 0.5 s: small
   instanced chips plus a smoke or dust puff (Quick Smoke on a small emitter,
   or a volume shader on a scaled cube keyed in density if the person wants it
   fast). Keep dust subtle: debris sells weight, dust sells scale.
6. **Camera.** A new camera `Destruction Cam` framing the object with room for
   the debris to fall, at eye level or slightly low, 35 to 50 mm. Add a small
   push-in over the shot, and a short shake (a noise modifier on rotation) for
   4 to 8 frames at the break. Set it as the scene camera only after asking.
7. **Bake.** Bake the rigid body cache for the shot's frame range. Follow long
   bakes with `nolgia_app_command`.

## Check

- Preview three frames: just before the break, the break, and 1 second after.
  Pieces must not jitter before the break, must not explode outward unless an
  explosion was asked for, and must settle on the floor without sinking.
- If pieces fly off too fast, lower the trigger speed or raise the mass. If
  nothing breaks, lower the breaking threshold.

## Deliver

Save, export an `mp4` preview of the shot, and tell the person the controls:
piece count and seed (re-run to refracture), breaking threshold, and trigger
speed or force strength, all on `Destruction Controls`.
