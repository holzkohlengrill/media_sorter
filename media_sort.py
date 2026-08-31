#!/usr/bin/env python3
"""
Media Sort Tool - Sorts photos, videos and gifs into year-based folders

This tool organizes media files by parsing dates from filenames or using creation dates,
maintaining directory structure while sorting into year-based folders.

=== LICENSE ===
Copyright (C) 2025  Marcel Schmalzl

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import os
import sys
import re
import shutil
import argparse
import json
import signal
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Callable, NamedTuple, Iterator
from dataclasses import dataclass, field
from enum import Enum
import time
import platform


NEW_YEAR_CUTOFF_MONTH = 1                  # Files before Jan 1st 14:00 belong to the previous year
NEW_YEAR_CUTOFF_DAY = 1
NEW_YEAR_CUTOFF_HOUR = 14
MIN_VALID_YEAR = 1970                      # Unix epoch start
MAX_VALID_YEAR = 3000                      # Upper limit for validating year
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1
STATUS_SAVE_INTERVAL = 100                 # Files between two writes of the status file
STATUS_FILE_NAME = ".media_sort_status.json"
VIRTUAL_CACHE_FILE_NAME = ".media_sort_virtual_cache.json"

MEDIA_EXTENSIONS = {
    # Images
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp',
    '.heic', '.heif', '.raw', '.cr2', '.nef', '.arw', '.dng', '.svg',
    # Videos
    '.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm', '.m4v',
    '.mpg', '.mpeg', '.3gp', '.3g2', '.mts', '.m2ts', '.vob', '.ogv',
}

# Files and directories to skip always regardless of settings
SKIP_DIRECTORIES = {
    '.git', '.svn', '.hg',                  # Version control
    '__MACOSX',                             # macOS
    '.Trash', '.Trashes',                   # Trash folders
    '__pycache__',                          # Dependencies/cache
    '.cache', '.tmp', '.temp'               # Temporary files
}
SKIP_FILES = {
    '.DS_Store',                            # macOS directory metadata
    'Thumbs.db',                            # Windows thumbnails
    'desktop.ini',                          # Windows folder settings
    '.gitignore',                           # Git ignore file
    '.gitkeep',                             # Git placeholder
}


def get_relative_path(path: Path) -> str:
    """
    Shorten a path for display by making it relative to the working directory.

    Args:
        path: Path to display

    Returns:
        The shortened path, or the path unchanged when it lies outside the working
        directory
    """
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def ask(question: str, on_end_of_input: str) -> str:
    """
    Put one question to the user.

    Args:
        question: Text to show
        on_end_of_input: Answer to fall back to when the input stream is at its end and
            nothing more can be read. This happens with no terminal attached, or when a
            script pipes in fewer answers than there are questions. Callers pass the
            least destructive answer, so an unattended run stays safe

    Returns:
        The answer in lower case and without surrounding spaces
    """
    try:
        return input(question).strip().lower()
    except EOFError:
        print()
        return on_end_of_input


class SkipReason(Enum):
    """Why a path is left alone, in the words shown to the user"""
    SYSTEM_FILE = "system file"
    SYSTEM_DIRECTORY = "system directory"
    HIDDEN = "hidden path"

    def __str__(self) -> str:
        return self.value


def get_skip_reason(relative_path: Path, exclude_hidden: bool) -> Optional[SkipReason]:
    """
    Decide whether a file has to be left alone and say why.

    Args:
        relative_path: Path of the file below the directory being scanned. It must be
            relative, not absolute: a scanned directory may itself lie below a dot-directory,
            and judging the absolute path would then mark every file inside as hidden
        exclude_hidden: Whether names starting with a dot count as skipped

    Returns:
        The reason to skip the file, or None when it may be processed
    """
    if relative_path.name in SKIP_FILES:
        return SkipReason.SYSTEM_FILE

    for part in relative_path.parts:
        if part in SKIP_DIRECTORIES:
            return SkipReason.SYSTEM_DIRECTORY

    if exclude_hidden:
        for part in relative_path.parts:
            if part.startswith('.'):
                return SkipReason.HIDDEN

    return None


def iter_tree_files(root: Path, follow_symlinks: bool, on_error: Callable[[Path, OSError], None]) -> Iterator[Tuple[os.DirEntry, str]]:
    """
    Walk a directory tree and yield each file with its path below the root.

    Directory entries already carry their type and are visited in name order, so the walk
    is deterministic. Directories on the skip list are not descended into. A symlink to a
    file is always followed and yielded, the same as an ordinary file. A symlink to a
    directory is descended into only when follow_symlinks is set; a link that then leads
    back into a directory already entered is skipped, so a loop cannot run forever and no
    file is reached under two paths.

    Args:
        root: Directory to walk
        follow_symlinks: Descend into directory symlinks, guarded against loops
        on_error: Called with the path and the error when a directory or entry cannot be
            read; that path is then skipped and the walk goes on

    Yields:
        Each file as its directory entry paired with its slash separated path below the
        root
    """
    visited = {root.resolve()} if follow_symlinks else set()
    pending = [(root, "")]

    while pending:
        directory, prefix = pending.pop()

        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError as error:
            on_error(directory, error)
            continue

        for entry in entries:
            relative_path = f"{prefix}{entry.name}"

            try:
                if entry.is_dir(follow_symlinks=follow_symlinks):
                    if entry.name in SKIP_DIRECTORIES:
                        continue
                    if follow_symlinks:
                        real = Path(entry.path).resolve()
                        if real in visited:
                            continue
                        visited.add(real)
                    pending.append((Path(entry.path), f"{relative_path}/"))
                elif entry.is_file():
                    yield entry, relative_path
            except OSError as error:
                on_error(Path(entry.path), error)


@dataclass(frozen=True)
class YearCutoff:
    """
    The point of the year before which a file still counts as the previous year.

    Pictures of a New Year's Eve party are taken after midnight but belong to the year
    that was celebrated. The cutoff pushes them back: a file dated on the cutoff day but
    before the cutoff time is sorted one year earlier. Files of every other day keep
    their own year, so moving the cutoff can never reclassify more than that one day.

    With the default of Jan 1st at 14:00, a photo taken 2025-01-01 at 03:00 goes to
    2024 and one taken at 15:00 goes to 2025.
    """
    month: int = NEW_YEAR_CUTOFF_MONTH
    day: int = NEW_YEAR_CUTOFF_DAY
    hour: int = NEW_YEAR_CUTOFF_HOUR
    minute: int = 0
    second: int = 0

    @classmethod
    def from_iso(cls, value: str) -> 'YearCutoff':
        """
        Build a cutoff from an ISO 8601 timestamp such as 2025-01-01T14:00:00Z.

        The year of the timestamp is dropped because the cutoff repeats every year, and
        a timezone suffix is dropped as well: the time counts as local wall clock time,
        just like the file timestamps it is compared against.

        Args:
            value: ISO 8601 timestamp, with or without a timezone suffix

        Returns:
            The cutoff described by month, day and time of day

        Raises:
            ValueError: The text is no ISO 8601 timestamp, or it names Feb 29th, which
                does not exist in every year
        """
        text = value.strip()
        if text[-1:] in ('z', 'Z'):
            text = text[:-1]

        try:
            moment = datetime.fromisoformat(text)
        except ValueError as error:
            raise ValueError(f"not a valid ISO 8601 timestamp: {value} ({error})") from error

        try:
            datetime(2001, moment.month, moment.day)      # 2001 is not a leap year
        except ValueError:
            raise ValueError(f"cutoff day must exist in every year: {value}") from None

        return cls(month=moment.month, day=moment.day, hour=moment.hour, minute=moment.minute, second=moment.second)

    def boundary_for_year(self, year: int) -> datetime:
        """
        Place the cutoff in a concrete year.

        Args:
            year: Year the cutoff should fall in

        Returns:
            The cutoff as a local timestamp of that year
        """
        return datetime(year, self.month, self.day, self.hour, self.minute, self.second)

    def resolve_year(self, moment: datetime) -> int:
        """
        Map the moment a file was taken to the year folder it belongs to.

        Args:
            moment: When the file was taken, as local time

        Returns:
            The year of the moment, or the year before it when the moment falls on the
            cutoff day and is earlier than the cutoff time
        """
        on_cutoff_day = (moment.month, moment.day) == (self.month, self.day)
        if on_cutoff_day and moment < self.boundary_for_year(moment.year):
            return moment.year - 1
        return moment.year

    def __str__(self) -> str:
        """
        Returns:
            The cutoff as month, day and time of day, without a year
        """
        return f"{self.month:02d}-{self.day:02d}T{self.hour:02d}:{self.minute:02d}:{self.second:02d}"


@dataclass(frozen=True)
class FilenameDate:
    """
    The date a file name spells out.

    Some naming conventions carry a time of day, others only a date. In the second case
    has_time is False and the time is midnight, which the caller has to replace with a
    time from elsewhere before the cutoff can judge the file.
    """
    moment: datetime
    has_time: bool
    pattern_name: str


class DatePattern:
    """
    One naming convention that puts a date into a file name.

    It ties the regular expression that recognises the convention to the rule that turns
    a match into a timestamp, and states whether the convention spells out a time of day.
    """

    def __init__(self, name: str, regex: str, extractor: Callable[[re.Match], datetime], has_time: bool = False):
        """
        Args:
            name: Readable form of the convention, shown in verbose output
            regex: Expression whose groups feed the extractor, searched in the file name
            extractor: Turns a match into a timestamp
            has_time: Whether the convention carries a time of day and not just a date
        """
        self.name = name
        self.regex = re.compile(regex)
        self.extractor = extractor
        self.has_time = has_time

    def extract(self, filename: str) -> Optional[datetime]:
        """
        Read the timestamp this convention encodes in a file name.

        Args:
            filename: Name of the file, without any directory part

        Returns:
            The timestamp, or None when the name does not follow this convention or its
            digits form no real date such as month 13
        """
        match = self.regex.search(filename)
        if not match:
            return None

        try:
            return self.extractor(match)
        except ValueError:
            return None


class DatePatternMatcher:
    """All naming conventions the tool recognises, ordered from specific to generic"""

    def __init__(self):
        self.patterns = [
            # PXL files (Google Pixel)
            DatePattern(
                name="PXL_YYYYMMDD_HHMMSS",
                regex=r'PXL_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
                extractor=lambda m: datetime(*(int(g) for g in m.groups())),
                has_time=True
            ),
            # Screenshot pattern
            DatePattern(
                name="Screenshot_YYYYMMDD-HHMMSS",
                regex=r'Screenshot_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})',
                extractor=lambda m: datetime(*(int(g) for g in m.groups())),
                has_time=True
            ),
            # IMG pattern with full date
            DatePattern(
                name="IMG_YYYYMMDD_HHMMSS",
                regex=r'IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
                extractor=lambda m: datetime(*(int(g) for g in m.groups())),
                has_time=True
            ),
            # WhatsApp pattern
            DatePattern(
                name="IMG-YYYYMMDD-WA",
                regex=r'IMG-(\d{4})(\d{2})(\d{2})-WA',
                extractor=lambda m: datetime(*(int(g) for g in m.groups()))
            ),
            # VID pattern
            DatePattern(
                name="VID_YYYYMMDD_HHMMSS",
                regex=r'VID_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
                extractor=lambda m: datetime(*(int(g) for g in m.groups())),
                has_time=True
            ),
            # DSC pattern (Digital Still Camera)
            DatePattern(
                name="DSC_YYYYMMDD",
                regex=r'DSC_(\d{4})(\d{2})(\d{2})',
                extractor=lambda m: datetime(*(int(g) for g in m.groups()))
            ),
            # Generic date patterns
            DatePattern(
                name="YYYY-MM-DD",
                regex=r'(\d{4})-(\d{2})-(\d{2})',
                extractor=lambda m: datetime(*(int(g) for g in m.groups()))
            ),
            DatePattern(
                name="YYYYMMDD",
                regex=r'(?:^|[_\-\s])(\d{4})(\d{2})(\d{2})(?:[_\-\s]|$)',
                extractor=lambda m: datetime(*(int(g) for g in m.groups()))
            ),
            # Date at start of filename
            DatePattern(
                name="YYYYMMDD_start",
                regex=r'^(\d{4})(\d{2})(\d{2})[_\-]',
                extractor=lambda m: datetime(*(int(g) for g in m.groups()))
            ),
        ]

    def match(self, filename: str) -> Optional[FilenameDate]:
        """
        Read the date a file name encodes, trying the conventions in order.

        Args:
            filename: Name of the file, without any directory part

        Returns:
            The date found, or None when no convention matches or the year it yields
            lies outside the plausible range
        """
        for pattern in self.patterns:
            moment = pattern.extract(filename)
            if moment and MIN_VALID_YEAR <= moment.year <= MAX_VALID_YEAR:
                return FilenameDate(moment=moment, has_time=pattern.has_time, pattern_name=pattern.name)

        return None


class ActionInfo(NamedTuple):
    """Prompt text and decision rule of one conflict action"""
    shorthand: str
    full_name: str
    description: str
    allows_operation: Callable[['ConflictInfo'], bool]


class ScopeInfo(NamedTuple):
    """Prompt text of one conflict scope"""
    shorthand: str
    full_name: str
    description: str


class OverwriteAction(Enum):
    """
    What to do with a file whose target is already taken.

    Yes means the file is written to the output directory even though something is
    already there. For a conflict on a virtual directory nothing is overwritten, because
    a virtual directory is never written to; yes then only means the copy in the output
    directory is wanted anyway.
    """
    YES = ActionInfo(
        shorthand="y",
        full_name="yes",
        description="Process this file anyway",
        allows_operation=lambda conflict: True
    )
    NO = ActionInfo(
        shorthand="n",
        full_name="no",
        description="Skip this file",
        allows_operation=lambda conflict: False
    )
    LARGER = ActionInfo(
        shorthand="l",
        full_name="larger",
        description="Only if the source is larger",
        allows_operation=lambda conflict: conflict.source.size > conflict.target.size
    )
    OLDER = ActionInfo(
        shorthand="o",
        full_name="older",
        description="Only if the source is older",
        allows_operation=lambda conflict: conflict.source.creation_date < conflict.target.creation_date
    )
    NEWER = ActionInfo(
        shorthand="new",
        full_name="newer",
        description="Only if the source is newer",
        allows_operation=lambda conflict: conflict.source.creation_date > conflict.target.creation_date
    )

    def allows(self, conflict: 'ConflictInfo') -> bool:
        """
        Apply this action to a conflict.

        Args:
            conflict: The conflict the action was chosen for

        Returns:
            True when the file may be written, False when it has to be skipped
        """
        return self.value.allows_operation(conflict)

    @classmethod
    def from_input(cls, text: str) -> Optional['OverwriteAction']:
        """
        Read the action part of an answer to the conflict prompt.

        Args:
            text: Shorthand or full name, for example "y", "yes", "l" or "newer"

        Returns:
            The action, or None when the text names none of them
        """
        wanted = text.lower().strip()
        for action in cls:
            if wanted in (action.value.shorthand, action.value.full_name):
                return action

        return None


class OverwriteScope(Enum):
    """
    How far a chosen action reaches beyond the conflict it was given for.

    One file can run into several conflicts at once, one for the output directory and one
    per virtual directory that holds it, which is what the file scope covers. The virtual
    scope goes the other way and follows the conflicts on virtual directories through the
    whole run, so that everything they already hold can be settled with one answer while
    the copies in the output directory are still asked about one by one.
    """
    CURRENT = ScopeInfo(shorthand="", full_name="", description="This conflict only")
    FILE = ScopeInfo(shorthand="f", full_name="file", description="Every remaining conflict of this file")
    VIRTUAL = ScopeInfo(shorthand="v", full_name="virtual", description="This conflict and every remaining one on a virtual directory")
    ALL = ScopeInfo(shorthand="a", full_name="all", description="Every remaining conflict of the run")

    def covers(self, conflict: 'ConflictInfo') -> bool:
        """
        Decide whether an answer given with this scope also settles a later conflict.

        Args:
            conflict: A conflict that comes after the one the answer was given for

        Returns:
            True for every later conflict with the all scope, and for the ones on a
            virtual directory with the virtual scope. False for the current-conflict and
            file scopes: they never reach a later file, so their reach is applied where
            the conflict is answered, not from here
        """
        if self is OverwriteScope.ALL:
            return True
        if self is OverwriteScope.VIRTUAL:
            return conflict.is_virtual

        return False

    @classmethod
    def from_input(cls, text: str) -> Optional['OverwriteScope']:
        """
        Read the scope part of an answer to the conflict prompt.

        Args:
            text: Shorthand or full name, for example "a", "all" or "f"; empty text
                means the current conflict only

        Returns:
            The scope, or None when the text names none of them
        """
        wanted = text.lower().strip()
        if not wanted:
            return cls.CURRENT

        for scope in cls:
            if wanted in (scope.value.shorthand, scope.value.full_name):
                return scope

        return None


@dataclass
class FileInfo:
    """Name, size and timestamps of one file"""
    path: Path
    size: int
    creation_date: datetime
    modification_date: datetime


@dataclass
class ConflictInfo:
    """
    One planned copy that would land on a file that is already there.

    Attributes:
        source: File that would be copied
        target: File that is in the way, either the one in the output directory or one
            on a virtual directory; the latter is only reported, never written to
        operation_target: Full destination path the copy would take in the output
            directory (output dir, year folder and sub-path joined), not relative to it,
            in whatever form the output directory was given, relative or absolute. It is
            the same for every conflict of one planned copy, so it keys the grouping
        is_virtual: Whether the target sits on a virtual directory
    """
    source: FileInfo
    target: FileInfo
    operation_target: Path
    is_virtual: bool = False

    def format_summary(self) -> str:
        """
        Returns:
            Two lines describing source and target with size and creation date, the
            target marked when it sits on a virtual directory
        """
        source_date = self.source.creation_date.strftime("%Y-%m-%d %H:%M:%S")
        target_date = self.target.creation_date.strftime("%Y-%m-%d %H:%M:%S")
        target_kind = "[VIRTUAL] " if self.is_virtual else ""

        return (f"Source: {get_relative_path(self.source.path)} ({self.source.size} bytes, {source_date})\n"
                f"Target: {target_kind}{get_relative_path(self.target.path)} ({self.target.size} bytes, {target_date})")


class ColorPrinter:
    """Writes messages to the terminal, in color where the terminal supports it"""

    COLORS = {
        'red': '\033[91m',
        'green': '\033[92m',
        'yellow': '\033[93m',
        'blue': '\033[94m',
        'magenta': '\033[95m',
        'cyan': '\033[96m',
        'white': '\033[97m',
    }
    RESET = '\033[0m'

    def __init__(self):
        self.use_color = self._supports_color()

    def _supports_color(self) -> bool:
        """
        Returns:
            True when the output is a terminal that understands ANSI colors and the
            user did not turn them off through NO_COLOR
        """
        if not hasattr(sys.stdout, 'isatty') or not sys.stdout.isatty():
            return False
        if os.environ.get('NO_COLOR'):
            return False
        if platform.system() == 'Windows':
            return os.environ.get('ANSICON') is not None or 'WT_SESSION' in os.environ

        return True

    def print(self, message: str, color: str = None):
        """
        Write one message.

        Args:
            message: Text to write
            color: Name of a color from COLORS, or None for the terminal default
        """
        if self.use_color and color in self.COLORS:
            print(f"{self.COLORS[color]}{message}{self.RESET}")
        else:
            print(message)

    def error(self, message: str):
        """Args: message: Text of a failure the user has to know about"""
        self.print(f"ERROR: {message}", 'red')

    def warning(self, message: str):
        """Args: message: Text of something surprising that did not stop the run"""
        self.print(f"WARNING: {message}", 'yellow')

    def success(self, message: str):
        """Args: message: Text of a completed operation"""
        self.print(f"SUCCESS: {message}", 'green')

    def info(self, message: str):
        """Args: message: Text of ordinary progress"""
        self.print(f"INFO: {message}", 'cyan')


class StatusTracker:
    """
    Records which files are done so an interrupted run can pick up where it stopped.

    Progress written by an earlier run is always read. Writing can be switched off, which
    a dry run needs: otherwise the preview would mark every file as done and the real run
    afterwards would find nothing left to do.
    """

    def __init__(self, status_file: Path, persist: bool = True):
        """
        Args:
            status_file: File the progress is written to and read from
            persist: Whether this run may write to that file
        """
        self.status_file = status_file
        self.persist = persist
        self.processed = set()
        self.failed = {}
        self.pending = []
        self._has_changes = False
        self.load_status()

    def load_status(self):
        """Read the progress of an earlier run, leaving the state empty when there is none or it is damaged"""
        if not self.status_file.exists():
            return

        try:
            with open(self.status_file, 'r') as f:
                data = json.load(f)

            self.processed = set(data.get('processed', []))
            self.failed = data.get('failed', {})
            self.pending = data.get('pending', [])
            self._has_changes = False
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    def save_status(self):
        """Write the progress, unless nothing changed or this run may not write"""
        if not self._has_changes or not self.persist:
            return

        data = {
            'processed': list(self.processed),
            'failed': self.failed,
            'pending': self.pending,
            'timestamp': datetime.now().isoformat()
        }
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.status_file, 'w') as f:
            json.dump(data, f, indent=2)

        self._has_changes = False

    def mark_processed(self, source: Path, target: Path):
        """
        Note one finished copy in memory; the file is written by save_status.

        Args:
            source: File that was copied or moved
            target: Where it went
        """
        key = self._key(source, target)
        self.processed.add(key)
        self.failed.pop(key, None)
        self._has_changes = True

    def mark_failed(self, source: Path, target: Path, error: str):
        """
        Note one failed copy in memory; the file is written by save_status.

        Args:
            source: File that could not be copied or moved
            target: Where it should have gone
            error: What went wrong
        """
        self.failed[self._key(source, target)] = {
            'source': str(source),
            'target': str(target),
            'error': error,
            'timestamp': datetime.now().isoformat()
        }
        self._has_changes = True

    def is_processed(self, source: Path, target: Path) -> bool:
        """
        Args:
            source: File about to be copied or moved
            target: Where it would go

        Returns:
            True when an earlier run already did exactly this
        """
        return self._key(source, target) in self.processed

    def set_pending(self, operations: List[Tuple[Path, Path]]):
        """
        Record the planned work so an interrupted run can be inspected.

        Args:
            operations: Source and target of every planned copy
        """
        self.pending = [(str(source), str(target)) for source, target in operations]
        self._has_changes = True

    def reset(self):
        """Drop all recorded progress, in memory and on disk, so the next run starts over"""
        self.processed.clear()
        self.failed.clear()
        self.pending.clear()
        self._has_changes = False
        self.cleanup()

    def cleanup(self):
        """Delete the status file"""
        if self.persist and self.status_file.exists():
            self.status_file.unlink()

    def has_existing_progress(self) -> bool:
        """
        Returns:
            True when an earlier run left work that could be resumed
        """
        return bool(self.processed or self.failed or self.pending)

    @staticmethod
    def _key(source: Path, target: Path) -> str:
        """
        Args:
            source: File of the operation
            target: Target of the operation

        Returns:
            The text that identifies this operation in the status file
        """
        return f"{source}|{target}"


class FileOperations:
    """Reads file metadata and moves files around, retrying when the filesystem hiccups"""

    @staticmethod
    def copy_or_move_with_retry(source: Path, target: Path, move: bool, printer: ColorPrinter) -> bool:
        """
        Put one file at its target, creating the directories above it.

        Args:
            source: File to copy or move
            target: Where it should end up
            move: Move instead of copy
            printer: Receives a warning per retry and an error when all attempts fail

        Returns:
            True when the file is at the target, False when every attempt failed
        """
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)

                if move:
                    shutil.move(str(source), str(target))
                else:
                    shutil.copy2(str(source), str(target))

                if target.exists():
                    return True

            except Exception as e:
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    printer.warning(f"Attempt {attempt + 1} failed: {e}. Retrying...")
                    time.sleep(RETRY_DELAY_SECONDS)
                else:
                    printer.error(f"Failed after {MAX_RETRY_ATTEMPTS} attempts: {e}")

        return False

    @staticmethod
    def dates_from_stat(stat_info: os.stat_result) -> Tuple[datetime, datetime]:
        """
        Derive the timestamps of a file from data that was already read.

        Args:
            stat_info: Result of a stat call on the file

        Returns:
            Creation and modification time as local time. Creation time is whatever the
            platform can offer: the birth time on macOS, the change time on Windows, and
            the earlier of change and modification time elsewhere
        """
        if platform.system() == 'Darwin':          # macOS
            # Shares such as NFS or SMB may not report a birth time
            creation_time = datetime.fromtimestamp(getattr(stat_info, 'st_birthtime', stat_info.st_ctime))
        elif platform.system() == 'Windows':
            creation_time = datetime.fromtimestamp(stat_info.st_ctime)
        else:                                      # Linux and others
            creation_time = datetime.fromtimestamp(min(stat_info.st_ctime, stat_info.st_mtime))

        return creation_time, datetime.fromtimestamp(stat_info.st_mtime)

    @staticmethod
    def get_file_dates(file_path: Path) -> Tuple[datetime, datetime]:
        """
        Args:
            file_path: File to read

        Returns:
            Creation and modification time as local time
        """
        return FileOperations.dates_from_stat(file_path.stat())

    @staticmethod
    def get_file_info(file_path: Path) -> FileInfo:
        """
        Read name, size and timestamps of a file with a single stat call.

        Args:
            file_path: File to read

        Returns:
            The collected metadata
        """
        stat_info = file_path.stat()
        creation_date, modification_date = FileOperations.dates_from_stat(stat_info)

        return FileInfo(path=file_path, size=stat_info.st_size, creation_date=creation_date, modification_date=modification_date)


@dataclass
class VirtualFileInfo:
    """Size and timestamps of one file on a virtual directory, without its content"""
    relative_path: str          # its path below the virtual directory, always with '/' as the separator
    size: int
    creation_date: datetime
    modification_date: datetime

    def to_dict(self) -> dict:
        """
        Returns:
            The metadata in the form the cache file stores it
        """
        return {
            'relative_path': self.relative_path,
            'size': self.size,
            'creation_date': self.creation_date.isoformat(),
            'modification_date': self.modification_date.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'VirtualFileInfo':
        """
        Args:
            data: One entry as stored in the cache file

        Returns:
            The metadata it describes

        Raises:
            KeyError, ValueError: The entry is incomplete or malformed
        """
        return cls(
            relative_path=data['relative_path'],
            size=data['size'],
            creation_date=datetime.fromisoformat(data['creation_date']),
            modification_date=datetime.fromisoformat(data['modification_date'])
        )


@dataclass
class CachedRoot:
    """
    What one virtual directory held when it was last read.

    Attributes:
        scanned_at: When the directory was last read
        files: Each file's metadata, keyed by its path below the directory as read
        match_index: Each file's metadata, keyed by the output path it is looked up by
            when it is a virtual source, that is its virtually sorted path (year folder
            plus the path below the directory). Empty for a virtual target, whose files
            are looked up straight from files. Rebuilt on every run, so never stored and
            never stale when the year cutoff changes
    """
    scanned_at: datetime
    files: Dict[str, VirtualFileInfo] = field(default_factory=dict)
    match_index: Dict[str, VirtualFileInfo] = field(default_factory=dict)


def _resolve_virtual_list(directories: List[Path]) -> List[Path]:
    """
    Turn one list of requested virtual directories into absolute paths.

    Args:
        directories: Directories as given on the command line, in any form

    Returns:
        The same directories as absolute paths, without repetitions

    Raises:
        ValueError: A directory is missing, which for a network share means it is not
            mounted and must not be mistaken for a directory that holds nothing; or a
            path is not a directory
    """
    roots: List[Path] = []

    for directory in directories:
        root = directory.expanduser().resolve()

        if not root.exists():
            raise ValueError(f"virtual directory does not exist (share not mounted?): {directory}")
        if not root.is_dir():
            raise ValueError(f"virtual directory is not a directory: {directory}")
        if root not in roots:
            roots.append(root)

    return roots


def resolve_virtual_directories(targets: List[Path], sources: List[Path]) -> Tuple[List[Path], List[Path]]:
    """
    Turn the requested virtual directories into absolute paths that can be compared.

    Args:
        targets: Sorted directories, already in the output year layout
        sources: Unsorted directories, sorted virtually before they are compared

    Returns:
        The targets and the sources, each as absolute paths without repetitions

    Raises:
        ValueError: A directory is missing or not a directory; the same directory is
            named as both a target and a source; or one directory lies inside another,
            in which case a file below both would be reached under two different paths
            and only one of them could ever match a planned target
    """
    resolved_targets = _resolve_virtual_list(targets)
    resolved_sources = _resolve_virtual_list(sources)

    overlap = {str(root) for root in resolved_targets} & {str(root) for root in resolved_sources}
    if overlap:
        raise ValueError(f"virtual directory is given as both a target and a source: {sorted(overlap)[0]}")

    all_roots = resolved_targets + resolved_sources
    for index, root in enumerate(all_roots):
        for other in all_roots[index + 1:]:
            if root in other.parents or other in root.parents:
                raise ValueError(f"virtual directories must not contain each other: {other} and {root}")

    return resolved_targets, resolved_sources


class VirtualDirectoryCache:
    """
    Remembers what the virtual directories hold, so they are read at most once per run.

    A virtual directory is an archive of files to check planned copies against without
    writing to it. It is often on a slow network share, though it can be any directory,
    and only names, sizes and timestamps are read, never file content.

    Both kinds of directory are stored the same way: the metadata of each file, keyed by
    its path below the directory. The kind changes only how a planned copy is matched:

    - A virtual target already holds a finished sort, so its path below the directory is
      already an output path. A planned copy matches when that path equals the copy's
      path below the output directory.
    - A virtual source is not sorted. Each of its files is first placed in the year
      folder it would land in, by the same rules as a real source, and the planned copy
      is matched against that virtually sorted path instead. Sub-folders are kept, so a
      file only matches a planned copy with the same structure below its own root.

    A match counts as a conflict, and the planned copy is not carried out unless the user
    allows it. Only the directories of the current run are consulted, so entries an
    earlier run cached for other directories never influence a later one.
    """

    def __init__(self, cache_file: Path, printer: ColorPrinter, follow_symlinks: bool = True):
        """
        Args:
            cache_file: File the collected metadata is written to and read from
            printer: Receives the progress and warnings of scanning and loading
            follow_symlinks: Descend into directory symlinks while reading a virtual
                directory. A cache reused from an earlier run keeps whatever setting
                built it
        """
        self.cache_file = cache_file
        self.printer = printer
        self.follow_symlinks = follow_symlinks
        self.requested: List[Tuple[Path, bool]] = []    # (absolute directory, sorted) for this run
        self.cached_roots: Dict[str, CachedRoot] = {}   # absolute directory -> what it held
        self.load_cache()

    def set_directories(self, targets: List[Path], sources: List[Path]):
        """
        Record the virtual directories of this run together with the kind of each.

        Paths are resolved to absolute here, because the cache keys its entries by the
        absolute path and this keeps that key correct on its own. Whether a directory
        exists, is a directory, and does not nest another is a user-facing check made
        earlier, so those mistakes can stop the run before it writes anything.

        Args:
            targets: Sorted directories, in the output year layout
            sources: Unsorted directories, sorted virtually before they are compared
        """
        resolved_targets = [(root.expanduser().resolve(), True) for root in targets]
        resolved_sources = [(root.expanduser().resolve(), False) for root in sources]
        self.requested = resolved_targets + resolved_sources

    def has_virtual_directories(self) -> bool:
        """
        Returns:
            True when this run was given at least one virtual directory
        """
        return bool(self.requested)

    def prepare(self, reuse: bool = False, force_update: bool = False):
        """
        Bring the cache in line with the directories of this run.

        The two arguments cover three ways to treat the cache file:

        | reuse | force_update | requested directories | entries of other directories |
        |-------|--------------|-----------------------|------------------------------|
        | False | either       | read again            | dropped                      |
        | True  | False        | taken from the cache  | kept                         |
        | True  | True         | read again            | kept                         |

        Args:
            reuse: Take the cached state of the requested directories as it is and leave
                the entries of other directories in the cache file. This spares the walk
                over a slow directory, at the price of comparing against a state that may
                have aged
            force_update: Read the requested directories again even with reuse, which
                keeps the entries of the other directories but refreshes these
        """
        if not self.requested:
            return

        requested_paths = {str(root) for root, _ in self.requested}

        if not reuse:
            self._drop_roots_except(requested_paths)

        for root, _ in self.requested:
            cached = self.cached_roots.get(str(root))

            if reuse and cached and not force_update:
                scanned = cached.scanned_at.strftime('%Y-%m-%d %H:%M:%S')
                self.printer.warning(f"Using the state of {get_relative_path(root)} as of {scanned} ({len(cached.files)} files)")
                continue

            self.printer.info(f"Reading virtual directory: {get_relative_path(root)}...")
            files = self._scan_root(root)
            self.cached_roots[str(root)] = CachedRoot(scanned_at=datetime.now(), files=files)
            self.printer.info(f"Cached {len(files)} files from {get_relative_path(root)}")

        self.save_cache()

    def build_indexes(self, year_of: Callable[[VirtualFileInfo], int]):
        """
        Build the match_index of each virtual source: the output path find_matches looks
        each of its files up by.

        A virtual target is skipped by the loop below; its files are looked up straight
        from their stored path, so it needs no index. A virtual source is virtually
        sorted here: each file is placed in the year folder it would land in, which gives
        the output path a planned copy is compared against.

        The index is not stored but rebuilt on each run, from the metadata plus this
        run's year cutoff, so a changed cutoff can never leave a stale key behind. Call
        it once after prepare and before the first lookup.

        Args:
            year_of: Returns the year folder a file on a virtual source would land in,
                from its cached metadata alone
        """
        for root, is_sorted in self.requested:
            if is_sorted:
                continue

            cached = self.cached_roots.get(str(root))
            if cached:
                cached.match_index = {f"{year_of(info)}/{path}": info for path, info in cached.files.items()}

    def find_matches(self, relative_target: str) -> List[Tuple[Path, VirtualFileInfo]]:
        """
        Look for a planned target among the virtual directories of this run.

        A sorted target is matched on the path as read; a source on the virtually sorted
        path from build_indexes.

        Args:
            relative_target: Path of the planned copy below the output directory, with
                slashes as separators

        Returns:
            One entry per virtual directory that holds a file at the same output path,
            each with the directory and the cached metadata. Empty when none holds it.
        """
        matches = []

        for root, is_sorted in self.requested:
            cached = self.cached_roots.get(str(root))
            if not cached:
                continue

            existing = cached.files.get(relative_target) if is_sorted else cached.match_index.get(relative_target)
            if existing:
                matches.append((root, existing))

        return matches

    def _drop_roots_except(self, keep: set):
        """
        Remove cached directories that are not part of this run.

        Args:
            keep: Absolute directories, as text, that must stay in the cache
        """
        outdated = [path for path in self.cached_roots if path not in keep]
        for path in outdated:
            del self.cached_roots[path]

        if outdated:
            self.printer.info(f"Dropped {len(outdated)} directory(s) from the cache that this run does not use")

    def _scan_root(self, root: Path) -> Dict[str, VirtualFileInfo]:
        """
        Walk one virtual directory and collect the metadata of every file below it.

        The walk is shared with the source scan. One stat call per file yields its size
        and timestamps, so a slow directory is touched once per file. Files on the skip
        list are left out. Directory symlinks are descended into when the run asks for it.
        Hidden names are kept, so the cache is a faithful snapshot of the directory;
        leaving hidden files out is a source-side choice at runtime.

        Args:
            root: Absolute directory to walk

        Returns:
            The metadata of each file, keyed by its slash separated path below the root.
            Files that cannot be read are reported as a warning and left out
        """
        files: Dict[str, VirtualFileInfo] = {}

        def warn(path: Path, error: OSError):
            self.printer.warning(f"Could not read {get_relative_path(path)}: {error}")

        for entry, relative_path in iter_tree_files(root, self.follow_symlinks, warn):
            if entry.name in SKIP_FILES:
                continue

            try:
                stat_info = entry.stat()
                creation_date, modification_date = FileOperations.dates_from_stat(stat_info)
                files[relative_path] = VirtualFileInfo(relative_path=relative_path, size=stat_info.st_size, creation_date=creation_date, modification_date=modification_date)
            except OSError as error:
                self.printer.warning(f"Could not read {relative_path}: {error}")

        return files

    def load_cache(self):
        """Read the cache file, starting from an empty cache when it is missing or unreadable"""
        if not self.cache_file.exists():
            return

        try:
            with open(self.cache_file, 'r') as f:
                data = json.load(f)

            for root_data in data['roots']:
                files = {}
                for file_data in root_data['files']:
                    info = VirtualFileInfo.from_dict(file_data)
                    files[info.relative_path] = info

                self.cached_roots[root_data['path']] = CachedRoot(
                    scanned_at=datetime.fromisoformat(root_data['scanned_at']),
                    files=files
                )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            self.printer.warning(f"Could not read the virtual cache, it is built again: {e}")
            self.cached_roots.clear()

    def save_cache(self):
        """Write every cached directory to the cache file"""
        data = {
            'timestamp': datetime.now().isoformat(),
            'roots': [
                {
                    'path': path,
                    'scanned_at': cached.scanned_at.isoformat(),
                    'files': [info.to_dict() for info in cached.files.values()]
                }
                for path, cached in self.cached_roots.items()
            ]
        }

        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, 'w') as f:
            json.dump(data, f, indent=2)

    def cleanup(self):
        """Delete the cache file"""
        if self.cache_file.exists():
            self.cache_file.unlink()


def group_conflicts(conflicts: List[ConflictInfo]) -> Dict[Tuple[Path, Path], List[ConflictInfo]]:
    """
    Collect the conflicts of each planned copy.

    Grouping happens once up front for two reasons: the prompt numbers files, not single
    conflicts, so it has to know how many conflicts a file has, and the copy loop then
    looks up the conflicts of each planned copy by its key.

    A planned copy is identified by its source path together with its target in the
    output directory. Every conflict of that copy shares this key, the one in the output
    directory and the ones on the virtual directories alike, because operation_target
    always holds the output-directory path.

    Args:
        conflicts: Every conflict found, in the order they are presented

    Returns:
        The conflicts of each planned copy, keyed by that pair and kept in the order the
        copies were planned. For example, one file that is only in the output directory
        and one that is there and on two virtual directories:

            {
                (Path("src/a.jpg"), Path("out/2024/a.jpg")): [<output-directory conflict>],
                (Path("src/b.jpg"), Path("out/2024/b.jpg")): [<output-directory conflict>,
                                                              <virtual1 conflict>,
                                                              <virtual2 conflict>],
            }
    """
    groups: Dict[Tuple[Path, Path], List[ConflictInfo]] = {}
    for conflict in conflicts:
        groups.setdefault((conflict.source.path, conflict.operation_target), []).append(conflict)

    return groups


def number_conflicts(groups: Dict[Tuple[Path, Path], List[ConflictInfo]]) -> Dict[int, str]:
    """
    Label the conflicts for the prompt, counting the files that need a decision.

    A file in conflict in a single place gets a plain number, such as "3 of 10". A file
    in conflict in several places gets that number with a part added per conflict, such
    as "4.1 of 10" and "4.2 of 10", so the questions of one file are recognisable. The
    total counts the files that need a decision, not the single conflicts.

    A label may in the end never be shown, because an answer can settle a conflict
    without a question: refusing a file hides its later parts (4.2, 4.3), and a standing
    all- or virtual-scope answer hides whole file numbers (5, 6). The labels are fixed
    when they are handed out, so a shown conflict keeps its number instead of being
    renumbered to close the gap.

    Args:
        groups: The conflicts of each planned copy

    Returns:
        The label of each conflict, such as "3 of 10" or "4.1 of 10". A conflict is a
        mutable object and so cannot be a dict key, and two conflicts of one file could
        compare equal, so the key is the object identity from id(); the label is looked
        up the same way where the conflict is shown
    """
    labels = {}

    for file_number, group in enumerate(groups.values(), start=1):
        for position, conflict in enumerate(group, start=1):
            number = str(file_number) if len(group) == 1 else f"{file_number}.{position}"
            labels[id(conflict)] = f"{number} of {len(groups)}"

    return labels


class ConflictResolver:
    """Asks the user what to do with a conflict and reuses answers that reach further"""

    def __init__(self, printer: ColorPrinter, verbose: bool = False):
        """
        Args:
            printer: Where the notes about conflicts settled without a question go
            verbose: Whether those notes are written at all
        """
        self.printer = printer
        self.verbose = verbose
        self.standing_action: Optional[OverwriteAction] = None
        self.standing_scope: Optional[OverwriteScope] = None

    def resolve(self, conflicts: List[ConflictInfo], labels: Dict[int, str]) -> bool:
        """
        Settle every conflict of one planned copy.

        A refusal is an action that does not allow the copy: a plain no, or a conditional
        like larger, older or newer whose condition is not met. The first refusal ends
        the questions for this file and skips it, so declining the copy in the output
        directory never leads to a question about the same file on a virtual directory. An
        answer with the file scope settles the remaining conflicts of the copy without
        asking again.

        Args:
            conflicts: Conflicts of one planned copy, the one in the output directory
                first
            labels: Label of each conflict for the prompt, by object id

        Returns:
            True when the copy may go ahead, False when the file has to be skipped
        """
        for position, conflict in enumerate(conflicts):
            if self.standing_scope and self.standing_scope.covers(conflict):
                action, scope = self.standing_action, self.standing_scope
                self._report(conflict, labels, f"{action.value.full_name}, from the earlier '{scope.value.full_name}' answer")
            else:
                action, scope = self._ask(conflict, labels[id(conflict)])

            remaining = conflicts[position + 1:]

            if not action.allows(conflict):
                self._report_all(remaining, labels, "the file is skipped already")
                return False

            if scope is OverwriteScope.FILE:
                return self._apply_to_rest_of_file(action, remaining, labels)

        return True

    def _ask(self, conflict: ConflictInfo, label: str) -> Tuple[OverwriteAction, OverwriteScope]:
        """
        Put one conflict to the user and read the answer, repeating until it is usable.

        Args:
            conflict: The conflict to describe
            label: Position of this conflict among the files that need a decision

        Returns:
            The chosen action and how far it reaches
        """
        self._print_prompt(conflict, label)

        while True:
            answer = ask("\nEnter action:scope (or just action for this conflict only): ", 'n')
            action_text, _, scope_text = answer.partition(':')

            if not action_text.strip():
                print("An action is required, for example 'n' or 'n:virtual'. Please try again.")
                continue

            action = OverwriteAction.from_input(action_text)
            if not action:
                # A scope typed on its own (e.g. "virtual") is a common slip; a scope
                # cannot stand alone because it does not say whether to copy or skip
                mistaken_scope = OverwriteScope.from_input(action_text)
                if mistaken_scope and mistaken_scope is not OverwriteScope.CURRENT:
                    print(f"'{action_text.strip()}' is a scope, not an action. Put an action first, for example 'n:{mistaken_scope.value.full_name}'. Please try again.")
                else:
                    print("Invalid action. Please try again.")
                continue

            scope = OverwriteScope.from_input(scope_text)
            if not scope:
                print("Invalid scope. Please try again.")
                continue

            if scope in (OverwriteScope.ALL, OverwriteScope.VIRTUAL):
                self.standing_action = action
                self.standing_scope = scope

            return action, scope

    def _apply_to_rest_of_file(self, action: OverwriteAction, conflicts: List[ConflictInfo], labels: Dict[int, str]) -> bool:
        """
        Use one answer for the conflicts of the same file that were not asked about.

        Args:
            action: The action the user chose for that file
            conflicts: Conflicts of the file that are still open
            labels: Label of each conflict, by object id

        Returns:
            True when the action allows all of them, False as soon as one is refused
        """
        for conflict in conflicts:
            allowed = action.allows(conflict)
            outcome = "" if allowed else ", the file is skipped"
            self._report(conflict, labels, f"{action.value.full_name}, from the answer for this file{outcome}")

            if not allowed:
                return False

        return True

    def _report(self, conflict: ConflictInfo, labels: Dict[int, str], decision: str):
        """
        Note one conflict that was settled without a question, in verbose runs only.

        Args:
            conflict: The conflict that was not asked about
            labels: Label of each conflict, by object id
            decision: What happened to it and where that came from
        """
        if not self.verbose:
            return

        marker = "[VIRTUAL] " if conflict.is_virtual else ""
        self.printer.info(f"Conflict {labels[id(conflict)]} not asked ({decision}): {get_relative_path(conflict.source.path)} vs {marker}{get_relative_path(conflict.target.path)}")

    def _report_all(self, conflicts: List[ConflictInfo], labels: Dict[int, str], decision: str):
        """
        Note several conflicts that were settled without a question, in verbose runs only.

        Args:
            conflicts: Conflicts that were not asked about
            labels: Label of each conflict, by object id
            decision: What happened to them and where that came from
        """
        for conflict in conflicts:
            self._report(conflict, labels, decision)

    def _print_prompt(self, conflict: ConflictInfo, label: str):
        """
        Describe one conflict and list the possible answers.

        Args:
            conflict: The conflict to describe
            label: Position of this conflict among the files that need a decision
        """
        print(f"\nConflict {label}:")
        print(conflict.format_summary())

        if conflict.is_virtual:
            print("The virtual directory is never written to; yes copies the file into the output directory anyway.")

        print("\nActions:")
        for action in OverwriteAction:
            print(f"  {action.value.shorthand}/{action.value.full_name} - {action.value.description}")

        print("\nScope:")
        for scope in OverwriteScope:
            name = f"{scope.value.shorthand}/{scope.value.full_name}" if scope.value.shorthand else "(default)"
            print(f"  {name} - {scope.value.description}")

        print("\nExamples: 'y:all', 'larger:f', 'n:virtual', 'n' (action only)")


@dataclass
class SortOptions:
    """
    Everything that steers one sorting run.

    Attributes:
        dry_run: Show what would happen and write no media files and no status file. The
            virtual cache is still refreshed, so a dry run can double as a way to fill it
        move_files: Move the files instead of copying them
        media_only: Handle images and videos only, by their extension
        exclude_hidden: Leave out names that start with a dot
        follow_source_symlinks: Descend into directory symlinks while scanning the sources
        verbose: Report every file, not only warnings and questions
        resume: Continue an interrupted run without asking
        virtual_targets: Absolute sorted directories to check for conflicts without writing to them
        virtual_sources: Absolute unsorted directories, sorted virtually before they are checked
        follow_virtual_symlinks: Descend into directory symlinks while reading the virtual directories
        keep_virtual_cache: Take the cached state of those directories instead of reading them again
        update_virtual_cache: Read those directories again even with keep_virtual_cache
        year_cutoff: When a file counts as part of the previous year
        status_file: Where the progress of the run is recorded
        virtual_cache_file: Where the metadata of the virtual directories is kept
    """
    dry_run: bool = False
    move_files: bool = False
    media_only: bool = False
    exclude_hidden: bool = False
    follow_source_symlinks: bool = True
    verbose: bool = False
    resume: bool = False
    virtual_targets: List[Path] = field(default_factory=list)
    virtual_sources: List[Path] = field(default_factory=list)
    follow_virtual_symlinks: bool = True
    keep_virtual_cache: bool = False
    update_virtual_cache: bool = False
    year_cutoff: YearCutoff = field(default_factory=YearCutoff)
    status_file: Path = Path(STATUS_FILE_NAME)
    virtual_cache_file: Path = Path(VIRTUAL_CACHE_FILE_NAME)


class MediaSorter:
    """Plans and carries out one sorting run"""

    def __init__(self, options: SortOptions = None, printer: ColorPrinter = None):
        """
        Args:
            options: How this run should behave; the defaults copy everything, ask about
                every conflict and use no virtual directories
            printer: Where messages go
        """
        self.options = options or SortOptions()
        self.printer = printer or ColorPrinter()
        self.date_matcher = DatePatternMatcher()
        self.conflict_resolver = ConflictResolver(self.printer, self.options.verbose)

        # A dry run must leave no progress behind, or the real run afterwards would
        # consider every previewed file as done
        self.status_tracker = StatusTracker(self.options.status_file, persist=not self.options.dry_run)

        self.virtual_cache = VirtualDirectoryCache(self.options.virtual_cache_file, self.printer, self.options.follow_virtual_symlinks)
        self.virtual_cache.set_directories(self.options.virtual_targets, self.options.virtual_sources)

        self.processed_files = 0
        self.skipped_files = 0
        self._interrupted = False

        signal.signal(signal.SIGINT, self._signal_handler)

    def process_files(self, source_dirs: List[Path], target_dir: Path):
        """
        Sort the sources into the output directory, from planning to summary.

        Args:
            source_dirs: Directories to read the files from
            target_dir: Directory the year folders are created in
        """
        self.check_resume()
        self.virtual_cache.prepare(reuse=self.options.keep_virtual_cache, force_update=self.options.update_virtual_cache)
        self.virtual_cache.build_indexes(self._virtual_source_year)

        operations = self.collect_operations(source_dirs, target_dir)
        if not operations:
            self.printer.info("No files to process")
            return

        conflicts = self.check_conflicts(operations, target_dir)
        self.process_operations(operations, conflicts)
        self._print_summary()
        self._offer_cleanup()

    def check_resume(self) -> None:
        """
        Decide whether the progress of an earlier run is continued.

        The outcome shows in the status tracker, not in a return value: answering no
        throws that progress away, in memory and on disk, so the run starts over instead
        of silently continuing.
        """
        if not self.status_tracker.has_existing_progress():
            return

        if self.options.resume:
            self.printer.info("Resuming from the progress of the previous run")
            return

        self.printer.info("Found existing progress from previous run.")
        while True:
            choice = ask("Do you want to resume from where you left off? (y/n): ", 'y')
            if choice in ('y', 'yes'):
                return
            if choice in ('n', 'no'):
                self.status_tracker.reset()
                self.printer.info("Previous progress discarded, starting from scratch")
                return

    def _resolve_year(self, name: str, creation_date_getter: Callable[[], datetime], describe: str, notify: bool) -> int:
        """
        Work out the year folder from a file name and, where needed, a creation date.

        The date comes from the name when a known naming convention is recognised, and
        from the creation date otherwise. When the name holds a date but no time of day,
        the time is taken from the creation date, which matters only on the cutoff day.
        The creation date is fetched through a callback, so a name that already carries a
        full date and time costs no extra read.

        Args:
            name: File name to read the date from
            creation_date_getter: Returns the creation date, called only when it is
                actually needed
            describe: How to refer to the file in the notes
            notify: Whether to write the notes about where the date came from. The scan
                of the sources sets this; the silent virtual sorting of a whole directory
                does not

        Returns:
            The year of its folder
        """
        found = self.date_matcher.match(name)

        if found:
            moment = found.moment
            if not found.has_time:
                creation_date = creation_date_getter()
                moment = moment.replace(hour=creation_date.hour, minute=creation_date.minute, second=creation_date.second)
            if notify and self.options.verbose:
                self.printer.info(f"Date from name ({found.pattern_name}) for: {describe}")

            return self.options.year_cutoff.resolve_year(moment)

        creation_date = creation_date_getter()
        if notify:
            self.printer.warning(f"Using creation date ({creation_date.strftime('%Y-%m-%d %H:%M:%S')}) for: {describe}")

        return self.options.year_cutoff.resolve_year(creation_date)

    def get_year_from_file(self, file_path: Path) -> int:
        """
        Decide which year folder a source file belongs to, reading it at most once.

        Args:
            file_path: File to judge

        Returns:
            The year of its folder
        """
        return self._resolve_year(
            file_path.name,
            lambda: FileOperations.get_file_dates(file_path)[0],
            get_relative_path(file_path),
            notify=True
        )

    def _virtual_source_year(self, info: VirtualFileInfo) -> int:
        """
        Decide which year folder a file on an unsorted virtual source would belong to.

        Only the cached metadata is used, never a fresh read of the file, and the notes
        are left out because a whole directory is sorted at once.

        Args:
            info: Cached metadata of one file below a virtual source

        Returns:
            The year of its folder
        """
        name = info.relative_path.rsplit('/', 1)[-1]
        return self._resolve_year(name, lambda: info.creation_date, info.relative_path, notify=False)

    def build_target_path(self, source_path: Path, source_root: Path, target_root: Path, year: int) -> Path:
        """
        Place a source file in its year folder, keeping the directories around it.

        Args:
            source_path: File in the source directory
            source_root: Source directory it was found in
            target_root: Output directory
            year: Year folder the file belongs to

        Returns:
            The path the file should be written to
        """
        return target_root / str(year) / source_path.relative_to(source_root)

    def collect_operations(self, source_dirs: List[Path], target_dir: Path) -> List[Tuple[Path, Path]]:
        """
        Walk the sources and plan where each file goes.

        Files on the skip lists, hidden files when they are excluded, files of other
        types when only media is wanted, and files an earlier run already handled are
        left out.

        Args:
            source_dirs: Directories to walk
            target_dir: Directory the year folders are created in

        Returns:
            Source and target of every planned copy, in the order they were found
        """
        operations = []

        def warn(path: Path, error: OSError):
            self.printer.warning(f"Could not read {get_relative_path(path)}: {error}")

        for source_dir in source_dirs:
            self.printer.info(f"Scanning {get_relative_path(source_dir)}...")

            for entry, relative_path in iter_tree_files(source_dir, self.options.follow_source_symlinks, warn):
                if self._interrupted:
                    break

                file_path = Path(entry.path)
                reason = get_skip_reason(Path(relative_path), self.options.exclude_hidden)
                if reason:
                    if self.options.verbose:
                        self.printer.info(f"Skipping {reason}: {get_relative_path(file_path)}")
                    continue

                if self.options.media_only and file_path.suffix.lower() not in MEDIA_EXTENSIONS:
                    if self.options.verbose:
                        self.printer.info(f"Skipping non-media file: {get_relative_path(file_path)}")
                    self.skipped_files += 1
                    continue

                year = self.get_year_from_file(file_path)
                target_path = self.build_target_path(file_path, source_dir, target_dir, year)

                if self.status_tracker.is_processed(file_path, target_path):
                    continue

                operations.append((file_path, target_path))

        return operations

    def check_conflicts(self, operations: List[Tuple[Path, Path]], target_dir: Path) -> List[ConflictInfo]:
        """
        Find every planned copy that would land on a file that is already there.

        A copy can be blocked by the output directory and by any virtual directory that
        holds the same path, so it can produce several conflicts. They are collected per
        planned copy, the one in the output directory first, which is the order the user
        is asked in.

        Args:
            operations: Source and target of every planned copy
            target_dir: Output directory, used to map a target onto the virtual directories

        Returns:
            Every conflict found, in the order they are presented
        """
        conflicts = []

        for source, target in operations:
            source_info = None

            if target.exists():
                source_info = FileOperations.get_file_info(source)
                conflicts.append(ConflictInfo(source=source_info, target=FileOperations.get_file_info(target), operation_target=target))

            if not self.virtual_cache.has_virtual_directories():
                continue

            try:
                relative_target = target.relative_to(target_dir).as_posix()
            except ValueError:
                continue                # a target outside the output directory has no virtual counterpart

            for root, existing in self.virtual_cache.find_matches(relative_target):
                source_info = source_info or FileOperations.get_file_info(source)
                virtual_file = FileInfo(path=root / existing.relative_path, size=existing.size, creation_date=existing.creation_date, modification_date=existing.modification_date)
                conflicts.append(ConflictInfo(source=source_info, target=virtual_file, operation_target=target, is_virtual=True))

        return conflicts

    def process_operations(self, operations: List[Tuple[Path, Path]], conflicts: List[ConflictInfo]):
        """
        Carry out the planned copies, asking about the conflicts on the way.

        Args:
            operations: Source and target of every planned copy
            conflicts: Every conflict found for those copies
        """
        self.status_tracker.set_pending(operations)

        conflicts_by_operation = group_conflicts(conflicts)
        labels = number_conflicts(conflicts_by_operation)

        if conflicts:
            print()
            self.printer.warning(f"Found {len(conflicts)} conflicts on {len(conflicts_by_operation)} files to resolve")

        unsaved_operations = 0

        for source, target in operations:
            if self._interrupted:
                break

            if not self.conflict_resolver.resolve(conflicts_by_operation.get((source, target), []), labels):
                if self.options.verbose:
                    self.printer.info(f"Skipped (conflict): {get_relative_path(source)}")
                self.skipped_files += 1
                continue

            self._run_operation(source, target)

            unsaved_operations += 1
            if unsaved_operations >= STATUS_SAVE_INTERVAL:
                self.status_tracker.save_status()
                unsaved_operations = 0

        if unsaved_operations:
            self.status_tracker.save_status()

    def _run_operation(self, source: Path, target: Path):
        """
        Copy or move one file and record the outcome.

        A failure is reported and noted, and the run goes on with the next file.

        Args:
            source: File to copy or move
            target: Where it should end up
        """
        if self.options.dry_run:
            action_str = "Would move" if self.options.move_files else "Would copy"
            self.printer.info(f"{action_str}: {get_relative_path(source)} -> {get_relative_path(target)}")
            self.processed_files += 1
            self.status_tracker.mark_processed(source, target)
            return

        if FileOperations.copy_or_move_with_retry(source, target, self.options.move_files, self.printer):
            action_str = "Moved" if self.options.move_files else "Copied"
            if self.options.verbose:
                self.printer.success(f"{action_str}: {get_relative_path(source)} -> {get_relative_path(target)}")
            self.processed_files += 1
            self.status_tracker.mark_processed(source, target)
        else:
            action_str = "move" if self.options.move_files else "copy"
            self.printer.warning(f"Skipping {get_relative_path(source)} after failed {action_str}, continuing with the rest")
            self.status_tracker.mark_failed(source, target, "Operation failed")

    def _signal_handler(self, signum, frame):
        """
        Save the progress when the user interrupts the run.

        Args:
            signum: Number of the signal that arrived
            frame: Stack frame at the time of the signal
        """
        self._interrupted = True
        self.printer.warning("\nInterrupted! Saving progress...")
        self.status_tracker.save_status()
        sys.exit(130)                   # 128 plus SIGINT, the usual code for an interrupted program

    def _print_summary(self):
        """Report how many files were handled, skipped and failed"""
        print("\n" + "=" * 80)
        self.printer.info(f"Total files processed: {self.processed_files}")
        self.printer.info(f"Total files skipped: {self.skipped_files}")

        if self.status_tracker.failed:
            self.printer.error(f"Failed operations: {len(self.status_tracker.failed)}")
            for info in self.status_tracker.failed.values():
                source = get_relative_path(Path(info['source']))
                target = get_relative_path(Path(info['target']))
                self.printer.error(f"  {source} -> {target}: {info['error']}")

        if self.options.dry_run:
            self.printer.warning("This was a dry run - no files were actually moved/copied")

    def _offer_cleanup(self):
        """Offer to delete the files this run leaves behind, each only when it exists"""
        if not self._interrupted and self.status_tracker.persist and self.status_tracker.status_file.exists():
            if ask("\nDelete status file? (y/n): ", 'n') in ('y', 'yes'):
                self.status_tracker.cleanup()
                self.printer.info("Status file deleted.")

        if self.virtual_cache.has_virtual_directories() and self.virtual_cache.cache_file.exists():
            if ask("\nDelete virtual directory cache? (y/n): ", 'n') in ('y', 'yes'):
                self.virtual_cache.cleanup()
                self.printer.info("Virtual directory cache deleted.")


def build_parser() -> argparse.ArgumentParser:
    """
    Returns:
        The command line parser, including the usage examples shown after the options
    """
    parser = argparse.ArgumentParser(
        description="Sort media files into year-based folders",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/source
  %(prog)s /path/to/source1 /path/to/source2 -o /path/to/output
  %(prog)s /path/to/source --dry-run
  %(prog)s /path/to/source --move --media-only
  %(prog)s /path/to/source --exclude-hidden                        # Exclude hidden files/directories
  %(prog)s /path/to/source --resume                                # Resume interrupted operation
  %(prog)s /path/to/source --verbose                               # Show all file operations
  %(prog)s /path/to/source --vt /mnt/nas1 --vt /mnt/nas2           # Check two slow directories
  %(prog)s /path/to/source --vt /mnt/nas1 --keep-virtual-cache     # Do not read them again
  %(prog)s /path/to/source --vs /mnt/dump                          # Check an unsorted archive too
  %(prog)s --build-virtual-cache --vt /mnt/nas1                    # Only fill the cache, sort nothing
  %(prog)s /path/to/source --new-year-cutoff 2025-01-01T14:00:00Z  # Move the new year boundary

  Media Sorter  Copyright (C) 2025  Marcel Schmalzl
This program comes with ABSOLUTELY NO WARRANTY
This is free software, and you are welcome to redistribute it under certain conditions."""
    )

    parser.add_argument('sources', nargs='*', type=Path,
                        help='Source directories to sort, at least one unless --build-virtual-cache is given')
    parser.add_argument('-o', '--output', type=Path,
                        help='Output directory (default: same level as first source)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without actually doing it')
    parser.add_argument('--move', action='store_true', help='Move files instead of copying them')
    parser.add_argument('--media-only', action='store_true',
                        help='Only process media files (photos, videos, gifs)')
    parser.add_argument('--exclude-hidden', action='store_true',
                        help='Exclude hidden files and directories (included by default)')
    parser.add_argument('--no-follow-source-symlinks', action='store_false', dest='follow_source_symlinks',
                        help='Do not descend into directory symlinks while scanning the sources (they are '
                             'followed by default; a symlink to a file is always included)')
    parser.add_argument('--resume', action='store_true',
                        help='Resume from previous interrupted operation without asking')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose output (show all file operations)')
    parser.add_argument('--new-year-cutoff', metavar='ISO_TIMESTAMP',
                        help='Files dated on this day but before this time belong to the previous year, as ISO '
                             '8601 timestamp such as 2025-01-01T14:00:00Z; the year is ignored, the time is '
                             f'local (default: {YearCutoff()})')
    parser.add_argument('--virtual-target', '--vt', action='append', type=Path, metavar='DIR',
                        dest='virtual_targets',
                        help='Directory already in the sorted year layout, often on a slow network share. It '
                             'is only read, never written to, and checked for conflicts from a metadata cache. '
                             'Repeat for several directories')
    parser.add_argument('--virtual-source', '--vs', action='append', type=Path, metavar='DIR',
                        dest='virtual_sources',
                        help='Like --virtual-target, but for an unsorted directory: its files are placed in '
                             'their year folders virtually before the check, so an archive that is not sorted '
                             'yet can be used too. Repeat for several directories')
    parser.add_argument('--no-follow-virtual-symlinks', action='store_false', dest='follow_virtual_symlinks',
                        help='Do not descend into directory symlinks while reading the virtual directories (they '
                             'are followed by default; a symlink to a file is always included)')
    parser.add_argument('--keep-virtual-cache', action='store_true',
                        help='Take the cached state of the given virtual directories instead of reading them again, '
                             'and keep the cached entries of directories that are not part of this run '
                             '(default: read them again and drop the entries of the others)')
    parser.add_argument('--update-virtual-cache', action='store_true',
                        help='Read the given virtual directories again even with --keep-virtual-cache, so only the '
                             'entries of the directories that are not part of this run are kept. Without '
                             '--keep-virtual-cache this is what happens anyway')
    parser.add_argument('--build-virtual-cache', action='store_true',
                        help='Only read the given virtual directories into the cache and exit, without sorting '
                             'anything. Needs no source and no output directory')
    parser.add_argument('--status-file', type=Path,
                        help=f'Status file for resume capability (default: {STATUS_FILE_NAME})')
    parser.add_argument('--virtual-cache-file', type=Path,
                        help=f'Metadata cache of the virtual directories (default: {VIRTUAL_CACHE_FILE_NAME})')

    return parser


