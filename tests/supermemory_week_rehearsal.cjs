'use strict';

// Prepended in the private, run-owned bunfig.toml ahead of the existing
// production receipt preload. The real adapter sees the exact pinned URL.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const children = require('node:child_process');
const original = globalThis.fetch;
const originalExec = children.execFileSync;
const origin = new URL(process.env.SM_OFFLINE_MODEL_ORIGIN);
if (origin.hostname !== '127.0.0.1') throw new Error('Expected the approved local model fixture');
const loopback = new Set(['127.0.0.1', 'localhost', '[::1]']);
const root = path.dirname(process.env.PACBENCH_RECEIPT_JOURNAL);
const observerPath = path.join(process.env.PACBENCH_DIRECT_REPO, 'pacbench/direct_runtime/supermemory_source_effect.cjs');

// Detect precisely which read reached the unmodified native admission guard.
children.execFileSync = function(executable, args, options) {
  if (args?.[0] !== '-m' || args?.[1] !== 'pacbench.direct_accounting' || args?.[2] !== 'reserve')
    return originalExec.apply(this, arguments);
  const evidence = {
    effect: JSON.parse(options.env.PACBENCH_SUPERMEMORY_EFFECT_WITNESS || 'null'),
    witness: JSON.parse(options.env.PACBENCH_SUPERMEMORY_CHUNK_WITNESS || 'null'),
    round: JSON.parse(options.env.PACBENCH_SUPERMEMORY_ROUND_WITNESS || 'null'),
    time_ms: Date.now(),
  };
  if (evidence.effect) {
    try { evidence.observer = require(observerPath).offlineChunkState(); } catch {}
  }
  try {
    const answer = originalExec.apply(this, arguments);
    fs.appendFileSync(process.env.SM_OFFLINE_NATIVE_TRACE+'.admission', JSON.stringify({
      ...evidence, accepted: true,
    })+'\n', {mode: 0o600});
    return answer;
  } catch (error) {
    const last = (String(error.stderr || '').trim().split('\n').at(-1) || '');
    const reason = /^[a-z_.]+\.[A-Z][A-Za-z]+: ([A-Za-z0-9 .,;'-]{1,170})$/.exec(last)?.[1];
    fs.appendFileSync(process.env.SM_OFFLINE_NATIVE_TRACE+'.admission', JSON.stringify({
      ...evidence, accepted: false, reason: reason || 'unclassified native admission failure',
    })+'\n', {mode: 0o600});
    throw error;
  }
};

globalThis.fetch = async function(input, init) {
  const requested = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url);
  if (loopback.has(requested.hostname))
    return original(input, init);
  if (requested.origin !== 'https://openrouter.ai')
    throw new Error('External fetch blocked by offline Supermemory fixture');
  if (!['/api/v1/models','/api/v1/chat/completions'].includes(requested.pathname))
    throw new Error('Unexpected model operation in offline Supermemory fixture');

  const request = new Request(input, init);
  const headers = new Headers(request.headers);
  if (headers.get('authorization') !== 'Bearer supermemory-offline-fixture')
    throw new Error('Only the dummy offline credential is allowed');
  headers.delete('authorization');
  headers.delete('content-length');
  headers.set('x-offline-fixture-sender', 'stock-server');
  let body;
  if (request.method === 'POST') {
    body = await request.text();
    const observe = require(observerPath);
    const effect = observe();
    if (!effect) throw new Error('Offline model transport lacks its stock worker effect');
    const round = observe.roundWitness();
    const witness = observe.chunkWitness();
    const source = fs.existsSync(path.join(root, 'source-membership', effect.documentId + '.json'));
    headers.set('x-offline-native-role', source ? 'source' : 'capture');
    const revision = path.join(root, 'capture-membership', effect.documentId, '5.json');
    if (!source && fs.existsSync(revision) && JSON.parse(fs.readFileSync(revision, 'utf8')).question_id === 'withdrawal')
      headers.set('x-offline-target-capture', 'five-revisions');
    fs.appendFileSync(process.env.SM_OFFLINE_NATIVE_TRACE, JSON.stringify({
      effect, source, round, witness, time_ms: Date.now(),
      request_sha256: crypto.createHash('sha256').update(body).digest('hex'),
    }) + '\n', {mode: 0o600});
  }
  return original(new Request(new URL(requested.pathname, origin).toString(), {
    method: request.method, headers, body, redirect: 'error', signal: request.signal,
  }));
};
