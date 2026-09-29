# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""What each command does inside Blender. Main thread only.

Each `do_<kind>(args, prepared, command)` returns the result dict or raises
CommandError. Network work happens on the worker thread before (download for
import_asset, see `prepare_import`) or after (upload of a rendered or exported
file, see `finish_upload`); a result that carries `_upload` is uploaded there.
"""

import contextlib
import glob
import os
import shutil
import tempfile

import bpy
import mathutils

from .core import util
from .core.api import ApiError, NetworkError
from .core.commands import CommandError
from .core.mainthread import ApprovalRequest
from .core.pyexec import run_python

EEVEE_IDS = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")
ENGINE_CHOICES = {
    "eevee": EEVEE_IDS,
    "workbench": ("BLENDER_WORKBENCH",),
    "cycles": ("CYCLES",),
}
PREVIEW_MAX_CYCLES_SAMPLES = 32
UPLOAD_TAGS = ["blender"]


# ---------------------------------------------------------------- context


def app_version():
    return "%d.%d.%d" % tuple(bpy.app.version[:3])


def active_window():
    win = getattr(bpy.context, "window", None)
    if win is not None:
        return win
    wm = bpy.context.window_manager
    return wm.windows[0] if wm is not None and len(wm.windows) else None


def active_scene():
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        win = active_window()
        scene = win.scene if win is not None else None
    return scene or bpy.data.scenes[0]


def active_view_layer(scene):
    win = active_window()
    if win is not None and win.scene == scene:
        return win.view_layer
    layer = getattr(bpy.context, "view_layer", None)
    if layer is not None and layer in scene.view_layers.values():
        return layer
    return scene.view_layers[0]


@contextlib.contextmanager
def ui_context():
    """Point operators at a window and 3D view when Blender has a window.

    A timer callback has no window, and many operators want one.
    """
    win = None if bpy.app.background else active_window()
    if win is None:
        yield
        return
    kwargs = {"window": win, "screen": win.screen}
    area = next((a for a in win.screen.areas if a.type == "VIEW_3D"), None)
    if area is not None:
        kwargs["area"] = area
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is not None:
            kwargs["region"] = region
    try:
        override = bpy.context.temp_override(**kwargs)
    except Exception:
        yield
        return
    with override:
        yield


def push_undo(message):
    if bpy.app.background:
        return
    try:
        with ui_context():
            bpy.ops.ed.undo_push(message=message)
    except Exception:
        pass


# Blender without a window does not track unsaved changes (bpy.data.is_dirty
# is always True there), so headless we track what our own commands changed.
_headless_changes = False


def mark_changed():
    global _headless_changes
    _headless_changes = True


def mark_clean():
    global _headless_changes
    _headless_changes = False


def is_dirty():
    if bpy.app.background:
        return _headless_changes
    return bool(bpy.data.is_dirty)


def document():
    """{name, path?} for the session's `document`."""
    path = bpy.data.filepath
    doc = {"name": os.path.basename(path) if path else "untitled"}
    if path:
        doc["path"] = path
    return doc


def download_dir(fallback):
    """Imported videos and sounds stay on disk, so keep them next to the
    .blend when it is saved (in nolgia_assets), else in the add-on's folder."""
    path = bpy.data.filepath
    if path:
        return os.path.join(os.path.dirname(path), "nolgia_assets")
    return fallback


def output_size(render):
    pct = render.resolution_percentage / 100.0
    return max(1, int(render.resolution_x * pct)), max(1, int(render.resolution_y * pct))


class Saved:
    """Remember settings and put them back afterwards."""

    def __init__(self):
        self.items = []

    def keep(self, obj, *attrs):
        if obj is None:
            return
        for attr in attrs:
            try:
                self.items.append((obj, attr, getattr(obj, attr)))
            except Exception:
                pass

    def restore(self):
        for obj, attr, value in reversed(self.items):
            try:
                setattr(obj, attr, value)
            except Exception:
                pass


def op_exists(path):
    module, name = path.split(".")
    try:
        getattr(getattr(bpy.ops, module), name).get_rna_type()
        return True
    except (AttributeError, KeyError):
        return False


def ensure_operator(path, addon, label):
    if op_exists(path):
        return
    try:
        import addon_utils

        addon_utils.enable(addon, default_set=False)
    except Exception:
        pass
    if not op_exists(path):
        raise CommandError(
            "Blender's %s add-on is off. Turn it on in Edit > Preferences > Add-ons, then try again." % label
        )


