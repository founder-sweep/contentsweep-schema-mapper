"""Clean unchanged companion files after a client removes this skill's entry."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath


def cleanup(skill_dir: Path, manifest: dict, *, apply: bool = False) -> dict:
    if skill_dir.is_symlink() or not skill_dir.is_dir():
        raise ValueError('Choose the actual installed skill directory, not a symlink')
    allowed_names = {manifest['skill'], PurePosixPath(manifest['skill_path']).parent.name}
    if skill_dir.name not in allowed_names:
        raise ValueError('The directory name does not match this product skill')
    if (skill_dir / 'SKILL.md').exists() or (skill_dir / 'SKILL.md').is_symlink():
        raise ValueError('Remove the skill through the client first; its SKILL.md is still present')
    owned = manifest['owned_skill_files']
    paths = {}
    # Validate the entire receipt before making any change.
    for name, expected in owned.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or not relative.parts or any(p in {'.', '..'} for p in relative.parts):
            raise ValueError('Unsafe path in the ownership manifest')
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('Invalid checksum in the ownership manifest')
        paths[name] = skill_dir.joinpath(*relative.parts)
    result = {'apply': apply, 'skill': manifest['skill'], 'removed': [], 'matching': [],
              'modified_or_linked': [], 'missing': [], 'unowned': []}
    for name, path in paths.items():
        relative = PurePosixPath(name)
        linked = any(skill_dir.joinpath(*relative.parts[:i]).is_symlink()
                     for i in range(1, len(relative.parts) + 1))
        if linked:
            result['modified_or_linked'].append(name)
        elif not path.exists():
            result['missing'].append(name)
        elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != owned[name]:
            result['modified_or_linked'].append(name)
        else:
            result['matching'].append(name)
            if apply:
                path.unlink()
                result['removed'].append(name)
    # Never follow directory symlinks or delete files absent from the receipt.
    for directory, dirs, files in os.walk(skill_dir, followlinks=False):
        base = Path(directory)
        for name in files:
            path = base / name
            relative = path.relative_to(skill_dir).as_posix()
            if relative not in owned:
                result['unowned'].append(relative)
        for name in dirs:
            path = base / name
            if path.is_symlink():
                relative = path.relative_to(skill_dir).as_posix()
                if relative not in owned:
                    result['unowned'].append(relative)
    if apply:
        for directory, _, _ in os.walk(skill_dir, topdown=False, followlinks=False):
            try:
                Path(directory).rmdir()
            except OSError:
                pass  # Modified files, unowned files and symlinks remain in place.
    for value in result.values():
        if isinstance(value, list):
            value.sort()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-dir', type=Path, required=True)
    parser.add_argument('--manifest', type=Path,
                        default=Path(__file__).resolve().parent.parent / 'product-manifest.json')
    parser.add_argument('--apply', action='store_true', help='Remove matching companions; default is preview')
    args = parser.parse_args()
    try:
        result = cleanup(args.skill_dir, json.loads(args.manifest.read_text()), apply=args.apply)
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
