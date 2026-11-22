'use strict';

// The exact pinned stock batch hands each sealed update to the document
// agent in order. A durable update owns its sends even if Rivet re-enters
// the entire batch with a new async context.
const {AsyncLocalStorage} = require('node:async_hooks');
const {createHash, randomUUID} = require('node:crypto');

const id = /^[A-Za-z0-9_-]{22}$/;
const stockManifestFrame = 'at try (/$bunfs/root/supermemory-server:2999:9460)';
const contexts = new Map();
const originalRun = AsyncLocalStorage.prototype.run;
const originalMap = Array.prototype.map;
const originalLog = console.log;
const observeRounds = process.env.PACBENCH_SUPERMEMORY_STOCK_ROUNDS === '1';
const observeChunks = process.env.PACBENCH_SUPERMEMORY_STOCK_CHUNKS === '1';
const stockFrame = '/$bunfs/root/supermemory-server:';
const sha = value => createHash('sha256').update(value).digest('hex');

function chunkRead(rows) {
  const chunks = originalMap.call(rows, ({id: chunkId, position, content}) => {
    if (!id.test(chunkId) || !Number.isInteger(position) || position < 0 || typeof content !== 'string')
      throw new Error('Invalid Supermemory chunk result');
    return {id: chunkId, position, sha256: sha(content)};
  });
  if (chunks.some((chunk, i) => i > 0 && chunk.position <= chunks[i - 1].position))
    throw new Error('Supermemory chunk positions are ambiguous');
  return {at_ms: Date.now(), chunks};
}

function chunkArgs(input) {
  if (!input || Object.getPrototypeOf(input) !== Object.prototype ||
      Object.keys(input).some(key => !['positions', 'start', 'count'].includes(key)) ||
      (input.positions !== undefined && (!Array.isArray(input.positions) || input.positions.length > 20 ||
        input.positions.some(position => !Number.isSafeInteger(position) || position < 0))) ||
      (input.start !== undefined && (!Number.isSafeInteger(input.start) || input.start < 0)) ||
      (input.count !== undefined && (!Number.isInteger(input.count) || input.count < 1 || input.count > 20)))
    return null;
  return JSON.parse(JSON.stringify(input));
}

AsyncLocalStorage.prototype.run = function(value, ...args) {
  if (value && Object.getPrototypeOf(value) === Object.prototype && !Reflect.ownKeys(value).length) {
    const stack = String(new Error().stack);
    if (stack.includes('at b (/$bunfs/root/supermemory-server:2999:22771)') &&
        stack.includes('at executeStep (/$bunfs/root/supermemory-server:2956:64037)')) {
      if (!contexts.has(this)) contexts.set(this, new WeakMap());
      const names = contexts.get(this);
      if (!names.has(value)) names.set(value, {
        members: null,
        next: 0,
        active: null,
        initialRead: null,
        ambiguous: false,
      });
    }
  }
  return originalRun.call(this, value, ...args);
};

function activeContext() {
  const active = [];
  for (const [storage, names] of contexts) {
    const context = names.get(storage.getStore());
    if (context) active.push(context);
  }
  if (active.length > 1) throw new Error('Ambiguous native Supermemory Rivet step');
  return active[0];
}

Array.prototype.map = function(callback, ...args) {
  const first = Array.isArray(this) && Object.getOwnPropertyDescriptor(this, '0')?.value;
  if (typeof first?.updateId === 'string' && typeof first?.documentId === 'string' &&
      typeof callback === 'function' && Function.prototype.toString.call(callback) === '(p)=>p.documentId' &&
      String(new Error().stack).includes(stockManifestFrame)) {
    const context = activeContext();
    if (context) {
      const members = [];
      const seen = new Set();
      for (const member of this) {
        if (typeof member?.updateId !== 'string' || !id.test(member.updateId) ||
            typeof member.documentId !== 'string' || !id.test(member.documentId) ||
            seen.has(member.updateId)) {
          context.ambiguous = true;
          break;
        }
        seen.add(member.updateId);
        members.push({update: member.updateId, document: member.documentId});
      }
      if (context.members !== null || context.active !== null) context.ambiguous = true;
      context.members = members;
    }
  }
  const mapped = originalMap.call(this, callback, ...args);
  if (observeChunks && Array.isArray(this) && (!this.length || Number.isInteger(first?.position))
      && typeof callback === 'function') {
    const callbackText = Function.prototype.toString.call(callback);
    const toolRead = callbackText === '($)=>({id:$.id,position:$.position,content:$.content})';
    const preview = callbackText.startsWith('(V)=>`<chunk position=');
    if (toolRead || preview) {
      try {
        const stack = String(new Error().stack);
        const trace = activeContext()?.active?.chunkTrace;
        if (trace && toolRead && stack.includes(`Fv5 (${stockFrame}2999:`)) {
          trace.pending.push({...chunkRead(this), result_sha256: sha(JSON.stringify({chunks: mapped}))});
        } else if (trace && preview && stack.includes(`hv5 (${stockFrame}2993:`)) {
          const expected = trace.opening?.chunks.slice(0, 8);
          trace.preview = trace.preview === null && !!expected && this.length === expected.length && this.every((row, n) =>
            row.position === expected[n].position && sha(row.content) === expected[n].sha256);
        }
      } catch { /* The missing independent projection is refused at admission. */ }
    }
  }
  return mapped;
};

