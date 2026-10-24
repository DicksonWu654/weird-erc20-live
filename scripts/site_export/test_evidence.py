"""Revised authority evidence must keep its own call IDs and retrieval times."""
import json
import unittest
from export import ROOT, load_control_index, forward_evidence


class ForwardEvidenceTests(unittest.TestCase):
    def test_forward_overlay_uses_supplemental_envelopes(self):
        row = load_control_index('ethereum')['0xdac17f958d2ee523a2206206994597c13d831ec7']
        result = forward_evidence(row)
        envelopes = [json.loads(line) for relative in result['raw']
                     for line in (ROOT / relative).read_text().splitlines()]
        calls = {r['id']:r for r in envelopes}
        for call in ('e7','e11','e12'):
            self.assertEqual(result['call_hashes'][call], calls[call]['sha256'])
        self.assertEqual(result['block'], row['legacy_forward']['block'])
        self.assertIn(calls['e12']['retrieved_at'], result['retrieved_at'])
        self.assertNotEqual(result['retrieved_at'], row['probed_at'])
        self.assertIn('result', calls['e11']['response'])
        self.assertIn('error', calls['e12']['response'])


if __name__ == '__main__':
    unittest.main()
