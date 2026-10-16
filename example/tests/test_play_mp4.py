import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


client_stub = types.ModuleType('whisplay_client')
client_stub.create_whisplay_hardware = None
spec = importlib.util.spec_from_file_location('play_mp4_under_test',
                                            Path(__file__).parents[1] / 'play_mp4.py')
player = importlib.util.module_from_spec(spec)
import_stubs = {'whisplay_client': client_stub}
try:
    import PIL
except ImportError:
    pil_stub = types.ModuleType('PIL')
    pil_stub.Image = pil_stub.ImageDraw = pil_stub.ImageFont = None
    import_stubs['PIL'] = pil_stub
with patch.dict(sys.modules, import_stubs):
    spec.loader.exec_module(player)


class FakeBoard:
    LCD_WIDTH = 2
    LCD_HEIGHT = 2

    def __init__(self):
        self.cleaned = False

    def set_backlight(self, _brightness):
        pass

    def on_focus_revoked(self, callback):
        self.revoke = callback

    def on_exit_request(self, callback):
        self.exit = callback

    def cleanup(self):
        self.cleaned = True


class FakeProcess:
    def __init__(self, callback=None, exited=False, piped=True):
        self.returncode = 0 if exited else None
        self.killed = False
        self.reaped = False
        self.stdout = io.BytesIO() if piped else None
        if callback:
            def readinto(_buffer):
                callback()
                return 0
            self.stdout.readinto = readinto

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout):
        self.reaped = True
        return self.returncode


class MediaCleanupTests(unittest.TestCase):
    def run_player(self, board, processes, audio):
        with patch.object(player, 'create_whisplay_hardware', return_value=board), \
             patch.object(player.os.path, 'exists', return_value=True), \
             patch.object(player, 'get_ffmpeg_cmd', return_value=['ffmpeg']), \
             patch.object(player.subprocess, 'Popen', side_effect=processes), \
             patch.object(player, 'start_audio_process', side_effect=audio):
            player.play_video('test.mp4')

    def test_focus_revocation_stops_and_reaps_both_children(self):
        board = FakeBoard()
        decoder = FakeProcess(lambda: board.revoke())
        audio = FakeProcess(piped=False)
        self.run_player(board, [decoder], [audio])
        for child in (decoder, audio):
            self.assertTrue(child.killed)
            self.assertTrue(child.reaped)
        self.assertTrue(decoder.stdout.closed)
        self.assertTrue(board.cleaned)

    def test_exit_request_does_not_wait_for_focus_timeout(self):
        board = FakeBoard()
        decoder = FakeProcess(lambda: board.exit())
        audio = FakeProcess(piped=False)
        self.run_player(board, [decoder], [audio])
        self.assertTrue(audio.killed and audio.reaped)
        self.assertTrue(board.cleaned)

    def test_finished_audio_is_reaped_without_killing(self):
        board = FakeBoard()
        decoder = FakeProcess(lambda: board.revoke())
        audio = FakeProcess(exited=True, piped=False)
        self.run_player(board, [decoder], [audio])
        self.assertFalse(audio.killed)
        self.assertTrue(audio.reaped)

    def test_audio_start_failure_cleans_decoder_and_board(self):
        board = FakeBoard()
        decoder = FakeProcess()
        with self.assertRaises(FileNotFoundError):
            self.run_player(board, [decoder], FileNotFoundError('ffplay'))
        self.assertTrue(decoder.killed and decoder.reaped)
        self.assertTrue(board.cleaned)

    def test_decoder_start_failure_cleans_board(self):
        board = FakeBoard()
        with self.assertRaises(FileNotFoundError):
            self.run_player(board, FileNotFoundError('ffmpeg'), [])
        self.assertTrue(board.cleaned)

    def test_loop_restart_reaps_old_decoder_and_final_children(self):
        board = FakeBoard()
        first = FakeProcess(exited=True)
        second = FakeProcess(lambda: board.revoke())
        audio = FakeProcess(piped=False)
        self.run_player(board, [first, second], [audio])
        self.assertTrue(first.reaped and first.stdout.closed)
        self.assertTrue(second.killed and second.reaped)
        self.assertTrue(audio.killed and audio.reaped)

    def test_exit_before_startup_does_not_create_children(self):
        class ExitingBoard(FakeBoard):
            def on_exit_request(self, callback):
                callback()
        board = ExitingBoard()
        self.run_player(board, [], [])
        self.assertTrue(board.cleaned)

    def test_keyboard_interrupt_stops_both_children(self):
        board = FakeBoard()
        def interrupt():
            raise KeyboardInterrupt
        decoder = FakeProcess(interrupt)
        audio = FakeProcess(piped=False)
        self.run_player(board, [decoder], [audio])
        self.assertTrue(decoder.killed and decoder.reaped)
        self.assertTrue(audio.killed and audio.reaped)
        self.assertTrue(board.cleaned)


if __name__ == '__main__':
    unittest.main()