def build_options(args: argparse.Namespace) -> SortOptions:
    """
    Turn parsed arguments into the settings of a run.

    Args:
        args: Result of parsing the command line

    Returns:
        The settings for MediaSorter

    Raises:
        ValueError: The cutoff or one of the virtual directories cannot be used
    """
    try:
        cutoff = YearCutoff.from_iso(args.new_year_cutoff) if args.new_year_cutoff else YearCutoff()
    except ValueError as error:
        raise ValueError(f"--new-year-cutoff {error}") from error

    virtual_targets, virtual_sources = resolve_virtual_directories(args.virtual_targets or [], args.virtual_sources or [])

    return SortOptions(
        dry_run=args.dry_run,
        move_files=args.move,
        media_only=args.media_only,
        exclude_hidden=args.exclude_hidden,
        follow_source_symlinks=args.follow_source_symlinks,
        verbose=args.verbose,
        resume=args.resume,
        virtual_targets=virtual_targets,
        virtual_sources=virtual_sources,
        follow_virtual_symlinks=args.follow_virtual_symlinks,
        keep_virtual_cache=args.keep_virtual_cache,
        update_virtual_cache=args.update_virtual_cache,
        year_cutoff=cutoff,
        status_file=args.status_file or Path(STATUS_FILE_NAME),
        virtual_cache_file=args.virtual_cache_file or Path(VIRTUAL_CACHE_FILE_NAME)
    )


