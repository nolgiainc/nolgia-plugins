# NOLGIA for DaVinci Resolve: commands

The command contract of the NOLGIA plugin for DaVinci Resolve Studio
(app id `resolve`): what each command takes and returns. How to install and
use the plugin is in the [main README](../README.md#nolgia-for-davinci-resolve).

Commands reach the plugin through the NOLGIA API (`POST /bridge/commands`
with `"app": "resolve"`, a `kind` and `args`) and the MCP tools built on it.
Every command answers `{status, result}` or `{status: "failed", error}`; a
failed `run` also carries `result: {value: null, stdout, stderr}`.

Frames count from 0 at the start of the timeline. `info` gives the
timeline's start timecode and start frame: Resolve's own scripting API
numbers frames from the start timecode (01:00:00:00 at 24 fps is frame
86400), so `run` code that calls Resolve directly uses those numbers.

## `info`

No arguments. Result:

```
{
  app: "resolve", app_version, product, studio,  // Resolve's GetVersionString, GetProductName, IsStudio
  page,                                           // media, cut, edit, fusion, color, fairlight, deliver (or photo)
  document: {name},                               // the open project's name
  project: {name, folder, database, width, height, fps,
            in_library: bool,                     // false for a project never saved with a name
            unsaved_changes: null,                // Resolve's scripting cannot tell
            changed_by_nolgia_since_save: bool, note} | null,
  timeline: {name, fps, width, height, start_timecode, start_frame, end_frame, duration_frames,
             current_timecode, current_frame,
             tracks: {video: [{index, name, items, enabled, locked}], audio: [...], subtitle: [...]},
             selected_clips: [{name, track: "V1", start_frame, end_frame}],
             clip_under_playhead: {name, track, start_frame, end_frame} | null,
             markers} | null,
  current_timecode,                               // null without a timeline
  timelines: [{name, current}],
  media_pool: {root, root_clips, current_bin, bins: [{name, clips, bins}]},
  render: {presets: [...], format_and_codec: {format, codec}, jobs, rendering}
}
```

## `run`

| Argument | |
|---|---|
| `language` | `"python"` (the default; anything else fails) |
| `code` | the Python to run |
| `timeout_seconds` | optional; the plugin also stops in time to report before the command expires |

The code sees `resolve`, `project_manager`, `project`, `media_pool`,
`timeline` (the current one, or `None`), `fusion` and `result`. Result:
`{value, stdout, stderr}`, where `value` is what the code put in `result`,
made JSON (Resolve objects become their names). A failure returns the
traceback in `error`. The code runs on the plugin's own thread, one command
at a time; `nolgia_resolve.disconnect()` and `nolgia_resolve.close_window()`
switch NOLGIA off from code.

## `preview`

| Argument | |
|---|---|
| `frame` | optional, from 0 at the start of the timeline |
| `timecode` | optional, instead of `frame`, e.g. `"01:00:05:12"` |
| `timeline` | optional timeline name (`camera` is accepted as the same thing: it is what the MCP preview tool calls it); default the current timeline |
| `width` | optional, 16 to 1920; default 1280, never wider than the timeline |

Default frame: the current one (Resolve has no playhead for scripts on the
Media and Fusion pages, so the plugin switches to the Edit page for the
moment it needs). Result: `{asset_id, width, height,
mime_type, timeline, frame, timecode}`. The still is uploaded to the NOLGIA
library: a PNG; when that would be over about 3.6 MB (too big for an agent
to see inline), Resolve's own JPEG of the frame if it has the size asked
for, else a PNG with fewer colour levels. The playhead and the current
timeline are put back.

## `import_asset`

Exactly one of:

| Argument | |
|---|---|
| `asset_id` | one asset from the NOLGIA library |
| `asset_ids` | several assets, a list of 1 to 100 ids, imported in this order |
| `project_id` | every ready video, image and audio asset of a NOLGIA project, oldest first (at most 200) |
| `color_preset` | a NOLGIA color preset by slug (`GET /color-presets`), e.g. `kodak-portra-400`, installed as a LUT |

and optionally:

| Argument | |
|---|---|
| `bin` | the media pool bin (under the top bin) to import into; made when missing; default `NOLGIA imports` |
| `append` | `true` to add the media to the end of the current timeline, in order (a new timeline named `NOLGIA timeline` when none is open, made from the clips) |
| `apply_to` | for a single LUT: `current` (the video clip under the playhead), `selected` (the clips selected in the timeline), `current_track` (every clip on the video track of the clip under the playhead) or `all` (every video clip in the timeline) |
| `node` | the grade node to set the LUT on, from 1; default 1 |

Images, video and audio are imported into the bin. With `append`, each clip
goes after the timeline's last clip, whatever its track (that is how Resolve
21.1.1 appends), in the order given; Resolve leaves its playhead at the end.
A `.cube` file from the library (by name, type or content) and a color preset
are installed as LUTs instead: into a `NOLGIA` folder in the LUT folder
Resolve reads (on Windows `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\LUT`,
the folder for all users, which every user may write to; see the main README
for macOS and Linux), named after the preset or the file, then Resolve's LUT
list is refreshed. A color
preset replaces its own older copy; a library LUT never replaces another
file (it gets a number). 3D models are refused. With `project_id`, other
kinds of assets are skipped and listed in `skipped`.

