"""Régressions de mémoire sur données synthétiques, sans appel modèle"""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from benchmark import publications, runtime, storage
from benchmark.transports import openrouter, pi
from tests.test_storage import PAYLOAD, cost, operation, receipt


def measured(call):
    tracemalloc.start()
    try:
        result = call()
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def public_fixture(root, size):
    files = {'index.html': b'<p>Fictif</p>', 'style.css': b'body{}', 'piece-p.txt': b'x' * size}
    fingerprints = {name: sha256(raw).hexdigest() for name, raw in files.items()}
    manifest = storage._strict_json(dict(
        schema_version=publications.SCHEMA, presentation_version=publications.PRESENTATION_VERSION,
        conclusion_version='1', campaign_id='synthetic', contract_sha256='a' * 64,
        task=dict(dossier_id='d', revision=1, version=1, package_sha256='b' * 64),
        evaluation_ids=[], limits=['Données fictives de test'], files=fingerprints,
        pieces={'p': {'file': 'piece-p.txt', 'sha256': fingerprints['piece-p.txt']}})).encode()
    identity = sha256(manifest).hexdigest()
    publications.materialize(dict(manifest=manifest, projection_sha256=identity, files=files),
                            dict(actor='approbateur-fictif-S6', authority_id='TEST_ONLY_PUBLICATION_S6',
                                 projection_sha256=identity, catalogue=False), root)
    return identity


class ResourceUsageTests(unittest.TestCase):
    def test_public_file_does_not_retain_other_pieces_but_checks_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            size = 8 * 1024 * 1024
            identity = public_fixture(root, size)
            raw, peak = measured(lambda: publications.public_bytes(root, identity, 'style.css'))
            self.assertEqual(b'body{}', raw)
            self.assertLess(peak, size // 4)
            with (root / identity / 'piece-p.txt').open('r+b') as stream:
                stream.write(b'y')
            with self.assertRaisesRegex(ValueError, 'divergents'):
                publications.public_bytes(root, identity, 'style.css')

    def test_oversized_profile_is_rejected_without_loading_it_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile.json'
            size = 8 * 1024 * 1024
            path.write_bytes(b' ' * size)

            def reject():
                with self.assertRaisesRegex(ValueError, 'hors limites'):
                    openrouter.load_profile(str(path))

            _, peak = measured(reject)
            self.assertLess(peak, size // 4)

    def test_storage_verification_streams_pieces_and_still_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'private'
            storage.initialize(root)
            with closing(storage.Store(root)) as store:
                store.save_dossier('d', 1, PAYLOAD)
                raw = b'x' * (8 * 1024 * 1024)
                meta = store.put_piece('d', 1, 'piece', name='synthetic', role='candidate',
                                       media_type='text/plain', content=raw)
                self.assertEqual(raw, store.read_piece('piece'))
                result, peak = measured(lambda: runtime.verify(store))
                self.assertTrue(result['integrity_ok'])
                self.assertLess(peak, len(raw) // 4)
                with (root / meta['relative_path']).open('r+b') as stream:
                    stream.write(b'y')
                with self.assertRaises(storage.IntegrityError):
                    runtime.verify(store)

    def test_status_does_not_keep_both_raw_and_decoded_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'private'
            storage.initialize(root)
            with closing(storage.Store(root)) as store:
                store.save_dossier('d', 1, PAYLOAD)
                store.create_budget('budget', '100', 'TEST')
                count, size = 16, 256 * 1024
                for index in range(count):
                    oid = f'op-{index}'
                    store.reserve_intent(operation(oid), 'budget', '1')
                    store.mark_emission_possible(oid)
                    value = receipt()
                    value['result'] = {'output': 'x' * size}
                    store.record_receipt(oid, value, cost('0.01'))
                result, peak = measured(lambda: runtime.status(root, store))
                self.assertEqual({'RECEIVED': count}, result['operations'])
                self.assertLess(peak, count * size * 3 // 2)

    def test_backup_hashes_large_files_with_bounded_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            piece = root / 'piece'
            raw = b'x' * (8 * 1024 * 1024)
            piece.write_bytes(raw)
            piece.chmod(0o600)
            expected = sha256(raw).hexdigest()
            result, peak = measured(lambda: runtime.hashes(root))
            self.assertEqual({'piece': expected}, result)
            self.assertLess(peak, len(raw) // 4)

    def test_pi_identity_hashes_large_files_without_loading_them_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('pi-coding-agent', 'pi-agent-core', 'pi-ai'):
                package = root / name
                (package / 'dist').mkdir(parents=True)
                (package / 'package.json').write_text(json.dumps(
                    {'name': '@earendil-works/' + name, 'version': pi.VERSION}))
                (package / 'dist/index.js').write_text('export const fixture = true;')
            package = root / 'pi-coding-agent'
            (package / 'npm-shrinkwrap.json').write_text('{}')
            binary = root / 'node'
            raw = b'x' * (8 * 1024 * 1024)
            binary.write_bytes(raw)
            expected = sha256(raw).hexdigest()
            with patch.object(pi.subprocess, 'check_output', return_value=b'v0-fixture\n'):
                identity, peak = measured(lambda: pi.identity(package, binary))
            self.assertEqual(expected, identity['node_sha256'])
            self.assertEqual('v0-fixture', identity['node_version'])
            self.assertLess(peak, len(raw) // 4)


if __name__ == '__main__':
    unittest.main()
