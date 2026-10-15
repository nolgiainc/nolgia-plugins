---
slug: photoshop-photo-set-grade
name: Grade and clean up a set of photos in Photoshop
category: Film assistant
app: photoshop
min_app_version: "25.0"
beta: true
starter_prompt: "Use NOLGIA to finish my selected shoot photos in Photoshop: one consistent grade, remove small distractions like signs and stray gear, keep every change on its own layer, save layered PSDs and export web JPEGs."
---

# Grade and clean up a set of photos in Photoshop

Take a set of selected photos from a shoot to finished, consistent pictures:
the same grade on every photo as a group of adjustment layers, small
distractions removed on masked layers of their own, a layered PSD per photo to
keep working in, and a web JPEG of each. The person decides what is a
distraction; nothing about a person's face or body is changed.

## Ask

1. Which photos: a folder of selects (default), or the open documents. Camera
   raw files open through Camera Raw; if its dialog appears it holds every
   script (see the dialog rule), so either the person opens them, or work from
   16-bit TIFFs of them. Say which.
2. The look: natural and clean (default), warmer, cooler, moodier, punchier.
3. Cleanup on which photos: none, the ones they name (default), or your
   suggestions after you have looked. List what you would remove, per photo,
   and let them pick: exit signs and wall fixtures, stray gear, a crew member
   cut by the frame edge. Never retouch skin, faces, bodies or clothing.
4. Where the PSDs and web JPEGs go (default: next to the photos, in
   `PSD` and `WEB` folders), and the web size (default 2048 px on the long
   side, quality 10).

## Build

1. **Look first.** `nolgia_app_preview` each photo you will touch (or the first
   few of a long set) and note its brightness and color. Measure, do not guess:
   a quick mean of the preview's pixels tells a dark photo from a bright one.
2. **Per photo, one `nolgia_app_run`**: open it (`app.open(entry)`), save it
   as `<name>_NOLGIA.psd` (the original file is never written), add the grade,
   save, export the web copy, close. Keeping one run per photo makes every
   photo one row in the panel's Activity and one step in History.
3. **The grade** is a group `NOLGIA Grade` of adjustment layers, made with
   `play([{ _obj: "make", _target: [{ _ref: "adjustmentLayer" }], using: {
   _obj: "adjustmentLayer", name, type } }])` and moved into the group:
   Exposure (`{ _obj: "exposure", exposure, offset: 0, gammaCorrection }`),
   Contrast (`curves`, a gentle S), Color (`vibrance`), Warmth
   (`photoFilter`, density 8 to 12). Scale the exposure by the measured
   brightness: a dark flash-lit interior needed about +1.0 stop, a bright one
   +0.2. Use the same contrast, color and warmth across the set so it reads as
   one shoot.
4. **Cleanup** follows the rules above: a group `NOLGIA Cleanup`, a copy of the
   photo per removal named for what it removes (`Remove: EXIT sign`), the
   selection made a little larger than the object (6 to 10 px), Content-Aware
   Fill on the copy, then a mask of just the patch. Take the whole object:
   an exit sign's lamp heads and mounting count, and stopping a few pixels
   short of a door frame keeps the fill from inventing a new fixture.
5. **The web copy**: `doc.duplicate(name, true)`, flatten, `convertMode`
   to 8 bits, `resizeImage` to the long side, `saveAs.jpg`, then
   `closeWithoutSaving()` on the copy and make the PSD active again.

## Check

- Before and after preview of every photo you cleaned up, and a few from the
  batch, looked at side by side. A fill that left a lighter or darker patch,
  or invented an object, is redone with a better selection.
- `nolgia_app_info` on a PSD: the person's Background is untouched, the two
  groups hold only your layers.
- Count the files: one PSD and one JPEG per photo.

## Deliver

Tell the person: how many photos were graded, which were cleaned up and what
was removed, the exposure used per brightness band, and where the PSDs and
JPEGs are. Every value can be changed by opening the adjustment layers.
