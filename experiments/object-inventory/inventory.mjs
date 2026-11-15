#!/usr/bin/env node
// Read-only metadata inventory. Never read object bodies or mutate cloud state.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const READS = new Set(['ListBucketsCommand', 'ListObjectsV2Command', 'ListObjectVersionsCommand',
  'GetBucketVersioningCommand', 'GetBucketLifecycleConfigurationCommand', 'GetBucketEncryptionCommand',
  'GetObjectLockConfigurationCommand', 'GetBucketAclCommand', 'GetBucketPolicyStatusCommand',
  'GetBucketPolicyCommand', 'ListMultipartUploadsCommand']);

export function prefixOf(key) {
  if (typeof key !== 'string') throw new Error('invalid_key');
  if (key.startsWith('blog/posts/')) return 'blog/posts/';
  if (key.startsWith('blog/covers/')) return 'blog/covers/';
  return key.includes('/') ? key.slice(0, key.indexOf('/') + 1) : '(root)';
}

export function plus(a, b) {
  if (!Number.isSafeInteger(b) || b < 0 || !Number.isSafeInteger(a + b)) throw new Error('unsafe_size');
  return a + b;
}

function safeError(error) {
  const name = /^[A-Za-z][A-Za-z0-9_]{0,70}$/.test(error?.name || '') ? error.name : 'read_failed';
  return { error: name, http_status: error?.$metadata?.httpStatusCode || null };
}

