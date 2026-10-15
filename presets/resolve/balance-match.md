---
slug: resolve-balance-match
name: Balance and match shots in DaVinci Resolve
category: Film assistant
app: resolve
min_app_version: "21.1"
beta: true
starter_prompt: "Use NOLGIA to balance and match the shots on my DaVinci Resolve timeline: read my camera log, pick a hero shot with me, balance every other shot to it, then put a NOLGIA film stock on top, all in a node of its own so my grade stays untouched."
---

# Balance and match shots in DaVinci Resolve

Do the first pass a colorist does before the look: read the footage, balance
each shot, and match the shots to a hero shot so the scene cuts together. Then
the film stock goes on top. Everything goes in one node of its own on each
clip, the node the "Grade a timeline" workflow uses for the stock. The
balance is a CDL Offset per channel, set in log before the stock, the way
printer lights work. The person's own nodes, LUTs and versions are never
touched.

This workflow is for camera log footage (Sony S-Log3, ARRI LogC3, Panasonic
V-Log, Blackmagic Film Gen 5) on a project that is not color managed
(`colorScienceMode` is `davinciYRGB`), with no Color Space Transform or
camera LUT already on the clips. Offsets in log are exact stops of exposure
and color there. On Rec.709 footage, or with the log converted earlier in the
grade, say so and use "Grade a timeline in DaVinci Resolve" alone.

## Ask

1. Which clips: every clip on the current timeline (default), the selected
   clips, or one video track.
2. The hero shot: the shot the others are matched to. Offer the clip under the
   playhead, or suggest one: well exposed, with skin, in the scene's main
   light. Show its still and wait for a yes unless they said "go". The hero
   keeps its own white, so a warm scene stays warm.
3. How far to match exposure: up to half a stop either way (default), so a
   low-key shot keeps its mood; a full stop; or color only, with no exposure
   change.
4. The film stock, as in "Grade a timeline in DaVinci Resolve" step 3. Default:
   your first suggestion from the hero's still.

## Build

1. **Read the timeline and the grades.** As in "Grade a timeline" step 1: the
   clips in scope with their frames, `Gamma Notes`, `Color Space Notes`,
   `Camera Type` and `Input Color Space`, the project's `colorScienceMode`,
   and each clip's node graph (`GetNumNodes`, `GetNodeLabel`,
   `GetToolsInNode`, `GetLUT`). Keep the list. Resolve often leaves `Camera
   Type` and `Gamma Notes` empty (renamed Sony XAVC files, for example). Then
   read the camera's own sidecar next to the clip's `File Path`: a Sony
   `NonRealTimeMeta` XML with the clip's name and `.XML` or `M01.XML`, whose
   `Device modelName`, `CaptureGammaEquation` and `CaptureColorPrimaries` name
   the camera and the log (`s-log3-cine`, `s-gamut3-cine`). Name the camera log in one line
   ("23 clips, Sony ILME-FX3A, S-Log3 / S-Gamut3.Cine") and stop with an
   explanation if the footage isn't one of the four logs above or the project
   is color managed.
2. **Find each clip's NOLGIA node.** Same rule as "Grade a timeline" step 5:
   an empty node, the last one when there are several (an ungraded clip has
   one, empty), or a new local color version `NOLGIA balance` when a clip has
   none. Record per clip where it is.
3. **Read each shot.** One `nolgia_app_run` per clip (or a few clips per run):
   move the playhead to the clip's middle frame, export the frame with
   `project.ExportCurrentFrameAsStill(path)` to a `.bmp` in the system temp
   folder, read it with the reader below, then delete the file. From a grid
   of about 160 x 90 samples, compute the two numbers below in scene light and
   return them. Never keep frames. Compute each percentile once per shot, not
   inside a loop over the pixels: the whole timeline then reads in seconds.
   - **White:** the median red, green and blue of the least saturated 30
     percent of the pixels between the 40th and 97th brightness percentile
     (walls, shirts, paper, sky: what should be white or grey).
   - **Mid exposure:** the median brightness between the 20th and 95th
     percentile.
