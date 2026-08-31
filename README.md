# Media Sorter
A Python command-line tool that automatically sorts photos, videos, and GIFs into year-based folders by extracting dates from filenames or using file creation dates.

## Features
* **Smart Date Detection**: Extracts dates from various filename patterns (PXL, IMG, Screenshot, WhatsApp, etc.)
    * **Fallback to Creation Date**: Uses file creation date when no date pattern is found in the filename
    * **New Year Cutoff**: Keeps pictures taken after midnight with the year that was celebrated, configurable
* **Directory Structure Preservation**: Maintains the original folder hierarchy within year folders
* **Conflict Resolution**: Interactive handling of duplicate files with multiple options
* **Virtual Directories**: Detects files that already exist in another directory, sorted (`--virtual-target`) or not (`--virtual-source`), which may be a slow network share, from a metadata cache and without ever writing to it
* **Resumable Operations**: Can be interrupted and resumed from where it left off
* **Cross-Platform**: Works on Windows, macOS, and Linux
* **Colored Output**: Clear, colored terminal output (when supported)
* **Flexible Options**: Copy or move files, process media-only, include/exclude hidden files, follow or skip directory symlinks
* **Automated Tests**: A test suite checks the behaviour against the sample files in `test/`


## Installation
* No installation required - it's a single Python file 🎉
* Requires Python 3.7+


## Usage
### Command Help
```bash
usage: media_sort.py [-h] [-o OUTPUT] [--dry-run] [--move] [--media-only] [--exclude-hidden]
                     [--no-follow-source-symlinks] [--resume] [--verbose] [--new-year-cutoff ISO_TIMESTAMP]
                     [--virtual-target DIR] [--virtual-source DIR] [--no-follow-virtual-symlinks]
                     [--keep-virtual-cache] [--update-virtual-cache] [--build-virtual-cache]
                     [--status-file STATUS_FILE] [--virtual-cache-file VIRTUAL_CACHE_FILE]
                     [sources ...]

Sort media files into year-based folders

positional arguments:
  sources               Source directories to sort, at least one unless --build-virtual-cache is given

options:
  -h, --help            show this help message and exit
  -o, --output OUTPUT   Output directory (default: same level as first source)
  --dry-run             Show what would be done without actually doing it
  --move                Move files instead of copying them
  --media-only          Only process media files (photos, videos, gifs)
  --exclude-hidden      Exclude hidden files and directories (included by default)
  --no-follow-source-symlinks
                        Do not descend into directory symlinks while scanning the sources (they are
                        followed by default; a symlink to a file is always included)
  --resume              Resume from previous interrupted operation without asking
  --verbose, -v         Enable verbose output (show all file operations)
  --new-year-cutoff ISO_TIMESTAMP
                        Files dated on this day but before this time belong to the previous year, as ISO
                        8601 timestamp such as 2025-01-01T14:00:00Z; the year is ignored, the time is local
                        (default: 01-01T14:00:00)
  --virtual-target, --vt DIR
                        Directory already in the sorted year layout, often on a slow network share. It is
                        only read, never written to, and checked for conflicts from a metadata cache. Repeat
                        for several directories
  --virtual-source, --vs DIR
                        Like --virtual-target, but for an unsorted directory: its files are placed in their
                        year folders virtually before the check, so an archive that is not sorted yet can be
                        used too. Repeat for several directories
  --no-follow-virtual-symlinks
                        Do not descend into directory symlinks while reading the virtual directories
                        (they are followed by default; a symlink to a file is always included)
  --keep-virtual-cache  Take the cached state of the given virtual directories instead of reading them
                        again, and keep the cached entries of directories that are not part of this run
                        (default: read them again and drop the entries of the others)
  --update-virtual-cache
                        Read the given virtual directories again even with --keep-virtual-cache, so only the
                        entries of the directories that are not part of this run are kept. Without --keep-
                        virtual-cache this is what happens anyway
  --build-virtual-cache
                        Only read the given virtual directories into the cache and exit, without sorting
                        anything. Needs no source and no output directory
  --status-file STATUS_FILE
                        Status file for resume capability (default: .media_sort_status.json)
  --virtual-cache-file VIRTUAL_CACHE_FILE
                        Metadata cache of the virtual directories (default: .media_sort_virtual_cache.json)

Examples:
  media_sort.py /path/to/source
  media_sort.py /path/to/source1 /path/to/source2 -o /path/to/output
  media_sort.py /path/to/source --dry-run
  media_sort.py /path/to/source --move --media-only
  media_sort.py /path/to/source --exclude-hidden                        # Exclude hidden files/directories
  media_sort.py /path/to/source --resume                                # Resume interrupted operation
  media_sort.py /path/to/source --verbose                               # Show all file operations
  media_sort.py /path/to/source --vt /mnt/nas1 --vt /mnt/nas2           # Check two slow directories
  media_sort.py /path/to/source --vt /mnt/nas1 --keep-virtual-cache     # Do not read them again
  media_sort.py /path/to/source --vs /mnt/dump                          # Check an unsorted archive too
  media_sort.py --build-virtual-cache --vt /mnt/nas1                    # Only fill the cache, sort nothing
  media_sort.py /path/to/source --new-year-cutoff 2025-01-01T14:00:00Z  # Move the new year boundary
```