export async function collect({ client, sdk, emit, maxPages = 10000, pause = async () => {} }) {
  let pages = 0;
  const calls = {};
  async function read(name, args) {
    if (!READS.has(name)) throw new Error('write_operation_refused');
    if (++pages > maxPages) throw new Error('request_bound_reached');
    calls[name] = (calls[name] || 0) + 1;
    await pause();
    return client.send(new sdk[name](args));
  }
  const buckets = [];
  let continuation;
  const bucketTokens = new Set();
  do {
    const result = await read('ListBucketsCommand', continuation ? { ContinuationToken: continuation } : {});
    if (!Array.isArray(result.Buckets)) throw new Error('bucket_list_not_available');
    buckets.push(...result.Buckets);
    continuation = result.ContinuationToken;
    if (continuation && bucketTokens.has(continuation)) throw new Error('bucket_pagination_loop');
    if (continuation) bucketTokens.add(continuation);
  } while (continuation);
  const reports = [];
  for (const bucket of buckets.sort((a, b) => a.Name.localeCompare(b.Name))) {
    const ref = `C${String(reports.length + 1).padStart(3, '0')}`;
    const groups = new Map();
    const row = { bucket_ref: ref, current_objects: 0, current_bytes: 0, all_versions: 0,
      all_version_bytes: 0, noncurrent_versions: 0, noncurrent_bytes: 0, delete_markers: 0,
      latest_live_versions: 0, latest_live_version_bytes: 0,
      current_listing_complete: false, version_listing_complete: false, unfinished_uploads: 0,
      multipart_listing_complete: false, controls: {}, groups: [], findings: [] };
    const group = key => {
      const prefix = prefixOf(key);
      if (!groups.has(prefix)) {
        if (groups.size >= 5000) throw new Error('prefix_bound_reached');
        groups.set(prefix, { prefix, current_objects: 0, current_bytes: 0, all_versions: 0,
          all_version_bytes: 0, noncurrent_versions: 0, noncurrent_bytes: 0, delete_markers: 0,
          oldest_current: null, newest_current: null });
      }
      return groups.get(prefix);
    };
    await emit({ type: 'bucket', bucket_ref: ref, name: bucket.Name, created: bucket.CreationDate });
    for (const [label, command] of [['versioning', 'GetBucketVersioningCommand'],
      ['lifecycle', 'GetBucketLifecycleConfigurationCommand'], ['encryption', 'GetBucketEncryptionCommand'],
      ['object_lock', 'GetObjectLockConfigurationCommand'], ['acl', 'GetBucketAclCommand'],
      ['policy_status', 'GetBucketPolicyStatusCommand'], ['policy', 'GetBucketPolicyCommand']]) {
      try {
        const result = await read(command, { Bucket: bucket.Name });
        const { $metadata, ...value } = result;
        await emit({ type: 'control', bucket_ref: ref, control: label, value });
        row.controls[label] = label === 'versioning' ? { readable: true, status: result.Status || 'not_enabled' } :
          label === 'lifecycle' ? { readable: true, rules: result.Rules?.length || 0,
            enabled_expiration_rules: (result.Rules || []).filter(x => x.Status === 'Enabled' &&
              (x.Expiration || x.NoncurrentVersionExpiration)).length } :
          label === 'object_lock' ? { readable: true, enabled: result.ObjectLockConfiguration?.ObjectLockEnabled === 'Enabled',
            default_retention: result.ObjectLockConfiguration?.Rule?.DefaultRetention || null } :
          label === 'policy_status' ? { readable: true, is_public: result.PolicyStatus?.IsPublic ?? null } :
          label === 'acl' ? { readable: true, public_grants: (result.Grants || []).filter(x =>
            /AllUsers|AuthenticatedUsers/.test(x.Grantee?.URI || '')).length } : { readable: true };
      } catch (error) { row.controls[label] = { readable: false, ...safeError(error) }; }
    }
    try {
      let token;
      const seen = new Set();
      do {
        const result = await read('ListObjectsV2Command', { Bucket: bucket.Name, MaxKeys: 1000,
          ...(token ? { ContinuationToken: token } : {}) });
        for (const obj of result.Contents || []) {
          const g = group(obj.Key);
          row.current_objects++; row.current_bytes = plus(row.current_bytes, obj.Size);
          g.current_objects++; g.current_bytes = plus(g.current_bytes, obj.Size);
          const modified = obj.LastModified?.toISOString() || null;
          if (modified) {
            if (!g.oldest_current || modified < g.oldest_current) g.oldest_current = modified;
            if (!g.newest_current || modified > g.newest_current) g.newest_current = modified;
          }
          await emit({ type: 'current', bucket_ref: ref, ...obj });
        }
        if (result.IsTruncated && (!result.NextContinuationToken || seen.has(result.NextContinuationToken)))
          throw new Error('object_pagination_incomplete');
        token = result.IsTruncated ? result.NextContinuationToken : undefined;
        if (token) seen.add(token);
      } while (token);
      row.current_listing_complete = true;
    } catch (error) { row.findings.push({ scope: 'current_listing', ...safeError(error) }); }
    try {
      let keyMarker, versionMarker;
      const seen = new Set();
      do {
        const result = await read('ListObjectVersionsCommand', { Bucket: bucket.Name, MaxKeys: 1000,
          ...(keyMarker ? { KeyMarker: keyMarker } : {}), ...(versionMarker ? { VersionIdMarker: versionMarker } : {}) });
        for (const obj of result.Versions || []) {
          const g = group(obj.Key);
          row.all_versions++; row.all_version_bytes = plus(row.all_version_bytes, obj.Size);
          g.all_versions++; g.all_version_bytes = plus(g.all_version_bytes, obj.Size);
          if (obj.IsLatest) { row.latest_live_versions++; row.latest_live_version_bytes = plus(row.latest_live_version_bytes, obj.Size); }
          if (!obj.IsLatest) {
            row.noncurrent_versions++; row.noncurrent_bytes = plus(row.noncurrent_bytes, obj.Size);
            g.noncurrent_versions++; g.noncurrent_bytes = plus(g.noncurrent_bytes, obj.Size);
          }
          await emit({ type: 'version', bucket_ref: ref, ...obj });
        }
        for (const obj of result.DeleteMarkers || []) {
          row.delete_markers++; group(obj.Key).delete_markers++;
          await emit({ type: 'delete_marker', bucket_ref: ref, ...obj });
        }
        const next = JSON.stringify([result.NextKeyMarker, result.NextVersionIdMarker]);
        if (result.IsTruncated && (!result.NextKeyMarker || seen.has(next))) throw new Error('version_pagination_incomplete');
        keyMarker = result.IsTruncated ? result.NextKeyMarker : undefined;
        versionMarker = result.IsTruncated ? result.NextVersionIdMarker : undefined;
        if (keyMarker) seen.add(next);
      } while (keyMarker);
      row.version_listing_complete = true;
    } catch (error) { row.findings.push({ scope: 'version_listing', ...safeError(error) }); }
    try {
      let keyMarker, uploadMarker;
      const seen = new Set();
      do {
        const result = await read('ListMultipartUploadsCommand', { Bucket: bucket.Name, MaxUploads: 1000,
          ...(keyMarker ? { KeyMarker: keyMarker } : {}), ...(uploadMarker ? { UploadIdMarker: uploadMarker } : {}) });
        row.unfinished_uploads += (result.Uploads || []).length;
        for (const upload of result.Uploads || []) await emit({ type: 'unfinished_upload', bucket_ref: ref, ...upload });
        const next = JSON.stringify([result.NextKeyMarker, result.NextUploadIdMarker]);
        if (result.IsTruncated && (!result.NextKeyMarker || seen.has(next))) throw new Error('multipart_pagination_incomplete');
        keyMarker = result.IsTruncated ? result.NextKeyMarker : undefined;
        uploadMarker = result.IsTruncated ? result.NextUploadIdMarker : undefined;
        if (keyMarker) seen.add(next);
      } while (keyMarker);
      row.multipart_listing_complete = true;
    } catch (error) { row.findings.push({ scope: 'multipart_listing', ...safeError(error) }); }
    let i = 0;
    row.listing_counters_agree = row.current_listing_complete && row.version_listing_complete &&
      row.current_objects === row.latest_live_versions && row.current_bytes === row.latest_live_version_bytes;
    for (const g of [...groups.values()].sort((a, b) => a.prefix.localeCompare(b.prefix))) {
      const groupRef = `${ref}P${String(++i).padStart(3, '0')}`;
      await emit({ type: 'group', bucket_ref: ref, group_ref: groupRef, ...g });
      const { prefix, ...publicGroup } = g;
      row.groups.push({ group_ref: groupRef, ...publicGroup });
    }
    await emit({ type: 'bucket_summary', name: bucket.Name, ...row });
    reports.push(row);
  }
  return { bucket_listing_complete: true, measurement_coverage_complete: reports.every(x =>
      x.current_listing_complete && x.version_listing_complete && x.multipart_listing_complete && x.listing_counters_agree),
    buckets: reports, logical_api_calls: calls,
    logical_api_call_total: pages, atomic_snapshot: false, object_bodies_read: false,
    provider_configuration_changed: false, objects_deleted: 0 };
}

