// Native fetch stays in this process and connects directly to OpenRouter.
// Local helper invocations only reserve/settle the persistent task ledger.
if (process.env.PACBENCH_DIRECT === '1' && !globalThis.__pacbenchDirectFetch) {
  globalThis.__pacbenchDirectFetch = true;
  const {execFileSync} = require('node:child_process');
  const fs = require('node:fs');
  const original = globalThis.fetch;
  const stockSourceEffect = process.env.PACBENCH_SUPERMEMORY_STOCK_SOURCE === '1'
    ? require('./supermemory_source_effect.cjs') : null;
  const sender = () => {
    const fields = fs.readFileSync('/proc/self/stat', 'utf8').split(/\) /).pop().trim().split(/\s+/);
    return {pid: process.pid, start_ticks: Number(fields[19]),
      boot_id: fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim(), uid: process.getuid()};
  };
  const helper = (op, value, witness, round) => JSON.parse(execFileSync(process.env.PACBENCH_DIRECT_PYTHON,
    ['-m', 'pacbench.direct_accounting', op], {
      input: JSON.stringify({...value, ...(process.env.PACBENCH_DISPATCH_TRACKING ? {sender: sender()} : {})}), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
      env: {...process.env, PYTHONPATH: process.env.PACBENCH_DIRECT_REPO,
        ...(stockSourceEffect ? {PACBENCH_SUPERMEMORY_EFFECT_WITNESS: JSON.stringify(witness),
          PACBENCH_SUPERMEMORY_ROUND_WITNESS: JSON.stringify(round)} : {})},
      stdio: ['pipe', 'pipe', 'pipe'],
    }));
  globalThis.fetch = async function(input, init) {
    const url = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url);
    if (url.hostname !== 'openrouter.ai') return original(input, init);
    const req = new Request(input, init);
    const marker = req.headers.get('authorization') || '';
    if (!/^Bearer pbdirect-[a-z0-9][a-z0-9_.-]{0,63}$/.test(marker))
      throw new Error('Unaccounted OpenRouter credential refused');
    if (url.origin !== 'https://openrouter.ai' || url.search || url.hash)
      throw new Error('Unexpected direct OpenRouter URL');
    const headers = new Headers(req.headers);
    headers.set('authorization', 'Bearer ' + process.env.PACBENCH_DIRECT_UPSTREAM_KEY);
    if (req.method === 'GET' && url.pathname === '/api/v1/models')
      return original(new Request(req, {headers}));
    if (req.method !== 'POST' || url.pathname !== '/api/v1/chat/completions')
      throw new Error('Unsupported direct model endpoint');
    const project = marker.slice('Bearer pbdirect-'.length);
    let witness = null;
    if (stockSourceEffect) {
      try { witness = stockSourceEffect(); }
      catch { /* The scoped helper records and rejects the missing witness. */ }
    }
    const track = (op, value) => {
      let round = null;
      if (stockSourceEffect && (op === 'reserve' || op === 'dispatch')) {
        try { round = stockSourceEffect.roundWitness(); }
        catch { /* Missing or changed tool-round proof is refused by the scoped helper. */ }
      }
      return helper(op, value, witness, round);
    };
    const receipt = track('reserve', {project, request: JSON.parse(await req.text())});
    headers.delete('content-length');
    if (process.env.PACBENCH_DISPATCH_TRACKING) track('dispatch', {id: receipt.id});
    let response;
    try { response = await original(new Request(req.url, {
      method: 'POST', headers, body: JSON.stringify(receipt.request), signal: req.signal,
      redirect: 'error',
    })); }
    catch (error) { track('settle', {id: receipt.id, status: 0}); throw error; }
    let chunks = [], size = 0, settled = false;
    const diagnosticHeaders = Object.fromEntries([...response.headers].filter(([name]) => /^(x-request-id|x-generation-id|x-openrouter-request-id)$/i.test(name)));
    if (Object.keys(diagnosticHeaders).length)
      track('observe', {id: receipt.id, status: response.status, headers: diagnosticHeaders});
    const decoder = new TextDecoder();
    let pending = '', frame = [], lastObservation = '';
    const observeFrame = () => {
      const text = frame.join('\n'); frame = [];
      if (!text || text.trim() === '[DONE]') return;
      let event; try { event = JSON.parse(text); } catch { return; }
      if (!event || typeof event !== 'object') return;
      const finishes = (Array.isArray(event.choices) ? event.choices : [])
        .filter(c => c && typeof c === 'object').map(c => c.finish_reason).filter(Boolean);
      if (!event.id && !event.usage && !finishes.length) return;
      const signature = JSON.stringify([event.id, event.model, event.provider, event.usage, finishes]);
      if (signature === lastObservation) return;
      // Python parses the untouched numeric lexemes and persists only a
      // sanitized metadata observation. No per-token ledger mutation.
      track('observe', {id: receipt.id, status: response.status, event_json: text});
      lastObservation = signature;
    };
    const observeBytes = value => {
      pending += decoder.decode(value, {stream: true});
      let newline;
      while ((newline = pending.indexOf('\n')) >= 0) {
        const line = pending.slice(0, newline).replace(/\r$/, '');
        pending = pending.slice(newline + 1);
        if (line.startsWith('data:')) frame.push(line.slice(5).replace(/^ /, ''));
        else if (!line) observeFrame();
        else if (!line.startsWith(':') && !line.startsWith('event:') && !line.startsWith('id:') && !line.startsWith('retry:')) {
          // Non-streaming JSON is settled normally at EOF, without changing it.
          pending = line + '\n' + pending; break;
        }
      }
    };
    const finish = (complete = false) => {
      if (!settled) {
        track('settle', {id: receipt.id, status: response.status,
          body_complete: complete,
          headers: Object.fromEntries([...response.headers].filter(([name]) => /^(retry-after|x-request-id|x-generation-id|x-openrouter-request-id|x-ratelimit-)/i.test(name))),
          data: Buffer.concat(chunks).toString('utf8')});
        settled = true;
      }
    };
    if (!response.body) { finish(true); return response; }
    const reader = response.body.getReader();
    const body = new ReadableStream({
      async pull(controller) {
        try {
          const {done, value} = await reader.read();
          if (done) { finish(true); controller.close(); return; }
          size += value.byteLength;
          if (size > 32 * 1024 * 1024) throw new Error('Response accounting limit');
          chunks.push(Buffer.from(value)); observeBytes(value); controller.enqueue(value);
        } catch (error) { finish(); controller.error(error); }
      },
      async cancel(reason) { try { await reader.cancel(reason); } finally { finish(); } },
    });
    return new Response(body, {status: response.status, statusText: response.statusText, headers: response.headers});
  };
}
