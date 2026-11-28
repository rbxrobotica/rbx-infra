// Compares, and on request publishes, the pages of the institutional site in
// the content store (bucket rbx-content, keys site/<locale>/<key>/index.md).
//
// It runs inside the cluster, in the image of the site, so the credentials of
// the store never leave it. MODE=compare reads only. MODE=publish first copies
// each live object to site-backup/<BACKUP_STAMP>/ and then replaces it, and it
// writes nothing unless every page is in a state it knows.
//
// Inputs, mounted as files named <locale>__<key with / as __>.md:
//   CANDIDATES_DIR  the reviewed pages (rbx-systems-frontend site-content/)
//   CAPTURE_DIR     the pages as the site served them on 2026-11-07, read from
//                   the caches of the pods: exact frontmatter, rendered HTML body
//
// State of a page:
//   candidate  the live object is the candidate, byte for byte: nothing to do
//   capture    the live object still says what the capture says: safe to replace
//   new        no live object and no capture: the candidate would be created
//   drift      the live object matches neither: someone changed it after the
//              capture, and replacing it would lose that change
//   missing    the capture has the page and the store does not
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { S3Client, GetObjectCommand, PutObjectCommand } from '@aws-sdk/client-s3';
import { load as parseYaml } from 'js-yaml';
import { marked } from 'marked';

const bucket = process.env.CONTENT_BUCKET ?? 'rbx-content';
const mode = process.env.MODE ?? 'compare';
const stamp = process.env.BACKUP_STAMP ?? '';
const allowDrift = new Set((process.env.ALLOW_DRIFT ?? '').split(',').filter(Boolean));
const timeout = () => ({ abortSignal: AbortSignal.timeout(15000) });

const sha256 = (body) => createHash('sha256').update(body).digest('hex');
const log = (event, fields = {}) => console.log(JSON.stringify({ event, ...fields }));

// GET returns quoted ETags, and this store compares the bare token on PUT: a
// quoted If-Match is refused with PreconditionFailed (rbx-systems-frontend
// docs/runbooks/SITE-CONTENT-PUBLICATION.md, and the first run of this Job).
function conditionalETag(etag) {
  if (typeof etag !== 'string') throw new Error('InvalidETag');
  const value = etag.startsWith('"') && etag.endsWith('"') ? etag.slice(1, -1) : etag;
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._:-]*$/.test(value)) throw new Error('InvalidETag');
  return value;
}

const keyOf = (file) => `site/${file.replace(/\.md$/, '').split('__').join('/')}/index.md`;

function readDir(directory) {
  const pages = new Map();
  for (const file of readdirSync(directory).filter((name) => name.endsWith('.md')).sort())
    pages.set(keyOf(file), readFileSync(path.join(directory, file)));
  return pages;
}

/** Frontmatter and body, as the Content Gateway of the site splits them. */
function split(body) {
  const text = body.toString('utf8');
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
  if (!match) return { meta: {}, content: text };
  return { meta: parseYaml(match[1]) ?? {}, content: match[2] };
}

/**
 * What a page says, for comparison: its frontmatter and its rendered HTML,
 * without the addresses of its links (the site rewrites them when it renders)
 * and without the white space between tags.
 */
function said(body) {
  const { meta, content } = split(body);
  const html = String(marked.parse(content))
    .replace(/\s(href|src)="[^"]*"/g, ' $1=""')
    .replace(/>\s+</g, '><')
    .replace(/\s+/g, ' ')
    .trim();
  return JSON.stringify({ meta, html });
}

async function getObject(client, key) {
  try {
    const response = await client.send(
      new GetObjectCommand({ Bucket: bucket, Key: key }),
      timeout()
    );
    const body = Buffer.from(await response.Body.transformToByteArray());
    return {
      body,
      etag: response.ETag,
      contentType: response.ContentType,
      cacheControl: response.CacheControl,
      lastModified: response.LastModified?.toISOString()
    };
  } catch (error) {
    if (error?.name === 'NoSuchKey' || error?.$metadata?.httpStatusCode === 404) return null;
    throw error;
  }
}

function stateOf(live, candidate, capture) {
  if (!live) return capture ? 'missing' : 'new';
  if (candidate && sha256(live.body) === sha256(candidate)) return 'candidate';
  if (capture && said(live.body) === said(capture)) return 'capture';
  return 'drift';
}

