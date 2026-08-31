#!/usr/bin/env python3
"""
Automated checks for the media sorter, built on the fixtures in the test directory.

Run all of them with:
    python3 test_media_sort.py
    python3 -m unittest test_media_sort -v

The checks never write into the fixtures. Output directories, status files and metadata
caches are created in a temporary directory that is removed again afterwards.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from media_sort import (
    ColorPrinter,
    ConflictInfo,
    ConflictResolver,
    DatePatternMatcher,
    FileInfo,
    MediaSorter,
    OverwriteAction,
    OverwriteScope,
    SkipReason,
    SortOptions,
    VirtualDirectoryCache,
    YearCutoff,
    get_relative_path,
    get_skip_reason,
    group_conflicts,
    main,
    number_conflicts,
    resolve_virtual_directories,
)

FIXTURES = Path(__file__).resolve().parent / 'test'
SOURCE1 = FIXTURES / 'source1'
SOURCE2 = FIXTURES / 'source2'
VIRTUAL1 = FIXTURES / 'virtual1'
VIRTUAL2 = FIXTURES / 'virtual2'
VIRTUAL_SOURCE1 = FIXTURES / 'virtual_source1'

# Where every fixture with a date in its name has to end up, relative to the output
# directory. Whatever the file clock says, these must not move
EXPECTED_TARGETS = {
    SOURCE1: {
        '.hidden/PXL_20240101_120000000.jpg': '2023/.hidden/PXL_20240101_120000000.jpg',
        'photos/PXL_20231225_090000000.MP.jpg': '2023/photos/PXL_20231225_090000000.MP.jpg',
        'photos/PXL_20240315_143022456.jpg': '2024/photos/PXL_20240315_143022456.jpg',
        'photos/PXL_20240815_120000000.jpg': '2024/photos/PXL_20240815_120000000.jpg',
        'photos/PXL_20240815_143000 (1).jpg': '2024/photos/PXL_20240815_143000 (1).jpg',
        'photos/PXL_20250101_120000000.NIGHT.jpg': '2024/photos/PXL_20250101_120000000.NIGHT.jpg',
        'photos/PXL_20250101_130000000.jpg': '2024/photos/PXL_20250101_130000000.jpg',
        'photos/PXL_20250101_150000000.jpg': '2025/photos/PXL_20250101_150000000.jpg',
        'photos/IMG_20240815_160000.jpg': '2024/photos/IMG_20240815_160000.jpg',
        'photos/DSC_20240210_001.jpg': '2024/photos/DSC_20240210_001.jpg',
        'photos/2024-06-15_beach.jpg': '2024/photos/2024-06-15_beach.jpg',
        'photos/20240615-vacation.png': '2024/photos/20240615-vacation.png',
        'photos/20240615_sunset.jpg': '2024/photos/20240615_sunset.jpg',
        'photos/family & friends 2024-08-15.jpg': '2024/photos/family & friends 2024-08-15.jpg',
        'photos/2024/summer/PXL_20240701_120000000.jpg': '2024/photos/2024/summer/PXL_20240701_120000000.jpg',
        'videos/PXL_20240720_183045123.TS.mp4': '2024/videos/PXL_20240720_183045123.TS.mp4',
        'videos/VID_20240815_143000.mp4': '2024/videos/VID_20240815_143000.mp4',
    },
    SOURCE2: {
        'PXL_20240815_120000000.jpg': '2024/PXL_20240815_120000000.jpg',
        'screenshots/Screenshot_20231231-235959.png': '2023/screenshots/Screenshot_20231231-235959.png',
        'screenshots/Screenshot_20240615-143022.png': '2024/screenshots/Screenshot_20240615-143022.png',
        'whatsapp/IMG-20240915-WA0001.jpg': '2024/whatsapp/IMG-20240915-WA0001.jpg',
        'whatsapp/VID-20240915-WA0002.mp4': '2024/whatsapp/VID-20240915-WA0002.mp4',
    },
}

# Fixtures without a date in their name; their year follows the file clock, so only
# their presence can be checked, never their year
CLOCK_DEPENDENT = {
    '.hidden/.secret_photo.jpg',
    'document.pdf',
    'photos/DSCF1234.jpg',
    'photos/IMG_9876.jpg',
    'photos/random_uuid_photo.jpg',
    'photos/vacation_photo.jpg',
    'videos/family_video.mp4',
    'some_notes.txt',
}

# Paths below virtual1 that must block a copy of source1
VIRTUAL1_CONFLICTS = {
    '2023/.hidden/PXL_20240101_120000000.jpg',
    '2023/photos/PXL_20231225_090000000.MP.jpg',
    '2024/photos/2024/summer/PXL_20240701_120000000.jpg',
    '2024/photos/IMG_20240815_160000.jpg',
    '2024/photos/PXL_20240815_120000000.jpg',
    '2024/photos/PXL_20250101_130000000.jpg',
    '2024/videos/VID_20240815_143000.mp4',
    '2025/photos/PXL_20250101_150000000.jpg',
}

# Output paths that a copy of source1 lands on once virtual_source1 is virtually sorted.
# The two files below virtual_source1/photos share source1's photos/ structure and its
# dates, so they collide; its file under random/ sorts elsewhere and does not
VIRTUAL_SOURCE1_CONFLICTS = {
    '2023/photos/PXL_20231225_090000000.MP.jpg',
    '2024/photos/PXL_20240815_120000000.jpg',
}


class Answers:
    """Replaces input() with a fixed list of answers and remembers every question asked"""

    def __init__(self, *answers, default: str = 'n'):
        """
        Args:
            answers: Answers to give, in order
            default: Answer to give once the list is used up
        """
        self.queue = list(answers)
        self.default = default
        self.questions = []

    def __call__(self, prompt: str = '') -> str:
        """
        Args:
            prompt: Text the code would have shown

        Returns:
            The next scripted answer, or the default
        """
        self.questions.append(prompt)
        return self.queue.pop(0) if self.queue else self.default


def make_options(work_dir: Path, **overrides) -> SortOptions:
    """
    Build run settings that keep every written file inside a temporary directory.

    Args:
        work_dir: Directory for the status file and the metadata cache
        overrides: Settings to change from the default

    Returns:
        The settings to hand to MediaSorter
    """
    defaults = {
        'status_file': work_dir / 'status.json',
        'virtual_cache_file': work_dir / 'cache.json',
    }
    defaults.update(overrides)

    return SortOptions(**defaults)


def plan_run(options: SortOptions, sources, output_dir: Path):
    """
    Run only the planning half of a sort, which asks no questions.

    Args:
        options: Settings of the run
        sources: Directories to read from
        output_dir: Directory the year folders would be created in

    Returns:
        The sorter, the planned copies and the conflicts they run into
    """
    sorter = MediaSorter(options, QuietPrinter())
    sorter.virtual_cache.prepare(reuse=options.keep_virtual_cache, force_update=options.update_virtual_cache)
    sorter.virtual_cache.build_indexes(sorter._virtual_source_year)
    operations = sorter.collect_operations(sources, output_dir)

    return sorter, operations, sorter.check_conflicts(operations, output_dir)


def make_conflict(name: str, source_size: int = 1, target_size: int = 1, is_virtual: bool = False) -> ConflictInfo:
    """
    Build one conflict without touching the filesystem.

    The source is always the newer of the two files, so the older and newer actions have
    something to tell apart.

    Args:
        name: File name used for source and target
        source_size: Size of the file that would be written
        target_size: Size of the file that is already there
        is_virtual: Whether the target sits on a virtual target

    Returns:
        The conflict to hand to a resolver
    """
    early, late = datetime(2024, 1, 1, 8, 0), datetime(2025, 1, 1, 8, 0)
    source = FileInfo(path=Path('src') / name, size=source_size, creation_date=late, modification_date=late)
    target = FileInfo(path=Path('dst') / name, size=target_size, creation_date=early, modification_date=early)

    return ConflictInfo(source=source, target=target, operation_target=Path('dst') / name, is_virtual=is_virtual)


def execute_run(options: SortOptions, sources, output_dir: Path, answers: Answers = None):
    """
    Run a complete sort with scripted answers and captured output.

    Args:
        options: Settings of the run
        sources: Directories to read from
        output_dir: Directory the year folders are created in
        answers: Answers for the prompts, all declined when left out

    Returns:
        The sorter after the run and everything it printed
    """
    answers = answers or Answers()
    written = io.StringIO()

    with mock.patch('builtins.input', answers), contextlib.redirect_stdout(written):
        sorter = MediaSorter(options, ColorPrinter())
        sorter.process_files(sources, output_dir)

    return sorter, written.getvalue()


class QuietPrinter(ColorPrinter):
    """A printer that keeps the test output readable by writing nothing"""

    def print(self, message: str, color: str = None):
        """Args: message, color: ignored"""


class RecordingPrinter(ColorPrinter):
    """A printer that collects the messages instead of writing them"""

    def __init__(self):
        super().__init__()
        self.messages = []

    def print(self, message: str, color: str = None):
        """Args: message: kept for the assertions; color: ignored"""
        self.messages.append(message)


class TestYearCutoff(unittest.TestCase):
    """The rule that keeps New Year's Eve pictures with the year that was celebrated"""

    def test_default_moves_only_the_hours_before_the_cutoff(self):
        cutoff = YearCutoff()
        self.assertEqual(cutoff.resolve_year(datetime(2025, 1, 1, 3, 0)), 2024)
        self.assertEqual(cutoff.resolve_year(datetime(2025, 1, 1, 13, 59, 59)), 2024)
        self.assertEqual(cutoff.resolve_year(datetime(2025, 1, 1, 14, 0)), 2025)
        self.assertEqual(cutoff.resolve_year(datetime(2025, 1, 1, 15, 0)), 2025)

    def test_other_days_keep_their_year(self):
        cutoff = YearCutoff()
        for moment in (datetime(2024, 12, 31, 23, 59), datetime(2025, 1, 2, 1, 0), datetime(2025, 6, 15, 3, 0)):
            self.assertEqual(cutoff.resolve_year(moment), moment.year, moment)

    def test_a_cutoff_late_in_the_year_touches_only_that_day(self):
        cutoff = YearCutoff.from_iso('2020-12-31T20:00:00')
        self.assertEqual(cutoff.resolve_year(datetime(2024, 12, 31, 18, 0)), 2023)
        self.assertEqual(cutoff.resolve_year(datetime(2024, 12, 31, 21, 0)), 2024)
        self.assertEqual(cutoff.resolve_year(datetime(2024, 6, 15, 10, 0)), 2024)

    def test_the_year_of_the_timestamp_is_ignored(self):
        self.assertEqual(YearCutoff.from_iso('1999-01-01T14:00:00Z'), YearCutoff.from_iso('2035-01-01T14:00:00'))

    def test_timezone_suffix_is_accepted(self):
        self.assertEqual(YearCutoff.from_iso('2025-01-01T14:00:00Z'), YearCutoff())
        self.assertEqual(YearCutoff.from_iso('2025-01-01T14:00:00+02:00'), YearCutoff())

    def test_minutes_and_seconds_are_kept(self):
        cutoff = YearCutoff.from_iso('2025-01-01T12:30:15')
        self.assertEqual(cutoff.resolve_year(datetime(2025, 1, 1, 12, 30, 14)), 2024)
        self.assertEqual(cutoff.resolve_year(datetime(2025, 1, 1, 12, 30, 16)), 2025)

    def test_unusable_timestamps_are_rejected(self):
        for text in ('yesterday', '', '01-01T14:00', '2025-02-29T10:00:00Z'):
            with self.assertRaises(ValueError, msg=text):
                YearCutoff.from_iso(text)

    def test_text_form_leaves_out_the_year(self):
        self.assertEqual(str(YearCutoff()), '01-01T14:00:00')


