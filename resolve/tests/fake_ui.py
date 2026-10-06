# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""A stand-in for Resolve's UIManager (fusion.UIManager) and UIDispatcher
(bmd.UIDispatcher), with the parts the NOLGIA window uses, as the Workflow
Integrations README describes them."""

KNOWN_PROPS = {
    "Label": {"ID", "Text", "WordWrap", "Weight", "Font", "Alignment", "Hidden", "MinimumSize"},
    "Button": {"ID", "Text", "Weight", "Hidden", "Enabled", "ToolTip"},
    "CheckBox": {"ID", "Text", "Weight", "Checked", "Hidden", "ToolTip"},
    "Tree": {"ID", "Weight", "RootIsDecorated", "AlternatingRowColors", "SelectionMode", "ColumnCount", "Hidden"},
    "TextEdit": {"ID", "PlainText", "ReadOnly", "Font", "LineWrapMode", "Lexer", "Weight"},
    "Timer": {"ID", "Interval", "SingleShot"},
    "VGroup": {"Spacing", "Weight"},
    "HGroup": {"Spacing", "Weight"},
}


class Handlers:
    """win.On.X.Clicked = fn and win.On["X"].Clicked = fn; and, on the
    dispatcher, On.Timeout = fn, where Resolve 21.1.1 delivers every timer's
    Timeout (a handler set on On[<timer id>].Timeout is never called)."""

    def __init__(self):
        object.__setattr__(self, "_by_id", {})
        object.__setattr__(self, "generic", {})

    def __getitem__(self, element_id):
        return self._by_id.setdefault(element_id, EventSlot())

    def __getattr__(self, element_id):
        return self[element_id]

    def __setattr__(self, event, fn):
        self.generic[event] = fn


class EventSlot:
    def __init__(self):
        object.__setattr__(self, "events", {})

    def __setattr__(self, name, fn):
        self.events[name] = fn

    def __getattr__(self, name):
        return self.events[name]


class Element:
    def __init__(self, kind, props=None, children=None):
        props = dict(props or {})
        unknown = set(props) - KNOWN_PROPS.get(kind, set(props))
        assert not unknown, "%s does not take %s" % (kind, unknown)
        self.__dict__["kind"] = kind
        self.__dict__["children"] = list(children or [])
        self.__dict__["props"] = props
        self.__dict__["rows"] = []
        self.__dict__["header"] = None
        self.__dict__["ColumnWidth"] = {}
        self.__dict__["started"] = False

    def __getattr__(self, name):
        if name in self.props:
            return self.props[name]
        if name in ("Text", "PlainText"):
            return ""
        if name in ("Checked", "Hidden"):
            return False
        raise AttributeError(name)

    def __setattr__(self, name, value):
        self.props[name] = value

    # Tree
    def SetHeaderLabels(self, labels):
        self.__dict__["header"] = list(labels)

    def Clear(self):
        self.rows.clear()

    def NewItem(self):
        return TreeItem()

    def AddTopLevelItem(self, item):
        self.rows.append(item)

    # Timer
    def Start(self):
        self.__dict__["started"] = True

    def Stop(self):
        self.__dict__["started"] = False

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class TreeItem:
    def __init__(self):
        self.Text = {}


class Window:
    def __init__(self, ui, props, layout):
        self.ui = ui
        self.props = props
        self.layout = layout
        self.On = Handlers()
        self.shown = False
        self.items = {e.props["ID"]: e for e in layout.walk() if "ID" in e.props}

    def GetItems(self):
        return dict(self.items)

    def Find(self, element_id):
        return self.items.get(element_id)

    def Show(self):
        self.shown = True

    def Hide(self):
        self.shown = False

    def Raise(self):
        pass

    def fire(self, element_id, event="Clicked"):
        self.On[element_id].events[event]({"who": element_id})


class UIManager:
    def __init__(self):
        self.windows = {}
        self.timers = []

    def _factory(kind):
        def make(self, props=None, children=None):
            if isinstance(props, list):
                props, children = {}, props
            element = Element(kind, props, children)
            if kind == "Timer":
                self.timers.append(element)
            return element
        return make

    Label = _factory("Label")
    Button = _factory("Button")
    CheckBox = _factory("CheckBox")
    Tree = _factory("Tree")
    TextEdit = _factory("TextEdit")
    Timer = _factory("Timer")
    VGroup = _factory("VGroup")
    HGroup = _factory("HGroup")

    def HGap(self, *args):
        return Element("HGap")

    def VGap(self, *args):
        return Element("VGap")

    def Font(self, props):
        return dict(props)

    def FindWindow(self, window_id):
        win = self.windows.get(window_id)
        return win if win is not None and win.shown else None


class Dispatcher:
    def __init__(self, ui):
        self.ui = ui
        self.On = Handlers()
        self.exited = False

    def AddWindow(self, props, layout):
        win = Window(self.ui, props, layout)
        self.ui.windows[props["ID"]] = win
        return win

    def ExitLoop(self, code=0):
        self.exited = True

    def tick(self, timer_id):
        timer = next(e for e in self.ui.timers if e.props.get("ID") == timer_id)
        if timer.started:
            self.On.generic["Timeout"]({"what": "Timeout", "who": timer_id})


class Fusion:
    def __init__(self):
        self.UIManager = UIManager()


class Bmd:
    def __init__(self):
        self.dispatchers = []

    def UIDispatcher(self, ui):
        disp = Dispatcher(ui)
        self.dispatchers.append(disp)
        return disp
