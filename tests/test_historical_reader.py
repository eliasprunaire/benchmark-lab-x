"""Lecture de résultats synthétiques scellés, sans acquisition ni installation Pi"""
from copy import deepcopy
from hashlib import sha256
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmark.prototype import __main__ as reader


class HistoricalReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='historical-reader-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'runs').mkdir(mode=0o700)
        self.counter = 0
        self.results = json.loads(Path(__file__).with_name('fixtures').joinpath(
            'historical-results.json').read_text())
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))

    def write(self, path, value):
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode())
        path.chmod(0o600)

    def sealed(self, results=None, historical=False):
        self.counter += 1
        source = self.root / 'runs' / f'source-{self.counter}'
        source.mkdir(mode=0o700)
        value = deepcopy(self.results if results is None else results)
        prefix = 'benchmark-lab-x-v2-alpha' if historical else 'benchmark-lab-x'
        value['schema'] = prefix + '-results-1'
        self.write(source / 'results.json', value)
        self.write(source / 'index.html', b'<p>Page historique deja scellee</p>')
        self.write(source / 'final-seal.json', {
            'schema': prefix + '-final-seal-1', 'run': 'runs/' + source.name,
            'files': {name: sha256((source / name).read_bytes()).hexdigest()
                      for name in ('results.json', 'index.html')}})
        return source

    def render(self, results):
        source = self.sealed(results)
        destination = self.root / 'runs' / f'presentation-{self.counter}'
        reader.present(source, destination, self.root)
        return (destination / 'index.html').read_text()

    def test_read_and_present_need_only_sealed_results(self):
        for historical in (False, True):
            with self.subTest(historical=historical):
                source = self.sealed(historical=historical)
                original = {p.name: p.read_bytes() for p in source.iterdir()}
                destination = self.root / 'runs' / f'presentation-{self.counter}'
                with patch.dict('os.environ', {}, clear=True), patch(
                        'subprocess.run', return_value=Mock(returncode=0)) as opener:
                    self.assertEqual(source / 'index.html', reader.show(source, self.root))
                    reader.present(source, destination, self.root)
                    self.assertEqual(destination / 'index.html', reader.show(destination, self.root))
                self.assertEqual(2, opener.call_count)
                self.assertEqual(original, {p.name: p.read_bytes() for p in source.iterdir()})
                self.assertEqual(original['results.json'], (destination / 'results.json').read_bytes())
                self.assertEqual(0o600, (destination / 'results.json').stat().st_mode & 0o777)
                self.assertEqual(0o700, destination.stat().st_mode & 0o777)
                with self.assertRaises(FileExistsError):
                    reader.present(source, destination, self.root)

    def test_tampered_or_missing_source_blocks_opening(self):
        for missing in (False, True):
            source = self.sealed()
            destination = self.root / 'runs' / f'presentation-{self.counter}'
            reader.present(source, destination, self.root)
            if missing:
                source.rename(source.with_name('moved-' + source.name))
            else:
                (source / 'results.json').write_bytes(b'tampered')
            with patch('subprocess.run') as opener:
                with self.assertRaises((ValueError, FileNotFoundError)):
                    reader.show(destination, self.root)
                opener.assert_not_called()

    def test_present_escapes_text_and_preserves_panel_order(self):
        self.results['configurations'][0]['reason'] = '<script>alert(1)</script>'
        self.results['configurations'].reverse()
        page = self.render(self.results)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script', page)
        self.assertLess(page.index('config-C1'), page.index('config-C2'))
        self.assertLess(page.index('config-C2'), page.index('config-C3'))
        self.assertIn('focus-visible', page)
        self.assertIn('n’est relié à aucune empreinte de source', page)

    def test_unknown_cost_and_failure_do_not_become_an_economic_success(self):
        self.results['configurations'][0]['cost'] = 'INCONNU'
        self.results['configurations'][1].update(verdict='NE SATISFAIT PAS', secondary=None)
        self.results['economy'].update(status='INCOMPLETE', least_expensive=[])
        page = self.render(self.results)
        self.assertIn('Conclusion économique INCOMPLETE', page)
        self.assertIn('Dépense observée, exclue de la recommandation', page)
        self.assertIn('Coût non communiqué : la recommandation reste incomplète', page)
        self.assertNotIn('la moins chère', page)

    def test_paths_outside_runs_and_symlinks_are_refused(self):
        source = self.sealed()
        linked = self.root / 'runs' / 'linked'
        linked.symlink_to(source, target_is_directory=True)
        for path in (linked, self.root / 'outside'):
            with patch('subprocess.run') as opener:
                with self.assertRaises(ValueError):
                    reader.show(path, self.root)
                opener.assert_not_called()

    def test_retired_commands_cannot_start_a_historical_campaign(self):
        for command in ('prepare', 'collect', 'review', 'build'):
            with self.subTest(command=command), redirect_stderr(io.StringIO()), \
                    patch('subprocess.Popen') as launch:
                with self.assertRaises(SystemExit) as stopped:
                    reader.main([command, '--help'])
                self.assertEqual(2, stopped.exception.code)
                launch.assert_not_called()


    def test_human_ui_maps_models_states_costs_and_secondary_criteria(self):
        results = deepcopy(self.results)
        failed = results["configurations"][1]
        failed["verdict"] = "NE SATISFAIT PAS"
        failed["secondary"] = None
        results["economy"] = {
            "status": "COMPLETE",
            "known_costs": {
                results["configurations"][0]["blind_id"]: results["configurations"][0]["cost"],
                results["configurations"][2]["blind_id"]: results["configurations"][2]["cost"],
            },
            "least_expensive": [results["configurations"][0]["blind_id"]],
            "benefits": {},
        }
        results["configurations"].reverse()
        page = self.render(results)
        self.assertLess(page.index('<span class="config-id">C1</span>'), page.index('<span class="config-id">C2</span>'))
        self.assertIn("C1 · openai/gpt-5.6-sol", page)
        self.assertIn("verdict--success", page)
        self.assertIn("verdict--failure", page)
        self.assertIn("Clarté des décisions et des statuts", page)
        self.assertIn("Rigueur sur les montants et les échéances", page)
        self.assertIn("Toutes configurations : dépense observée", page)
        self.assertIn("Hors recommandation : dépense observée", page)
        self.assertIn("Dépense observée, exclue de la recommandation", page)
        self.assertIn("Prise en compte dans la recommandation", page)
        self.assertIn("Aucun incident constaté", page)
        self.assertNotIn("D-8D6C6B58B0DA", page)
        self.assertNotIn("AUCUN", page)
        self.assertNotIn("sans objet", page)

    def fifteen(self, results, verdicts, unknown_cost=None):
        prototype, failed = results["configurations"][0], json.loads(json.dumps(results["configurations"][0]))
        failed["secondary"] = None
        configurations, panel = [], []
        for index, verdict in enumerate(verdicts, 1):
            item = json.loads(json.dumps(prototype if verdict == "SATISFAIT" else failed))
            item["blind_id"] = f"D-{index:012X}"
            item["verdict"] = verdict
            item["reason"] = f"motif synthétique {index}"
            item["requested"]["id"] = f"C{index}"
            item["requested"]["model"] = f"fournisseur-{index}/modele-synthetique-{index}"
            item["observed"]["model"] = item["requested"]["model"]
            item["cost"] = "INCONNU" if index == unknown_cost else index / 1000
            configurations.append(item)
            panel.append(item["requested"])
        results["configurations"] = list(reversed(configurations))
        results["contract"]["panel"] = panel
        satisfied = [item for item in configurations if item["verdict"] == "SATISFAIT"]
        known = {item["blind_id"]: item["cost"] for item in satisfied if item["cost"] != "INCONNU"}
        if unknown_cost and any(item["verdict"] == "SATISFAIT" and item["cost"] == "INCONNU" for item in configurations):
            results["economy"] = {"status": "INCOMPLETE", "known_costs": known, "least_expensive": [], "benefits": {}}
        else:
            results["economy"] = {"status": "COMPLETE", "known_costs": known, "least_expensive": [satisfied[0]["blind_id"]], "benefits": {}}
        return configurations

    def test_fifteen_configurations_keep_two_readable_steps(self):
        results = deepcopy(self.results)
        verdicts = ["SATISFAIT", "NE SATISFAIT PAS", "SATISFAIT", "INDETERMINE"] * 3 + ["SATISFAIT", "SATISFAIT", "NE SATISFAIT PAS"]
        configurations = self.fifteen(results, verdicts)
        page = self.render(results)
        self.assertIn("<title>Synthèse d’un fil de courriels de devis · Salle de décision", page)
        self.assertIn("<h1>Synthèse d’un fil de courriels de devis</h1>", page)
        self.assertEqual(page.count('data-step="'), 2)
        step1 = page[page.index('data-step="1"'):page.index('data-step="2"')]
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        verify = page[page.index('id="verify"'):]
        positions = [step1.index(f'<span class="config-id">C{index}</span>') for index in range(1, 16)]
        self.assertEqual(positions, sorted(positions))
        for index, item in enumerate(configurations, 1):
            self.assertEqual(step1.count(f"<summary>Examiner le résultat C{index}</summary>"), 1)
            self.assertIn(item["reason"], step1)
            self.assertIn(item["verdict"], step1)
            self.assertIn(f"C{index} · fournisseur-{index}/modele-synthetique-{index}", step2)
            self.assertIn(f"<h4>C{index} · fournisseur-{index}/modele-synthetique-{index}</h4>", verify)
        self.assertEqual(step1.count("<summary>"), 15)
        self.assertNotIn("USD", step1)
        self.assertNotIn("Coût", step1)
        self.assertEqual(step2.count("Prise en compte dans la recommandation"), 8)
        self.assertEqual(step2.count("Dépense observée, exclue de la recommandation"), 7)
        self.assertEqual(step2.count('class="bar"'), 15)
        self.assertEqual(step2.count("<li class=\"excluded\">"), 7)
        self.assertIn("La configuration la moins chère parmi celles qui satisfont le contrat est C1 · fournisseur-1/modele-synthetique-1", step2)
        self.assertIn("0,0150 USD", step2)
        self.assertIn("Hors recommandation : dépense observée", step2)
        self.assertIn("Configurations admissibles : dépense observée", step2)
        main_path = page[:page.index('id="verify"')]
        for forbidden in ["Darwin", "arm64", "Python", "sha256", results["conditions"]["observed"]["git_head"], configurations[0]["fingerprints"]["receipt"]]:
            self.assertNotIn(forbidden, main_path)
        for required in ["Darwin", "arm64", results["conditions"]["observed"]["git_head"], configurations[14]["fingerprints"]["raw_jsonl"]]:
            self.assertIn(required, verify)
        self.assertNotIn("<meter", page)
        self.assertNotIn("title=", page)

    def test_unknown_admissible_cost_keeps_incomplete_conclusion_and_shows_every_cost(self):
        results = deepcopy(self.results)
        verdicts = ["SATISFAIT"] * 13 + ["SATISFAIT", "NE SATISFAIT PAS"]
        self.fifteen(results, verdicts, unknown_cost=14)
        page = self.render(results)
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("Conclusion économique INCOMPLETE", step2)
        self.assertNotIn("la moins chère", step2)
        self.assertEqual(step2.count("Non communiqué"), 1)
        self.assertIn("Coût non communiqué : la recommandation reste incomplète", step2)
        self.assertEqual(step2.count("Coût connu ; recommandation suspendue (conclusion INCOMPLETE)"), 13)
        self.assertEqual(step2.count('class="bar bar--none"'), 1)
        self.assertIn("Toutes configurations : dépense connue", step2)
        self.assertIn("1 coût non communiqué sur 15", step2)
        self.assertIn("Configurations admissibles : dépense connue", step2)
        self.assertIn("1 coût non communiqué sur 14", step2)
        self.assertIn("Hors recommandation : dépense observée", step2)
        self.assertNotIn("dépense totale", step2.lower())
        self.assertIn("1 coût non communiqué sans barre", step2)
        # coût inconnu d'une configuration non admissible : visible, exclue, sans effet sur la conclusion
        results["configurations"][-1]["cost"] = "INCONNU"
        results["configurations"][-1]["verdict"] = "NE SATISFAIT PAS"
        page = self.render(results)
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("Coût non communiqué, exclue de la recommandation", step2)
        self.assertIn("Hors recommandation : dépense connue", step2)
        self.assertIn("1 coût non communiqué sur 2", step2)
        self.assertIn("2 coûts non communiqués sur 15", step2)
        self.assertIn("2 coûts non communiqués sans barre", step2)
        # plusieurs coûts inconnus admissibles
        for item in results["configurations"][2:5]:
            item["cost"] = "INCONNU"
        page = self.render(results)
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertEqual(step2.count("Coût non communiqué : la recommandation reste incomplète"), 4)
        self.assertIn("4 coûts non communiqués sur 13", step2)
        self.assertIn("5 coûts non communiqués sur 15", step2)
        # aucun coût connu : ni barre, ni maximum fictif, ni somme
        for item in results["configurations"]:
            item["cost"] = "INCONNU"
        results["economy"] = {"status": "INCOMPLETE", "known_costs": {}, "least_expensive": [], "benefits": {}}
        page = self.render(results)
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertNotIn('class="bar', step2)
        self.assertNotIn("0,0000 USD", step2)
        self.assertNotIn("--w:", step2)
        self.assertIn("Aucun coût communiqué : pas d’échelle commune ni de barre.", step2)
        self.assertEqual(step2.count("Aucun coût communiqué</strong>"), 3)
        self.assertEqual(step2.count("Non communiqué"), 15)

    def test_brief_is_frozen_by_campaign_or_falls_back_to_task_id(self):
        results = deepcopy(self.results)
        results["contract"]["task_id"] = "tache-future"
        results["task"] = "tache-future"
        page = self.render(results)
        self.assertIn("<h1>tache-future</h1>", page)
        self.assertIn("Non documenté : aucun brief figé avant exécution.", page)
        self.assertIn("Cette tâche n’a pas de brief figé avant exécution", page)
        results["contract"]["brief"] = {"title": "Titre <figé>", "context": "Situation figée", "objective": "Objectif figé", "decision": "Décision figée"}
        page = self.render(results)
        self.assertIn("<h1>Titre &lt;figé&gt;</h1>", page)
        for required in ["Situation figée", "Objectif figé", "Décision figée", "<dt>Résultat attendu</dt><dd>Une synthèse fidèle du fil historique selon le contrat G0.</dd>"]:
            self.assertIn(required, page)
        self.assertNotIn("Non documenté", page)
        self.assertNotIn("rédigé après la campagne", page)
        for bad in [{"title": "x"}, {**results["contract"]["brief"], "context": " "}, {**results["contract"]["brief"], "extra": "y"}, {**results["contract"]["brief"], "decision": 3}, {**results["contract"]["brief"], "expected": "remplacement du contrat"}]:
            results["contract"]["brief"] = bad
            with self.assertRaises(ValueError):
                self.render(results)
        results["contract"]["brief"] = None
        results["contract"]["task_id"] = "quote-thread-summary"
        page = self.render(results)
        self.assertIn("Ce brief de présentation a été rédigé après la campagne", page)
        self.assertEqual(page.count("Synthèse d’un fil de courriels de devis"), 2)

    def test_every_engine_incident_has_a_human_label(self):
        results = deepcopy(self.results)
        engine_tokens = {
            "JSONL_INVALIDE: JSON invalide (JSONL ligne 1): Expecting value": "flux de réponse illisible",
            "TOURS_ASSISTANT_INVALIDES": "nombre de tours de réponse invalide",
            "RETRY_DETECTE": "nouvelle tentative détectée",
            "IDENTITE_DIVERGENTE": "identité observée divergente de la demande",
            "RESPONSE_MODEL_DIVERGENT": "modèle de réponse divergent",
            "ARRET_NON_FINAL": "arrêt non final de la réponse",
            "SORTIE_NON_TEXTUELLE": "sortie non textuelle",
            "COUT_INCONNU_OU_INVALIDE": "coût non communiqué par le canal observé",
            "OBSERVATION_NON_TEXTUELLE": "une observation reçue n’était pas un texte",
            "SIGTERM_GROUPE_TUE": "campagne interrompue par le superviseur pendant l’exécution",
            "TIMEOUT_GROUPE_TUE": "durée maximale dépassée, exécution arrêtée",
            "INTERRUPTION_GROUPE_TUE": "exécution interrompue par le dispositif",
            "ERREUR_FOURNISSE": "erreur du fournisseur",
            "PROCESS_START_ERROR": "démarrage du harnais impossible",
        }
        item = results["configurations"][1]
        item["verdict"], item["secondary"] = "INDETERMINE", None
        results["economy"] = {"status": "COMPLETE", "known_costs": {results["configurations"][0]["blind_id"]: results["configurations"][0]["cost"]}, "least_expensive": [results["configurations"][0]["blind_id"]], "benefits": {}}
        for token, label in engine_tokens.items():
            item["incident"] = f"{token};COUT_INCONNU_OU_INVALIDE"
            page = self.render(results)
            self.assertIn(label, page)
            head = token.split(":")[0]
            self.assertNotIn(head, page)
            if head.lower().replace("_", " ") != label:
                self.assertNotIn(head.lower().replace("_", " "), page)
            self.assertNotIn("Expecting value", page)
        item["incident"] = "JETON_FUTUR_INCONNU: détail interne"
        page = self.render(results)
        self.assertIn("incident technique non répertorié", page)
        self.assertNotIn("JETON_FUTUR_INCONNU", page)
        self.assertNotIn("jeton futur inconnu", page)
        self.assertNotIn("détail interne", page)

    def test_ledger_cell_labels_stay_in_the_accessibility_tree_on_desktop(self):
        page = self.render(self.results)
        style = page[page.index("<style>"):page.index("</style>")]
        desktop, mobile = style.split("@media(max-width:760px)")
        desktop_rule = desktop[desktop.index(".ledger .cell-label{"):]
        desktop_rule = desktop_rule[:desktop_rule.index("}")]
        self.assertNotIn("display:none", desktop_rule)
        for required in ["position:absolute", "width:1px", "height:1px", "overflow:hidden", "clip:rect(0 0 0 0)"]:
            self.assertIn(required, desktop_rule)
        self.assertIn(".ledger .cell-label{position:static;width:auto;height:auto;overflow:visible;clip:auto;clip-path:none;white-space:normal;display:block}", mobile)
        self.assertNotIn("display:none", desktop.split(".ledger-head{")[1].split("}")[0])
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertEqual(step2.count('<span class="cell-label">Configuration</span>'), 3)
        self.assertEqual(step2.count('<span class="cell-label">Statut économique</span>'), 3)
        self.assertNotIn("<script", page)
        self.assertNotIn("tabindex", page)

    def test_zero_cost_is_a_communicated_cost(self):
        results = deepcopy(self.results)
        for item in results["configurations"]:
            item["cost"] = 0.0
        results["economy"] = {
            "status": "COMPLETE", "benefits": {},
            "known_costs": {item["blind_id"]: 0.0 for item in results["configurations"]},
            "least_expensive": [item["blind_id"] for item in results["configurations"]],
        }
        page = self.render(results)
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("co-moins-chères", step2)
        self.assertNotIn("Aucun coût communiqué", step2)
        self.assertNotIn("Non communiqué", step2)
        self.assertEqual(step2.count("0,0000 USD"), 3 + 2)
        self.assertIn("Hors recommandation</span><strong>Aucune configuration</strong>", step2)
        self.assertEqual(step2.count('style="--w:0.0%"'), 3)
        self.assertIn("Tous les coûts communiqués sont nuls : barres vides, sans échelle.", step2)
        self.assertIn("Toutes configurations : dépense observée", step2)
        # coût nul parmi des coûts positifs : barre vide à l'échelle du maximum réel
        results["configurations"][1]["cost"] = 0.01
        results["economy"]["known_costs"][results["configurations"][1]["blind_id"]] = 0.01
        results["economy"]["least_expensive"] = [results["configurations"][0]["blind_id"], results["configurations"][2]["blind_id"]]
        page = self.render(results)
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertIn("coût connu le plus élevé (0,0100 USD)", step2)
        self.assertEqual(step2.count('style="--w:0.0%"'), 2)
        self.assertEqual(step2.count('style="--w:100.0%"'), 1)
        # coût nul avec un coût inconnu : le nul reste communiqué, l'inconnu reste sans barre
        results["configurations"][2]["cost"] = "INCONNU"
        results["economy"] = {"status": "INCOMPLETE", "known_costs": {}, "least_expensive": [], "benefits": {}}
        page = self.render(results)
        step2 = page[page.index('data-step="2"'):page.index('id="verify"')]
        self.assertEqual(step2.count("Non communiqué"), 1)
        self.assertEqual(step2.count('class="bar bar--none"'), 1)
        self.assertIn("1 coût non communiqué sans barre", step2)

    def test_unknown_observations_keep_their_human_labels(self):
        results = deepcopy(self.results)
        item = results["configurations"][0]
        item["observed"].update({"provider": "INCONNU", "model": "INCONNU", "responseModel": "INCONNU", "stopReason": "INCONNU"})
        item["unknowns"] = ["provider", "model", "responseModel", "stopReason", "route", "effort"]
        item["incident"] = "OBSERVATION_NON_TEXTUELLE;IDENTITE_DIVERGENTE"
        item["verdict"], item["secondary"] = "INDETERMINE", None
        results["economy"] = {"status": "COMPLETE", "known_costs": {}, "least_expensive": [], "benefits": {}}
        page = self.render(results)
        for forbidden in ["{&#x27;", "[&#x27;", "{&quot;", "[&quot;", "INCONNU", "OBSERVATION_NON_TEXTUELLE", "IDENTITE_DIVERGENTE", "stopReason", "responseModel"]:
            self.assertNotIn(forbidden, page)
        for required in ["Fournisseur non communiqué par le canal observé", "Modèle non communiqué par le canal observé", "Motif d’arrêt non communiqué par le canal observé", "une observation reçue n’était pas un texte et a été remplacée par une absence", "identité observée divergente de la demande"]:
            self.assertIn(required, page)
        self.assertEqual(page.count("<dt>Fournisseur</dt><dd>Non communiqué</dd>"), 1)

    def test_duration_units_preserve_the_exact_limit(self):
        cases = [
            (0, "0 s"), (1, "1 s"), (59, "59 s"), (60, "1 min"),
            (61, "1 min 1 s"), (300, "5 min"), (3599, "59 min 59 s"),
            (3600, "1 h"), (3661, "1 h 1 min 1 s"), (7200, "2 h"),
            (None, "Non communiqué"), ("INCONNU", "Non communiqué"),
            (-1, "Non communiqué"), (True, "Non communiqué"),
        ]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                results = deepcopy(self.results)
                results["conditions"]["requested"]["timeout_seconds"] = seconds
                self.assertIn(f"<dt>Durée maximale</dt><dd>{expected}</dd>", self.render(results))

    def test_provenance_and_offline_page(self):
        page = self.render(self.results)
        self.assertEqual(page.count('data-step="'), 2)
        for forbidden in ["stdout.jsonl", "review-map.json", "fetch(", "XMLHttpRequest", "WebSocket", "http://", "https://"]:
            self.assertNotIn(forbidden, page)
        for required in ["Résultat attendu", "6 obligations", "3 erreurs éliminatoires", "0,50 USD", "min-width:0", "overflow-x:hidden", "focus-visible"]:
            self.assertIn(required, page)


if __name__ == '__main__':
    unittest.main()
