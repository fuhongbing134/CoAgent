"""Real SQLite + temporary Git/bare integration; scripted Agent transport only.

No production database, browser, credentials or network is used by these tests.
"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from collaboration_protocol import HANDOFF_FIELDS, validate_payload, validate_plan
from collaboration_service import CollaborationService
from git_feature_workflow import GitFeatureWorkflow, FeaturePaused
from git_automation import GitAutomationError
from execution_tracking import ExecutionTracker


def reply(**extra):
    return dict(protocol_version=1,status='completed',questions=[],
                handoff={k:'none' for k in HANDOFF_FIELDS},**extra)


def wire(payload):
    return 'Result\n```agent-collaboration\n' + json.dumps(payload) + '\n```'


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.repo=root/'repo'
        self.repo.mkdir()
        self.remote=root/'remote.git'
        self.git('init','-b','main')
        for key,value in [('user.name','Test'),('user.email','test@example.invalid'),
                          ('commit.gpgsign','false'),('core.autocrlf','false')]:
            self.git('config',key,value)
        (self.repo/'a.txt').write_text('base\n')
        self.git('add','a.txt')
        self.git('commit','-m','base')
        self.base=self.git('rev-parse','HEAD')
        subprocess.run(['git','init','--bare',str(self.remote)],check=True,capture_output=True)
        self.git('remote','add','origin',str(self.remote))
        self.codes=0
        self.reviews=0
        self.approved=True
        self.change_during_review=False
        self.service=CollaborationService(root/'test.db',self.invoke,lambda _:True,
            git_factory=lambda store:GitFeatureWorkflow(store,lambda _:self.repo))
        self.plan=dict(owner_id='codex',final_responder_id='codex',tasks=[
            dict(task_id='code',agent_id='codex',instruction='Implement',depends_on=[],git_stage='code')])
        self.start('job',self.plan)
        self.workflow=self.service.feature_workflow()

    def git(self,*args):
        return subprocess.run(['git','-C',str(self.repo),*args],check=True,
                              capture_output=True).stdout.decode().strip()

    def start(self,job,plan):
        self.service.store.create(job,'Feature request',plan)
        self.service.store.initialize(job,round_limit=30)
        self.service.store.bind(job,'room-'+job)

    def invoke(self,job,agent,role,prompt,**kwargs):
        if 'git_review=' in prompt:
            self.reviews+=1
            feature=self.workflow.get(job)
            if self.change_during_review:
                (self.repo/'a.txt').write_text('external edit\n')
            result=reply(git_review=dict(review_id=feature['review_id'],approved=self.approved,issues='none'))
        elif role == 'owner':
            result=reply()
        else:
            self.codes+=1
            (self.repo/'a.txt').write_text('revision '+str(self.codes)+'\n')
            result=reply()
        return 'completed',wire(result)

    def task(self,index=0):
        return copy.deepcopy(self.plan['tasks'][index])

    def test_host_chain_from_plan_runs_and_pushes(self):
        self.service.drive('job')
        feature=self.workflow.get('job')
        self.assertEqual(feature['state'],'succeeded')
        self.assertEqual((self.codes,self.reviews),(1,1))
        self.assertEqual(self.service.store.snapshot('job')['phase'],'completed')
        self.assertEqual(self.git('rev-parse','main'),self.base)
        self.assertEqual(self.git('ls-remote','origin','refs/heads/'+feature['branch']).split()[0],feature['base_commit'])
        record=self.workflow.reviews.read(feature['review_id'])
        self.assertNotEqual(record['reviewer'],feature['author'])
        self.assertEqual(record['operation']['commit_id'],feature['base_commit'])

    def test_missing_git_is_visible_before_author_runs(self):
        from git_automation import GitAutomationError
        self.service.git_workflow=None
        with patch.object(self.service,'git_factory',side_effect=GitAutomationError('Git unavailable')):
            self.service.drive('job')
        self.assertEqual(self.codes,0)
        state=self.service.store.snapshot('job')
        self.assertEqual(state['phase'],'blocked')
        self.assertIn('Git host unavailable',state['reason'])

    def test_http_actions_require_origin_token_and_bound_conversation(self):
        from git_feature_http import handle_git_action
        class Handler:
            server=SimpleNamespace(server_port=1234)
            def send_json(handler,code,payload):
                handler.response=(code,payload)
        for origin,token,conversation,expected in [
                ('http://evil.invalid','secret','room-job',403),
                ('http://localhost:1234','wrong','room-job',403),
                ('http://localhost:1234','secret','other-room',400),
                ('http://localhost:1234','secret','room-job',202)]:
            handler=Handler()
            body=json.dumps(dict(job_id='job',conversation_id=conversation,action='retry')).encode()
            handler.headers={'Host':'localhost:1234','Origin':origin,'X-Capability-Token':token,
                             'Content-Type':'application/json','Content-Length':str(len(body))}
            handler.rfile=io.BytesIO(body)
            with patch('git_feature_http.threading.Thread') as thread:
                handle_git_action(handler,self.service,'secret')
                self.assertEqual(handler.response[0],expected)
                self.assertEqual(thread.call_count,int(expected==202))

    def test_remote_choice_is_reused_across_conversations(self):
        self.git('remote','add','secondary',str(self.remote))
        with self.assertRaises(FeaturePaused):
            self.workflow.before_task('job',self.task())
        self.workflow.user_action('job','room-job','remote','secondary')
        self.start('next',self.plan)
        self.assertEqual(self.workflow.before_task('next',self.task()),'run')
        self.assertEqual(self.workflow.get('next')['remote'],'secondary')

    def test_changed_branch_is_rejected_before_next_author_runs(self):
        self.workflow.before_task('job',self.task())
        feature=self.workflow.get('job')
        feature['state']='rejected'
        self.workflow.save('job',feature)
        self.git('switch','main')
        revise={**self.task(),'task_id':'revise','git_stage':'revise'}
        with self.assertRaisesRegex(FeaturePaused,'before coding'):
            self.workflow.before_task('job',revise)

    def test_failed_hook_revision_keeps_owned_index_and_requires_new_review(self):
        self.workflow.before_task('job',self.task())
        (self.repo/'a.txt').write_text('first attempt\n')
        hook=self.repo/'.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        with self.assertRaises(FeaturePaused):
            self.workflow.finish_task(self.service,'job',self.task())
        old=self.workflow.get('job')['review_id']
        self.assertEqual(self.git('rev-parse','HEAD'),self.base)
        self.assertEqual(self.git('diff','--cached','--name-only'),'a.txt')
        revise={**self.task(),'task_id':'fix-hook','git_stage':'revise'}
        self.assertEqual(self.workflow.before_task('job',revise,self.service),'run')
        hook.unlink()
        (self.repo/'a.txt').write_text('reviewed fix\n')
        self.assertTrue(self.workflow.finish_task(self.service,'job',revise))
        feature=self.workflow.get('job')
        self.assertNotEqual(feature['review_id'],old)
        self.assertEqual(len(feature['rounds']),2)
        self.assertEqual(self.reviews,2)
        self.assertNotIn('revision_index_tree',feature)

    def test_retry_requeues_failed_original_push_without_replaying_coding(self):
        from git_automation import GitAutomationError
        self.workflow.before_task('job',self.task())
        (self.repo/'a.txt').write_text('pending push\n')
        original=self.workflow.api._remote
        def broken(context):
            if self.workflow.reviews.read(self.workflow.get('job')['review_id']).get('operation'):
                raise GitAutomationError('network')
            return original(context)
        with patch.object(self.workflow.api,'_remote',side_effect=broken):
            with self.assertRaises(FeaturePaused):
                self.workflow.finish_task(self.service,'job',self.task())
        commit=self.git('rev-parse','HEAD')
        with self.service.store.transaction() as db:
            db.execute("UPDATE collab_tasks SET state='failed' WHERE job_id='job'")
        self.workflow.user_action('job','room-job','retry')
        self.service.drive('job')
        self.assertEqual(self.git('rev-parse','HEAD'),commit)
        self.assertEqual(self.codes,0)
        self.assertEqual(self.workflow.get('job')['state'],'succeeded')

    def test_three_authors_each_use_the_same_host_chain(self):
        for number,author in enumerate(('codex','deepseek-harness','grok')):
            job='author-'+str(number)
            plan=copy.deepcopy(self.plan)
            plan.update(owner_id=author,final_responder_id=author)
            plan['tasks'][0]['agent_id']=author
            self.start(job,plan)
            self.service.drive(job)
            feature=self.workflow.get(job)
            self.assertEqual(feature['state'],'succeeded')
            self.assertNotEqual(self.workflow.reviews.read(feature['review_id'])['reviewer'],author)
        self.assertEqual(self.git('rev-parse','main'),self.base)

    def test_multi_round_and_restart_do_not_repeat_commits(self):
        task=self.task()
        self.assertEqual(self.workflow.before_task('job',task),'run')
        (self.repo/'a.txt').write_text('first\n')
        self.assertTrue(self.workflow.finish_task(self.service,'job',task))
        first=self.git('rev-parse','HEAD')
        reloaded=GitFeatureWorkflow(self.service.store,lambda _:self.repo)
        self.assertEqual(reloaded.before_task('job',task),'recover')
        self.assertTrue(reloaded.finish_task(self.service,'job',task))
        self.assertEqual(self.git('rev-parse','HEAD'),first)
        second={**task,'task_id':'second','git_stage':'revise'}
        self.assertEqual(reloaded.before_task('job',second),'run')
        (self.repo/'a.txt').write_text('second\n')
        self.assertTrue(reloaded.finish_task(self.service,'job',second))
        feature=reloaded.get('job')
        self.assertEqual(len(feature['rounds']),2)
        self.assertEqual(self.git('rev-parse','HEAD^'),first)

    def test_reject_and_changed_content_require_new_review(self):
        task=self.task()
        self.workflow.before_task('job',task)
        (self.repo/'a.txt').write_text('code\n')
        self.approved=False
        self.assertFalse(self.workflow.finish_task(self.service,'job',task))
        self.assertEqual(self.git('rev-parse','HEAD'),self.base)
        task.update(task_id='fix',git_stage='revise')
        self.workflow.before_task('job',task)
        (self.repo/'a.txt').write_text('fixed\n')
        self.approved=True
        self.change_during_review=True
        self.assertFalse(self.workflow.finish_task(self.service,'job',task))
        self.assertEqual(self.git('rev-parse','HEAD'),self.base)
        self.assertEqual(self.reviews,2)

    def test_dirty_workspace_preserved_and_cross_conversation_queue(self):
        (self.repo/'unrelated.txt').write_text('keep')
        with self.assertRaisesRegex(FeaturePaused,'Unattributed'):
            self.workflow.before_task('job',self.task())
        self.assertEqual(self.git('branch','--show-current'),'main')
        self.start('other',self.plan)
        self.assertEqual(self.workflow.before_task('other',self.task()),'queued')
        self.assertEqual((self.repo/'unrelated.txt').read_text(),'keep')

    def test_stop_wins_before_any_new_git_process(self):
        self.workflow.before_task('job',self.task())
        branch=self.workflow.get('job')['branch']
        self.service.store.operate('job','room-job','stop-id','stop')
        with patch('git_automation.subprocess.Popen') as launch:
            with self.assertRaises(FeaturePaused):
                self.workflow.api._text(self.workflow.resolve('branch_'+self.workflow.get('job')['feature_id']), 'rev-parse','HEAD')
            launch.assert_not_called()
        self.assertEqual(self.git('branch','--show-current'),branch)

    def test_unknown_exit_blocks_branch_creation(self):
        tracker,token=self.execution_run()
        tracker.mark_running(token)
        tracker.finish_run(token,outcome='failed',exit_evidence='unknown')
        with self.assertRaisesRegex(FeaturePaused,'exit/release'):
            self.workflow.before_task('job',self.task())
        self.assertEqual(self.git('branch','--show-current'),'main')

    def execution_run(self, conversation='tracked'):
        tracker=ExecutionTracker(self.service.store.path)
        token=tracker.begin_run(agent_id='codex',conversation_id=conversation,
                                instance_id='test-instance',source_kind='collaboration',
                                source_job_id='job',config_snapshot={})
        return tracker,token

    def test_released_prestart_failure_cancel_and_confirmed_allow_git(self):
        for outcome in ('failed','cancelled'):
            tracker,token=self.execution_run(outcome)
            if outcome=='cancelled': tracker.request_cancel(token)
            tracker.finish_run(token,outcome=outcome,exit_evidence='not_started')
        tracker,token=self.execution_run('confirmed')
        tracker.mark_running(token)
        tracker.finish_run(token,outcome='completed',exit_evidence='confirmed')
        self.assertEqual(self.workflow.before_task('job',self.task()),'run')
        self.service.store.interrupt_running()
        self.workflow.user_action('job','room-job','retry')
        self.assertEqual(self.service.store.snapshot('job')['phase'],'working')

    def test_pending_running_and_other_owner_in_same_domain_block_git(self):
        tracker,token=self.execution_run()
        for running in (False,True):
            if running: tracker.mark_running(token)
            with self.assertRaisesRegex(FeaturePaused,'exit/release'):
                self.workflow.before_task('job',self.task())
        tracker.finish_run(token,outcome='failed',exit_evidence='confirmed')
        tracker.begin_run(agent_id='codex',conversation_id='tracked',instance_id='other',
                          source_kind='collaboration',source_job_id='other-job',config_snapshot={})
        with self.assertRaisesRegex(FeaturePaused,'exit/release'):
            self.workflow.before_task('job',self.task())

    def test_interrupted_retry_still_rejects_unknown_execution(self):
        self.workflow.before_task('job',self.task())
        tracker,token=self.execution_run()
        tracker.mark_running(token)
        tracker.finish_run(token,outcome='failed',exit_evidence='unknown')
        self.service.store.interrupt_running()
        with self.assertRaisesRegex(FeaturePaused,'exit/release'):
            self.workflow.user_action('job','room-job','retry')
        self.assertEqual(self.service.store.snapshot('job')['phase'],'interrupted')

    def failed_branch(self, error=None):
        with patch.object(self.workflow.api,'create_branch',side_effect=error or GitAutomationError('failed before switch')):
            with self.assertRaises((GitAutomationError,subprocess.TimeoutExpired)):
                self.workflow.before_task('job',self.task())
        return self.workflow.get('job')

    def test_branch_failure_retries_original_task_with_same_base(self):
        feature=self.failed_branch()
        with self.service.store.transaction() as db:
            db.execute("UPDATE collab_tasks SET state='failed' WHERE job_id='job'")
        self.workflow.user_action('job','room-job','retry')
        with patch.object(self.workflow.api,'create_branch',wraps=self.workflow.api.create_branch) as create:
            self.assertEqual(self.workflow.before_task('job',self.task()),'run')
            self.assertEqual(create.call_count,1)
        self.assertEqual(self.workflow.get('job')['base_commit'],feature['base_commit'])
        self.assertEqual(self.git('branch','--show-current'),feature['branch'])
        with self.service.store.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM collab_tasks WHERE job_id='job'").fetchone()[0],'pending')

    def test_branch_success_then_ready_save_failure_recovers_without_create(self):
        original=self.workflow.save
        def failing(job,feature):
            if feature['state']=='ready': raise OSError('ready save failed')
            original(job,feature)
        with patch.object(self.workflow,'save',side_effect=failing):
            with self.assertRaises(OSError): self.workflow.before_task('job',self.task())
        self.assertEqual(self.workflow.get('job')['state'],'branch_intent')
        with patch.object(self.workflow.api,'create_branch') as create:
            self.assertEqual(self.workflow.before_task('job',self.task()),'run')
            create.assert_not_called()

    def test_branch_retry_does_not_refresh_moved_head(self):
        feature=self.failed_branch()
        self.git('commit','--allow-empty','-m','external baseline')
        with patch.object(self.workflow.api,'create_branch') as create:
            with self.assertRaisesRegex(FeaturePaused,'outcome unknown'):
                self.workflow.before_task('job',self.task())
            create.assert_not_called()
        self.assertEqual(self.workflow.get('job')['base_commit'],feature['base_commit'])

    def test_existing_unchecked_branch_and_unknown_command_remain_paused(self):
        feature=self.failed_branch()
        self.git('branch',feature['branch'])
        with self.assertRaisesRegex(FeaturePaused,'outcome unknown'):
            self.workflow.before_task('job',self.task())
        self.assertEqual(self.git('branch','--show-current'),'main')
        feature['branch_outcome']='unknown'
        self.workflow.save('job',feature)
        self.git('switch',feature['branch'])
        with self.assertRaisesRegex(FeaturePaused,'command outcome unknown'):
            self.workflow.before_task('job',self.task())

    def test_timed_out_branch_with_no_ref_is_not_blindly_replayed(self):
        self.failed_branch(subprocess.TimeoutExpired('git',1))
        with patch.object(self.workflow.api,'create_branch') as create:
            with self.assertRaisesRegex(FeaturePaused,'command outcome unknown'):
                self.workflow.before_task('job',self.task())
            create.assert_not_called()

    def test_push_approval_gate_handles_many_config_arguments(self):
        self.workflow.before_task('job',self.task())
        context=self.workflow.resolve('branch_'+self.workflow.get('job')['feature_id'])
        with patch('git_automation.subprocess.Popen') as launch:
            with self.assertRaisesRegex(FeaturePaused,'independent approval'):
                self.workflow.api._git(context,*(['-c','push.followTags=false']*6),'push','origin')
            launch.assert_not_called()

    def test_push_failure_recovers_original_commit(self):
        task=self.task()
        self.workflow.before_task('job',task)
        (self.repo/'a.txt').write_text('change\n')
        original=self.workflow.api._git
        def fail_push(context,*args,**kwargs):
            if 'push' in args:
                raise GitAutomationError('network')
            return original(context,*args,**kwargs)
        from git_automation import GitAutomationError
        with patch.object(self.workflow.api,'_git',side_effect=fail_push):
            with self.assertRaises(FeaturePaused):
                self.workflow.finish_task(self.service,'job',task)
        commit=self.git('rev-parse','HEAD')
        self.assertNotEqual(commit,self.base)
        self.assertEqual(self.workflow.before_task('job',task),'recover')
        self.assertTrue(self.workflow.finish_task(self.service,'job',task))
        self.assertEqual(self.git('rev-parse','HEAD'),commit)
        self.assertEqual(self.reviews,1)

    def test_plain_completed_is_not_approval(self):
        task=self.task()
        self.workflow.before_task('job',task)
        (self.repo/'a.txt').write_text('change\n')
        with patch.object(self.service,'call',return_value=reply()):
            with self.assertRaisesRegex(FeaturePaused,'matching review'):
                self.workflow.finish_task(self.service,'job',task)
        self.assertEqual(self.git('rev-parse','HEAD'),self.base)

    def test_protocol_extensions_are_strict_and_legacy_compatible(self):
        validate_plan(copy.deepcopy(self.plan),self.service.store.registry)
        bad=copy.deepcopy(self.plan)
        bad['tasks'][0]['git_stage']='chat'
        with self.assertRaises(ValueError):
            validate_plan(bad,self.service.store.registry)
        valid=reply(git_review=dict(review_id='id',approved=True,issues='none'))
        validate_payload(valid,role='collaborator')
        with self.assertRaises(ValueError):
            validate_payload(valid,role='owner')
        valid['git_review']['approved']='true'
        with self.assertRaises(ValueError):
            validate_payload(valid,role='collaborator')

    def test_remote_selection_saved_without_manual_task_fields(self):
        self.git('remote','add','backup',str(self.remote))
        with self.assertRaises(FeaturePaused) as paused:
            self.workflow.before_task('job',self.task())
        self.assertTrue(paused.exception.user_action)
        self.workflow.user_action('job','room-job','remote','origin')
        self.assertEqual(self.workflow.before_task('job',self.task()),'run')
        self.assertEqual(self.workflow.get('job')['remote'],'origin')

    def test_remote_change_after_review_cannot_redirect_push(self):
        self.workflow.before_task('job',self.task())
        (self.repo/'a.txt').write_text('change\n')
        other=Path(self.temp.name)/'other.git'
        subprocess.run(['git','init','--bare',str(other)],check=True,capture_output=True)
        self.git('remote','set-url','origin',str(other))
        from git_automation import GitAutomationError
        with self.assertRaises(GitAutomationError):
            self.workflow.finish_task(self.service,'job',self.task())
        self.assertEqual(self.git('rev-parse','HEAD'),self.base)
        self.assertEqual(self.git('ls-remote','origin'), '')

    def test_unicode_file_scope_uses_literal_paths(self):
        self.workflow.before_task('job',self.task())
        (self.repo/'中文 name.txt').write_text('你好\n',encoding='utf-8')
        self.assertTrue(self.workflow.finish_task(self.service,'job',self.task()))
        record=self.workflow.reviews.read(self.workflow.get('job')['review_id'])
        self.assertEqual(record['files'],['中文 name.txt'])

    def test_credentials_use_secure_helper_and_never_expose_output(self):
        feature={'directory':str(self.repo)}
        secret=b'username=test\npassword=secret-test-only\n'
        result=subprocess.CompletedProcess([],0,secret,b'')
        with patch('git_feature_workflow.os.name','nt'), patch('git_feature_workflow.subprocess.run',return_value=result) as run:
            self.assertTrue(self.workflow.credentials(feature,interactive=True))
            self.assertEqual(run.call_count,2)
            command=run.call_args_list[0].args[0]
            self.assertIn('credential.helper=',command)
            self.assertIn('credential.helper=manager-core',command)
            environment=run.call_args_list[0].kwargs['env']
            self.assertEqual(environment['GCM_CREDENTIAL_STORE'],'wincredman')
            self.assertEqual(environment['GCM_GITHUB_AUTHMODES'],'browser')
            self.assertEqual(run.call_args.args[0][-1],'approve')
        self.assertNotIn('secret-test-only',json.dumps(self.workflow.snapshot('job')))

    def test_interrupted_push_recovery_keeps_original_commit(self):
        task=self.task()
        self.workflow.before_task('job',task)
        (self.repo/'a.txt').write_text('change\n')
        original=self.workflow.api._git
        from git_automation import GitAutomationError
        def unavailable(context,*args,**kwargs):
            if 'push' in args:
                raise GitAutomationError('network unavailable')
            return original(context,*args,**kwargs)
        with patch.object(self.workflow.api,'_git',side_effect=unavailable):
            with self.assertRaises(FeaturePaused):
                self.workflow.finish_task(self.service,'job',task)
        commit=self.git('rev-parse','HEAD')
        self.service.store.interrupt_running()
        self.workflow.user_action('job','room-job','retry')
        self.assertEqual(self.workflow.before_task('job',task),'recover')
        self.assertTrue(self.workflow.finish_task(self.service,'job',task))
        self.assertEqual(self.git('rev-parse','HEAD'),commit)

    def test_stop_start_race_does_not_hold_gate_during_wait(self):
        self.workflow.before_task('job',self.task())
        context=self.workflow.resolve('branch_'+self.workflow.get('job')['feature_id'])
        started=threading.Event()
        stopped=threading.Event()
        class Child:
            args=['git']
            returncode=0
            def communicate(child,timeout=None):
                started.set()
                if not stopped.wait(5):
                    raise RuntimeError('Stop blocked by process wait')
                return b'',b''
        errors=[]
        def read():
            try:
                self.workflow.api._git(context,'rev-parse','HEAD')
            except Exception as error:
                errors.append(error)
        with patch('git_automation.subprocess.Popen',return_value=Child()) as launch:
            thread=threading.Thread(target=read)
            thread.start()
            self.assertTrue(started.wait(5))
            self.service.store.operate('job','room-job','stop-race','stop')
            stopped.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            with self.assertRaises(FeaturePaused):
                self.workflow.api._git(context,'rev-parse','HEAD')
            self.assertEqual(launch.call_count,1)
        self.assertEqual(errors,[])
