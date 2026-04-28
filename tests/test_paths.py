from app.core.paths import (
    is_directory_path,
    join_xbox_path,
    map_xbox_path_to_usb,
    normalize_xbox_path,
    parent_dir,
    split_xbox_drive,
)


class TestNormalize:
    def test_backslash_to_forward(self):
        assert normalize_xbox_path("Hdd1:\\Content\\Foo\\") == "Hdd1:/Content/Foo/"

    def test_already_forward(self):
        assert normalize_xbox_path("Hdd1:/Content/") == "Hdd1:/Content/"

    def test_empty(self):
        assert normalize_xbox_path("") == ""


class TestIsDirectory:
    def test_trailing_slash_dir(self):
        assert is_directory_path("Hdd1:\\Content\\")
        assert is_directory_path("Hdd1:/Content/")

    def test_no_extension_dir(self):
        assert is_directory_path("Hdd1:/Content/0000000000000000")

    def test_with_extension_file(self):
        assert not is_directory_path("Hdd1:/Content/file.bin")

    def test_xex_file(self):
        assert not is_directory_path("Hdd1:\\Content\\game.xex")


class TestJoin:
    def test_dir_appends_filename(self):
        result = join_xbox_path("Hdd1:\\Content\\", "mod.bin")
        assert result == "Hdd1:/Content/mod.bin"

    def test_file_path_used_as_is(self):
        result = join_xbox_path("Hdd1:\\Content\\specific.bin", "ignored.bin")
        assert result == "Hdd1:/Content/specific.bin"

    def test_dir_no_trailing_slash(self):
        # No extension → directory
        result = join_xbox_path("Hdd1:/Content/dirname", "file.bin")
        assert result == "Hdd1:/Content/dirname/file.bin"


class TestSplitDrive:
    def test_hdd1(self):
        drive, rest = split_xbox_drive("Hdd1:\\Content\\Foo")
        assert drive == "Hdd1:"
        assert rest == "/Content/Foo"

    def test_no_drive(self):
        drive, rest = split_xbox_drive("/Content/Foo")
        assert drive == ""
        assert rest == "/Content/Foo"


class TestUsbMap:
    def test_basic(self):
        result = map_xbox_path_to_usb("Hdd1:\\Content\\Game\\file.bin", "/media/user/XBOX")
        assert result.replace("\\", "/") == "/media/user/XBOX/Content/Game/file.bin"

    def test_windows_root(self):
        result = map_xbox_path_to_usb("Hdd1:/Content/", "E:\\")
        # Path component preserved
        assert "Content" in result


class TestParent:
    def test_file_parent(self):
        assert parent_dir("Hdd1:/Content/foo.bin") == "Hdd1:/Content"

    def test_trailing_slash(self):
        assert parent_dir("Hdd1:/Content/") == "Hdd1:"
