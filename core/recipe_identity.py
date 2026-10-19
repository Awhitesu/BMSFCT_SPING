"""Stable identities, independent of editable display names."""
import uuid


def ensure_step_ids(data):
    items = data.get('items', []) if isinstance(data, dict) else data
    seen = set()
    for item in items:
        for node in (item, *item.get('sub_steps', [])):
            identity = node.get('step_id')
            if not isinstance(identity, str) or not identity or identity in seen:
                identity = uuid.uuid4().hex
                node['step_id'] = identity
            seen.add(identity)
    return data
