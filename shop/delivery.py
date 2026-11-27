"""Packing proof (photos/video of the exact item before dispatch) and the delivery PIN."""
import os
import secrets
import uuid

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired
from werkzeug.utils import secure_filename

from shop.extensions import token_serializer
from shop.security import is_real_image

VIDEO_EXTENSIONS = {'mp4', 'mov', 'm4v', '3gp', 'webm'}
PHOTO_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_PROOF_FILES = 10
MAX_VIDEO_MB = 25


def _extension(filename):
    return filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''


def is_real_video(file_storage):
    """MP4/MOV/3GP files have 'ftyp' at byte 4; WebM starts with the EBML header."""
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    return head[4:8] == b'ftyp' or head.startswith(b'\x1a\x45\xdf\xa3')


def is_real_webp(file_storage):
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    return head[:4] == b'RIFF' and head[8:12] == b'WEBP'


def classify_proof_upload(file_storage):
    """'photo', 'video', or None if the file isn't a real photo/video (checked by content, not just the name)."""
    ext = _extension(file_storage.filename or '')
    if ext in PHOTO_EXTENSIONS and (is_real_image(file_storage) or is_real_webp(file_storage)):
        return 'photo'
    if ext in VIDEO_EXTENSIONS and is_real_video(file_storage):
        return 'video'
    return None


def save_proof_file(file_storage):
    """Save into the private proof folder; returns the stored file name."""
    name = f"{uuid.uuid4().hex}_{secure_filename(file_storage.filename)[-60:]}"
    file_storage.save(os.path.join(current_app.config['PROOF_FOLDER'], name))
    return name


def proof_file_path(name):
    # name comes from our own database, but never let it point outside the folder
    folder = os.path.realpath(current_app.config['PROOF_FOLDER'])
    path = os.path.realpath(os.path.join(folder, name))
    return path if path.startswith(folder + os.sep) and os.path.isfile(path) else None


def delete_proof_file(name):
    path = proof_file_path(name)
    if path:
        try:
            os.remove(path)
        except OSError as e:
            current_app.logger.error(f'Could not delete proof file {name}: {e}')


def new_delivery_pin():
    return f'{secrets.randbelow(10000):04d}'


def rider_token(order_id):
    return token_serializer().dumps({'o': order_id}, salt='rider-link')


def order_from_rider_token(token):
    """Order id from a rider link, or None if the link is invalid or older than RIDER_LINK_DAYS."""
    try:
        data = token_serializer().loads(token, salt='rider-link', max_age=current_app.config['RIDER_LINK_DAYS'] * 86400)
        return int(data['o'])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None
