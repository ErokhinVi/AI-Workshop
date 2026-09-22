// tools/proxy/worker.js: Cloudflare Worker, прокси <имя>.<поддомен>.workers.dev
// → сервис на Render. Корпоративная сеть режет *.onrender.com, а *.workers.dev
// пропускает. Один скрипт на все сервисы, адрес сервиса в переменной TARGET.
// Разворачивает tools/setup/cf_proxy.py.
//
// UI блоков и табло ходят только к своему origin (retail сам проксирует на
// backend и cib), поэтому прокси на каждый сервис хватает.

export default {
  async fetch(request, env) {
    const incoming = new URL(request.url);
    const target = new URL(incoming.pathname + incoming.search, env.TARGET);
    const headers = new Headers(request.headers);
    headers.delete("host");
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "manual",
    });
    const out = new Headers(upstream.headers);
    const location = out.get("location");
    if (location && location.startsWith(env.TARGET)) {
      out.set("location", incoming.origin + location.slice(env.TARGET.length));
    }
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: out,
    });
  },
};
