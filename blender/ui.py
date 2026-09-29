# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""Preferences, the NOLGIA tab in the 3D Viewport sidebar, and buttons."""

import time

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty

from . import runtime

PACKAGE = __package__
MAX_CODE_LINES = 30

STATUS_ICONS = {
    "received": "TIME",
    "running": "PLAY",
    "approval": "QUESTION",
    "succeeded": "CHECKMARK",
    "failed": "ERROR",
    "cancelled": "CANCEL",
    "expired": "CANCEL",
}


def _ctl():
    return runtime.controller


def _on_connected(prefs, _context):
    ctl = _ctl()
    if ctl is None or ctl._syncing:
        return
    if prefs.connected:
        ctl.connect()
    else:
        ctl.disconnect()


def _on_allow_agent(_prefs, _context):
    ctl = _ctl()
    if ctl is not None and not ctl._syncing:
        ctl.update_snapshot()


class NOLGIA_Preferences(bpy.types.AddonPreferences):
    bl_idname = PACKAGE

    token: StringProperty(name="Token", subtype="PASSWORD", options={"HIDDEN"})
    account_email: StringProperty(name="Account", options={"HIDDEN"})
    token_expires_at: FloatProperty(name="Token expires", options={"HIDDEN"})
    connected: BoolProperty(
        name="Connected",
        description="Let NOLGIA send commands to this Blender. Turn off to pause",
        default=False,
        update=_on_connected,
    )
    allow_agent: BoolProperty(
        name="Allow NOLGIA Agent",
        description="Let your NOLGIA Agent in the cloud work here too, not only the agent apps "
        "you run yourself (Claude, Cursor and others)",
        default=True,
        update=_on_allow_agent,
    )
    ask_before_run: BoolProperty(
        name="Ask before running code",
        description="Show each piece of Python NOLGIA wants to run and wait for you to approve it",
        default=False,
    )

    def draw(self, context):
        draw_nolgia(self.layout, context, in_prefs=True)


def draw_nolgia(layout, context, in_prefs=False):
    ctl = _ctl()
    prefs = ctl.prefs() if ctl is not None else None
    if ctl is None or prefs is None:
        layout.label(text="NOLGIA is loading...")
        return

    col = layout.column(align=True)
    col.label(text=ctl.status_line(), icon=_status_icon(ctl))

    if ctl.login is not None:
        box = layout.box()
        prompt = ctl.login.prompt
        if prompt is not None:
            box.label(text="Your code:")
            row = box.row()
            row.scale_y = 1.6
            row.label(text=prompt.user_code)
            box.label(text="Approve it in the browser page that opened.")
            row = box.row(align=True)
            row.operator("nolgia.open_sign_in_page", icon="URL")
            row.operator("nolgia.copy_code", icon="COPYDOWN")
        box.operator("nolgia.cancel_sign_in", icon="CANCEL")
    elif not ctl.signed_in:
        row = layout.row()
        row.scale_y = 1.4
        row.operator("nolgia.sign_in", icon="USER")
    else:
        email = ctl.account_email()
        if email:
            layout.label(text="Signed in as %s" % email)
        elif ctl.env_token:
            layout.label(text="Signed in with NOLGIA_TOKEN")
        col = layout.column()
        col.prop(prefs, "connected")
        col.prop(prefs, "allow_agent")
        col.prop(prefs, "ask_before_run")
        layout.operator("nolgia.sign_out", icon="X")

    for pending in list(ctl.executor.approvals):
        box = layout.box()
        box.label(text=pending.request.title, icon="QUESTION")
        for line in pending.request.lines[:6]:
            box.label(text=_clip(line))
        if len(pending.request.lines) > 6:
            box.operator("nolgia.review_request", text="Show all", icon="TEXT").command_id = pending.id
        row = box.row(align=True)
        approve = row.operator("nolgia.approve", text=pending.request.approve_label, icon="CHECKMARK")
        approve.command_id = pending.id
        row.operator("nolgia.deny", text="Deny", icon="CANCEL").command_id = pending.id

    items = ctl.activity.items()
    box = layout.box()
    header = box.row()
    header.label(text="Activity", icon="SORTTIME")
    if ctl.signed_in:
        paused = not prefs.connected
        header.operator("nolgia.pause", text="Resume" if paused else "Pause",
                        icon="PLAY" if paused else "PAUSE")
    if not items:
        box.label(text="Nothing yet.")
    for item in items:
        row = box.row(align=True)
        row.label(text=time.strftime("%H:%M:%S", time.localtime(item["time"])))
        row.label(text=item["kind"])
        label = runtime.ActivityLog.STATUS_LABELS.get(item["status"], item["status"])
        row.label(text=label, icon=STATUS_ICONS.get(item["status"], "DOT"))
    if in_prefs:
        layout.label(text="NOLGIA works in this Blender only while Connected is on.")


def _status_icon(ctl):
    if ctl.executor.approvals:
        return "QUESTION"
    worker = ctl.worker
    if worker is None:
        return "UNLINKED" if ctl.signed_in else "USER"
    return "LINKED" if worker.state == runtime.Status.CONNECTED else "TIME"


def _clip(line, width=90):
    line = line.replace("\t", "    ")
    return line if len(line) <= width else line[: width - 3] + "..."


