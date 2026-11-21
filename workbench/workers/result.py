from dataclasses import dataclass
from functools import wraps


@dataclass(frozen=True)
class WorkerResult:
    status: str
    text: str
    process_result: object = None
    session_validation: object = None


def structured_worker(worker):
    @wraps(worker)
    def execute(conversation, prompt, message_id, user_message_id):
        result = worker(conversation, prompt, message_id, user_message_id)
        if not isinstance(result, WorkerResult) or result.status not in {'completed','failed','cancelled'} or not isinstance(result.text, str):
            raise RuntimeError('Worker returned invalid structured transport result')
        if result.status == 'completed' and (result.process_result is None or result.session_validation is None):
            raise RuntimeError('Successful worker result lacks execution evidence')
        return result
    return execute
