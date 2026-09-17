"""Autorisation et routage HTTP côté exécuteur pour la préparation privée."""
import hmac
import re
from urllib.parse import urlsplit, parse_qsl

from . import preparation as p
from .storage import _fields


def dispatch(store, method, path, token, body, source, transport, *, qualification_transport=None, candidate_transport=None,
             candidate_identity=None,
             access_secret=None, access_transport=None, presentation=None):
    """Executor-side authorization: HTTP fields can never claim an operator role."""
    if method == 'GET' and path == '/preparation':
        session_id, csrf, token = p.session(store, token, create=True)
        rows = p.connection_for(store).execute('SELECT dossier_id,current_revision FROM s2_dossiers WHERE session_id=? ORDER BY dossier_id',
                                               (session_id,)).fetchall()
        return 200, {'csrf_token': csrf, 'availability': p.availability(store, transport),
                     'dossiers': [{'dossier_id': d, 'revision': r,
                                   'need': store.get_dossier(d, r)['request']} for d, r in rows]}, token, None
    session_id, csrf, _ = p.session(store, token)
    access_paths = ('/preparation/access', '/preparation/access/start',
                    '/preparation/access/callback', '/preparation/access/disconnect')
    if method == 'GET' and path == '/preparation/access':
        from . import provider_access
        if not provider_access.available(store):
            return 503, {'connected': False, 'status': 'unavailable',
                         'error_code': 'ACCESS_UNAVAILABLE'}, None, None
        value = provider_access.view(store, session_id, access_secret, access_transport)
        if value['status'] == 'unavailable':
            value['error_code'] = 'ACCESS_UNAVAILABLE'
        return (200 if value['status'] != 'unavailable' else 503), value, None, None
    if method == 'GET':
        from . import restitution
        parsed = urlsplit(path)
        preview_route = re.fullmatch(
            r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/campaigns/([A-Za-z0-9_-]{1,128})/preview', parsed.path)
        if preview_route:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, errors='strict')
            if parsed.scheme or parsed.netloc or parsed.fragment or any(key != 'piece' for key, _ in pairs):
                raise ValueError('Sélection de pièces requise sur un chemin local')
            value = restitution.preview_view(store, session_id, *preview_route.groups(), piece_ids=[pid for _, pid in pairs],
                                             presentation=presentation)
            return 200, value, None, None
        comparison_route = re.fullmatch(
            r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/campaigns/([A-Za-z0-9_-]{1,128})'
            r'(?:/attempts/([A-Za-z0-9_-]{1,128}))?', parsed.path)
        if comparison_route:
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise ValueError('Chemin local requis')
            dossier_id, campaign_id, attempt_id = comparison_route.groups()
            query = restitution.query_parameters(parsed.query)
            if attempt_id is None:
                value = restitution.comparison(store, session_id, dossier_id, campaign_id, query=query)
            else:
                value = restitution.detail(store, session_id, dossier_id, campaign_id, attempt_id, query=query)
            return 200, value, None, None
        if path == '/preparation/catalogue':
            return 200, restitution.catalogue(store, session_id), None, None
    if method == 'POST':
        if type(body) is not dict:
            raise ValueError('Formulaire requis')
        if path != '/preparation/access/callback':
            supplied = body.get('csrf_token')
            if type(supplied) is not str or not hmac.compare_digest(supplied.encode(), csrf.encode()):
                raise p.Denied('Protection CSRF requise')
            body = {key: value for key, value in body.items() if key != 'csrf_token'}
    configuration_route = re.fullmatch(
        r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/configurations', path)
    if configuration_route:
        if method not in ('GET', 'POST'):
            raise p.Denied('Action inaccessible')
        from . import campaigns
        dossier_id = configuration_route.group(1)
        revision = p.owner(p.connection_for(store), session_id, dossier_id)
        p.require_requester_steps(store, p.connection_for(store), session_id, dossier_id, revision)
        if candidate_identity is None:
            return 503, {'error': 'Harnais candidat indisponible',
                         'error_code': 'CANDIDATE_PI_UNAVAILABLE'}, None, None
        if method == 'POST':
            return 201, campaigns.prepare_configurations(
                store, session_id, dossier_id, body, candidate_identity), None, None
        if method == 'GET':
            return 200, campaigns.configurations_view(store, session_id, dossier_id), None, None
    if path in access_paths:
        from . import provider_access
        unavailable = access_secret is None or not provider_access.available(store)
        if unavailable:
            return 503, {'connected': False, 'status': 'unavailable',
                         'error_code': 'ACCESS_UNAVAILABLE'}, None, None
        if method == 'POST' and path == '/preparation/access/start':
            _fields(body, ('callback_url',), 'access start')
            return 200, provider_access.start(store, session_id, access_secret,
                                               body['callback_url']), None, None
        if method == 'POST' and path == '/preparation/access/callback':
            _fields(body, ('code',), 'access callback')
            return 200, provider_access.callback(store, session_id, access_secret, body['code'],
                                                  access_transport), None, None
        if method == 'POST' and path == '/preparation/access/disconnect':
            _fields(body, (), 'access disconnect')
            return 200, provider_access.disconnect(store, session_id, access_secret), None, None
        raise p.Denied('Action inaccessible')
    launch_route = re.fullmatch(r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/campaigns/([A-Za-z0-9_-]{1,128})/(conditions|cap|start)', path)
    if launch_route:
        from . import campaigns
        dossier_id, campaign_id, action = launch_route.groups()
        connection = p.connection_for(store)
        p.owner(connection, session_id, dossier_id)
        if not campaigns.connection_for(store).execute(
                'SELECT 1 FROM s4_campaigns WHERE campaign_id=?', (campaign_id,)).fetchone():
            if campaign_id.startswith(dossier_id + '-c'):
                raise p.Denied('STEP_INCOMPLETE', step='configurations')
            raise p.Denied('Ressource inaccessible')
        snapshot = campaigns.inspect(store, campaign_id)
        if snapshot['task']['dossier_id'] != dossier_id:
            raise p.Denied('Ressource inaccessible')
        requester = snapshot.get('manifest', {}).get('funding') == 'requester'
        if requester:
            p.require_requester_steps(store, p.connection_for(store), session_id, dossier_id,
                                      snapshot['task']['revision'])
        else:
            p.require_qualification(store, p.connection_for(store), dossier_id,
                                    snapshot['task']['revision'])
        if method == 'POST' and action == 'cap':
            value = campaigns.set_cap(
                store, session_id, dossier_id, campaign_id, body,
                access_secret=access_secret, access_transport=access_transport)
            return 200, value, None, None
        if method == 'POST' and action == 'start':
            if not callable(candidate_transport):
                raise p.Denied('Acquisition indisponible')
            attempts = campaigns.launch(store, session_id, dossier_id, campaign_id, body,
                                        access_secret=access_secret, access_transport=access_transport)
            value = campaigns.launch_view(store, session_id, dossier_id, campaign_id,
                                          access_secret=access_secret,
                                          access_transport=access_transport)
            if requester:
                value['launchable'] = False
            else:
                value['can_launch'] = False
            return 202, value, None, {'candidate_attempts': attempts} if attempts else None
        if method == 'GET' and action == 'conditions':
            value = campaigns.launch_view(store, session_id, dossier_id, campaign_id,
                                          access_secret=access_secret,
                                          access_transport=access_transport)
            if requester:
                value['launchable'] = value['launchable'] and callable(candidate_transport)
            else:
                value['can_launch'] = value['can_launch'] and callable(candidate_transport)
            return 200, value, None, None
        raise p.Denied('Action inaccessible')
    if method == 'POST' and path == '/preparation/dossiers':
        body, source_sha256 = p._normalized_submission(body, True)
        operation_id, start = p.submit(store, session_id, body['dossier_id'], body, source, transport,
                                       enforce_limits=True, source_sha256=source_sha256)
        return 202, {'operation_id': operation_id, 'dossier_id': body['dossier_id']}, None, operation_id if start else None
    proof = re.fullmatch(r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})/evaluations/([A-Za-z0-9_-]{1,128})/pieces/([A-Za-z0-9_-]{1,128})', path)
    if method == 'GET' and proof:
        from .evaluation import piece_bytes as evaluation_piece
        return 200, evaluation_piece(store, session_id, *proof.groups()), None, None
    match = re.fullmatch(r'/preparation/dossiers/([A-Za-z0-9_-]{1,128})(?:/(messages|validation)|/revisions/([1-9][0-9]*)(?:/pieces/([A-Za-z0-9_-]{1,128}))?)?', path)
    if not match:
        raise p.Denied('Ressource inaccessible')
    dossier_id, action, revision, piece_id = match.groups()
    p.owner(p.connection_for(store), session_id, dossier_id)
    revision = None if revision is None else int(revision)
    if method == 'GET' and action is None:
        if piece_id:
            return 200, p.piece_bytes(store, session_id, dossier_id, revision, piece_id), None, None
        result = p.view(store, session_id, dossier_id, revision, include_history=True)
        result['availability'] = p.availability(store, transport)
        # The CSRF token travels independently in HTML rendering through the web's session query
        return 200, result, None, None
    if method == 'POST' and action == 'messages':
        body, source_sha256 = p._normalized_submission(body, False)
        operation_id, start = p.submit(store, session_id, dossier_id, body, source, transport,
                                       enforce_limits=True, source_sha256=source_sha256)
        return 202, {'operation_id': operation_id, 'dossier_id': dossier_id}, None, operation_id if start else None
    if method == 'POST' and action == 'validation':
        _fields(body, ('dossier_id', 'revision', 'package_sha256'), 'validation')
        if qualification_transport is not None:
            value, operation_id, start = p.validate_and_qualify(
                store, session_id, dossier_id, body, source, qualification_transport)
            return 202, {**value, 'operation_id': operation_id}, None, {
                'qualification_operation': operation_id} if start else None
        return 200, p.validate(store, session_id, dossier_id, body), None, None
    raise p.Denied('Action inaccessible')
