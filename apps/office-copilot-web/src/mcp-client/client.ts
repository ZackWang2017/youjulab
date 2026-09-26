export async function api(path: string, body?: unknown) {
  const response = await fetch(`/backend${path}`, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  let data;
  try { data = await response.json(); } catch { throw new Error('无法连接 MCP 服务，请检查服务是否启动。'); }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
