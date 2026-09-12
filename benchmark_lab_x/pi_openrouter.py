"""One tool-free Pi candidate turn through OpenRouter, with private raw receipts"""
from base64 import b64encode
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time

from . import openrouter_preparation as http, qualification as q, storage, outgoing

PACKAGE = '@earendil-works/pi-coding-agent'
VERSION = '0.85.1'
BRIDGE = Path(__file__).with_name('pi_bridge.mjs')


def identity(package, node):
    """Fingerprint the installed Pi modules used by the bridge, without a model call"""
    package = Path(package).resolve(strict=True)
    roots = {PACKAGE: package}
    for name in ('pi-agent-core', 'pi-ai'):
        roots['@earendil-works/' + name] = (package.parent / name).resolve(strict=True)
    files = {}
    for name, root in roots.items():
        metadata = json.loads((root / 'package.json').read_text())
        if metadata['name'] != name or metadata['version'] != VERSION:
            raise ValueError('Installation Pi 0.85.1 cohérente requise')
        paths = [root / 'package.json', *sorted((root / 'dist').rglob('*'))]
        if not (root / 'dist/index.js').is_file():
            raise ValueError('Module Pi absent')
        for path in paths:
            if path.is_symlink():
                raise ValueError('Module Pi symbolique inattendu')
            if path.is_file():
                files[name + '/' + path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    lock = package / 'npm-shrinkwrap.json'
    files['npm-shrinkwrap.json'] = sha256(lock.read_bytes()).hexdigest()
    node = str(Path(node).resolve(strict=True))
    node_version = subprocess.check_output([node, '--version'], timeout=10, env={}).decode().strip()
    return dict(package=PACKAGE, version=VERSION, sha256=q.digest(files),
                bridge_sha256=sha256(BRIDGE.read_bytes()).hexdigest(),
                node_version=node_version, node_sha256=sha256(Path(node).read_bytes()).hexdigest(),
                scope='Installed Pi coding-agent, agent-core and pi-ai module trees; remaining dependencies described by npm-shrinkwrap')


def system_context(system):
    # Pi 0.85.1 appends this directory even to an explicit custom system prompt
    return system + '\nCurrent working directory: /\n'


def _read_line(process, timeout):
    deadline = time.monotonic() + timeout
    raw = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while b'\n' not in raw:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError('Pi sans réponse terminale')
            block = os.read(process.stdout.fileno(), 65536)
            if not block:
                raise ValueError('Pi interrompu')
            raw.extend(block)
            if len(raw) > http.MAX_RESPONSE_BYTES:
                raise ValueError('Reçu Pi hors limites')
    if raw.count(b'\n') != 1 or not raw.endswith(b'\n'):
        raise ValueError('Message Pi non attribuable')
    return json.loads(raw, object_pairs_hook=storage._unique_object)


class PiOpenRouter:
    def __init__(self, api_key, package, node):
        # Reuse the channel's credential validation; this value never reaches Pi
        http.OpenRouterPreparation(api_key)
        self._key = api_key
        self.package = Path(package).resolve(strict=True)
        self.node = str(Path(node).resolve(strict=True))

    def prepare(self, operation, request):
        if request.get('outgoing_format') != outgoing.FORMAT:
            raise ValueError('Ancien format sortant : nouvelle version de tâche requise')
        projected = outgoing.closed_candidate(request['outgoing'])
        config, conditions = request['requested_configuration'], request['conditions']
        if operation['phase'] != 'acquisition':
            raise ValueError('Tentative candidate requise')
        if any(conditions[k] for k in ('tools', 'packages', 'skills')):
            raise ValueError('Ce transport Pi ne fournit aucun outil ni extension')
        defaults = conditions['defaults']
        storage._fields(defaults, ('system_prompt', 'timeout_seconds', 'context_window'), 'Pi defaults')
        if (type(defaults['system_prompt']) is not str or not defaults['system_prompt'].strip()
                or type(defaults['timeout_seconds']) is not int or defaults['timeout_seconds'] <= 0
                or type(defaults['context_window']) is not int or defaults['context_window'] <= 0):
            raise ValueError('Contexte Pi et durée décidés requis')
        if sha256(system_context(defaults['system_prompt']).encode()).hexdigest() != conditions['context_sha256']:
            raise ValueError('Contexte système Pi divergent')
        live = identity(self.package, self.node)
        if any(conditions['pi'][k] != live[k] for k in ('package', 'version', 'sha256')):
            raise ValueError('Installation Pi divergente')
        if any(conditions['environment'].get(k) != live[k] for k in ('node_version', 'node_sha256', 'bridge_sha256')):
            raise ValueError('Exécuteur Pi divergent')
        parameters = config['parameters']
        prompt = storage._strict_json(projected)
        messages = [dict(role='system', content=system_context(defaults['system_prompt'])),
                    dict(role='user', content=prompt)]
        wire = storage._strict_json(self._payload(config, messages))
        if len(wire.encode()) > http.MAX_REQUEST_BYTES or self._key in wire:
            raise ValueError('Requête hors limites')
        self._input = dict(model=config['model'], system=defaults['system_prompt'], prompt=prompt,
                           max_tokens=parameters['max_tokens'], context_window=defaults['context_window'])
        self._wire_bytes = wire
        self._wire_sha256 = sha256(wire.encode('utf-8')).hexdigest()
        self._wire_proof = outgoing.wire_proof(wire, messages)
        self._prepared = q.digest(request)
        self._identity = live
        self._timeout = defaults['timeout_seconds']

    def _payload(self, config, messages):
        if config['access'] != 'API' or config['channel_id'] != http.ENDPOINT:
            raise ValueError('Canal OpenRouter obligatoire')
        if (not config['model'].strip() or config['model'] != config['revision']):
            raise ValueError('Identifiant de modèle exact requis, sans alias substitué')
        parameters = config['parameters']
        if (not {'max_tokens', 'provider'} <= parameters.keys()
                or parameters.keys() - {'max_tokens', 'provider', 'temperature', 'top_p', 'reasoning', 'stream'}
                or type(parameters['max_tokens']) is not int or parameters['max_tokens'] <= 0
                or parameters.get('stream', False) is not False):
            raise ValueError('Paramètres textuels explicites requis')
        for field, low, high in (('temperature', 0, 2), ('top_p', 0, 1)):
            if field in parameters and (type(parameters[field]) not in (int, float) or not low <= parameters[field] <= high):
                raise ValueError('Paramètre numérique invalide')
        reasoning = parameters.get('reasoning')
        if reasoning is not None:
            storage._fields(reasoning, ('effort',), 'reasoning')
            q._texts([reasoning['effort']], 'effort', required=True)
        if config['effort'] != (reasoning['effort'] if reasoning else 'off'):
            raise ValueError('Effort demandé divergent des paramètres émis')
        provider = parameters['provider']
        storage._fields(provider, ('only', 'order', 'allow_fallbacks', 'require_parameters'), 'OpenRouter routing')
        q._texts(provider['only'], 'providers', required=True, unique=True)
        if (provider['order'] != provider['only'] or type(provider['allow_fallbacks']) is not bool
                or provider['require_parameters'] is not True):
            raise ValueError('Routage explicite et paramètres requis')
        return {'model': config['model'], **parameters, 'stream': False, 'messages': messages}

    def _messages(self, wire):
        return json.loads(wire)['messages']

    def __call__(self, operation, request):
        if (operation['state'] != 'EMISSION_POSSIBLE' or getattr(self, '_prepared', None) != q.digest(request)):
            raise ValueError('Préparation et intention persistante requises')
        wire = getattr(self, '_wire_bytes', None)
        if wire is None or sha256(wire.encode('utf-8')).hexdigest() != self._wire_sha256:
            raise ValueError('Corps modifié avant émission')
        # No credentials, home configuration, tools, or judge pieces enter Pi
        with tempfile.TemporaryDirectory(prefix='benchmark-pi-') as directory:
            process = subprocess.Popen([self.node, str(BRIDGE), str(self.package)], cwd=directory,
                env={'HOME': directory, 'PI_OFFLINE': '1'}, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                process.stdin.write((storage._strict_json(self._input) + '\n').encode())
                process.stdin.flush()
                event = _read_line(process, self._timeout)
                storage._fields(event, ('type', 'model', 'context'), 'Pi request')
                context = event['context']
                messages = context.get('messages', [])
                emitted = self._messages(wire)
                if (event['type'] != 'request' or event['model'] != self._input['model']
                        or context.get('systemPrompt') != emitted[0]['content'] or context.get('tools') != []
                        or len(messages) != 1 or messages[0].get('role') != 'user'
                        or messages[0].get('content') != [{'type': 'text', 'text': self._input['prompt']}]):
                    raise ValueError('Contexte Pi effectif divergent')
                response = self._exchange(operation, request)
                observation = response['receipt']['observed_configuration']
                observation['pi'] = dict(self._identity, context_sha256=request['conditions']['context_sha256'],
                                         provider_requests=1, terminal=False)
                result = response['receipt']['result']
                try:
                    process.stdin.write((storage._strict_json(dict(output=result['output'] or '', incident=result['incident'])) + '\n').encode())
                    process.stdin.flush()
                    terminal = _read_line(process, self._timeout)
                    process.stdin.close()
                    process.wait(timeout=self._timeout)
                    if (process.returncode != 0 or terminal != dict(type='done', calls=1, output=result['output'] or '',
                            stop_reason='error' if result['incident'] else 'stop')):
                        raise ValueError('Fin Pi divergente')
                    observation['pi']['terminal'] = True
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    result['incident'] = 'HARNESS_ERROR'
                return response
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                if not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                process.stdout.close()

    def _exchange(self, operation, request):
        wire = self._wire_bytes
        if sha256(wire.encode('utf-8')).hexdigest() != self._wire_sha256:
            raise ValueError('Corps modifié avant émission')
        payload = json.loads(wire)
        status, headers, raw, complete, started, clock = http.post(self._key, wire, self._timeout)
        redacted = self._key.encode() in raw
        document = None
        try:
            document = json.loads(raw, object_pairs_hook=storage._unique_object, parse_float=str)
            storage._strict_json(document)
            redacted = redacted or self._key in storage._strict_json(document)
        except (ValueError, TypeError):
            document = None
        if redacted:
            raw, document = b'[REDACTED_CREDENTIAL]', None
        data = document if type(document) is dict else {}
        output, incident = None, 'PROVIDER_RESPONSE_INCOMPLETE'
        try:
            choices = data['choices']
            if type(choices) is list and len(choices) == 1:
                message = choices[0]['message']
                if type(message.get('content')) is str:
                    output = message['content']
                if (status == 200 and complete and not redacted and message.get('role') == 'assistant'
                        and not message.get('tool_calls') and choices[0]['finish_reason'] == 'stop' and output is not None):
                    incident = None
                if (message.get('refusal') or choices[0].get('native_finish_reason') == 'refusal'
                        or choices[0].get('finish_reason') == 'content_filter'):
                    incident = 'CONTENT_REFUSAL'
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        route = data.get('openrouter_metadata')
        available = route.get('endpoints', {}).get('available', []) if type(route) is dict and type(route.get('endpoints')) is dict else []
        selected = [x for x in available if type(x) is dict and x.get('selected') is True] if type(available) is list else []
        endpoint = selected[0] if len(selected) == 1 else {}
        model = data.get('model') if type(data.get('model')) is str else None
        observed = dict(provider=endpoint.get('provider'), model=model, revision=model,
            access='API', channel_id=http.ENDPOINT, route=endpoint.get('tag'), parameters=None, effort=None,
            sources=dict(provider='OpenRouter selected endpoint' if endpoint.get('provider') else None,
                model='HTTP response /model' if model else None, revision='HTTP response /model; model slug, not hidden weight revision' if model else None,
                access='Executor HTTPS request', channel_id='Executor HTTPS endpoint', route='OpenRouter selected endpoint tag' if endpoint.get('tag') else None),
            routing=route, request=payload,
            outgoing=self._wire_proof,
            http=dict(endpoint=http.ENDPOINT, status=status, response_headers=headers, started_at=started,
                received_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-clock,
                complete=complete, credential_redacted=redacted, body_base64=b64encode(raw).decode(), body_sha256=sha256(raw).hexdigest()))
        if (model is not None or status == 200) and model != request['requested_configuration']['revision']:
            incident = 'MODEL_IDENTITY_MISMATCH'
        tag = endpoint.get('tag')
        if tag is not None and (type(tag) is not str or not any(
                tag == allowed or tag.startswith(allowed + '/') for allowed in payload['provider']['only'])):
            incident = 'PROVIDER_ROUTE_MISMATCH'
        if type(route) is dict and (route.get('pipeline') or route.get('requested', self._input['model']) != self._input['model']):
            incident = 'HARNESS_ERROR'
        if headers.get('X-Generation-Id') and data.get('id') and headers['X-Generation-Id'] != data['id']:
            incident = 'HARNESS_ERROR'
        measured = http.consumption(document, complete=complete and status == 200 and not redacted)
        observed['consumption'] = measured
        amount = measured['amount']
        return dict(receipt=dict(receipt_id='openrouter-'+operation['operation_id'], observed_configuration=observed,
                        resources_seen=[p['name'] for p in request['outgoing']['pieces']],
                        result=dict(output=output, incident=incident, emission='ESTABLISHED')),
                    cost=dict(status='KNOWN' if amount is not None else 'UNKNOWN', amount=amount, currency='USD',
                        source='OpenRouter /usage/cost' if amount is not None else 'Coût financier absent ; réserve conservée'))
