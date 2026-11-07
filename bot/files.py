"""Controlled private files, shared by the two services (no SQLite)."""
from pathlib import Path
import os
import shutil
import stat

class UnsafeFile(ValueError):
    pass

def private_dir(path):
    path = Path(path).absolute()
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            if part.is_symlink() or not part.is_dir():
                raise UnsafeFile('unsafe_directory')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise UnsafeFile('directory_must_be_private')
    return path

def task_dir(task_id):
    if type(task_id) is not int or task_id <= 0:
        raise UnsafeFile('invalid_task')
    return private_dir(private_dir('data/tmp') / str(task_id))

def ready_path(task_id, audio, files_dir='files'):
    return private_dir(files_dir) / f'{task_id}.{"mp3" if audio else "mp4"}'

def checked(path, root, max_bytes=None):
    path, root = Path(path).absolute(), Path(root).absolute()
    if root not in path.parents:
        raise UnsafeFile('outside_root')
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise UnsafeFile('symlink')
        if part == root:
            break
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.geteuid() or s.st_size <= 0:
        raise UnsafeFile('invalid_file')
    if max_bytes is not None and s.st_size > max_bytes:
        raise UnsafeFile('file_too_large')
    return path

def clean_task(task_id):
    p = Path('data/tmp') / str(task_id)
    if p.is_symlink():
        raise UnsafeFile('symlink')
    if p.exists():
        shutil.rmtree(p)
