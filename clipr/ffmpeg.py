"""FFmpeg plumbing: screen capture to an intermediate MP4, then MP4 to GIF.

Recording writes H.264 first and only converts to GIF after trim/crop, so the
expensive palette work happens once, on the final framing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QThread, Signal

from .tools import (  # noqa: F401  (re-exported: the UI imports them from here)
    ffmpeg_path,
    ffprobe_path,
    gifsicle_path,
    run_hidden,
)


def cleanup_temp(temp_dir: Path, ttl: float = 24 * 3600) -> int:
    """Remove leftover rec_* folders older than ttl.

    A crash or a force-quit leaves an unfinished capture behind; without this the
    temp folder grows by an MP4 per accident.
    """
    removed = 0
    try:
        now = time.time()
        for entry in Path(temp_dir).glob("rec_*"):
            try:
                if entry.is_dir() and now - entry.stat().st_mtime > ttl:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int

    def even(self) -> "Region":
        """H.264 with yuv420p needs even dimensions."""
        return Region(self.x, self.y, self.width - self.width % 2, self.height - self.height % 2)

    @property
    def is_valid(self) -> bool:
        return self.width >= 16 and self.height >= 16


def probe(path: Path) -> dict:
    """Duration/width/height of a media file. Empty dict when ffprobe is absent."""
    exe = ffprobe_path()
    if not exe:
        return {}
    result = run_hidden([
        exe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json", str(path),
    ], timeout=30)
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return {}
    stream = (data.get("streams") or [{}])[0]
    info = {
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "duration": float((data.get("format") or {}).get("duration", 0) or 0),
    }
    rate = stream.get("r_frame_rate", "0/1")
    try:
        num, den = rate.split("/")
        info["fps"] = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        info["fps"] = 0.0
    return info


class Recorder(QObject):
    """Screen capture as a sequence of MP4 segments, concatenated on stop.

    Pause is implemented by ending the current segment and starting a new one on
    resume: ffmpeg has no pause, and stitching segments keeps the timeline free
    of the frozen frames a paused-but-running capture would record.
    """

    started = Signal()
    paused = Signal()
    resumed = Signal()
    finished = Signal(str)  # final mp4 path ("" when nothing was captured)
    failed = Signal(str)

    def __init__(self, settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._process: QProcess | None = None
        self._segments: list[Path] = []
        self._work_dir: Path | None = None
        self._region: Region | None = None
        self._elapsed_before_pause = 0.0
        self._segment_start = 0.0
        self._state = "idle"  # idle | recording | paused | stopping
        self._stderr_tail = ""

    # ---- state -----------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state in ("recording", "paused")

    def elapsed(self) -> float:
        if self._state == "recording":
            return self._elapsed_before_pause + (time.monotonic() - self._segment_start)
        return self._elapsed_before_pause

    # ---- control ---------------------------------------------------------
    def start(self, region: Region) -> None:
        if self.is_active:
            return
        if not ffmpeg_path():
            self.failed.emit("FFmpeg не найден. Установите его в настройках.")
            return
        region = region.even()
        if not region.is_valid:
            self.failed.emit("Область записи слишком мала.")
            return

        root = self.settings.ensure_temp_dir()
        self._work_dir = root / f"rec_{time.strftime('%Y%m%d_%H%M%S')}"
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._segments = []
        self._region = region
        self._elapsed_before_pause = 0.0
        self._stderr_tail = ""
        self._spawn_segment()
        self._state = "recording"
        self.started.emit()

    def pause(self) -> None:
        if self._state != "recording":
            return
        self._elapsed_before_pause += time.monotonic() - self._segment_start
        self._state = "paused"
        self._stop_segment()
        self.paused.emit()

    def resume(self) -> None:
        if self._state != "paused":
            return
        self._spawn_segment()
        self._state = "recording"
        self.resumed.emit()

    def toggle_pause(self) -> None:
        self.pause() if self._state == "recording" else self.resume()

    def stop(self) -> None:
        if not self.is_active:
            return
        was_recording = self._state == "recording"
        if was_recording:
            self._elapsed_before_pause += time.monotonic() - self._segment_start
        self._state = "stopping"
        if was_recording:
            self._stop_segment()
        self._finalize()

    def cancel(self) -> None:
        """Stop and discard everything (used on app shutdown)."""
        if self._state == "recording":
            self._stop_segment()
        self._state = "idle"
        if self._work_dir and self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)

    # ---- internals -------------------------------------------------------
    def _segment_path(self) -> Path:
        assert self._work_dir is not None
        return self._work_dir / f"seg_{len(self._segments):03d}.mp4"

    def _capture_args(self, region: Region) -> list[str]:
        fps = str(self.settings.capture_fps)
        mouse = "1" if self.settings.draw_mouse else "0"
        if self.settings.capture_backend == "ddagrab":
            source = (
                f"ddagrab=output_idx=0:framerate={fps}:draw_mouse={mouse}"
                f":video_size={region.width}x{region.height}"
                f":offset_x={region.x}:offset_y={region.y}"
            )
            return ["-f", "lavfi", "-i", source, "-vf", "hwdownload,format=bgra"]
        return [
            "-f", "gdigrab",
            "-framerate", fps,
            "-draw_mouse", mouse,
            "-offset_x", str(region.x),
            "-offset_y", str(region.y),
            "-video_size", f"{region.width}x{region.height}",
            "-i", "desktop",
        ]

    def _spawn_segment(self) -> None:
        assert self._region is not None
        target = self._segment_path()
        args = [
            "-hide_banner", "-loglevel", "error",
            *self._capture_args(self._region),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-y", str(target),
        ]

        process = QProcess(self)
        process.setProgram(ffmpeg_path())
        process.setArguments(args)
        process.readyReadStandardError.connect(self._drain_stderr)
        process.errorOccurred.connect(lambda _e: self.failed.emit(self._stderr_tail or "FFmpeg не запустился."))
        process.start()
        if not process.waitForStarted(5000):
            self.failed.emit("Не удалось запустить FFmpeg.")
            return
        self._process = process
        self._segments.append(target)
        self._segment_start = time.monotonic()

    def _drain_stderr(self) -> None:
        if self._process:
            chunk = bytes(self._process.readAllStandardError()).decode("utf-8", "replace")
            self._stderr_tail = (self._stderr_tail + chunk)[-2000:]

    def _stop_segment(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        # "q" on stdin makes ffmpeg flush and close the container cleanly;
        # killing it would leave an unplayable moov-less MP4.
        process.write(b"q")
        process.closeWriteChannel()
        if not process.waitForFinished(5000):
            process.kill()
            process.waitForFinished(2000)

    def _finalize(self) -> None:
        assert self._work_dir is not None
        segments = [s for s in self._segments if s.exists() and s.stat().st_size > 1024]
        if not segments:
            self._state = "idle"
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self.finished.emit("")
            return

        if len(segments) == 1:
            final = segments[0]
        else:
            list_file = self._work_dir / "segments.txt"
            list_file.write_text(
                "".join(f"file '{p.as_posix()}'\n" for p in segments), "utf-8"
            )
            final = self._work_dir / "capture.mp4"
            result = run_hidden([
                ffmpeg_path(), "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", "-y", str(final),
            ], timeout=300)
            if result.returncode != 0 or not final.exists():
                self.failed.emit(result.stderr.strip() or "Не удалось склеить сегменты записи.")
                final = segments[0]

        self._state = "idle"
        self.finished.emit(str(final))


class GifExporter(QThread):
    """Two-pass palette GIF export, optionally squeezed further by gifsicle."""

    progress = Signal(int, str)  # 0..100, stage label
    done = Signal(str, int)  # path, size in bytes
    failed = Signal(str)

    def __init__(
        self,
        source: Path,
        target: Path,
        settings,
        start: float = 0.0,
        end: float | None = None,
        crop: Region | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.source = Path(source)
        self.target = Path(target)
        self.settings = settings
        self.start_time = start
        self.end_time = end
        self.crop = crop

    def _filters(self) -> str:
        chain = []
        if self.crop:
            c = self.crop
            chain.append(f"crop={c.width}:{c.height}:{c.x}:{c.y}")
        chain.append(f"fps={self.settings.gif_fps}")
        if self.settings.gif_width > 0:
            chain.append(f"scale={self.settings.gif_width}:-1:flags=lanczos")
        return ",".join(chain)

    def _trim_args(self) -> list[str]:
        args = ["-ss", f"{self.start_time:.3f}"]
        if self.end_time is not None and self.end_time > self.start_time:
            args += ["-t", f"{self.end_time - self.start_time:.3f}"]
        return args

    def run(self) -> None:  # noqa: D102 (QThread entry point)
        exe = ffmpeg_path()
        if not exe:
            self.failed.emit("FFmpeg не найден. Установите его в настройках.")
            return
        self.target.parent.mkdir(parents=True, exist_ok=True)
        palette = self.target.with_suffix(".palette.png")
        filters = self._filters()

        try:
            self.progress.emit(10, "Строим палитру…")
            result = run_hidden([
                exe, "-hide_banner", "-loglevel", "error",
                *self._trim_args(), "-i", str(self.source),
                "-vf", f"{filters},palettegen=stats_mode=diff",
                "-y", str(palette),
            ], timeout=600)
            if result.returncode != 0:
                self.failed.emit(result.stderr.strip() or "Ошибка palettegen.")
                return

            self.progress.emit(45, "Собираем GIF…")
            result = run_hidden([
                exe, "-hide_banner", "-loglevel", "error",
                *self._trim_args(), "-i", str(self.source),
                "-i", str(palette),
                "-lavfi",
                f"{filters} [x]; [x][1:v] paletteuse=dither={self.settings.gif_dither}"
                ":diff_mode=rectangle",
                "-loop", "0",
                "-y", str(self.target),
            ], timeout=1800)
            if result.returncode != 0:
                self.failed.emit(result.stderr.strip() or "Ошибка paletteuse.")
                return

            gifsicle = gifsicle_path()
            if self.settings.gif_optimize and gifsicle:
                self.progress.emit(80, "Сжимаем…")
                optimized = self.target.with_suffix(".opt.gif")
                args = [gifsicle, "-O3"]
                if self.settings.gif_lossy > 0:
                    args.append(f"--lossy={self.settings.gif_lossy}")
                args += [str(self.target), "-o", str(optimized)]
                result = run_hidden(args, timeout=900)
                if result.returncode == 0 and optimized.exists():
                    if optimized.stat().st_size < self.target.stat().st_size:
                        self.target.unlink(missing_ok=True)
                        optimized.replace(self.target)
                    else:
                        optimized.unlink(missing_ok=True)

            self.progress.emit(100, "Готово")
            self.done.emit(str(self.target), self.target.stat().st_size)
        except subprocess.TimeoutExpired:
            self.failed.emit("FFmpeg не ответил вовремя.")
        except OSError as exc:
            self.failed.emit(str(exc))
        finally:
            palette.unlink(missing_ok=True)
