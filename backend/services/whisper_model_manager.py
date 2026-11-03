"""
Whisper model management service (faster-whisper)

Responsible for downloading, status checking, and deleting Systran/faster-whisper models. Models are pulled from HuggingFace
and cached uniformly in `<data_dir>/whisper-models` (HF_HOME is set by whisper_runtime).
Dependencies (huggingface_hub) come from the runtime installation directory; all related imports are deferred to inside functions.

Download sources (AUTOCLIP_WHISPER_MODEL_SOURCE, RC156 Win QA #6):
- auto (default): try huggingface.co first, and fall back to hf-mirror.com on failure (domestic networks often cannot reach the official source).
- huggingface: use only the official source; hf-mirror: use only the mirror.
If the user has set HF_ENDPOINT themselves, respect it and do not switch automatically. When using the mirror, hf_xet must be disabled:
xet storage connects directly to cas-server.xethub.hf.co, bypassing HF_ENDPOINT (QA measured 401).
"""
import logging
import os
import threading
import time
from typing import Dict, List, Optional
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

from . import whisper_runtime

logger = logging.getLogger(__name__)


def _silence_download_progress() -> None:
    """Disable HuggingFace / tqdm progress bars.

    PYTHON-FASTAPI-H / A: When desktop stdout is not a console, tqdm.status_printer
    writing ``\\r`` throws OSError / BrokenPipeError, and the download is interrupted.
    """
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ.setdefault("TQDM_DISABLE", "1")
    try:
        from huggingface_hub.utils import disable_progress_bars
        disable_progress_bars()
    except Exception:  # noqa: BLE001
        pass


OFFICIAL_ENDPOINT = "https://huggingface.co"
MIRROR_ENDPOINT = "https://hf-mirror.com"
MODEL_SOURCES = ("auto", "huggingface", "hf-mirror")


def model_source() -> str:
    value = (os.environ.get("AUTOCLIP_WHISPER_MODEL_SOURCE") or "auto").strip().lower()
    return value if value in MODEL_SOURCES else "auto"


