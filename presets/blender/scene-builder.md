---
slug: blender-scene-builder
name: Build a scene in Blender
category: Film assistant
app: blender
min_app_version: "4.2"
starter_prompt: "Use NOLGIA to build a 3D scene in my open Blender file from my brief: the setting, the characters and props, their poses, and three camera angles to choose from."
---

# Build a scene in Blender

Lay out a shot from a brief: a set, the people and props in it, poses that
read, light that fits the mood, and a small choice of cameras. Everything is
separate and editable so the person can keep directing it.

## Ask

1. The setting and time of day. (Offer three quick options if the brief is
   vague.)
2. Who and what is in it, and what is happening (one sentence of action).
3. The look: realistic, stylised, or a reference image. If they give an image,
   ask whether it is for layout, for mood, or both.
4. Which models do they already have? For anything missing, offer to make it
   with NOLGIA: `nolgia_generate_3d` from a photo of the object or from an
   image made with `nolgia_text_to_image`. For a character, make a full-body
   image of them as a figure on a plain background, in the pose the shot needs
   (seated on a plain block if they sit; an A pose only if you will rig them),
   and turn that into the model; never work from a photo of a real person.
   Generated people bake the reference image's lighting into their texture and
   faces can read wrong, so plan to frame them so faces are not the main read
   (back light, profiles, shallow focus, details). If
   `hunyuan3d-v3` times out, `trellis` is the fast, cheap fallback. Say the
   rough credit cost before generating, and generate only what they agree to.

## Build

1. **A plan first.** Write a short shot plan back to the person: the set pieces,
   characters and props with their rough positions, the key light direction,
   and three camera ideas (wide establishing, medium on the action, a close or
   low angle). Wait for a yes or changes.
2. **Set.** Block the space with simple geometry at real-world scale (metres):
   floor, walls or terrain, the big shapes that define the silhouette. Put it in
   `NOLGIA Set`. Use the person's assets where they have them.
3. **Bring in characters and props.** Import each with `nolgia_app_import`
   (GLB), shade smooth, scale to real size (people 1.6 to 1.9 m standing; seat
   to crown 0.8 to 1.0 m seated), place on the floor or seat (no floating, no
   clipping; hide a generated seat block inside the real seat), and put them in
   `NOLGIA Cast` and `NOLGIA Props`.
4. **Pose.** Rigged characters: pose the armature bones for the action with the
   weight on the right foot, eyelines toward what they look at, hands touching
   what they hold. Unrigged generated characters: keep them as statues and say
   so; offer to rig later.
5. **Light.** A key, a fill or bounce, and a rim or practical that motivates the
   mood; a world colour or an HDRI (make a sky or environment plate with
   `nolgia_text_to_image` if asked). A background plate goes far away as an
   emission plane: keep every camera inside its field of view, extend its sky
   if the wide sees above it, and match the sun lamp to the sun in the plate.
   Name every light for its job. Prefer natural, motivated light (sun, sky,
   bounce, practicals) unless the brief asks for a stylised look.
6. **Cameras.** Three cameras named for their framing (`Cam Wide`,
   `Cam Medium`, `Cam Close`), each composed on thirds with a clear foreground,
   subject and background, 24 to 35 mm for the wide, 50 mm for the medium, 85
   mm for the close. Add depth of field on the medium and close, focused on the
   subject's eyes, or on the action for a detail close (hands, an object).

## Check

- Preview every camera. Each must read the action in one glance: subject
  sharp, eyeline clear, nothing important cut by the frame edge.
- Scale check in code: every standing character between 1.5 and 2.0 m tall
  (seated: seat to crown 0.8 to 1.0 m) unless the brief says otherwise; every object's lowest point within 1 cm of the floor or
  of the object it rests on (a lid on a bin, a crate on a crate).

## Deliver

Save, export a `png` from each camera, and show them side by side so the person
can pick. Tell them where things live (the three collections), which camera is
active, and how to switch.
