#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 NOLGIA Inc
"""In-app test: the NOLGIA window inside a running DaVinci Resolve Studio
(with its window), against the mock API.

1. Builds the download and installs it with its own installer into your
   Scripts/Utility folder (as a person would), and checks that Resolve's
   Scripts path includes that folder.
2. Makes a throwaway project and opens it.
3. Runs the installed NOLGIA.py inside Resolve, the way Workspace > Scripts
   > NOLGIA does (through Fusion's RunScript, with resolve, fusion and bmd
   set), with NOLGIA pointed at the mock API (token, settings and log in a
   scratch folder, so your own sign in is not touched). The NOLGIA window
   shows on screen while the test runs.
4. Drives commands through the mock, which the window runs on Resolve's
   script thread from its UIManager timer; clicks Run code in the NOLGIA
   request window from code; closes the window from code.
5. Reads the window's log, deletes the throwaway project and the LUTs it
   installed. The installed plugin stays (pass --uninstall to remove it).

It needs External scripting set to Local (to start the script and make the
project) and DaVinci Resolve open with its window.

    python3 resolve/tests/inapp_resolve.py
    python3 resolve/tests/inapp_resolve.py --api prod --token-file ~/.config/nolgia/tokens.json

With --api prod the window talks to the real NOLGIA API with your token
(never printed); the run's assets are deleted afterwards unless
--keep-assets. Only free operations.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import e2e_resolve as e2e  # noqa: E402
from e2e_resolve import COLOR_PRESETS, Caller, Checks, Host, MockBridgeServer, expect_ok  # noqa: E402

TOKEN = "inapp-token"

# Started by Resolve (Fusion's RunScript runs it in Resolve's fuscript
# program, the way the Scripts menu runs a script: resolve, fusion, bmd and
# app set, no __file__, the script's path in sys.argv[0]). Sets NOLGIA's
# variables for this program only, runs the installed NOLGIA.py the same
# way, and leaves a marker file when it ends.
WRAPPER = r'''
import os, sys, threading, traceback
os.environ.update(%(env)r)
# Never leave the window on screen: end this program after 10 minutes.
_guard = threading.Timer(600, lambda: os._exit(2))
_guard.daemon = True
_guard.start()
_script = %(script)r
_done = %(done)r
try:
    sys.argv = [_script]
    with open(_script, encoding="utf-8") as _handle:
        _code = compile(_handle.read(), _script, "exec")
    exec(_code, {"resolve": resolve, "fusion": fusion, "fu": fusion, "bmd": bmd, "app": app, "__name__": "__main__"})
    _outcome = "ended"
except SystemExit as _exit:
    _outcome = "exit %%s" %% (_exit.code,)
except BaseException:
    _outcome = traceback.format_exc()
with open(_done, "w", encoding="utf-8") as _handle:
    _handle.write("%%s\npython %%s\n%%s\n" %% (_outcome, sys.version.split()[0], sys.executable))
'''

# Runs in ResolvePython next to Resolve: where Resolve looks for scripts, and
# asks it to run the wrapper (RunScript returns at once; the script runs on).
RUN_IN_APP = r'''
import json, sys
import DaVinciResolveScript as dvr
resolve = dvr.scriptapp("Resolve")
fusion = resolve.Fusion()
out = {"paths": {}}
for key in ("Scripts:", "Scripts:Utility/"):
    out["paths"][key] = fusion.MapPath(key)
segments = fusion.MapPathSegments("Scripts:")
out["segments"] = list(segments.values()) if isinstance(segments, dict) else segments
print("INAPP_PATHS " + json.dumps(out, default=str), flush=True)
print("INAPP_STARTED " + json.dumps({"value": fusion.RunScript(sys.argv[1])}, default=str), flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    key = "win" if (e2e.WSL or sys.platform.startswith("win")) else ("darwin" if sys.platform == "darwin" else "linux")
    parser.add_argument("--resolve-python", default=os.environ.get("RESOLVE_PYTHON", e2e.DEFAULTS[key][0]))
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--uninstall", action="store_true", help="remove the installed plugin afterwards")
    parser.add_argument("--download", default=None,
                        help="install from this unzipped download (a release, as a person has it) instead of a build")
    parser.add_argument("--api", choices=("mock", "prod"), default="mock")
    parser.add_argument("--api-url", default=e2e.PROD_API, help="for --api prod")
    parser.add_argument("--token-file", default=os.path.expanduser("~/.config/nolgia/tokens.json"),
                        help="JSON with access_token, for --api prod (never printed)")
    parser.add_argument("--keep-assets", action="store_true", help="prod: leave the run's assets in the library")
    opts = parser.parse_args()
    prod = opts.api == "prod"
    if e2e.WSL and opts.resolve_python[1:3] == ":\\":
        opts.resolve_python = subprocess.check_output(["wslpath", "-u", opts.resolve_python], text=True).strip()
    host = Host(opts.resolve_python, "")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = os.path.join(e2e.scratch_root(host), "nolgia-resolve-inapp-" + stamp)
    os.makedirs(work)
    print("scratch folder: %s" % work, flush=True)
    started = time.time()
    checks = Checks()
    project_name = "NOLGIA in-app " + stamp
    luts = os.path.join(e2e.lut_dir(host), "NOLGIA")
    state = {"previous": None, "luts_before": set(os.listdir(luts)) if os.path.isdir(luts) else None}
    if host.wsl or sys.platform.startswith("win"):
        appdata = host.local(host.windows_env("APPDATA"))
        utility = os.path.join(appdata, "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts", "Utility")
    elif sys.platform == "darwin":
        utility = os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
    else:
        utility = os.path.expanduser("~/.local/share/DaVinciResolve/Fusion/Scripts/Utility")

    download = os.path.abspath(opts.download) if opts.download else os.path.join(work, "download")

    def install():
        if not opts.download:
            path = e2e.build.build(os.path.join(work, "dist"))
            with zipfile.ZipFile(path) as archive:
                archive.extractall(download)
        if host.wsl or sys.platform.startswith("win"):
            cmd = host.native(os.path.join(download, "install.cmd"))
            out = subprocess.run(["cmd.exe", "/c", cmd, "/quiet"], capture_output=True, text=True,
                                 cwd="/mnt/c" if host.wsl else None, timeout=120)
        else:
            out = subprocess.run(["sh", os.path.join(download, "install.sh")],
                                 capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "NOLGIA is installed" in out.stdout, out.stdout
        for name in ("NOLGIA.py", "nolgia_resolve.zip"):
            assert os.path.isfile(os.path.join(utility, name)), "not installed: " + name

    if not checks.check("install with the download's installer", install):
        return e2e.finish(checks, work, opts, started)

    def ready():
        probe = e2e.helper(host, work, "probe")
        assert probe["ok"], "Resolve does not answer scripts (External scripting must be Local): %s" % probe
        state["previous"] = probe.get("project")
        res = e2e.helper(host, work, "setup", {"name": project_name})
        assert res["ok"], res

    if not checks.check("throwaway project in the open Resolve", ready):
        return e2e.finish(checks, work, opts, started)

    if prod:
        with open(opts.token_file, encoding="utf-8") as handle:
            token = json.load(handle)["access_token"]
        api_url = opts.api_url.rstrip("/")
        server = None
        caller = Caller(api_url[:-3] if api_url.endswith("/v1") else api_url, token, mock=False)
    else:
        server = MockBridgeServer(tokens=(TOKEN,), poll_wait_seconds=10).start()
        token, api_url = TOKEN, server.base_url
        caller = Caller(server.root_url, TOKEN)
    config = os.path.join(work, "config")
    env = {
        "NOLGIA_TOKEN": token,
        "NOLGIA_API_URL": api_url,
        "NOLGIA_CONFIG_DIR": host.native(config),
        "NOLGIA_IMPORT_DIR": host.native(os.path.join(work, "imports")),
        "NOLGIA_BRIDGE_AUTOCONNECT": "1",
        "NOLGIA_ASK_BEFORE_RUN": "0",
        "NOLGIA_INSTANCE_ID": "inapp-" + stamp,
    }
    wrapper = os.path.join(work, "run_nolgia_in_app.py")
    done = os.path.join(work, "in_app_done.txt")
    with open(wrapper, "w", encoding="utf-8") as handle:
        handle.write(WRAPPER % {"env": env, "script": host.native(os.path.join(utility, "NOLGIA.py")),
                                "done": host.native(done)})
    runner = os.path.join(work, "run_in_app.py")
    with open(runner, "w", encoding="utf-8") as handle:
        handle.write(RUN_IN_APP)
    run_log = os.path.join(work, "run_in_app.log")
    proc = host.py_popen([host.native(runner), host.native(wrapper)], {}, run_log)
    made = {}

    def run_output():
        with open(run_log, encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def window_log():
        path = os.path.join(config, "nolgia.log")
        if not os.path.isfile(path):
            return ""
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()

    try:
        def script_path():
            end = time.time() + 60
            while time.time() < end and "INAPP_PATHS" not in run_output():
                time.sleep(0.5)
            line = next(l for l in run_output().splitlines() if l.startswith("INAPP_PATHS "))
            data = json.loads(line[len("INAPP_PATHS "):])
            made["paths"] = data
            print("  Resolve's Scripts path: %s" % data, flush=True)
            folders = list(data["segments"] or []) + [v for v in data["paths"].values() if isinstance(v, str)]
            want = os.path.normcase(host.native(os.path.dirname(utility)).rstrip("\\/"))
            assert any(os.path.normcase(str(f)).rstrip("\\/").startswith(want) for f in folders), \
                "the user Scripts folder is not in Resolve's Scripts path: %s" % folders

        checks.check("Resolve's Scripts path has the install folder", script_path)

        def window_opens_and_connects():
            end = time.time() + 90
            session = None
            while time.time() < end:
                status, data = caller.http("GET", "/v1/bridge/sessions")
                ours = [s for s in (data["sessions"] if status == 200 else []) if s["instance_id"] == env["NOLGIA_INSTANCE_ID"]]
                if ours:
                    session = ours[0]
                    break
                assert not os.path.exists(done), "the script ended early:\n%s\n%s" % (
                    open(done, encoding="utf-8").read(), window_log()[-2000:])
                time.sleep(0.5)
            assert session, "no session within 90 s:\n%s\n%s" % (run_output()[-2000:], window_log()[-2000:])
            assert session["app"] == "resolve" and session["document"] == {"name": project_name}, session
            if prod:
                caller.session_id = session["id"]
            log = window_log()
            assert "NOLGIA window open" in log, log[-2000:]
            opened = next((l for l in log.splitlines() if "NOLGIA window open" in l), "")
            print("  " + opened.split(" ", 2)[-1], flush=True)
            if host.wsl or sys.platform.startswith("win"):
                # Which Python the script runs on: the DLLs of Resolve's fuscript programs.
                script = ["powershell.exe", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='fuscript.exe'\" | ForEach-Object { "
                          "$c = $_.CommandLine; $p = Get-Process -Id $_.ProcessId; "
                          "$p.Modules | Where-Object { $_.ModuleName -like 'python3*.dll' } | "
                          "ForEach-Object { \"$($_.FileName) <- $c\" } }"]
                try:
                    out = subprocess.run(script, capture_output=True, text=True, timeout=60).stdout
                    for line in out.replace("\r", "").splitlines():
                        if "run_nolgia_in_app" in line:
                            print("  fuscript loaded " + line.split(" <- ")[0], flush=True)
                except Exception as err:
                    print("  (could not list fuscript's DLLs: %s)" % err, flush=True)

        if not checks.check("the NOLGIA window opens in Resolve and connects", window_opens_and_connects):
            raise SystemExit

        def timer_runs_commands():
            res = expect_ok(caller.command("info"))
            assert res["project"]["name"] == project_name, res
            assert "The NOLGIA window is running (timer ticks)." in window_log(), window_log()[-2000:]
            res = expect_ok(caller.command("run", {"code":
                "import threading\nresult = {'thread': threading.current_thread().name, "
                "'main': threading.current_thread() is threading.main_thread(), 'page': resolve.GetCurrentPage()}"}))
            made["thread"] = res["value"]
            print("  commands run on: %s" % res["value"], flush=True)

        checks.check("commands run from the UIManager timer", timer_runs_commands)

        png_id = caller.seed_asset("sunset-wall.png", "image/png", e2e.make_png())

        def import_and_preview():
            res = expect_ok(caller.command("import_asset", {"asset_id": png_id, "append": True}))
            assert res["appended"]["timeline"] == "NOLGIA timeline", res
            res = expect_ok(caller.command("preview", {"width": 480}))
            data = caller.asset_bytes(res["asset_id"])
            assert e2e.png_size(data) == (res["width"], res["height"]) and res["width"] == 480, res

        checks.check("import_asset and preview in the app", import_and_preview)

        def lut_and_render():
            res = expect_ok(caller.command("import_asset", {"color_preset": COLOR_PRESETS[0][0], "apply_to": "all"}))
            assert res["applied_to"], res
            res = expect_ok(caller.command("export", {"format": "mp4", "frames": e2e.MP4_FRAMES}, 600))
            assert caller.asset_bytes(res["asset_id"])[4:8] == b"ftyp"
            assert res["frames"] == [0, 47], res
            res = expect_ok(caller.command("save"))
            assert res["saved"] is True, res
            hopped = "Showed the Color page once" in window_log()
            print("  first still export: %s" % ("Resolve needed the Color page shown once (the plugin did it)"
                                               if hopped else "worked without showing the Color page"), flush=True)

        checks.check("LUT, a 2 s MP4 render and save in the app", lut_and_render)

        def untitled_project():
            # Close the throwaway project: Resolve shows an unsaved Untitled Project. save must refuse (no
            # Save dialog); opening the throwaway again closes the empty untitled project first (no dialog).
            res = expect_ok(caller.command("run", {"code":
                "project_manager.CloseProject(project)\nresult = project_manager.GetCurrentProject().GetName()"}))
            res2 = caller.command("save")
            assert res2["status"] == "failed" and "never been saved" in res2["error"], res2
            res3 = expect_ok(caller.command("open", {"project": project_name}))
            assert res3["project"] == project_name, res3
            print("  unsaved project %r: save refused, open closed it first" % res["value"], flush=True)

        checks.check("save and open over an unsaved project, in the app", untitled_project)

        def approve_in_the_request_window():
            # Turn on Ask before running code, and have the request window's
            # own Run code button clicked (from code, on the window's thread)
            # whenever a request shows, for the next 40 seconds.
            expect_ok(caller.command("run", {"code": "\n".join([
                "import threading, time",
                "from nolgia_resolve import panel",
                "win = panel.current",
                "win.controller.set_setting('ask_before_run', True)",
                "stop_at = time.time() + 40",
                "def click():",
                "    req = win.request_win",
                "    if req is not None and win.request_shown:",
                "        req.GetItems()['Approve'].Click()",
                "    if time.time() < stop_at:",
                "        threading.Timer(1.0, lambda: win.controller.events.put(click)).start()",
                "threading.Timer(1.0, lambda: win.controller.events.put(click)).start()",
                "result = True",
            ])}))
            res = expect_ok(caller.command("run", {"code": "result = 6 * 7"}))
            assert res["value"] == 42, res
            log = window_log()
            assert "Asked the person: Your agent wants to run Python in DaVinci Resolve." in log, log[-2000:]
            expect_ok(caller.command("run", {"code":
                "from nolgia_resolve import panel\npanel.current.controller.set_setting('ask_before_run', False)"}))

        checks.check("Ask before running code: Run code in the request window", approve_in_the_request_window)

        def close_from_code():
            sid = [s for s in caller.http("GET", "/v1/bridge/sessions")[1]["sessions"]
                   if s["instance_id"] == env["NOLGIA_INSTANCE_ID"]][0]["id"]
            res = expect_ok(caller.command("run", {"code": "import nolgia_resolve\nresult = nolgia_resolve.close_window()"}))
            assert res["value"] is True, res
            end = time.time() + 60
            while time.time() < end and not os.path.exists(done):
                time.sleep(0.5)
            with open(done, encoding="utf-8") as handle:
                ending = handle.read()
            print("  the in-app script: " + ending.replace("\n", " | "), flush=True)
            assert ending.startswith("ended"), ending
            if prod:
                live = [s["id"] for s in caller.http("GET", "/v1/bridge/sessions")[1]["sessions"]]
                assert sid not in live, "the session is still listed as live"
            else:
                deleted = [d["session_id"] for d in caller.state()["deleted_sessions"]]
                assert sid in deleted, deleted
            assert "The NOLGIA window closed; switching off." in window_log()

        checks.check("closing the window switches off", close_from_code)
    except SystemExit:
        pass
    finally:
        if not os.path.exists(done):
            # Leave nothing open on screen: close the window through the plugin.
            try:
                caller.command("run", {"code": "import nolgia_resolve\nresult = nolgia_resolve.close_window()"}, 30)
            except Exception as err:
                print("  could not close the NOLGIA window (%s); close it by hand" % err, flush=True)
        if proc.poll() is None:
            proc.kill()
        if server is not None:
            server.stop()
        print("  window log:\n    " + "\n    ".join(window_log().splitlines()[-25:]), flush=True)
        if prod and not opts.keep_assets:
            def delete_assets():
                deleted = caller.delete_assets()
                for asset_id, name, outcome in deleted:
                    print("  deleted %s (%s): %s" % (asset_id, name, outcome), flush=True)
                assert all(d[2].endswith("then GET 404") for d in deleted), deleted

            checks.check("delete the %d assets this run made" % len({a for a, _ in caller.assets}), delete_assets)

    def cleanup():
        res = e2e.helper(host, work, "teardown", {"name": project_name, "back": state.get("previous")})
        assert res["ok"] and res["left"] == [], res
        if os.path.isdir(luts):
            for name in os.listdir(luts):
                if state["luts_before"] is None or name not in state["luts_before"]:
                    os.remove(os.path.join(luts, name))
            if not os.listdir(luts) and state["luts_before"] is None:
                os.rmdir(luts)
        if opts.uninstall:
            for name in ("NOLGIA.py", "nolgia_resolve.zip"):
                os.remove(os.path.join(utility, name))

    checks.check("clean up: project and LUTs", cleanup)
    return e2e.finish(checks, work, opts, started)


if __name__ == "__main__":
    sys.exit(main())