class TestDatePatternMatcher(unittest.TestCase):
    """Reading the date out of a file name"""

    def setUp(self):
        self.matcher = DatePatternMatcher()

    def test_names_with_a_time_of_day(self):
        expected = {
            'PXL_20250101_130000000.jpg': '2025-01-01T13:00:00',
            'PXL_20240815_143000 (1).jpg': '2024-08-15T14:30:00',
            'PXL_20231225_090000000.MP.jpg': '2023-12-25T09:00:00',
            'IMG_20240815_160000.jpg': '2024-08-15T16:00:00',
            'VID_20240815_143000.mp4': '2024-08-15T14:30:00',
            'Screenshot_20231231-235959.png': '2023-12-31T23:59:59',
        }
        for name, moment in expected.items():
            found = self.matcher.match(name)
            self.assertIsNotNone(found, name)
            self.assertEqual(found.moment.isoformat(), moment, name)
            self.assertTrue(found.has_time, name)

    def test_names_with_a_date_only(self):
        expected = {
            'IMG-20240915-WA0001.jpg': '2024-09-15T00:00:00',
            'DSC_20240210_001.jpg': '2024-02-10T00:00:00',
            '2024-06-15_beach.jpg': '2024-06-15T00:00:00',
            '20240615_sunset.jpg': '2024-06-15T00:00:00',
            'family & friends 2024-08-15.jpg': '2024-08-15T00:00:00',
        }
        for name, moment in expected.items():
            found = self.matcher.match(name)
            self.assertIsNotNone(found, name)
            self.assertEqual(found.moment.isoformat(), moment, name)
            self.assertFalse(found.has_time, name)

    def test_names_without_a_date(self):
        for name in ('DSCF1234.jpg', 'IMG_9876.jpg', 'vacation_photo.jpg', 'family_video.mp4'):
            self.assertIsNone(self.matcher.match(name), name)

    def test_digits_that_form_no_real_date(self):
        self.assertIsNone(self.matcher.match('PXL_20241332_120000000.jpg'))
        self.assertIsNone(self.matcher.match('IMG_20240230_120000.jpg'))

    def test_years_outside_the_plausible_range(self):
        self.assertIsNone(self.matcher.match('IMG_19000101_120000.jpg'))

    def test_the_matching_convention_is_named(self):
        self.assertEqual(self.matcher.match('PXL_20240815_120000000.jpg').pattern_name, 'PXL_YYYYMMDD_HHMMSS')


