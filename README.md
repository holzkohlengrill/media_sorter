# Media Sorter
A Python command-line tool that automatically sorts photos, videos, and GIFs into year-based folders by extracting dates from filenames or using file creation dates.

## Features
* **Smart Date Detection**: Extracts dates from various filename patterns (PXL, IMG, Screenshot, WhatsApp, etc.)
    * **Fallback to Creation Date**: Uses file creation date when no date pattern is found in the filename
* **Directory Structure Preservation**: Maintains the original folder hierarchy within year folders
* **Conflict Resolution**: Interactive handling of duplicate files with multiple options
* **Resumable Operations**: Can be interrupted and resumed from where it left off
* **Cross-Platform**: Works on Windows, macOS, and Linux
* **Colored Output**: Clear, colored terminal output (when supported)
* **Flexible Options**: Copy or move files, process media-only, include/exclude hidden files


## Installation
* No installation required - it's a single Python file 🎉
* Requires Python 3.6+


## Usage
### Command Help
```
usage: media_sort.py [-h] [-o OUTPUT] [--dry-run] [--move] [--media-only] [--exclude-hidden] [--resume]
                     [--status-file STATUS_FILE]
                     sources [sources ...]

Sort media files into year-based folders

positional arguments:
  sources               Source directories to sort

options:
  -h, --help            show this help message and exit
  -o OUTPUT, --output OUTPUT
                        Output directory (default: same level as first source)
  --dry-run             Show what would be done without actually doing it
  --move                Move files instead of copying them
  --media-only          Only process media files (photos, videos, gifs)
  --exclude-hidden      Exclude hidden files and directories (included by default)
  --resume              Resume from previous interrupted operation
  --status-file STATUS_FILE
                        Status file for resume capability (default: .media_sort_status.json)

Examples:
  media_sort.py /path/to/source
  media_sort.py /path/to/source1 /path/to/source2 -o /path/to/output
  media_sort.py /path/to/source --dry-run
  media_sort.py /path/to/source --move --media-only
  media_sort.py /path/to/source --exclude-hidden  # Exclude hidden files/directories
  media_sort.py /path/to/source --resume  # Resume interrupted operation
```

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
   * Files created on January 1st before 14:00 are considered part of the previous year
   * Directory structure is preserved: `source/a/b/photo.jpg` → `output/2024/a/b/photo.jpg`
4. **Conflict Resolution**: When a file already exists at the destination:
   * Interactive prompt with options: Yes/No/Larger/Older/Newer
   * Scope options: Current file only/All files/All following files


## Supported File Types
### Media Files (default with `--media-only`)
* **Images**: jpg, jpeg, png, gif, bmp, tiff, tif, webp, heic, heif, raw, cr2, nef, arw, dng, svg
* **Videos**: mp4, avi, mov, wmv, flv, mkv, webm, m4v, mpg, mpeg, 3gp, 3g2, mts, m2ts, vob, ogv

### Hidden Files
* By default, hidden files and directories (starting with `.`) are included
* Use `--exclude-hidden` to skip them
* Always skipped with `--exclude-hidden`: `.git`, `.svn`, `.DS_Store`, `.Trash`, `node_modules`, `__pycache__`

## Test Data Structure
The `test/` directory contains sample files for testing the tool:
```
test/
├── source1/
│   ├── photos/
│   │   ├── PXL_*.jpg                   # Google Pixel photos with dates
│   │   │   ├── PXL_20240101_120000.jpg # Jan 1st after 14:00 (stays in 2024)
│   │   │   ├── PXL_20250101_100000.jpg # New Year's exception (before 14:00 → 2024)
│   │   │   └── PXL_20240720_*.jpg      # Regular dates
│   │   ├── IMG_*.jpg                   # Standard camera photos
│   │   ├── DSC_*.jpg                   # Digital camera photos
│   │   ├── YYYY-MM-DD_*.jpg            # Generic date patterns
│   │   ├── YYYYMMDD.jpg                # Simple date pattern (e.g., 20240815.jpg)
│   │   ├── vacation_photo.jpg          # No date in filename (uses creation date)
│   │   ├── "family photo (2024).jpg"   # Spaces and parentheses in filename
│   │   ├── photo@event.jpg             # Special characters (@, #, &)
│   │   └── 2024/summer/                # Nested directory structure
│   │       └── beach_sunset.jpg        # Preserves source directory structure
│   ├── videos/
│   │   ├── PXL_*.mp4                   # Google Pixel videos
│   │   ├── VID_*.mp4                   # Standard video files
│   │   └── random_video.mp4            # No date pattern (uses creation date)
│   ├── .hidden/                        # Hidden directory with files
│   │   ├── .secret_photo.jpg           # Hidden file
│   │   └── .config/                    # Nested hidden directory
│   │       └── settings.json           # Non-media in hidden directory
│   └── document.pdf                    # Non-media file (filtered with --media-only)
└── source2/
    ├── screenshots/
    │   ├── Screenshot_*.png            # Screenshot files
    │   └── Screenshot_*_duplicate.png  # Same name from different sources (conflict test)
    └── whatsapp/
        └── IMG-*-WA*.jpg               # WhatsApp media files
```

### Run
```bash
# Run with test data
cd test
python ../media_sort.py source1 source2 -o output --dry-run

# Test with media-only filter
python ../media_sort.py source1 --media-only --dry-run

# Do the actual run
python ../media_sort.py source1 source2 -o output
```

### Expected Output Structure
After running the tool on the test data, the output directory would be organized like this:
```
output/
├── 2023/
│   ├── photos/
│   │   └── DSC_20230615.jpg
│   └── whatsapp/
│       └── IMG-20230815-WA0001.jpg
├── 2024/
│   ├── photos/
│   │   ├── PXL_20240101_120000.jpg
│   │   ├── PXL_20240101_160000.jpg
│   │   ├── IMG_20240315_143022.jpg
│   │   ├── 2024-06-15_vacation.jpg
│   │   ├── vacation_photo.jpg         # Uses creation date
│   │   └── 2024/
│   │       └── summer/
│   │           └── beach_sunset.jpg
│   ├── videos/
│   │   ├── PXL_20240720_180530.mp4
│   │   └── VID_20240815_120000.mp4
│   ├── screenshots/
│   │   └── Screenshot_20240915-143022.png
│   ├── .hidden/                       # Only if --exclude-hidden not used
│   │   └── .secret_photo.jpg
│   └── document.pdf                   # Only if --media-only not used
└── 2025/
    └── photos/
        └── PXL_20250101_100000.jpg   # New Year's exception (before 14:00)
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
