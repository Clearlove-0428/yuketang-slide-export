"""Export the signed-in student's visible lesson slides, with exact pixel deduplication."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import struct
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, ImageOps
from playwright.sync_api import sync_playwright
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.local' / 'session.json'
BASE = 'https://www.yuketang.cn'
CN_TIME = timezone(timedelta(hours=8))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def safe_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:90] or 'lesson'


def date_text(ms, pattern='%Y-%m-%d %H:%M:%S'):
    return datetime.fromtimestamp(ms / 1000, CN_TIME).strftime(pattern) if ms else ''


def public_url(url):
    """Remove CDN signatures from the portable manifest."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


def pixel_data(data: bytes):
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        image = ImageOps.exif_transpose(source).convert('RGBA')
        background = Image.new('RGBA', image.size, 'white')
        background.alpha_composite(image)
        image = background.convert('RGB')
    digest = hashlib.sha256(struct.pack('>II', *image.size) + image.tobytes()).hexdigest()
    return image, digest


def api(client, path, params=None):
    response = client.get(BASE + path, params=params or {}, timeout=30000)
    if response.status in (401, 403) or '/web' == urlsplit(response.url).path:
        raise RuntimeError('登录已失效，请先运行 login 重新扫码。')
    if response.status != 200:
        raise RuntimeError(f'雨课堂接口返回 HTTP {response.status}: {path}')
    try:
        result = response.json()
    except Exception:
        raise RuntimeError('接口未返回 JSON，可能需要重新登录或网站已经改版。') from None
    code = result.get('code', result.get('errcode'))
    if code != 0:
        raise RuntimeError(f"雨课堂未允许此请求：{result.get('msg', result.get('errmsg', code))}")
    if 'data' not in result:
        raise RuntimeError(f'接口缺少 data 字段：{path}')
    return result['data']


def login(playwright, timeout=300):
    """Display the site's own QR code; never handle or store a password."""
    import tkinter as tk
    from PIL import ImageTk
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1200, 'height': 850})
    page = context.new_page()
    window = None
    try:
        page.goto(BASE + '/web', wait_until='domcontentloaded', timeout=30000)
        window = tk.Tk()
        window.title('雨课堂扫码登录')
        window.resizable(False, False)
        tk.Label(window, text='请用微信或雨课堂 App 扫码，并在手机上确认登录', padx=24, pady=15).pack()
        picture = tk.Label(window)
        picture.pack(padx=35, pady=10)
        status = tk.Label(window, text='正在获取雨课堂二维码…', pady=12)
        status.pack()
        cancelled = [False]
        window.protocol('WM_DELETE_WINDOW', lambda: cancelled.__setitem__(0, True))
        last_qr = 0
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            window.update()
            if cancelled[0]:
                raise RuntimeError('已取消扫码登录。')
            page.wait_for_timeout(500)
            if urlsplit(page.url).path not in ('/web', '/web/'):
                # Confirm a usable student session before saving any credentials.
                api(context.request, '/v2/api/web/courses/list', {'identity': 2})
                STATE.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(STATE))
                print('登录成功，登录状态仅保存在 .local/session.json。')
                return
            if time.monotonic() - last_qr > 140:
                if last_qr:
                    page.reload(wait_until='domcontentloaded')
                qr = page.locator('.logma:visible').first
                try:
                    data = qr.screenshot(timeout=1500)
                except Exception:
                    status.config(text='等待网站返回二维码…')
                    continue
                image = Image.open(io.BytesIO(data)).resize((300, 300), Image.Resampling.NEAREST)
                picture.image = ImageTk.PhotoImage(image)
                picture.config(image=picture.image)
                status.config(text='二维码会自动刷新；请保持此窗口打开')
                last_qr = time.monotonic()
        raise RuntimeError('扫码登录超时，请重新运行 login。')
    finally:
        if window is not None:
            window.destroy()
        context.close()
        browser.close()


def course_list(client):
    data = api(client, '/v2/api/web/courses/list', {'identity': 2})
    if not isinstance(data, dict) or not isinstance(data.get('list'), list):
        raise RuntimeError('课程列表结构已变化。')
    return data['list']


def choose_course(courses, name, classroom_id=None):
    matches = [c for c in courses if (
        str(c.get('classroom_id')) == str(classroom_id) if classroom_id
        else name in c.get('course', {}).get('name', '')
    )]
    if len(matches) != 1:
        descriptions = '\n'.join(f"{c['classroom_id']}  {c['course']['name']}  {c['name']}" for c in matches)
        raise RuntimeError(f'匹配到 {len(matches)} 门课程，请用 --classroom-id 明确指定。\n{descriptions}')
    return matches[0]


