---
slug: resolve-color-grading
name: Grade a timeline in DaVinci Resolve
category: Film assistant
app: resolve
min_app_version: "21.1"
beta: true
starter_prompt: "Use NOLGIA to grade the timeline open in DaVinci Resolve with a NOLGIA film stock: suggest stocks that suit my footage, show me before and after stills from the same frame, keep my own grade untouched, and let me try another stock."
---

# Grade a timeline in DaVinci Resolve

Give the clips on the person's timeline the look of a real film stock: a
NOLGIA film stock LUT installed in their Resolve, applied in a place of its
own so their own grade stays exactly as it was, with a before and an after
still from the same frame to judge it by. The stock can be swapped, switched
off or dialled back by the person at any time.

## Ask

1. Which clips get the stock: every clip on the current timeline (default),
   the clips selected in the timeline, the clip under the playhead, or one
   video track.
2. The look they are after: a few words ("warm and soft", "clean daylight",
   "night, tungsten"), a film or a reference image, or your suggestion. Look
   at a still first (see below) and propose two or three stocks that suit the
   footage, by their real names, with one line each on why. (Default: your
   first suggestion.)
3. Where the stock may live. The default is an empty node at the end of each
   clip's grade (an ungraded clip has exactly one, so nothing of theirs
   changes). When the timeline's own node graph already has an empty node
   (the person added one on the Color page; a fresh timeline graph has none
   and the API cannot add one), the stock can go there once for every clip.
   The other way is a new local color version of each clip named after the
   stock, so their own version stays as it was. (Default: an empty node on
   each clip; the timeline graph when it has one; a new version when a clip
   has no empty node.)

## Build

1. **Read the timeline and the grades.** One `nolgia_app_run` that lists the
   clips in scope (track, name, first and last frame, duration, `FPS` and
   `Resolution` from `GetMediaPoolItem().GetClipProperty()`) and, for each,
   its grade: `item.GetNodeGraph()`, `GetNumNodes()`, and per node
   `GetNodeLabel(i)`, `GetToolsInNode(i)` and `GetLUT(i)`, plus
   `item.GetCurrentVersion()`. Read the timeline's own graph the same way
   (`timeline.GetNodeGraph()`). Keep this list: at the end you compare against
   it. Say in one line what is already graded (a clip with a LUT or an
   output transform means the stock will stack on top; say so).
2. **The before still.** Pick a frame that shows skin or the main subject in
   good light, in the middle of a clip (frame counts from 0 at the start of
   the timeline), and call `nolgia_app_preview` with that `frame`. Remember
   the frame number: the after still is taken from the same one.
3. **Propose stocks.** From the still and the clip facts (daylight or
   tungsten, how much contrast there is, how saturated it is, whether skin is
   in the frame), name two or three stocks with their real names and
   descriptions, read from `nolgia_list_color_presets`. These are NOLGIA's film
   stocks:

   | Slug | Name | Description |
   |---|---|---|
   | `kodak-vision3-500t` | Kodak Vision3 500T | The modern film workhorse: tungsten balanced, warm shadows, soft highlight roll. |
   | `kodak-vision3-250d` | Kodak Vision3 250D | Daylight balanced negative: clean neutral skin, fine grain, gentle roll. |
   | `kodak-vision3-200t` | Kodak Vision3 200T | Medium speed tungsten stock: 500T's warmth with a tighter, cleaner feel. |
   | `kodak-2383-print` | Kodak 2383 Print | Theatrical print stock: deeper blacks, gentle cyan shadows, warm highlights. Pairs over any negative. |
   | `kodak-portra-400` | Kodak Portra 400 | Pastel warmth and forgiving skin: low contrast, creamy highlights. |
   | `kodak-portra-800` | Kodak Portra 800 | Portra's skin with more presence: a touch more contrast and warmth for low light. |
   | `kodak-ektar-100` | Kodak Ektar 100 | Saturated daylight stock: punchy reds and blues, fine grain, crisp contrast. |
   | `kodachrome-64` | Kodachrome 64 | The archive look: rich reds, dense shadows, unmistakable warmth. |
   | `fuji-eterna-3510` | Fuji Eterna 3510 | The cinematic counterweight to Kodak: soft contrast, cool neutral, quiet skin. |
   | `fuji-eterna-vivid` | Fuji Eterna Vivid | Eterna's cool base with the color turned up: saturated without going warm. |
   | `fuji-reala-500d` | Fuji Reala 500D | Neutral daylight negative: honest color, natural greens, easy skin. |
   | `fuji-velvia-50` | Fuji Velvia 50 | The landscape slide: high saturation, deep blues and greens, dense contrast. |
   | `fuji-provia-100f` | Fuji Provia 100F | The neutral slide: accurate color with slide-film snap, nothing pushed. |
   | `fuji-superia-400` | Fuji Superia 400 | The consumer 90s negative: green tinted shadows, a magenta bias in skin. |
   | `cinestill-800t` | CineStill 800T | Tungsten night stock with the remjet gone: teal shadows and red halation blooming around light sources. |
   | `ilford-hp5` | Ilford HP5 Plus | Documentary black and white: soft contrast, generous shadows, honest grain. |
   | `kodak-tri-x` | Kodak Tri-X 400 | The classic reportage black and white: punchy contrast, deep blacks, bite. |
   | `agfa-vista` | Agfa Vista | The corner store classic: warm reds against slightly cool shadows. |
   | `arri-alexa-rec709` | ARRI Alexa Rec709 | The K1S1 LogC to Rec709 look: the neutral cinematic display transform. |

   Daylight footage with people usually wants Portra 400, Vision3 250D or
   Reala 500D; tungsten interiors Vision3 500T or 200T; neon and street light
   at night CineStill 800T; landscapes Ektar 100 or Velvia 50. The list also
   holds stylised looks (teal and orange, bleach bypass, cross process, and
   others): offer those only when the person asks for a stylised look. Wait
   for a choice unless they said "go", then take your first suggestion.