def download_plan() -> List[tuple]:
    """[(source label, endpoint)] in the order they are tried."""
    source = model_source()
    if source == "huggingface":
        return [("huggingface", OFFICIAL_ENDPOINT)]
    if source == "hf-mirror":
        return [("hf-mirror", MIRROR_ENDPOINT)]
    custom = (os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    if custom:
        return [("custom", custom)]
    return [("huggingface", OFFICIAL_ENDPOINT), ("hf-mirror", MIRROR_ENDPOINT)]


def _disable_xet() -> None:
    """hf_xet downloads bypass HF_ENDPOINT; a mirror only works over plain HTTP.

    huggingface_hub reads the constant at call time, the env var only at import.
    Process-wide on purpose: plain HTTP also works against huggingface.co.
    """
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    try:
        from huggingface_hub import constants
        constants.HF_HUB_DISABLE_XET = True
    except Exception:  # noqa: BLE001
        pass


def canonical_model_name(model_name: str) -> str:
    # SpeechRecognitionConfig is compatible with the legacy `large` alias.
    return "large-v3" if model_name == "large" else model_name


class ModelStatus(str, Enum):
    AVAILABLE = "available"      # Runtime ready, downloadable
    DOWNLOADING = "downloading"  # Downloading
    DOWNLOADED = "downloaded"    # Downloaded
    ERROR = "error"              # Error (usually runtime not installed)
    NOT_FOUND = "not_found"


@dataclass
class ModelInfo:
    name: str
    size: str
    size_bytes: int
    description: str
    accuracy: str
    speed: str
    status: ModelStatus
    repo_id: str = ""
    download_progress: Optional[int] = None
    local_path: Optional[str] = None
    error_message: Optional[str] = None
    source: Optional[str] = None  # The source actually used for download: huggingface / hf-mirror / custom


# Model name -> HuggingFace repo + display info (faster-whisper / CTranslate2 models)
_MODELS = {
    "tiny": {
        "repo_id": "Systran/faster-whisper-tiny",
        "size": "~75 MB", "size_bytes": 75 * 1024 * 1024,
        "description": "Fastest, lower accuracy, suitable for quick previews", "accuracy": "Low", "speed": "Fastest",
    },
    "base": {
        "repo_id": "Systran/faster-whisper-base",
        "size": "~145 MB", "size_bytes": 145 * 1024 * 1024,
        "description": "A balanced choice, recommended for daily use", "accuracy": "Medium", "speed": "Fast",
    },
    "small": {
        "repo_id": "Systran/faster-whisper-small",
        "size": "~488 MB", "size_bytes": 488 * 1024 * 1024,
        "description": "Good accuracy, suitable for important content", "accuracy": "Good", "speed": "Medium",
    },
    "medium": {
        "repo_id": "Systran/faster-whisper-medium",
        "size": "~1.5 GB", "size_bytes": 1500 * 1024 * 1024,
        "description": "High accuracy, suitable for professional use", "accuracy": "High", "speed": "Slower",
    },
    "large-v3": {
        "repo_id": "Systran/faster-whisper-large-v3",
        "size": "~3 GB", "size_bytes": 3000 * 1024 * 1024,
        "description": "Highest accuracy", "accuracy": "Highest", "speed": "Slowest",
    },
}


def repo_id_for(model_name: str) -> Optional[str]:
    cfg = _MODELS.get(model_name)
    return cfg["repo_id"] if cfg else None


def _snapshot_is_complete(snapshot: Path) -> bool:
    """HF creates the snapshot before the weights finish downloading; the directory existing does not mean offline transcription is possible."""
    try:
        required = [snapshot / name for name in ("model.bin", "config.json", "tokenizer.json")]
        vocabulary = list(snapshot.glob("vocabulary.*"))
        return (
            all(path.is_file() and path.stat().st_size > 0 for path in required)
            and any(path.is_file() and path.stat().st_size > 0 for path in vocabulary)
        )
    except OSError:
        return False


class WhisperModelManager:
    def __init__(self):
        self.model_configs = _MODELS
        # model_name -> {"status","progress","error"}
        self._download_state: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    # ---- Paths / status ----
    def _model_cache_dir(self, model_name: str) -> Path:
        repo = self.model_configs[model_name]["repo_id"]
        # HF cache directory naming: models--<org>--<name>
        return whisper_runtime.get_models_dir() / "hub" / ("models--" + repo.replace("/", "--"))

    def get_local_model_path(self, model_name: str) -> Optional[Path]:
        model_name = canonical_model_name(model_name)
        if model_name not in self.model_configs:
            return None
        d = self._model_cache_dir(model_name)
        snaps = d / "snapshots"
        try:
            candidates = sorted(snaps.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
            ref = d / "refs" / "main"
            if ref.is_file():
                revision = ref.read_text(encoding="utf-8").strip()
                # Only select a snapshot within the current cache; do not use arbitrary paths.
                candidates.sort(key=lambda path: path.name != revision)
            return next((path for path in candidates if _snapshot_is_complete(path)), None)
        except OSError:
            return None

    def _is_downloaded(self, model_name: str) -> bool:
        return self.get_local_model_path(model_name) is not None

    def _check_model_status(self, model_name: str) -> ModelStatus:
        with self._lock:
            st = self._download_state.get(model_name)
        if st and st.get("status") == "downloading":
            return ModelStatus.DOWNLOADING
        if st and st.get("status") == "error":
            return ModelStatus.ERROR
        if self._is_downloaded(model_name):
            return ModelStatus.DOWNLOADED
        if not whisper_runtime.is_installed():
            return ModelStatus.ERROR  # Runtime not installed, so the model is unusable too
        return ModelStatus.AVAILABLE

    def _info(self, model_name: str) -> ModelInfo:
        cfg = self.model_configs[model_name]
        status = self._check_model_status(model_name)
        with self._lock:
            st = self._download_state.get(model_name, {})
        return ModelInfo(
            name=model_name,
            size=cfg["size"], size_bytes=cfg["size_bytes"],
            description=cfg["description"], accuracy=cfg["accuracy"], speed=cfg["speed"],
            status=status, repo_id=cfg["repo_id"],
            download_progress=st.get("progress"),
            local_path=str(self._model_cache_dir(model_name)) if status == ModelStatus.DOWNLOADED else None,
            error_message=st.get("error"),
            source=st.get("source"),
        )

    def get_all_models_info(self) -> List[ModelInfo]:
        return [self._info(name) for name in self.model_configs]

    def get_model_info(self, model_name: str) -> Optional[ModelInfo]:
        if model_name not in self.model_configs:
            return None
        return self._info(model_name)

    # ---- Download (background thread, non-blocking) ----
    async def download_model(self, model_name: str) -> str:
        """Start a background download. Returns "installed" or "downloading", never "done".

        The download finishes (or fails) later: callers poll get_model_info (RC156 Win QA #5).
        """
        if model_name not in self.model_configs:
            raise ValueError(f"Unsupported model: {model_name}")
        if not whisper_runtime.is_installed():
            raise RuntimeError("Please install the Whisper runtime first")
        if self._is_downloaded(model_name):
            # A complete resumed cache must recover a previous download error
            # immediately, without requiring an application restart.
            with self._lock:
                st = self._download_state.get(model_name, {})
                if st.get("status") != "downloading":
                    self._download_state[model_name] = {
                        "status": "downloaded", "progress": 100, "error": None,
                    }
            return "installed"
        with self._lock:
            st = self._download_state.get(model_name)
            if st and st.get("status") == "downloading":
                return "downloading"
            self._download_state[model_name] = {"status": "downloading", "progress": 0, "error": None}
        threading.Thread(
            target=self._download_blocking, args=(model_name,),
            name=f"whisper-dl-{model_name}", daemon=True,
        ).start()
        return "downloading"

    def ensure_downloaded(self, model_name: str, poll_seconds: float = 1.0) -> Path:
        """Blocking: the local snapshot, downloading it first (same sources/fallback) when missing.

        Transcription must never let faster-whisper fetch the model itself: that path ignores
        the mirror fallback and leaves no status for the UI.
        """
        model_name = canonical_model_name(model_name)
        if model_name not in self.model_configs:
            raise ValueError(f"Unsupported model: {model_name}")
        local = self.get_local_model_path(model_name)
        if local is not None:
            return local
        with self._lock:
            st = self._download_state.get(model_name)
            running = bool(st and st.get("status") == "downloading")
            if not running:
                self._download_state[model_name] = {"status": "downloading", "progress": 0, "error": None}
        if running:
            while True:
                with self._lock:
                    st = self._download_state.get(model_name) or {}
                if st.get("status") != "downloading":
                    break
                time.sleep(poll_seconds)
        else:
            self._download_blocking(model_name)
        local = self.get_local_model_path(model_name)
        if local is None:
            with self._lock:
                error = (self._download_state.get(model_name) or {}).get("error")
            raise RuntimeError(error or f"Whisper model {model_name} download incomplete")
        return local

    def _download_blocking(self, model_name: str) -> None:
        repo_id = self.model_configs[model_name]["repo_id"]
        try:
            whisper_runtime.ensure_on_path()
            from huggingface_hub import snapshot_download
            _silence_download_progress()
            plan = download_plan()
            failures = []
            for index, (source, endpoint) in enumerate(plan):
                if endpoint != OFFICIAL_ENDPOINT:
                    _disable_xet()
                logger.info("Starting download of Whisper model %s (%s, source %s)", model_name, repo_id, source)
                try:
                    snapshot = snapshot_download(
                        repo_id=repo_id,
                        cache_dir=str(whisper_runtime.get_models_dir() / "hub"),
                        endpoint=endpoint,
                    )
                    if not _snapshot_is_complete(Path(snapshot)):
                        raise RuntimeError("Whisper model files are incomplete. Please re-download the model and try again.")
                except Exception as e:  # noqa: BLE001
                    failures.append(f"{source}: {type(e).__name__}: {e}")
                    if index + 1 < len(plan):
                        logger.warning("Whisper model %s failed to download from %s (%s), switching to %s",
                                       model_name, source, type(e).__name__, plan[index + 1][0])
                        continue
                    if len(failures) > 1:
                        raise RuntimeError("; ".join(failures)) from e
                    raise
                with self._lock:
                    self._download_state[model_name] = {
                        "status": "downloaded", "progress": 100, "error": None, "source": source,
                    }
                logger.info("Whisper model %s download complete (source %s)", model_name, source)
                return
        except Exception as e:  # noqa: BLE001
            # Handled failures are logged only as warning: LoggingIntegration(event_level=ERROR) + exc_info
            # would send recoverable errors like tqdm writing to the console to Sentry.
            logger.warning(
                "Failed to download Whisper model %s: %s: %s",
                model_name, type(e).__name__, e,
            )
            with self._lock:
                self._download_state[model_name] = {"status": "error", "progress": 0, "error": str(e)}

    def download_status(self, model_name: str) -> Optional[str]:
        """In-process download state only ("downloading" / "downloaded" / "error"); no disk or network."""
        with self._lock:
            return (self._download_state.get(canonical_model_name(model_name)) or {}).get("status")

    def get_download_progress(self, model_name: str) -> Optional[int]:
        with self._lock:
            st = self._download_state.get(model_name)
        if not st or st.get("status") == "downloaded":
            return 100 if self._is_downloaded(model_name) else None
        return st.get("progress")

    def cancel_download(self, model_name: str) -> bool:
        # snapshot_download is not easy to interrupt; this only clears the state, and downloaded shards are kept for resuming
        with self._lock:
            if model_name in self._download_state and self._download_state[model_name].get("status") == "downloading":
                self._download_state[model_name] = {"status": "available", "progress": 0, "error": None}
                return True
        return False

    def delete_model(self, model_name: str) -> bool:
        if model_name not in self.model_configs:
            return False
        try:
            import shutil
            d = self._model_cache_dir(model_name)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            with self._lock:
                self._download_state.pop(model_name, None)
            logger.info(f"Whisper model {model_name} deleted")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to delete Whisper model {model_name}: {e}")
            return False


_model_manager: Optional[WhisperModelManager] = None


def get_model_manager() -> WhisperModelManager:
    global _model_manager
    if _model_manager is None:
        _model_manager = WhisperModelManager()
    return _model_manager