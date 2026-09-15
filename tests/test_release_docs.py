import json,unittest,xml.etree.ElementTree as ET
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cost_scenario import calculate
class ReleaseTests(unittest.TestCase):
    def test_readme_matches_implementation(self):
        text=Path('README.md').read_text()
        for value in ['256','605','EIP-191','ContinuityRegistry.sol','hash chain'] :self.assertIn(value,text)
        self.assertNotIn('contracts/SynaFlyRegistry.sol',text)
        self.assertNotIn('EIP-712 typed signature verification',text)
        self.assertNotIn('Merkle-tree state commitments',text)
    def test_public_docs_do_not_promote_a_token_contract(self):
        for p in [Path('README.md'),*Path('docs').rglob('*.md')]:
            text=p.read_text().lower()
            self.assertNotRegex(text,r'https?://(?:www\.)?bscscan\.com/token/0x[0-9a-f]{40}')
            for phrase in ['project-designated bsc token','project token listed','creator-tax']:
                self.assertNotIn(phrase,text,str(p))
    def test_diagrams_have_no_active_or_remote_content(self):
        for p in Path('docs/diagrams').glob('*.svg'):
            text=p.read_text();ET.fromstring(text)
            for forbidden in ['<script','<foreignObject','href=','/Users/']:self.assertNotIn(forbidden,text)
        self.assertEqual(len(list(Path('docs/diagrams').glob('*.svg'))),3)
    def test_example_is_a_labeled_scenario(self):
        result=calculate(json.loads(Path('data/cost-scenario-example.json').read_text()))
        self.assertEqual(result['net_annual'],'40000.00');self.assertEqual(result['kind'],'illustrative_scenario_not_measured')
    def test_cost_inputs_reject_invalid_rates(self):
        values=json.loads(Path('data/cost-scenario-example.json').read_text())
        for bad in ['1.1','-0.1','NaN']:
            with self.assertRaises(ValueError):calculate({**values,'offload_share':bad})
    def test_public_results_do_not_fingerprint_host(self):
        for p in Path('results').glob('*.json'):
            if p.name=='source-manifest.json':continue
            text=p.read_text()
            for word in ['Darwin','arm64','/Users/','median_ms','all_wall_times_ms','recovery_ms']:self.assertNotIn(word,text)
