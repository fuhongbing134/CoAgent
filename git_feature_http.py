"""Same-origin UI actions for repository choice, browser login and recovery."""
import json
import secrets
import threading
import sqlite3


def handle_git_action(handler, service, token):
    host = handler.headers.get('Host','')
    if (host not in {f'127.0.0.1:{handler.server.server_port}', f'localhost:{handler.server.server_port}'}
            or handler.headers.get('Origin') != 'http://' + host
            or not secrets.compare_digest(handler.headers.get('X-Capability-Token',''),token)):
        handler.send_json(403,{'error':'Same-origin authorization required'})
        return
    try:
        if handler.headers.get('Content-Type','').split(';',1)[0].strip().lower() != 'application/json':
            raise ValueError('JSON content type required')
        length=int(handler.headers.get('Content-Length','0'))
        if not 0 < length <= 4096 or handler.headers.get('Transfer-Encoding'):
            raise ValueError('Invalid content length')
        payload=json.loads(handler.rfile.read(length).decode('utf-8'))
        if not isinstance(payload,dict) or payload.keys()-{'job_id','conversation_id','action','remote'}:
            raise ValueError('Invalid Git action fields')
        job, conversation = payload.get('job_id'), payload.get('conversation_id')
        if any(not isinstance(v,str) or not 0 < len(v) <= 200 for v in (job,conversation)):
            raise ValueError('Job and conversation required')
        if payload.get('action') not in {'login','retry','remote'}:
            raise ValueError('Invalid Git action')
        workflow=service.feature_workflow()
        with service.store.transaction() as db:
            service.store._authorize(db,job,conversation)
            if payload['action'] != 'retry':
                workflow.assert_live(db,job)
        def run():
            try:
                service.git_user_action(job,conversation,payload['action'],payload.get('remote'))
            except (RuntimeError,ValueError,OSError,sqlite3.Error):
                workflow.reason(job,'Git action failed; check repository selection, system credential manager or task state')
        threading.Thread(target=run,daemon=True).start()
        handler.send_json(202,{'status':'accepted'})
    except (ValueError,TypeError,UnicodeError,RuntimeError) as error:
        handler.send_json(400,{'error':str(error)})