**Note:** By default, the tool runs in quiet mode, showing only warnings, errors, and prompts. Use `--verbose`/`-v` to see all operations including successful file copies/moves, skipped files and the conflicts that an earlier answer settled without asking again.

### Basic Usage
```bash
# Sort files from a source directory (copies by default)
python media_sort.py /path/to/source

# Sort files from multiple sources
python media_sort.py /path/to/source1 /path/to/source2

# Specify output directory
python media_sort.py /path/to/source -o /path/to/output
```

### Examples
```bash
# Preview what would happen (dry run)
python media_sort.py ~/Pictures --dry-run

# Move files instead of copying
python media_sort.py ~/Pictures --move

# Process only media files, excluding hidden directories
python media_sort.py ~/Pictures --media-only --exclude-hidden

# Resume an interrupted operation
python media_sort.py ~/Pictures --resume
```


## How It Works
1. **Date Extraction**: The tool tries to extract dates from filenames using these patterns:
   * `PXL_YYYYMMDD_HHMMSS` (Google Pixel photos)
   * `Screenshot_YYYYMMDD-HHMMSS`
   * `IMG_YYYYMMDD_HHMMSS`
   * `IMG-YYYYMMDD-WA` (WhatsApp)
   * `VID_YYYYMMDD_HHMMSS`
   * `DSC_YYYYMMDD` (Digital cameras)
   * Generic patterns: `YYYY-MM-DD`, `YYYYMMDD`
2. **Fallback**: If no date pattern is found, it uses the file's creation date (with a warning)
3. **Special Rules**:
   * Files dated January 1st before 14:00 are considered part of the previous year, so pictures of a New Year's Eve party taken after midnight stay with the year that was celebrated. 14:00 itself and everything after it counts as the new year
   * Move that boundary with `--new-year-cutoff`. Only files dated on the cutoff day are affected, so the setting can never reclassify more than a single day
   * The time of day comes from the same source as the date, following the extraction rules above: from the filename when its pattern carries a time, from the file itself otherwise
   * Directory structure is preserved: `source/a/b/photo.jpg` dated 2024 goes to `output/2024/a/b/photo.jpg`
   * This layout - a year folder at the top, the source's sub-structure kept underneath - is what this tool calls **sorted**. A directory arranged like this (an earlier output) is a *sorted* directory; one still in its original layout is *unsorted*. The distinction matters for virtual directories below
