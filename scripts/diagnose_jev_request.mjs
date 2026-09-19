// Isolated diagnostic only. Read one cached request from stdin; never log credentials.
import { experimental_evaluate as evaluate } from 'ai';
import { createGateway } from '@ai-sdk/gateway';
let input = '';
for await (const part of process.stdin) input += part;
const request = JSON.parse(input);
const model = createGateway({ apiKey: process.env.AI_GATEWAY_API_KEY }).evaluationModel('typesafe-ai/jev');
try {
  const result = await evaluate({ model, ...request, maxRetries: 0, abortSignal: AbortSignal.timeout(45000) });
  console.log(JSON.stringify({ status: 'success', usage: result.usage, questions: Object.keys(result.answers).length,
    gateway: result.providerMetadata?.gateway }));
} catch (error) {
  const safe = value => String(value ?? '').replaceAll(process.env.AI_GATEWAY_API_KEY, '[credential]').slice(0, 1600);
  console.log(JSON.stringify({ status: 'failed', name: error.name, code: error.statusCode,
    message: safe(error.message), cause: safe(error.cause?.message) }));
}