def print_configuration(printer: ColorPrinter, options: SortOptions, sources: List[Path], output_dir: Path):
    """
    Show what the run is about to do.

    Args:
        printer: Where the lines go
        options: Settings of the run
        sources: Directories the files are read from
        output_dir: Directory the year folders are created in
    """
    printer.info("Media Sort Configuration:")
    printer.info(f"\tSource directories: {', '.join(get_relative_path(s) for s in sources)}")
    printer.info(f"\tOutput directory: {get_relative_path(output_dir)}")
    printer.info(f"\tMode: {'Move' if options.move_files else 'Copy'}")
    printer.info(f"\tMedia only: {'Yes' if options.media_only else 'No'}")
    printer.info(f"\tHidden files: {'Excluded' if options.exclude_hidden else 'Included'}")
    printer.info(f"\tFollow source symlinks: {'Yes' if options.follow_source_symlinks else 'No'}")
    printer.info(f"\tDry run: {'Yes' if options.dry_run else 'No'}")
    printer.info(f"\tVerbose: {'Yes' if options.verbose else 'No'}")
    printer.info(f"\tNew year cutoff: {options.year_cutoff}")

    if options.virtual_targets:
        printer.info(f"\tVirtual targets (sorted): {', '.join(get_relative_path(t) for t in options.virtual_targets)}")
    if options.virtual_sources:
        printer.info(f"\tVirtual sources (unsorted): {', '.join(get_relative_path(t) for t in options.virtual_sources)}")
    if options.virtual_targets or options.virtual_sources:
        printer.info(f"\tVirtual directories are: {describe_cache_use(options)}")
        printer.info(f"\tFollow virtual symlinks: {'Yes' if options.follow_virtual_symlinks else 'No'}")
    if options.resume:
        printer.info("\tResuming from previous operation")

    print()