def lessons(client, classroom_id):
    """Read only classroom activities; never request homework/score details."""
    result = []
    seen = set()
    prev_id = None
    for number in range(100):
        params = {'actype': 14, 'page': number, 'offset': 20, 'sort': -1}
        if prev_id is not None:
            params['prev_id'] = prev_id
        data = api(client, f'/v2/api/web/logs/learn/{classroom_id}', params)
        if not isinstance(data.get('activities'), list):
            raise RuntimeError('课堂列表结构已变化。')
        added = 0
        for item in data['activities']:
            if item.get('type') == 14 and str(item.get('courseware_id')) not in seen:
                seen.add(str(item['courseware_id']))
                result.append(item)
                added += 1
        if not data.get('has_more'):
            return result
        if not added:
            raise RuntimeError('课堂列表分页未前进，已停止以免遗漏课堂。')
        prev_id = data.get('prev_id')
    raise RuntimeError('课堂列表超过 100 页，请按课程分批处理。')


def visible_timeline(client, lesson_id):
    data = api(client, '/api/v3/classroom-report/student/review', {'lesson_id': str(lesson_id)})
    timeline = data.get('timelineList')
    if not isinstance(timeline, list):
        raise RuntimeError('课件播放记录结构已变化。')
    return timeline


def build_pdf(folder: Path, manifest, keep_duplicates=False):
    pages = manifest['pages']
    selected = ([event['pdf_page'] for event in manifest['events'] if event.get('pdf_page')]
                if keep_duplicates else list(range(1, len(pages) + 1)))
    if not selected:
        raise RuntimeError('没有可以导出的课件图片。')
    pdf_path = folder / ('课堂顺序_含回翻.pdf' if keep_duplicates else '课件汇总_去重.pdf')
    temp_path = pdf_path.with_suffix('.pdf.tmp')
    canvas = Canvas(str(temp_path), pageCompression=1)
    canvas.setTitle(f"{manifest['lesson']['courseName']} - {manifest['lesson']['lessonName']}")
    canvas.setAuthor('雨课堂课件本地整理')
    for number in selected:
        entry = pages[number - 1]
        image_path = (folder / entry['image']).resolve()
        if not image_path.is_relative_to(folder.resolve()):
            raise RuntimeError('清单中的图片路径超出课件目录。')
        image, digest = pixel_data(image_path.read_bytes())
        if digest != entry['pixel_sha256']:
            raise RuntimeError(f'图片校验失败：{entry["image"]}')
        width, height = image.size
        canvas.setPageSize((width, height))
        # JPEG files are embedded directly, without recompression or enlargement.
        canvas.drawImage(ImageReader(str(image_path)), 0, 0, width=width, height=height)
        if not keep_duplicates:
            key = f'page-{number}'
            canvas.bookmarkPage(key)
            canvas.addOutlineEntry(f"{number:03d} | slide {entry.get('slide_index', '?')}", key, 0)
        canvas.showPage()
    canvas.save()
    temp_path.replace(pdf_path)
    return pdf_path


