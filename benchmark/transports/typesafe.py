"""Échange TypeSafe System One : une requête, aucun rejeu implicite.

La clé reste dans l'en-tête HTTP et n'entre ni dans le corps, ni dans le reçu.
Le délai est celui de l'aide sémantique ; le SDK n'est pas utilisé, ce qui
supprime ses reprises automatiques.
"""
from datetime import datetime, timezone
from http.client import HTTPSConnection, IncompleteRead
import time

from ..semantic_help import ENDPOINT, MAX_RESPONSE_BYTES, TIMEOUT_SECONDS

HOST = 'api.typesafe.ai'
PATH = '/v1/systemone'


class NotSent(OSError):
    """Connexion impossible (DNS, refus, TLS) : aucun octet de la requête n'est parti"""


def validate_key(api_key) -> str:
    if (type(api_key) is not str or not api_key or not api_key.isascii()
            or any(character.isspace() or ord(character) < 32 for character in api_key)):
        raise ValueError('Clé TypeSafe explicite requise côté exécuteur')
    return api_key


class TypeSafeSystemOne:
    """Transport lié à la clé de l'exécuteur, sans alias ni fournisseur de secours"""

    def __init__(self, api_key, *, timeout=TIMEOUT_SECONDS):
        self._key = validate_key(api_key)
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or not 0 < timeout <= TIMEOUT_SECONDS:
            raise ValueError('Délai TypeSafe invalide')
        self._timeout = timeout

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Délai TypeSafe dépassé')
        return remaining

    def post(self, wire: str) -> dict:
        if type(wire) is not str or not wire:
            raise ValueError('Corps TypeSafe requis')
        if self._key in wire:
            raise ValueError('Clé présente dans le corps sortant')
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        deadline = clock + self._timeout
        connection = HTTPSConnection(HOST, timeout=self._timeout)
        try:
            connection.timeout = self._remaining(deadline)
            try:
                connection.connect()
            except OSError as error:
                raise NotSent('Connexion à TypeSafe impossible') from error
            sock = connection.sock
            if sock is not None:
                sock.settimeout(self._remaining(deadline))
            connection.request('POST', PATH, body=wire.encode(), headers={
                'Authorization': 'Bearer ' + self._key,
                'Content-Type': 'application/json',
                'Accept': 'application/json'})
            if sock is not None:
                sock.settimeout(self._remaining(deadline))
            response = connection.getresponse()
            status = response.status
            chunks, size, complete = [], 0, True
            try:
                while True:
                    if sock is not None:
                        sock.settimeout(self._remaining(deadline))
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        complete = False
                        break
                if size > MAX_RESPONSE_BYTES:
                    raw = b''.join(chunks)[:MAX_RESPONSE_BYTES]
                else:
                    raw = b''.join(chunks)
                    complete = complete and response.length in (None, 0)
                    if (getattr(response, 'chunked', False)
                            and getattr(response, 'chunk_left', None) is not None):
                        complete = False
            except IncompleteRead as error:
                raw = b''.join(chunks + [error.partial])[:MAX_RESPONSE_BYTES]
                complete = False
            if time.monotonic() > deadline:
                raise TimeoutError('Délai TypeSafe dépassé')
        finally:
            connection.close()
        # Une clé réfléchie ne peut pas entrer dans un reçu ni dans une réponse exploitable
        if self._key.encode() in raw:
            raw = b''
            complete = False
        return {'status': status, 'body': raw, 'complete': complete,
                'endpoint': ENDPOINT, 'started_at': started,
                'elapsed_seconds': time.monotonic() - clock}