def resolve_path(path, must_exist=False):
    """Absolute path for a save/open target. `//` means next to this .blend."""
    path = os.path.expanduser(path.strip())
    if path.startswith("//"):
        if not bpy.data.filepath:
            raise CommandError("A path starting with // needs a saved file. Use a full path instead.")
        path = bpy.path.abspath(path)
    elif not os.path.isabs(path):
        if not bpy.data.filepath:
            raise CommandError("Use a full path, for example C:/Projects/shot.blend or /Users/me/shot.blend.")
        path = os.path.join(os.path.dirname(bpy.data.filepath), path)
    path = os.path.normpath(path)
    if must_exist and not os.path.isfile(path):
        raise CommandError("There is no file at %s." % path)
    return path


# ------------------------------------------------------------------- info


def do_info(args, prepared, command):
    scene = active_scene()
    render = scene.render
    layer = active_view_layer(scene)
    width, height = output_size(render)
    collections = [{"name": scene.collection.name, "objects": len(scene.collection.objects)}]
    for coll in scene.collection.children_recursive:
        collections.append({"name": coll.name, "objects": len(coll.objects)})
    selected = [o.name for o in layer.objects if o.select_get(view_layer=layer)]
    active = layer.objects.active
    doc = document()
    doc["dirty"] = is_dirty()
    return {
        "app": "blender",
        "app_version": app_version(),
        "document": doc,
        "scene": {
            "name": scene.name,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "frame_current": scene.frame_current,
            "fps": round(render.fps / render.fps_base, 3),
            "camera": scene.camera.name if scene.camera else None,
            "objects": len(scene.objects),
        },
        "render": {
            "engine": render.engine,
            "resolution_x": render.resolution_x,
            "resolution_y": render.resolution_y,
            "resolution_percentage": render.resolution_percentage,
            "output_width": width,
            "output_height": height,
        },
        "scenes": [s.name for s in bpy.data.scenes],
        "view_layer": layer.name,
        "collections": collections,
        "selected_objects": selected,
        "active_object": active.name if active else None,
        "cameras": [
            {"name": o.name, "active": o == scene.camera}
            for o in scene.objects
            if o.type == "CAMERA"
        ],
    }


# -------------------------------------------------------------------- run


def run_approval(command, ask):
    if not ask:
        return None
    code = command.args.get("code", "")
    lines = code.splitlines() or [""]
    return ApprovalRequest(
        "%s wants to run Python in Blender" % command.caller_label,
        lines,
        headless_error="Ask before running code is on, and Blender has no window to ask in, "
        "so the code did not run.",
        approve_label="Run code",
    )


def do_run(args, prepared, command):
    namespace = {
        "bpy": bpy,
        "mathutils": mathutils,
        "C": bpy.context,
        "D": bpy.data,
        "result": None,
    }
    timeout = args.get("timeout_seconds") or max(1.0, command.remaining())
    mark_changed()
    with ui_context():
        outcome = run_python(args["code"], namespace, timeout=timeout)
    push_undo("NOLGIA: run code")
    if not outcome.ok:
        raise CommandError(outcome.error, result=outcome.result())
    return outcome.result()


# ---------------------------------------------------------------- rendering


def _find_camera(scene, name):
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "CAMERA":
            cams = [o.name for o in scene.objects if o.type == "CAMERA"]
            raise CommandError(
                "There is no camera named %s. Cameras in this scene: %s."
                % (name, ", ".join(cams) or "none")
            )
        return obj
    if scene.camera is not None:
        return scene.camera
    cam = next((o for o in scene.objects if o.type == "CAMERA"), None)
    if cam is None:
        raise CommandError("This scene has no camera. Add one, or name one with `camera`.")
    return cam


def _set_engine(render, choice):
    if not choice or choice == "current":
        return
    for engine in ENGINE_CHOICES[choice]:
        try:
            render.engine = engine
            return
        except TypeError:
            continue
    raise CommandError("This Blender does not have the %s render engine." % choice)


