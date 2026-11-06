"""Advanced packaging components (desktop mode, installed on demand).

Magazine-style / street templates use Playwright to capture HTML overlays. Podcast templates are not in this version. The desktop installer does not include this runtime by default
(the Python package is about 40 MB, plus roughly 100 MB for headless Chromium). When it is not installed properly, the final video falls back to classic (ASS),
so output is never blocked.

The installation location follows the same convention as Whisper / person recognition:

- Install into the user-writable directory `<data_dir>/packaging-runtime`, not into the .app (to avoid read-only issues and breaking code signing).
- Use the current backend's Python (`sys.executable`) to run `pip install --target`.
- Put the browser binary under that directory in `browsers/`, and set `PLAYWRIGHT_BROWSERS_PATH`.
- The pip source follows `AUTOCLIP_PIP_INDEX` (default `auto`): try the official PyPI first, and after a silent timeout switch to the Tsinghua mirror.
  `pypi` uses only the official source, `mirror` uses only the mirror. If `PIP_INDEX_URL` is already set, respect it and do not switch automatically.
- Browser downloads follow `AUTOCLIP_PLAYWRIGHT_DOWNLOAD_SOURCE` (default `auto`): try the official CDN first, and after a timeout switch to
  npmmirror. If `PLAYWRIGHT_DOWNLOAD_HOST` or `AUTOCLIP_PLAYWRIGHT_DOWNLOAD_HOST` pins the address, do not switch.
  If Chrome or Edge already exists on the system, use `channel='chrome'/'msedge'` instead of downloading
  chromium-headless-shell, and do not access the download mirror. On Windows, prefer Edge; on other systems, prefer Chrome.
"""
from __future__ import annotations

import importlib
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.services.studio.templates.spec import HTML_TEMPLATE_IDS as _HTML_TEMPLATE_IDS

logger = logging.getLogger(__name__)

PACKAGES = ["playwright>=1.49,<2"]
IMPORT_NAME = "playwright"
BROWSER_PACKAGE = "chromium-headless-shell"
# The download root documented by Playwright. npmmirror provides the same set of builds at `<host>/<revision>/<browser>`.
CN_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright"
OFFICIAL_PIP_INDEX = "https://pypi.org/simple"
# The default pip source for desktop build scripts. Used as a fallback when machines in mainland China cannot reach pypi.org.
CN_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_INDEX_MODES = ("auto", "pypi", "mirror")
PLAYWRIGHT_DOWNLOAD_MODES = ("auto", "official", "mirror")
# Routes from mainland China to the official source can blackhole. pip's own retries can spin for over ten minutes with no output.
# Switch to the mirror as soon as the official source goes silent; leave the mirror enough time for the actual download.
PIP_OFFICIAL_STALL_SECONDS = 40
PIP_OFFICIAL_TIMEOUT_SECONDS = 90
PIP_MIRROR_STALL_SECONDS = 120
PIP_MIRROR_TIMEOUT_SECONDS = 600
BROWSER_OFFICIAL_STALL_SECONDS = 45
BROWSER_OFFICIAL_TIMEOUT_SECONDS = 180
BROWSER_MIRROR_STALL_SECONDS = 180
BROWSER_MIRROR_TIMEOUT_SECONDS = 600
PIP_SOCKET_TIMEOUT = "15"
PIP_RETRIES = "1"
TIMEOUT_EXIT = 124
HTML_TEMPLATES = frozenset(_HTML_TEMPLATE_IDS)
CLASSIC = "classic"
# Older layouts are still ASS; do not mistake them for HTML templates when reading them.
_CLASSIC_ALIASES = frozenset({"", CLASSIC, "interview_zh", "podcast_en", "landscape", "none", "auto"})
_SHELL_PREFIXES = ("chromium_headless_shell-", "chromium-")
_SHELL_BINARIES = {
    "chrome", "chrome.exe", "chromium", "chromium.exe",
    "headless_shell", "headless_shell.exe",
}

_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "status": "unknown",  # not_installed | installing | installed | error
    "progress": 0,
    "message": "",
    "log_tail": "",
}
_runtime_import_error = ""


def _data_dir() -> Path:
    try:
        from backend.core.desktop_config import get_desktop_data_dir
        return Path(get_desktop_data_dir())
    except Exception:
        return Path(os.getenv("AUTOCLIP_DATA_DIR", str(Path.home() / "Library/Application Support/AutoClip")))