class TestSkipReason(unittest.TestCase):
    """Which files are left alone"""

    def test_ordinary_file_is_processed(self):
        self.assertIsNone(get_skip_reason(Path('photos/holiday.jpg'), exclude_hidden=False))

    def test_system_files_and_directories(self):
        self.assertEqual(get_skip_reason(Path('.DS_Store'), False), SkipReason.SYSTEM_FILE)
        self.assertEqual(get_skip_reason(Path('photos/Thumbs.db'), False), SkipReason.SYSTEM_FILE)
        self.assertEqual(get_skip_reason(Path('__MACOSX/holiday.jpg'), False), SkipReason.SYSTEM_DIRECTORY)
        self.assertEqual(get_skip_reason(Path('a/.git/config.jpg'), False), SkipReason.SYSTEM_DIRECTORY)

    def test_hidden_paths_only_when_excluded(self):
        self.assertIsNone(get_skip_reason(Path('.hidden/holiday.jpg'), exclude_hidden=False))
        self.assertEqual(get_skip_reason(Path('.hidden/holiday.jpg'), exclude_hidden=True), SkipReason.HIDDEN)
        self.assertEqual(get_skip_reason(Path('.secret.jpg'), exclude_hidden=True), SkipReason.HIDDEN)

    def test_a_hidden_scan_root_does_not_hide_its_content(self):
        source_root = Path('/home/someone/.local/pictures')
        file_path = source_root / 'holiday.jpg'
        self.assertIsNone(get_skip_reason(file_path.relative_to(source_root), exclude_hidden=True))


class TestResolveVirtualDirectories(unittest.TestCase):
    """Checking the directories a run is pointed at"""

    def test_paths_become_absolute_and_unique(self):
        targets, sources = resolve_virtual_directories([VIRTUAL1, Path(VIRTUAL1), VIRTUAL1 / '..' / 'virtual1'], [])
        self.assertEqual(targets, [VIRTUAL1.resolve()])
        self.assertEqual(sources, [])

    def test_targets_and_sources_are_kept_apart(self):
        targets, sources = resolve_virtual_directories([VIRTUAL1], [VIRTUAL_SOURCE1])
        self.assertEqual((targets, sources), ([VIRTUAL1.resolve()], [VIRTUAL_SOURCE1.resolve()]))

    def test_missing_directory_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            resolve_virtual_directories([Path('/mnt/definitely_not_mounted')], [])
        self.assertIn('does not exist', str(caught.exception))

    def test_file_is_refused(self):
        with self.assertRaises(ValueError):
            resolve_virtual_directories([], [SOURCE1 / 'document.pdf'])

    def test_the_same_directory_as_target_and_source_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            resolve_virtual_directories([VIRTUAL1], [VIRTUAL1])
        self.assertIn('both a target and a source', str(caught.exception))

    def test_nested_directories_are_refused(self):
        for pair in ([VIRTUAL1, VIRTUAL1 / '2024'], [VIRTUAL1 / '2024', VIRTUAL1]):
            with self.assertRaises(ValueError) as caught:
                resolve_virtual_directories(pair, [])
            self.assertIn('must not contain each other', str(caught.exception))

    def test_a_source_nested_in_a_target_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            resolve_virtual_directories([VIRTUAL1], [VIRTUAL1 / '2024'])
        self.assertIn('must not contain each other', str(caught.exception))


