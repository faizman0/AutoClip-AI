"""
Simplified pipeline adapter - integrates the new progress system
"""

import logging
import os
from typing import Dict, Any, Optional, Callable
from pathlib import Path

from backend.services.simple_progress import emit_progress, clear_progress
from backend.core.project_cancellation import ProjectDeleted
from backend.pipeline.failures import (
    PipelineFailure, HINT_LOWER_THRESHOLD, HINT_CHECK_FFMPEG, http_status_of,
    llm_key_failure, failure_from_speech_error, missing_subtitle_failure,
    cloud_transcription_failure,
    empty_timeline_failure,
)
from backend.pipeline.step1_outline import run_step1_outline
from backend.pipeline.step2_timeline import run_step2_timeline
from backend.pipeline.step3_scoring import run_step3_scoring
from backend.pipeline.step4_title import run_step4_title
from backend.core import llm_usage
from backend.pipeline.step5_clustering import run_step5_clustering
from backend.pipeline.step6_video import run_step6_video

logger = logging.getLogger(__name__)



STUDIO_TRANSCRIBING_MESSAGE = 'Generating subtitles'
STUDIO_PRODUCTION_MESSAGE = 'Producing a publish-ready final video'

# Transcription is the slow part of SUBTITLE (minutes for a long talk), so it gets almost the whole
# stage band: sub-steps 5 → 95 of SUBTITLE, i.e. the project bar moves from 10% to 24% with the audio.
# #301 used sub-steps 25 → 40, which compute_percent turns into only 13% → 16% of the project bar
# (SUBTITLE is the 10–25% band), so the home card sat at 14–16% for the whole run (RC156 #22).
TRANSCRIBE_START_SUBPERCENT = 5
TRANSCRIBE_DONE_SUBPERCENT = 100


def transcription_subpercent(fraction: float) -> float:
    """SUBTITLE sub-step for `fraction` of the audio transcribed."""
    return TRANSCRIBE_START_SUBPERCENT + 90 * max(0.0, min(1.0, fraction))


class TranscriptionProgress:
    """Show transcription progress as the share of audio already processed (RC156 Win QA #14).

    - Project progress bar (home card, project page): SUBTITLE follows the audio across most of
      its band (10% → 24% of the whole bar) instead of sitting on fixed checkpoints.
    - Studio (managed import): analysis shows "Generating subtitles · N%" with N = processed / total audio.
    Writes are throttled to whole-percent steps at most every `interval` seconds.
    """

    def __init__(self, project_id: str, interval: float = 2.0):
        self.project_id = project_id
        self.interval = interval
        self.last_percent = -1
        self.last_at = None

    def __call__(self, fraction: float) -> None:
        import time
        from backend.core.project_cancellation import checkpoint
        checkpoint(self.project_id)  # deleted mid-transcription: stop Whisper (RC156 #12)
        percent = int(fraction * 100)
        now = time.monotonic()
        if percent <= self.last_percent or (percent < 100 and self.last_at is not None and now - self.last_at < self.interval):
            return
        self.last_percent, self.last_at = percent, now
        emit_progress(self.project_id, "SUBTITLE", "Generating subtitles with AI...", subpercent=transcription_subpercent(fraction))
        self._studio(lambda analysis: analysis.update(message=STUDIO_TRANSCRIBING_MESSAGE, percent=percent))

    def _studio(self, update) -> None:
        self.studio_update(self.project_id, update)

    @staticmethod
    def studio_update(project_id: str, update) -> None:
        from backend.services.studio import store
        try:
            if not (store.directory(project_id) / 'metadata' / 'studio.json').is_file():
                return  # legacy project: never create Studio state
            def mutate(data):
                analysis = data.get('analysis')
                if isinstance(analysis, dict) and analysis.get('status') == 'running' and analysis.get('phase') == 'production':
                    update(analysis)
            store.change(project_id, mutate)
        except FileNotFoundError:
            raise
        except Exception as error:  # noqa: BLE001 - progress display never fails the pipeline
            logger.debug('Studio transcription progress skipped: %s', type(error).__name__)

    @classmethod
    def finish_studio(cls, project_id: str) -> None:
        def restore(analysis):
            if analysis.get('message') == STUDIO_TRANSCRIBING_MESSAGE:
                analysis['message'] = STUDIO_PRODUCTION_MESSAGE
                analysis.pop('percent', None)
        try:
            cls.studio_update(project_id, restore)
        except FileNotFoundError:
            pass