4. **Compute the balance.** For each clip, against the hero:
   - color, in stops per channel: `log2((hero_white / mean(hero_white)) /
     (white / mean(white)))`, so the clip's white lands on the hero's white;
   - exposure, in stops: `log2(hero_mid / mid)`, clipped to the range from
     question 3 (0 for the hero);
   - the CDL Offset per channel is `(color + exposure) x stop`, where `stop` is
     one stop in the camera's log code: S-Log3 0.0770, ARRI LogC3 (EI 800)
     0.0744, Panasonic V-Log 0.0727, Blackmagic Film Gen 5 0.0603, divided by
     1.0704: an Offset of 1.0 in `SetCDL` moves a still exported with
     `ExportCurrentFrameAsStill` by 1095/1023 (legal to full range), so values
     measured on those stills are scaled back. Slope 1 1 1, Power 1 1 1,
     Saturation 1.
   Show the table (clip, color stops R G B, exposure stops) before anything is
   set, and wait for a yes unless they said "go".
5. **Set it.** One `nolgia_app_run`: for each clip,
   `item.SetCDL({"NodeIndex": "<n>", "Slope": "1 1 1", "Offset": "<r> <g> <b>",
   "Power": "1 1 1", "Saturation": "1"})` on its NOLGIA node, and read the
   graph back. The Color page must be open for grading calls (see "Grade a
   timeline" step 5).
6. **The stock on top.** Install the stock's log version with
   `nolgia_app_import` `color_preset: "<slug>-<log>"` and `SetLUT` it on the
   same NOLGIA node of every clip, exactly as "Grade a timeline" steps 4 and 5.
   In a node, Resolve applies the LUT after the node's own corrections (CDL
   included), so the balance happens in log and the stock converts it. A color
   group's post-clip graph also has a node of its own, if the person groups
   the shots and wants the stock there instead, once for the group.
7. **Before and after.** `nolgia_app_preview` of the hero and of two matched
   clips at the same frames as step 3, with the stock on, and the before
   stills from before step 5. Say what moved in two lines per clip.

The reader for step 3 (standard library only, as Resolve's Python has no
Pillow). It returns rows of `(r, g, b)` in 0..1 on a coarse grid:

```python
import struct

def read_bmp_grid(path, cols=160, rows=90):
    with open(path, "rb") as f:
        data = f.read()
    off = struct.unpack_from("<I", data, 10)[0]
    w, h = struct.unpack_from("<ii", data, 18)
    bpp = struct.unpack_from("<H", data, 28)[0] // 8
    stride = (w * bpp + 3) & ~3
    top_down = h < 0
    h = abs(h)
    grid = []
    for j in range(rows):
        y = int((j + 0.5) * h / rows)
        row = off + (y if top_down else h - 1 - y) * stride
        for i in range(cols):
            p = row + int((i + 0.5) * w / cols) * bpp
            b, g, r = data[p], data[p + 1], data[p + 2]
            grid.append((r / 255.0, g / 255.0, b / 255.0))
    return grid
```

Scene light from log code (0..1), for the white and mid exposure:

```python
import math

def slog3_to_linear(c):
    cv = c * 1023.0
    if cv >= 171.2102946929:
        return (10 ** ((cv - 420.0) / 261.5)) * 0.19 - 0.01
    return (cv - 95.0) * 0.01125 / (171.2102946929 - 95.0)

def logc3_to_linear(c):  # ARRI LogC3, EI 800
    if c > 0.149658:
        return (10 ** ((c - 0.385537) / 0.247190) - 0.052272) / 5.555556
    return (c - 0.092809) / 5.367655

def vlog_to_linear(c):
    if c < 0.181:
        return (c - 0.125) / 5.6
    return 10 ** ((c - 0.598206) / 0.241514) - 0.00873

def bmdfilm5_to_linear(c):
    if c < 0.13388378:  # the linear toe below scene light 0.005
        return (c - 0.09246575342465753) / 8.283605932402494
    return math.exp((c - 0.5300133392291939) / 0.08692876065491224) - 0.005494072432257808
```

## Check

- The before and after stills: the matched clips' whites read like the
  hero's; skin stays skin (no green or magenta); no clip moved more than the
  exposure range the person chose; nothing clips harder than in the LUT-only
  look.
- Read the grades again and compare with step 1: every node the person had is
  the same; the only change per clip is its NOLGIA node (CDL and LUT) or the
  `NOLGIA balance` version.
- If a clip's white came from a strongly colored scene (all neon, all
  sunset), its balance may fight the scene: say which clips and offer color
  only for them, or to leave them out.

## Deliver

Save the project. Tell the person the hero shot, the table of offsets per
clip, where they sit (node or version), how to take one back (on the Color
page, that node's Offset wheel back to zero, or the clip's own version in its
Versions menu), the stock and its LUT path, and the safety copy's path.

What this workflow never does: it never changes the person's own nodes, LUTs
or versions, never matches beyond the range they chose, and never renders or
deletes anything without asking first.
