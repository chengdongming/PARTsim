#!/usr/bin/env python3
"""Generate and validate a lightweight PARTSim local build identity."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


BUILD_IDENTITY_SCHEMA_VERSION = 1
BUILD_IDENTITY_FILENAME = 'partsim_build_identity.json'


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _critical_source_paths(source_root):
    source_root = Path(source_root).resolve()
    patterns = (
        'CMakeLists.txt', 'cmake/**/*.cmake', 'cmakeopts/**/*.cmake',
        'cmakeopts/**/CMakeLists.txt', 'libmetasim/CMakeLists.txt',
        'libmetasim/**/*.cpp', 'libmetasim/**/*.hpp',
        'librtsim/CMakeLists.txt', 'librtsim/**/*.cpp',
        'librtsim/**/*.hpp', 'rtsim/CMakeLists.txt', 'rtsim/**/*.cpp',
        'rtsim/**/*.hpp', 'scripts/build_identity.py',
    )
    paths = set()
    for pattern in patterns:
        paths.update(path for path in source_root.glob(pattern) if path.is_file())
    return sorted(paths, key=lambda path: path.relative_to(source_root).as_posix())


def _git_bytes(source_root, arguments):
    completed = subprocess.run(
        ['git', *arguments], cwd=str(source_root), check=True,
        capture_output=True,
    )
    return completed.stdout


def source_tree_identity(source_root):
    source_root = Path(source_root).resolve()
    files = [{
        'relative_path': path.relative_to(source_root).as_posix(),
        'size_bytes': path.stat().st_size,
        'sha256': file_sha256(path),
    } for path in _critical_source_paths(source_root)]
    tracked_diff = _git_bytes(
        source_root, ['diff', '--binary', 'HEAD', '--']
    )
    untracked = _git_bytes(
        source_root, ['ls-files', '--others', '--exclude-standard']
    ).decode('utf-8').splitlines()
    untracked_production = []
    for relative in sorted(untracked):
        path = (source_root / relative).resolve()
        try:
            path.relative_to(source_root)
        except ValueError:
            continue
        if (path.is_file()
                and relative.startswith(('librtsim/', 'libmetasim/', 'rtsim/'))):
            untracked_production.append({
                'relative_path': relative,
                'size_bytes': path.stat().st_size,
                'sha256': file_sha256(path),
            })
    payload = {
        'files': files,
        'tracked_diff_size': len(tracked_diff),
        'tracked_diff_sha256': hashlib.sha256(tracked_diff).hexdigest(),
        'untracked_production_files': untracked_production,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(',', ':'), allow_nan=False,
    ).encode('utf-8')
    return {
        'combined_sha256': hashlib.sha256(encoded).hexdigest(),
        **payload,
    }


def _relative_build_path(path, build_dir):
    path = Path(path).resolve()
    build_dir = Path(build_dir).resolve()
    return path.relative_to(build_dir).as_posix()


def generate_build_identity(source_root, build_dir, build_type, compiler_id,
                            compiler_version, binary, library, output):
    source_root = Path(source_root).resolve()
    build_dir = Path(build_dir).resolve()
    binary = Path(binary).resolve()
    library = Path(library).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema_version': BUILD_IDENTITY_SCHEMA_VERSION,
        'source_root': str(source_root),
        'build_directory': str(build_dir),
        'build_type': str(build_type),
        'compiler_id': str(compiler_id),
        'compiler_version': str(compiler_version),
        'configure_time': datetime.now(timezone.utc).isoformat(),
        'source_tree': source_tree_identity(source_root),
        'rtsim_binary': {
            'relative_path': _relative_build_path(binary, build_dir),
            'size_bytes': binary.stat().st_size,
            'sha256': file_sha256(binary),
        },
        'librtsim': {
            'relative_path': _relative_build_path(library, build_dir),
            'size_bytes': library.stat().st_size,
            'sha256': file_sha256(library),
        },
    }
    temporary = output.with_name(output.name + '.partial.' + str(os.getpid()))
    temporary.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + '\n',
        encoding='utf-8',
    )
    os.replace(temporary, output)
    return payload


def _load_json_no_duplicates(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate build identity key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=pairs)


def validate_build_identity(binary, source_root, required_build_type='Release'):
    binary = Path(binary).resolve()
    source_root = Path(source_root).resolve()
    build_dir = binary.parent.parent
    identity_path = build_dir / BUILD_IDENTITY_FILENAME
    if not identity_path.is_file():
        raise ValueError('fresh build identity is missing: ' + str(identity_path))
    payload = _load_json_no_duplicates(identity_path)
    if (not isinstance(payload, dict)
            or payload.get('schema_version') != BUILD_IDENTITY_SCHEMA_VERSION):
        raise ValueError('unsupported fresh build identity schema')
    if Path(str(payload.get('source_root', ''))).resolve() != source_root:
        raise ValueError('fresh build source root mismatch')
    if Path(str(payload.get('build_directory', ''))).resolve() != build_dir:
        raise ValueError('fresh build directory mismatch')
    if payload.get('build_type') != required_build_type:
        raise ValueError('fresh build type mismatch')
    if not payload.get('compiler_id') or not payload.get('compiler_version'):
        raise ValueError('fresh build compiler identity is missing')
    if not payload.get('configure_time'):
        raise ValueError('fresh build configure time is missing')

    for name, expected_path in (
            ('rtsim_binary', binary), ('librtsim', None)):
        record = payload.get(name)
        if not isinstance(record, dict):
            raise ValueError('fresh build artifact identity is missing: ' + name)
        relative = Path(str(record.get('relative_path', '')))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid fresh build artifact path')
        artifact = (build_dir / relative).resolve()
        artifact.relative_to(build_dir)
        if expected_path is not None and artifact != expected_path:
            raise ValueError('fresh rtsim binary path mismatch')
        if (not artifact.is_file()
                or artifact.stat().st_size != record.get('size_bytes')
                or file_sha256(artifact) != record.get('sha256')):
            raise ValueError('fresh build artifact hash mismatch: ' + name)

    observed_source = source_tree_identity(source_root)
    if payload.get('source_tree') != observed_source:
        raise ValueError('fresh build source fingerprint mismatch')
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--build-dir', required=True)
    parser.add_argument('--build-type', required=True)
    parser.add_argument('--compiler-id', required=True)
    parser.add_argument('--compiler-version', required=True)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--library', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    generate_build_identity(
        args.source_root, args.build_dir, args.build_type,
        args.compiler_id, args.compiler_version, args.binary,
        args.library, args.output,
    )


if __name__ == '__main__':
    main()