def describe_cache_use(options: SortOptions) -> str:
    """
    Args:
        options: Settings of the run

    Returns:
        How this run treats the virtual directories, in the words of the three cases the
        cache knows
    """
    if not options.keep_virtual_cache:
        return "read again, entries of other directories dropped"
    if options.update_virtual_cache:
        return "read again, entries of other directories kept"

    return "taken from the cache, entries of other directories kept"


def build_virtual_cache(options: SortOptions, printer: ColorPrinter):
    """
    Read the virtual directories into the cache without sorting anything.

    Args:
        options: Settings of the run, of which only the virtual directories and the
            cache settings are used
        printer: Where the progress goes
    """
    printer.info("Building the virtual directory cache only, no files are sorted:")
    if options.virtual_targets:
        printer.info(f"\tVirtual targets (sorted): {', '.join(get_relative_path(t) for t in options.virtual_targets)}")
    if options.virtual_sources:
        printer.info(f"\tVirtual sources (unsorted): {', '.join(get_relative_path(t) for t in options.virtual_sources)}")
    printer.info(f"\tCache file: {get_relative_path(options.virtual_cache_file)}")
    print()

    cache = VirtualDirectoryCache(options.virtual_cache_file, printer, options.follow_virtual_symlinks)
    cache.set_directories(options.virtual_targets, options.virtual_sources)
    cache.prepare(reuse=options.keep_virtual_cache, force_update=options.update_virtual_cache)