def render_still(scene, camera, frame, width, height, path, engine="current", fast=False):
    """Render one frame to a PNG. Returns the engine used.

    Settings are put back afterwards. If the engine fails (EEVEE on a render
    node without a GPU, say), try again with Workbench.
    """
    render = scene.render
    saved = Saved()
    saved.keep(scene, "camera")
    saved.keep(render, "resolution_x", "resolution_y", "resolution_percentage", "filepath",
               "use_file_extension", "engine")
    saved.keep(render.image_settings, "file_format", "color_mode", "color_depth", "compression")
    cycles = getattr(scene, "cycles", None)
    saved.keep(cycles, "samples")
    frame_before = scene.frame_current
    try:
        scene.camera = camera
        if frame is not None and frame != scene.frame_current:
            scene.frame_set(frame)
        render.resolution_x, render.resolution_y = width, height
        render.resolution_percentage = 100
        _set_engine(render, engine)
        if fast and render.engine == "CYCLES" and cycles is not None:
            cycles.samples = min(cycles.samples, PREVIEW_MAX_CYCLES_SAMPLES)
        settings = render.image_settings
        settings.file_format = "PNG"
        settings.color_mode = "RGBA" if render.film_transparent else "RGB"
        settings.color_depth = "8"
        settings.compression = 15
        engines = [render.engine]
        if render.engine != "BLENDER_WORKBENCH":
            engines.append("BLENDER_WORKBENCH")
        last_error = None
        for engine_id in engines:
            try:
                render.engine = engine_id
                bpy.ops.render.render(write_still=False, scene=scene.name)
                result = bpy.data.images.get("Render Result")
                if result is None:
                    raise RuntimeError("Blender made no image")
                result.save_render(filepath=path, scene=scene)
                if os.path.isfile(path):
                    return engine_id
                raise RuntimeError("the image was not written")
            except Exception as err:
                last_error = err
        raise CommandError("Blender could not render the image: %s" % last_error)
    finally:
        saved.restore()
        if scene.frame_current != frame_before:
            scene.frame_set(frame_before)


def do_preview(args, prepared, command):
    scene = active_scene()
    camera = _find_camera(scene, args.get("camera"))
    render = scene.render
    out_w, _ = output_size(render)
    aspect = (render.resolution_x * render.pixel_aspect_x) / max(1e-6, render.resolution_y * render.pixel_aspect_y)
    width = args.get("width") or min(1280, max(16, out_w))
    height = max(16, int(round(width / aspect)))
    if height > 1920:  # very tall frames: keep the long side at most 1920
        height = 1920
        width = max(16, int(round(height * aspect)))
    frame = args.get("frame")
    if frame is None:
        frame = scene.frame_current
    folder = tempfile.mkdtemp(prefix="nolgia-preview-")
    path = os.path.join(folder, "preview.png")
    try:
        engine = render_still(scene, camera, frame, width, height, path, args.get("engine"), fast=True)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {
        "_upload": {
            "path": path,
            "content_type": "image/png",
            "filename": "%s-preview-%04d.png" % (_stem(), frame),
            "display_name": "Blender preview, %s frame %d" % (scene.name, frame),
            "cleanup": folder,
        },
        "width": width,
        "height": height,
        "camera": camera.name,
        "frame": frame,
        "engine": engine,
    }


def _has_sound(scene):
    editor = scene.sequence_editor
    if editor is None:
        return False
    strips = getattr(editor, "strips_all", None) or getattr(editor, "sequences_all", [])
    return any(s.type == "SOUND" for s in strips)


def render_movie(scene, folder, stem, frames):
    render = scene.render
    ffmpeg = render.ffmpeg
    saved = Saved()
    saved.keep(scene, "frame_start", "frame_end")
    saved.keep(render, "filepath", "use_file_extension", "resolution_x", "resolution_y",
               "resolution_percentage")
    saved.keep(render.image_settings, "file_format")
    saved.keep(ffmpeg, "format", "codec", "constant_rate_factor", "ffmpeg_preset", "audio_codec")
    frame_before = scene.frame_current
    try:
        if frames:
            scene.frame_start, scene.frame_end = frames
        width, height = output_size(render)
        if width % 2 or height % 2:  # H.264 needs even sizes
            render.resolution_x, render.resolution_y = width - width % 2, height - height % 2
            render.resolution_percentage = 100
        render.image_settings.file_format = "FFMPEG"
        ffmpeg.format = "MPEG4"
        ffmpeg.codec = "H264"
        for attr, value in (("constant_rate_factor", "MEDIUM"), ("ffmpeg_preset", "GOOD")):
            try:
                setattr(ffmpeg, attr, value)
            except Exception:
                pass
        ffmpeg.audio_codec = "AAC" if _has_sound(scene) else "NONE"
        render.filepath = os.path.join(folder, stem + "-")
        render.use_file_extension = True
        try:
            bpy.ops.render.render(animation=True, scene=scene.name)
        except Exception as err:
            raise CommandError("Blender could not render the video: %s" % err) from None
    finally:
        saved.restore()
        if scene.frame_current != frame_before:
            scene.frame_set(frame_before)
    movies = sorted(glob.glob(os.path.join(folder, "*.mp4")), key=os.path.getmtime)
    if not movies:
        raise CommandError("Blender finished rendering but wrote no video file.")
    final = os.path.join(folder, stem + ".mp4")
    os.replace(movies[-1], final)
    return final


