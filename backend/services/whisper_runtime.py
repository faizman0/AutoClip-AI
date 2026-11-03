"""
Whisper runtime management (desktop mode, installed on demand)

The desktop installer does not include Whisper by default (the runtime + models take up space, and there is no need to make every user carry it). In the settings page,
users can decide for themselves whether to install it and which model to download. The backend uses faster-whisper (CTranslate2, which does not depend on
PyTorch; the runtime is ~200-400MB, several times faster than the official whisper, and cross-platform).

Design points:
- Install into a "user-writable directory" `<data_dir>/whisper-runtime`, not inside the .app bundle
  (/Applications is usually read-only, and writing to it would break code signing).
- Install with pip using the "portable Python that is currently running the backend" (sys.executable), to ensure the interpreter is consistent.
- Model cache is stored in `<data_dir>/whisper-models` (consolidated via HF_HOME).
- All imports of faster_whisper / huggingface_hub are deferred to inside functions, to avoid the build-time
  dependency scanner treating them as missing dependencies and causing the packaging to fail.
"""

import os
import sys
import shutil
import logging
import threading
import subprocess
import queue
import time
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Runtime packages to install (faster-whisper brings along ctranslate2, onnxruntime, av, huggingface_hub, etc.,
# but not PyTorch)
# PyAV 19 removed av.open(metadata_errors=...), still used by faster-whisper.
#
# Install only prebuilt wheels (--only-binary=:all:). When versions are not pinned, if a package's page on a mirror or the official source cannot be reached,
# or the download is interrupted, pip falls back to very old versions, and those versions only have source packages on cp313, so it starts compiling on the spot
# av (needs FFmpeg dev libraries) and tokenizers (needs Rust), spinning for over an hour on Windows and then exiting with code 1
# (1.5.7 Windows acceptance). On Windows, pin the packages with native code to versions confirmed to have cp313 win_amd64 wheels;
# on other platforms, only set an upper bound, and --only-binary picks the latest wheel usable on this machine (older macOS will pick
# older av / onnxruntime versions that do have wheels).
WHISPER_PACKAGES = [
    "faster-whisper==1.2.1",
    "av>=11,<19",
    'av==18.1.0; sys_platform == "win32"',
    'ctranslate2==4.8.2; sys_platform == "win32"',
    'onnxruntime==1.31.0; sys_platform == "win32"',
    'tokenizers==0.23.3; sys_platform == "win32"',
]
# pip arguments, source order, durations, and silent timeouts are all in pip_install, shared with the person recognition component.
from backend.services import pip_install  # noqa: E402
from backend.services.pip_install import (  # noqa: E402,F401
    PIP_FLAGS as WHISPER_PIP_FLAGS, PIP_MIRROR_TIMEOUT_SECONDS, PIP_OFFICIAL_TIMEOUT_SECONDS,
    PIP_STALL_SECONDS, NO_WHEEL_EXIT, SOURCE_BUILD_EXIT, TIMEOUT_EXIT,
)
# Runtime core module (used to detect whether it is already installed)
WHISPER_IMPORT_NAME = "faster_whisper"
_runtime_import_error = ""


def _data_dir() -> Path:
    try:
        from backend.core.desktop_config import get_desktop_data_dir
        return Path(get_desktop_data_dir())
    except Exception:
        return Path(os.getenv("AUTOCLIP_DATA_DIR", str(Path.home() / "Library/Application Support/AutoClip")))


def get_install_dir() -> Path:
    d = _data_dir() / "whisper-runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_models_dir() -> Path:
    d = _data_dir() / "whisper-models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _install_error_path() -> Path:
    return _data_dir() / "whisper-install-error.txt"


def _read_install_error() -> str:
    """A one-line explanation of the last installation failure. In-memory state is lost after a process restart, but the settings page still needs to show "Retry install"."""
    try:
        return _install_error_path().read_text(encoding="utf-8").strip()[:500]
    except Exception:
        return ""


def _write_install_error(message: str) -> None:
    try:
        path = _install_error_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((message or "Installation failed")[:500], encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to record the reason for Whisper installation failure: {e}")


def _clear_install_error() -> None:
    try:
        _install_error_path().unlink(missing_ok=True)
    except Exception:
        pass


def ensure_on_path() -> None:
    """Add the runtime directory to sys.path, and consolidate the model cache directory into HF_HOME."""
    install_dir = str(get_install_dir())
    if install_dir not in sys.path:
        # The person recognition directory (framing-runtime) must come first: both sides ship a copy of numpy, and mixing them causes cv2 import failures (PYTHON-FASTAPI-40).
        framing_dir = str(_data_dir() / "framing-runtime")
        index = sys.path.index(framing_dir) + 1 if framing_dir in sys.path else 0
        sys.path.insert(index, install_dir)
    # Cache models uniformly in the data directory for easy management/uninstallation
    os.environ.setdefault("HF_HOME", str(get_models_dir()))
    # Desktop stdout is often not a console; tqdm writing \r progress bars causes OSError / BrokenPipe
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ.setdefault("TQDM_DISABLE", "1")
    # faster-whisper needs ffmpeg to decode audio: add the directory containing the bundled ffmpeg to PATH
    ffmpeg_path = os.getenv("AUTOCLIP_FFMPEG_PATH")
    if ffmpeg_path:
        ffmpeg_dir = str(Path(ffmpeg_path).parent)
        if ffmpeg_dir not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")


def is_installed() -> bool:
    """Whether the runtime is ready (faster_whisper can be imported)."""
    global _runtime_import_error
    ensure_on_path()
    try:
        import importlib
        importlib.import_module(WHISPER_IMPORT_NAME)
        _runtime_import_error = ""
        return True
    except Exception as exc:
        missing_package = isinstance(exc, ModuleNotFoundError) and exc.name == WHISPER_IMPORT_NAME
        if not missing_package and not _runtime_import_error:
            logger.warning("Whisper runtime import failed: %s", type(exc).__name__)  # Status polling calls this repeatedly: log only the first time
        # The exception name only goes to the log, not to the settings page copy (RC156 #11).
        _runtime_import_error = "" if missing_package else (
            "Failed to load Whisper runtime dependencies. Please reinstall Whisper and restart AutoClip."
        )
        return False


# ---- Installation state (for frontend polling) ----
_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "status": "unknown",   # not_installed | installing | installed | error
    "progress": 0,         # coarse-grained percentage
    "message": "",
    "log_tail": "",
}


def _set_state(**kw) -> None:
    with _state_lock:
        _state.update(kw)


def get_status() -> Dict[str, Any]:
    with _state_lock:
        st = dict(_state)
    # Do not interrupt while installation is in progress. Once installed, availability is determined by whether it can be imported.
    # Failure must leave error set: otherwise every poll overwrites it with not_installed, and the settings page's "Retry install" never appears.
    if st["status"] != "installing":
        if is_installed():
            st["status"] = "installed"
            st["progress"] = 100
        elif st["status"] == "error":
            pass
        else:
            remembered = _runtime_import_error or _read_install_error()
            if remembered:
                st["status"] = "error"
                st["message"] = remembered
            else:
                st["status"] = "not_installed"
    st["platform_supported"] = True  # faster-whisper is cross-platform
    st["packages"] = WHISPER_PACKAGES
    return st


def pip_command(index_url: Optional[str]) -> list[str]:
    return pip_install.command(WHISPER_PACKAGES, str(get_install_dir()), index_url)


def index_plan(index_url: Optional[str]) -> list[tuple[str, Optional[str]]]:
    return pip_install.index_plan(index_url)


def _stream(cmd: list[str], timeout: float, stall_timeout: float, label: str = "") -> tuple[int, list[str]]:
    progress = pip_install.PipProgress(label)
    if label:
        _set_state(message=f"Connecting to {pip_install.SOURCE_NAMES.get(label, label)}…")

    def on_line(line: str, lines: list[str]) -> None:
        step = progress.feed(line)
        if step:
            _set_state(progress=step[0], message=step[1])
        _set_state(log_tail="\n".join(lines[-12:]))

    return pip_install.stream(cmd, timeout, stall_timeout, on_line, owner="whisper")


def _failure_message(code: int, failures: list[str]) -> str:
    message = pip_install.failure_message(code, failures, " Whisper ")
    if code == NO_WHEEL_EXIT:
        message = message.removesuffix(".") + ", or use a video with subtitles instead."
    return message


def _do_install(index_url: Optional[str]) -> None:
    _set_state(status="installing", progress=5, message="Preparing installation…", log_tail="", error_code="")
    try:
        code, failures = pip_install.run_plan(
            index_plan(index_url), pip_command,
            lambda cmd, timeout, stall, label: _stream(cmd, timeout, stall, label=label)[0],
            on_switch=lambda _old, new, _why: _set_state(message=f"The official source did not work, switching to {pip_install.SOURCE_NAMES.get(new, new)}…"),
            what="Whisper runtime",
        )
        if code == 0 and is_installed():
            _clear_install_error()
            _set_state(status="installed", progress=100, message="Installation complete")
            logger.info("Whisper runtime installation complete")
            return
        if code == pip_install.CANCEL_EXIT:
            _set_state(status="not_installed", progress=0, message="Installation canceled")
            logger.info("Whisper runtime installation canceled")
            return
        message = _failure_message(code, failures) if code != 0 else "Installation finished, but the Whisper runtime is still unavailable"
        _set_state(error_code=pip_install.error_code(code, failures) if code != 0 else "not_usable")
        _write_install_error(message)
        _set_state(status="error", message=message)
        logger.error("Whisper runtime installation failed: %s", "; ".join(failures) or message)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Exception installing Whisper runtime: {e}", exc_info=True)
        message = f"Installation exception: {e}"
        _write_install_error(message)
        _set_state(status="error", message=message)


def _bump_progress(min_v: int, max_v: int, message: str) -> None:
    with _state_lock:
        cur = _state.get("progress", 0)
        _state["progress"] = max(min_v, min(max_v, cur + 2))
        _state["message"] = message


def start_install(index_url: Optional[str] = None) -> Dict[str, Any]:
    with _state_lock:
        if _state["status"] == "installing":
            return {"started": False, "message": "Installation in progress"}
        _state.update(status="installing", progress=0, message="Checking runtime…", log_tail="")
    if is_installed():
        _set_state(status="installed", progress=100, message="Installed")
        return {"started": False, "message": "Installed"}
    # None is passed to index_plan: auto tries official first then mirror; if PIP_INDEX_URL is set, use only that.
    threading.Thread(target=_do_install, args=(index_url,), name="whisper-install", daemon=True).start()
    return {"started": True, "message": "Installation started"}


def uninstall() -> Dict[str, Any]:
    with _state_lock:
        if _state["status"] == "installing":
            return {"success": False, "message": "Installation in progress, cannot uninstall"}
    install_dir = get_install_dir()
    try:
        shutil.rmtree(install_dir, ignore_errors=True)
        # Remove from sys.modules so this process can no longer import them
        for mod in [m for m in list(sys.modules) if m.startswith("faster_whisper") or m.startswith("ctranslate2")]:
            sys.modules.pop(mod, None)
        p = str(install_dir)
        if p in sys.path:
            sys.path.remove(p)
        _clear_install_error()
        _set_state(status="not_installed", progress=0, message="Uninstalled")
        return {"success": True, "message": "Whisper runtime uninstalled"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to uninstall Whisper runtime: {e}")
        return {"success": False, "message": str(e)}


def cancel_install() -> bool:
    """Stop a running install (kills pip). The install thread then records "Installation canceled"."""
    from backend.services import pip_install
    return pip_install.cancel("whisper") > 0