def main():
    """Read the command line, check it and either build the cache or run the sorter"""
    parser = build_parser()
    args = parser.parse_args()

    if not args.sources and not args.build_virtual_cache:
        parser.error("at least one source directory is required unless --build-virtual-cache is given")
    if args.build_virtual_cache and not args.virtual_targets and not args.virtual_sources:
        parser.error("--build-virtual-cache needs at least one --virtual-target or --virtual-source")

    for source in args.sources:
        if not source.exists():
            print(f"Error: Source directory does not exist: {source}")
            sys.exit(1)
        if not source.is_dir():
            print(f"Error: Source is not a directory: {source}")
            sys.exit(1)

    # Nothing has been written yet, so an unusable setting can stop the run without loss
    try:
        options = build_options(args)
    except ValueError as error:
        print(f"Error: {error}")
        sys.exit(1)

    printer = ColorPrinter()

    if args.build_virtual_cache:
        build_virtual_cache(options, printer)
        return

    if args.output:
        output_dir = args.output
    else:
        first_source = args.sources[0].resolve()
        output_dir = first_source.parent / f"{first_source.name}_sorted"

    if not options.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    print_configuration(printer, options, args.sources, output_dir)
    MediaSorter(options, printer).process_files(args.sources, output_dir)


if __name__ == '__main__':
    main()
