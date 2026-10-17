# NOLGIA plugins

NOLGIA's own plugins for the apps you already work in. A plugin connects the
app on your computer to NOLGIA, so your agent (Claude, Cursor, the NOLGIA
Agent and others, through NOLGIA's MCP server) can look at your project, make
changes in it, render previews and move files between the app and your
NOLGIA library.

**Each plugin now has its own repository.** They lived here, one folder per
app, until October 16, 2026, when each folder moved to a repository of its
own with its history, its branches, its tags and its releases:

| Plugin | Repository | Was here in | Download |
|---|---|---|---|
| NOLGIA for Blender (4.2 and newer) | [nolgiainc/nolgia-blender](https://github.com/nolgiainc/nolgia-blender) | `blender/`, `presets/blender/` | [nolgia.ai/plugins/blender](https://nolgia.ai/plugins/blender) |
| NOLGIA for After Effects, Premiere Pro and Illustrator (2025 and newer) | [nolgiainc/nolgia-adobe](https://github.com/nolgiainc/nolgia-adobe) | `adobe/`, `presets/after-effects/`, `presets/premiere/`, `presets/illustrator/` | [nolgia.ai/plugins](https://nolgia.ai/plugins) |
| NOLGIA for Photoshop (2024 and newer) | [nolgiainc/nolgia-photoshop](https://github.com/nolgiainc/nolgia-photoshop) | `photoshop/`, `presets/photoshop/` | [nolgia.ai/plugins/photoshop](https://nolgia.ai/plugins/photoshop) |
| NOLGIA for DaVinci Resolve Studio (21.1 and newer) | [nolgiainc/nolgia-resolve](https://github.com/nolgiainc/nolgia-resolve) | `resolve/`, `presets/resolve/` | [nolgia.ai/plugins/davinci-resolve](https://nolgia.ai/plugins/davinci-resolve) |

In each repository the plugin's folder is the root, next to its Film
assistant workflows (`presets/<app>/`, the same paths as here) and the mock
API its tests run against (`tools/mock_bridge_server.py`, one copy per
repository). Each has its own license (GPL-3.0-or-later, as here), README and
CI.

**Issues and pull requests belong in the plugin's repository.** The open
Blender issue moved to
[nolgia-blender](https://github.com/nolgiainc/nolgia-blender/issues).

**Downloads.** The releases on this repository stay where they are, so every
link to them keeps working. The same files, under the same tags, are on each
plugin's repository, and new releases are made there.

## Moving a local clone

The history was rewritten so that each plugin's folder is its repository's
root, so a clone of this repository cannot switch to a new remote: clone the
plugin's repository instead.

```sh
git clone git@github.com:nolgiainc/nolgia-blender.git    # or nolgia-adobe, nolgia-photoshop, nolgia-resolve
```

Every branch that changed a plugin moved to that plugin's repository under
the same name. To carry work you have not pushed, export it from your old
clone and apply it in the new one. For the plugin's own folder, strip the
folder name from the paths:

```sh
# In your clone of nolgia-plugins
git format-patch --relative=blender origin/main..my-branch -o ~/plugin-patches
# Workflows and the mock API keep their paths
git format-patch origin/main..my-branch -o ~/preset-patches -- presets/blender tools

# In the new clone
git switch -c my-branch
git am ~/plugin-patches/*.patch ~/preset-patches/*.patch
```

The commits there keep their authors and dates. Their ids are new.