4. **Conflict Resolution**: When a file already exists at the destination:
   * Interactive prompt with options: Yes/No/Larger/Older/Newer
   * Scope options: this conflict only (default), this file (`f`), the virtual directories (`v`), the whole run (`a`)
   * Format: `action:scope` or just `action` (e.g., `y:all`, `larger:f`, `n:virtual`, `n`)


## Supported File Types
### Media Files (default with `--media-only`)
* **Images**: jpg, jpeg, png, gif, bmp, tiff, tif, webp, heic, heif, raw, cr2, nef, arw, dng, svg
* **Videos**: mp4, avi, mov, wmv, flv, mkv, webm, m4v, mpg, mpeg, 3gp, 3g2, mts, m2ts, vob, ogv

### Hidden Files and Directories
* By default, hidden files and directories (starting with `.`) are included
* Use `--exclude-hidden` to skip all hidden files and directories
* Only the path below a source directory is judged, so a source that itself lies in a hidden directory is still processed
* Certain system files and directories are always skipped regardless of settings:
  * **Files**: `.DS_Store`, `Thumbs.db`, `desktop.ini`, `.gitignore`, `.gitkeep`
  * **Directories**: `.git`, `.svn`, `.hg`, `__MACOSX`, `.Trash`, `.Trashes`, `__pycache__`, `.cache`, `.tmp`, `.temp`

### Symbolic Links
* A symlink to a file is always followed and handled like an ordinary file
* A symlink to a directory is descended into by default, so a linked subtree is included. A link that leads back into a directory already visited is skipped, so a loop cannot run forever and no file is read under two paths
* Turn following off per scan: `--no-follow-source-symlinks` for the source directories, `--no-follow-virtual-symlinks` for the virtual directories. The source scan is the one that can move or copy the linked files; the virtual scan only reads them