def _stem(filename=None):
    if filename:
        return util.safe_filename(os.path.splitext(os.path.basename(filename))[0], "untitled")
    path = bpy.data.filepath
    return util.safe_filename(os.path.splitext(os.path.basename(path))[0] if path else "untitled", "untitled")


def do_export(args, prepared, command):
    fmt = args["format"]
    scene = active_scene()
    stem = _stem(args.get("filename"))
    folder = tempfile.mkdtemp(prefix="nolgia-export-")
    try:
        if fmt == "blend":
            path = os.path.join(folder, stem + ".blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, check_existing=False)
            content_type = util.UPLOAD_TYPES[".blend"]
        elif fmt == "png":
            frames = args.get("frames")
            frame = frames[0] if frames else scene.frame_current
            camera = _find_camera(scene, None)
            width, height = output_size(scene.render)
            path = os.path.join(folder, "%s-%04d.png" % (stem, frame))
            render_still(scene, camera, frame, width, height, path)
            content_type = "image/png"
        elif fmt == "mp4":
            _find_camera(scene, None)
            path = render_movie(scene, folder, stem, args.get("frames"))
            content_type = "video/mp4"
        else:  # glb
            ensure_operator("export_scene.gltf", "io_scene_gltf2", "glTF 2.0")
            path = os.path.join(folder, stem + ".glb")
            with ui_context():
                bpy.ops.export_scene.gltf(
                    filepath=path, export_format="GLB", use_selection=bool(args.get("selected_only"))
                )
            content_type = "model/gltf-binary"
        if not os.path.isfile(path):
            raise CommandError("Blender did not write the %s file." % fmt)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {
        "_upload": {
            "path": path,
            "content_type": content_type,
            "filename": os.path.basename(path),
            "display_name": os.path.basename(path),
            "cleanup": folder,
        },
        "format": fmt,
        "filename": os.path.basename(path),
    }


# ------------------------------------------------------------ save and open


def do_save(args, prepared, command):
    path = args.get("path")
    if not path:
        current = bpy.data.filepath
        if not current:
            raise CommandError(
                "This file has never been saved, so there is no file to save to. "
                "Pass `path`, for example C:/Projects/shot.blend."
            )
        bpy.ops.wm.save_mainfile(filepath=current, check_existing=False)
        mark_clean()
        return {"path": bpy.data.filepath}
    target = resolve_path(path)
    if not target.lower().endswith(".blend"):
        target += ".blend"
    if os.path.isdir(target):
        raise CommandError("%s is a folder. Give a file name ending in .blend." % target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=target, check_existing=False)
    mark_clean()
    return {"path": bpy.data.filepath}


def open_approval(command):
    if not is_dirty():
        return None
    path = command.args.get("path", "")
    return ApprovalRequest(
        "%s wants to open another file" % command.caller_label,
        ["Open: %s" % path, "This file has unsaved changes. Opening the other file loses them."],
        headless_error="This file has unsaved changes and Blender has no window to ask the person in. "
        "Save first with the save command, then open.",
        approve_label="Open and lose changes",
    )


def do_open(args, prepared, command):
    target = resolve_path(args["path"], must_exist=True)
    if not target.lower().endswith(".blend"):
        raise CommandError("Blender opens .blend files. To bring in %s, upload it to NOLGIA and use import_asset." % os.path.basename(target))
    try:
        bpy.ops.wm.open_mainfile(filepath=target)
    except RuntimeError as err:
        raise CommandError("Blender could not open %s: %s" % (target, err)) from None
    mark_clean()
    return {"path": bpy.data.filepath}


# ----------------------------------------------------------------- import


def prepare_import(command, api, fallback_dir, snapshot):
    """Worker thread: fetch the asset's file. No bpy here.

    Models and images end up inside the .blend, so they download to a temp
    folder. Videos and sounds stay files on disk: they move to nolgia_assets
    next to the .blend (or the add-on's own folder while it is unsaved).
    """
    asset_id = command.args["asset_id"]
    try:
        asset = api.get_asset(asset_id)
    except ApiError as err:
        if err.status == 404:
            raise CommandError("NOLGIA has no asset %s in this account." % asset_id) from None
        raise
    url = (asset or {}).get("signed_url")
    if not url:
        raise CommandError("NOLGIA did not give a download link for asset %s." % asset_id)
    name = asset.get("display_name") or asset_id
    temp_dir = tempfile.mkdtemp(prefix="nolgia-import-")
    temp = os.path.join(temp_dir, "download")
    try:
        try:
            head = api.download(url, temp)
        except NetworkError as err:
            raise CommandError("Could not download the asset from NOLGIA (%s)." % err) from None
        ext = util.import_extension(name, asset.get("mime_type"), head)
        if not ext:
            raise CommandError(
                "Blender cannot import this file type (%s). It imports GLB, glTF, FBX, OBJ, images, "
                "video and audio." % (asset.get("mime_type") or "unknown")
            )
        kind = util.IMPORT_KINDS[ext]
        filename = util.safe_filename(os.path.splitext(name)[0], asset_id) + ext
        if kind in ("video", "audio"):
            base_dir = (snapshot or {}).get("download_dir") or fallback_dir
            folder = os.path.join(base_dir, util.safe_filename(asset_id))
            os.makedirs(folder, exist_ok=True)
            final = os.path.join(folder, filename)
            shutil.move(temp, final)
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir = None
        else:
            final = os.path.join(temp_dir, filename)
            os.replace(temp, final)
    except BaseException:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return {"path": final, "kind": kind, "ext": ext, "asset_id": asset_id, "temp_dir": temp_dir}


def _new_objects(before):
    return [o.name for o in bpy.data.objects if o.name not in before]


def _import_planes(path):
    before = set(bpy.data.objects.keys())
    images_before = set(bpy.data.images.keys())
    ensure_operator("image.import_as_mesh_planes", "io_import_images_as_planes", "Import Images as Planes")
    with ui_context():
        bpy.ops.image.import_as_mesh_planes(
            files=[{"name": os.path.basename(path)}], directory=os.path.dirname(path) + os.sep
        )
    for name in set(bpy.data.images.keys()) - images_before:
        image = bpy.data.images[name]
        if image.source == "FILE" and image.packed_file is None:
            try:
                image.pack()
            except Exception:
                pass
    return _new_objects(before)


def _import_file(path, kind, ext, mode):
    if kind == "model":
        before = set(bpy.data.objects.keys())
        with ui_context():
            if ext in (".glb", ".gltf"):
                ensure_operator("import_scene.gltf", "io_scene_gltf2", "glTF 2.0")
                bpy.ops.import_scene.gltf(filepath=path)
            elif ext == ".fbx":
                ensure_operator("import_scene.fbx", "io_scene_fbx", "FBX")
                bpy.ops.import_scene.fbx(filepath=path)
            else:
                bpy.ops.wm.obj_import(filepath=path)
        return _new_objects(before)
    if kind == "image":
        if mode == "texture":
            image = bpy.data.images.load(path, check_existing=False)
            image.pack()
            return [image.name]
        return _import_planes(path)
    if kind == "video":
        if mode == "plane":
            return _import_planes(path)
        return [bpy.data.movieclips.load(path, check_existing=False).name]
    return [bpy.data.sounds.load(path, check_existing=False).name]


def do_import_asset(args, prepared, command):
    path, kind, ext = prepared["path"], prepared["kind"], prepared["ext"]
    temp_dir = prepared.get("temp_dir")
    mark_changed()
    try:
        imported = _import_file(path, kind, ext, args.get("as"))
    except RuntimeError as err:
        raise CommandError("Blender could not import %s: %s" % (os.path.basename(path), err)) from None
    finally:
        if temp_dir:  # models and images now live inside the .blend
            shutil.rmtree(temp_dir, ignore_errors=True)
    if not imported:
        raise CommandError("Blender opened the file but nothing new came into the scene.")
    push_undo("NOLGIA: import")
    result = {"imported": imported, "kind": kind}
    if not temp_dir:
        result["path"] = path  # videos and sounds stay on disk here
    return result


# ----------------------------------------------------------------- upload


def finish_upload(command, result, api):
    """Worker thread: upload a file a command made, then drop the temp copy."""
    if not isinstance(result, dict) or "_upload" not in result:
        return result
    result = dict(result)
    upload = result.pop("_upload")
    try:
        asset = api.upload_file(
            upload["path"],
            upload["content_type"],
            display_name=upload.get("display_name"),
            tags=UPLOAD_TAGS,
            filename=upload.get("filename"),
        )
    except ApiError as err:
        raise CommandError("NOLGIA did not take the file: %s" % (err.detail or err.title or err.status)) from None
    except NetworkError as err:
        raise CommandError("Could not upload the file to NOLGIA (%s)." % err) from None
    finally:
        shutil.rmtree(upload.get("cleanup") or "", ignore_errors=True)
    out = {"asset_id": asset.get("id")}
    out.update(result)
    return out


RUNNERS = {
    "info": do_info,
    "run": do_run,
    "preview": do_preview,
    "import_asset": do_import_asset,
    "export": do_export,
    "save": do_save,
    "open": do_open,
}
