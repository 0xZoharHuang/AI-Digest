// Isolated five-request transport diagnostic. Not a production worker or model router.
import { experimental_evaluate as evaluate } from 'ai';
import { createGateway } from '@ai-sdk/gateway';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const subcalls = [];
try {
  if (Buffer.byteLength(input) > 24000) throw new Error('diagnostic request too large');
  const request = JSON.parse(input);
  const entries = Object.entries(request.questions);
  if (entries.length !== 5 || request.state.inputs.length !== 5) throw new Error('exactly five diagnostic calls required');
  if (!['fresh', 'reuse'].includes(request.state.transport)) throw new Error('unknown diagnostic transport');
  const gateway = createGateway({ apiKey: process.env.AI_GATEWAY_API_KEY });
  const model = gateway.evaluationModel('typesafe-ai/jev');
  const answers = {};
  let inputTokens = 0, outputTokens = 0, cost = 0;
  for (let i = 0; i < entries.length; i++) {
    const [key, question] = entries[i];
    const child = { state: request.state.inputs[i], questions: { signal: question } };
    const started = performance.now();
    let result;
    if (request.state.transport === 'fresh') {
      const bridge = fileURLToPath(new URL('./jev_gateway.mjs', import.meta.url));
      const launcher = "await import(process.argv[1]); await new Promise(r => process.stdout.write('', r)); process.exit(process.exitCode ?? 0);";
      const proc = spawnSync(process.execPath, ['--use-env-proxy', '--input-type=module', '-e', launcher, bridge],
        { input: JSON.stringify(child), encoding: 'utf8', timeout: 9000, maxBuffer: 1024 * 1024 });
      if (proc.error || proc.status !== 0) throw new Error('fresh diagnostic subprocess failed');
      result = JSON.parse(proc.stdout);
    } else {
      const value = await evaluate({ model, ...child, maxRetries: 0, abortSignal: AbortSignal.timeout(9000) });
      result = { answers: value.answers, usage: value.usage, providerMetadata: value.providerMetadata, modelId: value.response.modelId };
    }
    if (result.modelId !== 'typesafe-ai/jev' || result.providerMetadata?.gateway?.routing?.finalProvider !== 'typesafe-ai') {
      throw new Error('unexpected diagnostic provider/model');
    }
    answers[key] = result.answers.signal;
    inputTokens += result.usage.inputTokens;
    outputTokens += result.usage.outputTokens;
    cost += Number(result.providerMetadata.gateway.cost);
    subcalls.push({ index: i, seconds: (performance.now() - started) / 1000,
      usage: result.usage, providerMetadata: result.providerMetadata, answer: result.answers.signal });
  }
  process.stdout.write(JSON.stringify({ answers, modelId: 'typesafe-ai/jev',
    usage: { inputTokens, outputTokens },
    providerMetadata: { gateway: { routing: { finalProvider: 'typesafe-ai' }, cost: cost.toFixed(12) } },
    transportBenchmark: { transport: request.state.transport, subcalls,
      note: 'Five actual provider calls; aggregate reservation and receipt. Per-call original costs retained.' },
  }) + '\n');
} catch (error) {
  process.stdout.write(JSON.stringify({ error: { name: error?.name ?? 'Error', status: error?.statusCode ?? null,
    message: String(error?.message ?? 'transport benchmark failed').replace(/vck_[\w-]+/g, '[redacted]').slice(0, 600) },
    completedSubcalls: subcalls }) + '\n');
  process.exitCode = 1;
}