class SimplePipelineAdapter:
    """Simplified pipeline adapter, uses the fixed-stage progress system"""
    
    def __init__(self, project_id: str, task_id: str):
        self.project_id = project_id
        self.task_id = task_id

    def _prompt_files(self, project_dir: Path):
        """Select prompt/<category>/ by project type. The desktop side never passed this before, so the category directories were effectively unused."""
        from backend.core.shared_config import get_prompt_files
        import json

        category = "default"
        meta_path = project_dir / "project.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                category = meta.get("video_category") or "default"
            except Exception:  # noqa: BLE001
                pass
        if category == "default":
            try:
                from backend.core.database import SessionLocal
                from backend.models.project import Project
                db = SessionLocal()
                try:
                    row = db.query(Project).filter(Project.id == self.project_id).first()
                    if row is not None and row.project_type is not None:
                        category = row.project_type.value if hasattr(row.project_type, "value") else str(row.project_type)
                finally:
                    db.close()
            except Exception:  # noqa: BLE001
                pass
        logger.info(f"Using category prompt: {category}")
        return get_prompt_files(category)
        
    async def _generate_subtitle_automatically(self, video_path: str, metadata_dir: Path) -> Path:
        """
        Automatically generate a subtitle file
        
        Args:
            video_path: Video file path
            metadata_dir: Metadata directory
            
        Returns:
            The generated SRT file path, or None on failure
        """
        from backend.utils.speech_recognizer import (
            SpeechRecognitionError,
            generate_subtitle_for_video,
        )
        from backend.services.cloud_transcription import CloudTranscriptionError
        try:
            logger.info(f"Starting automatic subtitle generation for video {video_path}")
            
            # Update progress
            from backend.services.simple_progress import emit_progress
            emit_progress(self.project_id, "SUBTITLE", "Generating subtitles with AI...", subpercent=TRANSCRIBE_START_SUBPERCENT)
            
            # Use the currently selected transcription service to generate subtitles
            try:
                from pathlib import Path
                
                video_file_path = Path(video_path)
                if not video_file_path.exists():
                    logger.error(f"Video file does not exist: {video_path}")
                    return None
                
                logger.info("Attempting to generate subtitles using the selected transcription service")
                output_path = metadata_dir / f"{video_file_path.stem}.srt"
                from backend.utils.transcription_progress import reporting
                try:
                    with reporting(TranscriptionProgress(self.project_id)):
                        srt_path = generate_subtitle_for_video(
                            video_file_path,
                            output_path=output_path,
                            method="auto",
                            language="auto"
                        )
                finally:
                    TranscriptionProgress.finish_studio(self.project_id)
                
                if srt_path and srt_path.exists():
                    logger.info(f"Transcription subtitle generation succeeded: {srt_path}")
                    emit_progress(self.project_id, "SUBTITLE", "AI subtitle generation complete", subpercent=TRANSCRIBE_DONE_SUBPERCENT)
                    return srt_path
                logger.warning("Transcription subtitle generation failed")
                    
            except (SpeechRecognitionError, CloudTranscriptionError):
                raise
            except Exception as e:
                logger.warning(f"Transcription subtitle generation failed: {e}")
            
            logger.error("Subtitle generation failed")
            return None
            
        except (SpeechRecognitionError, CloudTranscriptionError):
            raise
        except Exception as e:
            logger.error(f"An error occurred during automatic subtitle generation: {e}")
            return None
        
    @staticmethod
    def _preflight_llm() -> None:
        """Before running any LLM step, confirm the provider is available; otherwise step1 will report an error for every chunk and hand out an empty outline.
        In AUTOCLIP_LLM_CACHE_DIR replay mode a real provider is not needed (backend/eval)."""
        if os.getenv("AUTOCLIP_LLM_CACHE_DIR"):
            return
        from backend.core.llm_manager import get_llm_manager
        info = get_llm_manager().get_current_provider_info()
        if info.get("available"):
            return
        name = info.get("display_name") or info.get("provider") or "not selected"
        model = info.get("model") or "-"
        raise llm_key_failure(
            "ANALYZE",
            f"No available LLM provider (current selection: {name} · {model}); missing API Key or local service address.",
        )

    def _find_clips_in_one_pass(self, srt_path: Path, metadata_dir: Path):
        """Titled clips from one model pass over the whole transcript, or None to use the legacy steps."""
        from backend.pipeline.clip_finder import find_clips
        from backend.pipeline.step3_scoring import resolve_min_score_threshold
        from backend.services.studio.intelligence import text_json
        from backend.utils.text_processor import TextProcessor
        llm_usage.set_stage("clip_finder")
        emit_progress(self.project_id, "ANALYZE", "Reading the full transcript to select clips")
        try:
            entries = TextProcessor.parse_srt(Path(srt_path))
            with llm_usage.timed("clip_finder"):
                return find_clips(entries, text_json, threshold=resolve_min_score_threshold(), metadata_dir=metadata_dir)
        except ProjectDeleted:
            raise
        except Exception as error:  # noqa: BLE001 - the legacy steps still work with any model
            logger.warning("One-pass clip finding unavailable, switching to step-by-step analysis: %s", error)
            return None

    async def process_project_sync(self, input_video_path: str, input_srt_path: str, clips_only: bool = False) -> Dict[str, Any]:
        """
        Process the project synchronously - uses the simplified progress system
        
        Args:
            input_video_path: Input video path
            input_srt_path: Input SRT path
            clips_only: Quick output only needs titled clip segments: skip topic clustering (one model call) and per-clip
                re-encoding (Studio renders directly from the source video), saving model costs and a lot of CPU.
            
        Returns:
            Processing result
        """
        logger.info(f"Starting project processing: {self.project_id}")
        
        try:
            # Clear previous progress data
            clear_progress(self.project_id)
            
            # Create the necessary directory structure - use the correct paths
            from backend.core.path_utils import get_project_directory
            project_dir = get_project_directory(self.project_id)
            metadata_dir = project_dir / "metadata"
            output_dir = project_dir / "output"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            # Project-specific output subdirectories
            clips_output_dir = output_dir / "clips"
            collections_output_dir = output_dir / "collections"
            clips_output_dir.mkdir(parents=True, exist_ok=True)
            collections_output_dir.mkdir(parents=True, exist_ok=True)
            prompt_files = self._prompt_files(project_dir)
            
            # Stage 1: Material preparation. Confirm the LLM is available first; otherwise every later step is wasted work
            from backend.core.project_cancellation import checkpoint
            checkpoint(self.project_id)
            emit_progress(self.project_id, "INGEST", "Material preparation complete")
            # Source video shorter than the minimum clip length, or no audio track and no subtitles: the subtitle route is doomed, block it up front and do not call any model (RC156 #10/#11)
            from backend.pipeline import media_precheck
            blocked = media_precheck.subtitle_route_failure(
                input_video_path, srt_available=bool(input_srt_path and Path(input_srt_path).exists()))
            if blocked:
                raise blocked
            self._preflight_llm()
            checkpoint(self.project_id)

            # Stage 2: Subtitle processing
            emit_progress(self.project_id, "SUBTITLE", "Starting subtitle processing")
            
            if input_srt_path and Path(input_srt_path).exists():
                logger.info(f"Using existing SRT file: {input_srt_path}")
                srt_path = Path(input_srt_path)
            else:
                from backend.utils.speech_recognizer import SpeechRecognitionError
                from backend.services.cloud_transcription import CloudTranscriptionError
                logger.warning("No SRT file, attempting automatic subtitle generation")
                try:
                    with llm_usage.timed("transcribe"):
                        srt_path = await self._generate_subtitle_automatically(input_video_path, metadata_dir)
                except CloudTranscriptionError as e:
                    raise cloud_transcription_failure(str(e)) from e
                except SpeechRecognitionError as e:
                    raise failure_from_speech_error(str(e)) from e
                if not (srt_path and srt_path.exists()):
                    # Previously this wrote an empty outline and then "succeeded" all the way through; the user saw Completed · 0 clips
                    raise missing_subtitle_failure()
                logger.info(f"Automatic subtitle generation succeeded: {srt_path}")

            # Quick output: one-pass clip finding (the whole transcript is given to the model at once); fall back to the old four steps when unavailable
            checkpoint(self.project_id)
            titled_clips = self._find_clips_in_one_pass(srt_path, metadata_dir) if clips_only else None
            if titled_clips is not None:
                outlines, timeline_data, scored_clips = [], [], titled_clips
                emit_progress(self.project_id, "HIGHLIGHT", "Clip selection complete", subpercent=40)
            else:
                # Step 1: Outline extraction (when subtitles are empty / all models fail / parsing is impossible, step1 itself raises PipelineFailure)
                logger.info("Running Step 1: Outline extraction")
                llm_usage.set_stage("outline")
                outlines = run_step1_outline(srt_path, metadata_dir=metadata_dir, prompt_files=prompt_files)
                emit_progress(self.project_id, "SUBTITLE", "Subtitle processing complete", subpercent=100)  # never behind the transcription band
            
                # Stage 3: Content analysis
                emit_progress(self.project_id, "ANALYZE", "Starting content analysis")
            
                # Step 2: Timeline extraction
                logger.info("Running Step 2: Timeline extraction")
                llm_usage.set_stage("timeline")
                timeline_data = run_step2_timeline(
                    metadata_dir / "step1_outline.json",
                    metadata_dir=metadata_dir,
                    prompt_files=prompt_files,
                )
                if not timeline_data:
                    # Step 2 normally raises its observed failure. Keep the legacy
                    # fallback for callers returning [] without diagnostic data.
                    raise empty_timeline_failure(len(outlines))
                emit_progress(self.project_id, "ANALYZE", "Timeline extraction complete", subpercent=50)
            
                # Step 3: Content scoring
                logger.info("Running Step 3: Content scoring")
                llm_usage.set_stage("scoring")
                scored_clips = run_step3_scoring(
                    metadata_dir / "step2_timeline.json",
                    metadata_dir=metadata_dir,
                    prompt_files=prompt_files,
                )
                if not scored_clips:
                    from backend.pipeline.step3_scoring import resolve_min_score_threshold
                    raise PipelineFailure(
                        "ANALYZE",
                        f"No clips passed the scoring filter ({len(timeline_data)} candidates, threshold {resolve_min_score_threshold()}).",
                        HINT_LOWER_THRESHOLD,
                    )
                emit_progress(self.project_id, "ANALYZE", "Content analysis complete", subpercent=100)
            
                # Stage 4: Clip localization
                emit_progress(self.project_id, "HIGHLIGHT", "Starting clip localization")
            
                # Step 4: Title generation
                logger.info("Running Step 4: Title generation")
                llm_usage.set_stage("titles")
                titled_clips = run_step4_title(
                    metadata_dir / "step3_high_score_clips.json",
                    metadata_dir=str(metadata_dir),
                    prompt_files=prompt_files,
                )
                emit_progress(self.project_id, "HIGHLIGHT", "Title generation complete", subpercent=40)
            
            if clips_only:
                collections, video_result = [], {}
                emit_progress(self.project_id, "HIGHLIGHT", "Clip localization complete", subpercent=100)
            else:
                # Step 5: Topic clustering
                logger.info("Running Step 5: Topic clustering")
                llm_usage.set_stage("clustering")
                collections = run_step5_clustering(
                    metadata_dir / "step4_titles.json",
                    metadata_dir=str(metadata_dir),
                    prompt_files=prompt_files,
                )
                emit_progress(self.project_id, "HIGHLIGHT", "Clip localization complete", subpercent=100)
            
                # Stage 5: Video export
                emit_progress(self.project_id, "EXPORT", "Starting video export")
            
                # Step 6: Video cutting
                logger.info("Running Step 6: Video cutting")
                video_result = run_step6_video(
                    metadata_dir / "step4_titles.json",
                    metadata_dir / "step5_collections.json",
                    input_video_path,
                    output_dir=output_dir,
                    clips_dir=str(clips_output_dir),
                    collections_dir=str(collections_output_dir),
                    metadata_dir=str(metadata_dir)
                )
                if titled_clips and not video_result.get("clips_generated"):
                    raise PipelineFailure(
                        "EXPORT",
                        f"Video cutting produced no files ({len(titled_clips)} clips to cut).",
                        HINT_CHECK_FFMPEG,
                    )
                emit_progress(self.project_id, "EXPORT", "Video export complete", subpercent=100)
            
            # Stage 6: Processing complete
            emit_progress(self.project_id, "DONE", "Processing complete")
            
            # Automatically sync data to the database
            try:
                from backend.services.data_sync_service import DataSyncService
                from backend.core.database import SessionLocal
                
                db = SessionLocal()
                try:
                    sync_service = DataSyncService(db)
                    sync_result = sync_service.sync_project_from_filesystem(self.project_id, project_dir)
                    if sync_result.get("success"):
                        logger.info(f"Project {self.project_id} data sync succeeded: {sync_result}")
                    else:
                        logger.error(f"Project {self.project_id} data sync failed: {sync_result}")
                finally:
                    db.close()
            except Exception as e:
                logger.error(f"Data sync failed: {e}")
            
            logger.info(f"Project processing complete: {self.project_id}")
            return {
                "status": "succeeded",
                "project_id": self.project_id,
                "task_id": self.task_id,
                "result": {
                    "outlines": outlines,
                    "timeline": timeline_data,
                    "scored_clips": scored_clips,
                    "titled_clips": titled_clips,
                    "collections": collections,
                    "video_result": video_result
                }
            }
            
        except ProjectDeleted:
            raise  # Project deleted: stop, do not write failure progress (RC156 #12)
        except PipelineFailure as e:
            # Explicit failure: carries the stage and next-step hint; the frontend failure state / in-app feedback displays it directly
            error_msg = e.user_message()
            logger.error(f"Pipeline failed at stage {e.stage}: {error_msg}")
            emit_progress(self.project_id, e.stage, f"Processing failed: {error_msg}")
            failed = {
                "status": "failed",
                "project_id": self.project_id,
                "task_id": self.task_id,
                "stage": e.stage,
                "error": error_msg,
                "message": error_msg,
            }
            if e.code:
                failed["error_code"] = e.code
            # The result is a plain dict (Studio gets it via process_video_pipeline.apply().get()), so the exception chain breaks here:
            # write the upstream HTTP status code into it, and jobs.run_content then carries it back to PipelineFailure (RC156 Win QA #1).
            status = http_status_of(e)
            if status is not None:
                failed["http_status"] = status
            return failed
        except Exception as e:
            error_msg = f"Pipeline processing failed: {str(e)}"
            logger.exception(error_msg)
            
            # Emit failure status
            emit_progress(self.project_id, "DONE", f"Processing failed: {error_msg}")

            failed = {
                "status": "failed",
                "project_id": self.project_id,
                "task_id": self.task_id,
                "error": error_msg,
                "message": error_msg,
            }
            status = http_status_of(e)
            if status is not None:
                failed["http_status"] = status
            return failed


def create_simple_pipeline_adapter(project_id: str, task_id: str) -> SimplePipelineAdapter:
    """Create a simplified pipeline adapter instance"""
    return SimplePipelineAdapter(project_id, task_id)
