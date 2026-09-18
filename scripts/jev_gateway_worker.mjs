// One long-lived SDK client; one input JSON line produces exactly one output JSON line.
// stdout is protocol-only. No model fallback, hidden retries or content transformations.
import { createInterface } from 'node:readline';
import { experimental_evaluate as evaluate } from 'ai';
import { createGateway } from '@ai-sdk/gateway';

const gateway = createGateway({ apiKey: process.env.AI_GATEWAY_API_KEY });
const model = gateway.evaluationModel('typesafe-ai/jev');
for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  if (!line.trim()) continue;
  try {
    const request = JSON.parse(line);
    const bytes = value => Buffer.byteLength(JSON.stringify(value));
    if (Buffer.byteLength(line) > 60000 || bytes(request.state) + Math.max(0, ...Object.values(request.questions).map(bytes)) > 28000) {
      throw new Error('request exceeds conservative input byte bound');
    }
    const value = await evaluate({ model, state: request.state, questions: request.questions,
      maxRetries: 0, abortSignal: AbortSignal.timeout(45000) });
    process.stdout.write(JSON.stringify({ answers: value.answers, usage: value.usage,
      providerMetadata: value.providerMetadata, modelId: value.response.modelId, rounding: value.rounding }) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ error: { name: error?.name ?? 'Error', status: error?.statusCode ?? null,
      message: String(error?.message ?? 'evaluation failed').replace(/vck_[\w-]+/g, '[redacted]').slice(0, 600) } }) + '\n');
  }
}
