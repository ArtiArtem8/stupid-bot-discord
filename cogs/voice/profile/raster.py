"""Persistent Inkscape rasterizer for production profile layers."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from time import monotonic
from typing import BinaryIO

from PIL import Image, ImageFont


@dataclass(frozen=True, slots=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


def _read_output(stream: BinaryIO, output: Queue[bytes | None]) -> None:
    try:
        while chunk := os.read(stream.fileno(), 65536):
            output.put(chunk)
    except (OSError, ValueError):
        pass  # Closing our child process also closes the reader's pipe.
    finally:
        output.put(None)


def _stop_shell(
    process: subprocess.Popen[bytes], directory: TemporaryDirectory[str]
) -> None:
    try:
        if process.poll() is None:
            if process.stdin is not None:
                try:
                    _ = process.stdin.write(b"quit\n")
                    process.stdin.flush()
                except OSError:
                    pass
            try:
                _ = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                _ = process.wait(timeout=2)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        directory.cleanup()


def inkscape_executable() -> str:
    """Use an explicit binary or a conventional installation. Never use a shell."""
    explicit = os.environ.get("INKSCAPE_BIN")
    if explicit:
        if Path(explicit).is_file():
            return explicit
        raise RuntimeError("INKSCAPE_BIN does not point to an executable")
    found = shutil.which("inkscape")
    if found:
        return found
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        directory = os.environ.get(variable)
        if directory:
            candidate = Path(directory) / "Inkscape" / "bin" / "inkscape.exe"
            if candidate.is_file():
                return str(candidate)
    raise RuntimeError("Install Inkscape or set INKSCAPE_BIN to its executable")


def font_cache_directory() -> Path:
    """Use a durable per-user font cache, independent of temporary SVG files."""
    configured = os.environ.get("PROFILE_CACHE_DIR")
    if configured:
        root = Path(configured)
    elif os.name == "nt":
        root = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
            / "stupid-bot-discord"
        )
    else:
        root = (
            Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
            / "stupid-bot-discord"
        )
    cache = root.expanduser().resolve() / "fontconfig"
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    return cache


class NativeRasterizer:
    """Batch static layers. No browser, GUI session or process per animation frame."""

    def __init__(self, *, timeout: float = 12.0) -> None:
        self.executable: str = inkscape_executable()
        self.timeout: float = timeout
        default_fonts = Path(__file__).resolve().parents[3] / "resources" / "fonts"
        self.font_dir: Path = Path(
            os.environ.get("PROFILE_FONT_DIR", str(default_fonts))
        )
        if any(
            not (self.font_dir / name).is_file()
            for name in ("Inter.ttf", "Inter-600.ttf", "Inter-750.ttf")
        ):
            raise RuntimeError(
                "Required Inter fonts are missing; check resources/fonts "
                "or PROFILE_FONT_DIR"
            )
        for name in ("Inter.ttf", "Inter-600.ttf", "Inter-750.ttf"):
            ImageFont.truetype(str(self.font_dir / name), 16)
        self.font_cache_dir: Path = font_cache_directory()
        self._process: subprocess.Popen[bytes] | None = None
        self._output: Queue[bytes | None] = Queue()
        self._directory: TemporaryDirectory[str] | None = None
        self._closed = False
        self._lock: Lock = Lock()

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._close_unlocked()

    def start(self) -> None:
        """Start the owned shell during Cog load, off the event loop."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Profile rasterizer is closed")
            if self._process is None:
                try:
                    self._start_shell()
                except (OSError, RuntimeError):
                    self._close_unlocked()
                    raise

    def _close_unlocked(self) -> None:
        if self._process is not None and self._directory is not None:
            _stop_shell(self._process, self._directory)
            self._directory = None
        self._process = None

    def _wait_prompt(self) -> str:
        deadline = monotonic() + self.timeout
        data = bytearray()
        while not data.endswith(b"> "):
            try:
                chunk = self._output.get(timeout=max(0, deadline - monotonic()))
            except Empty as error:
                raise RuntimeError("Inkscape shell timed out") from error
            if chunk is None:
                diagnostic = data[-1800:].decode("utf-8", "replace")
                raise RuntimeError(f"Inkscape shell closed unexpectedly: {diagnostic}")
            data.extend(chunk)
            if len(data) > 8 * 1024 * 1024:
                raise RuntimeError("Inkscape shell output exceeded its budget")
        return data[:-2].decode("utf-8", "replace")

    def _start_shell(self) -> subprocess.Popen[bytes]:
        # Fontconfig is also used by Inkscape on Windows. Bind the same project
        # font directory for measurement and rasterization, not an accidental
        # user-specific substitute. System fonts are fallback for missing glyphs.
        includes = '<include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
        windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        # Cache metadata must survive query()/layers() temporary directories.
        # Put this cache first; keep the existing font sources and fallback order.
        config = (
            f"<fontconfig><cachedir>{escape(str(self.font_cache_dir))}</cachedir>"
            f"{includes}<dir>{escape(str(self.font_dir.resolve()))}</dir>"
            f"<dir>{escape(str(windows_fonts))}</dir></fontconfig>"
        )
        directory = TemporaryDirectory(prefix="profile-shell-")
        font_config = Path(directory.name) / "fonts.conf"
        _ = font_config.write_text(config, encoding="utf-8")
        environment = dict(os.environ)
        environment["FONTCONFIG_FILE"] = str(font_config)
        try:
            # The executable is operator configuration; arguments never use a shell.
            process = subprocess.Popen(  # noqa: S603
                [self.executable, "--shell"],
                cwd=directory.name,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            directory.cleanup()
            raise
        self._process = process
        self._directory = directory
        self._output = Queue()
        if process.stdout is None:
            raise RuntimeError("Inkscape output pipe is unavailable")
        Thread(
            target=_read_output, args=(process.stdout, self._output), daemon=True
        ).start()
        _ = self._wait_prompt()
        return process

    @staticmethod
    def _action_path(path: Path) -> str:
        value = path.resolve().as_posix()
        if any(char in value for char in ";\r\n"):
            raise ValueError(
                "SVG temporary paths cannot contain Inkscape action delimiters"
            )
        return value

    def _execute_actions(self, actions: Sequence[str]) -> str:
        # This is Inkscape's action interpreter, never an OS command shell.
        with self._lock:
            try:
                if self._closed:
                    raise RuntimeError("Profile rasterizer is closed")
                process = self._process or self._start_shell()
                if process.stdin is None:
                    raise RuntimeError("Inkscape input pipe is unavailable")
                _ = process.stdin.write((f"{';'.join(actions)}\n").encode())
                process.stdin.flush()
                return self._wait_prompt()
            except (OSError, RuntimeError):
                self._close_unlocked()
                raise

    def query(self, svg: bytes) -> dict[str, Box]:
        """Measure actual shaped text and transformed objects with the renderer."""
        with TemporaryDirectory(prefix="profile-measure-") as name:
            directory = Path(name)
            path = directory / "card.svg"
            _ = path.write_bytes(svg)
            output = self._execute_actions(
                (f"file-open:{self._action_path(path)}", "query-all", "file-close")
            )
        boxes: dict[str, Box] = {}
        for line in output.splitlines():
            values = line.split(",")
            if len(values) != 5:
                continue
            identifier, x, y, width, height = values
            try:
                boxes[identifier] = Box(float(x), float(y), float(width), float(height))
            except ValueError:
                continue
        if not boxes:
            raise RuntimeError("Inkscape returned no object bounds")
        return boxes

    def layers(self, documents: tuple[bytes, ...]) -> tuple[Image.Image, ...]:
        """One process for all static SVG layers; PNG is only the native handoff."""
        with TemporaryDirectory(prefix="profile-layers-") as name:
            directory = Path(name)
            paths: list[Path] = []
            for index, svg in enumerate(documents):
                path = directory / f"layer-{index}.svg"
                _ = path.write_bytes(svg)
                paths.append(path)
            actions: list[str] = []
            for path in paths:
                actions.extend(
                    (
                        f"file-open:{self._action_path(path)}",
                        "export-type:png",
                        "export-area-page",
                        "export-background-opacity:0",
                        "export-png-compression:1",
                        "export-png-antialias:2",
                        "export-png-use-dithering:false",
                        f"export-filename:{self._action_path(path.with_suffix('.png'))}",
                        "export-do",
                        "file-close",
                    )
                )
            _ = self._execute_actions(actions)
            result: list[Image.Image] = []
            for path in paths:
                with Image.open(
                    BytesIO(path.with_suffix(".png").read_bytes())
                ) as image:
                    result.append(image.convert("RGBA"))
        return tuple(result)