## Test Data Structure
The `test/` directory contains sample files for testing the tool. `source1` and `source2` are the input. `virtual1` and `virtual2` play the role of two virtual targets: earlier, finished sorts, so they mirror the output tree with its year folders (`2023/`, `2024/`, ...). `virtual_source1` plays the role of a virtual source: an unsorted archive with no year folders, laid out like a source, which the tool sorts virtually before it compares.
```text
test/
├── source1/
│   ├── .hidden/
│   │   ├── .secret_photo.jpg               # Hidden file, no date pattern
│   │   └── PXL_20240101_120000000.jpg      # 12:00 on Jan 1st -> 2023 (cutoff)
│   ├── photos/
│   │   ├── PXL_20231225_090000000.MP.jpg   # -> 2023
│   │   ├── PXL_20240315_143022456.jpg      # -> 2024
│   │   ├── PXL_20240815_120000000.jpg      # -> 2024, same name as a file in source2
│   │   ├── PXL_20240815_143000 (1).jpg     # -> 2024, space and parentheses in the name
│   │   ├── PXL_20250101_120000000.NIGHT.jpg # 12:00 on Jan 1st -> 2024 (cutoff)
│   │   ├── PXL_20250101_130000000.jpg      # 13:00 on Jan 1st -> 2024 (cutoff)
│   │   ├── PXL_20250101_150000000.jpg      # 15:00 on Jan 1st -> 2025 (past the cutoff)
│   │   ├── IMG_20240815_160000.jpg         # -> 2024
│   │   ├── DSC_20240210_001.jpg            # -> 2024, date without time in the name
│   │   ├── 2024-06-15_beach.jpg            # -> 2024, generic YYYY-MM-DD
│   │   ├── 20240615-vacation.png           # -> 2024, generic YYYYMMDD
│   │   ├── 20240615_sunset.jpg             # -> 2024, generic YYYYMMDD
│   │   ├── family & friends 2024-08-15.jpg # -> 2024, special characters in the name
│   │   ├── DSCF1234.jpg                    # no pattern, uses the file creation date
│   │   ├── IMG_9876.jpg                    # no pattern, uses the file creation date
│   │   ├── random_uuid_photo.jpg           # no pattern, uses the file creation date
│   │   ├── vacation_photo.jpg              # no pattern, uses the file creation date
│   │   └── 2024/summer/
│   │       └── PXL_20240701_120000000.jpg  # -> 2024, nested source directory
│   ├── videos/
│   │   ├── PXL_20240720_183045123.TS.mp4   # -> 2024
│   │   ├── VID_20240815_143000.mp4         # -> 2024
│   │   └── family_video.mp4                # no pattern, uses the file creation date
│   └── document.pdf                        # non-media, filtered by --media-only
├── source2/
│   ├── PXL_20240815_120000000.jpg          # same name as in source1, but at the source root
│   ├── screenshots/
│   │   ├── Screenshot_20231231-235959.png  # -> 2023
│   │   └── Screenshot_20240615-143022.png  # -> 2024
│   ├── whatsapp/
│   │   ├── IMG-20240915-WA0001.jpg         # -> 2024
│   │   └── VID-20240915-WA0002.mp4         # -> 2024
│   └── some_notes.txt                      # non-media, filtered by --media-only
├── virtual1/                               # first virtual target, mirrors the output tree
│   ├── 2023/.hidden/PXL_20240101_120000000.jpg      # conflict, hidden path and cutoff
│   ├── 2023/photos/PXL_20231225_090000000.MP.jpg    # conflict in another year folder
│   ├── 2024/photos/PXL_20240815_120000000.jpg       # conflict, also present in virtual2
│   ├── 2024/photos/IMG_20240815_160000.jpg          # conflict
│   ├── 2024/photos/PXL_20250101_130000000.jpg       # conflict only because of the cutoff
│   ├── 2024/photos/2024/summer/PXL_20240701_*.jpg   # conflict below a nested source directory
│   ├── 2024/photos/unique_virtual_photo.jpg         # only here, must not conflict
│   ├── 2024/photos/desktop.ini                      # system file, must not be cached
│   ├── 2024/videos/VID_20240815_143000.mp4          # conflict
│   ├── 2025/photos/PXL_20250101_150000000.jpg       # conflict, 15:00 stays in 2025
│   ├── 2025/videos/unique_virtual_video.mp4         # only here, must not conflict
│   └── __MACOSX/ghost_photo.jpg                     # skipped directory, must not be cached
├── virtual2/                               # second virtual target
│   ├── 2024/photos/PXL_20240815_120000000.jpg       # same path as in virtual1, both are reported
│   ├── 2024/photos/PXL_20240315_143022456.jpg       # conflict only on this virtual target
│   └── 2024/screenshots/Screenshot_20240615-143022.png  # conflict with source2
└── virtual_source1/                        # virtual source, unsorted, no year folders
    ├── photos/PXL_20240815_120000000.jpg            # sorts to 2024/photos -> conflict with source1
    ├── photos/PXL_20231225_090000000.MP.jpg         # sorts to 2023/photos -> conflict with source1
    ├── random/PXL_20240720_183045123.TS.mp4         # source1 has it under videos/, so no conflict
    └── no_date_photo.jpg                            # no pattern, uses the file creation date
```

**Note on reproducibility:** every file with a date in its name lands in a fixed year folder. The files marked "uses the file creation date" depend on when the repository was checked out, so they never serve as conflict fixtures. File timestamps are not stored by git either, so the dates and sizes shown in a conflict prompt differ per checkout, while which conflicts are found does not.

### Automated Tests
`test_media_sort.py` checks all of the cases below without touching the test data. Output directories, status files and caches are written to a temporary directory and removed afterwards.
```bash
python3 test_media_sort.py            # all checks, one line per check
python3 -m unittest test_media_sort   # same checks, compact output
```

