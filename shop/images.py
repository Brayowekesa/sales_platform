"""Smaller, faster copies of product and promo photos (WebP), made on first request and kept.

Saves a lot of data for customers on mobile bundles. Re-encoding also strips EXIF data
(e.g. the GPS location stored in phone photos).
"""
import hashlib
import os
import warnings

from flask import current_app, request, url_for
from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 40_000_000    # refuse "decompression bomb" images

# name: (max width/height in px, WebP quality)
SIZES = {
    'thumb': (160, 72),
    'card': (480, 78),
    'large': (1000, 82),
    'promo': (800, 78),
    # data-saver versions
    'thumb_lite': (120, 45),
    'card_lite': (260, 45),
    'large_lite': (600, 55),
    'promo_lite': (400, 45),
}
RASTER = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def data_saver_on():
    try:
        return request.cookies.get('data_saver') == '1'
    except RuntimeError:          # outside a request (e.g. emails)
        return False


def sized_url(path, size):
    """URL of a resized copy, or None if this file can't be resized (missing, SVG...)."""
    full = os.path.join(current_app.static_folder, path or '')
    if not path or not path.startswith('uploads/') or os.path.splitext(path)[1].lower() not in RASTER or not os.path.isfile(full):
        return None
    if data_saver_on() and f'{size}_lite' in SIZES:
        size = f'{size}_lite'
    # the file's modification time in the URL lets browsers cache it "forever"
    return url_for('pages.image', size=size, path=path, v=int(os.path.getmtime(full)))


def cached_copy(path, size):
    """Path of the resized WebP copy, creating it if needed. None if the source isn't a valid image.

    Each image has exactly one URL per size: the request must use the file's canonical path
    (no '//', './', '../' variants), so nobody can fill the disk with copies of the same photo.
    Already-resized copies (uploads/_cache/) and non-photo files are refused.
    """
    if size not in SIZES or not path:
        return None
    static = os.path.realpath(current_app.static_folder)
    src = os.path.realpath(os.path.join(static, path))
    if not src.startswith(os.path.join(static, 'uploads') + os.sep) or not os.path.isfile(src):
        return None
    rel = os.path.relpath(src, static).replace(os.sep, '/')
    if path != rel or rel.startswith('uploads/_cache/') or os.path.splitext(rel)[1].lower() not in RASTER:
        return None
    key = hashlib.sha1(f'{rel}:{os.path.getmtime(src)}:{size}'.encode()).hexdigest()
    folder = os.path.join(current_app.config['UPLOAD_FOLDER'], '_cache', size)
    out = os.path.join(folder, key + '.webp')
    if os.path.isfile(out):
        return out
    os.makedirs(folder, exist_ok=True)
    max_px, quality = SIZES[size]
    try:
        with warnings.catch_warnings():
            # Pillow only warns between MAX_IMAGE_PIXELS and twice that: refuse those too
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)               # keep phone photos the right way up
                im = im.convert('RGBA' if im.mode in ('RGBA', 'LA', 'P') else 'RGB')
                im.thumbnail((max_px, max_px), Image.LANCZOS)
                tmp = out + '.tmp'
                im.save(tmp, 'WEBP', quality=quality, method=4)   # re-encoded: no EXIF/GPS data
                os.replace(tmp, out)
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, ValueError) as e:
        current_app.logger.warning(f'Could not resize {rel}: {e}')
        return None
    return out


def save_clean_image(file_storage, path):
    """Save an uploaded photo as a fresh copy made by Pillow instead of the raw upload.

    Fully decoding and re-encoding removes hidden data (camera details, GPS location) and anything
    smuggled after the picture (e.g. a file that only *starts* like a GIF). Raises ValueError if the
    file isn't a picture Pillow can read.
    """
    import warnings
    try:
        file_storage.stream.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(file_storage.stream) as im:
                fmt = (im.format or '').upper()
                frames = getattr(im, 'n_frames', 1)
                if fmt == 'GIF' and frames > 1:
                    im.save(path, 'GIF', save_all=True, optimize=False)        # keep animations
                    return
                im.load()
                im = ImageOps.exif_transpose(im)
                if fmt in ('JPEG', 'MPO'):
                    im.convert('RGB').save(path, 'JPEG', quality=90, optimize=True)
                elif fmt == 'WEBP':
                    im.save(path, 'WEBP', quality=90)
                elif fmt == 'GIF':
                    im.save(path, 'GIF')
                else:
                    im.save(path, 'PNG', optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning, SyntaxError) as e:
        raise ValueError(f'not a readable image: {e}')
