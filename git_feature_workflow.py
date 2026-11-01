"""Host-owned feature state, independent review and Git orchestration.

Only explicit git_stage plan tasks opt in. Ordinary chat and legacy plans keep
their old behavior. State is additive, transactional SQLite data; no task JSON
or approval file is maintained by the user. Git never runs in a DB transaction.
"""
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from urllib.parse import urlsplit
import re

from git_automation import GitAutomation, GitAutomationError, GitTask
from git_workflow_gate import GIT_START_GATE


class FeaturePaused(GitAutomationError):
    def __init__(self, message, *, user_action=False):
        super().__init__(message)
        self.user_action = user_action


class SqlReviews:
    def __init__(self, store):
        self.store = store

    def read(self, review_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT record FROM git_feature_reviews WHERE review_id=?', (review_id,)).fetchone()
        if not row:
            raise FeaturePaused('Review record unavailable')
        return json.loads(row[0])

    def write(self, review_id, record):
        with self.store.transaction() as db:
            db.execute('UPDATE git_feature_reviews SET record=? WHERE review_id=?',
                       (json.dumps(record, sort_keys=True), review_id))


class GitFeatureWorkflow:
    def __init__(self, store, directory, *, executable=None):
        self.store, self.directory = store, directory
        self.reviews = SqlReviews(store)
        self.api = GitAutomation(self.resolve, self.reviews, executable=executable,
                                 before_start=self.before_start)
        # Additive extension: no rewrite of legacy task/call rows or approvals.
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS git_features (job_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS git_repo_owners (repository TEXT PRIMARY KEY, job_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS git_repo_preferences (repository TEXT PRIMARY KEY, remote TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS git_feature_reviews (
                review_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, record TEXT NOT NULL)''')

    def get(self, job):
        with self.store.transaction() as db:
            row = db.execute('SELECT data FROM git_features WHERE job_id=?', (job,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, job, data):
        with self.store.transaction() as db:
            db.execute('INSERT OR REPLACE INTO git_features VALUES (?,?)', (job, json.dumps(data)))

    def reason(self, job, reason):
        with self.store.transaction() as db:
            db.execute('UPDATE collab_lifecycle SET reason=? WHERE job_id=?', (reason, job))

    def resolve(self, review_id):
        record = self.reviews.read(review_id)
        return GitTask(**{**record['task'], 'directory': Path(record['task']['directory'])})

    def probe(self, directory, *args):
        """Read-only discovery only; never pass Git writes through this bypass."""
        task = GitTask(Path(directory), 'feature/probe', '', 'origin', 'host', 'host')
        # Reads have no feature ownership, but use the same sanitized Git runner.
        api = GitAutomation(lambda _: task, self.reviews, executable=self.api.executable)
        return api._text(task, *args)

    def paths(self, directory, *args):
        """Read-only NUL-delimited path discovery, without feature admission."""
        task=GitTask(Path(directory),'feature/probe','','origin','host','host')
        api=GitAutomation(lambda _:task,self.reviews,executable=self.api.executable)
        return [p.decode('utf-8') for p in api._git(task,*args).stdout.split(b'\0') if p]

    def assert_live(self, db, job):
        row = db.execute('''SELECT j.stopped,l.phase FROM collab_jobs j
            JOIN collab_lifecycle l USING(job_id) WHERE j.job_id=?''', (job,)).fetchone()
        if not row or row[0] or row[1] != 'working':
            raise FeaturePaused('Feature stopped or not working')
        self.assert_released(db,job)

    def assert_released(self, db, job):
        # not_started is a released failed/cancelled run with exit_confirmed=0.
        # Unknown exits retain quiescing state and domain ownership instead.
        if db.execute('''SELECT 1 FROM execution_runs r WHERE r.source_job_id=?
            AND (r.state IN ('pending','running','quiescing') OR NOT EXISTS (
                SELECT 1 FROM execution_domains d WHERE d.agent_id=r.agent_id
                AND d.conversation_id=r.conversation_id
                AND d.state='idle' AND d.owner_run_id IS NULL))''', (job,)).fetchone():
            raise FeaturePaused('Agent exit/release is not confirmed')

    @contextmanager
    def before_start(self, task, args):
        # All commands are checked: cancellation also stops recovery reads from
        # accidentally leading to a later write. Stop takes the same start gate.
        with GIT_START_GATE:
            with self.store.transaction() as db:
                row = db.execute('SELECT job_id FROM git_repo_owners WHERE repository=?',
                                 (os.path.normcase(str(Path(task.directory).resolve())),)).fetchone()
                if not row:
                    raise FeaturePaused('Feature no longer owns repository')
                job = row[0]
                self.assert_live(db, job)
                feature = json.loads(db.execute('SELECT data FROM git_features WHERE job_id=?', (job,)).fetchone()[0])
                if feature['branch'] != task.branch or feature['author'] != task.author:
                    raise FeaturePaused('Feature context changed')
                command=list(args)
                while command and command[0] == '-c':
                    command=command[2:]
                if not command:
                    raise FeaturePaused('Missing Git command')
                if command[0] in {'push','add','write-tree','commit'}:
                    record = db.execute('SELECT record FROM git_feature_reviews WHERE review_id=?',
                                        (feature.get('review_id'),)).fetchone()
                    approved = json.loads(record[0]) if record else {}
                    if approved.get('approved') is not True or approved.get('reviewer') == task.author:
                        raise FeaturePaused('Valid independent approval required')
            yield  # Popen occurs here, after the transaction and before stop acceptance.

    def before_task(self, job, task, service=None):
        stage = task.get('git_stage')
        if not stage:
            return 'run'
        feature = self.get(job)
        if not feature:
            directory = Path(self.directory(job)).resolve(strict=True)
            root = Path(self.probe(directory, 'rev-parse', '--show-toplevel')).resolve(strict=True)
            if root != directory:
                directory = root
            remote_names = self.probe(directory, 'remote').splitlines()
            with self.store.transaction() as db:
                preferred = db.execute('SELECT remote FROM git_repo_preferences WHERE repository=?',
                                       (os.path.normcase(str(directory)),)).fetchone()
            chosen = preferred[0] if preferred and preferred[0] in remote_names else None
            base = self.probe(directory, 'rev-parse', 'HEAD')
            identifier = uuid.uuid4().hex
            feature = dict(feature_id=identifier, directory=str(directory), repository=os.path.normcase(str(directory)),
                           branch='feature/' + identifier, base_commit=base,
                           remote=chosen or (remote_names[0] if len(remote_names)==1 else None), remotes=remote_names,
                           author=task['agent_id'], state='registered', rounds=[], task_id=task['task_id'])
            self.save(job, feature)
        if not feature['remote']:
            raise FeaturePaused('Choose a repository remote or enter the GitHub repository URL on the task card',user_action=True)
        if (feature['state'] == 'push_pending' and feature['task_id'] != task['task_id']
                and service is not None):
            record=self.reviews.read(feature['review_id'])
            operation=record.get('operation',{})
            if (stage == 'revise' and operation.get('commit_exit_confirmed') is True
                    and operation.get('commit_returncode',0) != 0
                    and not operation.get('commit_id')
                    and self.probe(feature['directory'],'rev-parse','HEAD') == feature['base_commit']
                    and self.api._text(self.resolve(feature['review_id']),'write-tree') == operation.get('tree')):
                # Only the exact index created by this failed, exited commit
                # attempt may be carried into a newly reviewed revision. Never
                # clear/reset it, and never reuse an unknown command outcome.
                feature.update(state='rejected',revision_index_tree=operation['tree'])
                self.save(job,feature)
            else:
                self.finish_task(service,job,{'git_stage':'revise'})
            feature=self.get(job)
        if feature['author'] != task['agent_id']:
            raise FeaturePaused('One feature has one coding author; schedule revisions to the original author')
        with GIT_START_GATE, self.store.transaction() as db:
            self.assert_live(db, job)
            owner = db.execute('SELECT job_id FROM git_repo_owners WHERE repository=?', (feature['repository'],)).fetchone()
            if owner and owner[0] != job:
                queued = True
            else:
                db.execute('INSERT OR IGNORE INTO git_repo_owners VALUES (?,?)', (feature['repository'],job))
                queued = False
        if queued:
            self.reason(job, 'Git feature queued: repository occupied by another feature')
            return 'queued'
        if str(Path(self.directory(job)).resolve()) != feature['directory']:
            # A subdirectory is resolved once on entry, but subsequent setting
            # changes must never redirect the feature to another repository.
            if Path(self.probe(self.directory(job), 'rev-parse', '--show-toplevel')).resolve() != Path(feature['directory']):
                raise FeaturePaused('Conversation repository changed')
        task_context = GitTask(Path(feature['directory']), feature['branch'], feature['base_commit'],
                               feature['remote'], feature['author'], 'workbench')
        if feature['state'] in {'registered','branch_intent'}:
            if feature['state'] == 'registered':
                feature['base_commit']=self.probe(feature['directory'],'rev-parse','HEAD')
                task_context=replace(task_context,base_commit=feature['base_commit'])
                self.save(job,feature)
            self.api._remote(task_context)
            remote_url = self.probe(feature['directory'],'remote','get-url',feature['remote'])
            parsed = urlsplit(remote_url)
            if parsed.scheme in {'http','https'} and (parsed.username or parsed.password):
                raise FeaturePaused('Remove credentials from remote URL; use system credential storage')
            digest = hashlib.sha256(remote_url.encode()).hexdigest()
            if feature.get('remote_digest',digest) != digest:
                raise FeaturePaused('Remote changed; confirm repository selection before continuing')
            feature['remote_digest'] = digest
            self.save(job,feature)
            task_context = replace(task_context,remote_digest=digest)
            if parsed.scheme == 'https' and parsed.hostname == 'github.com':
                if not self.credentials(feature, interactive=False):
                    feature['needs_login'] = True
                    self.save(job,feature)
                    raise FeaturePaused('GitHub authorization required: use Sign in to GitHub on the task card',user_action=True)
                feature['secure_credentials']=True
                feature['needs_login']=False
                task_context=replace(task_context,secure_credentials=True)
                self.save(job,feature)
            if feature['state'] == 'branch_intent':
                current = self.probe(feature['directory'],'branch','--show-current')
                head = self.probe(feature['directory'],'rev-parse','HEAD')
                refs=self.probe(feature['directory'],'for-each-ref','--format=%(refname)',
                                'refs/heads/'+feature['branch']).splitlines()
                if feature.get('branch_outcome') not in {'failed','confirmed'}:
                    raise FeaturePaused('Branch command outcome unknown; preserve workspace and inspect')
                if head != feature['base_commit'] or (refs and current != feature['branch']):
                    raise FeaturePaused('Branch operation outcome unknown; preserve workspace and inspect')
                create = not refs
            else:
                create = True
            if create:
                paths = self.probe(feature['directory'],'status','--porcelain','--untracked-files=all')
                if paths:
                    raise FeaturePaused('Unattributed changes; feature not started:\n' + paths)
                feature['state'] = 'branch_intent'
                self.save(job, feature)
                # create_branch needs a resolvable context, not a code approval.
                key = 'branch_' + feature['feature_id']
                with self.store.transaction() as db:
                    db.execute('INSERT OR IGNORE INTO git_feature_reviews VALUES (?,?,?)',
                               (key,job,json.dumps({'task': {**asdict(task_context),'directory':str(task_context.directory)}})))
                feature['branch_outcome']='unknown'
                self.save(job,feature)
                try:
                    self.api.create_branch(key, feature['branch'])
                except subprocess.TimeoutExpired:
                    feature['branch_outcome']='unknown'
                    self.save(job,feature)
                    raise FeaturePaused('Branch command outcome unknown; preserve workspace and inspect')
                except (GitAutomationError,OSError):
                    feature['branch_outcome']='failed'
                    self.save(job,feature)
                    raise
                feature['branch_outcome']='confirmed'
                self.save(job,feature)
            feature['state'] = 'ready'
            self.save(job, feature)
        if feature['task_id'] == task['task_id'] and feature['state'] in {'reviewing','approved','push_pending','succeeded'}:
            return 'recover'
        if feature['state'] == 'coding':
            if stage == 'revise' and feature['task_id'] != task['task_id']:
                if (self.probe(feature['directory'],'branch','--show-current') != feature['branch']
                        or self.probe(feature['directory'],'rev-parse','HEAD') != feature['base_commit']):
                    raise FeaturePaused('Previous coding changed Git baseline; inspect before revision')
                feature['state']='ready'
            else:
                raise FeaturePaused('Previous coding execution outcome unknown; inspect before scheduling a revision')
        if feature['state'] not in {'ready','succeeded','rejected'}:
            raise FeaturePaused('Previous Git operation must be resolved before more coding')
        if feature['state'] == 'rejected' and stage != 'revise':
            raise FeaturePaused('Review rejected; schedule a revise stage to the original author')
        if (self.probe(feature['directory'],'branch','--show-current') != feature['branch']
                or self.probe(feature['directory'],'rev-parse','HEAD') != feature['base_commit']):
            raise FeaturePaused('Branch or baseline changed before coding; preserve workspace')
        feature.update(state='coding', task_id=task['task_id'])
        self.save(job, feature)
        self.reason(job, 'Git feature: coding on ' + feature['branch'])
        return 'run'

    def finish_task(self, service, job, task):
        if not task.get('git_stage'):
            return True
        feature = self.get(job)
        if feature['state'] == 'succeeded':
            return True
        if feature['state'] == 'coding':
            with self.store.transaction() as db:
                self.assert_live(db, job)
            directory = feature['directory']
            if self.probe(directory,'branch','--show-current') != feature['branch'] or self.probe(directory,'rev-parse','HEAD') != feature['base_commit']:
                raise FeaturePaused('Coding changed the branch or baseline; automatic commit paused')
            staged = self.probe(directory,'diff','--cached','--name-only')
            if staged and (not feature.get('revision_index_tree') or
                    self.api._text(self.resolve(feature['review_id']),'write-tree') != feature['revision_index_tree']):
                raise FeaturePaused('Existing staged content retained; inspect before review')
            files = set(self.paths(directory,'diff','--name-only','--no-renames','-z'))
            files.update(self.paths(directory,'ls-files','--others','--exclude-standard','-z'))
            if feature.get('revision_index_tree'):
                files.update(self.paths(directory,'diff','--cached','--name-only','--no-renames','-z'))
            if not files:
                feature['state'] = 'succeeded'
                self.save(job,feature)
                return True
            review_id = uuid.uuid4().hex
            context = GitTask(Path(directory),feature['branch'],feature['base_commit'],feature['remote'],
                              feature['author'],'workbench',feature.get('remote_digest'))
            context=replace(context,secure_credentials=feature.get('secure_credentials',False),
                            owned_index_tree=feature.get('revision_index_tree'))
            record = dict(task_id=review_id, branch=feature['branch'], base_commit=feature['base_commit'],
                          author=feature['author'], approved=False,
                          task={**asdict(context),'directory': directory}, files=sorted(files))
            with self.store.transaction() as db:
                db.execute('INSERT INTO git_feature_reviews VALUES (?,?,?)',(review_id,job,json.dumps(record)))
            record['content'] = self.api.content(review_id, sorted(files))
            self.reviews.write(review_id, record)
            feature.update(state='reviewing', review_id=review_id)
            feature['rounds'].append(review_id)
            self.save(job,feature)
        review_id = feature['review_id']
        record = self.reviews.read(review_id)
        if feature['state'] == 'reviewing':
            reviewers = [a.agent_id for a in self.store.registry.enabled_agents() if a.agent_id != feature['author']]
            if not reviewers:
                raise FeaturePaused('No independent reviewer available')
            reviewer = record.get('reviewer') or reviewers[0]
            # Persist the selected reviewer and call ID before invoking. Lost
            # responses query that call, never silently create another approval.
            if 'review_call_id' not in record:
                record.update(reviewer=reviewer, review_call_id=uuid.uuid4().hex)
                self.reviews.write(review_id,record)
            self.reason(job,'Git feature: independent review')
            instruction = ('只审核，不改文件，不执行Git写操作。核对以下宿主受检基线、文件与内容摘要，'
                           '实际读取差异；在标准协议顶层附加 git_review={"review_id":"' + review_id +
                           '","approved":true或false,"issues":"明确问题或无"}。completed本身不算批准。\n' +
                           json.dumps({k:record[k] for k in ('branch','base_commit','files','content')},ensure_ascii=False))
            with self.store.transaction() as db:
                prior = db.execute('SELECT state,payload FROM collab_calls WHERE job_id=? AND call_id=?',
                                   (job,record['review_call_id'])).fetchone()
            if prior:
                if prior[0] != 'completed' or not prior[1]:
                    raise FeaturePaused('Review call outcome unknown; no automatic replay')
                payload = json.loads(prior[1])
            else:
                payload = service.call(job,reviewer,'collaborator',instruction, call_id=record['review_call_id'])
            result = payload.get('git_review') if payload else None
            if not result or result['review_id'] != review_id:
                raise FeaturePaused('Explicit matching review result required')
            if not result['approved']:
                feature.update(state='rejected',issues=result['issues'])
                self.save(job,feature)
                self.reason(job,'Git review requires revision: ' + result['issues'])
                return False
            if self.api.content(review_id,record['files']) != record['content']:
                feature.update(state='rejected', issues='Content changed after review; new revision required')
                self.save(job,feature)
                return False
            record.update(approved=True,issues=result['issues'])
            self.reviews.write(review_id,record)
            feature['state']='approved'
            self.save(job,feature)
        self.reason(job,'Git feature: commit/push or recovery')
        if 'operation' not in record and self.api.content(review_id,record['files']) != record['content']:
            feature.update(state='rejected', issues='Content changed after approval; revision and review required')
            self.save(job,feature)
            return False
        if feature.get('secure_credentials') and not self.credentials(feature,interactive=False):
            feature['needs_login']=True
            self.save(job,feature)
            raise FeaturePaused('GitHub authorization expired; use Sign in to GitHub, then retry the original operation',user_action=True)
        result = self.api.commit_and_push(review_id,feature['branch'],record['files'],
                                          'Feature ' + feature['feature_id'] + ' round ' + str(len(feature['rounds'])))
        feature.update(result=result, state='succeeded' if result.get('push_status') == 'succeeded' else 'push_pending')
        if feature['state'] == 'succeeded':
            feature['base_commit']=result['commit_id']
            feature.pop('revision_index_tree',None)
        self.save(job,feature)
        self.reason(job,'Git feature: ' + feature['state'] + '; commit=' + str(result.get('commit_id')))
        if feature['state'] == 'push_pending':
            raise FeaturePaused('Git result pending: ' + str(result.get('error','network/push failed')) +
                                '; original commit retained; inspect and recover the original operation')
        return feature['state'] == 'succeeded'

    def credentials(self, feature, *, interactive):
        """Use only GCM + Windows secure storage; discard credential output.

        Official GCM usage: git-ecosystem/git-credential-manager/docs/usage.md.
        Browser authentication is delegated to GCM, never to an Agent process.
        This explicit user login is outside the repository start gate; stopping
        a feature does not cancel an already requested credential fill/approve.
        """
        if os.name != 'nt':
            raise FeaturePaused('Browser sign-in requires a supported system secure credential manager')
        environment = {k:v for k,v in os.environ.items() if not k.upper().startswith(('GIT_','GCM_'))}
        environment.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='always' if interactive else 'never',
                           GCM_CREDENTIAL_STORE='wincredman', GCM_GITHUB_AUTHMODES='browser',
                           GCM_PROVIDER='github', GCM_TRACE='0', GCM_TRACE_SECRETS='0')
        command = [self.api.executable, '-C', feature['directory'], '-c','credential.helper=',
                   '-c','credential.helper=manager-core','credential','fill']
        try:
            result = subprocess.run(command, input=b'protocol=https\nhost=github.com\n\n',
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env=environment, shell=False, timeout=180 if interactive else 15)
            if result.returncode != 0 or b'password=' not in result.stdout:
                return False
            if interactive:
                # Store only through the explicitly selected secure helper.
                saved=subprocess.run(command[:-1]+['approve'],input=result.stdout+b'\n',
                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                     env=environment,shell=False,timeout=15)
                return saved.returncode == 0
            return True
        except (OSError,subprocess.TimeoutExpired):
            return False

    def user_action(self, job, conversation, action, remote=None):
        feature = self.get(job)
        if not feature:
            raise FeaturePaused('No Git feature exists')
        with self.store.transaction() as db:
            self.store._authorize(db,job,conversation)
            life=db.execute('SELECT phase FROM collab_lifecycle WHERE job_id=?',(job,)).fetchone()
            stopped=db.execute('SELECT stopped FROM collab_jobs WHERE job_id=?',(job,)).fetchone()[0]
            if action == 'retry' and life[0] == 'interrupted' and not stopped:
                self.assert_released(db,job)
                db.execute("UPDATE collab_lifecycle SET phase='working' WHERE job_id=?",(job,))
                if feature['state'] in {'reviewing','approved','push_pending','succeeded','branch_intent'}:
                    db.execute("UPDATE collab_tasks SET state='pending' WHERE job_id=? AND task_id=?",
                               (job,feature['task_id']))
            self.assert_live(db,job)
            if action == 'retry' and feature['state'] in {'reviewing','approved','push_pending','branch_intent'}:
                db.execute("UPDATE collab_tasks SET state='pending' WHERE job_id=? AND task_id=? AND state='failed'",
                           (job,feature['task_id']))
        if action == 'remote':
            if feature['state'] != 'registered' or not isinstance(remote,str):
                raise FeaturePaused('Choose a repository before coding')
            if not feature['remotes']:
                parsed=urlsplit(remote)
                if (parsed.scheme != 'https' or parsed.netloc != 'github.com'
                        or parsed.query or parsed.fragment
                        or not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?',parsed.path)):
                    raise FeaturePaused('Enter an HTTPS GitHub repository URL without credentials')
                with GIT_START_GATE, self.store.transaction() as db:
                    self.assert_live(db,job)
                    owner=db.execute('SELECT job_id FROM git_repo_owners WHERE repository=?',(feature['repository'],)).fetchone()
                    if owner and owner[0] != job:
                        raise FeaturePaused('Repository occupied; wait before saving its remote')
                    db.execute('INSERT OR IGNORE INTO git_repo_owners VALUES (?,?)',(feature['repository'],job))
                context=GitTask(Path(feature['directory']),feature['branch'],feature['base_commit'],'origin',feature['author'],'workbench')
                self.api._git(context,'remote','add','origin',remote)
                feature.update(remote='origin',remotes=['origin'])
            elif remote in feature['remotes']:
                feature['remote']=remote
            else:
                raise FeaturePaused('Choose one existing remote before coding')
            self.save(job,feature)
            with self.store.transaction() as db:
                db.execute('INSERT OR REPLACE INTO git_repo_preferences VALUES (?,?)',
                           (feature['repository'],feature['remote']))
        elif action == 'login':
            if not feature.get('needs_login'):
                raise FeaturePaused('No authorization requested')
            if not self.credentials(feature,interactive=True):
                raise FeaturePaused('Browser authorization failed or credential manager unavailable; check Git for Windows installation')
            feature['needs_login']=False
            self.save(job,feature)
        elif action != 'retry':
            raise ValueError('Unknown Git action')

    def release_if_done(self, job, tasks):
        feature = self.get(job)
        if not feature:
            return False
        succeeded = feature['state'] == 'succeeded' and not any(
            t['state']=='pending' and t.get('git_stage') for t in tasks)
        if not succeeded:
            if (feature['state'] != 'registered' or feature.get('rounds') != []
                    or any(feature.get(k) for k in
                           ('review_id', 'commit_id', 'branch_outcome', 'result'))):
                return False
            try:
                branch = feature['branch']
                if not branch or self.probe(feature['directory'], 'for-each-ref',
                                            '--format=%(refname)', 'refs/heads/'+branch):
                    return False
            except (GitAutomationError, OSError, KeyError):
                return False
        with self.store.transaction() as db:
            if not succeeded:
                row = db.execute('''SELECT j.stopped,l.phase FROM collab_jobs j
                    JOIN collab_lifecycle l USING(job_id) WHERE j.job_id=?''', (job,)).fetchone()
                pending = db.execute("SELECT 1 FROM collab_events WHERE job_id=? AND kind LIKE 'cancel_then_%' AND state!='done'", (job,)).fetchone()
                confirmed_stop = row and row[0] and db.execute(
                    "SELECT 1 FROM collab_events WHERE job_id=? AND kind='cancel_then_stop' AND state='done'", (job,)).fetchone()
                if not row or pending or not (row[1] == 'completed' or confirmed_stop):
                    return False
                if db.execute('SELECT 1 FROM git_feature_reviews WHERE job_id=?', (job,)).fetchone():
                    return False
                try:
                    self.assert_released(db, job)
                except FeaturePaused:
                    return False
            return bool(db.execute('DELETE FROM git_repo_owners WHERE repository=? AND job_id=?',
                                   (feature['repository'], job)).rowcount)

    def snapshot(self, job):
        feature = self.get(job)
        if not feature:
            return None
        return {k:feature.get(k) for k in ('feature_id','branch','state','base_commit','rounds',
                                           'needs_login','remotes','remote','result','issues')}