class TestVirtualDirectoryCache(unittest.TestCase):
    """Collecting and reusing what the virtual directories hold"""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp())
        self.cache_file = self.work_dir / 'cache.json'

    def tearDown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def build_cache(self, roots, reuse=False, force_update=False) -> VirtualDirectoryCache:
        targets, sources = resolve_virtual_directories(roots, [])
        cache = VirtualDirectoryCache(self.cache_file, QuietPrinter())
        cache.set_directories(targets, sources)
        cache.prepare(reuse=reuse, force_update=force_update)

        return cache

    def test_system_files_and_directories_are_left_out(self):
        cache = self.build_cache([VIRTUAL1])
        cached = cache.cached_roots[str(VIRTUAL1.resolve())].files

        self.assertEqual(len(cached), 10)
        self.assertNotIn('2024/photos/desktop.ini', cached)
        self.assertNotIn('__MACOSX/ghost_photo.jpg', cached)
        self.assertIn('2024/photos/unique_virtual_photo.jpg', cached)

    def test_hidden_files_are_kept_in_the_cache(self):
        cache = self.build_cache([VIRTUAL1])
        cached = cache.cached_roots[str(VIRTUAL1.resolve())].files

        self.assertIn('2023/.hidden/PXL_20240101_120000000.jpg', cached)

    def test_paths_are_relative_to_the_share_and_slash_separated(self):
        cache = self.build_cache([VIRTUAL1])
        cached = cache.cached_roots[str(VIRTUAL1.resolve())].files
        self.assertEqual(set(cached) & VIRTUAL1_CONFLICTS, VIRTUAL1_CONFLICTS)

    def test_the_same_path_on_two_shares_is_kept_apart(self):
        cache = self.build_cache([VIRTUAL1, VIRTUAL2])
        matches = cache.find_matches('2024/photos/PXL_20240815_120000000.jpg')

        self.assertEqual([root for root, _ in matches], [VIRTUAL1.resolve(), VIRTUAL2.resolve()])
        self.assertNotEqual(matches[0][1].size, matches[1][1].size)

    def test_a_share_that_is_not_part_of_the_run_is_dropped(self):
        self.build_cache([VIRTUAL1, VIRTUAL2])
        cache = self.build_cache([VIRTUAL2])

        self.assertEqual(list(cache.cached_roots), [str(VIRTUAL2.resolve())])
        self.assertEqual(cache.find_matches('2024/photos/IMG_20240815_160000.jpg'), [])

    def test_keeping_the_cache_retains_other_shares_but_ignores_them(self):
        self.build_cache([VIRTUAL1], reuse=True)
        cache = self.build_cache([VIRTUAL2], reuse=True)

        self.assertEqual(set(cache.cached_roots), {str(VIRTUAL1.resolve()), str(VIRTUAL2.resolve())})
        self.assertEqual(cache.find_matches('2024/photos/IMG_20240815_160000.jpg'), [])

    def test_a_kept_cache_is_not_read_from_disk_again(self):
        first = self.build_cache([VIRTUAL1], reuse=True)
        scanned_at = first.cached_roots[str(VIRTUAL1.resolve())].scanned_at

        second = self.build_cache([VIRTUAL1], reuse=True)
        self.assertEqual(second.cached_roots[str(VIRTUAL1.resolve())].scanned_at, scanned_at)

    def test_a_rebuilt_cache_reads_the_share_again(self):
        first = self.build_cache([VIRTUAL1])
        scanned_at = first.cached_roots[str(VIRTUAL1.resolve())].scanned_at

        second = self.build_cache([VIRTUAL1])
        self.assertGreater(second.cached_roots[str(VIRTUAL1.resolve())].scanned_at, scanned_at)

    def test_forcing_an_update_reads_again_and_still_keeps_the_others(self):
        first = self.build_cache([VIRTUAL1, VIRTUAL2], reuse=True)
        scanned_at = first.cached_roots[str(VIRTUAL2.resolve())].scanned_at

        second = self.build_cache([VIRTUAL2], reuse=True, force_update=True)

        self.assertEqual(set(second.cached_roots), {str(VIRTUAL1.resolve()), str(VIRTUAL2.resolve())})
        self.assertGreater(second.cached_roots[str(VIRTUAL2.resolve())].scanned_at, scanned_at)

    def test_the_cache_survives_a_round_trip_through_the_file(self):
        self.build_cache([VIRTUAL1])
        reloaded = VirtualDirectoryCache(self.cache_file, QuietPrinter())
        reloaded.set_directories([VIRTUAL1.resolve()], [])

        self.assertEqual(len(reloaded.cached_roots[str(VIRTUAL1.resolve())].files), 10)
        self.assertEqual(len(reloaded.find_matches('2024/photos/IMG_20240815_160000.jpg')), 1)

    def test_a_damaged_cache_file_is_replaced(self):
        self.cache_file.write_text('this is not json')
        cache = self.build_cache([VIRTUAL1])
        self.assertEqual(len(cache.cached_roots[str(VIRTUAL1.resolve())].files), 10)

    def test_a_cache_of_an_older_layout_is_replaced(self):
        self.cache_file.write_text(json.dumps({'dirs_hash': 'x', 'files': [], 'cached_dirs': []}))
        cache = self.build_cache([VIRTUAL1])
        self.assertEqual(len(cache.cached_roots[str(VIRTUAL1.resolve())].files), 10)


class TestPlanning(unittest.TestCase):
    """Where the files would go, without touching anything"""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp())
        self.output = self.work_dir / 'output'

    def tearDown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def plan(self, sources, **overrides):
        options = make_options(self.work_dir, **overrides)
        return plan_run(options, sources, self.output)

    def test_every_dated_fixture_lands_in_a_fixed_folder(self):
        _, operations, _ = self.plan([SOURCE1, SOURCE2])
        planned = {source: target for source, target in operations}

        for source_root, expected in EXPECTED_TARGETS.items():
            for relative_source, relative_target in expected.items():
                source = source_root / relative_source
                self.assertIn(source, planned, source)
                self.assertEqual(planned[source], self.output / relative_target)

    def test_all_fixtures_are_planned(self):
        _, operations, _ = self.plan([SOURCE1, SOURCE2])
        dated = sum(len(paths) for paths in EXPECTED_TARGETS.values())

        self.assertEqual(len(operations), dated + len(CLOCK_DEPENDENT))

    def test_a_later_cutoff_moves_the_files_of_that_day(self):
        _, operations, _ = self.plan([SOURCE1], year_cutoff=YearCutoff.from_iso('2025-01-01T12:30:00Z'))
        planned = {source.name: target.relative_to(self.output).as_posix() for source, target in operations}

        self.assertEqual(planned['PXL_20250101_120000000.NIGHT.jpg'], '2024/photos/PXL_20250101_120000000.NIGHT.jpg')
        self.assertEqual(planned['PXL_20250101_130000000.jpg'], '2025/photos/PXL_20250101_130000000.jpg')

    def test_media_only_leaves_out_other_files(self):
        _, operations, _ = self.plan([SOURCE1, SOURCE2], media_only=True)
        names = {source.name for source, _ in operations}

        self.assertNotIn('document.pdf', names)
        self.assertNotIn('some_notes.txt', names)

    def test_excluding_hidden_files_leaves_out_the_hidden_directory(self):
        _, operations, _ = self.plan([SOURCE1], exclude_hidden=True)
        for source, _ in operations:
            self.assertNotIn('.hidden', source.parts, source)

    def test_system_files_are_never_planned(self):
        _, operations, _ = self.plan([SOURCE1, SOURCE2])
        for source, _ in operations:
            self.assertNotIn(source.name, {'.DS_Store', 'Thumbs.db', 'desktop.ini'})


