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
from typing import List, Tuple, Optional, Dict, Set, Callable, Any, NamedTuple
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
import time
import platform


NEW_YEAR_CUTOFF_HOUR = 14                  # Files created on Jan 1st before 14:00 belong to previous year
MIN_VALID_YEAR = 1970                      # Unix epoch start
MAX_VALID_YEAR = 3000                      # Upper limit for validating year
MIN_VALID_MONTH = 1
MAX_VALID_MONTH = 12
MIN_VALID_DAY = 1
MAX_VALID_DAY = 31
MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1
STATUS_FILE_NAME = ".media_sort_status.json"

MEDIA_EXTENSIONS = {
    # Images
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', 
    '.heic', '.heif', '.raw', '.cr2', '.nef', '.arw', '.dng', '.svg',
    # Videos
    '.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm', '.m4v', 
    '.mpg', '.mpeg', '.3gp', '.3g2', '.mts', '.m2ts', '.vob', '.ogv',
    # Animated
    '.gif', '.webp'
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
    """Convert a path to relative path from current working directory."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        # Path is not relative to current directory, return as-is
        return str(path)


class DatePattern:
    """Represents a date extraction pattern"""
    
    def __init__(self, name: str, regex: str, extractor: Callable[[re.Match], Optional[Tuple[int, int, int]]]):
        self.name = name
        self.regex = re.compile(regex)
        self.extractor = extractor
    
    def extract_date(self, filename: str) -> Optional[Tuple[int, int, int]]:
        """Extract date from filename if pattern matches"""
        match = self.regex.search(filename)
        if match:
            try:
                return self.extractor(match)
            except:
                return None
        return None


class DatePatternMatcher:
    """Manages date pattern matching"""
    
    def __init__(self):
        self.patterns = [
            # PXL files (Google Pixel)
            DatePattern(
                name="PXL_YYYYMMDD_HHMMSS",
                regex=r'PXL_(\d{4})(\d{2})(\d{2})_\d{6}',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
            # Screenshot pattern
            DatePattern(
                name="Screenshot_YYYYMMDD-HHMMSS",
                regex=r'Screenshot_(\d{4})(\d{2})(\d{2})-\d{6}',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
            # IMG pattern with full date
            DatePattern(
                name="IMG_YYYYMMDD_HHMMSS",
                regex=r'IMG_(\d{4})(\d{2})(\d{2})_\d{6}',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
            # WhatsApp pattern
            DatePattern(
                name="IMG-YYYYMMDD-WA",
                regex=r'IMG-(\d{4})(\d{2})(\d{2})-WA',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
            # VID pattern
            DatePattern(
                name="VID_YYYYMMDD_HHMMSS",
                regex=r'VID_(\d{4})(\d{2})(\d{2})_\d{6}',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
            # DSC pattern (Digital Still Camera)
            DatePattern(
                name="DSC_YYYYMMDD",
                regex=r'DSC_(\d{4})(\d{2})(\d{2})',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
            # Generic date patterns
            DatePattern(
                name="YYYY-MM-DD",
                regex=r'(\d{4})-(\d{2})-(\d{2})',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
            DatePattern(
                name="YYYYMMDD",
                regex=r'(?:^|[_\-\s])(\d{4})(\d{2})(\d{2})(?:[_\-\s]|$)',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
            # Date at start of filename
            DatePattern(
                name="YYYYMMDD_start",
                regex=r'^(\d{4})(\d{2})(\d{2})[_\-]',
                extractor=lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ),
        ]

    def extract_date(self, filename: str) -> Optional[Tuple[int, int, int]]:
        """Extract date from filename using all patterns"""
        for pattern in self.patterns:
            date_tuple = pattern.extract_date(filename)
            if date_tuple and self._is_valid_date(*date_tuple):
                return date_tuple
        return None
    
    def _is_valid_date(self, year: int, month: int, day: int) -> bool:
        """Validate date components"""
        return (MIN_VALID_YEAR <= year <= MAX_VALID_YEAR and 
                MIN_VALID_MONTH <= month <= MAX_VALID_MONTH and 
                MIN_VALID_DAY <= day <= MAX_VALID_DAY)


class ActionInfo(NamedTuple):
    """Information about an overwrite action"""
    shorthand: str
    full_name: str
    description: str


class ScopeInfo(NamedTuple):
    """Information about an overwrite scope"""
    shorthand: str
    full_name: str
    description: str


class OverwriteAction(Enum):
    """Enumeration for overwrite actions"""
    YES = ActionInfo(shorthand="y", full_name="yes", description="Overwrite this file")
    NO = ActionInfo(shorthand="n", full_name="no", description="Skip this file")
    LARGER = ActionInfo(shorthand="larger", full_name="larger", description="Overwrite only if source is larger")
    OLDER = ActionInfo(shorthand="older", full_name="older", description="Overwrite only if source is older")
    NEWER = ActionInfo(shorthand="newer", full_name="newer", description="Overwrite only if source is newer")
    
    @classmethod
    def from_input(cls, input_str: str) -> Optional['OverwriteAction']:
        """
        Get action from user input
        
        This method takes a user input string and matches it against all available
        actions' shorthand and full names to find the corresponding action.
        """
        input_lower = input_str.lower().strip()
        for action in cls:
            if input_lower in [action.value.shorthand, action.value.full_name]:
                return action
        return None


class OverwriteScope(Enum):
    """Enumeration for overwrite scope"""
    CURRENT = ScopeInfo(shorthand="", full_name="", description="Current file only")
    ALL = ScopeInfo(shorthand="a", full_name="all", description="Apply to all remaining conflicts")
    FOLLOWING = ScopeInfo(shorthand="f", full_name="following", description="Apply to this and all following conflicts")
    
    @classmethod
    def from_input(cls, input_str: str) -> Optional['OverwriteScope']:
        """Get scope from user input"""
        input_lower = input_str.lower().strip()
        if not input_lower:
            return cls.CURRENT
        for scope in cls:
            if input_lower in [scope.value.shorthand, scope.value.full_name]:
                return scope
        return None


@dataclass
class FileInfo:
    """Information about a file"""
    path: Path
    size: int
    creation_date: datetime
    modification_date: datetime
    
    def get_size_mb(self) -> float:
        """Get file size in MB"""
        return self.size / (1024 * 1024)


@dataclass
class ConflictInfo:
    """Information about a file conflict"""
    source: FileInfo
    target: FileInfo
    
    @property
    def same_size(self) -> bool:
        """Check if files have same size"""
        return self.source.size == self.target.size
    
    @property
    def same_creation_date(self) -> bool:
        """Check if files have same creation date"""
        return self.source.creation_date == self.target.creation_date
    
    def format_summary(self) -> str:
        """Format conflict as a single line summary"""
        return (f"{get_relative_path(self.source.path)} ({self.source.size}B, {self.source.creation_date:%Y-%m-%d %H:%M}) -> "
                f"{get_relative_path(self.target.path)} ({self.target.size}B, {self.target.creation_date:%Y-%m-%d %H:%M}) "
                f"[Size: {'=' if self.same_size else '≠'}, Date: {'=' if self.same_creation_date else '≠'}]")


class ColorPrinter:
    """Handles colored output if terminal supports it"""
    
    def __init__(self):
        self.use_color = self._supports_color()
        
    def _supports_color(self) -> bool:
        """Check if terminal supports colored output"""
        if not hasattr(sys.stdout, 'isatty'):
            return False
        if not sys.stdout.isatty():
            return False
        if os.environ.get('NO_COLOR'):
            return False
        if platform.system() == 'Windows':
            return os.environ.get('ANSICON') is not None or 'WT_SESSION' in os.environ
        return True
    
    def print(self, message: str, color: str = None):
        """Print message with optional color"""
        if self.use_color and color:
            colors = {
                'red': '\033[91m',
                'green': '\033[92m',
                'yellow': '\033[93m',
                'blue': '\033[94m',
                'magenta': '\033[95m',
                'cyan': '\033[96m',
                'white': '\033[97m',
                'reset': '\033[0m'
            }
            print(f"{colors.get(color, '')}{message}{colors['reset']}")
        else:
            print(message)
    
    def error(self, message: str):
        self.print(f"ERROR: {message}", 'red')
    
    def warning(self, message: str):
        self.print(f"WARNING: {message}", 'yellow')
    
    def success(self, message: str):
        self.print(f"SUCCESS: {message}", 'green')
    
    def info(self, message: str):
        self.print(f"INFO: {message}", 'cyan')


class StatusTracker:
    """Tracks progress and allows resuming"""
    
    def __init__(self, status_file: Path):
        self.status_file = status_file
        self.processed = set()
        self.failed = {}
        self.pending = []
        self._has_changes = False
        self.load_status()
    
    def load_status(self):
        """Load status from file if exists"""
        if self.status_file.exists():
            try:
                with open(self.status_file, 'r') as f:
                    data = json.load(f)
                    self.processed = set(data.get('processed', []))
                    self.failed = data.get('failed', {})
                    self.pending = data.get('pending', [])
                    self._has_changes = False
            except:
                pass
    
    def save_status(self):
        """Save current status to file (only if there are changes)"""
        if self._has_changes:
            data = {
                'processed': list(self.processed),
                'failed': self.failed,
                'pending': self.pending,
                'timestamp': datetime.now().isoformat()
            }
            with open(self.status_file, 'w') as f:
                json.dump(data, f, indent=2)
            self._has_changes = False
    
    def mark_processed(self, source: str, target: str):
        """Mark a file as processed (doesn't save immediately)"""
        key = f"{source}|{target}"
        self.processed.add(key)
        if key in self.failed:
            del self.failed[key]
        self._has_changes = True
    
    def mark_failed(self, source: str, target: str, error: str):
        """Mark a file as failed (doesn't save immediately)"""
        key = f"{source}|{target}"
        self.failed[key] = {
            'source': source,
            'target': target,
            'error': error,
            'timestamp': datetime.now().isoformat()
        }
        self._has_changes = True
    
    def is_processed(self, source: str, target: str) -> bool:
        """Check if a file was already processed"""
        return f"{source}|{target}" in self.processed
    
    def set_pending(self, operations: List[Tuple[Path, Path]]):
        """Set pending operations"""
        self.pending = [(str(s), str(t)) for s, t in operations]
        self._has_changes = True
    
    def cleanup(self):
        """Remove status file"""
        if self.status_file.exists():
            self.status_file.unlink()
    
    def has_existing_progress(self) -> bool:
        """Check if there's existing progress to resume"""
        return bool(self.processed) or bool(self.failed) or bool(self.pending)


class FileOperations:
    """Handles file operations with retry logic"""
    
    @staticmethod
    def copy_or_move_with_retry(source: Path, target: Path, move: bool, 
                                printer: ColorPrinter, max_attempts: int = MAX_RETRY_ATTEMPTS) -> bool:
        """Copy or move file with retry logic"""
        for attempt in range(max_attempts):
            try:
                # Create target directory if needed
                target.parent.mkdir(parents=True, exist_ok=True)
                
                # Get source timestamps before operation
                source_stat = source.stat()
                
                if move:
                    shutil.move(str(source), str(target))
                else:
                    shutil.copy2(str(source), str(target))
                
                # Verify file exists at target
                if target.exists():
                    return True
                    
            except Exception as e:
                if attempt < max_attempts - 1:
                    printer.warning(f"Attempt {attempt + 1} failed: {e}. Retrying...")
                    time.sleep(RETRY_DELAY_SECONDS)
                else:
                    printer.error(f"Failed after {max_attempts} attempts: {e}")
                    return False
        
        return False
    
    @staticmethod
    def get_file_dates(file_path: Path) -> Tuple[datetime, datetime]:
        """Get creation and modification dates for a file (cross-platform)"""
        stat_info = file_path.stat()
        
        # Platform-specific creation time
        if platform.system() == 'Darwin':          # macOS
            creation_time = datetime.fromtimestamp(stat_info.st_birthtime)
        elif platform.system() == 'Windows':
            creation_time = datetime.fromtimestamp(stat_info.st_ctime)
        else:                                       # Linux and others
            # Use the earlier of ctime and mtime
            creation_time = datetime.fromtimestamp(min(stat_info.st_ctime, stat_info.st_mtime))
        
        modification_time = datetime.fromtimestamp(stat_info.st_mtime)
        return creation_time, modification_time


class ConflictResolver:
    """Handles conflict resolution"""
    
    # LUT for overwrite decisions
    OVERWRITE_DECISIONS = {
        OverwriteAction.YES: lambda conflict: True,
        OverwriteAction.NO: lambda conflict: False,
        OverwriteAction.LARGER: lambda conflict: conflict.source.size > conflict.target.size,
        OverwriteAction.OLDER: lambda conflict: conflict.source.creation_date < conflict.target.creation_date,
        OverwriteAction.NEWER: lambda conflict: conflict.source.creation_date > conflict.target.creation_date,
    }
    
    def __init__(self, printer: ColorPrinter):
        self.printer = printer
        self.current_action: Optional[OverwriteAction] = None
        self.current_scope: Optional[OverwriteScope] = None
    
    def get_user_choice(self, conflict: ConflictInfo, index: int, total: int) -> Tuple[OverwriteAction, OverwriteScope]:
        """Get user choice for conflict resolution"""
        # Use persistent choice if applicable
        if self.current_scope == OverwriteScope.ALL:
            return self.current_action, self.current_scope
        
        print(f"\nConflict {index + 1} of {total}:")
        print(conflict.format_summary())
        
        # Display action options
        print("\nActions:")
        for action in OverwriteAction:
            info = action.value
            print(f"  {info.shorthand}/{info.full_name} - {info.description}")
        
        # Display scope options
        print("\nScope:")
        for scope in OverwriteScope:
            info = scope.value
            if info.shorthand:
                print(f"  {info.shorthand}/{info.full_name} - {info.description}")
            else:
                print(f"  (default) - {info.description}")
        
        print("\nExamples: 'y:all', 'larger:f', 'n' (action only)")
        
        # Get user input
        while True:
            choice = input("\nEnter action:scope (or just action for current file only): ").strip().lower()
            
            # Parse input - split on colon
            parts = choice.split(':', 1)  # Split on first colon only
            action_input = parts[0].strip()
            scope_input = parts[1].strip() if len(parts) > 1 else ""
            
            if not action_input:
                continue
            
            # Get action
            action = OverwriteAction.from_input(action_input)
            if not action:
                print("Invalid action. Please try again.")
                continue
            
            # Get scope
            scope = OverwriteScope.CURRENT
            if scope_input:
                scope = OverwriteScope.from_input(scope_input)
                if not scope:
                    print("Invalid scope. Please try again.")
                    continue
            
            # Update persistent choice if needed
            if scope in [OverwriteScope.ALL, OverwriteScope.FOLLOWING]:
                self.current_action = action
                self.current_scope = scope
            
            return action, scope
    
    def should_overwrite(self, conflict: ConflictInfo, action: OverwriteAction) -> bool:
        """Determine if file should be overwritten based on action using lookup table"""
        decision_func = self.OVERWRITE_DECISIONS.get(action)
        if decision_func:
            return decision_func(conflict)
        return False


class MediaSorter:
    """Main class for sorting media files"""
    
    def __init__(self, dry_run: bool = False, move_files: bool = False, 
                 media_only: bool = False, exclude_hidden: bool = False,
                 verbose: bool = False,
                 printer: ColorPrinter = None, status_file: Path = None):
        self.dry_run = dry_run
        self.move_files = move_files
        self.media_only = media_only
        self.exclude_hidden = exclude_hidden
        self.verbose = verbose
        self.printer = printer or ColorPrinter()
        self.date_matcher = DatePatternMatcher()
        self.conflict_resolver = ConflictResolver(self.printer)
        self.status_tracker = StatusTracker(status_file or Path(STATUS_FILE_NAME))
        self.processed_files = 0
        self.skipped_files = 0
        self._interrupted = False
        
        # Set up signal handler for interruption
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle interruption signal"""
        self._interrupted = True
        self.printer.warning("\nInterrupted! Saving progress...")
        self.status_tracker.save_status()
        sys.exit(0)
    
    def _should_skip_path(self, path: Path) -> bool:
        """Check if a path should be skipped based on hidden files/directories settings"""
        if path.name in SKIP_FILES:
            return True
        
        # Always skip certain directories (check all parts of the path)
        for part in path.parts:
            if part in SKIP_DIRECTORIES:
                return True
            
        if self.exclude_hidden:
            # Check all parts of the path for hidden directories
            for part in path.parts:
                if part.startswith('.') and part != '.':
                    return True
        return False
    
    def check_resume(self) -> bool:
        """Check if we should resume from existing progress"""
        if self.status_tracker.has_existing_progress():
            self.printer.info("Found existing progress from previous run.")
            while True:
                choice = input("Do you want to resume from where you left off? (y/n): ").strip().lower()
                if choice in ['y', 'yes']:
                    return True
                elif choice in ['n', 'no']:
                    # Ask if they want to delete the status file
                    delete = input("Delete the existing progress file? (y/n): ").strip().lower()
                    if delete in ['y', 'yes']:
                        self.status_tracker.cleanup()
                        self.status_tracker = StatusTracker(self.status_tracker.status_file)
                    return False
        return False
    
    def get_year_from_file(self, file_path: Path) -> int:
        """Get year from filename or creation date"""
        # Try filename first
        date_tuple = self.date_matcher.extract_date(file_path.name)
        if date_tuple:
            year, month, day = date_tuple
            # Handle New Year's cutoff for Jan 1st files
            if month == 1 and day == 1:
                creation_date, _ = FileOperations.get_file_dates(file_path)
                if creation_date.hour < NEW_YEAR_CUTOFF_HOUR:
                    year -= 1
            return year
        
        # Fall back to creation date
        creation_date, _ = FileOperations.get_file_dates(file_path)
        self.printer.warning(f"Using creation date ({creation_date.strftime('%Y-%m-%d %H:%M:%S')}) for: {get_relative_path(file_path)}")
        
        year = creation_date.year
        # Handle New Year's cutoff
        if creation_date.month == 1 and creation_date.day == 1 and creation_date.hour < NEW_YEAR_CUTOFF_HOUR:
            year -= 1
        
        return year
    
    def build_target_path(self, source_path: Path, source_root: Path, target_root: Path, year: int) -> Path:
        """Build target path maintaining directory structure"""
        relative_path = source_path.relative_to(source_root)
        return target_root / str(year) / relative_path
    
    def collect_operations(self, source_dirs: List[Path], target_dir: Path) -> List[Tuple[Path, Path]]:
        """Collect all file operations to be performed"""
        operations = []
        
        for source_dir in source_dirs:
            self.printer.info(f"Scanning {get_relative_path(source_dir)}...")
            
            for file_path in source_dir.rglob('*'):
                if self._interrupted:
                    break
                    
                if not file_path.is_file():
                    continue
                
                if self._should_skip_path(file_path):
                    if self.verbose:
                        if file_path.name in SKIP_FILES:
                            self.printer.info(f"Skipping system file: {get_relative_path(file_path)}")
                        elif any(part in SKIP_DIRECTORIES for part in file_path.parts):
                            self.printer.info(f"Skipping system directory: {get_relative_path(file_path)}")
                        else:
                            self.printer.info(f"Skipping hidden path: {get_relative_path(file_path)}")
                    continue
                
                # Skip if already processed
                year = self.get_year_from_file(file_path)
                target_path = self.build_target_path(file_path, source_dir, target_dir, year)
                
                if self.status_tracker.is_processed(str(file_path), str(target_path)):
                    continue
                
                if self.media_only and file_path.suffix.lower() not in MEDIA_EXTENSIONS:
                    if self.verbose:
                        self.printer.info(f"Skipping non-media file: {get_relative_path(file_path)}")
                    self.skipped_files += 1
                    continue
                
                operations.append((file_path, target_path))
        
        return operations
    
    def check_conflicts(self, operations: List[Tuple[Path, Path]]) -> List[ConflictInfo]:
        """Check for conflicts and return list of conflicts"""
        conflicts = []
        
        for source, target in operations:
            if target.exists():
                source_info = FileInfo(
                    path=source,
                    size=source.stat().st_size,
                    creation_date=FileOperations.get_file_dates(source)[0],
                    modification_date=FileOperations.get_file_dates(source)[1]
                )
                target_info = FileInfo(
                    path=target,
                    size=target.stat().st_size,
                    creation_date=FileOperations.get_file_dates(target)[0],
                    modification_date=FileOperations.get_file_dates(target)[1]
                )
                conflicts.append(ConflictInfo(source=source_info, target=target_info))
        
        return conflicts
    
    def process_operations(self, operations: List[Tuple[Path, Path]], conflicts: List[ConflictInfo]):
        """Process all operations with conflict resolution"""
        # Save pending operations for resume capability
        self.status_tracker.set_pending(operations)
        
        # Create a set of conflict paths for quick lookup
        conflict_paths = {(c.source.path, c.target.path) for c in conflicts}
        
        if conflicts:
            self.printer.warning(f"\nFound {len(conflicts)} conflicts")
        
        # Process in batches to reduce status file writes
        batch_size = 100
        batch_count = 0
        
        for source, target in operations:
            if self._interrupted:
                break
            
            # Check if there's a conflict
            if (source, target) in conflict_paths:
                # Find the conflict object
                conflict = next(c for c in conflicts if c.source.path == source and c.target.path == target)
                conflict_index = conflicts.index(conflict)
                
                action, scope = self.conflict_resolver.get_user_choice(
                    conflict, conflict_index, len(conflicts)
                )
                
                if not self.conflict_resolver.should_overwrite(conflict, action):
                    self.skipped_files += 1
                    continue
            
            # Execute operation
            if self.dry_run:
                action_str = "Would move" if self.move_files else "Would copy"
                self.printer.info(f"{action_str}: {get_relative_path(source)} -> {get_relative_path(target)}")
            else:
                success = FileOperations.copy_or_move_with_retry(
                    source, target, self.move_files, self.printer
                )
                
                if success:
                    action_str = "Moved" if self.move_files else "Copied"
                    if self.verbose:
                        self.printer.success(f"{action_str}: {get_relative_path(source)} -> {get_relative_path(target)}")
                    self.processed_files += 1
                    self.status_tracker.mark_processed(str(source), str(target))
                else:
                    self.status_tracker.mark_failed(str(source), str(target), "Copy/move failed")
                    self.skipped_files += 1
            
            # Save progress periodically
            batch_count += 1
            if batch_count >= batch_size:
                self.status_tracker.save_status()
                batch_count = 0
        
        # Save any remaining progress
        if batch_count > 0:
            self.status_tracker.save_status()
    
    def process_files(self, source_dirs: List[Path], target_dir: Path):
        """Main processing method"""
        # Check if we should resume
        self.check_resume()
        
        # Collect operations
        operations = self.collect_operations(source_dirs, target_dir)
        
        if not operations:
            self.printer.info("No files to process")
            return
        
        # Check conflicts
        conflicts = self.check_conflicts(operations)
        
        # Process operations
        self.process_operations(operations, conflicts)
        
        # Print summary
        self._print_summary()
        
        # Handle status file cleanup
        if not self.dry_run and not self._interrupted:
            if self.status_tracker.failed:
                self.printer.warning("Some operations failed. Status file kept for review.")
            else:
                # Ask if status file should be deleted
                if self.status_tracker.status_file.exists():
                    delete = input("\nAll operations completed successfully. Delete status file? (y/n): ").strip().lower()
                    if delete in ['y', 'yes']:
                        self.status_tracker.cleanup()
                        self.printer.info("Status file deleted.")
    
    def _print_summary(self):
        """Print processing summary"""
        print("\n" + "="*80)
        self.printer.info(f"Total files processed: {self.processed_files}")
        self.printer.info(f"Total files skipped: {self.skipped_files}")
        
        if self.status_tracker.failed:
            self.printer.error(f"Failed operations: {len(self.status_tracker.failed)}")
            for key, info in self.status_tracker.failed.items():
                source_path = Path(info['source'])
                target_path = Path(info['target'])
                self.printer.error(f"  {get_relative_path(source_path)} -> {get_relative_path(target_path)}: {info['error']}")
        
        if self.dry_run:
            self.printer.warning("This was a dry run - no files were actually moved/copied")


def main():
    parser = argparse.ArgumentParser(
        description="Sort media files into year-based folders",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/source
  %(prog)s /path/to/source1 /path/to/source2 -o /path/to/output
  %(prog)s /path/to/source --dry-run
  %(prog)s /path/to/source --move --media-only
  %(prog)s /path/to/source --exclude-hidden  # Exclude hidden files/directories
  %(prog)s /path/to/source --resume  # Resume interrupted operation
  %(prog)s /path/to/source --verbose  # Show all file operations

  Media Sorter  Copyright (C) 2025  Marcel Schmalzl
This program comes with ABSOLUTELY NO WARRANTY
This is free software, and you are welcome to redistribute it under certain conditions."""
    )
    
    parser.add_argument('sources', nargs='+', type=Path,
                        help='Source directories to sort')
    parser.add_argument('-o', '--output', type=Path,
                        help='Output directory (default: same level as first source)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without actually doing it')
    parser.add_argument('--move', action='store_true',
                        help='Move files instead of copying them')
    parser.add_argument('--media-only', action='store_true',
                        help='Only process media files (photos, videos, gifs)')
    parser.add_argument('--exclude-hidden', action='store_true',
                        help='Exclude hidden files and directories (included by default)')
    parser.add_argument('--resume', action='store_true',
                        help='Resume from previous interrupted operation')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose output (show all file operations)')
    parser.add_argument('--status-file', type=Path,
                        help=f'Status file for resume capability (default: {STATUS_FILE_NAME})')
    
    args = parser.parse_args()
    
    # Validate source directories
    for source in args.sources:
        if not source.exists():
            print(f"Error: Source directory does not exist: {source}")
            sys.exit(1)
        if not source.is_dir():
            print(f"Error: Source is not a directory: {source}")
            sys.exit(1)
    
    # Determine output directory
    if args.output:
        output_dir = args.output
    else:
        # Default to same level as first source
        first_source = args.sources[0].resolve()
        output_dir = first_source.parent / f"{first_source.name}_sorted"
    
    # Create output directory if it doesn't exist
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize printer and sorter
    printer = ColorPrinter()
    sorter = MediaSorter(
        dry_run=args.dry_run,
        move_files=args.move,
        media_only=args.media_only,
        exclude_hidden=args.exclude_hidden,
        verbose=args.verbose,
        printer=printer,
        status_file=args.status_file
    )
    
    # Print configuration
    printer.info("Media Sort Configuration:")
    printer.info(f"\tSource directories: {', '.join(get_relative_path(s) for s in args.sources)}")
    printer.info(f"\tOutput directory: {get_relative_path(output_dir)}")
    printer.info(f"\tMode: {'Move' if args.move else 'Copy'}")
    printer.info(f"\tMedia only: {'Yes' if args.media_only else 'No'}")
    printer.info(f"\tHidden files: {'Excluded' if args.exclude_hidden else 'Included'}")
    printer.info(f"\tDry run: {'Yes' if args.dry_run else 'No'}")
    printer.info(f"\tVerbose: {'Yes' if args.verbose else 'No'}")
    if args.resume:
        printer.info("\tResuming from previous operation")
    print()
    
    # Process files
    sorter.process_files(args.sources, output_dir)


if __name__ == '__main__':
    main()