### Run by Hand
```bash
cd test

# Preview both sources: 30 files, no conflicts
python ../media_sort.py source1 source2 -o output --dry-run

# One virtual target: 8 conflicts (10 of the 12 files below virtual1 are cached, 2 are junk)
python ../media_sort.py source1 -o output --vt virtual1 --dry-run

# Two virtual targets: 11 conflicts on 10 files, PXL_20240815_120000000.jpg is on both
python ../media_sort.py source1 source2 -o output --vt virtual1 --vt virtual2 --dry-run

# One virtual source: 2 conflicts, its photos/ files sort into year folders and meet source1;
# the same directory as a --vt would find nothing, since it has no year folders
python ../media_sort.py source1 -o output --vs virtual_source1 --dry-run

# Filters cut the hidden conflict and the two non-media files: 7 conflicts
python ../media_sort.py source1 source2 -o output --vt virtual1 --media-only --exclude-hidden --dry-run

# A local file plus both virtual targets: that file is asked as 4.1, 4.2 and 4.3, output directory first
mkdir -p output/2024/photos && touch output/2024/photos/PXL_20240815_120000000.jpg
python ../media_sort.py source1 -o output --vt virtual1 --vt virtual2 --dry-run --verbose

# A virtual target that is not mounted stops the run before anything is written
python ../media_sort.py source1 -o output --vt /mnt/not_mounted --dry-run

# Fill the cache for both virtual targets and exit, without a source directory
python ../media_sort.py --build-virtual-cache --vt virtual1 --vt virtual2

# Do the actual run
python ../media_sort.py source1 source2 -o output --verbose
```

### Expected Output Structure
Sorting both test sources produces this layout. Files whose year comes from the file creation date are listed under the year of the checkout used here.
```text
output/
├── 2023/
│   ├── .hidden/PXL_20240101_120000000.jpg        # 12:00 on Jan 1st, moved by the cutoff
│   ├── photos/PXL_20231225_090000000.MP.jpg
│   └── screenshots/Screenshot_20231231-235959.png
├── 2024/
│   ├── PXL_20240815_120000000.jpg                # from the source2 root
│   ├── photos/
│   │   ├── 2024-06-15_beach.jpg
│   │   ├── 20240615-vacation.png
│   │   ├── 20240615_sunset.jpg
│   │   ├── DSC_20240210_001.jpg
│   │   ├── IMG_20240815_160000.jpg
│   │   ├── PXL_20240315_143022456.jpg
│   │   ├── PXL_20240815_120000000.jpg
│   │   ├── PXL_20240815_143000 (1).jpg
│   │   ├── PXL_20250101_120000000.NIGHT.jpg      # moved by the cutoff
│   │   ├── PXL_20250101_130000000.jpg            # moved by the cutoff
│   │   ├── family & friends 2024-08-15.jpg
│   │   └── 2024/summer/PXL_20240701_120000000.jpg
│   ├── screenshots/Screenshot_20240615-143022.png
│   ├── videos/
│   │   ├── PXL_20240720_183045123.TS.mp4
│   │   └── VID_20240815_143000.mp4
│   └── whatsapp/
│       ├── IMG-20240915-WA0001.jpg
│       └── VID-20240915-WA0002.mp4
└── 2025/
    ├── .hidden/.secret_photo.jpg                 # only without --exclude-hidden
    ├── document.pdf                              # only without --media-only
    ├── some_notes.txt                            # only without --media-only
    ├── photos/
    │   ├── DSCF1234.jpg                          # creation date
    │   ├── IMG_9876.jpg                          # creation date
    │   ├── PXL_20250101_150000000.jpg            # 15:00, past the cutoff
    │   ├── random_uuid_photo.jpg                 # creation date
    │   └── vacation_photo.jpg                    # creation date
    └── videos/family_video.mp4                   # creation date
```


## Troubleshooting
### Common Issues
1. **"Using creation date" warnings**: The filename doesn't match any known pattern
2. **Interrupted operations**: Use `--resume` to continue from where you left off