console.log = function(message, ...args) {
  if (typeof message === 'string' && message.startsWith('[Workflow] Document ')) {
    const beginning = /^\[Workflow\] Document ([A-Za-z0-9_-]{22}) starting memory agent \(\d+ chunks\)$/.exec(message);
    const ending = /^\[Workflow\] Document ([A-Za-z0-9_-]{22}) memory agent (completed|failed) \(/.exec(message);
    if (beginning || ending) {
      const context = activeContext();
      if (context && !context.ambiguous) {
        if (beginning && context.active === null && context.members?.[context.next]?.document === beginning[1]) {
          context.active = {...context.members[context.next],
            ...(observeRounds ? {visit: randomUUID(), results: new Map()} : {}),
            ...(observeChunks ? {chunkTrace: {
              opening: context.initialRead, preview: null, prompt: null, pending: [], tools: new Map(),
              arguments: new Map(),
            }} : {})};
          context.initialRead = null;
        } else if (ending && context.active?.document === ending[1]) {
          context.next += 1;
          context.active = null;
        } else {
          context.ambiguous = true;
        }
      }
    }
  }
  return originalLog.call(this, message, ...args);
};

if (observeChunks) {
  const slice = Array.prototype.slice;
  Array.prototype.slice = function(start, end) {
    const sliced = slice.apply(this, arguments);
    if (start === 0 && end === 8 && String(new Error().stack).includes(`(${stockFrame}2999:858)`)) {
      try {
        const context = activeContext();
        if (context?.members?.[context.next] && !context.active) {
          context.initialRead = context.initialRead === null ? chunkRead(this) : false;
        }
      } catch { /* No native chunk snapshot can be asserted. */ }
    }
    return sliced;
  };

  // The final stock Bun template transform returns the exact user prompt,
  // including its document metadata and the independently matched preview.
  const replace = String.prototype.replace;
  const bunEscapes = /\\u(?:\{([\da-fA-F]{1,6})\}|([\da-fA-F]{4}))/g.source;
  String.prototype.replace = function(pattern, replacement) {
    const rendered = replace.apply(this, arguments);
    if (pattern instanceof RegExp && pattern.source === bunEscapes &&
        String(new Error().stack).includes(`hv5 (${stockFrame}2956:`)) {
      try {
        const trace = activeContext()?.active?.chunkTrace;
        if (trace) trace.prompt = trace.preview && trace.prompt === null ? sha(rendered) : false;
      } catch { /* The prompt must be observed before send. */ }
    }
    return rendered;
  };
}

if (observeRounds) {
  // Bundled AI SDK's ei8 collects successful tool outputs into the model
  // transcript before the OpenAI-compatible provider serializes its request.
  // This observes the result independently of the subsequent HTTP payload.
  const originalPush = Array.prototype.push;
  Array.prototype.push = function(...args) {
    const part = args[0];
    const toolResult = part?.type === 'tool-result' && part?.output?.type === 'json';
    const chunkCall = observeChunks && part?.type === 'tool-call' && part.toolName === 'readChunks';
    if (args.length === 1 && (toolResult || chunkCall) &&
        typeof part.toolCallId === 'string' && typeof part.toolName === 'string' &&
        /\bat ei8 \(\/\$bunfs\/root\/supermemory-server:\d+:\d+\)/.test(String(new Error().stack))) {
      try {
        const context = activeContext();
        if (context?.active && !context.ambiguous) {
          const trace = context.active.chunkTrace;
          if (chunkCall && trace) {
            const input = chunkArgs(part.input);
            const earlier = trace.arguments.get(part.toolCallId);
            trace.arguments.set(part.toolCallId,
              earlier !== undefined && JSON.stringify(earlier) !== JSON.stringify(input) ? null : input);
            return originalPush.apply(this, args);
          }
          const rendered = JSON.stringify(part.output.value);
          const value = {name: part.toolName, content_sha256: sha(rendered)};
          const old = context.active.results.get(part.toolCallId);
          if (old && (old.name !== value.name || old.content_sha256 !== value.content_sha256)) {
            context.ambiguous = true;
          } else {
            context.active.results.set(part.toolCallId, value);
            if (!old && trace && part.toolName === 'readChunks') {
              const matching = trace.pending.filter(read => read.result_sha256 === value.content_sha256);
              const input = trace.arguments.get(part.toolCallId);
              if (matching.length === 1 && input) {
                trace.pending.splice(trace.pending.indexOf(matching[0]), 1);
                trace.tools.set(part.toolCallId, {...matching[0], arguments: input});
              }
            }
          }
        }
      } catch { /* Ambiguous or unrenderable evidence cannot authorize the later send. */ }
    }
    return originalPush.apply(this, args);
  };
}

function currentNativeOperation() {
  const context = activeContext();
  if (!context) return null;
  if (context.ambiguous || context.members && !context.active) {
    throw new Error('No unique native Supermemory sealed update at transport');
  }
  if (!context.active) return null;
  const {update, document} = context.active;
  return {updateId: update, documentId: document};
}

module.exports = currentNativeOperation;
module.exports.roundWitness = function roundWitness() {
  const effect = currentNativeOperation();
  if (!effect || !observeRounds) return null;
  const {visit, results} = activeContext().active;
  return {effect, visit, results: Object.fromEntries(results)};
};
module.exports.chunkWitness = function chunkWitness() {
  const effect = currentNativeOperation();
  const active = effect && activeContext().active;
  const trace = active?.chunkTrace;
  if (!trace?.prompt) return null;
  return {effect, visit: active.visit, opening: trace.opening, prompt_sha256: trace.prompt,
    tools: Object.fromEntries(trace.tools)};
};