def install_root() -> Path:
    """Installation directory. Do not create it during probing, to avoid turning status polling into a write when the data directory is read-only."""
    return _data_dir() / "packaging-runtime"


def get_install_dir() -> Path:
    path = install_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def browsers_dir() -> Path:
    return install_root() / "browsers"


def get_browsers_dir() -> Path:
    path = browsers_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _error_path() -> Path:
    return _data_dir() / "packaging-install-error.txt"


def _read_install_error() -> str:
    try:
        return _error_path().read_text(encoding="utf-8").strip()[:500]
    except Exception:
        return ""


def _write_install_error(message: str) -> None:
    try:
        path = _error_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((message or "Installation failed")[:500], encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to record the reason for packaging component installation failure: %s", type(exc).__name__)


def _clear_install_error() -> None:
    try:
        _error_path().unlink(missing_ok=True)
    except Exception:
        pass


def _named_mode(value: str, allowed: tuple[str, ...], aliases: dict[str, str]) -> str | None:
    text = aliases.get(value, value)
    return text if text in allowed else None


def pip_index_mode() -> str:
    raw = (os.getenv("AUTOCLIP_PIP_INDEX") or "auto").strip().lower()
    return _named_mode(raw, PIP_INDEX_MODES, {
        "official": "pypi", "pypi.org": "pypi",
        "cn": "mirror", "china": "mirror", "tuna": "mirror", "tsinghua": "mirror",
    }) or "auto"


def pip_index_plan(explicit: str | None = None) -> list[tuple[str, str]]:
    """[(label, index url)], in order of attempts.

    Same shape as Whisper's download plan: explicit mode takes priority; if the user has set PIP_INDEX_URL, use only that;
    for auto, try the official index first, then the Tsinghua mirror.
    """
    if explicit and explicit.strip():
        return [("custom", explicit.strip())]
    mode = pip_index_mode()
    if mode == "pypi":
        return [("pypi", OFFICIAL_PIP_INDEX)]
    if mode == "mirror":
        return [("mirror", CN_PIP_INDEX)]
    custom = (os.getenv("PIP_INDEX_URL") or "").strip()
    if custom:
        return [("custom", custom)]
    return [("pypi", OFFICIAL_PIP_INDEX), ("mirror", CN_PIP_INDEX)]


def playwright_download_mode() -> str:
    raw = (os.getenv("AUTOCLIP_PLAYWRIGHT_DOWNLOAD_SOURCE") or "").strip().lower()
    chosen = _named_mode(raw, PLAYWRIGHT_DOWNLOAD_MODES, {
        "cdn": "official", "playwright": "official",
        "npmmirror": "mirror", "cn": "mirror", "china": "mirror",
    })
    if chosen:
        return chosen
    switch = (os.getenv("AUTOCLIP_PLAYWRIGHT_MIRROR") or "").strip().lower()
    if switch in {"0", "off", "false", "no"}:
        return "official"
    if switch in {"1", "on", "true", "yes", "cn"}:
        return "mirror"
    return "auto"


def browser_download_plan() -> list[tuple[str, str | None]]:
    """[(label, PLAYWRIGHT_DOWNLOAD_HOST)]. The official CDN host is None."""
    explicit = (os.getenv("PLAYWRIGHT_DOWNLOAD_HOST") or "").strip()
    override = (os.getenv("AUTOCLIP_PLAYWRIGHT_DOWNLOAD_HOST") or "").strip()
    pinned = explicit or override
    if pinned:
        return [("custom", pinned)]
    mode = playwright_download_mode()
    if mode == "official":
        return [("official", None)]
    if mode == "mirror":
        return [("mirror", CN_DOWNLOAD_HOST)]
    return [("official", None), ("mirror", CN_DOWNLOAD_HOST)]


def download_host() -> str | None:
    """The pinned browser download root. auto tries each one in turn; this returns None to avoid writing the mirror before the official source has been tried."""
    plan = browser_download_plan()
    if len(plan) == 1:
        return plan[0][1]
    return None


def ensure_environment() -> dict[str, Any]:
    """Consolidate the browser cache and domestic mirrors into the data directory. Call once before frame capture."""
    browsers = get_browsers_dir()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    host = download_host()
    if host:
        os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = host
    ensure_on_path()
    return {"browsers_path": str(browsers), "download_host": host}


def ensure_on_path() -> None:
    install_dir = str(install_root())
    if install_dir not in sys.path:
        sys.path.insert(0, install_dir)


def bundled_package_present() -> bool:
    """Since 1.5.7, the desktop installer bundles the playwright Python package (without browsers, W157-F7)."""
    try:
        import importlib.util
        spec = importlib.util.find_spec(IMPORT_NAME)
    except Exception:
        return False
    if spec is None or not spec.origin:
        return False
    try:
        return Path(install_root()) not in Path(spec.origin).resolve().parents
    except Exception:
        return True


def package_present() -> bool:
    return bundled_package_present() or (install_root() / IMPORT_NAME / "__init__.py").is_file()


def _playwright_importable() -> bool:
    global _runtime_import_error
    ensure_on_path()
    try:
        importlib.import_module(IMPORT_NAME)
    except Exception as exc:
        missing = isinstance(exc, ModuleNotFoundError) and exc.name == IMPORT_NAME
        if not missing and not _runtime_import_error:
            logger.warning("Packaging runtime import failed: %s", type(exc).__name__)
        _runtime_import_error = "" if missing else (
            "Failed to load packaging runtime dependencies. Please reinstall the advanced packaging components and restart AutoClip."
        )
        return False
    _runtime_import_error = ""
    return True


def _running_as_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def chromium_args() -> list[str]:
    args = ["--disable-dev-shm-usage", "--disable-extensions"]
    # If it must run as root in a container, the Chromium sandbox will refuse to start. Keep the sandbox for normal desktop users.
    if _running_as_root():
        args.append("--no-sandbox")
    return args


def _channel_order(platform: str) -> list[str]:
    # Windows desktop always has Edge; Chrome is more common on macOS / Linux.
    if platform == "win32":
        return ["msedge", "chrome"]
    return ["chrome", "msedge"]


def _channel_locations(platform: str, channel: str) -> list[str]:
    if platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        program = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        if channel == "msedge":
            relative = os.path.join("Microsoft", "Edge", "Application", "msedge.exe")
            return [os.path.join(program_x86, relative), os.path.join(program, relative), os.path.join(local, relative)]
        relative = os.path.join("Google", "Chrome", "Application", "chrome.exe")
        return [os.path.join(local, relative), os.path.join(program, relative), os.path.join(program_x86, relative)]
    if platform == "darwin":
        home = str(Path.home())
        if channel == "chrome":
            name = "Google Chrome.app/Contents/MacOS/Google Chrome"
        else:
            name = "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        return [str(Path("/Applications") / name), str(Path(home) / "Applications" / name)]
    if channel == "chrome":
        return ["google-chrome-stable", "google-chrome"]
    return ["microsoft-edge-stable", "microsoft-edge"]


def find_system_browser(
    platform: str | None = None,
    exists: Callable[[Path], bool] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> dict[str, str] | None:
    """System Chrome / Edge. If found, there is no need to download chromium-headless-shell."""
    system = platform or sys.platform
    exists = exists or (lambda path: path.is_file())
    which = which or shutil.which
    for channel in _channel_order(system):
        for location in _channel_locations(system, channel):
            path = Path(location)
            absolute = path.is_absolute() or (system == "win32" and len(location) > 2 and location[1] == ":")
            if absolute:
                if exists(path):
                    return {"channel": channel, "executable": str(path)}
                continue
            found = which(location)
            if found:
                return {"channel": channel, "executable": found}
    return None


def _shell_payload_present(root: Path) -> bool:
    if not root.is_dir():
        return False
    try:
        children = list(root.iterdir())
    except OSError:
        return False
    for child in children:
        if not child.is_dir() or not child.name.startswith(_SHELL_PREFIXES):
            continue
        try:
            for file in child.rglob("*"):
                if file.is_file() and file.name.lower() in _SHELL_BINARIES:
                    return True
        except OSError:
            continue
    return False


def headless_shell_installed() -> bool:
    return _shell_payload_present(browsers_dir())


def browser_plan() -> dict[str, Any]:
    found = find_system_browser()
    if found:
        return {
            "ready": True,
            "mode": "channel",
            "channel": found["channel"],
            "executable": found["executable"],
            "needs_download": False,
        }
    if headless_shell_installed():
        return {
            "ready": True,
            "mode": BROWSER_PACKAGE,
            "channel": None,
            "executable": None,
            "needs_download": False,
        }
    return {
        "ready": False,
        "mode": BROWSER_PACKAGE,
        "channel": None,
        "executable": None,
        "needs_download": True,
    }


def launch_kwargs() -> dict[str, Any]:
    """Arguments for `playwright.chromium.launch`. Prefer the system browser."""
    plan = browser_plan()
    kwargs: dict[str, Any] = {"headless": True, "args": chromium_args()}
    if plan["mode"] == "channel" and plan.get("channel"):
        kwargs["channel"] = plan["channel"]
        # On Mac, Chrome may be installed in ~/Applications; channel only recognizes the default location, so pass the detected path as well.
        if plan.get("executable"):
            kwargs["executable_path"] = plan["executable"]
    return kwargs


def missing_browser() -> bool:
    """playwright is available but there is no Chrome / Edge on this machine (and no downloaded headless shell)."""
    try:
        if not package_present() or not _playwright_importable():
            return False
        return not bool(browser_plan()["ready"])
    except Exception:
        return False


NO_BROWSER_WARNING = "Chrome or Edge browser not found. Magazine-style/street styles have been switched to the classic style. Install Google Chrome and re-export to use them."
NO_RUNTIME_WARNING = "Advanced packaging components are unavailable. Magazine-style/street styles have been switched to the classic style."


def downgrade_warning(reason: str | None) -> str | None:
    """The message shown to the user when downgrading due to missing_runtime (the frontend translates the Chinese key into 8 languages)."""
    if reason != "missing_runtime":
        return None
    return NO_BROWSER_WARNING if missing_browser() else NO_RUNTIME_WARNING


def is_installed() -> bool:
    if not package_present() or not _playwright_importable():
        return False
    return bool(browser_plan()["ready"])


def effective_template(requested: str | None) -> str:
    """Fall back to classic when the component is not ready. Any probing failure falls back to classic and does not throw an exception to the output."""
    name = (requested or "").strip().lower()
    if name in _CLASSIC_ALIASES or name not in HTML_TEMPLATES:
        return CLASSIC
    try:
        ready = is_installed()
    except Exception:
        logger.exception("Packaging runtime status probing failed, switching to classic")
        return CLASSIC
    return name if ready else CLASSIC


def blocks_output() -> bool:
    """Advanced packaging is optional. Missing components only downgrade the template and do not block output."""
    return False


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


def _public_failure(message: str) -> str:
    # The settings page polling will receive message. Raw exception text often contains local paths, so only write them to the log.
    return message


def get_status() -> dict[str, Any]:
    with _state_lock:
        status = dict(_state)
    if status["status"] != "installing":
        try:
            ready = is_installed()
        except Exception:
            logger.exception("Packaging runtime status probing failed")
            ready = False
            status["status"] = "error"
            status["message"] = _public_failure("Failed to read packaging component status. Please reinstall the advanced packaging components.")
        if ready:
            status["status"] = "installed"
            status["progress"] = 100
        elif status["status"] == "error":
            pass
        else:
            remembered = _runtime_import_error or _read_install_error()
            if remembered:
                status["status"] = "error"
                status["message"] = remembered
            else:
                status["status"] = "not_installed"
    try:
        plan = browser_plan()
    except Exception:
        logger.exception("Packaging browser probing failed")
        plan = {"ready": False, "mode": BROWSER_PACKAGE, "channel": None, "executable": None, "needs_download": True}
    host = None
    try:
        host = download_host()
    except Exception:
        logger.exception("Packaging download mirror probing failed")
    status["platform_supported"] = True
    status["packages"] = list(PACKAGES)
    status["browser_package"] = BROWSER_PACKAGE
    status["install_dir"] = str(install_root())
    status["browsers_path"] = str(browsers_dir())
    status["download_host"] = host
    status["browser"] = {key: plan.get(key) for key in ("mode", "channel", "needs_download", "ready")}
    status["fallback_template"] = CLASSIC
    status["blocks_output"] = blocks_output()
    return status


def _child_env() -> dict[str, str]:
    ensured = ensure_environment()
    env = os.environ.copy()
    root = str(get_install_dir())
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIP_PROGRESS_BAR"] = "off"
    env["PLAYWRIGHT_BROWSERS_PATH"] = ensured["browsers_path"]
    if ensured["download_host"]:
        env["PLAYWRIGHT_DOWNLOAD_HOST"] = ensured["download_host"]
    return env


def pip_command(index_url: str | None) -> list[str]:
    command = [
        sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
        # Socket timeouts cannot stop blackholed DNS / TCP. The process-level silent timeout in _stream is the backstop.
        "--timeout", PIP_SOCKET_TIMEOUT, "--retries", PIP_RETRIES,
        "--target", str(get_install_dir()), *PACKAGES,
    ]
    if index_url:
        command += ["--index-url", index_url]
    return command


def browser_install_command() -> list[str]:
    return [sys.executable, "-m", "playwright", "install", BROWSER_PACKAGE]


def _bump(min_value: int, max_value: int, message: str) -> None:
    with _state_lock:
        current = int(_state.get("progress") or 0)
        _state["progress"] = max(min_value, min(max_value, current + 2))
        _state["message"] = message


def _attempt_limits(kind: str, label: str) -> tuple[float, float]:
    short = label in {"pypi", "official"}
    if kind == "browser":
        if short:
            return (BROWSER_OFFICIAL_TIMEOUT_SECONDS, BROWSER_OFFICIAL_STALL_SECONDS)
        return (BROWSER_MIRROR_TIMEOUT_SECONDS, BROWSER_MIRROR_STALL_SECONDS)
    if short:
        return (PIP_OFFICIAL_TIMEOUT_SECONDS, PIP_OFFICIAL_STALL_SECONDS)
    return (PIP_MIRROR_TIMEOUT_SECONDS, PIP_MIRROR_STALL_SECONDS)


def _with_download_host(env: dict[str, str], host: str | None) -> dict[str, str]:
    copied = dict(env)
    if host:
        copied["PLAYWRIGHT_DOWNLOAD_HOST"] = host
    else:
        copied.pop("PLAYWRIGHT_DOWNLOAD_HOST", None)
    return copied


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    stdout = getattr(proc, "stdout", None)
    if stdout is not None:
        try:
            stdout.close()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _stream(
    command: list[str],
    env: dict[str, str],
    progress: tuple[int, int],
    timeout: float | None = None,
    stall_timeout: float | None = None,
) -> int:
    # It must stop even if no timeout is passed. On mainland China networks, a pip run with no output can spin for over ten minutes.
    if timeout is None:
        timeout = PIP_MIRROR_TIMEOUT_SECONDS
    if stall_timeout is None:
        stall_timeout = PIP_MIRROR_STALL_SECONDS
    logger.info("Packaging component: %s", " ".join(command))
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
    )
    assert proc.stdout is not None
    lines_out: queue.Queue[str | None] = queue.Queue()

    def _read() -> None:
        try:
            for line in iter(proc.stdout.readline, ""):
                lines_out.put(line)
        except Exception:
            logger.debug("Packaging component output read interrupted", exc_info=True)
        finally:
            lines_out.put(None)

    threading.Thread(target=_read, name="packaging-install-output", daemon=True).start()
    started = time.monotonic()
    last_output = started
    lines: list[str] = []
    while True:
        now = time.monotonic()
        waits: list[float] = []
        if timeout is not None:
            waits.append(started + timeout - now)
        if stall_timeout is not None:
            waits.append(last_output + stall_timeout - now)
        wait = min(waits) if waits else None
        if wait is not None and wait <= 0:
            logger.warning("Packaging component command silent timeout, terminated")
            _stop_process(proc)
            return TIMEOUT_EXIT
        try:
            item = lines_out.get(timeout=wait)
        except queue.Empty:
            logger.warning("Packaging component command silent timeout, terminated")
            _stop_process(proc)
            return TIMEOUT_EXIT
        if item is None:
            break
        last_output = time.monotonic()
        line = item.rstrip()
        if not line:
            continue
        lines.append(line)
        lines[:] = lines[-40:]
        low = line.lower()
        if "collecting" in low or "downloading" in low or "download" in low:
            _bump(progress[0], progress[1], line[:180])
        _set_state(log_tail="\n".join(lines[-12:]))
    return proc.wait()


def _run_plan(
    kind: str,
    plan: list[tuple[str, str | None]],
    env: dict[str, str],
    progress: tuple[int, int],
) -> tuple[int, list[str]]:
    code = 1
    failures: list[str] = []
    for index, (label, value) in enumerate(plan):
        hard, stall = _attempt_limits(kind, label)
        if kind == "browser":
            command = browser_install_command()
            attempt_env = _with_download_host(env, value)
        else:
            command = pip_command(value if isinstance(value, str) else None)
            attempt_env = env
        if index and failures:
            logger.warning("Packaging component %s failed from %s (%s), switching to %s", kind, plan[index - 1][0], failures[-1], label)
            _set_state(message="Official source unresponsive, switching to domestic mirror…")
        code = _stream(command, attempt_env, progress, timeout=hard, stall_timeout=stall)
        if code == 0:
            return 0, failures
        reason = "timeout" if code == TIMEOUT_EXIT else f"exit code {code}"
        failures.append(f"{label}: {reason}")
    return code, failures


def _failure_message(kind: str, code: int, failures: list[str]) -> str:
    timed_out = code == TIMEOUT_EXIT
    if len(failures) > 1:
        if kind == "browser":
            return "Browser download failed (official source unresponsive, domestic mirror also failed)"
        return "Installation failed (official PyPI unresponsive, domestic mirror also failed)"
    if kind == "browser":
        return "Browser download failed (timeout)" if timed_out else f"Browser download failed (exit code {code})"
    return "Installation failed (pip timeout)" if timed_out else f"Installation failed (pip exit code {code})"


def _do_install(index_url: str | None) -> None:
    _set_state(status="installing", progress=5, message="Preparing advanced packaging components…", log_tail="")
    try:
        env = _child_env()
        if not package_present() or not _playwright_importable():
            code, failures = _run_plan("pip", pip_index_plan(index_url), env, (10, 60))
            if code != 0:
                message = _public_failure(_failure_message("pip", code, failures))
                _write_install_error(message)
                _set_state(status="error", message=message)
                logger.error("Packaging component pip installation failed: %s", "; ".join(failures) or code)
                return
        plan = browser_plan()
        if plan["needs_download"]:
            _set_state(progress=65, message="Downloading headless browser…")
            code, failures = _run_plan("browser", browser_download_plan(), env, (65, 95))
            if code != 0:
                message = _public_failure(_failure_message("browser", code, failures))
                _write_install_error(message)
                _set_state(status="error", message=message)
                logger.error("chromium-headless-shell installation failed: %s", "; ".join(failures) or code)
                return
        if is_installed():
            _clear_install_error()
            _set_state(status="installed", progress=100, message="Installation complete")
            logger.info("Advanced packaging component installation complete, browser mode %s", browser_plan()["mode"])
        else:
            message = _public_failure("Installation finished, but the packaging component is still unavailable")
            _write_install_error(message)
            _set_state(status="error", message=message)
    except Exception as exc:  # noqa: BLE001
        logger.error("Exception installing advanced packaging component: %s", exc, exc_info=True)
        message = _public_failure("Installation exception. Please try again later, or continue using the classic template for output.")
        _write_install_error(message)
        _set_state(status="error", message=message)


def start_install(index_url: str | None = None) -> dict[str, Any]:
    with _state_lock:
        if _state["status"] == "installing":
            return {"started": False, "message": "Installation in progress"}
        _state.update(status="installing", progress=0, message="Checking runtime…", log_tail="")
    if is_installed():
        _set_state(status="installed", progress=100, message="Installed")
        return {"started": False, "message": "Installed"}
    # None is passed to pip_index_plan: auto tries official first then mirror; PIP_INDEX_URL uses only the user-specified source.
    threading.Thread(target=_do_install, args=(index_url,), name="packaging-install", daemon=True).start()
    return {"started": True, "message": "Installation started"}


def install_blocking(index_url: str | None = None) -> dict[str, Any]:
    """Synchronous installation used by speed tests and repair scripts. The settings page uses `start_install`."""
    if is_installed():
        _set_state(status="installed", progress=100, message="Installed")
        return get_status()
    _do_install(index_url)
    return get_status()


def uninstall() -> dict[str, Any]:
    with _state_lock:
        if _state["status"] == "installing":
            return {"success": False, "message": "Installation in progress, cannot uninstall"}
    root = install_root()
    try:
        shutil.rmtree(root, ignore_errors=True)
        if root.exists():
            return {"success": False, "message": "Uninstall failed"}
        removed = str(root)
        while removed in sys.path:
            sys.path.remove(removed)
        for name in [module for module in list(sys.modules) if module == IMPORT_NAME or module.startswith(IMPORT_NAME + ".")]:
            sys.modules.pop(name, None)
        _clear_install_error()
        global _runtime_import_error
        _runtime_import_error = ""
        _set_state(status="not_installed", progress=0, message="Uninstalled", log_tail="")
        return {"success": True, "message": "Advanced packaging components uninstalled"}
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to uninstall advanced packaging components: %s", type(exc).__name__)
        return {"success": False, "message": "Uninstall failed"}