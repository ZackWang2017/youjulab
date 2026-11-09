"""Small CLI shared by the Python sidecar and Electron backup workflow."""
import argparse
from pathlib import Path
from pathlib import PurePosixPath
import shutil
import sqlite3
import stat
import tarfile
import zipfile


def archive_backup(source, target):
    source, target = Path(source), Path(target)
    if target.suffix == '.zip':
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(source.rglob('*'),key=lambda p:(p.name!='manifest.json',str(p))):
                if file.is_symlink():
                    raise ValueError('备份不能包含符号链接。')
                if file.is_file():
                    archive.write(file, file.relative_to(source).as_posix())
    else:
        with tarfile.open(target, 'w:gz') as archive:
            for file in sorted(source.rglob('*'),key=lambda p:(p.name!='manifest.json',str(p))):
                if file.is_symlink():
                    raise ValueError('备份不能包含符号链接。')
                if file.is_file():
                    archive.add(file, arcname=file.relative_to(source).as_posix(), recursive=False)


def extract_backup(source, target):
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    archive = zipfile.ZipFile(source) if zipfile.is_zipfile(source) else tarfile.open(source, 'r:*')
    with archive:
        zipped = isinstance(archive, zipfile.ZipFile)
        members = archive.infolist() if zipped else archive.getmembers()
        total = 0
        for item in members:
            name = item.filename if zipped else item.name
            path = PurePosixPath(name)
            total += item.file_size if zipped else item.size
            linked = stat.S_ISLNK(item.external_attr >> 16) if zipped else not (item.isfile() or item.isdir())
            if (linked or '\\' in name or path.is_absolute() or '..' in path.parts or ':' in name
                    or not (target / name).resolve().is_relative_to(target)):
                raise ValueError('备份压缩包包含不安全的路径或链接。')
        if len(members) > 100000 or total > 20 * 1024**3:
            raise ValueError('备份压缩包超过安全解压限制。')
        for item in members:
            name = item.filename if zipped else item.name
            directory = item.is_dir() if zipped else item.isdir()
            if directory:
                (target / name).mkdir(parents=True, exist_ok=True)
                continue
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.open(item) if zipped else archive.extractfile(item)
            with stream as src:
                with destination.open('xb') as dst:
                    shutil.copyfileobj(src, dst)


def backup_sqlite(source: str, target: str):
    origin, destination = Path(source).resolve(), Path(target).resolve()
    if origin == destination:
        raise ValueError('备份目标不能与源数据库相同。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(origin.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--backup-sqlite', nargs=2)
    group.add_argument('--archive-backup', nargs=2)
    group.add_argument('--extract-backup', nargs=2)
    args = parser.parse_args(argv)
    if args.backup_sqlite:
        backup_sqlite(*args.backup_sqlite)
    elif args.archive_backup:
        archive_backup(*args.archive_backup)
    else:
        extract_backup(*args.extract_backup)


if __name__ == '__main__':
    main()
