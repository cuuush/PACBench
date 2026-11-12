// Native fetch stays in this process and connects directly to OpenRouter.
// Local helper invocations only reserve/settle the persistent task ledger.
if (process.env.PACBENCH_DIRECT === '1' && !globalThis.__pacbenchDirectFetch) {
  globalThis.__pacbenchDirectFetch = true;
  const {execFileSync} = require('node:child_process');
  const original = globalThis.fetch;
  const helper = (op, value) => JSON.parse(execFileSync(process.env.PACBENCH_DIRECT_PYTHON,
    ['-m', 'pacbench.direct_accounting', op], {
      input: JSON.stringify(value), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
      env: {...process.env, PYTHONPATH: process.env.PACBENCH_DIRECT_REPO},
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
    const receipt = helper('reserve', {project, request: JSON.parse(await req.text())});
    headers.delete('content-length');
    let response;
    try { response = await original(new Request(req.url, {
      method: 'POST', headers, body: JSON.stringify(receipt.request), signal: req.signal,
      redirect: 'error',
    })); }
    catch (error) { helper('settle', {id: receipt.id, status: 0}); throw error; }
    let chunks = [], size = 0, settled = false;
    const finish = () => {
      if (!settled) {
        helper('settle', {id: receipt.id, status: response.status,
          data: Buffer.concat(chunks).toString('utf8')});
        settled = true;
      }
    };
    if (!response.body) { finish(); return response; }
    const reader = response.body.getReader();
    const body = new ReadableStream({
      async pull(controller) {
        try {
          const {done, value} = await reader.read();
          if (done) { finish(); controller.close(); return; }
          size += value.byteLength;
          if (size > 32 * 1024 * 1024) throw new Error('Response accounting limit');
          chunks.push(Buffer.from(value)); controller.enqueue(value);
        } catch (error) { finish(); controller.error(error); }
      },
      async cancel(reason) { try { await reader.cancel(reason); } finally { finish(); } },
    });
    return new Response(body, {status: response.status, statusText: response.statusText, headers: response.headers});
  };
}
