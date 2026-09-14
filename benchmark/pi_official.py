"""Explicit official API acquisition under the same tool-free Pi harness"""
from base64 import b64encode
from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPSConnection, IncompleteRead
import json
import os
import time
from urllib.parse import urlsplit

from . import storage
from .pi_openrouter import PiOpenRouter
from . import openrouter_preparation as limits

CHANNELS = {
    'anthropic': ('Anthropic', 'api.anthropic.com', '/v1/messages', 'ANTHROPIC_API_KEY'),
    'deepseek': ('DeepSeek', 'api.deepseek.com', '/chat/completions', 'DEEPSEEK_API_KEY'),
    'zai': ('Z.ai', 'api.z.ai', '/api/paas/v4/chat/completions', 'ZAI_API_KEY'),
    'openai': ('OpenAI', 'api.openai.com', '/v1/responses', 'OPENAI_API_KEY'),
    'moonshot': ('Moonshot AI', 'api.moonshot.ai', '/v1/chat/completions', 'MOONSHOT_API_KEY'),
    'dashscope': ('Alibaba Cloud Model Studio', None, None, 'DASHSCOPE_API_KEY'),
    'tokenhub': ('Tencent TokenHub', None, None, 'TENCENT_TOKENHUB_API_KEY'),
}

_NATIVE_IDENTITIES = {
    ('deepseek', 'deepseek/deepseek-v4.1-flash'): 'deepseek-flash',
    ('moonshot', 'moonshotai/kimi-k3'): 'kimi-k3',
    ('dashscope', 'qwen/qwen3.8-max'): 'qwen3.8-max',
    ('dashscope', 'qwen/qwen3.8-max-0902'): 'qwen3.8-max-0902',
    ('tokenhub', 'tencent/hy4-preview'): 'hy4-preview',
}


def _dashscope_endpoint(base_url):
    parsed = urlsplit(base_url)
    host = parsed.hostname or ''
    workspace = host.split('.', 1)[0]
    workspace_host = (workspace and workspace not in ('www', 'api') and (
        host.endswith('.cn-beijing.maas.aliyuncs.com')
        or host.endswith('.ap-southeast-1.maas.aliyuncs.com')
        or host.endswith('.ap-northeast-1.maas.aliyuncs.com')
        or host.endswith('.cn-hongkong.maas.aliyuncs.com')
        or host.endswith('.eu-central-1.maas.aliyuncs.com')
        or host.endswith('.us-east-1.maas.aliyuncs.com')))
    legacy_hosts = {'dashscope-us.aliyuncs.com', 'dashscope.aliyuncs.com',
                    'dashscope-intl.aliyuncs.com', 'cn-hongkong.dashscope.aliyuncs.com'}
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/compatible-mode/v1'
            or not (workspace_host or host in legacy_hosts)):
        raise ValueError('DASHSCOPE_BASE_URL HTTPS officiel Alibaba requis')
    path = parsed.path.rstrip('/') + '/responses'
    return host, path


def _tokenhub_endpoint(base_url):
    parsed = urlsplit(base_url)
    hosts = {'tokenhub.tencentmaas.com', 'tokenhub-intl.tencentmaas.com',
             'tokenhub.tencentmaas.cn', 'tokenhub-intl.tencentmaas.cn',
             'tokenhub.tencentcloudmaas.com', 'tokenhub-intl.tencentcloudmaas.com',
             'tokenhub-us.tencentcloudmaas.com', 'tokenhub.tencentcloudmaas.tech',
             'tokenhub-intl.tencentcloudmaas.tech', 'tokenhub-us.tencentcloudmaas.tech'}
    if (parsed.scheme != 'https' or parsed.hostname not in hosts or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path.rstrip('/') or parsed.query or parsed.fragment):
        raise ValueError('TENCENT_TOKENHUB_BASE_URL HTTPS officiel Tencent requis')
    return parsed.hostname, '/v1/chat/completions'


def resolve_channel(kind, base_url=None):
    provider, host, path, key = CHANNELS[kind]
    if kind == 'dashscope':
        host, path = _dashscope_endpoint(base_url if base_url is not None else os.environ.get('DASHSCOPE_BASE_URL', ''))
    elif kind == 'tokenhub':
        host, path = _tokenhub_endpoint(base_url if base_url is not None else os.environ.get('TENCENT_TOKENHUB_BASE_URL', ''))
    return provider, host, path, key


def kind_for_endpoint(endpoint):
    if not isinstance(endpoint, str):
        return None
    for kind in CHANNELS:
        if kind in ('dashscope', 'tokenhub'):
            try:
                base = (endpoint.removesuffix('/responses') if kind == 'dashscope'
                        else endpoint.removesuffix('/v1/chat/completions'))
                host, path = (_dashscope_endpoint if kind == 'dashscope' else _tokenhub_endpoint)(base)
            except ValueError:
                continue
            if endpoint == 'https://' + host + path:
                return kind
        else:
            _, host, path, _ = CHANNELS[kind]
            if endpoint == 'https://' + host + path:
                return kind
    return None


