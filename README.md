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
| [`tools/`](tools/) | Test tools shared by the plugins | GPL-3.0-or-later |

## NOLGIA for Blender

### Install and use

1. Get `nolgia-<version>.zip` from [nolgia.ai/plugins/blender](https://nolgia.ai/plugins/blender).
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

Build the installable zip and check it:

```sh
blender --command extension build --source-dir blender --output-dir blender/dist
blender --command extension validate blender/dist/nolgia-0.1.0.zip
```

Run the mock API on its own:

```sh
python3 tools/mock_bridge_server.py --port 8765 --token test-token --auto-approve-after 1
```

The wire format is the NOLGIA API's `/bridge/*` endpoints (`api/openapi.yaml`,
`internal/handlers/bridge.go`); the mock follows the same rules.
