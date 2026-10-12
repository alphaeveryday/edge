import tempfile
import unittest
from unittest.mock import patch
from edge_analysis_v2.tools.graph.store import EvidenceStore


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=EvidenceStore(self.tmp.name,'2026-10-05T09:00:00+09:00')

    def save(self,error=None):
        return self.store.commit('sample',{}, {'dataset':{'items':[1,2,3],'scope':{'complete':True}},
            'selection':{'items':[1,2,3]}},elapsed_ms=1,queries=[],error=error)

    def test_page_does_not_shrink_calculation_dataset(self):
        response=self.save();ref={'tool_run_id':response['tool_run_id'],'path':'/dataset'}
        self.assertEqual(self.store.page(ref,0,1)['items'],[1])
        self.assertEqual(self.store.reference(ref,'dataset')['items'],[1,2,3])
        self.assertFalse(self.store.page(ref,0,1)['page']['complete'])

    def test_cross_analysis_and_kind_substitution_are_rejected(self):
        ref={'tool_run_id':self.save()['tool_run_id'],'path':'/dataset'}
        with self.assertRaises(ValueError):self.store.reference(ref,'selection')
        other=EvidenceStore(self.tmp.name,self.store.cutoff)
        with self.assertRaises(ValueError):other.reference(ref,'dataset')

    def test_failed_write_cannot_create_reusable_evidence(self):
        with patch('edge_analysis_v2.tools.graph.store.os.replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.save()
        self.assertEqual(self.store.calls,[])
        self.assertEqual(list(self.store.directory.glob('*')),[])

    def test_error_run_and_mutated_return_cannot_change_saved_evidence(self):
        failed=self.save('source unavailable')
        with self.assertRaises(ValueError):self.store.reference({'tool_run_id':failed['tool_run_id'],'path':'/dataset'},'dataset')
        response=self.save();response['result']['dataset']['items'].append(4)
        self.assertEqual(self.store.reference({'tool_run_id':response['tool_run_id'],'path':'/dataset'},'dataset')['items'],[1,2,3])
