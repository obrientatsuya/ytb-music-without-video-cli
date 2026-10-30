import contextlib
import io
import json
import unittest

from ytplay_cli.cli import build_parser
from ytplay_cli.player import _build_ffplay_cmd, _build_mpv_cmd
from ytplay_cli.playqueue import Queue
from ytplay_cli.search import (
    _SEP,
    _fmt,
    _parse,
    is_playlist,
    is_video,
    is_youtube_url,
    video_id,
)


class UrlDetectionTests(unittest.TestCase):
    def test_youtube_urls_with_and_without_scheme(self):
        self.assertTrue(is_youtube_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(is_youtube_url("youtu.be/abc"))
        self.assertTrue(is_youtube_url("music.youtube.com/watch?v=abc"))
        self.assertFalse(is_youtube_url("https://example.com/watch?v=abc"))
        self.assertFalse(is_youtube_url("not a url"))

    def test_playlist_detection(self):
        self.assertTrue(
            is_playlist("https://www.youtube.com/watch?v=abc&list=PL123")
        )
        self.assertTrue(
            is_playlist("https://www.youtube.com/playlist?list=PL123")
        )
        self.assertFalse(is_playlist("https://youtu.be/abc"))
        self.assertFalse(is_playlist("https://example.com/playlist?list=PL1"))

    def test_video_detection(self):
        self.assertTrue(is_video("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(is_video("https://youtu.be/abc?t=10"))
        self.assertTrue(is_video("https://www.youtube.com/shorts/abc"))
        self.assertTrue(is_video("https://www.youtube.com/live/abc"))
        self.assertFalse(is_video("https://www.youtube.com/playlist?list=PL1"))

    def test_video_id(self):
        self.assertEqual(
            video_id("https://www.youtube.com/watch?v=abc"), "abc"
        )
        self.assertEqual(video_id("https://youtu.be/abc?t=10"), "abc")
        self.assertEqual(
            video_id("https://www.youtube.com/shorts/abc"), "abc"
        )
        self.assertIsNone(
            video_id("https://www.youtube.com/playlist?list=PL1")
        )


class ParsingTests(unittest.TestCase):
    def test_parse_tracks_and_durations(self):
        rows = [
            ("abc", "First song", 65),
            ("def", "Second song", 3661),
            ("ghi", "Live stream", None),
        ]
        raw = "\n".join(
            _SEP.join(json.dumps(value) for value in row) for row in rows
        )
        tracks = _parse(raw)

        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[0]["yt_url"], "https://www.youtube.com/watch?v=abc")
        self.assertEqual(tracks[0]["duration"], "1:05")
        self.assertEqual(tracks[1]["duration"], "1:01:01")
        self.assertEqual(tracks[2]["duration"], "?")

    def test_parse_skips_malformed_lines(self):
        raw = "\n".join(
            [
                "not-json",
                _SEP.join([json.dumps("abc"), json.dumps("ok"), "60"]),
                _SEP.join([json.dumps("NA"), json.dumps("bad"), "1"]),
                _SEP.join([json.dumps("def")]),
            ]
        )
        tracks = _parse(raw)
        self.assertEqual([track["title"] for track in tracks], ["ok"])

    def test_clean_removes_terminal_control_characters(self):
        tracks = _parse(
            _SEP.join(
                [json.dumps("abc"), json.dumps("bad\x1b[31mtitle\nline"), "60"]
            )
        )
        self.assertEqual(tracks[0]["title"], "bad [31mtitle line")

    def test_fmt(self):
        self.assertEqual(_fmt(None), "?")
        self.assertEqual(_fmt(0), "?")
        self.assertEqual(_fmt("bad"), "?")
        self.assertEqual(_fmt(5), "0:05")
        self.assertEqual(_fmt(65), "1:05")
        self.assertEqual(_fmt(3661), "1:01:01")


class QueueTests(unittest.TestCase):
    def test_navigation(self):
        tracks = [{"title": str(i)} for i in range(4)]
        queue = Queue(tracks=tracks, index=1)

        self.assertEqual(queue.current()["title"], "1")
        self.assertTrue(queue.has_next())
        self.assertEqual(queue.next()["title"], "2")
        self.assertEqual(queue.prev()["title"], "1")
        self.assertTrue(queue.autoplay)
        self.assertFalse(queue.toggle_autoplay())
        self.assertFalse(queue.autoplay)

    def test_shuffle_keeps_current_first(self):
        tracks = [{"title": str(i)} for i in range(10)]
        queue = Queue(tracks=tracks, index=6)
        current = queue.current()

        queue.shuffle()

        self.assertEqual(queue.index, 0)
        self.assertEqual(queue.current(), current)
        self.assertEqual(len(queue.tracks), 10)
        self.assertEqual(
            {track["title"] for track in queue.tracks},
            {str(i) for i in range(10)},
        )


class PlayerCommandTests(unittest.TestCase):
    def test_mpv_command_is_audio_only_and_does_not_read_terminal(self):
        cmd = _build_mpv_cmd(
            "mpv", "https://example/audio", "/tmp/ytplay.sock", 100
        )
        self.assertIn("--no-video", cmd)
        self.assertIn("--input-terminal=no", cmd)
        self.assertIn("--input-ipc-server=/tmp/ytplay.sock", cmd)
        self.assertEqual(cmd[-2:], ["--", "https://example/audio"])

    def test_ffplay_command_uses_audio_only_settings(self):
        cmd = _build_ffplay_cmd("ffplay", "https://example/audio", 130)
        self.assertIn("-nodisp", cmd)
        self.assertIn("-autoexit", cmd)
        self.assertIn("-nostdin", cmd)
        self.assertEqual(cmd[-2:], ["-i", "https://example/audio"])
        self.assertEqual(cmd[cmd.index("-volume") + 1], "100")


class CliParserTests(unittest.TestCase):
    def test_defaults_and_flags(self):
        args = build_parser().parse_args(["daft", "punk"])
        self.assertEqual(args.query, ["daft", "punk"])
        self.assertEqual(args.limit, 8)
        self.assertEqual(args.volume, 100)
        self.assertFalse(args.no_autoplay)

        args = build_parser().parse_args(
            [
                "--limit",
                "3",
                "--volume",
                "80",
                "--no-autoplay",
                "--shuffle",
                "--single",
                "query",
            ]
        )
        self.assertEqual(args.limit, 3)
        self.assertEqual(args.volume, 80)
        self.assertTrue(args.no_autoplay)
        self.assertTrue(args.shuffle)
        self.assertTrue(args.single)

    def test_rejects_invalid_volume(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(
            SystemExit
        ):
            build_parser().parse_args(["--volume", "131"])


if __name__ == "__main__":
    unittest.main()
