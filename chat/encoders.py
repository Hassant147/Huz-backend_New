# encoders.py
import json
from uuid import UUID
from datetime import datetime


class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def json_serializer(data):
    return json.dumps(data, cls=UUIDEncoder)