"""Exact data-plane capability admission, independent of account setup access."""
import json
from pathlib import Path

PLAN = json.loads(Path(__file__).with_name('access-plan.json').read_text())


def desired(role, bucket_id):
    if role not in ('writer', 'verifier') or not bucket_id:
        raise ValueError('unknown_role_or_bucket')
    return {'capabilities': PLAN['destination'][role+'_capabilities'],
            'bucketIds': [bucket_id], 'namePrefix': PLAN['destination']['prefix'],
            'validDurationInSeconds': PLAN['destination']['valid_duration_seconds']}


def admit(role, actual, bucket_id):
    expected = desired(role, bucket_id)
    if set(actual['capabilities']) != set(expected['capabilities']):
        raise ValueError('unexpected_capabilities')
    if actual.get('bucketIds') != expected['bucketIds'] or actual.get('namePrefix') != expected['namePrefix']:
        raise ValueError('unexpected_bucket_or_prefix')
    if set(actual['capabilities']) & set(PLAN['destination']['forbidden_runtime_capabilities']):
        raise ValueError('forbidden_runtime_capability')
    return True
