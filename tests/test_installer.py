from pathlib import Path

from app.core.installer import _resolve_remote_paths


def test_single_install_path_dir_with_multiple_files(tmp_path: Path):
    root = tmp_path / "x"
    root.mkdir()
    f1 = root / "a.bin"
    f2 = root / "b.bin"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")

    mappings = _resolve_remote_paths(["Hdd1:\\Content\\"], [f1, f2], root)
    remotes = sorted(r for _, r in mappings)
    assert remotes == ["Hdd1:/Content/a.bin", "Hdd1:/Content/b.bin"]


def test_single_install_path_with_subdir(tmp_path: Path):
    root = tmp_path / "x"
    sub = root / "sub"
    sub.mkdir(parents=True)
    f = sub / "file.bin"
    f.write_bytes(b"x")
    mappings = _resolve_remote_paths(["Hdd1:\\Content\\"], [f], root)
    assert mappings[0][1] == "Hdd1:/Content/sub/file.bin"


def test_one_to_one_mapping(tmp_path: Path):
    root = tmp_path / "x"
    root.mkdir()
    f1 = root / "a.bin"
    f2 = root / "b.bin"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")

    mappings = _resolve_remote_paths(
        ["Hdd1:\\A\\", "Hdd1:\\B\\"],
        [f1, f2],
        root,
    )
    assert mappings[0][1] == "Hdd1:/A/a.bin"
    assert mappings[1][1] == "Hdd1:/B/b.bin"


def test_exact_file_install_path(tmp_path: Path):
    root = tmp_path / "x"
    root.mkdir()
    f = root / "src.bin"
    f.write_bytes(b"x")
    mappings = _resolve_remote_paths(["Hdd1:\\Content\\final.bin"], [f], root)
    assert mappings[0][1] == "Hdd1:/Content/final.bin"
