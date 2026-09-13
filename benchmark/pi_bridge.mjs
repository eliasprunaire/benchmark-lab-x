// Pi owns one candidate turn; the parent owns OpenRouter credentials and HTTP
import { pathToFileURL } from 'node:url';
import readline from 'node:readline';

// No provider network or credential discovery in this process
// The only provider implementation below communicates with the trusted parent
process.env.PI_OFFLINE = '1';
globalThis.fetch = () => { throw new Error('NETWORK_OWNED_BY_PARENT'); };
const { createAgentSession, ModelRuntime, SettingsManager, DefaultResourceLoader, SessionManager } =
    await import(pathToFileURL(process.argv[2] + '/dist/index.js'));
const lines = readline.createInterface({ input: process.stdin });
const input = lines[Symbol.asyncIterator]();
const next = async () => {
    const line = await input.next();
    if (line.done) throw new Error('PARENT_DISCONNECTED');
    return JSON.parse(line.value);
};
let session;
try {
    const request = await next();
    const runtime = await ModelRuntime.create({
        authPath: process.cwd() + '/auth.json', modelsPath: null,
        modelsStorePath: process.cwd() + '/models.json', refreshOnCreate: false, allowModelNetwork: false,
    });
    let calls = 0;
    runtime.registerProvider('openrouter', {
        api: 'openai-completions', baseUrl: 'https://openrouter.ai/api/v1', apiKey: 'benchmark-stdio',
        models: [{ id: request.model, name: request.model, reasoning: false, input: ['text'],
            // Internal Pi counters are not financial evidence; retain OpenRouter usage in the parent receipt
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: request.context_window, maxTokens: request.max_tokens }],
        streamSimple(model, context) {
            if (++calls !== 1) throw new Error('SECOND_REQUEST_REFUSED');
            process.stdout.write(JSON.stringify({ type: 'request', model: model.id, context }) + '\n');
            const message = next().then(response => ({
                role: 'assistant', content: [{ type: 'text', text: response.output }],
                api: 'openai-completions', provider: 'openrouter', model: model.id,
                stopReason: response.incident ? 'error' : 'stop',
                ...(response.incident ? { errorMessage: 'PROVIDER_RESPONSE_INCOMPLETE' } : {}),
                usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
                    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }, timestamp: Date.now(),
            }));
            return {
                result: () => message,
                async *[Symbol.asyncIterator]() {
                    const value = await message;
                    yield value.stopReason === 'error'
                        ? { type: 'error', reason: 'error', error: value }
                        : { type: 'done', reason: 'stop', message: value };
                },
            };
        },
    });
    const settings = SettingsManager.inMemory({ retry: { enabled: false }, compaction: { enabled: false }, packages: [] });
    const loader = new DefaultResourceLoader({
        cwd: '/', agentDir: process.cwd(), settingsManager: settings,
        noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
        systemPrompt: request.system,
    });
    await loader.reload();
    ({ session } = await createAgentSession({
        cwd: '/', agentDir: process.cwd(), modelRuntime: runtime,
        model: runtime.getModel('openrouter', request.model), thinkingLevel: 'off', tools: [], noTools: 'all',
        resourceLoader: loader, settingsManager: settings, sessionManager: SessionManager.inMemory('/'),
    }));
    await session.prompt(request.prompt, { expandPromptTemplates: false });
    const last = session.state.messages.at(-1);
    if (calls !== 1 || last?.role !== 'assistant' || session.state.tools.length !== 0) throw new Error('TURN_INCOMPLETE');
    process.stdout.write(JSON.stringify({ type: 'done', calls, output: last.content.map(x => x.text ?? '').join(''),
        stop_reason: last.stopReason }) + '\n');
} catch {
    process.exitCode = 78;
} finally {
    session?.dispose();
    lines.close();
}
