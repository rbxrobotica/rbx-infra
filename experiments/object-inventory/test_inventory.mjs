import test from 'node:test';
import assert from 'node:assert/strict';
import { collect, prefixOf, plus, READS } from './inventory.mjs';

function fixture(reply) {
  const calls = [], records = [];
  const sdk = Object.fromEntries([...READS].map(name => [name, class { constructor(input) { this.name = name; this.input = input; } }]));
  const client = { send: async command => { calls.push(command); return reply(command); } };
  return { client, sdk, calls, records, emit: async value => records.push(value), pause: async () => {} };
}

function base(command) {
  if (command.name === 'ListBucketsCommand') return { Buckets: [{ Name: 'fixture-private-bucket' }] };
  if (command.name === 'ListObjectsV2Command') return { Contents: [{ Key: 'blog/posts/fixture.md', Size: 13 }], IsTruncated: false };
  if (command.name === 'ListObjectVersionsCommand') return { Versions: [{ Key: 'blog/posts/fixture.md', Size: 13, IsLatest: true },
    { Key: 'blog/posts/fixture.md', Size: 7, IsLatest: false }], DeleteMarkers: [{ Key: 'old/log' }], IsTruncated: false };
  if (command.name === 'ListMultipartUploadsCommand') return { Uploads: [] };
  return {};
}

test('only explicitly named read operations can be sent', async () => {
  const f = fixture(base); await collect(f);
  assert.ok(f.calls.every(x => READS.has(x.name)));
  assert.equal(f.calls.length, 11);
  assert.equal([...READS].some(x => /Put|Delete|Create|Abort|UploadPart/.test(x)), false);
});

test('versions and current bytes are counted independently; names remain private', async () => {
  const f = fixture(base), r = await collect(f), b = r.buckets[0];
  assert.equal(b.current_bytes, 13); assert.equal(b.all_version_bytes, 20);
  assert.equal(b.noncurrent_bytes, 7); assert.equal(b.delete_markers, 1);
  assert.equal(r.measurement_coverage_complete, true);
  assert.ok(!JSON.stringify(r).includes('fixture-private-bucket'));
  assert.ok(!JSON.stringify(r).includes('fixture.md'));
  assert.ok(JSON.stringify(f.records).includes('fixture.md'));
});

test('pagination fetches every page without silently omitting bytes', async () => {
  const f = fixture(c => c.name === 'ListObjectsV2Command' ?
    (c.input.ContinuationToken ? { Contents: [{ Key: 'other/b', Size: 4 }], IsTruncated: false } :
      { Contents: [{ Key: 'other/a', Size: 3 }], IsTruncated: true, NextContinuationToken: 'next' }) : base(c));
  const r = await collect(f);
  assert.equal(r.buckets[0].current_bytes, 7);
  assert.equal(r.logical_api_calls.ListObjectsV2Command, 2);
  assert.equal(r.measurement_coverage_complete, false);
});

test('repeated/missing tokens produce an incomplete inventory', async () => {
  const f = fixture(c => c.name === 'ListObjectsV2Command' ?
    { Contents: [], IsTruncated: true, NextContinuationToken: 'same' } : base(c));
  const r = await collect(f);
  assert.equal(r.buckets[0].current_listing_complete, false);
  assert.equal(r.measurement_coverage_complete, false);
  assert.equal(r.logical_api_calls.ListObjectsV2Command, 2);
});

test('access denied is a gap, not an empty bucket', async () => {
  const f = fixture(c => { if (c.name === 'ListObjectsV2Command') throw Object.assign(new Error('sensitive-provider-message'),
    { name: 'AccessDenied', $metadata: { httpStatusCode: 403 } }); return base(c); });
  const r = await collect(f);
  assert.equal(r.buckets[0].current_listing_complete, false);
  assert.equal(r.measurement_coverage_complete, false);
  assert.ok(!JSON.stringify(r).includes('sensitive-provider-message'));
});

test('missing bucket enumeration never claims zero resources', async () => {
  const f = fixture(c => c.name === 'ListBucketsCommand' ? {} : base(c));
  await assert.rejects(collect(f), /bucket_list_not_available/);
});

test('prefix grouping is bounded to semantic groups', () => {
  assert.equal(prefixOf('blog/posts/any.md'), 'blog/posts/');
  assert.equal(prefixOf('blog/covers/image.jpg'), 'blog/covers/');
  assert.equal(prefixOf('backups/2026/11/01.gpg'), 'backups/');
  assert.equal(prefixOf('file'), '(root)');
});

test('invalid or overflowing sizes cannot become a false volume', () => {
  assert.throws(() => plus(1, -1), /unsafe_size/);
  assert.throws(() => plus(Number.MAX_SAFE_INTEGER, 1), /unsafe_size/);
});

test('request bounds stop repeated enumeration', async () => {
  const f = fixture(base);
  const r = await collect({ ...f, maxPages: 3 });
  assert.equal(r.measurement_coverage_complete, false);
  assert.equal(f.calls.length, 3);
});
