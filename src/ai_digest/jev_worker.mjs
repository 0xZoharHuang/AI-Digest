// Packaged runtime resource: no development script or Node-version-specific flag.
import { createInterface } from 'node:readline';
import { experimental_evaluate as evaluate } from 'ai';
import { createGateway } from '@ai-sdk/gateway';

if (!process.env.AI_GATEWAY_API_KEY) throw new Error('Gateway credentials unavailable');
const model = createGateway({ apiKey: process.env.AI_GATEWAY_API_KEY }).evaluationModel('typesafe-ai/jev');
for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  if (!line.trim()) continue;
  try {
    const request = JSON.parse(line);
    const bytes = value => Buffer.byteLength(JSON.stringify(value));
    if (bytes(request) > 56000 || bytes(request.state) + Math.max(0, ...Object.values(request.questions).map(bytes)) > 25000) {
      throw new Error('request exceeds validated reading window');
    }
    const result = await evaluate({ model, state: request.state, questions: request.questions,
      maxRetries: 0, abortSignal: AbortSignal.timeout(45000) });
    // JSON line framing must not translate raw U+2028/U+2029 or CR inside source data.
    process.stdout.write(JSON.stringify({ answers: result.answers, usage: result.usage,
      providerMetadata: result.providerMetadata, modelId: result.response.modelId })
      .replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029') + '\n');
  } catch (error) {
    // Error messages can contain source bodies, credentials or headers: return codes only.
    process.stdout.write(JSON.stringify({ error: { name: error?.name ?? 'Error', status: error?.statusCode ?? null } }) + '\n');
  }
}
process.exit(0);