def write_csv(folder, events):
    fields = ['sequence', 'pdf_page', 'duplicate_of_sequence', 'slide_index', 'slide_id',
              'presentation_id', 'event_time_ms', 'relative_seconds', 'duration_ms',
              'first_time_ms', 'status']
    with (folder / '播放顺序与去重.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(events)


def export_lesson(client, lesson_id, out, keep_duplicates=False):
    info = api(client, '/api/v3/classroom-report/student/lesson-info', {'lesson_id': str(lesson_id)})
    info = {key: info.get(key) for key in ('lessonId', 'lessonName', 'start', 'end',
                                         'classroomId', 'courseId', 'classroomName', 'courseName')}
    if not info.get('end'):
        raise RuntimeError('这节课仍在上课，请在下课后导出，避免生成不完整的快照。')
    timeline = visible_timeline(client, lesson_id)
    folder = Path(out).resolve() / safe_name(
        f"{info['courseName']}_{date_text(info['start'], '%Y-%m-%d')}_{info['lessonName']}_{lesson_id}")
    cache = folder / '.cache'
    images = folder / 'images'
    cache.mkdir(parents=True, exist_ok=True)
    images.mkdir(parents=True, exist_ok=True)
    manifest = {'format_version': 1, 'lesson': info,
                'exported_at': datetime.now(CN_TIME).isoformat(),
                'source_page': f'{BASE}/v2/web/student-lesson-report/{info["classroomId"]}/{lesson_id}',
                'deduplication': 'sha256(width,height,decoded RGB pixels); first appearance; exact only',
                'order_note': 'events 保留接口原始顺序；firstTime 可能是本页首次播放时间，不能用于重新排序。',
                'pages': [], 'events': [], 'complete': False}
    hashes = {}
    errors = []
    download_count = 0
    try:
        for sequence, item in enumerate(timeline, 1):
            event_time = item.get('dt')
            event = {'sequence': sequence, 'slide_index': item.get('index'),
                     'slide_id': str(item.get('id', '')),
                     'presentation_id': str(item.get('presentationId', '')),
                     'first_time_ms': item.get('firstTime'), 'event_time_ms': event_time,
                     'relative_seconds': round((event_time - info['start']) / 1000, 3)
                         if isinstance(event_time, (int, float)) and info.get('start') else None,
                     'duration_ms': item.get('duration'), 'pdf_page': None,
                     'duplicate_of_sequence': None}
            manifest['events'].append(event)
            if item.get('visible') is not True:
                event['status'] = 'hidden_not_downloaded'
                continue
            if item.get('type') != 'slide' or not item.get('cover'):
                event['status'] = 'non_slide_or_missing_cover'
                continue
            url = item['cover']
            hostname = (urlsplit(url).hostname or '').lower()
            if urlsplit(url).scheme not in ('http', 'https') or not any(
                    hostname == domain or hostname.endswith('.' + domain)
                    for domain in ('yuketang.cn', 'xuetangx.com')):
                raise RuntimeError('图片地址不属于已知的雨课堂课件域名，已停止。')
            cache_key = hashlib.sha256(public_url(url).encode()).hexdigest()
            cache_file = cache / f'{cache_key}.image'
            try:
                if cache_file.exists():
                    raw = cache_file.read_bytes()
                    try:
                        image, digest = pixel_data(raw)
                    except Exception:
                        cache_file.unlink()
                        raise RuntimeError('图片缓存损坏，已移除；重新运行即可下载。') from None
                else:
                    response = client.get(url, headers={'Referer': BASE + '/'}, timeout=45000)
                    if response.status != 200:
                        raise RuntimeError(f'课件图片 HTTP {response.status}，重新运行可刷新临时图片地址。')
                    raw = response.body()
                    image, digest = pixel_data(raw)
                    cache_file.write_bytes(raw)
                    download_count += 1
                if digest in hashes:
                    first = hashes[digest]
                    event.update(pdf_page=first['pdf_page'], duplicate_of_sequence=first['sequence'], status='duplicate')
                else:
                    page_number = len(manifest['pages']) + 1
                    with Image.open(io.BytesIO(raw)) as original:
                        keep_jpeg = (original.format == 'JPEG' and original.mode == 'RGB'
                                     and original.getexif().get(274, 1) == 1)
                    extension = '.jpg' if keep_jpeg else '.png'
                    image_file = images / f'{page_number:03d}_{digest[:12]}{extension}'
                    if keep_jpeg:
                        image_file.write_bytes(raw)
                    else:
                        image.save(image_file, optimize=True)
                    entry = {'pdf_page': page_number, 'sequence': sequence,
                             'slide_index': item.get('index'), 'slide_id': event['slide_id'],
                             'presentation_id': event['presentation_id'],
                             'image': image_file.relative_to(folder).as_posix(),
                             'width': image.width, 'height': image.height,
                             'pixel_sha256': digest, 'source_image': public_url(url)}
                    manifest['pages'].append(entry)
                    hashes[digest] = entry
                    event.update(pdf_page=page_number, status='kept')
                print(f"[{sequence}/{len(timeline)}] 原课件页 {item.get('index')} -> PDF {event['pdf_page']}  {event['status']}", flush=True)
            except Exception as exc:
                event.update(status='failed', error=str(exc))
                errors.append(sequence)
                raise
        if not manifest['pages']:
            raise RuntimeError('这节课没有账号可见的课件图片，未生成 PDF。')
        manifest['complete'] = True
    finally:
        manifest['summary'] = {'timeline_records': len(timeline), 'processed_records': len(manifest['events']),
                               'unique_pages': len(manifest['pages']),
                               'duplicates_removed': sum(e.get('status') == 'duplicate' for e in manifest['events']),
                               'excluded_records': sum(e.get('status') in ('hidden_not_downloaded', 'non_slide_or_missing_cover') for e in manifest['events']),
                               'failed_sequences': errors, 'downloaded_this_run': download_count}
        write_json(folder / ('manifest.json' if manifest['complete'] else 'manifest.partial.json'), manifest)
        if manifest['complete']:
            (folder / 'manifest.partial.json').unlink(missing_ok=True)
            write_csv(folder, manifest['events'])
    pdf_path = build_pdf(folder, manifest)
    if keep_duplicates:
        build_pdf(folder, manifest, keep_duplicates=True)
    print(f"\n已完成：{pdf_path}\n{json.dumps(manifest['summary'], ensure_ascii=False)}")
    return folder


def print_lessons(items):
    for n, lesson in enumerate(items, 1):
        state = '已结束' if lesson.get('is_finished') else '正在上课'
        print(f"{n:2}. {date_text(lesson['create_time'])}  {lesson['title']}  [{state}]  id={lesson['courseware_id']}")


def main():
    parser = argparse.ArgumentParser(description='雨课堂可见课件导出为 PDF；完整像素相同的页面只保留一次。')
    parser.add_argument('command', choices=['wizard', 'login', 'courses', 'lessons', 'export', 'rebuild'])
    parser.add_argument('--course', default='计算机网络')
    parser.add_argument('--classroom-id')
    parser.add_argument('--lesson-id')
    parser.add_argument('--title')
    parser.add_argument('--out', default=str(ROOT / 'output'))
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--keep-duplicates', action='store_true', help='额外生成保留回翻的 PDF')
    args = parser.parse_args()
    if args.command == 'rebuild':
        if not args.manifest:
            parser.error('rebuild 需要 --manifest')
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        if not manifest.get('complete'):
            raise RuntimeError('清单尚未完整，不能生成完整课件。')
        print(build_pdf(args.manifest.resolve().parent, manifest, args.keep_duplicates))
        return
    with sync_playwright() as playwright:
        if args.command == 'login':
            login(playwright)
            return
        if not STATE.exists():
            if args.command == 'wizard':
                login(playwright)
            else:
                raise RuntimeError('尚未登录，请先运行 login，或双击 start.cmd。')
        client = playwright.request.new_context(storage_state=str(STATE))
        try:
            courses = course_list(client)
            if args.command == 'courses':
                for course in courses:
                    print(f"{course['classroom_id']}  {course['course']['name']}  {course['name']}")
                return
            if args.command == 'wizard':
                name = input('课程名称（回车选择计算机网络）：').strip() or '计算机网络'
                matches = [c for c in courses if name in c.get('course', {}).get('name', '')]
                if not matches:
                    raise RuntimeError('没有找到这门课程。')
                if len(matches) > 1:
                    for n, c in enumerate(matches, 1):
                        print(f"{n}. {c['course']['name']} {c['name']}")
                    course_choice = int(input('选择班级编号：'))
                    if not 1 <= course_choice <= len(matches):
                        raise RuntimeError('班级编号超出范围。')
                    course = matches[course_choice - 1]
                else:
                    course = matches[0]
            else:
                course = choose_course(courses, args.course, args.classroom_id)
            items = lessons(client, course['classroom_id'])
            if args.command == 'lessons':
                print_lessons(items)
                return
            completed = [i for i in items if i.get('is_finished')]
            if args.command == 'wizard':
                print_lessons(completed)
                choice = int(input('选择一节课（回车选最近一节已结束的课）：').strip() or '1')
                if not 1 <= choice <= len(completed):
                    raise RuntimeError('课堂编号超出范围。')
                selected = completed[choice - 1]
            else:
                selected_items = [i for i in completed if (
                    str(i['courseware_id']) == args.lesson_id if args.lesson_id
                    else args.title in i['title'] if args.title else True)]
                if not selected_items or ((args.lesson_id or args.title) and len(selected_items) != 1):
                    raise RuntimeError('没有唯一匹配的已结束课堂，请运行 lessons 查看课堂 ID。')
                selected = selected_items[0]
            export_lesson(client, str(selected['courseware_id']), args.out, args.keep_duplicates)
        finally:
            client.dispose()


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        main()
    except KeyboardInterrupt:
        print('\n已取消；已下载的图片缓存保留。', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f'错误：{exc}', file=sys.stderr)
        sys.exit(1)