async function main() {
  if (!['compare', 'publish'].includes(mode)) throw new Error(`Unknown MODE ${mode}`);
  if (mode === 'publish' && !/^[0-9]{4}-[0-9]{2}-[0-9]{2}(-[a-z0-9]+)?$/.test(stamp))
    throw new Error('MODE=publish needs BACKUP_STAMP, for example 2026-11-27');

  const candidates = readDir(process.env.CANDIDATES_DIR);
  const captures = readDir(process.env.CAPTURE_DIR);
  const client = new S3Client({
    endpoint: process.env.CONTABO_S3_ENDPOINT,
    region: process.env.CONTABO_S3_REGION ?? 'default',
    forcePathStyle: true,
    credentials: {
      accessKeyId: process.env.CONTABO_S3_ACCESS_KEY,
      secretAccessKey: process.env.CONTABO_S3_SECRET_KEY
    }
  });

  const keys = [...new Set([...candidates.keys(), ...captures.keys()])].sort();
  const pages = [];
  for (const key of keys) {
    const candidate = candidates.get(key);
    const capture = captures.get(key);
    const live = await getObject(client, key);
    const state = stateOf(live, candidate, capture);
    pages.push({ key, candidate, live, state });
    log('page', {
      key,
      state,
      hasCandidate: Boolean(candidate),
      liveBytes: live?.body.length,
      liveSha256: live ? sha256(live.body) : undefined,
      liveLastModified: live?.lastModified,
      liveContentType: live?.contentType,
      candidateSha256: candidate ? sha256(candidate) : undefined,
      liveTitle: live ? String(split(live.body).meta.title ?? '') : undefined
    });
  }

  const count = (state) => pages.filter((page) => page.state === state).length;
  const summary = {
    mode,
    pages: pages.length,
    candidate: count('candidate'),
    capture: count('capture'),
    new: count('new'),
    drift: count('drift'),
    missing: count('missing'),
    withoutCandidate: pages.filter((page) => !page.candidate).map((page) => page.key)
  };
  log('summary', summary);
  if (mode === 'compare') return;

  const toWrite = pages.filter((page) => page.candidate && page.state !== 'candidate');
  const blocked = toWrite.filter(
    (page) => page.state === 'missing' || (page.state === 'drift' && !allowDrift.has(page.key))
  );
  if (blocked.length > 0) {
    log('publish_refused', { keys: blocked.map((page) => `${page.state}:${page.key}`) });
    throw new Error('LiveContentNotAsExpected');
  }

  // Every backup is written and read back before the first page is replaced.
  for (const page of toWrite.filter((entry) => entry.live)) {
    const backupKey = `site-backup/${stamp}/${page.key}`;
    const existing = await getObject(client, backupKey);
    if (existing && sha256(existing.body) !== sha256(page.live.body))
      throw new Error(`BackupExistsWithOtherContent ${backupKey}`);
    if (!existing) {
      await client.send(
        new PutObjectCommand({
          Bucket: bucket,
          Key: backupKey,
          Body: page.live.body,
          ContentType: page.live.contentType ?? 'text/markdown'
        }),
        timeout()
      );
      const written = await getObject(client, backupKey);
      if (!written || sha256(written.body) !== sha256(page.live.body))
        throw new Error(`BackupNotReadBack ${backupKey}`);
    }
    log('backup_ready', { key: page.key, backupKey, sha256: sha256(page.live.body) });
  }

  for (const page of toWrite) {
    log('object_write_attempt', { key: page.key, sha256: sha256(page.candidate) });
    await client.send(
      new PutObjectCommand({
        Bucket: bucket,
        Key: page.key,
        Body: page.candidate,
        ContentType: page.live?.contentType ?? 'text/markdown',
        ...(page.live?.cacheControl ? { CacheControl: page.live.cacheControl } : {}),
        // The object is replaced only if it is still the one that was read.
        ...(page.live ? { IfMatch: conditionalETag(page.live.etag) } : { IfNoneMatch: '*' })
      }),
      timeout()
    );
    const written = await getObject(client, page.key);
    if (!written || sha256(written.body) !== sha256(page.candidate))
      throw new Error(`WriteNotReadBack ${page.key}`);
    log('object_written', { key: page.key, sha256: sha256(page.candidate) });
  }
  log('published', { written: toWrite.length, backupPrefix: `site-backup/${stamp}/` });
}

main().catch((error) => {
  log('failed', {
    error: error?.name,
    status: error?.$metadata?.httpStatusCode,
    message: String(error?.message ?? error).slice(0, 300)
  });
  process.exitCode = 1;
});
