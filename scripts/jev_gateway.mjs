// Evaluation-only bridge. Never falls back to a generative model.
import { experimental_evaluate as evaluate } from 'ai';
import { createGateway } from '@ai-sdk/gateway';

let input = '';
for await (const chunk of process.stdin) input += chunk;
try {
  if (Buffer.byteLength(input) > 24000) throw new Error('request exceeds conservative input byte bound');
  const request = JSON.parse(input);
  if (!process.env.AI_GATEWAY_API_KEY) throw new Error('AI_GATEWAY_API_KEY missing');
  const gateway = createGateway({ apiKey: process.env.AI_GATEWAY_API_KEY });
  const result = await evaluate({
    model: gateway.evaluationModel('typesafe-ai/jev'),
    state: request.state,
    questions: request.questions,
    maxRetries: 0,
    abortSignal: AbortSignal.timeout(45000),
  });
  process.stdout.write(JSON.stringify({
    answers: result.answers, usage: result.usage,
    providerMetadata: result.providerMetadata, modelId: result.response.modelId,
    rounding: result.rounding,
  }) + '\n');
} catch (error) {
  // SDK errors can contain headers/request bodies: never dump them.
  process.stdout.write(JSON.stringify({error: {
    name: error?.name ?? 'Error', status: error?.statusCode ?? null,
    message: String(error?.message ?? 'evaluation failed').replace(/vck_[\w-]+/g, '[redacted]').slice(0, 600),
  }}) + '\n');
  process.exitCode = 1;
}
