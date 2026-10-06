# NOLGIA plugins

NOLGIA's own plugins for the apps you already work in. A plugin connects the
app on your computer to NOLGIA, so your agent (Claude, Cursor, the NOLGIA
Agent and others, through NOLGIA's MCP server) can look at your project, make
changes in it, render previews and move files between the app and your
NOLGIA library.

```
your agent --MCP--> mcp.nolgia.ai --> api.nolgia.ai <--(the plugin connects out)-- the app on your computer
```

The plugin always connects out to NOLGIA; nothing connects in to your
computer. It only takes commands while you are signed in and have it switched
on, and it lists every command it runs.

One folder per app. Each folder has its own license.

| Folder | App | License |
|---|---|---|
| [`blender/`](blender/) | Blender 4.2 and newer | GPL-3.0-or-later ([`blender/LICENSE`](blender/LICENSE)) |
| [`adobe/`](adobe/) | After Effects 2025, Premiere Pro 2025 and Illustrator 2025, and newer | GPL-3.0-or-later ([`adobe/LICENSE`](adobe/LICENSE)); IBM Plex Sans under the SIL Open Font License ([`adobe/fonts/OFL.txt`](adobe/fonts/OFL.txt)) |
| [`presets/`](presets/) | The Film assistant workflows for each app, and the rules each app's workflows share | GPL-3.0-or-later |
| [`photoshop/`](photoshop/) | Photoshop 2024 (25.0) and newer | GPL-3.0-or-later ([`photoshop/LICENSE`](photoshop/LICENSE)) |
| [`resolve/`](resolve/) | DaVinci Resolve Studio 21.1 and newer | GPL-3.0-or-later ([`resolve/LICENSE`](resolve/LICENSE)) |
| [`tools/`](tools/) | Test tools shared by the plugins | GPL-3.0-or-later |

## NOLGIA for Blender

### Install and use

1. Get `nolgia-blender-<version>.zip` from [nolgia.ai/plugins/blender](https://nolgia.ai/plugins/blender)
   (or the [releases](https://github.com/nolgiainc/nolgia-plugins/releases) here).
2. In Blender, open Edit > Preferences > Get Extensions, click the small arrow
   at the top right, choose Install from Disk and pick the zip. (Dragging the
   zip into Blender works too.)
3. In the 3D Viewport, press N and open the NOLGIA tab.
4. Click Sign in. Your browser opens a NOLGIA page; check that the code
   matches the one in Blender and approve it.
5. Keep Connected on and ask your agent to work in Blender. Each command shows
   up in the Activity list. Click Pause (or turn Connected off) to stop at any
   time.

Settings in the NOLGIA tab (also in the add-on's preferences):

- **Connected**: NOLGIA can send commands to this Blender only while this is on.
- **Allow NOLGIA Agent**: also let your NOLGIA Agent in the cloud work here, not
  only the agent apps you run yourself.
- **Ask before running code**: show every piece of Python NOLGIA wants to run
  and wait for you to click Approve or Deny.
- **Sign out**: disconnect and forget the sign in on this computer.

Python that NOLGIA runs in Blender runs on your computer with your
permissions, like any add-on or script. Turn on Ask before running code if you
want to see it first.

### What your agent can do

| Command | Arguments | Result |
|---|---|---|
| `info` | none | open file (name, path, unsaved changes), scene, frame range, fps, render engine and size, collections with object counts, selected and active object, cameras |
| `run` | `language: "python"`, `code`, `timeout_seconds?` | `{value, stdout, stderr}`; `value` is whatever the code puts in `result` (the code sees `bpy`, `mathutils`, `C`, `D`). A failure returns the traceback. |
| `preview` | `camera?`, `frame?`, `width?` (16 to 1920), `engine?` (`current`, `eevee`, `workbench`, `cycles`) | renders a still and uploads it: `{asset_id, width, height, mime_type, camera, frame, engine}`. A PNG, or a JPEG when the PNG would be too big for your agent to see inline (about 3.6 MB). |
| `import_asset` | `asset_id`, `as?` (`plane`, `texture`, `clip`) | brings a NOLGIA asset into the scene: GLB models as objects, images as a plane (or a texture with `as: "texture"`), video as a movie clip (or a plane), audio as a sound: `{imported: [names], kind}` |
| `export` | `format` (`png`, `mp4`, `glb`, `blend`), `frames?` (`"24"` or `"1-120"`), `filename?`, `selected_only?` (glb) | renders a still or an H.264 video at the scene's settings, or exports a GLB, and uploads it: `{asset_id, format, filename}`. `blend` saves a copy on this computer instead (next to the open file, or in Documents/NOLGIA exports while it is unsaved) and never overwrites a file: `{asset_id: null, path, note}`. Blender files are not uploaded to NOLGIA. |
| `save` | `path?` | saves the file; without `path` only to the file that is already open: `{path}` |
| `open` | `path` | opens a .blend file; asks you first when the open file has unsaved changes: `{path}` |

### Blender without a window (render machines, scripts, tests)

```sh
NOLGIA_TOKEN=nol_... blender --background shot.blend --python-expr \
  "import addon_utils; addon_utils.enable('bl_ext.user_default.nolgia', default_set=True); \
   import bl_ext.user_default.nolgia as nolgia; nolgia.serve()"
```

`serve()` connects, runs commands until the plugin is switched off (or Ctrl+C),
and returns True after a normal stop. Without a window nobody can approve
code, so the plugin refuses to connect while Ask before running code is on.

| Variable | Effect |
|---|---|
| `NOLGIA_TOKEN` | use this token instead of signing in (never saved) |
| `NOLGIA_API_URL` | API to talk to (default `https://api.nolgia.ai/v1`) |
| `NOLGIA_BRIDGE_AUTOCONNECT=1` | connect as soon as Blender starts (with a window) |
| `NOLGIA_ASK_BEFORE_RUN=0/1` | set Ask before running code |
| `NOLGIA_ALLOW_AGENT=0/1` | set Allow NOLGIA Agent |
| `NOLGIA_INSTANCE_ID` | fixed session id for this Blender (render farms running many Blenders) |
| `NOLGIA_EXPORT_DIR` | where `export` `blend` puts copies of unsaved files |

### Development

Everything is standard library Python. The parts that do not need Blender
live in [`blender/core/`](blender/core/) and have unit tests that run with
plain Python 3.10 or newer:

```sh
python3 -m unittest discover -s blender/tests -p 'test_*.py'
```

The end-to-end test builds the extension, installs it into a throwaway Blender
user folder (your own settings are not touched), starts the mock API from
[`tools/mock_bridge_server.py`](tools/mock_bridge_server.py), runs Blender
without a window and drives every command through it:

```sh
python3 blender/tests/e2e_blender.py --blender /path/to/blender
```

From WSL it works with a Windows `blender.exe` too (paths go through
`wslpath`, variables through `WSLENV`).

Build the installable zip and check it. Releases are tagged `blender-v<version>`
with the zip attached as `nolgia-blender-<version>.zip`; the website links to
that exact name.

```sh
blender --command extension build --source-dir blender --output-filepath blender/dist/nolgia-blender-0.1.0.zip
blender --command extension validate blender/dist/nolgia-blender-0.1.0.zip
```

Run the mock API on its own:

```sh
python3 tools/mock_bridge_server.py --port 8765 --token test-token --auto-approve-after 1
```

The wire format is the NOLGIA API's `/bridge/*` endpoints (`api/openapi.yaml`,
`internal/handlers/bridge.go`); the mock follows the same rules.

## NOLGIA for After Effects, Premiere Pro and Illustrator

One extension for all three apps (a CEP extension, `adobe/`). It has two
parts: an invisible part that starts with the app and keeps the connection to
NOLGIA, and the NOLGIA panel, which shows what it is doing and holds the
switches. Closing the panel does not disconnect.

### Install and use

1. Get `nolgia-adobe-<version>.zxp` from [nolgia.ai/plugins](https://nolgia.ai/plugins)
   (or the [releases](https://github.com/nolgiainc/nolgia-plugins/releases) here).
2. Quit After Effects, Premiere Pro and Illustrator, then install it with
   Adobe's own installer. On Windows:

   ```bat
   "C:\Program Files\Common Files\Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe" /install nolgia-adobe-0.1.0.zxp
   ```

   On macOS the same tool is
   `/Library/Application Support/Adobe/Adobe Desktop Common/RemoteComponents/UPI/UnifiedPluginInstallerAgent/UnifiedPluginInstallerAgent.app/Contents/MacOS/UnifiedPluginInstallerAgent --install nolgia-adobe-0.1.0.zxp`.
   The installer skips a version that is already installed: to reinstall the
   same version, remove it first (`/remove NOLGIA`). The package is signed, so
   no debug setting is needed.
3. Start the app and open Window > Extensions > NOLGIA.
4. Click Sign in. Your browser opens a NOLGIA page; check that the code
   matches the one in the panel and approve it. Each app signs in on its own.
5. Keep Connected on and ask your agent to work in the app. Each command shows
   up in the Activity list. Click Pause (or turn Connected off) to stop at any
   time.

Settings in the panel:

- **Connected**: NOLGIA can send commands to this app only while this is on.
  It stays on the next time the app starts.
- **Allow NOLGIA Agent**: also let your NOLGIA Agent in the cloud work here, not
  only the agent apps you run yourself.
- **Ask before running code**: show every script NOLGIA wants to run in the
  panel (it opens by itself) and wait for you to click Run code or Deny.
- **Sign out**: disconnect and forget the sign in on this computer.

The sign in and the switches are kept per app in
`%APPDATA%\NOLGIA\adobe\<app>\settings.json` (on macOS
`~/Library/Application Support/NOLGIA/adobe/<app>/settings.json`), readable only
by you, next to a log of what the plugin did (`nolgia.log`, never the token).

ExtendScript that NOLGIA runs runs on your computer with your permissions, like
any script. Turn on Ask before running code if you want to see it first.

### What your agent can do

Every app takes the seven command kinds. Frames count from 0 at the start of
the comp or sequence; artboards count from 1.

| Command | After Effects | Premiere Pro | Illustrator |
|---|---|---|---|
| `info` | project (file, unsaved changes, items by kind, bit depth), active comp (size, frame rate, frames, current frame, work area, layers with type, effects and selection), all comps, selected items, render queue | project (file), bins with counts, sequences (size, frame rate, length, tracks), active sequence (playhead, in and out, tracks, selected clips), selected items | open documents, the active one's colour mode, size, artboards, layers and sublayers, selection, counts of paths, groups, images and text |
| `run` | ExtendScript: `{value, stdout, stderr}` (see below) | same | same |
| `preview` | `comp` (or `camera`) name, `frame`, `width`: a still of a comp or of a footage item | `sequence` (or `camera`), `frame`, `width`: a frame of the sequence | `artboard` (name or number, or `camera`), `width`: the artboard |
| `import_asset` | footage into the `NOLGIA imports` folder; `as: "layer"` also adds it to the active comp | media into the `NOLGIA imports` bin | images embedded (or `as: "link"`) and SVG, PDF or AI as groups, on a `NOLGIA imports` layer; with nothing open, the image opens as a document |
| `export` | `mp4` (render queue, H.264), `png` (a frame), `aep` | `mp4` (H.264 Match Source), `png` (a frame), `prproj` | `png` (the artboard, at least 2048 px), `svg`, `pdf`, `ai` |
| `save` | `path?` | `path?` | `path?` |
| `open` | `.aep` (asks you first over unsaved changes) | `.prproj` (opens next to the open one) | any file Illustrator opens (in its own window) |

Previews are uploaded to your NOLGIA library as a PNG, or a JPEG when the PNG
would be too big for your agent to see inline (about 3.6 MB): `{asset_id,
width, height, mime_type, comp|sequence|artboard, frame}`. `mp4` and `png`
exports are uploaded too: `{asset_id, format, filename, frames}`. Project files
(`aep`, `prproj`, `ai`) stay on your computer: exporting one copies the project
as last saved next to it, never over a file (a project that was never saved is
saved into Documents/NOLGIA exports). NOLGIA's library does not take SVG or PDF
yet, so those exports stay on your computer too, next to the document:
`{asset_id: null, path, note}`. Imported files that the app needs on disk go
into `nolgia_assets` next to the project, or Documents/NOLGIA imports while it
is unsaved; WebP images are converted to PNG, which the apps read.

**`run`** evaluates ExtendScript in the app. The code sees `print()`, `log()`,
`console.log/info/warn/error` and `$.writeln` (captured as stdout; warn and error
as stderr), and `alert()` writes to stderr instead of opening a dialog. `value`
is what the code puts in `result`, else the value of its last statement, or
what it returns when it uses `return` at the top level. It is turned into JSON:
app objects become their names, files their paths, dates ISO text. A failure
returns the error with its line number and the lines around it. After Effects
groups each run into one undo step. ExtendScript cannot be stopped from
outside: when code outlives its time, NOLGIA stops waiting and says so, and the
app finishes it on its own.

### For scripts, render machines and tests

| Variable | Effect |
|---|---|
| `NOLGIA_TOKEN` | use this token instead of signing in (never saved) |
| `NOLGIA_API_URL` | API to talk to (default `https://api.nolgia.ai/v1`) |
| `NOLGIA_BRIDGE_AUTOCONNECT=1` | connect as soon as the app starts |
| `NOLGIA_ASK_BEFORE_RUN=0/1` | set Ask before running code |
| `NOLGIA_ALLOW_AGENT=0/1` | set Allow NOLGIA Agent |
| `NOLGIA_INSTANCE_ID` | fixed session id for this app |
| `NOLGIA_EXPORT_DIR` | where `aep`, `prproj` and `ai` copies of unsaved projects go |
| `NOLGIA_PREVIEW_MAX_BYTES` | the size above which previews become JPEG |

Apps started from the Creative Cloud app or a launcher often do not get your
environment, so the same keys can go in a file instead:
`%APPDATA%\NOLGIA\adobe\env.json` (macOS
`~/Library/Application Support/NOLGIA/adobe/env.json`), for example
`{"NOLGIA_TOKEN": "nol_...", "NOLGIA_BRIDGE_AUTOCONNECT": "1"}`. The
environment wins over the file.

### Development

The parts that do not need an Adobe app live in [`adobe/core/`](adobe/core/)
(Node, no dependencies, CommonJS for the Node 17 inside CEP 12) and have unit
tests that run with Node 18 or newer against the same mock API the Blender
tests use:

```sh
cd adobe && npm test          # node --test "tests/*.test.js"
```

[`adobe/host/`](adobe/host/) is the ExtendScript: `nolgia.jsx` (JSON, `run`,
the call entry point) and one file per app (`aeft.jsx`, `ppro.jsx`,
`ilst.jsx`). It knows nothing about CEP, so a later UXP port can keep the app
files and replace only the transport ([`adobe/js/`](adobe/js/): `service.js`,
`panel.js` and the thin `cep.js`). CEP is switched off by default from
December 2028.

Build the signed installer (Adobe's `ZXPSignCmd` from
[Adobe-CEP/CEP-Resources](https://github.com/Adobe-CEP/CEP-Resources), which
signs best on the platform the package is installed on; from WSL it runs the
Windows one). Without `--cert` it makes a self-signed certificate once and
keeps it in `adobe/dist` (ignored by git):

```sh
node adobe/tools/build.js --zxpsigncmd /path/to/ZXPSignCmd.exe
# adobe/dist/nolgia-adobe-0.1.0.zxp
```

`--debug` adds a `.debug` file that opens Chrome DevTools on ports 8092 to 8094
(the invisible part in After Effects, Premiere Pro, Illustrator) and 8095 to
8097 (the panel). Never ship that build.

The end-to-end test builds and installs the package with Adobe's installer,
points the plugin at the mock API (or production with `--api prod`) through
`env.json`, starts each app, drives every command through the caller side of
the API against its own session, checks the results and quits the app:

```sh
python3 adobe/tests/e2e_adobe.py --zxpsigncmd /path/to/ZXPSignCmd.exe
python3 adobe/tests/e2e_adobe.py --api prod --token-file ~/.config/nolgia/tokens.json --no-install
```

It runs on Windows or from WSL (Windows paths through `wslpath`), closes the
apps' own warning dialogs as they appear (their text is printed), and removes
`env.json` and the test assets it made in production when it is done.

## NOLGIA for Photoshop

A UXP plugin for Photoshop 2024 (25.0) and newer. Tested on Windows with
Photoshop 2026 (27.5); UXP plugins run on macOS too, but this one has not
been tried there yet.

### Install and use

1. Get `nolgia-photoshop-<version>.ccx` from [nolgia.ai/plugins/photoshop](https://nolgia.ai/plugins/photoshop)
   (or the [releases](https://github.com/nolgiainc/nolgia-plugins/releases) here).
2. Double-click the file. Creative Cloud installs it into Photoshop after you
   confirm a plugin from outside the Adobe Marketplace. On Windows the
   installer also runs from a command prompt:
   `"C:\Program Files\Common Files\Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe" /install nolgia-photoshop-<version>.ccx`
   (and `/remove "NOLGIA for Photoshop"` takes it out again).
3. In Photoshop, open the NOLGIA panel from the Plugins menu (under NOLGIA
   for Photoshop).
4. Click Sign in. Your browser opens a NOLGIA page; check that the code
   matches the one in the panel and approve it.
5. Keep Connected on and ask your agent to work in Photoshop. Each command
   shows up in the Activity list. Click Pause (or turn Connected off) to stop
   at any time.

Photoshop loads the plugin when it starts, so NOLGIA reconnects by itself
when you left Connected on, even with the panel closed.

Settings in the NOLGIA panel:

- **Connected**: NOLGIA can send commands to this Photoshop only while this is on.
- **Allow NOLGIA Agent**: also let your NOLGIA Agent in the cloud work here, not
  only the agent apps you run yourself.
- **Ask before running code**: show every piece of code NOLGIA wants to run, in
  a window and in the panel, and wait for you to click Run code or Deny.
- **Sign out**: disconnect and forget the sign in on this computer (the token
  is kept in UXP secure storage, encrypted by your system).

Code that NOLGIA runs in Photoshop runs on your computer with your
permissions, like any plugin or script. Turn on Ask before running code if
you want to see it first. The plugin asks Photoshop for: network access to
api.nolgia.ai and NOLGIA's file storage (storage.googleapis.com), full file
access (to open, save and export where your agent asks), opening https links
(the sign in page), the clipboard (Copy code) and running code from text
(the `run` command).

### What your agent can do

The MCP tools `nolgia_app_info`, `nolgia_app_run`, `nolgia_app_preview`,
`nolgia_app_import` and `nolgia_app_export` reach these commands; `save` and
`open` are there for callers of the API (and for code in `run`).

| Command | Arguments | Result |
|---|---|---|
| `info` | none | open documents (name, path, unsaved changes, active); for the active one: size, resolution, colour mode, bit depth, colour profile, the layer tree (id, name, kind, visibility, opacity, blend mode, layer and vector masks, bounds, locks, groups with their layers), the selected layers, the selection's bounds and the last History step |
| `run` | `language: "uxp"`, `code`, `timeout_seconds?` | `{value, stdout, stderr}`. The code is the body of an async function (so it can `await`); `value` is what it puts in `result` (or `return`s). It sees `app`, `core`, `action`, `batchPlay`, `play` (batchPlay that throws when a step fails), `constants`, `imaging`, `photoshop`, `uxp`, `fs` (the local file system), `formats`, `doc` (the active document), `modal(fn)`, `sleep(ms)`, `require` and a `console` whose output is returned. A failure returns the error with the line of your code it happened on. |
| `preview` | `width?` (16 to 1920), `region?` (`{left, top, right, bottom}` in pixels), `document?` | a flattened copy of the document (visible layers), never larger than it is, uploaded: `{asset_id, width, height, mime_type}`. A PNG, or a JPEG when the PNG would be too big for your agent to see inline (about 3.6 MB). |
| `import_asset` | `asset_id`, `as?` (`layer`, `pixels`, `document`), `name?` | brings a NOLGIA image in: by default as a Smart Object layer right above the selected layer, or as a new document when none is open. An image with the document's shape is scaled to cover the canvas exactly; anything else is centred. The selection is kept. `{imported, kind, layer_id, group, placement, bounds}` |
| `export` | `format` (`png`, `jpg`, `psd`), `filename?`, `quality?` (jpg, 1 to 12), `document?` | a flattened full-size `png` or `jpg`, uploaded: `{asset_id, format, filename, width, height}`. `psd` saves a copy on this computer instead (next to the open file, or in Documents/NOLGIA exports while it is unsaved) and never overwrites a file: `{asset_id: null, path, note}`. Photoshop files are not uploaded to NOLGIA. |
| `save` | `path?`, `document?` | saves the document; without `path` only to the file it already has; with `path` as a `.psd` or `.psb`: `{path}` |
| `open` | `path` | opens a file in a new document tab: `{path, document, id}` |

Photoshop only lets a plugin change a document inside `executeAsModal`.
Every `run` already runs inside it, as one step in Photoshop's History named
"NOLGIA: run code" (one Undo takes it back), and a run that fails is undone.
So agent code edits the document directly:

```js
const layer = await doc.layers.add({ name: "Grain" });
await play([{ _obj: "fill", using: { _enum: "fillContents", _value: "gray" },
              opacity: { _unit: "percentUnit", _value: 100 }, mode: { _enum: "blendMode", _value: "normal" } }]);
layer.blendMode = constants.BlendMode.OVERLAY;
result = { id: layer.id };
```

Use the `modal(async (ctx) => ...)` helper, not `core.executeAsModal`, if
code needs a scope of its own: an error thrown inside `executeAsModal` comes
back as bare text without a line number. The pitfalls Photoshop scripting has
(where new layers land, batchPlay not throwing, and more) are in
[`presets/photoshop/_common.md`](presets/photoshop/_common.md).

### Known limits

- Photoshop gives a plugin no time to reach the network when it quits, so
  NOLGIA only notices a closed Photoshop when it stops checking in (about a
  minute). A command sent in that minute waits until it expires.
- A long poll the plugin gave up on can stay open on NOLGIA's side for up to
  25 seconds (Google's front end keeps it), and would take the next command.
  So after Pause and Resume, or a quick restart, the plugin waits for that
  poll to run out before it connects again ("Reconnecting to NOLGIA...").
- Photoshop's Generative Fill has no scripting interface the plugin supports.

### Development

The parts that do not need Photoshop live in
[`photoshop/core/`](photoshop/core/) (plain JavaScript, no dependencies) and
have unit tests that run with Node 18 or newer against the mock API from
[`tools/mock_bridge_server.py`](tools/mock_bridge_server.py) (so Python 3.8+
too):

```sh
node --test photoshop/tests/*.test.js
```

Build the installable `.ccx` (Python 3, standard library):

```sh
python3 photoshop/build.py                    # photoshop/dist/nolgia-photoshop-0.1.0.ccx
python3 photoshop/build.py --dev-domain http://localhost:8791
                                              # a test build that may also reach a local mock API
```

Releases are tagged `photoshop-v<version>` with the file attached as
`nolgia-photoshop-<version>.ccx`.

The end-to-end test runs on Windows (from Windows or from WSL). It builds the
plugin, installs it with Adobe's installer, starts Photoshop (which must be
closed first), drives every command through the API as an agent would,
checks the results, the files and the uploaded images, signs in with the
device flow, restarts Photoshop to check the saved sign in reconnects, and
closes it again:

```sh
python3 photoshop/tests/e2e_photoshop.py                  # against the mock API
python3 photoshop/tests/e2e_photoshop.py --prod --token-file ~/.config/nolgia/tokens.json
```

`--prod` uses the real API with your token: it uploads a few small test
images to your library and signs in one more device session. Nothing costs
credits.

Scripts and tests connect without clicks through a developer file,
`nolgia-dev.json` in the plugin's data folder, read when Photoshop starts
(on Windows
`%APPDATA%\Adobe\UXP\PluginsStorage\PHSP\<Photoshop major version>\External\com.nolgia.photoshop\PluginData`;
the plugin writes the folder it uses to the UXP log at startup, in a line
starting `NOLGIA: data folder:`). The panel says when a developer file is in
use. Delete it when you are done.

| Key | Effect |
|---|---|
| `token` | use this token instead of signing in (never saved) |
| `api_url` | API to talk to (default `https://api.nolgia.ai/v1`); a saved sign in is only ever sent to the API it came from |
| `autoconnect` | connect as soon as Photoshop starts |
| `ask_before_run`, `allow_agent` | set those settings |
| `instance_id` | fixed session id for this Photoshop |
| `show_panel` | open the NOLGIA panel at startup (test builds only) |
| `export_dir` | where `export` `psd` puts copies of unsaved documents |
| `preview_max_bytes` | the size above which previews become JPEG |

The plugin writes what it does to Photoshop's UXP log
(`%APPDATA%\Adobe\Adobe Photoshop <year>\Logs\UXPLogs_*.log` on Windows),
each line starting with `NOLGIA:`.

## NOLGIA for DaVinci Resolve

A script plugin for DaVinci Resolve Studio 21.1 and newer (since 21.1 Resolve
runs Python scripts only in the Studio edition). It needs Python 3.8 or
newer and nothing outside Python's standard library. Tested on Windows; it is
written for macOS and Linux too, but has not been run there yet (see
[Tested on](#tested-on)).

Which Python runs it: Resolve runs a Scripts menu script in its own
`fuscript` program with a Python it finds on the computer. On Windows,
Resolve 21.1 took the one named in `PYTHON3HOME` (or `PYTHONHOME`) when set,
else the one in the registry (a per-user install before a machine-wide one;
`PATH` plays no part), not its own bundled Python 3.14. What it picks on a
computer with no Python installed, and on macOS, has not been checked; on a
Mac, the Python 3 that Apple's Command Line Tools install (3.9 at the time of
writing) is new enough if Resolve binds to it.

### Install and use

1. Get `nolgia-resolve-<version>.zip` from [nolgia.ai/plugins/davinci-resolve](https://nolgia.ai/plugins/davinci-resolve)
   (or the [releases](https://github.com/nolgiainc/nolgia-plugins/releases) here)
   and unzip it.
2. Quit DaVinci Resolve and run the installer: on Windows double-click
   `install.cmd`; on macOS or Linux run `sh install.sh` in Terminal, in the
   unzipped folder. It copies `NOLGIA.py` and `nolgia_resolve.zip` from the
   `Utility` folder into Resolve's Scripts folder for your user, so it needs
   no administrator rights. To do it by hand, copy those two files into:
   - Windows: `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility`
   - macOS: `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility`
   - Linux: `~/.local/share/DaVinciResolve/Fusion/Scripts/Utility`
3. Start Resolve, open the Workspace > Scripts menu and choose NOLGIA. The
   NOLGIA window opens.
4. Click Sign in. Your browser opens a NOLGIA page; check that the code
   matches the one in the NOLGIA window and approve it.
5. Keep the NOLGIA window open with Connected on, and ask your agent to work
   in Resolve. Each command shows up in the Activity list. Click Pause, turn
   Connected off or close the window to stop at any time.

NOLGIA works in Resolve only while its window is open: closing it switches
NOLGIA off. When you open it again it reconnects by itself if you left
Connected on. To remove the plugin, delete the two files.

Settings in the NOLGIA window:

- **Connected**: NOLGIA can send commands to this Resolve only while this is on.
- **Allow NOLGIA Agent**: also let your NOLGIA Agent in the cloud work here, not
  only the agent apps you run yourself.
- **Ask before running code**: show every piece of Python NOLGIA wants to run
  in a NOLGIA request window and wait for you to click Run code or Deny.
  Closing that window leaves the request waiting (Show request brings it back).
- **Sign out**: disconnect and forget the sign in on this computer.

The sign in and the switches are kept in `settings.json` in a NOLGIA folder
for your user: `%APPDATA%\NOLGIA\resolve` on Windows,
`~/Library/Application Support/NOLGIA/resolve` on macOS and
`~/.config/nolgia/resolve` on Linux. On macOS and Linux the file is readable
only by you (mode 600); on Windows it sits in your own `%APPDATA%`. Next to it
is a log of what the plugin did (`nolgia.log`, never the token). A saved sign
in is only ever sent to the API it came from.

Python that NOLGIA runs in Resolve runs on your computer with your
permissions, like any script. Turn on Ask before running code if you want to
see it first.

### What your agent can do

The full arguments and results are in
[`resolve/README.md`](resolve/README.md). Frames count from 0 at the start
of the timeline (Resolve's own API numbers frames from the start timecode,
01:00:00:00 at 24 fps being frame 86400; `info` gives both).

| Command | Arguments | Result |
|---|---|---|
| `info` | none | Resolve's version and edition, the current page, the open project, the current timeline (frame rate, size, start timecode, length, current timecode, tracks with their clip counts, selected clips, the clip under the playhead), the other timelines, the media pool's bins with clip counts, the render presets, `current_timecode`. Resolve's scripting cannot tell whether a project has unsaved changes, so `unsaved_changes` is null (and `changed_by_nolgia_since_save` says what NOLGIA did). |
| `run` | `language: "python"`, `code`, `timeout_seconds?` | `{value, stdout, stderr}`; `value` is whatever the code puts in `result` (the code sees `resolve`, `project_manager`, `project`, `media_pool`, `timeline` (the current one, or None) and `fusion`; Resolve objects come back as their names). A failure returns the traceback. |
| `preview` | `frame?` or `timecode?`, `timeline?` (or `camera`), `width?` (16 to 1920) | a still of a frame of the timeline (default the current frame of the current timeline), never wider than the timeline, uploaded: `{asset_id, width, height, mime_type, timeline, frame, timecode}`. A PNG; when that would be too big for your agent to see inline (about 3.6 MB), Resolve's own JPEG of the frame when it has the size asked for, else a PNG with fewer colour levels. |
| `import_asset` | one of `asset_id`, `asset_ids` (up to 100, in order), `project_id` (a NOLGIA project's ready video, image and audio, oldest first) or `color_preset` (a NOLGIA color preset's slug); `bin?`, `append?`, `apply_to?`, `node?` | media goes into the `NOLGIA imports` bin (or `bin`), in order, and with `append: true` onto the end of the current timeline (a new `NOLGIA timeline` when none is open): `{imported, asset_ids, assets, kind, bin, appended?}`. A `.cube` file or a color preset is installed as a LUT and, with `apply_to` (`current`, `selected`, `current_track`, `all`), set on grade node `node` (default 1): `{imported, kind: "lut", path, lut, applied_to?}`. |
| `export` | `format` (`png`, `mp4`), `frames?` (`"24"` or `"0-119"`), `filename?`, `timeline?` | renders at the timeline's size and uploads it: `png` one frame (default the current one), `mp4` H.264 with the timeline's audio (default the whole timeline): `{asset_id, format, filename, frames, width, height, timeline}`. It renders into a temporary folder, never over your files, and puts the render queue and the Deliver page's settings back. |
| `save` | none | saves the open project: `{project, saved}` |
| `open` | `project`, `folder?` | opens another project: `{project, folder}`. With the NOLGIA window open it always asks you first (Resolve cannot tell whether there are unsaved changes). |

**LUTs.** NOLGIA's film-look color presets come from the API it uses
(`GET /color-presets`, public). `import_asset` installs a LUT into a `NOLGIA`
folder in the LUT folder DaVinci Resolve reads, named after the preset or the
file (`Kodak Portra 400.cube`), and refreshes Resolve's LUT list. A color
preset replaces its own older copy; a LUT from your library never replaces
another file. The LUT folder:

- Windows: `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\LUT\NOLGIA`.
  This is Resolve's LUT folder for all users of the computer; Resolve 21.1
  did not read LUTs from a folder under `%APPDATA%`. Resolve's installer lets
  every user write there, so no administrator rights are needed.
- macOS: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/NOLGIA`
  (Resolve's documented LUT folder), or, when that cannot be written without
  administrator rights, `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/NOLGIA`.
  Not yet tried: which of the two Resolve reads on a Mac is still to be checked.
- Linux: `/opt/resolve/LUT/NOLGIA`, else `~/.local/share/DaVinciResolve/LUT/NOLGIA`
  (not yet tried).
- Anywhere: `NOLGIA_LUT_DIR`, or Resolve's own `BMD_RESOLVE_LUT_DIR`, when set.

Imported media stays on disk where Resolve can find it, in
`Documents/NOLGIA imports/<project>/<asset id>/` (or `NOLGIA_IMPORT_DIR`).
Resolve does not import 3D models.

### Without the NOLGIA window (render machines, scripts, tests)

With Resolve open (or started without its window, `Resolve -nogui`) and
Preferences > System > General > External scripting set to Local, run
the installed `NOLGIA.py` with Resolve's own Python:

```bat
rem Windows
set NOLGIA_TOKEN=nol_...
"C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolvePython\ResolvePython.exe" "%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\NOLGIA.py" --serve
```

```sh
# macOS
NOLGIA_TOKEN=nol_... "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolvePython" \
  ~/"Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/NOLGIA.py" --serve
# Linux: /opt/resolve/bin/ResolvePython ~/.local/share/DaVinciResolve/Fusion/Scripts/Utility/NOLGIA.py --serve
```

It connects, runs commands until it is switched off (`nolgia_resolve.disconnect()`
from `run` code, or Ctrl+C) or Resolve closes, and exits with 0 after a
normal stop and 3 when it could not connect or NOLGIA stopped accepting the
sign in. Without a window nobody can approve code, so it refuses to connect
while Ask before running code is on, and refuses `open` after NOLGIA changed
the project since it was last saved.

| Variable | Effect |
|---|---|
| `NOLGIA_TOKEN` | use this token instead of signing in (never saved) |
| `NOLGIA_API_URL` | API to talk to (default `https://api.nolgia.ai/v1`) |
| `NOLGIA_BRIDGE_AUTOCONNECT=1` | connect as soon as the NOLGIA window opens |
| `NOLGIA_ASK_BEFORE_RUN=0/1` | set Ask before running code (for this run; not saved) |
| `NOLGIA_ALLOW_AGENT=0/1` | set Allow NOLGIA Agent (for this run; not saved) |
| `NOLGIA_INSTANCE_ID` | fixed session id for this Resolve |
| `NOLGIA_CONFIG_DIR` | where the settings, sign in and log are kept |
| `NOLGIA_IMPORT_DIR` | where imported media is kept |
| `NOLGIA_LUT_DIR` | the LUT folder to install into (Resolve must read it) |
| `NOLGIA_PREVIEW_MAX_BYTES` | the size above which previews are made smaller |

### Known limits

- Resolve's scripting cannot tell whether a project has unsaved changes, so
  `info` says `unsaved_changes: null`, and `open` asks you first.
- Commands run one at a time on the NOLGIA script's own thread, the only one
  that calls Resolve. A long `run` holds the NOLGIA window until it ends; a
  render does not (the plugin checks on it between clicks).
- Rendering opens Resolve's Deliver page and moves the playhead, and about
  half a second after Resolve reports the render complete it moves the
  playhead to the start once more; the plugin waits a second, then puts the
  page and the playhead back. Adding clips to a timeline leaves Resolve's
  playhead at its end. On the Media and Fusion pages Resolve has no timeline
  playhead for scripts, so `preview`, a `png` export and `apply_to:
  "current"` switch to the Edit page for a moment and back. Resolve 21.1.1
  sometimes answers a playhead move with True without moving (right after a
  timeline is made), so the plugin reads the playhead back and sets it again.
- `append` adds each clip after the timeline's last clip, whatever its
  track, so audio and pictures follow one another rather than overlap.
- Resolve runs a Scripts menu script in its own `fuscript` program, with the
  Python it finds on the computer (on the Windows test machine that was an
  installed Python 3.11, not Resolve's own 3.14). The plugin needs Python
  3.8 or newer: its unit tests pass on 3.8, 3.9, 3.10 and 3.11, and it ran
  under 3.11 inside Resolve and under Resolve's own 3.14 with `--serve`.
- Resolve finds new scripts when it starts, so restart it after installing or
  updating.

### Tested on

Windows 11 with DaVinci Resolve Studio 21.1.1 (build 10), on October 5,
2026, against the mock API:

- headless (`NOLGIA.py --serve` under Resolve's own Python 3.14, Resolve open
  with External scripting set to Local): every command, in a throwaway
  project (`resolve/tests/e2e_resolve.py`, 31 checks);
- in the app: installed with `install.cmd`, the NOLGIA window run inside
  Resolve the way the Scripts menu runs it, commands run from the window,
  the request window's Run code button, closing the window
  (`resolve/tests/inapp_resolve.py`, 10 checks). Choosing NOLGIA from the
  menu by hand and the look of the window have not been checked yet.

And against the real NOLGIA API (api.nolgia.ai) the same day, in a
throwaway project, headless and in the app: `info`, `run`, `preview`,
`import_asset` of library assets, of a list of them and of the
`kodak-portra-400` color preset applied to clips, `export` of a frame and
of a two second H.264 MP4, `save`, `open`; `status`, `info`, `run`,
`preview` and `export` went through NOLGIA's MCP server (mcp.nolgia.ai) as
an agent's would, the rest through `POST /bridge/commands`
(`resolve/tests/e2e_resolve.py --api prod`, 30 checks;
`resolve/tests/inapp_resolve.py --api prod`, 11 checks). Every asset the
runs made was deleted afterwards.

Not yet run on macOS or Linux.

### Development

The plugin is standard library Python in
[`resolve/nolgia_resolve/`](resolve/nolgia_resolve/). Its `core/` is a copy
of [`blender/core/`](blender/core/), kept the same by
[`resolve/tests/test_core_copy.py`](resolve/tests/test_core_copy.py), which
lists the few places they may differ (the app's constants, the argument
checks, the app's name in messages): copy a fix made in one to the other.
The unit tests run with plain Python 3.8 or newer against stand-ins for
Resolve and its UIManager (which follow what Resolve 21.1.1 was seen to do)
and the mock API, including the macOS and Linux paths and installer with the
platform patched:

```sh
python3 -m unittest discover -s resolve/tests -p 'test_*.py'
```

On threads: every Resolve call is made from the script's main thread, one
command at a time (the network threads never call Resolve). Simple read
calls from a background Python thread did work in Resolve 21.1.1, both from
ResolvePython and inside the app, but nothing documents the API as thread
safe. Inside the app, UIManager's `RunLoop()` holds Python's lock most of the
time, so the window's timer (every 50 ms, delivered to the dispatcher's
`On.Timeout`) sleeps a few milliseconds each tick to let the network threads
run.

The end-to-end test builds the download, starts Resolve without its window
(or uses the open one with `--use-running`), makes a throwaway project, runs
`NOLGIA.py --serve` under Resolve's own Python against the mock API, drives
every command through it, then deletes the project and the LUTs it
installed. The in-app test installs the download with its installer and runs
the NOLGIA window inside the open Resolve (through Fusion's `RunScript`, as
the Scripts menu does) against the mock API. Both need External scripting
set to Local. From WSL they use the Windows Resolve (paths through
`wslpath`, variables through `WSLENV`):

```sh
python3 resolve/tests/e2e_resolve.py
python3 resolve/tests/inapp_resolve.py
```

Build the download (Python 3, standard library). Releases are tagged
`resolve-v<version>` with the zip attached as `nolgia-resolve-<version>.zip`.

```sh
python3 resolve/build.py      # resolve/dist/nolgia-resolve-0.1.0.zip
```