async function main() {
  const base = process.argv[2];
  if (!base || !path.isAbsolute(base)) throw new Error('private_output_directory_required');
  fs.mkdirSync(base, { recursive: true, mode: 0o700 });
  const info = fs.lstatSync(base);
  if (!info.isDirectory() || (info.mode & 0o777) !== 0o700 || info.uid !== process.getuid()) throw new Error('private_owned_directory_required');
  const recipient = JSON.parse(fs.readFileSync('/home/psyctl/.cache/rbx-validation/dns-state-custody-20261115/recipient-private-metadata.json'));
  if (!/^[0-9A-F]{40,64}$/.test(recipient.fingerprint)) throw new Error('invalid_recipient');
  const secret = ref => {
    const p = spawnSync('pass', ['show', ref], { encoding: 'utf8', timeout: 20000 });
    if (p.status || !p.stdout.trim()) throw new Error('credential_unavailable');
    return p.stdout.split('\n')[0].trim();
  };
  const sdkRequire = createRequire('/home/psyctl/rbx/rbx-systems-frontend/package.json');
  const sdk = sdkRequire('@aws-sdk/client-s3');
  const transportRequire = createRequire(sdkRequire.resolve('@aws-sdk/client-s3'));
  const { NodeHttpHandler } = transportRequire('@smithy/node-http-handler');
  const client = new sdk.S3Client({ endpoint: 'https://eu2.contabostorage.com', region: 'default',
    requestHandler: new NodeHttpHandler({ connectionTimeout: 5000, requestTimeout: 20000 }),
    forcePathStyle: true, maxAttempts: 3, credentials: { accessKeyId: secret('rbx/s3/access-key'),
      secretAccessKey: secret('rbx/s3/secret-key') } });
  const encryptedPath = path.join(base, 'contabo-inventory.ndjson.gpg');
  const fd = fs.openSync(encryptedPath, 'wx', 0o600);
  const gpg = spawn('/usr/bin/gpg', ['--batch', '--no-auto-key-retrieve', '--recipient', recipient.fingerprint,
    '--encrypt', '--output', '-'], { stdio: ['pipe', fd, 'pipe'] });
  fs.closeSync(fd);
  let gpgError = false, bytes = 0;
  gpg.stderr.on('data', () => {});
  gpg.on('error', () => { gpgError = true; });
  const gpgClosed = new Promise(resolve => gpg.on('close', code => resolve(code)));
  const emit = async value => {
    const raw = JSON.stringify(value) + '\n';
    bytes += Buffer.byteLength(raw);
    if (bytes > 100 * 1024 * 1024) throw new Error('manifest_bound_reached');
    if (!gpg.stdin.write(raw)) await once(gpg.stdin, 'drain');
  };
  let report;
  const started = new Date().toISOString();
  try {
    await emit({ type: 'capture_start', started, scope: 'Contabo EU credential-visible buckets',
      implementation_sha256: crypto.createHash('sha256').update(fs.readFileSync(fileURLToPath(import.meta.url))).digest('hex'),
      sdk_version: sdkRequire('@aws-sdk/client-s3/package.json').version });
    report = { completed: true, ...await collect({ client, sdk, emit,
      pause: () => new Promise(resolve => setTimeout(resolve, 80)) }) };
  } catch (error) { report = { completed: false, ...safeError(error) }; }
  finally { client.destroy(); }
  report.started_utc = started; report.finished_utc = new Date().toISOString();
  report.scope = 'Contabo EU buckets visible to the existing credential; other regions/accounts not inferred';
  report.growth_rate_measured = false; report.payload_integrity_verified = false;
  await emit({ type: 'capture_end', ...report });
  gpg.stdin.end();
  if (await gpgClosed || gpgError) throw new Error('encrypted_inventory_failed');
  report.encrypted_manifest_bytes = fs.statSync(encryptedPath).size;
  report.encrypted_manifest_sha256 = crypto.createHash('sha256').update(fs.readFileSync(encryptedPath)).digest('hex');
  fs.writeFileSync(path.join(base, 'aggregate.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  console.log(JSON.stringify(report));
  if (!report.completed) process.exitCode = 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.log(JSON.stringify({ completed: false, ...safeError(error), details_withheld: true })); process.exitCode = 1; });
}
