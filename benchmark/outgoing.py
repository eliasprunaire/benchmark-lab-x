"""Closed, role-specific outgoing content; authorization stays with the caller"""
from hashlib import sha256

from .storage import IntegrityError, _strict_json as encode

FORMAT = 'benchmark-lab-x/outgoing/v1'


def logical_name(value):
    if (type(value) is not str or value in ('', '.', '..') or '/' in value or '\\' in value
            or any(ord(c) < 32 for c in value)):
        raise IntegrityError('Nom logique de pièce requis')
    return value


def text(value):
    if type(value) is not str:
        raise ValueError('Texte sortant requis')
    return value


def texts(values):
    if type(values) is not list:
        raise ValueError('Liste de textes sortants requise')
    return [text(value) for value in values]


def agreements(values):
    if type(values) is not list:
        raise ValueError('Liste d’accords requise')
    result = []
    for value in values:
        if type(value) is str:
            result.append(value)
        elif type(value) is dict and {'question', 'answer'} <= value.keys():
            result.append(dict(question=text(value['question']), answer=text(value['answer'])))
        else:
            raise ValueError('Accord question/réponse requis')
    return result


CANDIDATE_FIELDS = ('instruction', 'deliverables', 'criteria', 'acceptable_ambiguities', 'pieces')
PREPARATION_FIELDS = ('request', 'message', 'reformulation', 'clarifications',
                      'validated_assumptions', 'fictional_parameters', 'previous_candidate')
REVIEW_FIELDS = ('task', 'result_expected', 'obligations', 'eliminatory_errors', 'method',
                 'secondary_criteria', 'limits', 'output', 'references')
REVIEW_TASK_FIELDS = ('instruction', 'deliverables', 'criteria', 'acceptable_ambiguities', 'pieces')


def named_contents(values):
    if type(values) is not list:
        raise ValueError('Pièces requises')
    visible = []
    names = set()
    for piece in values:
        if type(piece) is not dict or set(piece) != {'name', 'content'}:
            raise ValueError('Pièce sortante fermée requise')
        name = logical_name(piece['name'])
        if name in names:
            raise IntegrityError('Nom logique répété')
        names.add(name)
        visible.append(dict(name=name, content=text(piece['content'])))
    return visible


def closed_candidate(content):
    """Copy a candidate view field by field; reject extra keys and host-like names"""
    if type(content) is not dict or set(content) != set(CANDIDATE_FIELDS):
        raise ValueError('Vue candidate fermée requise')
    return dict(instruction=text(content['instruction']), deliverables=texts(content['deliverables']),
                criteria=texts(content['criteria']), acceptable_ambiguities=texts(content['acceptable_ambiguities']),
                pieces=named_contents(content['pieces']))


def candidate(package, pieces):
    """Pieces must already be authorized for this dossier/revision by the caller"""
    indexed = {p['id']: p for p in pieces}
    if len(indexed) != len(pieces) or set(indexed) != {p['id'] for p in package['pieces']}:
        raise IntegrityError('Sélection de pièces divergente')
    visible = []
    names = set()
    for meta in package['pieces']:
        piece = indexed[meta['id']]
        name = logical_name(meta['name'])
        if name in names or piece['role'] != 'candidate' or sha256(piece['content'].encode('utf-8')).hexdigest() != meta['sha256']:
            raise IntegrityError('Pièce candidate non vérifiée')
        names.add(name)
        visible.append(dict(name=name, content=piece['content']))
    return closed_candidate(dict(instruction=package['instruction'], deliverables=package['deliverables'],
                                 criteria=package['criteria'], acceptable_ambiguities=package['acceptable_ambiguities'],
                                 pieces=visible))


def closed_preparation(content):
    """Copy a preparation view field by field; reject the internal request shape"""
    if type(content) is not dict or set(content) != set(PREPARATION_FIELDS):
        raise ValueError('Vue de préparation fermée requise')
    parameters = content['fictional_parameters']
    if type(parameters) is not dict:
        raise ValueError('Paramètres fictifs textuels requis')
    previous = None if content['previous_candidate'] is None else closed_candidate(content['previous_candidate'])
    return dict(request=text(content['request']), message=text(content['message']),
                reformulation=text(content['reformulation']), clarifications=texts(content['clarifications']),
                validated_assumptions=agreements(content['validated_assumptions']),
                fictional_parameters={text(k): text(v) for k, v in parameters.items()}, previous_candidate=previous)


def preparation(request):
    if not {'payload', 'message', 'kind'} <= request.keys():
        raise ValueError('Contexte de préparation incomplet')
    payload = request['payload']
    parameters = payload['fictional_parameters']
    if type(parameters) is not dict:
        raise ValueError('Paramètres fictifs textuels requis')
    previous = candidate(request['package'], request['pieces_seen']) if request.get('package') else None
    return closed_preparation(dict(request=payload['request'], message=request['message'],
                                   reformulation=payload['reformulation'], clarifications=payload['clarifications'],
                                   validated_assumptions=payload['validated_assumptions'],
                                   fictional_parameters=parameters, previous_candidate=previous))


