// Claude Desktop speaks newline-delimited MCP on stdio. The local PubPartner
// server uses stateless JSON over HTTP, including 202 for notifications.
const readline = require('node:readline');
const endpoint = 'http://127.0.0.1:8768/mcp';

let protocolVersion;

async function relay(line) {
  let message;
  try {
    message = JSON.parse(line);
    const headers = {'Content-Type': 'application/json', Accept: 'application/json, text/event-stream'};
    if (protocolVersion) headers['MCP-Protocol-Version'] = protocolVersion;
    const response = await fetch(endpoint, {
      method: 'POST',
      redirect: 'error',
      headers,
      body: line,
      signal: AbortSignal.timeout(15000)
    });
    if (!response.ok) throw Error('PubPartner returned HTTP ' + response.status);
    if (response.status !== 202 && message.id !== undefined) {
      const reply = await response.json();
      if (reply.id !== message.id || reply.jsonrpc !== '2.0') throw Error('Unexpected MCP reply');
      if (message.method === 'initialize') protocolVersion = reply.result?.protocolVersion;
      process.stdout.write(JSON.stringify(reply) + '\n');
    }
  } catch {
    if (message && message.id !== undefined) {
      process.stdout.write(JSON.stringify({jsonrpc:'2.0', id:message.id, error:{
        code:-32603, message:'Open PubPartner on this computer and start Local MCP in Connections.'
      }}) + '\n');
    }
  }
}
(async () => {
  for await (const line of readline.createInterface({input:process.stdin})) {
    if (line.trim()) await relay(line);
  }
})();