class TestConflictDetection(unittest.TestCase):
    """Which planned copies are blocked, by the output directory and by the shares"""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp())
        self.output = self.work_dir / 'output'

    def tearDown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def plan(self, sources, **overrides):
        options = make_options(self.work_dir, **overrides)
        return plan_run(options, sources, self.output)

    def test_without_shares_nothing_is_blocked(self):
        _, _, conflicts = self.plan([SOURCE1, SOURCE2])
        self.assertEqual(conflicts, [])

    def test_one_share_blocks_the_files_it_holds(self):
        _, _, conflicts = self.plan([SOURCE1], virtual_targets=[VIRTUAL1])
        blocked = {conflict.target.path.relative_to(VIRTUAL1).as_posix() for conflict in conflicts}

        self.assertEqual(blocked, VIRTUAL1_CONFLICTS)
        self.assertTrue(all(conflict.is_virtual for conflict in conflicts))

    def test_two_shares_are_reported_separately(self):
        _, _, conflicts = self.plan([SOURCE1, SOURCE2], virtual_targets=[VIRTUAL1, VIRTUAL2])
        blocked_files = {conflict.operation_target for conflict in conflicts}

        self.assertEqual(len(conflicts), 11)
        self.assertEqual(len(blocked_files), 10)

    def test_the_output_directory_is_asked_about_before_the_shares(self):
        target = self.output / '2024/photos/PXL_20240815_120000000.jpg'
        target.parent.mkdir(parents=True)
        target.write_text('already there')

        _, _, conflicts = self.plan([SOURCE1], virtual_targets=[VIRTUAL1, VIRTUAL2])
        same_file = [conflict for conflict in conflicts if conflict.operation_target == target]

        self.assertEqual([conflict.is_virtual for conflict in same_file], [False, True, True])
        self.assertEqual(same_file[1].target.path.parent.parent.parent, VIRTUAL1.resolve())
        self.assertEqual(same_file[2].target.path.parent.parent.parent, VIRTUAL2.resolve())

    def test_filters_remove_the_conflicts_of_the_files_they_drop(self):
        _, _, conflicts = self.plan([SOURCE1, SOURCE2], virtual_targets=[VIRTUAL1],
                                    media_only=True, exclude_hidden=True)
        self.assertEqual(len(conflicts), len(VIRTUAL1_CONFLICTS) - 1)

    def test_a_share_holds_files_that_block_nothing(self):
        _, _, conflicts = self.plan([SOURCE1], virtual_targets=[VIRTUAL1])
        blocked = {conflict.target.path.name for conflict in conflicts}

        self.assertNotIn('unique_virtual_photo.jpg', blocked)
        self.assertNotIn('unique_virtual_video.mp4', blocked)

    def test_a_virtual_source_is_sorted_before_it_blocks(self):
        _, _, conflicts = self.plan([SOURCE1], virtual_sources=[VIRTUAL_SOURCE1])
        blocked = {conflict.operation_target.relative_to(self.output).as_posix() for conflict in conflicts}

        self.assertEqual(blocked, VIRTUAL_SOURCE1_CONFLICTS)
        self.assertTrue(all(conflict.is_virtual for conflict in conflicts))
        self.assertTrue(all(VIRTUAL_SOURCE1 in conflict.target.path.parents for conflict in conflicts))

    def test_a_virtual_source_keeps_sub_structure(self):
        # The mp4 below virtual_source1/random/ shares source1's date but not its
        # videos/ structure, so once sorted it lands elsewhere and blocks nothing
        _, _, conflicts = self.plan([SOURCE1], virtual_sources=[VIRTUAL_SOURCE1])
        self.assertNotIn('PXL_20240720_183045123.TS.mp4', {conflict.target.path.name for conflict in conflicts})

    def test_the_same_directory_blocks_more_as_a_source_than_as_a_target(self):
        _, _, as_target = self.plan([SOURCE1], virtual_targets=[VIRTUAL_SOURCE1])
        _, _, as_source = self.plan([SOURCE1], virtual_sources=[VIRTUAL_SOURCE1])

        self.assertEqual(as_target, [])
        self.assertEqual(len(as_source), len(VIRTUAL_SOURCE1_CONFLICTS))


@unittest.skipIf(os.name == 'nt', "directory symlinks need privileges on Windows")
class TestSymlinkFollowing(unittest.TestCase):
    """Whether directory symlinks are descended into, on the source scan and the virtual scan"""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp())
        self.output = self.work_dir / 'output'

    def tearDown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def source_with_linked_directory(self) -> Path:
        """A source whose linked/ points outside to a directory holding one dated file"""
        source = self.work_dir / 'source'
        (source / 'photos').mkdir(parents=True)
        (source / 'photos' / 'PXL_20240701_120000000.jpg').write_text('here')

        external = self.work_dir / 'external'
        external.mkdir()
        (external / 'PXL_20220701_120000000.jpg').write_text('there')
        (source / 'linked').symlink_to(external, target_is_directory=True)

        return source

    def plan(self, sources, **overrides):
        options = make_options(self.work_dir, **overrides)
        return plan_run(options, sources, self.output)

    def planned_targets(self, sources, **overrides) -> set:
        _, operations, _ = self.plan(sources, **overrides)
        return {target.relative_to(self.output).as_posix() for _, target in operations}

    def test_a_source_symlink_is_followed_by_default(self):
        targets = self.planned_targets([self.source_with_linked_directory()])

        self.assertIn('2022/linked/PXL_20220701_120000000.jpg', targets)
        self.assertIn('2024/photos/PXL_20240701_120000000.jpg', targets)

    def test_a_source_symlink_can_be_left_out(self):
        targets = self.planned_targets([self.source_with_linked_directory()], follow_source_symlinks=False)

        self.assertNotIn('2022/linked/PXL_20220701_120000000.jpg', targets)
        self.assertIn('2024/photos/PXL_20240701_120000000.jpg', targets)

    def test_a_source_symlink_loop_terminates_and_reads_each_file_once(self):
        source = self.work_dir / 'looping'
        (source / 'photos').mkdir(parents=True)
        (source / 'photos' / 'PXL_20240701_120000000.jpg').write_text('here')
        (source / 'photos' / 'back').symlink_to(source, target_is_directory=True)

        _, operations, _ = self.plan([source])
        names = [target.name for _, target in operations]

        self.assertEqual(names.count('PXL_20240701_120000000.jpg'), 1)

    def virtual_with_linked_year(self) -> Path:
        """A virtual target whose 2022/ is a symlink to an outside directory with one file"""
        root = self.work_dir / 'virtual'
        (root / '2024' / 'photos').mkdir(parents=True)
        (root / '2024' / 'photos' / 'PXL_20240701_120000000.jpg').write_text('here')

        external = self.work_dir / 'ext'
        external.mkdir()
        (external / 'PXL_20220701_120000000.jpg').write_text('there')
        (root / '2022').symlink_to(external, target_is_directory=True)

        return root

    def scan_virtual(self, root: Path, follow: bool) -> set:
        cache = VirtualDirectoryCache(self.work_dir / f'cache-{follow}.json', QuietPrinter(), follow)
        cache.set_directories([root], [])
        cache.prepare()

        return set(cache.cached_roots[str(root.resolve())].files)

    def test_a_virtual_symlink_is_followed_by_default(self):
        cached = self.scan_virtual(self.virtual_with_linked_year(), follow=True)
        self.assertIn('2022/PXL_20220701_120000000.jpg', cached)

    def test_a_virtual_symlink_can_be_left_out(self):
        cached = self.scan_virtual(self.virtual_with_linked_year(), follow=False)
        self.assertNotIn('2022/PXL_20220701_120000000.jpg', cached)