4. **Install the stock.** `nolgia_app_import` with `color_preset: "<slug>"` and
   nothing else. It installs `<name>.cube` in the `NOLGIA` folder of Resolve's
   LUT folder, refreshes the LUT list and answers `lut` (the path Resolve lists
   it under, `NOLGIA/Kodak Portra 400.cube`) and `path` (the file). Keep `lut`.
5. **Apply it in a place of its own.** One `nolgia_app_run`, in this order of
   preference, never on a node that already holds tools or a LUT. An empty
   node is one where `GetToolsInNode(i)` answers None or `[]` and `GetLUT(i)`
   is `""`:
   - *Each clip:* `g = item.GetNodeGraph()`, find an empty node, preferring
     the last one so the stock comes after their corrections (an ungraded
     clip has one node, empty), call `g.SetLUT(i, lut)` and read `g.GetLUT(i)`
     back.
   - *All clips at once, only when the timeline graph has an empty node:*
     `g = timeline.GetNodeGraph()`; when `g.GetNumNodes()` is at least 1 and
     one node is empty, `SetLUT` there and every clip gets the stock after its
     own grade. A fresh timeline graph has no node and the API cannot add
     one, so this is the exception, not the default.
   - *A clip with no empty node:* the API cannot add a node. Either add a new
     local color version named `NOLGIA <stock name>`
     (`item.AddVersion(name, 0)` then `item.LoadVersionByName(name, 0)`),
     read what its graph holds with `GetNumNodes()` and `GetToolsInNode(i)`
     before you set the LUT on an empty node of it, and tell the person that
     their own version is still there under the clip's Versions menu; or ask
     the person to add a serial node at the end of the grade of those clips
     (Alt+S, Option+S on a Mac, on the Color page) and say which clips.
   Grading calls work on the Color page: if `GetNodeGraph()` answers None or
   `SetLUT` keeps answering False, read `resolve.GetCurrentPage()`, switch
   with `resolve.OpenPage("color")`, run the step and put the page back.
   `SetLUT` takes `lut` (relative) or `path` (absolute); if both answer False,
   call `project.RefreshLUTList()` once and try again. Record in `result`,
   per clip, exactly where the stock went (timeline node 1, clip node 3, or
   version name) and which clips got nothing and why.
6. **The after still.** `nolgia_app_preview` at the same `frame` as the before
   still. Show both and say what changed in two lines (skin, shadows,
   highlights, saturation). Then offer: keep it, try another stock (set the
   new `lut` on the same node with `SetLUT`, so there is still only one), or
   dial it back.
7. **Dialling it back.** The scripting API cannot read or set a node's key
   output gain, so say where it is: Color page, click the node that holds the
   stock, open the Key palette and lower Key Output Gain (1.0 is the full
   stock, about 0.5 is half). `SetNodeEnabled(i, False)` switches the node off
   and on again for a quick comparison. A gentler stock (Portra 400 instead
   of Ektar 100, Vision3 250D instead of 2383) is the other way.

## Check

- Before and after at the timeline's width (the plugin caps `width` at it): skin must still read as
  skin (no green or magenta cast), blacks must not be crushed and highlights
  must not clip harder than before; a stock stacked on a clip that already had
  a LUT must be said and shown.
- Read the grades again, as in step 1, and compare: every node the person had
  still has the same label, tools and LUT; the only differences are the node
  (or version) you used.
- Preview two more frames (a darker clip, a second scene) and look at them.

## Deliver

Save the project. Export the after frame with `nolgia_app_export` `png`, and
offer an `mp4` of a short range (`frames` such as `0-119`) so they can see the
stock in motion. Tell the person: the stock's name and its LUT path
(`NOLGIA/<name>.cube`), where it sits on each clip (timeline node, clip node
or version), how to switch it off (the node's toggle on the Color page, or
their own version in the clip's Versions menu), how to dial it back (Key
Output Gain), and the path of the safety copy.

What this workflow never does: it never changes, resets or disables the
person's own nodes, LUTs, versions or settings; it never adds a LUT to a node
that holds anything; and it never renders, overwrites or deletes anything
without asking first.