class VIEW3D_PT_nolgia(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "NOLGIA"
    bl_label = "NOLGIA"

    def draw(self, context):
        draw_nolgia(self.layout, context)


class NOLGIA_OT_sign_in(bpy.types.Operator):
    bl_idname = "nolgia.sign_in"
    bl_label = "Sign in"
    bl_description = "Sign in to NOLGIA in your browser"

    def execute(self, context):
        _ctl().sign_in()
        return {"FINISHED"}


class NOLGIA_OT_cancel_sign_in(bpy.types.Operator):
    bl_idname = "nolgia.cancel_sign_in"
    bl_label = "Cancel"
    bl_description = "Stop signing in"

    def execute(self, context):
        _ctl().cancel_sign_in()
        return {"FINISHED"}


class NOLGIA_OT_open_sign_in_page(bpy.types.Operator):
    bl_idname = "nolgia.open_sign_in_page"
    bl_label = "Open page again"
    bl_description = "Open the NOLGIA sign in page in your browser"

    def execute(self, context):
        ctl = _ctl()
        if ctl.login is not None and ctl.login.prompt is not None:
            bpy.ops.wm.url_open(url=ctl.login.prompt.open_url)
        return {"FINISHED"}


class NOLGIA_OT_copy_code(bpy.types.Operator):
    bl_idname = "nolgia.copy_code"
    bl_label = "Copy code"
    bl_description = "Copy the sign in code"

    def execute(self, context):
        ctl = _ctl()
        if ctl.login is not None and ctl.login.prompt is not None:
            context.window_manager.clipboard = ctl.login.prompt.user_code
        return {"FINISHED"}


class NOLGIA_OT_sign_out(bpy.types.Operator):
    bl_idname = "nolgia.sign_out"
    bl_label = "Sign out"
    bl_description = "Disconnect and forget the NOLGIA sign in on this computer"

    def execute(self, context):
        _ctl().sign_out()
        return {"FINISHED"}


class NOLGIA_OT_pause(bpy.types.Operator):
    bl_idname = "nolgia.pause"
    bl_label = "Pause"
    bl_description = "Stop or resume taking commands from NOLGIA"

    def execute(self, context):
        prefs = _ctl().prefs()
        prefs.connected = not prefs.connected
        return {"FINISHED"}


class NOLGIA_OT_approve(bpy.types.Operator):
    bl_idname = "nolgia.approve"
    bl_label = "Approve"
    bl_description = "Let NOLGIA do this"
    bl_options = {"INTERNAL"}

    command_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        _ctl().executor.approve(self.command_id)
        _ctl().bump()
        return {"FINISHED"}


class NOLGIA_OT_deny(bpy.types.Operator):
    bl_idname = "nolgia.deny"
    bl_label = "Deny"
    bl_description = "Do not let NOLGIA do this"
    bl_options = {"INTERNAL"}

    command_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        _ctl().executor.deny(self.command_id)
        _ctl().bump()
        return {"FINISHED"}


class NOLGIA_OT_review_request(bpy.types.Operator):
    """Shows a waiting request. OK approves; closing the window leaves it
    waiting in the NOLGIA panel."""

    bl_idname = "nolgia.review_request"
    bl_label = "NOLGIA request"
    bl_description = "Review what NOLGIA wants to do"
    bl_options = {"INTERNAL"}

    command_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def _pending(self):
        ctl = _ctl()
        if ctl is None:
            return None
        return next((p for p in ctl.executor.approvals if p.id == self.command_id), None)

    def invoke(self, context, event):
        pending = self._pending()
        if pending is None:
            return {"CANCELLED"}
        kwargs = {"width": 640}
        try:
            return context.window_manager.invoke_props_dialog(
                self, title=pending.request.title, confirm_text=pending.request.approve_label, **kwargs
            )
        except TypeError:  # Blender before 4.1 has no title/confirm_text
            return context.window_manager.invoke_props_dialog(self, **kwargs)

    def draw(self, context):
        pending = self._pending()
        layout = self.layout
        if pending is None:
            layout.label(text="This request is no longer waiting.")
            return
        layout.label(text=pending.request.title, icon="QUESTION")
        box = layout.box()
        col = box.column(align=True)
        for line in pending.request.lines[:MAX_CODE_LINES]:
            col.label(text=_clip(line, 110) or " ")
        if len(pending.request.lines) > MAX_CODE_LINES:
            col.label(text="... %d more lines" % (len(pending.request.lines) - MAX_CODE_LINES))
        layout.label(text="Code runs on this computer with your permissions.", icon="INFO")
        layout.operator("nolgia.deny", text="Deny", icon="CANCEL").command_id = self.command_id

    def execute(self, context):
        _ctl().executor.approve(self.command_id)
        _ctl().bump()
        return {"FINISHED"}


CLASSES = (
    NOLGIA_Preferences,
    VIEW3D_PT_nolgia,
    NOLGIA_OT_sign_in,
    NOLGIA_OT_cancel_sign_in,
    NOLGIA_OT_open_sign_in_page,
    NOLGIA_OT_copy_code,
    NOLGIA_OT_sign_out,
    NOLGIA_OT_pause,
    NOLGIA_OT_approve,
    NOLGIA_OT_deny,
    NOLGIA_OT_review_request,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