class TestConflictNumbering(unittest.TestCase):
    """How the conflicts are labelled for the prompt"""

    def setUp(self):
        self.conflicts = [
            make_conflict('a.jpg'),
            make_conflict('b.jpg'), make_conflict('b.jpg', is_virtual=True), make_conflict('b.jpg', is_virtual=True),
            make_conflict('c.jpg'),
        ]
        self.labels = number_conflicts(group_conflicts(self.conflicts))

    def test_a_file_blocked_in_one_place_gets_a_plain_number(self):
        self.assertEqual(self.labels[id(self.conflicts[0])], '1 of 3')
        self.assertEqual(self.labels[id(self.conflicts[4])], '3 of 3')

    def test_a_file_blocked_in_several_places_gets_sub_numbers(self):
        self.assertEqual(self.labels[id(self.conflicts[1])], '2.1 of 3')
        self.assertEqual(self.labels[id(self.conflicts[2])], '2.2 of 3')
        self.assertEqual(self.labels[id(self.conflicts[3])], '2.3 of 3')

    def test_the_total_counts_files_that_need_a_decision(self):
        self.assertEqual(len(group_conflicts(self.conflicts)), 3)
        self.assertTrue(all(label.endswith('of 3') for label in self.labels.values()))

    def test_the_conflicts_of_one_file_stay_together_and_in_order(self):
        groups = list(group_conflicts(self.conflicts).values())
        self.assertEqual([len(group) for group in groups], [1, 3, 1])
        self.assertEqual([conflict.is_virtual for conflict in groups[1]], [False, True, True])


class TestConflictAnswers(unittest.TestCase):
    """How an answer to one conflict reaches the others"""

    def setUp(self):
        self.printer = RecordingPrinter()
        self.resolver = ConflictResolver(self.printer)

    def resolve(self, conflicts, answers: Answers) -> bool:
        """
        Args:
            conflicts: Conflicts of one planned copy
            answers: Answers to give to the prompts

        Returns:
            Whether the copy may go ahead
        """
        labels = number_conflicts(group_conflicts(conflicts))

        with mock.patch('builtins.input', answers), contextlib.redirect_stdout(io.StringIO()):
            return self.resolver.resolve(conflicts, labels)

    def test_actions_compare_the_two_files(self):
        bigger_and_newer = make_conflict('a.jpg', source_size=20, target_size=10)

        self.assertTrue(OverwriteAction.YES.allows(bigger_and_newer))
        self.assertFalse(OverwriteAction.NO.allows(bigger_and_newer))
        self.assertTrue(OverwriteAction.LARGER.allows(bigger_and_newer))
        self.assertTrue(OverwriteAction.NEWER.allows(bigger_and_newer))
        self.assertFalse(OverwriteAction.OLDER.allows(bigger_and_newer))

    def test_answers_are_read_from_shorthand_and_full_name(self):
        self.assertIs(OverwriteAction.from_input('y'), OverwriteAction.YES)
        self.assertIs(OverwriteAction.from_input('LARGER'), OverwriteAction.LARGER)
        self.assertIs(OverwriteAction.from_input(' new '), OverwriteAction.NEWER)
        self.assertIsNone(OverwriteAction.from_input('maybe'))

        self.assertIs(OverwriteScope.from_input(''), OverwriteScope.CURRENT)
        self.assertIs(OverwriteScope.from_input('a'), OverwriteScope.ALL)
        self.assertIs(OverwriteScope.from_input('file'), OverwriteScope.FILE)
        self.assertIs(OverwriteScope.from_input('v'), OverwriteScope.VIRTUAL)
        self.assertIsNone(OverwriteScope.from_input('everything'))

    def test_only_the_wide_scopes_reach_later_conflicts(self):
        physical, virtual = make_conflict('a.jpg'), make_conflict('a.jpg', is_virtual=True)

        self.assertTrue(OverwriteScope.ALL.covers(physical))
        self.assertTrue(OverwriteScope.ALL.covers(virtual))
        self.assertFalse(OverwriteScope.VIRTUAL.covers(physical))
        self.assertTrue(OverwriteScope.VIRTUAL.covers(virtual))
        self.assertFalse(OverwriteScope.FILE.covers(virtual))
        self.assertFalse(OverwriteScope.CURRENT.covers(virtual))

    def test_declining_the_first_conflict_ends_the_questions_for_that_file(self):
        conflicts = [make_conflict('a.jpg'), make_conflict('a.jpg', is_virtual=True)]
        answers = Answers('n')

        self.assertFalse(self.resolve(conflicts, answers))
        self.assertEqual(len(answers.questions), 1)

    def test_the_file_scope_settles_the_remaining_conflicts_of_that_file(self):
        conflicts = [make_conflict('a.jpg', 5, 1), make_conflict('a.jpg', 5, 1, is_virtual=True)]
        answers = Answers('larger:file')

        self.assertTrue(self.resolve(conflicts, answers))
        self.assertEqual(len(answers.questions), 1)

    def test_the_file_scope_can_still_stop_the_copy(self):
        conflicts = [make_conflict('a.jpg', 5, 1), make_conflict('a.jpg', 5, 9, is_virtual=True)]
        answers = Answers('larger:file')

        self.assertFalse(self.resolve(conflicts, answers))
        self.assertEqual(len(answers.questions), 1)

    def test_every_conflict_is_asked_about_when_each_answer_agrees(self):
        conflicts = [make_conflict('a.jpg'), make_conflict('a.jpg', is_virtual=True)]
        answers = Answers('y', 'y')

        self.assertTrue(self.resolve(conflicts, answers))
        self.assertEqual(len(answers.questions), 2)

    def test_the_run_scope_answers_the_conflicts_of_later_files_too(self):
        first = [make_conflict('a.jpg')]
        second = [make_conflict('b.jpg'), make_conflict('b.jpg', is_virtual=True)]
        answers = Answers('y:all')

        self.assertTrue(self.resolve(first, answers))
        self.assertTrue(self.resolve(second, answers))
        self.assertEqual(len(answers.questions), 1)

    def test_the_virtual_scope_leaves_the_output_directory_to_the_user(self):
        blocked_twice = [make_conflict('a.jpg'), make_conflict('a.jpg', is_virtual=True)]
        blocked_locally = [make_conflict('b.jpg')]
        answers = Answers('y', 'n:virtual', 'y')

        self.assertFalse(self.resolve(blocked_twice, answers))     # refused on the virtual target
        self.assertTrue(self.resolve(blocked_locally, answers))    # still asked, and accepted
        self.assertEqual(len(answers.questions), 3)

    def test_the_virtual_scope_settles_later_virtual_conflicts(self):
        first = [make_conflict('a.jpg', is_virtual=True)]
        second = [make_conflict('b.jpg', is_virtual=True)]
        answers = Answers('n:v')

        self.assertFalse(self.resolve(first, answers))
        self.assertFalse(self.resolve(second, answers))
        self.assertEqual(len(answers.questions), 1)

    def test_conflicts_that_are_not_asked_about_stay_quiet(self):
        conflicts = [make_conflict('a.jpg'), make_conflict('a.jpg', is_virtual=True)]
        self.resolve(conflicts, Answers('n'))

        self.assertEqual(self.printer.messages, [])

    def test_verbose_reports_the_conflicts_that_are_not_asked_about(self):
        self.resolver = ConflictResolver(self.printer, verbose=True)
        conflicts = [make_conflict('a.jpg'), make_conflict('a.jpg', is_virtual=True),
                     make_conflict('a.jpg', is_virtual=True)]
        self.resolve(conflicts, Answers('n'))

        reported = [message for message in self.printer.messages if 'not asked' in message]
        self.assertEqual(len(reported), 2)
        self.assertIn('Conflict 1.2 of 1', reported[0])
        self.assertIn('Conflict 1.3 of 1', reported[1])

    def test_verbose_reports_where_a_reused_answer_came_from(self):
        self.resolver = ConflictResolver(self.printer, verbose=True)
        self.resolve([make_conflict('a.jpg', is_virtual=True)], Answers('n:virtual'))
        self.resolve([make_conflict('b.jpg', is_virtual=True)], Answers())

        reported = [message for message in self.printer.messages if 'not asked' in message]
        self.assertEqual(len(reported), 1)
        self.assertIn("'virtual' answer", reported[0])


