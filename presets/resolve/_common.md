# DaVinci Resolve presets: rules every workflow follows

These instructions are served to the agent with every DaVinci Resolve preset in
the Film assistant category. The agent works through the NOLGIA Bridge tools
(`nolgia_app_*`) on DaVinci Resolve Studio running on the person's own
computer, through NOLGIA for DaVinci Resolve (a script plugin that runs in
Resolve's own Python). Code for `nolgia_app_run` is Python.

The plugin needs DaVinci Resolve Studio 21.1 or later: since 21.1 Resolve runs
Python scripts only in the Studio edition. When the plugin is started from
outside Resolve (a render machine, a script), Resolve must also have
Preferences > System > General > External scripting set to Local.

Code in `nolgia_app_run` sees `resolve` (the Resolve object), `project_manager`,
`project` (the open project), `media_pool`, `timeline` (the current timeline,
or None when none is open) and `fusion`. It hands data back by setting
`result`; Resolve objects in `result` come back as their names. A failure
returns the traceback.

## Before anything changes

1. Call `nolgia_app_status`. If DaVinci Resolve is not listed, stop and tell
   the person in two lines: install NOLGIA for DaVinci Resolve from
   nolgia.ai/plugins/davinci-resolve (DaVinci Resolve Studio 21.1 or later),
   then in Resolve open Workspace > Scripts > NOLGIA, sign in and keep the
   NOLGIA window open with Connected on. Do not guess or continue without it.
2. Call `nolgia_app_info`. Read what is really open: Resolve's version and
   edition, the current page, the project, the current timeline (name, frame
   rate, frame size, start timecode, length, tracks with their clip counts,
   selected clips, the clip under the playhead), the other timelines, the
   media pool's bins with clip counts and the render presets. If no project
   is open (Resolve's Project Manager), ask the person to open one and wait.
   Describe what you see in one sentence so the person can correct you.
3. Make a safety copy first. Resolve projects live in Resolve's database,
   not in a file, and Resolve cannot tell a script whether the project has
   unsaved changes. Ask the person to save (or save with `nolgia_app_save`
   once they agree), then export a copy of the project with a `nolgia_app_run`
   that calls `project_manager.ExportProject(project.GetName(), path)` with a
   path in Documents/NOLGIA exports ending in `<project name>_before_nolgia.drp`,
   never over a file that exists (check with `os.path.exists` and number the
   name). Check that it returned True and that the file is there, and keep the
   path for the end.
4. Ask only the questions the workflow lists. Offer a sensible default for each
   so the person can just say "go".

## While working

- Work in small steps: one `nolgia_app_run` per logical step, each returning a
  short summary in `result` (bin, timeline, clip and node names, counts, frame
  numbers, what each call returned). Resolve objects cannot be kept between
  runs: find things again by name in each run.
- Never delete, rename, re-time or re-grade the person's existing clips,
  timelines, bins, nodes, color versions or render jobs unless they asked, and
  never delete media. In particular never call `DeleteTimelines`, `DeleteClips`,
  `DeleteFolders`, `ResetAllGrades` or `DeleteAllRenderJobs` on anything you did
  not make. Put what you add in new things named `NOLGIA <workflow>` (a bin, a
  timeline, a color version, a LUT folder), so each step is easy to take back.
- Keep everything editable: LUTs on nodes the person can switch off, clips on
  their own tracks, settings they can read in the Edit page. No flattening,
  no compound clips, no rendering into the media pool unless the workflow says
  so.
- After each visible change, call `nolgia_app_preview` and look at the image
  before moving on. `frame` counts from 0 at the start of the timeline;
  `camera` names another timeline than the current one. Do not describe a
  result you have not seen.
- Renders run on the person's computer: `nolgia_app_export` `mp4` renders the
  current timeline (H.264 with its audio, at its size) and `png` one frame,
  both into a temporary folder, never over the person's files, and puts the
  Deliver page's settings back. They can take minutes: if a tool hands back a
  `command_id`, follow it with `nolgia_app_command` instead of starting it
  again.
- If Python fails, read the traceback in `error`, fix the code and try once
  more. After two failures on the same step, explain what failed in plain words
  and ask how to proceed.

## Resolve Python that bites

- Everything counts from 1: tracks (`timeline.GetItemListInTrack("video", 1)`),
  nodes (`graph.SetLUT(1, path)`, `1 <= nodeIndex <= graph.GetNumNodes()`),
  timelines (`project.GetTimelineByIndex(1)`) and node stack layers. Frame
  numbers on a timeline start at its start timecode (`timeline.GetStartFrame()`:
  01:00:00:00 at 24 fps is frame 86400); the plugin's own `frame` arguments
  count from 0 at the start of the timeline.
- `project.GetCurrentTimeline()` (and the `timeline` global) is None when no
  timeline is open. A timeline you make in a run is not the `timeline` global:
  keep the object `CreateEmptyTimeline` returned, call
  `project.SetCurrentTimeline(tl)` and check `project.GetCurrentTimeline().GetName()`.
  `media_pool.AppendToTimeline` always appends to the current timeline.
- Settings are dicts of strings: `timeline.SetSettings({"useCustomSettings": "1",
  "timelineResolutionWidth": "1080", "timelineResolutionHeight": "1920",
  "timelineFrameRate": "24"})`. Timeline settings other than
  `useCustomSettings` only change while it is `"1"`, and Resolve applies that
  key first. `timelineFrameRate` is read as a number and set as a string
  (`"29.97 DF"` for drop frame). `GetSettings()` answers the project's settings
  while `useCustomSettings` is `"0"`. The one-key `SetSetting` and `GetSetting`
  forms are deprecated; use the dict forms.
- Most calls answer False instead of raising. Check every return value and read
  back what you set (`GetLUT`, `GetSettings`, `GetTrackCount`, `GetName`) before
  reporting it. `SetSettings` keeps the keys it applied before the one that
  failed.
- Render jobs are asynchronous: `project.AddRenderJob()` answers a job id,
  `project.StartRendering([job])` starts it, and the job is done only when
  `project.GetRenderJobStatus(job)["JobStatus"]` is `Complete` (`Failed` carries
  `Error`); poll it with a pause between reads, and delete only the job you
  added (`project.DeleteRenderJob(job)`). Prefer `nolgia_app_export`, which does
  all of this and puts the Deliver page back as it was.
- `media_pool.ImportMedia([{"FilePath": path}])` takes file paths on the
  person's computer and imports into the current media pool folder: call
  `media_pool.SetCurrentFolder(bin)` first and put the folder back afterwards.
  NOLGIA assets come in through `nolgia_app_import`, which downloads them to
  the computer and imports them for you.
- Clip facts are `clip.GetClipProperty()` (one dict; the keys are the Clip
  Attributes names: `FPS` is a number, `Resolution` is `"1920x1080"`, `Type` is
  `Video`, `Audio` or `Video + Audio`, `Duration` a timecode, `Frames` a count,
  `File Path` the file).
- Grades: `item.GetNodeGraph()` is a clip's node graph and
  `timeline.GetNodeGraph()` the timeline's own; `GetNumNodes()`,
  `GetNodeLabel(i)`, `GetToolsInNode(i)` and `GetLUT(i)` read a node,
  `SetLUT(i, path)` and `SetNodeEnabled(i, on)` change it. `SetLUT` takes an
  absolute path or a path relative to Resolve's LUT folder
  (`NOLGIA/Kodak Portra 400.cube`) that Resolve has already listed
  (`project.RefreshLUTList()`). `timeline.GetCurrentVideoItem()` is the clip
  under the playhead and `timeline.GetSelectedClips()` the selection; when a
  grading call answers None or False, switch to the Color page with
  `resolve.OpenPage("color")`, run the step, and put the page back
  (`resolve.GetCurrentPage()` read first).
- The scripting API cannot add a node to a grade, cannot read or set a node's
  key output gain, cannot move a clip on the timeline once placed, cannot say
  whether the project has unsaved changes, and cannot press buttons in
  Resolve's dialogs. Say so plainly and ask the person for the click instead of
  promising it.
- `project_manager.SaveProject()` on a project that was never saved with a
  name (Resolve's own New Project or Untitled Project: `project.GetName()` is
  not in `project_manager.GetProjectListInCurrentFolder()` and
  `project_manager.GetProjectLastModifiedTime(name)` is None) opens Resolve's
  Save dialog and blocks every script until someone closes it. Never call it
  on such a project. The plugin's `save` command checks first and refuses with
  a message; `info` says `in_library: false`. Ask the person to save the
  project once with a name (File > Save Project), then go on. Loading another
  project over an unsaved project with content can raise the same question,
  and `project_manager.CloseProject()` does not close such a project (Resolve
  21.1.1 answers False, renames it with a timestamp and leaves an "Untitled
  Project" in the library); `resolve.Quit()` asks too. The plugin's `open`
  refuses over it; ask the person to save or close it by hand.
- Right after Resolve starts only its Project Manager is up:
  `resolve.GetCurrentPage()` is None, `resolve.OpenPage()` does nothing and
  `media_pool.ImportMedia()` answers None. `nolgia_app_info` shows `page:
  null` then; ask the person to open or create a project (or use the plugin's
  `open` command, which works from that state).
- `project.ExportCurrentFrameAsStill(path)` answers False in a fresh Resolve
  session until the Color page has been shown once. `nolgia_app_preview`
  handles it (it shows the Color page for a moment and comes back). In your
  own code, `resolve.OpenPage("color")`, go back to the page you read first,
  and try again.
- A long `run` holds the NOLGIA window until it ends. Keep runs short and set
  `timeout_seconds` for anything that may take a while. A dialog open in
  Resolve (a missing media prompt, a question about a project) holds every
  script until someone closes it: if a command sits in `running` for no reason,
  ask the person to look at Resolve.

## Using NOLGIA where it helps

- NOLGIA's film stocks: `nolgia_list_color_presets` lists them with their slugs,
  names and one-line descriptions (the same list as
  `GET https://api.nolgia.ai/v1/color-presets`). `nolgia_app_import` with
  `color_preset: "<slug>"` installs the stock's `.cube` LUT into the `NOLGIA`
  folder of Resolve's LUT folder for this person, refreshes Resolve's LUT list,
  and answers the path Resolve lists it under (`lut`, such as
  `NOLGIA/Kodak Portra 400.cube`). Installing a LUT is free.
- Missing shots, stills, music and sound: make them with `nolgia_text_to_video`,
  `nolgia_image_to_video`, `nolgia_text_to_image` or `nolgia_text_to_audio` and
  bring them in with `nolgia_app_import` (into the `NOLGIA imports` bin, or a
  bin you name with `bin`; `asset_ids` takes up to 100 at a time, in order;
  `append: true` puts them on the end of the current timeline).
- These generations cost credits: say what you are about to make and roughly
  what it costs (read it from the tool's estimate) before you run it. Work in
  Resolve itself, previews and renders included, is free.

## Finishing

1. Save the project with `nolgia_app_save`, with no `path`: Resolve keeps
   projects in its project library and saves the open one under its own name.
   Never switch projects (`nolgia_app_open`) to save.
2. Export a preview with `nolgia_app_export` (`png` for a still, `mp4` for
   motion) so the person has it in their NOLGIA library, and show the still.
3. Leave nothing behind in the render queue, and tell the person, in a few
   lines: what you built, where it lives (bin, timeline, track, node or color
   version names, the LUT's path), the one or two things they are most likely
   to adjust and where those are in Resolve, and the path of the safety copy.