Result for media:

```
{
  imported: [clip names, in order],
  asset_ids: [ids, in order],
  assets: [{asset_id, name, kind, path, already_in_bin?}],   // kind: image, video, audio or lut
  kind: "video" | "image" | "audio" | "mixed",
  bin,
  path,                                   // with a single asset only
  skipped?: [{asset_id, name, why}],      // project_id only
  appended?: {timeline, new_timeline, items: [{name, track: "V1" | "A1", start_frame, end_frame}]}
}
```

Result for a LUT (`color_preset`, or a single `.cube` asset):

```
{
  imported: [LUT name], kind: "lut", path, lut: "NOLGIA/Kodak Portra 400.cube",
  color_preset?, asset_ids?: [id],
  applied_to?: [{name, track, start_frame, end_frame}], not_applied?: [{..., why}], node?
}
```

`lut` is the path under the LUT folder (Resolve 21.1.1's own `GetLUT()` then
reports it as `NOLGIA\Kodak Portra 400.cube` on Windows). When `apply_to` is
given and no clip took the LUT, the command fails, with the result above (the
LUT stays installed). `current` and `current_track` need a video clip under
the playhead; on the Media and Fusion pages, which have no playhead for
scripts, the plugin switches to the Edit page for the moment it needs.

## `export`

| Argument | |
|---|---|
| `format` | `png` (one frame) or `mp4` (H.264, with the timeline's audio) |
| `frames` | optional: `"24"` or `"0-119"` (also `24`, `[0, 119]`, `{"start": 0, "end": 119}`); `png` default the current frame, `mp4` default the whole timeline |
| `filename` | optional; default the project's name |
| `timeline` | optional timeline name; default the current timeline |

Renders at the timeline's size and uploads the file. Result: `{asset_id,
format, filename, frames: [first, last], width, height, timeline}`, plus
`timecode` for `png`. Renders go to a temporary folder (never over a file of
yours) that is removed afterwards; the render job is removed from the render
queue, the Deliver page's settings, the page Resolve was on and the
playhead are put back (rendering opens the Deliver page and moves the
playhead).

## `save`

No arguments (a `path` is refused: Resolve keeps projects in its project
library). Saves the open project. Result: `{project, saved: true}`.

A project Resolve made itself (New Project, Untitled Project) is not in the
project library until it is saved with a name (`info` reports `in_library:
false`), and Resolve's `SaveProject()` on it opens the Save dialog, which
blocks every script until someone closes it. `save` refuses such a project
instead, with a message asking the person to save it once by hand (File >
Save Project).

## `open`

| Argument | |
|---|---|
| `project` | the project's name (`name` is accepted too) |
| `folder` | optional project folder path, e.g. `Clients/ACME`; default the current folder |

Result: `{project, folder}`, or `{project, already_open: true}`. Resolve's
scripting cannot tell whether the open project has unsaved changes, so with
the NOLGIA window open the person is asked first (Open project or Deny);
without a window it is refused only when NOLGIA itself changed the project
since it was last saved.

When the open project has never been saved (`in_library: false`), loading
another over it could make Resolve ask about saving it, so the plugin closes
it first with `CloseProject`, which Resolve documents as closing without
saving. An empty one (no timelines, clips or bins) is closed without asking;
one with content needs the person's Open and lose it in the NOLGIA window,
and is refused without a window, with a message to save it with a name or
close it by hand first.
