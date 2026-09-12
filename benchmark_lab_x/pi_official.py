"""Explicit official API acquisition under the same tool-free Pi harness"""
from base64 import b64encode
from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPSConnection, IncompleteRead
import json
import time

from . import storage
from .pi_openrouter import PiOpenRouter
from . import openrouter_preparation as limits

CHANNELS = {
    'anthropic': ('Anthropic', 'api.anthropic.com', '/v1/messages', 'ANTHROPIC_API_KEY'),
    'deepseek': ('DeepSeek', 'api.deepseek.com', '/chat/completions', 'DEEPSEEK_API_KEY'),
    'zai': ('Z.ai', 'api.z.ai', '/api/paas/v4/chat/completions', 'ZAI_API_KEY'),
}


class PiOfficial(PiOpenRouter):
    def __init__(self, api_key, package, node, provider):
        self.provider, self.host, self.path, _ = CHANNELS[provider]
        self.kind = provider
        self.endpoint = 'https://' + self.host + self.path
        super().__init__(api_key, package, node)

    def _payload(self, config, messages):
        if (config['access'] != 'API' or config['channel_id'] != self.endpoint
                or config['provider'] != self.provider or config['route'] != self.endpoint):
            raise ValueError('Configuration officielle distincte et canal exact requis')
        for field in ('model', 'revision'):
            if not isinstance(config[field], str) or not config[field].strip():
                raise ValueError('Identité officielle explicite requise')
        params = config['parameters']
        common = {'max_tokens', 'stream'}
        allowed = common | ({'output_config'} if self.kind == 'anthropic' else
                            {'thinking', 'reasoning_effort', 'temperature', 'top_p'})
        if (set(params) - allowed or not common <= set(params)
                or type(params['max_tokens']) is not int or params['max_tokens'] <= 0
                or params['stream'] is not False):
            raise ValueError('Paramètres natifs textuels explicites requis')
        if self.kind == 'anthropic':
            storage._fields(params.get('output_config'), ('effort',), 'official effort')
            effort = params['output_config']['effort']
            if effort not in ('low', 'medium', 'high', 'max'):
                raise ValueError('Effort natif non pris en charge')
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
        return dict(model=config['model'], messages=messages, **params)

    def _messages(self, wire):
        body = json.loads(wire)
        if self.kind == 'anthropic':
            return [dict(role='system', content=body['system']), *body['messages']]
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
