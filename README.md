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
