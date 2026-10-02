"""Garde des harnais de test : aucune connexion hors de la machine, aucune attente infinie

Deux usages du même fichier. Dans un interpréteur enfant, Python l'importe au démarrage quand ce
dossier figure dans PYTHONPATH : la garde s'arme si BENCHMARK_TEST_NETWORK_JOURNAL nomme le journal
partagé. Dans le processus des tests, `garder_module()` sert de setUpModule : il arme la garde pour
le module et ses enfants Python, plus un délai global qui imprime la pile de tous les fils.

Une connexion non locale est refusée par une OSError, que le produit traite comme une panne
réseau, et consignée : la fin du module échoue si le journal n'est pas vide. Les enfants non
Python (Node, Pi) ne sont pas couverts.
"""
import faulthandler
import ipaddress
import os
from pathlib import Path
import socket
import sys
import tempfile

VARIABLE = 'BENCHMARK_TEST_NETWORK_JOURNAL'
DOSSIER = str(Path(__file__).resolve().parent)
_installee = False


def _locale(host):
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode(errors='replace')
    host = str(host)
    if host in ('', 'localhost'):
        return True
    try:
        return ipaddress.ip_address(host.split('%')[0]).is_loopback
    except ValueError:
        return False


def _surveiller(event, args):
    journal = os.environ.get(VARIABLE)
    if not journal:
        return
    if event == 'socket.getaddrinfo':
        host, port = args[0], args[1]
    elif event == 'socket.connect':
        if args[0].family == getattr(socket, 'AF_UNIX', None) or type(args[1]) is not tuple:
            return
        host, port = args[1][0], args[1][1]
    else:
        return
    if _locale(host):
        return
    with open(journal, 'a', encoding='utf-8') as stream:
        stream.write(f'{os.getpid()} {event} {host}:{port}\n')
    raise OSError(f'Réseau non local interdit en test : {host}:{port}')


def installer():
    """Ajoute le crochet d'audit, une fois par interpréteur ; inactif sans journal"""
    global _installee
    if not _installee:
        sys.addaudithook(_surveiller)
        _installee = True


def garder_module(delai=600):
    """setUpModule commun : garde réseau du module et de ses enfants, pile imprimée après `delai` s"""
    import unittest
    descripteur, journal = tempfile.mkstemp(prefix='reseau-')
    os.close(descripteur)
    precedent = {nom: os.environ.get(nom) for nom in (VARIABLE, 'PYTHONPATH')}
    os.environ[VARIABLE] = journal
    os.environ['PYTHONPATH'] = os.pathsep.join(
        [DOSSIER] + ([precedent['PYTHONPATH']] if precedent['PYTHONPATH'] else []))
    installer()
    # Un blocage imprime la pile de tous les fils puis arrête le processus, au lieu d'attendre sans fin
    faulthandler.dump_traceback_later(delai, exit=True)

    def verifier():
        faulthandler.cancel_dump_traceback_later()
        for nom, valeur in precedent.items():
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur
        tentatives = Path(journal).read_text(encoding='utf-8').splitlines()
        Path(journal).unlink()
        if tentatives:
            raise AssertionError(f'{len(tentatives)} connexions non locales tentées :\n' + '\n'.join(tentatives))
    unittest.addModuleCleanup(verifier)


if os.environ.get(VARIABLE):
    installer()
