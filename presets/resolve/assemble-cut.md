---
slug: resolve-assemble-cut
name: Assemble a cut in DaVinci Resolve
category: Film assistant
app: resolve
min_app_version: "21.1"
beta: true
starter_prompt: "Use NOLGIA to assemble a cut in DaVinci Resolve from one of my NOLGIA projects: bring its clips into a bin, lay them on a new timeline in order at the clips' frame size and frame rate, add my music if I have some, and show me the first frame."
---

# Assemble a cut in DaVinci Resolve

Turn a NOLGIA project into a first cut in the person's Resolve: its clips in
a bin named after the project, in the order they were made, on a new
timeline at the clips' own frame size and frame rate (vertical 1080x1920
clips are common), with music on its own track if they have some, and a
first frame to look at. Their existing timelines are never touched.

## Ask

1. Which NOLGIA project? List them with `nolgia_list_projects` (name and
   asset count), then show what the chosen one holds with `nolgia_list_assets`
   and `project_id` (names, kinds, how many videos, images and audio clips).
   It lists newest first; the import puts them oldest first, so say the order
   you will use.
2. The order: the order the clips were made (default), or an order they give
   by name.
3. The timeline: named after the project (default), at the frame size most
   of the clips share and the frame rate most of them share (default). Clips
   that differ are scaled into the frame by Resolve; name them.
4. Music: an audio clip from the project (offer it when the project has one),
   one from their library (`nolgia_list_assets` with `modality` audio), or
   none (default). Should it be trimmed to the length of the cut? (Default:
   yes.)
5. Images, if the project has any, come in at Resolve's standard still
   length (Project Settings > Editing > Standard still duration). Keep that,
   or set a length in seconds? (Default: keep it.)

## Build

1. **Take stock without changing anything.** One `nolgia_app_run` that lists
   every timeline in the project (`project.GetTimelineCount()`,
   `project.GetTimelineByIndex(i)`, their names and `GetStartFrame()` to
   `GetEndFrame()`) and the root bins (`media_pool.GetRootFolder()`,
   `GetSubFolderList()`, names and clip counts). Keep this list: at the end
   you compare against it. If a timeline or a root bin already carries the
   project's name, ask whether to use the bin or make `<name> 2`; a timeline
   name must be new (`CreateEmptyTimeline` answers None for a name in use).
2. **Import the clips into the bin.** `nolgia_app_import` with
   `project_id: "<NOLGIA project id>"` and `bin: "<project name>"`, no
   `append`. It brings every ready video, image and audio asset of the project
   in, oldest first, into that bin (made at the root of the media pool when
   it is not there yet), and answers `imported` (names, in order), `assets`
   (asset id, name and kind for each) and `skipped` (what Resolve does not
   import, such as 3D models). For an order the person gave, or a project of
   more than 200 assets, use `asset_ids` with the ids in that order instead.
   Keep the `assets` list: it is the order of the cut.
3. **Read the clips.** One `nolgia_app_run` over the bin's clips
   (`bin.GetClipList()`, matched to the imported names with `GetName()`):
   `GetClipProperty()` gives `Type` (`Video`, `Video + Audio`, `Audio`, or a
   still), `FPS`, `Resolution`, `Frames` and `Duration`. Work out the frame
   size and the frame rate most of the picture clips share, and list the
   ones that differ. Say the plan in two lines (size, rate, number of clips,
   music) and wait for a yes unless they said "go".
4. **Make the timeline, before any clip is on it.** One `nolgia_app_run`:
   `tl = media_pool.CreateEmptyTimeline(name)` (None means the name is in
   use: stop and ask), then
   `tl.SetSettings({"useCustomSettings": "1", "timelineResolutionWidth": "1080", "timelineResolutionHeight": "1920", "timelineFrameRate": "24"})`
   with the size and rate you chose (strings; `"29.97 DF"` for drop frame),
   then `project.SetCurrentTimeline(tl)`. `SetSettings` answers False when a
   key failed, with the keys in the error: report it rather than carrying on.
   Read `tl.GetSettings()` back and put the width, height and frame rate in
   `result`, and check `project.GetCurrentTimeline().GetName()` is the new
   timeline.
5. **Lay the clips down in order.** One `nolgia_app_run`:
   `media_pool.AppendToTimeline([{"mediaPoolItem": clip} for clip in ordered])`
   with the picture clips in the order of step 2 (audio clips stay in the
   bin for step 6). It answers the timeline items it added: check the count
   and read `GetStart()` and `GetEnd()` of the first and last. For a still
   length the person asked for, give each image `"startFrame": 0` and
   `"endFrame": seconds * fps` in its entry. The default track is V1, with
   each clip's own sound on A1.
6. **Music (if asked).** Bring the audio clip in (it is in the bin from step
   2, or `nolgia_app_import` with its `asset_id` and the same `bin`), then one
   `nolgia_app_run`: add a track with `tl.AddTrack("audio", "stereo")` and
   read `tl.GetTrackCount("audio")` for its index, then
   `media_pool.AppendToTimeline([{"mediaPoolItem": music, "mediaType": 2, "trackIndex": index, "recordFrame": tl.GetStartFrame(), "startFrame": 0, "endFrame": length}])`
   where `length` is the cut's frames (`tl.GetEndFrame() - tl.GetStartFrame()`),
   no more than the clip's own `Frames`. Read the new item's `GetStart()`: it
   must equal `tl.GetStartFrame()`. If it landed elsewhere, remove only that
   item (`tl.DeleteClips([item])`) and try `"recordFrame": 0`; the API cannot
   move an item once placed. Say that the music is laid under the cut, not
   mixed: levels are theirs to set in the Fairlight page.
7. **Look at it.** `nolgia_app_preview` with `frame` 0, and one in the middle
   of the cut. The picture must fill the frame (a vertical clip on a vertical
   timeline shows no black at the sides); if it does not, read
   `tl.GetSettings()["timelineInputResMismatchBehavior"]` and say what Resolve
   is doing with the clips that differ.
8. **Offer a draft render.** Ask first. `nolgia_app_export` `mp4` renders the
   whole timeline (H.264 at the timeline's size, with its audio) into the
   person's NOLGIA library and answers the asset id; follow a `command_id`
   with `nolgia_app_command`. For a long cut offer a range (`frames` such as
   `0-239`) first.

## Check

- `nolgia_app_info`: the new timeline is current, its frame size and frame
  rate are the ones agreed, V1 holds as many clips as pictures were imported,
  the music (if any) starts on the first frame of its track and ends with the
  cut, and the bin holds every imported clip.
- Compare with step 1: every timeline the person had is still there with the
  same name, start and end frame, and every root bin still has its clip
  count. Nothing of theirs changed.
- The first and middle frames look right: in order, the right way up, filling
  the frame.

## Deliver

Save the project. Export the draft `mp4` if they asked, else a `png` of the
first frame, so they have it in their NOLGIA library. Tell the person: the bin
and timeline names, the frame size and rate, the order of the clips (and
which were scaled), which track holds the music, how to reorder clips (drag
them in the Edit page; the timeline is an ordinary timeline), and the path of
the safety copy.

What this workflow never does: it never edits, renames, re-times or deletes
the person's existing timelines, bins or clips; it only ever adds a bin, a
timeline and the clips in them, and it never renders without asking.
