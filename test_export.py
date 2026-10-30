"""Offline regression checks for exact deduplication and recoverable failures."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, PngImagePlugin

from yuketang_export import export_lesson, pixel_data


def png(color, note=''):
    image = Image.new('RGB', (96, 54), color)
    info = PngImagePlugin.PngInfo()
    info.add_text('note', note)
    data = io.BytesIO()
    image.save(data, format='PNG', pnginfo=info)
    return data.getvalue()


class Response:
    status = 200

    def __init__(self, url, data=None, body=None, status=200):
        self.url, self.data, self.raw, self.status = url, data, body, status

    def json(self):
        return {'code': 0, 'data': self.data}

    def body(self):
        return self.raw


class Client:
    def __init__(self, fail=False):
        first = png('white', 'first encoding')
        same = png('white', 'different metadata, identical pixels')
        changed = Image.open(io.BytesIO(first)).convert('RGB')
        changed.putpixel((48, 27), (254, 255, 255))
        buffer = io.BytesIO()
        changed.save(buffer, format='PNG')
        self.images = {'a': first, 'b': png('blue'), 'c': same, 'd': buffer.getvalue()}
        self.downloads = []
        self.fail = fail

    def get(self, url, **kwargs):
        if '/lesson-info' in url:
            return Response(url, {'lessonId': '1', 'lessonName': 'test', 'courseName': 'test',
                                  'classroomId': '2', 'start': 1000, 'end': 8000})
        if '/student/review' in url:
            return Response(url, {'timelineList': [
                {'id': key, 'index': n, 'dt': 1000 + n * 1000, 'firstTime': 1000,
                 'duration': 1000, 'type': 'slide', 'visible': key != 'hidden',
                 'cover': f'https://rain-pri-ups.yuketang.cn/{key}?auth_key=secret'}
                for n, key in enumerate(['a', 'b', 'c', 'd', 'hidden'])]})
        key = url.split('/')[-1].split('?')[0]
        self.downloads.append(key)
        if self.fail and key == 'b':
            return Response(url, status=503)
        return Response(url, body=self.images[key])


class ExportTests(unittest.TestCase):
    def test_pixel_hash_ignores_metadata_and_keeps_one_pixel_changes(self):
        client = Client()
        self.assertNotEqual(client.images['a'], client.images['c'])
        self.assertEqual(pixel_data(client.images['a'])[1], pixel_data(client.images['c'])[1])
        self.assertNotEqual(pixel_data(client.images['a'])[1], pixel_data(client.images['d'])[1])

    def test_export_order_hidden_pages_and_cache(self):
        import json
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            client = Client()
            folder = export_lesson(client, '1', temp)
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual([e['pdf_page'] for e in manifest['events']], [1, 2, 1, 3, None])
            self.assertEqual(manifest['events'][2]['duplicate_of_sequence'], 1)
            self.assertEqual(client.downloads, ['a', 'b', 'c', 'd'])
            self.assertNotIn('auth_key', (folder / 'manifest.json').read_text(encoding='utf-8'))
            self.assertTrue(manifest['complete'])
            self.assertEqual(manifest['summary']['unique_pages'], 3)
            client.downloads.clear()
            export_lesson(client, '1', temp)
            self.assertEqual(client.downloads, [])

    def test_failure_leaves_partial_manifest_and_can_resume(self):
        import json
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            client = Client(fail=True)
            with self.assertRaisesRegex(RuntimeError, '503'):
                export_lesson(client, '1', temp)
            folder = next(Path(temp).iterdir())
            self.assertFalse((folder / 'manifest.json').exists())
            self.assertFalse((folder / '课件汇总_去重.pdf').exists())
            partial = json.loads((folder / 'manifest.partial.json').read_text(encoding='utf-8'))
            self.assertFalse(partial['complete'])
            self.assertEqual(partial['summary']['failed_sequences'], [2])
            client.fail = False
            client.downloads.clear()
            export_lesson(client, '1', temp)
            self.assertEqual(client.downloads, ['b', 'c', 'd'])
            self.assertFalse((folder / 'manifest.partial.json').exists())


if __name__ == '__main__':
    unittest.main()
