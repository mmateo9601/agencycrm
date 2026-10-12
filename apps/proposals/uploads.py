"""Bound asset upload streams before Django can buffer unbounded temporary files."""

import re

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload

from .documents import MAX_ASSET_BYTES


class BoundedAssetUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.bounded = bool(
            request and re.fullmatch(r"/w/[0-9a-fA-F-]+/proposals/assets/", request.path)
        )
        self.total = 0
        self.files = 0
        self.limit = MAX_ASSET_BYTES if self.bounded else settings.FILE_UPLOAD_MAX_MEMORY_SIZE

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.files += 1
        if self.files > 1:
            raise StopUpload(connection_reset=True)

    def receive_data_chunk(self, raw_data, start):
        self.total += len(raw_data)
        if self.total > self.limit:
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
