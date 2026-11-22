'use strict';

// The exact pinned stock batch hands each sealed update to the document
// agent in order. A durable update owns its sends even if Rivet re-enters
// the entire batch with a new async context.
const {AsyncLocalStorage} = require('node:async_hooks');

const id = /^[A-Za-z0-9_-]{22}$/;
const stockManifestFrame = 'at try (/$bunfs/root/supermemory-server:2999:9460)';
const contexts = new Map();
const originalRun = AsyncLocalStorage.prototype.run;
const originalMap = Array.prototype.map;
const originalLog = console.log;

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
  return originalMap.call(this, callback, ...args);
};

console.log = function(message, ...args) {
  if (typeof message === 'string' && message.startsWith('[Workflow] Document ')) {
    const beginning = /^\[Workflow\] Document ([A-Za-z0-9_-]{22}) starting memory agent \(\d+ chunks\)$/.exec(message);
    const ending = /^\[Workflow\] Document ([A-Za-z0-9_-]{22}) memory agent (completed|failed) \(/.exec(message);
    if (beginning || ending) {
      const context = activeContext();
      if (context && !context.ambiguous) {
        if (beginning && context.active === null && context.members?.[context.next]?.document === beginning[1]) {
          context.active = context.members[context.next];
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

module.exports = function currentNativeOperation() {
  const context = activeContext();
  if (!context) return null;
  if (context.ambiguous || context.members && !context.active) {
    throw new Error('No unique native Supermemory sealed update at transport');
  }
  if (!context.active) return null;
  const {update, document} = context.active;
  return {updateId: update, documentId: document};
};
