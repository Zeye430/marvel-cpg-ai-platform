"""Data-delivery CLI dispatch and boundary errors; no client data."""

import io
import json
import unittest
from pathlib import Path
from contextlib import redirect_stderr,redirect_stdout
from unittest.mock import patch
from data_platform.__main__ import main


class DeliveryCliTests(unittest.TestCase):
    def invoke(self,args):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            try: code=main(args)
            except SystemExit as error: code=error.code
        return code,out.getvalue(),err.getvalue()

    def test_all_new_help_commands_work_without_execution(self):
        for command in ('build-features','build-labels','build-serving','build-all','publish-serving','rollback-serving'):
            with self.subTest(command=command):
                code,out,err=self.invoke([command,'--help'])
                self.assertEqual(code,0);self.assertIn('usage:',out);self.assertEqual(err,'')

    def test_features_and_labels_dispatch(self):
        for command,module,method in (('build-features','feature_runner','build_features'),('build-labels','labels','build_labels')):
            with self.subTest(command=command),patch(f'data_platform.{module}.{method}',return_value={'status':'Accepted'}) as called:
                code,out,_=self.invoke([command,'--source-build-dir','source','--as-of','2026-05-31'])
                self.assertEqual(code,0);self.assertEqual(json.loads(out)['status'],'Accepted')
                self.assertEqual(str(called.call_args.kwargs['as_of']),'2026-05-31')
        with patch('data_platform.feature_runner.build_features',return_value={'status':'Accepted'}) as called:
            self.assertEqual(self.invoke(['build-features','--source-build-dir','source','--as-of','2026-06-30','--purpose','scoring'])[0],0)
            self.assertEqual(called.call_args.kwargs['purpose'],'scoring')
        with patch('data_platform.labels.build_labels',return_value={'status':'Accepted'}) as called:
            self.assertEqual(self.invoke(['build-labels','--source-build-dir','source','--as-of','2026-06-30','--purpose','test','--test-release','release.json'])[0],0)
            self.assertEqual(called.call_args.kwargs['purpose'],'test')
            self.assertEqual(called.call_args.kwargs['test_release'],Path('release.json'))

    def test_serving_dispatch_requires_matching_feature_input(self):
        self.assertEqual(self.invoke(['build-serving','--source-build-dir','source'])[0],2)
        with patch('data_platform.serving.build_serving',return_value={'status':'Accepted'}) as called:
            self.assertEqual(self.invoke(['build-serving','--source-build-dir','source','--feature-build-dir','features','--publish'])[0],0)
            self.assertTrue(called.call_args.kwargs['publish'])

    def test_complete_workflow_flags_are_explicit(self):
        with patch('data_platform.workflow.build_all',return_value={'status':'Accepted'}) as called:
            self.assertEqual(self.invoke(['build-all','--as-of','2026-05-31','--assume-complete-snapshot','--publish'])[0],0)
            self.assertTrue(called.call_args.kwargs['assume_complete_snapshot'])
            self.assertTrue(called.call_args.kwargs['publish'])

    def test_publish_and_rollback_dispatch(self):
        with patch('data_platform.serving.publish_serving',return_value={'publication_id':'a'*32}):
            self.assertEqual(self.invoke(['publish-serving','--output-root','out','--artifact-dir','artifact'])[0],0)
        with patch('data_platform.serving.rollback_serving',return_value={'publication_id':'b'*32}):
            self.assertEqual(self.invoke(['rollback-serving','--output-root','out','--publication-id','a'*32])[0],0)

    def test_failures_are_nonzero_and_do_not_report_success(self):
        with patch('data_platform.workflow.build_all',side_effect=RuntimeError('fixture quality failure')):
            code,out,err=self.invoke(['build-all','--as-of','2026-05-31'])
            self.assertEqual(code,1);self.assertEqual(out,'');self.assertIn('accepted publications unchanged',err)
        self.assertEqual(self.invoke(['build-all','--as-of','invalid'])[0],2)


if __name__=='__main__':unittest.main()