class TestCompleteRuns(unittest.TestCase):
    """Whole runs, with and without shares"""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp())
        self.output = self.work_dir / 'output'
        self.status_file = self.work_dir / 'status.json'

    def tearDown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def written_files(self):
        return {path.relative_to(self.output).as_posix() for path in self.output.rglob('*') if path.is_file()}

    def test_a_run_without_shares_writes_every_file(self):
        options = make_options(self.work_dir)
        sorter, _ = execute_run(options, [SOURCE1, SOURCE2], self.output)

        self.assertEqual(sorter.processed_files, 30)
        self.assertEqual(sorter.skipped_files, 0)
        self.assertEqual(len(self.written_files()), 30)

        for expected in EXPECTED_TARGETS.values():
            for relative_target in expected.values():
                self.assertIn(relative_target, self.written_files())

    def test_declining_the_share_conflicts_leaves_those_files_out(self):
        options = make_options(self.work_dir, virtual_targets=[VIRTUAL1.resolve()])
        sorter, _ = execute_run(options, [SOURCE1], self.output)

        self.assertEqual(sorter.skipped_files, len(VIRTUAL1_CONFLICTS))
        self.assertEqual(sorter.processed_files, 24 - len(VIRTUAL1_CONFLICTS))
        for blocked in VIRTUAL1_CONFLICTS:
            self.assertNotIn(blocked, self.written_files())

    def test_accepting_the_share_conflicts_writes_them_anyway(self):
        options = make_options(self.work_dir, virtual_targets=[VIRTUAL1.resolve()])
        sorter, _ = execute_run(options, [SOURCE1], self.output, Answers(default='y'))

        self.assertEqual(sorter.processed_files, 24)
        for blocked in VIRTUAL1_CONFLICTS:
            self.assertIn(blocked, self.written_files())

    def test_a_share_is_never_written_to(self):
        before = {path: path.stat().st_size for path in VIRTUAL1.rglob('*') if path.is_file()}
        options = make_options(self.work_dir, virtual_targets=[VIRTUAL1.resolve()])
        execute_run(options, [SOURCE1], self.output, Answers(default='y'))
        after = {path: path.stat().st_size for path in VIRTUAL1.rglob('*') if path.is_file()}

        self.assertEqual(before, after)

    def test_moving_empties_the_source(self):
        source = self.work_dir / 'movable'
        shutil.copytree(SOURCE1, source)
        options = make_options(self.work_dir, move_files=True)
        sorter, _ = execute_run(options, [source], self.output)

        self.assertEqual(sorter.processed_files, 24)
        self.assertEqual([path for path in source.rglob('*') if path.is_file()], [])

    def test_a_dry_run_writes_nothing_and_does_not_block_the_real_run(self):
        preview_options = make_options(self.work_dir, dry_run=True)
        preview, _ = execute_run(preview_options, [SOURCE1], self.output)

        self.assertEqual(preview.processed_files, 24)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.status_file.exists())

        real, _ = execute_run(make_options(self.work_dir), [SOURCE1], self.output)
        self.assertEqual(real.processed_files, 24)
        self.assertEqual(len(self.written_files()), 24)

    def test_a_dry_run_can_prepare_the_cache_for_a_later_run(self):
        cache_file = self.work_dir / 'cache.json'
        preview_options = make_options(self.work_dir, dry_run=True, virtual_targets=[VIRTUAL1.resolve()])
        execute_run(preview_options, [SOURCE1], self.output)

        self.assertTrue(cache_file.exists())
        scanned_at = json.loads(cache_file.read_text())['roots'][0]['scanned_at']

        later_options = make_options(self.work_dir, virtual_targets=[VIRTUAL1.resolve()], keep_virtual_cache=True)
        sorter, _ = execute_run(later_options, [SOURCE1], self.output)

        self.assertEqual(json.loads(cache_file.read_text())['roots'][0]['scanned_at'], scanned_at)
        self.assertEqual(sorter.skipped_files, len(VIRTUAL1_CONFLICTS))

    def test_resuming_skips_what_is_already_done(self):
        execute_run(make_options(self.work_dir), [SOURCE1], self.output, Answers(default='n'))
        self.assertTrue(self.status_file.exists())

        resumed, printed = execute_run(make_options(self.work_dir, resume=True), [SOURCE1], self.output)
        self.assertEqual(resumed.processed_files, 0)
        self.assertIn('No files to process', printed)

    def test_declining_the_resume_question_starts_over(self):
        execute_run(make_options(self.work_dir), [SOURCE1], self.output, Answers(default='n'))

        again, printed = execute_run(make_options(self.work_dir), [SOURCE1], self.output, Answers(default='n'))
        self.assertIn('starting from scratch', printed)
        self.assertEqual(again.skipped_files, 24)          # every file now conflicts with the output directory

    def test_a_run_without_answers_declines_instead_of_failing(self):
        def no_input(prompt: str = ''):
            raise EOFError

        options = make_options(self.work_dir, virtual_targets=[VIRTUAL1.resolve()])
        written = io.StringIO()

        with mock.patch('builtins.input', no_input), contextlib.redirect_stdout(written):
            sorter = MediaSorter(options, ColorPrinter())
            sorter.process_files([SOURCE1], self.output)

        self.assertEqual(sorter.skipped_files, len(VIRTUAL1_CONFLICTS))
        self.assertEqual(sorter.processed_files, 24 - len(VIRTUAL1_CONFLICTS))
        self.assertTrue(self.status_file.exists())

    def test_the_status_file_can_be_deleted_at_the_end(self):
        execute_run(make_options(self.work_dir), [SOURCE1], self.output, Answers(default='y'))
        self.assertFalse(self.status_file.exists())

    @unittest.skipIf(os.name == 'nt' or os.geteuid() == 0, "needs a directory the user cannot write to")
    def test_a_failing_copy_is_reported_and_the_run_goes_on(self):
        self.output.mkdir(parents=True)
        self.output.chmod(0o500)
        try:
            with mock.patch('media_sort.time.sleep'):        # do not wait out the retries
                sorter, printed = execute_run(make_options(self.work_dir), [SOURCE1], self.output)
        finally:
            self.output.chmod(0o700)

        self.assertEqual(sorter.processed_files, 0)
        self.assertEqual(len(sorter.status_tracker.failed), 24)
        self.assertIn('Failed operations: 24', printed)
        self.assertEqual(len(json.loads(self.status_file.read_text())['failed']), 24)