### Status File
The tool creates a `.media_sort_status.json` file to track progress. This file:
* Allows resuming interrupted operations
* Tracks processed, failed, and pending files
* Is automatically deleted after successful completion (with prompt)
* Is not written during a dry run, so a preview never makes the real run afterwards skip everything
* Is discarded when the resume prompt is answered with no, so the next run starts from scratch


## Conflict Resolution
When a file would land on a file that already exists, the tool asks what to do. Two kinds of conflict are detected:

1. **Physical**: the file is already in the output directory
2. **Virtual**: the file is already in one of the virtual directories of this run (`--virtual-target` or `--virtual-source`), found in the metadata cache so that the directory itself is not touched

One file can hit several conflicts at once: the one in the output directory plus one per virtual directory that holds it. They are asked in that order and the first refusal skips the file, so declining the copy in the output directory never leads to a question about the same file on a virtual directory. A refusal is any answer that does not allow the copy: a plain `n`, or a conditional (`larger`/`older`/`newer`) whose condition is not met. Virtual conflicts are shown with a `[VIRTUAL]` prefix.

A virtual directory is never written to, whatever you answer. Answering `y` to a virtual conflict means the file is copied into the output directory even though a copy of it exists on the virtual directory; the file on the virtual directory stays as it is.

### Answering a Conflict
An answer takes the form `action:scope`, or just `action` for the conflict at hand (the scope then defaults to this conflict only). The action decides what happens to the file, the scope decides how far the answer reaches. Examples: `y`, `n:virtual`, `larger:f`, `y:all`.

The action decides whether the planned copy is carried out:

| Action          | Carries out the copy                                        |
| --------------- | ----------------------------------------------------------- |
| `y` / `yes`     | Always                                                      |
| `n` / `no`      | Never (skips the file)                                      |
| `l` / `larger`  | Only when the source file is larger than the one in the way |
| `o` / `older`   | Only when the source file is older than the one in the way  |
| `new` / `newer` | Only when the source file is newer than the one in the way  |

The scope decides which further conflicts the same action settles without asking again:

| Scope           | Reaches                                                                        |
| --------------- | ------------------------------------------------------------------------------ |
| (none)          | This conflict only                                                             |
| `f` / `file`    | Every remaining conflict of the same file                                      |
| `v` / `virtual` | This conflict and every remaining one on a virtual directory, in the whole run |
| `a` / `all`     | Every remaining conflict of the run                                            |

* `f` is for a file in conflict in several places at once, so that one decision covers all of them
* `v` is for the common wish to skip everything the virtual directories already hold, while still being asked about the copies in the output directory: answer `n:virtual` once
* With `--verbose` every conflict that a scope settled on the way is printed, so nothing is decided silently

The action always answers the conflict on screen; the scope only decides which *later* conflicts inherit that answer. So `n:virtual` typed while a virtual conflict is shown skips that one and every later virtual conflict, but `n:virtual` typed while the output-directory copy is shown still skips that copy (and the whole file), and only *then* auto-declines the later virtual conflicts. You cannot leave the conflict in front of you unanswered.

### Numbering
The prompt counts the files that need a decision, not the conflicts. A file that is blocked in a single place gets a plain number, a file that is blocked in several places gets that number plus one part per conflict:

```text
Conflict 3 of 10:      one conflict, an ordinary file
Conflict 4.1 of 10:    the copy in the output directory
Conflict 4.2 of 10:    the same file on the first virtual target
Conflict 4.3 of 10:    the same file on the second virtual target
Conflict 5 of 10:      the next file
```

Declining 4.1 skips the file, so 4.2 and 4.3 are never asked and the next question is 5. The count of decisions therefore stays correct even when one answer settles several conflicts, and the summary line at the start names both numbers, for instance "Found 11 conflicts on 10 files to resolve".