def provider_for_endpoint(endpoint):
    kind = kind_for_endpoint(endpoint)
    return CHANNELS[kind][0] if kind else None


def native_identity(kind, openrouter_identity):
    mapped = _NATIVE_IDENTITIES.get((kind, openrouter_identity))
    if mapped:
        return mapped
    namespaces = {'anthropic': 'anthropic/', 'zai': 'z-ai/', 'openai': 'openai/'}
    prefix = namespaces.get(kind)
    if prefix and openrouter_identity.startswith(prefix) and len(openrouter_identity) > len(prefix):
        return openrouter_identity[len(prefix):]
    raise ValueError('Identité native exacte indisponible ; aucun alias de substitution')


class PiOfficial(PiOpenRouter):
    def __init__(self, api_key, package, node, provider, base_url=None):
        self.provider, self.host, self.path, _ = resolve_channel(provider, base_url)
        self.kind = provider
        self.endpoint = 'https://' + self.host + self.path
        if (type(api_key) is not str or not api_key or not api_key.isascii()
                or any(character.isspace() or ord(character) < 32 for character in api_key)):
            raise ValueError('Clé API officielle explicite requise côté exécuteur')
        super().__init__(api_key, package, node)

    def _payload(self, config, messages):
        if (config['access'] != 'API' or config['channel_id'] != self.endpoint
                or config['provider'] != self.provider or config['route'] != self.endpoint):
            raise ValueError('Configuration officielle distincte et canal exact requis')
        for field in ('model', 'revision'):
            if not isinstance(config[field], str) or not config[field].strip():
                raise ValueError('Identité officielle explicite requise')
        params = config['parameters']
        responses = self.kind in ('openai', 'dashscope')
        token_field = 'max_output_tokens' if responses else 'max_tokens'
        common = {token_field, 'stream'}
        extras = {
            'anthropic': {'output_config'},
            'openai': {'reasoning'},
            'moonshot': {'reasoning_effort'},
            'dashscope': {'reasoning', 'temperature', 'top_p'},
            'tokenhub': {'thinking', 'reasoning_effort', 'temperature', 'top_p'},
            'deepseek': {'thinking', 'reasoning_effort', 'temperature', 'top_p'},
            'zai': {'thinking', 'reasoning_effort', 'temperature', 'top_p'},
        }[self.kind]
        allowed = common | extras
        if (set(params) - allowed or not common <= set(params)
                or type(params[token_field]) is not int or params[token_field] <= 0
                or params['stream'] is not False):
            raise ValueError('Paramètres natifs textuels explicites requis')
        if self.kind == 'anthropic':
            storage._fields(params.get('output_config'), ('effort',), 'official effort')
            effort = params['output_config']['effort']
            if effort not in ('low', 'medium', 'high', 'max'):
                raise ValueError('Effort natif non pris en charge')
        elif self.kind == 'openai':
            storage._fields(params.get('reasoning'), ('effort',), 'official reasoning')
            effort = params['reasoning']['effort']
            if effort not in ('none', 'low', 'medium', 'high', 'xhigh', 'max'):
                raise ValueError('Effort natif non pris en charge')
        elif self.kind == 'moonshot':
            effort = params.get('reasoning_effort')
            if effort != 'max':
                raise ValueError('Kimi K3 exige reasoning_effort=max')
        elif self.kind == 'dashscope':
            storage._fields(params.get('reasoning'), ('effort',), 'official reasoning')
            effort = params['reasoning']['effort']
            if effort not in ('low', 'medium', 'xhigh'):
                raise ValueError('Raisonnement natif explicite requis')
        elif self.kind == 'tokenhub':
            storage._fields(params.get('thinking'), ('type',), 'official thinking')
            effort = params.get('reasoning_effort')
            if params['thinking']['type'] != 'enabled' or effort not in ('low', 'high'):
                raise ValueError('Raisonnement natif explicite requis')
        else:
            storage._fields(params.get('thinking'), ('type',), 'official thinking')
            effort = params.get('reasoning_effort')
            allowed_efforts = ('low', 'high', 'max')
            if params['thinking']['type'] != 'enabled' or effort not in allowed_efforts:
                raise ValueError('Raisonnement natif explicite requis')
        for field, low, high in (('temperature', 0, 2), ('top_p', 0, 1)):
            if field in params and (type(params[field]) not in (int, float) or not low <= params[field] <= high):
                raise ValueError('Paramètre natif numérique invalide')
        if config['effort'] != effort:
            raise ValueError('Effort natif divergent de la configuration admise')
        if self.kind == 'anthropic':
            return dict(model=config['model'], system=messages[0]['content'], messages=[messages[1]], **params)
        if responses:
            return dict(model=config['model'], input=messages, **params)
        return dict(model=config['model'], messages=messages, **params)

    def _messages(self, wire):
        body = json.loads(wire)
        if self.kind == 'anthropic':
            return [dict(role='system', content=body['system']), *body['messages']]
        if self.kind in ('openai', 'dashscope'):
            return body['input']
        return body['messages']

    def _exchange(self, operation, request):
        wire = self._wire_bytes
        if sha256(wire.encode()).hexdigest() != self._wire_sha256:
            raise ValueError('Corps modifié avant émission')
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        headers = {'Content-Type': 'application/json'}
        if self.kind == 'anthropic':
            headers.update({'x-api-key': self._key, 'anthropic-version': '2023-06-01'})
        else:
            headers['Authorization'] = 'Bearer ' + self._key
        connection = HTTPSConnection(self.host, timeout=self._timeout)
        try:
            connection.request('POST', self.path, body=wire.encode(), headers=headers)
            response = connection.getresponse()
            status = response.status
            try:
                raw = response.read(limits.MAX_RESPONSE_BYTES + 1)
                complete = response.length in (None, 0)
            except IncompleteRead as error:
                raw, complete = error.partial, False
            if len(raw) > limits.MAX_RESPONSE_BYTES:
                raw, complete = raw[:limits.MAX_RESPONSE_BYTES], False
        finally:
            connection.close()
        redacted = self._key.encode() in raw
        data = {}
        try:
            decoded = json.loads(raw, object_pairs_hook=storage._unique_object)
            redacted = redacted or self._key in storage._strict_json(decoded)
            if isinstance(decoded, dict):
                data = decoded
        except (ValueError, TypeError):
            pass
        if redacted:
            raw, data = b'[REDACTED_CREDENTIAL]', {}
        output, incident = None, 'PROVIDER_RESPONSE_INCOMPLETE'
        try:
            if self.kind == 'anthropic':
                blocks = data['content']
                if type(blocks) is not list or any(type(b) is not dict for b in blocks):
                    raise ValueError('Blocs natifs invalides')
                text = ''.join(b['text'] for b in blocks if b.get('type') == 'text')
                refused = data.get('stop_reason') == 'refusal' or any(b.get('type') == 'refusal' for b in blocks)
                ended = (data['stop_reason'] == 'end_turn' and data.get('role') == 'assistant'
                         and all(b.get('type') in ('text', 'thinking', 'redacted_thinking') for b in blocks))
            elif self.kind in ('openai', 'dashscope'):
                items = data['output']
                if type(items) is not list or any(type(item) is not dict for item in items):
                    raise ValueError('Sortie Responses invalide')
                messages = [item for item in items if item.get('type') == 'message']
                blocks = [block for item in messages for block in item.get('content', [])]
                text = ''.join(block['text'] for block in blocks if block.get('type') == 'output_text')
                refused = any(block.get('type') == 'refusal' for block in blocks)
                ended = (data.get('status') == 'completed' and len(messages) == 1
                         and messages[0].get('role') == 'assistant'
                         and messages[0].get('status', 'completed') == 'completed'
                         and all(block.get('type') in ('output_text', 'refusal') for block in blocks))
            else:
                choices = data['choices']
                if type(choices) is not list or len(choices) != 1:
                    raise ValueError('Choix natif unique requis')
                choice = choices[0]
                message = choice['message']
                text = message['content']
                refused = (bool(message.get('refusal')) or choice.get('finish_reason') == 'content_filter'
                           or choice.get('native_finish_reason') == 'refusal')
                ended = choice['finish_reason'] == 'stop' and message.get('role') == 'assistant' and not message.get('tool_calls')
            if type(text) is str:
                output = text
            if refused:
                incident = 'CONTENT_REFUSAL'
            elif status == 200 and complete and not redacted and ended and output and output.strip():
                incident = None
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
        model = data.get('model') if type(data.get('model')) is str else None
        if (model is not None or status == 200) and model != request['requested_configuration']['revision']:
            incident = 'MODEL_IDENTITY_MISMATCH'
        observed = dict(provider=self.provider, model=model, revision=model, access='API',
            channel_id=self.endpoint, route=self.endpoint, parameters=None, effort=None,
            sources=dict(provider='Executor official HTTPS endpoint', model='Native response /model' if model else None,
                         revision='Native response /model; no hidden weight revision attested' if model else None,
                         access='Executor HTTPS request', channel_id='Executor HTTPS endpoint', route='Executor HTTPS endpoint'),
            request=json.loads(wire), outgoing=self._wire_proof, native_usage=data.get('usage'),
            http=dict(endpoint=self.endpoint, status=status, response_headers={}, started_at=started,
                      received_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-clock,
                      complete=complete, credential_redacted=redacted,
                      body_base64=b64encode(raw).decode(), body_sha256=sha256(raw).hexdigest()))
        return dict(receipt=dict(receipt_id='official-'+operation['operation_id'], observed_configuration=observed,
            resources_seen=[p['name'] for p in request['outgoing']['pieces']],
            result=dict(output=output, incident=incident, emission='ESTABLISHED')),
            cost=dict(status='UNKNOWN', amount=None, currency='USD',
                      source='Native usage is not a financial receipt; reserve retained pending reconciliation'))