class TestCommandLine(unittest.TestCase):
    """The entry point, including the run that only fills the cache"""

    def setUp(self):
        self.work_dir = Path(tempfile.mkdtemp())
        self.cache_file = self.work_dir / 'cache.json'
        self.status_file = self.work_dir / 'status.json'

    def tearDown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def run_main(self, *arguments, answers: Answers = None):
        """
        Args:
            arguments: Command line arguments, without the program name
            answers: Answers for the prompts, all declined when left out

        Returns:
            Everything the program printed
        """
        written = io.StringIO()

        with mock.patch('sys.argv', ['media_sort.py', *arguments]), \
                mock.patch('builtins.input', answers or Answers()), \
                contextlib.redirect_stdout(written):
            main()

        return written.getvalue()

    def test_building_the_cache_needs_no_source_and_writes_nothing_else(self):
        self.run_main('--build-virtual-cache', '--vt', str(VIRTUAL1), '--virtual-cache-file', str(self.cache_file),
                      '--status-file', str(self.status_file))

        cached = json.loads(self.cache_file.read_text())['roots']
        self.assertEqual([root['path'] for root in cached], [str(VIRTUAL1.resolve())])
        self.assertEqual(len(cached[0]['files']), 10)
        self.assertFalse(self.status_file.exists())
        self.assertEqual([path for path in self.work_dir.iterdir()], [self.cache_file])

    def test_a_later_run_can_use_the_cache_that_was_built(self):
        self.run_main('--build-virtual-cache', '--vt', str(VIRTUAL1), '--virtual-cache-file', str(self.cache_file))
        scanned_at = json.loads(self.cache_file.read_text())['roots'][0]['scanned_at']

        options = make_options(self.work_dir, virtual_targets=[VIRTUAL1.resolve()], keep_virtual_cache=True)
        sorter, _ = execute_run(options, [SOURCE1], self.work_dir / 'output')

        self.assertEqual(json.loads(self.cache_file.read_text())['roots'][0]['scanned_at'], scanned_at)
        self.assertEqual(sorter.skipped_files, len(VIRTUAL1_CONFLICTS))

    def test_building_the_cache_accepts_a_virtual_source(self):
        self.run_main('--build-virtual-cache', '--vs', str(VIRTUAL_SOURCE1),
                      '--virtual-cache-file', str(self.cache_file))

        cached = json.loads(self.cache_file.read_text())['roots']
        self.assertEqual([root['path'] for root in cached], [str(VIRTUAL_SOURCE1.resolve())])
        self.assertEqual(len(cached[0]['files']), 4)

    def test_cache_and_status_files_create_their_parent_directories(self):
        cache_file = self.work_dir / 'nested' / 'cache.json'
        status_file = self.work_dir / 'other' / 'deep' / 'status.json'

        self.run_main(str(SOURCE1), '-o', str(self.work_dir / 'output'), '--vt', str(VIRTUAL1),
                      '--virtual-cache-file', str(cache_file), '--status-file', str(status_file))

        self.assertTrue(cache_file.exists())
        self.assertTrue(status_file.exists())

    def test_a_source_is_required_for_an_ordinary_run(self):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            self.run_main('--vt', str(VIRTUAL1))
        self.assertEqual(caught.exception.code, 2)

    def test_building_the_cache_requires_a_virtual_target(self):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            self.run_main('--build-virtual-cache')
        self.assertEqual(caught.exception.code, 2)

    def test_a_virtual_target_before_the_sources_is_not_mistaken_for_one(self):
        printed = self.run_main('--vt', str(VIRTUAL1), str(SOURCE1), '-o', str(self.work_dir / 'output'),
                                '--dry-run', '--virtual-cache-file', str(self.cache_file),
                                '--status-file', str(self.status_file))

        self.assertIn(f"Source directories: {get_relative_path(SOURCE1)}", printed)
        self.assertIn('Total files processed: 16', printed)

    def test_an_unusable_cutoff_stops_the_run(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main(str(SOURCE1), '--new-year-cutoff', 'yesterday')
        self.assertEqual(caught.exception.code, 1)

    def test_an_unmounted_virtual_target_stops_the_run(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main(str(SOURCE1), '--vt', '/mnt/definitely_not_mounted')
        self.assertEqual(caught.exception.code, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