def closed_generation(package):
    """Containers assign roles; the model must not send a role field"""
    if type(package) is not dict or set(package) != {'candidate', 'internal', 'judgment'}:
        raise ValueError('Paquet de génération fermé requis')
    candidate_view = closed_candidate(package['candidate'])
    if not candidate_view['deliverables'] or not candidate_view['criteria'] or not candidate_view['pieces']:
        raise ValueError('Paquet incomplet')
    internal = package['internal']
    if type(internal) is not dict or set(internal) != {'human_work', 'limits'}:
        raise ValueError('Notes internes fermées requises')
    judgment = package['judgment']
    if type(judgment) is not dict or set(judgment) != {'pieces'}:
        raise ValueError('Référence de jugement fermée requise')
    judgment_pieces = named_contents(judgment['pieces'])
    if not judgment_pieces:
        raise ValueError('Pièces requises')
    names = [p['name'] for p in candidate_view['pieces']] + [p['name'] for p in judgment_pieces]
    if len(names) != len(set(names)):
        raise IntegrityError('Nom logique répété')
    return dict(candidate=candidate_view,
                internal=dict(human_work=text(internal['human_work']), limits=texts(internal['limits'])),
                judgment=dict(pieces=judgment_pieces))


def evidence_piece(piece):
    if type(piece) is not dict or set(piece) != {'piece_id', 'name', 'sha256', 'content'}:
        raise ValueError('Pièce de preuve fermée requise')
    piece_id = piece['piece_id']
    if type(piece_id) is not str or not piece_id or '/' in piece_id or '\\' in piece_id:
        raise ValueError('Identifiant de pièce requis')
    digest = piece['sha256']
    if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Empreinte de pièce requise')
    body = text(piece['content'])
    if sha256(body.encode('utf-8')).hexdigest() != digest:
        raise IntegrityError('Empreinte de pièce divergente')
    return dict(piece_id=piece_id, name=logical_name(piece['name']), sha256=digest, content=body)


def closed_review_task(content):
    if type(content) is not dict or set(content) != set(REVIEW_TASK_FIELDS):
        raise ValueError('Tâche de revue fermée requise')
    pieces = [evidence_piece(p) for p in content['pieces']]
    if not pieces:
        raise ValueError('Pièces candidates requises')
    return dict(instruction=text(content['instruction']), deliverables=texts(content['deliverables']),
                criteria=texts(content['criteria']), acceptable_ambiguities=texts(content['acceptable_ambiguities']),
                pieces=pieces)


def closed_review(content):
    """Judgment view field by field; campaign and attempt ids stay with the caller"""
    if type(content) is not dict or set(content) != set(REVIEW_FIELDS):
        raise ValueError('Vue de revue fermée requise')
    obligations = []
    for item in content['obligations']:
        if type(item) is not dict or set(item) != {'id', 'description', 'tolerance', 'control_ids'}:
            raise ValueError('Obligation de revue fermée requise')
        obligations.append(dict(id=text(item['id']), description=text(item['description']),
                                tolerance=text(item['tolerance']), control_ids=texts(item['control_ids'])))
    errors = []
    for item in content['eliminatory_errors']:
        if type(item) is not dict or set(item) != {'id', 'description', 'control_ids'}:
            raise ValueError('Erreur éliminatoire de revue fermée requise')
        errors.append(dict(id=text(item['id']), description=text(item['description']),
                           control_ids=texts(item['control_ids'])))
    method = content['method']
    if type(method) is not dict or set(method) != {'id', 'version', 'control_ids', 'expected_evidence', 'responsible_role'}:
        raise ValueError('Méthode de revue fermée requise')
    measures = []
    for item in content['secondary_criteria']:
        if type(item) is not dict or set(item) != {'id', 'measure', 'proof', 'unit', 'favorable', 'aggregation'}:
            raise ValueError('Critère secondaire de revue fermé requis')
        measures.append(dict(id=text(item['id']), measure=text(item['measure']), proof=text(item['proof']),
                             unit=text(item['unit']), favorable=text(item['favorable']), aggregation=item['aggregation']))
    output = None if content['output'] is None else evidence_piece(content['output'])
    return dict(task=closed_review_task(content['task']), result_expected=text(content['result_expected']),
                obligations=obligations, eliminatory_errors=errors,
                method=dict(id=text(method['id']), version=text(method['version']),
                            control_ids=texts(method['control_ids']),
                            expected_evidence=text(method['expected_evidence']),
                            responsible_role=text(method['responsible_role'])),
                secondary_criteria=measures, limits=texts(content['limits']), output=output,
                references=[evidence_piece(p) for p in content['references']])


def wire_proof(wire, messages):
    return dict(outgoing_format=FORMAT, request_body_sha256=sha256(wire.encode('utf-8')).hexdigest(),
                messages_sha256=sha256(encode(messages).encode('utf-8')).hexdigest())