## Virtual Directories
A virtual directory is an archive that planned copies are checked against without writing to it, to catch files that are already kept elsewhere. It is typically on a slow network share (Samba, NFS, ...), which is what the caching is for, but any directory can be used. It is only read, and only names, sizes and timestamps are read, never file content, which keeps the cost at one directory entry plus one stat per file. There are two kinds, told apart by the option, not guessed:

* **`--virtual-target` / `--vt`** for a directory that already holds an earlier, finished sort, so it mirrors the output tree with its year folders. Its path below the directory is compared to the planned path below the output directory directly.
* **`--virtual-source` / `--vs`** for a directory that is not sorted, laid out like a source. Each of its files is first virtually placed in the year folder it would land in, by the same date rules as a real source, and that virtually sorted path is compared instead. Sub-folders are kept, so a file matches a planned copy only when it has the same structure below its own root; flatten the directory first if you want name-only matching. Nothing on the directory is moved; the sorting is only computed.

Either way a match counts as a conflict, and a planned copy is not carried out unless you allow it. Virtual directories must not contain each other, and the same directory cannot be both a target and a source; the tool refuses such a pair.

### Usage
Repeat the option once per directory. `--vt` and `--vs` can be mixed.
```bash
# Two sorted targets
python media_sort.py /local/photos --vt /mnt/nas1 --vt /mnt/nas2

# An unsorted archive as a source
python media_sort.py /local/photos --vs /mnt/dump

# Take what was cached earlier instead of walking the directories again
python media_sort.py /local/photos --vt /mnt/nas1 --keep-virtual-cache

# Combine with other options
python media_sort.py /photos --vt /smb/share --dry-run --verbose
```

### Building the Cache Ahead of Time
Reading a large virtual directory takes long, so it can be done while it is fast or reachable and used later. `--build-virtual-cache` does only that and needs neither a source nor an output directory.
```bash
# 1. Fill the cache, sort nothing
python media_sort.py --build-virtual-cache --vt /mnt/nas1

# 2. Copy later from the cache, without reading /mnt/nas1 again
python media_sort.py /local/photos --vt /mnt/nas1 --keep-virtual-cache
```

A `--dry-run` fills the cache as well, and shows what would be copied on top of it. It needs a source directory and writes nothing else, not even the status file.
```bash
python media_sort.py /local/photos --vt /mnt/nas1 --dry-run   # answer no when it offers to delete the cache
```

Without `--keep-virtual-cache` the later run reads the virtual directory again, which is the safe default. Keep the cache only as long as you trust it to still describe the directory. The cache keeps the metadata as read; whether a directory is treated as sorted or unsorted is decided each run by the option, so the same cache serves both `--vt` and `--vs`.

### Cache Management
The cache file is `.media_sort_virtual_cache.json`, or whatever `--virtual-cache-file` names. Two options decide what a run does with it:

| Options                                         | Given virtual dirs   | Entries of other directories |
| ----------------------------------------------- | -------------------- | ---------------------------- |
| (none)                                          | read again           | dropped                      |
| `--keep-virtual-cache`                          | taken from the cache | kept                         |
| `--keep-virtual-cache` `--update-virtual-cache` | read again           | kept                         |
| `--update-virtual-cache`                        | read again           | dropped, same as no option   |

* Dropping by default keeps a run from comparing against a directory it was not pointed at, even if an earlier run cached one
* Entries of directories that this run was not pointed at are never compared against, whether they were kept or not, so keeping them only saves the next run some reading
* The date a reused directory was last read is printed as a warning, since the directory may have changed since
* Directories are always matched by their absolute path, so `nas`, `./nas` and `/mnt/nas` are one entry
* Directory symlinks are followed while reading, unless `--no-follow-virtual-symlinks` is given; the cache then reflects whichever choice built it, so a reused cache keeps it
* A directory that cannot be found stops the run with an error instead of silently reporting no conflicts. For a network share that is the case when it is not mounted. Nothing is written before that point, and a status file of an earlier run stays untouched
* The tool prompts to delete the cache after a run that used virtual directories
